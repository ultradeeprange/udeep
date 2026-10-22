"""Real-data figures for the site's gallery. Each dataset is openly licensed; the files are downloaded into
data/ (not committed) and checked against a recorded SHA-256.

  gw   -- LIGO Hanford strain around GW150914 (GWOSC, CC BY 4.0). Highlight; a STATISTICAL knee: the 1e-4
          false-alarm level of the whitened noise power, ln(1e4) x the mean. Control: noise-only tiles must
          follow the exponential law (1 % above ln 100); the build fails if they do not.
  sun  -- SDO/AIA 171 A, 2011-06-07 06:33:02 UT (NASA; not copyrighted). Highlight; an EXPLORATORY knee (a
          percentile: DN has no physical threshold here). Pixels that may be saturated are masked.
  ct   -- one contrast-enhanced chest CT slice, LIDC-IDRI-0001 (TCIA, CC BY 3.0). Extend; a PRESET knee: 240 HU,
          the top of the soft-tissue window W400/L40. Above it: contrast-filled heart chambers and vessels, and
          bone. Not for diagnosis.

Every figure is written twice, for a light and for a dark page (udeep.mpl.theme): <name>_hdr.jpg and
<name>_dark_hdr.jpg, and the same for the SDR figures. The data, the mapping and the declaration are the same in
both; only the furniture and so the SDR digest differ. The report is of the light set, with the dark set's
conformance beside it.

Needs: pip install astropy h5py scipy pydicom.   Run: python tools/make_showcase.py
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from udeep import mpl as hp
from udeep import read_gainmap_jpeg, read_metadata

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / "data", ROOT / "docs" / "assets"
HEADROOM = 4.0
TCIA = "https://services.cancerimagingarchive.net/nbia-api/services/v1"
SERIES = "1.3.6.1.4.1.14519.5.2.1.6279.6001.179049373636438705059720603192"
SOP = "1.3.6.1.4.1.14519.5.2.1.6279.6001.135156800731029909386852815043"
SOURCES = {
    "H-H1_GWOSC_4KHZ_R1-1126259447-32.hdf5": (
        "https://gwosc.org/eventapi/json/GWTC-1-confident/GW150914/v3/H-H1_GWOSC_4KHZ_R1-1126259447-32.hdf5",
        "c5ea87beced5094b"),
    "AIA20110607_063302_0171_lowres.fits": (
        "https://github.com/sunpy/data/raw/main/sunpy/v1/AIA20110607_063302_0171_lowres.fits", "49bda3ad85bb8382"),
    "lidc_slice.dcm": (f"{TCIA}/getSingleImage?SeriesInstanceUID={SERIES}&SOPInstanceUID={SOP}", "7a300c01f1faf4dd"),
    "H-H1_GWOSC_O4b_4KHZ_R1-1420877824-4096.hdf5": (
        "https://gwosc.org/archive/data/O4b_4KHZ_R1/1420820480/H-H1_GWOSC_O4b_4KHZ_R1-1420877824-4096.hdf5", "db0b394ff10573f0"),
}


SUFFIX = ""                     # "_dark" while the dark set is being made


EHT_SOURCE = "EHT data product 2019-D01-01, imaged with pipeline 2019-D01-02 (tools/run_eht_pipeline.py)"


def fetch(name: str) -> Path:
    url, sha = SOURCES[name]
    path = DATA / name
    if not path.exists():
        DATA.mkdir(exist_ok=True)
        path.write_bytes(urllib.request.urlopen(url, timeout=120).read())
    got = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    if got != sha:
        raise RuntimeError(f"{name}: sha256 {got} does not match the recorded {sha}")
    return path


def save(fig, name: str) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    name += SUFFIX
    fig.savefig(OUT / f"{name}_sdr.png", bbox_inches="tight", pad_inches=0.1)
    hp.savefig_hdr(fig, OUT / f"{name}_hdr.jpg", headroom=HEADROOM)
    text = hp.caption(fig, HEADROOM)
    checked = hp.validate(fig, OUT / f"{name}_hdr.jpg", sdr=OUT / f"{name}_sdr.png")    # every point, pixels included
    plt.close(fig)
    if not checked.ok:
        raise RuntimeError(f"{name}: the file does not conform to the profile: {checked}")
    declared = read_metadata(OUT / f"{name}_hdr.jpg")           # read back from the file, not from the figure
    if declared is None:
        raise RuntimeError(f"{name}: the file carries no declared metadata")
    full = read_gainmap_jpeg(OUT / f"{name}_hdr.jpg", HEADROOM)
    base = read_gainmap_jpeg(OUT / f"{name}_hdr.jpg", 1.0)
    return {"declared": dataclasses.asdict(declared), "caption": text,
            "conformance": {f"{c.point} {c.name}": f"{c.status}: {c.detail}" for c in checked.checks},
            "size": list(full.shape[:2]), "base_max": round(float(base.max()), 3), "hdr_max": round(float(full.max()), 3),
            "over_headroom_values": int((full > HEADROOM * 1.01).sum()),
            "lifted_percent_of_figure": round(100 * float((full.max(-1) > 1.05).mean()), 2)}


GW_EVENTS = {
    # name: (file, event GPS, seconds of record to use, title, asset name)
    "gw150914": ("H-H1_GWOSC_4KHZ_R1-1126259447-32.hdf5", 1126259462.4, 32.0, "GW150914, LIGO Hanford", "gw150914"),
    "gw250114": ("H-H1_GWOSC_O4b_4KHZ_R1-1420877824-4096.hdf5", 1420878141.2, 256.0, "GW250114, LIGO Hanford", "gw250114"),
}


def gw(which: str = "gw150914") -> dict:
    import h5py
    from scipy.signal import spectrogram, welch

    name, gps, span, title, asset = GW_EVENTS[which]
    with h5py.File(fetch(name), "r") as f:
        ds = f["strain/Strain"]
        fs = 1.0 / ds.attrs["Xspacing"]
        start = max(0, round(float((gps - span / 2 - float(ds.attrs["Xstart"])) * fs)))
        strain = ds[start:start + int(span * fs)]
        t0 = float(ds.attrs["Xstart"]) + start / fs
        dq = f["quality/simple/DQmask"][int(t0 - float(ds.attrs["Xstart"])):int(t0 - float(ds.attrs["Xstart"]) + span)]
    if not np.isfinite(strain).all() or not (np.asarray(dq) & 1).all():
        raise RuntimeError(f"{which}: the record has missing data")
    event = gps - t0                                            # seconds into the record (GWOSC event GPS)
    # Whiten first. Raw strain noise is orders of magnitude stronger at low frequency and leaks across the
    # spectrogram: unwhitened, GW150914's noise crossed the 1e-4 knee 45 times in 9984 tiles instead of once.
    fw, pxx = welch(strain, fs=fs, nperseg=int(4 * fs), noverlap=int(2 * fs), window="hann")
    fr = np.fft.rfftfreq(len(strain), 1.0 / fs)
    white = np.fft.irfft(np.fft.rfft(strain) / np.sqrt(np.interp(fr, fw, pxx)), n=len(strain))
    nper, hop = 256, 16                                         # 62.5 ms tiles, 3.9 ms hop
    f_, t_, p = spectrogram(white, fs=fs, window="hann", nperseg=nper, noverlap=nper - hop, scaling="density")
    band = (f_ >= 32) & (f_ <= 400)
    f_, p = f_[band], p[band]
    # noise-only tiles: more than 2 s from the event, and 2 s clear of the record's ends (whitening edge effects)
    quiet = (np.abs(t_ - event) > 2.0) & (t_ > 2.0) & (t_ < t_[-1] - 2.0)
    mean_noise = np.median(p[:, quiet], axis=1, keepdims=True) / np.log(2.0)    # exponential: mean = median / ln 2
    z = p / mean_noise                                          # power in units of the mean noise power per frequency
    knee = float(np.log(1e4))                                   # P(noise > knee) = 1e-4  (13.3 x the median)
    # control on independent (non-overlapping) noise-only tiles: exponential noise crosses ln(100) 1 % of the time
    ind = z[:, quiet][:, ::nper // hop]
    n = int(ind.size)
    control = {"noise_tiles": n, "mean": round(float(ind.mean()), 3),
               "above_ln100": int((ind > np.log(100.0)).sum()), "expected_above_ln100": round(n * 0.01, 1),
               "expected_sd": round(float(np.sqrt(n * 0.01 * 0.99)), 1),
               "above_knee": int((ind > knee).sum()), "expected_above_knee": round(n * 1e-4, 2)}
    if abs(control["above_ln100"] - n * 0.01) > 4 * np.sqrt(n * 0.01 * 0.99):
        raise RuntimeError(f"noise control failed: {control}")          # the statistical knee would not be valid
    win = np.abs(t_ - event) <= 0.35
    zz, tt = z[:, win], t_[win] - event
    rep = 3                                                     # show each tile as a 3 x 3 block (nearest), so the
    img = np.kron(zz, np.ones((rep, rep)))                      # minimum-area filter judges tiles, not single pixels
    fig, ax = plt.subplots(figsize=(6.6, 5.0), dpi=150)
    im = hp.imshow(ax, img, mode="highlight", knee=knee, stretch={"kind": "linear", "lo": 0.0, "hi": float(np.ceil(max(50.0, zz.max()) / 50.0) * 50.0)},
                   headroom=HEADROOM, cmap="fire", extent=(tt[0], tt[-1], f_[0], f_[-1]), aspect="auto", origin="lower",
                   unit="mean noise power", knee_rule="statistical; noise alone exceeds it once in 10,000 tiles",
                   source=SOURCES[name][0])
    hp.colorbar(im, ax=ax, label="power / mean noise power")
    ax.set_title(f"{title}: whitened strain power", fontsize=11)
    ax.set_xlabel("time from the event (s)"); ax.set_ylabel("frequency (Hz)")
    fig.tight_layout()
    out = save(fig, asset)
    out.update(knee=round(knee, 3), control=control, peak_power=round(float(zz.max()), 1),
               tiles_above_knee_in_window=int((zz > knee).sum()), tiles_in_window=int(zz.size))
    return out


def sun() -> dict:
    from astropy.io import fits

    with fits.open(fetch("AIA20110607_063302_0171_lowres.fits")) as h:
        hdu = next(x for x in h if x.data is not None)
        d = np.asarray(hdu.data, dtype=np.float64)
        hd = dict(hdu.header)
    sat = 16383.0                                               # 14-bit full well of one original pixel, in DN
    mask = d >= sat                                             # each pixel here is a 4 x 4 sum: any sum >= 16383 MAY
    knee = float(np.percentile(d[~mask], 99.0))                 # hold a saturated pixel. Conservative.
    fig, ax = plt.subplots(figsize=(6.2, 5.4), dpi=150)
    im = hp.imshow(ax, d, mode="highlight", knee=knee, stretch={"kind": "asinh", "lo": 0.0, "hi": sat, "width": 150.0},
                   headroom=HEADROOM, cmap="fire", mask=mask, origin="lower", unit="DN per 4 × 4 binned pixel",
                   knee_rule="percentile; the 99th percentile of the unmasked pixels",
                   source=SOURCES["AIA20110607_063302_0171_lowres.fits"][0])
    hp.colorbar(im, ax=ax, label="DN (4 x 4 binned)")
    ax.set_title("Sun, SDO/AIA 171 Å, 2011-06-07 06:33 UT (exploratory knee)", fontsize=10.5)
    ax.set_xlabel("x (pixel)"); ax.set_ylabel("y (pixel)")
    fig.tight_layout()
    out = save(fig, "aia171")
    out.update(knee=round(knee, 1), knee_rule="percentile 99 of unmasked pixels (exploratory)", masked=int(mask.sum()),
               header={k: hd.get(k) for k in ("TELESCOP", "WAVELNTH", "DATE-OBS", "EXPTIME", "LVL_NUM", "NSATPIX")},
               data_max=round(float(d.max()), 1))
    return out


def ct() -> dict:
    import pydicom

    ds = pydicom.dcmread(fetch("lidc_slice.dcm"))
    hu = ds.pixel_array.astype(np.float64) * float(ds.RescaleSlope) + float(ds.RescaleIntercept)
    # W400/L40 below the knee. The declared range ends at 700 HU: 89 % of the pixels above the knee are contrast-filled
    # blood at 240-500 HU, and a top of 1900 HU would show them at only 1.2-1.4x white. The cost, stated on the page:
    # the 0.22 % of pixels denser than 700 HU all sit at the headroom and cannot be told apart.
    lo, knee, hi = -160.0, 240.0, 700.0
    fig, ax = plt.subplots(figsize=(6.2, 5.4), dpi=150)
    im = hp.imshow(ax, hu, mode="extend", knee=knee, stretch={"kind": "linear", "lo": lo, "hi": hi},
                   headroom=HEADROOM, cmap="grey", unit="HU",
                   knee_rule="preset; the top of the W400/L40 soft-tissue window", source=SOURCES["lidc_slice.dcm"][0])
    hp.colorbar(im, ax=ax, label="HU")
    ax.set_title("Chest CT (contrast-enhanced), LIDC-IDRI-0001: soft-tissue window", fontsize=10.5)
    ax.set_xlabel("x (pixel)"); ax.set_ylabel("y (pixel)")
    fig.tight_layout()
    out = save(fig, "ct_lidc")
    out.update(knee=knee, above_knee_percent=round(100 * float((hu > knee).mean()), 2),
               above_range_percent=round(100 * float((hu > hi).mean()), 3),
               dicom={k: str(ds.get(k)) for k in ("Modality", "Manufacturer", "KVP", "ConvolutionKernel", "SliceThickness",
                                                  "BitsStored", "WindowCenter", "WindowWidth", "PatientID")})
    return out


def m87() -> dict:
    """M87* from the EHT's released data and pipeline (tools/run_eht_pipeline.py). The data is light, so the
    mapping is PROPORTIONAL (profile 6.6): light = Tb / Tb_white, with white at a round physical value."""
    from astropy.io import fits

    path = DATA / "eht" / "m87_2017_101.fits"
    if not path.exists():
        raise RuntimeError("run tools/run_eht_pipeline.py first (it needs its own environment with ehtim)")
    with fits.open(path) as h:
        hd, jy = h[0].header, np.asarray(h[0].data, dtype=np.float64).squeeze()
    c, k = 299792458.0, 1.380649e-23
    pix = abs(hd["CDELT1"]) * np.pi / 180.0                     # pixel size, radians
    tb = jy * 1e-26 * c**2 / (2 * k * hd["FREQ"] ** 2 * pix**2) / 1e9          # brightness temperature, 1e9 K
    white = 3.0                                                 # 3e9 K at diffuse white
    half = jy.shape[0] * pix / 4.848136811e-12 / 2              # half the field, in microarcseconds
    rep = 8                                                     # show each 2 uas pixel as an 8 x 8 block (nearest)
    fig, ax = plt.subplots(figsize=(6.2, 5.4), dpi=150)
    # hi = headroom x white makes the curve above the knee exactly linear: light = Tb / white everywhere.
    im = hp.imshow(ax, np.kron(tb, np.ones((rep, rep))), mode="extend", knee=white,
                   stretch={"kind": "linear", "lo": 0.0, "hi": HEADROOM * white}, headroom=HEADROOM, cmap="fire",
                   extent=(half, -half, -half, half), origin="lower", unit="× 10⁹ K", proportional=True,
                   knee_rule="physical; a round value of brightness temperature", source=EHT_SOURCE)
    hp.colorbar(im, ax=ax, label="brightness temperature (10⁹ K)")
    ax.set_title("M87*, 11 April 2017, 1.3 mm: EHT data and pipeline", fontsize=10.5)
    ax.set_xlabel("relative right ascension (μas)"); ax.set_ylabel("relative declination (μas)")
    fig.tight_layout()
    out = save(fig, "m87")
    # the best SDR alternative: the same data scaled so that white is at the top of the range, nothing clipped
    fig, ax = plt.subplots(figsize=(6.2, 5.4), dpi=150)
    im = hp.imshow(ax, np.kron(tb, np.ones((rep, rep))), mode="extend", knee=HEADROOM * white,
                   stretch={"kind": "linear", "lo": 0.0, "hi": HEADROOM * white}, headroom=1.0, cmap="fire",
                   extent=(half, -half, -half, half), origin="lower")
    hp.colorbar(im, ax=ax, label="brightness temperature (10⁹ K)")
    ax.set_title("M87*, 11 April 2017, 1.3 mm: SDR scaled to the top of the range", fontsize=10.5)
    ax.set_xlabel("relative right ascension (μas)"); ax.set_ylabel("relative declination (μas)")
    fig.tight_layout()
    fig.savefig(OUT / f"m87{SUFFIX}_full.png", bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)
    # The EHT's published treatment (M87 Paper IV, ApJL 875, L4, Figs 14-15): the eht-imaging image restored with a
    # circular Gaussian of 17.1 uas FWHM, to match the 20 uas beam of the DIFMAP pipeline. (The published images are
    # the average of three pipelines so blurred; only this one runs here.)
    from scipy.ndimage import gaussian_filter
    uas = pix / 4.848136811e-12
    tb_b = gaussian_filter(tb, sigma=17.1 / (2.0 * np.sqrt(2.0 * np.log(2.0))) / uas, mode="constant")
    for name, title, knee_b, top_b, head_b in (
            ("m87_blur", "M87*, eht-imaging image restored to 17.1 μas", white, HEADROOM * white, HEADROOM),
            ("m87_blur_full", "M87*, restored to 17.1 μas: SDR scaled to the top", 7.0, 7.0, 1.0)):
        fig, ax = plt.subplots(figsize=(6.2, 5.4), dpi=150)
        im = hp.imshow(ax, np.kron(tb_b, np.ones((rep, rep))), mode="extend", knee=knee_b,
                       stretch={"kind": "linear", "lo": 0.0, "hi": top_b}, headroom=head_b, cmap="fire",
                       extent=(half, -half, -half, half), origin="lower", unit="× 10⁹ K", proportional=True,
                       knee_rule="physical; a round value of brightness temperature", source=EHT_SOURCE)
        hp.colorbar(im, ax=ax, label="brightness temperature (10⁹ K)")
        ax.set_title(title, fontsize=10.5)
        ax.set_xlabel("relative right ascension (μas)"); ax.set_ylabel("relative declination (μas)")
        fig.tight_layout()
        if head_b > 1.0:
            blur_stats = save(fig, name)
        else:
            fig.savefig(OUT / f"{name.removesuffix('_full')}{SUFFIX}_full.png", bbox_inches="tight", pad_inches=0.1)
            plt.close(fig)
    yy, xx = np.mgrid[:jy.shape[0], :jy.shape[1]]
    r = np.hypot(xx - (jy.shape[1] - 1) / 2, yy - (jy.shape[0] - 1) / 2) * 2.0
    south = lambda im_: float(im_[(yy < 31.5) & (r > 10) & (r < 30)].mean() / im_[(yy > 31.5) & (r > 10) & (r < 30)].mean())
    out["restored_17p1uas"] = {"peak_1e9K": round(float(tb_b.max()), 2), "peak_over_white": round(float(tb_b.max() / white), 2),
                               "flux_kept": round(float(tb_b.sum() / tb.sum()), 4),
                               "centre_over_peak": round(float(tb_b[r < 4].mean() / tb_b.max()), 3),
                               "south_over_north": round(south(tb_b), 2), "south_over_north_unblurred": round(south(tb), 2),
                               "hdr_max": blur_stats["hdr_max"], "lifted_percent_of_figure": blur_stats["lifted_percent_of_figure"]}
    prof = [float(tb[(r >= a) & (r < a + 4)].mean()) for a in range(0, 40, 4)]
    out.update(white_1e9K=white, peak_1e9K=round(float(tb.max()), 2), peak_over_white=round(float(tb.max() / white), 2),
               total_flux_Jy=round(float(jy.sum()), 4), pixel_uas=round(pix / 4.848136811e-12, 3),
               ring_radius_uas=int(np.argmax(prof)) * 4 + 2, centre_over_peak=round(float(tb[r < 6].mean() / tb.max()), 3),
               pixels_above_white_percent=round(100 * float((tb > white).mean()), 1),
               sha256=hashlib.sha256(path.read_bytes()).hexdigest()[:16])
    return out


HST = "hlsp_heritage_hst_acs-wfc_m51_f555w_v1_drz"
HST_SHA = "d6581bd4fcf888cf"


def m51() -> dict:
    """M51 (the Whirlpool Galaxy), Hubble Heritage ACS/WFC F555W mosaic (HST programme 10452, MAST HLSP).
    Units: electrons/second per 0.05 arcsec pixel (README h_m51_v1_rdm.txt); sky not subtracted. With the header's
    PHOTFLAM, c e/s per pixel is -2.5 log10(c) + 19.167 STmag per square arcsecond. PHYSICAL knee: 19.0."""
    from astropy.io import fits

    path = DATA / "hst" / "mastDownload" / "HLSP" / HST / f"{HST}.fits"
    if not path.exists():
        from astroquery.mast import Observations
        prod = Observations.get_product_list("25072710")
        Observations.download_products(prod[prod["productType"] == "SCIENCE"], download_dir=str(DATA / "hst"))
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            digest.update(chunk)
    if digest.hexdigest()[:16] != HST_SHA:
        raise RuntimeError(f"{path.name}: sha256 {digest.hexdigest()[:16]} does not match the recorded {HST_SHA}")
    with fits.open(path, memmap=True) as h:
        hd = h[0].header
        zp = -2.5 * np.log10(hd["PHOTFLAM"]) + hd["PHOTZPT"] + 2.5 * np.log10((abs(hd["CD1_1"]) * 3600.0) ** 2)
        b = 8                                                   # 8 x 8 block MEAN keeps e/s per original pixel
        ny, nx = h[0].data.shape[0] // b, h[0].data.shape[1] // b
        blk = np.asarray(h[0].data[:ny * b, :nx * b], dtype=np.float32).reshape(ny, b, nx, b)
    nodata = ~(blk != 0).all(axis=(1, 3))                       # blocks touching the mosaic's empty border or gaps
    img = blk.mean(axis=(1, 3)).astype(np.float64)
    img[nodata] = 0.0
    knee_mag, top = 19.0, 20.0
    knee = float(10 ** ((zp - knee_mag) / 2.5))                 # e/s per pixel at 19.0 STmag/arcsec2
    scale = abs(hd["CD1_1"]) * 3600.0 * b / 60.0                # arcminutes per binned pixel
    fig, ax = plt.subplots(figsize=(5.4, 6.6), dpi=150)
    im = hp.imshow(ax, img, mode="highlight", knee=knee, stretch={"kind": "asinh", "lo": 0.0, "hi": top, "width": 0.1},
                   headroom=HEADROOM, cmap="ice", mask=nodata, origin="lower",
                   extent=(0, nx * scale, 0, ny * scale), unit="e⁻/s per 0.05″ pixel",
                   knee_rule="physical; a surface brightness of 19.0 STmag per square arcsecond",
                   source=f"MAST HLSP {HST}.fits (observation 25072710)")
    hp.colorbar(im, ax=ax, label="e⁻/s per 0.05″ pixel (F555W)")
    ax.set_title("M51, Hubble ACS F555W: knee at 19.0 mag/arcsec²", fontsize=10.5)
    ax.set_xlabel("arcminutes"); ax.set_ylabel("arcminutes")
    fig.tight_layout()
    out = save(fig, "m51")
    v = img[~nodata]
    out.update(zero_point_mag_arcsec2_at_1eps=round(float(zp), 3), knee_eps=round(knee, 3), knee_mag_arcsec2=knee_mag,
               range_top_eps=top, range_top_mag_arcsec2=round(float(zp - 2.5 * np.log10(top)), 2),
               above_knee_percent=round(100 * float((v > knee).mean()), 3), above_top_pixels=int((v > top).sum()),
               max_eps=round(float(v.max()), 1), nodata_percent=round(100 * float(nodata.mean()), 1),
               binned_shape=list(img.shape), median_eps=round(float(np.median(v)), 4),
               header={k: str(hd.get(k)) for k in ("TELESCOP", "INSTRUME", "FILTER1", "PROPOSID", "DATE-OBS", "PHOTFLAM", "BUNIT")})
    return out


if __name__ == "__main__":
    def build() -> dict:
        return {"gw150914": gw("gw150914"), "gw250114": gw("gw250114"), "aia171": sun(), "ct_lidc": ct(), "m87": m87(),
                "m51": m51()}

    report = build()
    SUFFIX = "_dark"
    with plt.rc_context(hp.theme("dark")):
        dark = build()
    for name, entry in report.items():      # the dark set declares the same as the light one, bar the SDR figure
        same = lambda d: {k: v for k, v in d["declared"].items() if k not in ("sdr_digest", "sdr_digest_sha3")}
        if same(dark[name]) != same(entry) or dark[name]["caption"] != entry["caption"]:
            raise RuntimeError(f"{name}: the dark figure does not declare what the light one does")
        entry["dark"] = {"sdr_digest": dark[name]["declared"]["sdr_digest"],
                         "sdr_digest_sha3": dark[name]["declared"].get("sdr_digest_sha3"), "conformance": dark[name]["conformance"],
                         "hdr_max": dark[name]["hdr_max"], "base_max": dark[name]["base_max"]}
    (ROOT / "tools" / "showcase_report.json").write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, default=str))
