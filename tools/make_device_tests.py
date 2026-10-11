"""Build the device-check pages under docs/check/ (profile Annex A stimuli).

Every stimulus is synthetic and verified by decoding before it is written into a page.

  pattern/    one referenced pattern (1x white band with 2x/4x/8x squares; patches 1/2/4/8x) in every
              container: gain-map JPEG, 16-bit PNG PQ (cICP), AVIF PQ/HLG, HEVC and AV1 video
  svg/        the pattern inside SVG: inline, linked, standalone file (gain map vs AVIF PQ); vector fills
  video/      10- and 12-bit HEVC / AV1 playback
  precision/  blind banding test: PQ ramps in 16-bit PNG rounded to 8..16 bits, shuffled, three ranges

Needs:  pip install imagecodecs imageio-ffmpeg   (AVIF encoder, and an ffmpeg with libx265/libaom)
Run:    python tools/make_device_tests.py
"""
from __future__ import annotations

import base64
import html
import json
import struct
import subprocess
import zlib
from pathlib import Path

import imagecodecs
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from udeep.export import CICP_BT2020_PQ_FULL, _chunk, pq_encode, srgb_oetf
from udeep.gainmap import write_gainmap_jpeg
from udeep.jnd import pq_step_in_jnd

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "docs" / "check"
WHITE = 203.0                                     # BT.2408 reference white, cd/m2
FF = imageio_ffmpeg.get_ffmpeg_exe()

# ---------------------------------------------------------------------------------------------- pages
STYLE = """
  :root { color-scheme: dark; --ground: #121417; --panel: #1b1f24; --ink: #e3e7eb; --muted: #909aa4;
          --tag: #8cc2e6; --line: #2b3138;
          --sans: "IBM Plex Sans", "Segoe UI", system-ui, -apple-system, sans-serif;
          --mono: "IBM Plex Mono", ui-monospace, Menlo, monospace; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--ground); color: var(--ink); font: 15px/1.55 var(--sans); }
  main { max-width: 760px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 64px; }
  a { color: var(--tag); }
  h1 { font-size: 26px; line-height: 1.2; margin: 0 0 10px; text-wrap: balance; }
  h2 { font-size: 18px; margin: 34px 0 6px; }
  p { margin: 0 0 10px; max-width: 68ch; } .muted { color: var(--muted); }
  .row { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); }
  .id { font-family: var(--mono); font-weight: 600; color: var(--tag); letter-spacing: .06em; }
  code { font-family: var(--mono); font-size: 13px; }
  img, video, svg { dynamic-range-limit: no-limit; display: block; max-width: 100%; height: auto; }
  video { width: 100%; background: #000; }
  .crumb { font-family: var(--mono); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
  .v { width: 160px; height: 160px; background: #ffffff; padding: 30px; }
  .v span { display: block; width: 100px; height: 100px; background: #ffffff; }
"""
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
         '&family=IBM+Plex+Sans:wght@400;600&display=swap">')


def page(path: Path, title: str, body: str, depth: int = 2) -> None:
    up = "../" * depth
    doc = (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
           f'<title>{html.escape(title)} · udeep</title>{FONTS}<style>{STYLE}</style></head><body><main>'
           f'<p class="crumb"><a href="{up}index.html">UDeep</a> / <a href="../index.html">device checks</a></p>'
           f'{body}</main></body></html>\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc, encoding="utf-8")


# ------------------------------------------------------------------------------------------- encoders
def png16_pq(signal: np.ndarray, path: Path) -> None:
    """16-bit RGB PNG with cICP BT.2020/PQ; `signal` is PQ code value 0..1 (H x W) or (H x W x 3)."""
    sig = signal if signal.ndim == 3 else np.repeat(signal[..., None], 3, -1)
    q = np.round(np.clip(sig, 0, 1) * 65535).astype(">u2")
    h, w, _ = q.shape
    raw = b"".join(b"\x00" + q[r].tobytes() for r in range(h))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
                     + _chunk(b"cICP", CICP_BT2020_PQ_FULL) + _chunk(b"IDAT", zlib.compress(raw, 9))
                     + _chunk(b"IEND", b""))


def png16_read(path: Path) -> np.ndarray:
    """Decode our own 16-bit RGB PNG (filter 0 rows) -> uint16 H x W x 3. Pillow reads 16-bit RGB as 8-bit."""
    b = path.read_bytes(); i = 8; idat = b""; w = h = 0
    while i < len(b):
        n = struct.unpack(">I", b[i:i + 4])[0]; t = b[i + 4:i + 8]
        if t == b"IHDR":
            w, h = struct.unpack(">II", b[i + 8:i + 16])
        elif t == b"IDAT":
            idat += b[i + 8:i + 8 + n]
        i += 12 + n
    raw = zlib.decompress(idat); st = 1 + w * 6
    rows = [np.frombuffer(raw[r * st + 1:(r + 1) * st], dtype=">u2") for r in range(h)]
    assert all(raw[r * st] == 0 for r in range(h))
    return np.stack(rows).reshape(h, w, 3).astype(np.int64)


def hlg(nits, peak=1000.0):
    e = np.clip(nits / peak, 0, 1) ** (1 / 1.2)
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    return np.where(e <= 1 / 12, np.sqrt(3 * e), a * np.log(np.maximum(12 * e - b, 1e-12)) + c)


def avif(signal: np.ndarray, path: Path, bits: int, transfer: str = "PQ") -> None:
    n = 2**bits - 1
    q = np.round(np.clip(signal, 0, 1) * n).astype(np.uint8 if bits == 8 else np.uint16)
    q = q if q.ndim == 3 else np.repeat(q[..., None], 3, -1)
    path.write_bytes(imagecodecs.avif_encode(q, level=100, bitspersample=bits, pixelformat="YUV444",
                                             primaries="BT2020", transfer=transfer, matrix="BT2020_NCL"))
    back = imagecodecs.avif_decode(path.read_bytes())
    assert np.abs(back.astype(np.int64) - q).max() <= 1, path


def video(signal: np.ndarray, path: Path, pix_fmt: str, codec: list[str], trc: str = "smpte2084") -> None:
    h, w = signal.shape[:2]
    sig = signal if signal.ndim == 3 else np.repeat(signal[..., None], 3, -1)
    frame = np.round(np.clip(sig, 0, 1) * 65535).astype("<u2").tobytes()
    vf = (f"scale=out_color_matrix=bt2020:out_range=tv,"
          f"setparams=color_primaries=bt2020:color_trc={trc}:colorspace=bt2020nc:range=tv")
    cmd = [FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}", "-r", "10",
           "-i", "-", "-vf", vf, "-pix_fmt", pix_fmt, *codec, "-color_primaries", "bt2020", "-color_trc", trc,
           "-colorspace", "bt2020nc", "-color_range", "tv", "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, input=frame * 30, check=True)
    info = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True, check=False).stderr   # ffmpeg -i alone exits non-zero
    assert "bt2020" in info and trc in info, f"{path}: colour tags missing"


# -------------------------------------------------------------------------------------------- stimuli
def pattern(n: int = 1080) -> np.ndarray:
    """Linear light, multiples of SDR white. Patches 1/2/4/8x; a 1x band with 2/4/8x squares inside
    (the in-image reference Annex A.1 requires); a log ramp 0.01..8x. Labels at 1x."""
    lin = np.full((n, n), 0.04)
    lab = Image.new("L", (n, n), 0); d = ImageDraw.Draw(lab)
    try:
        big, small = ImageFont.truetype("arialbd.ttf", 44), ImageFont.truetype("arial.ttf", 30)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((n // 2, 60), "HDR check: patches at N x SDR white", fill=255, font=small, anchor="mm")
    pw, gap, top = 200, 40, 130; x0 = (n - (4 * pw + 3 * gap)) // 2
    for i, k in enumerate([1, 2, 4, 8]):
        x = x0 + i * (pw + gap); lin[top:top + pw, x:x + pw] = k
        d.text((x + pw // 2, top + pw + 40), f"{k}x", fill=255, font=big, anchor="mm")
    lin[470:690, 60:n - 60] = 1.0
    for i, k in enumerate([2, 4, 8]):
        cx = 60 + (i + 1) * (n - 120) // 4; lin[520:640, cx - 60:cx + 60] = k
    d.text((n // 2, 730), "white band = 1x; squares inside = 2x, 4x, 8x", fill=255, font=small, anchor="mm")
    lin[800:900, 60:n - 60] = np.logspace(-2, np.log10(8), n - 120)[None, :]
    for v in [0.01, 0.1, 1, 8]:
        x = 60 + int(np.log10(v / 0.01) / np.log10(800) * (n - 121))
        d.text((x, 940), f"{v:g}x", fill=255, font=small, anchor="mm")
    d.text((n // 2, 1010), "log ramp; right of 1x is above SDR white", fill=255, font=small, anchor="mm")
    t = np.asarray(lab, float) / 255.0
    return np.where(t > 0, lin * (1 - t) + t, lin)


def build_pattern() -> list[str]:
    out = CHECK / "pattern"; out.mkdir(parents=True, exist_ok=True)
    lin = pattern(); rgb = np.repeat(lin[..., None], 3, -1); pq = pq_encode(lin * WHITE); hl = hlg(lin * WHITE)
    Image.fromarray(np.round(srgb_oetf(np.clip(rgb, 0, 1)) * 255).astype(np.uint8)).save(out / "sdr.png")
    write_gainmap_jpeg(np.clip(rgb, 0, 1), rgb, out / "gainmap.jpg", headroom=8.0)
    png16_pq(pq, out / "pq16.png")
    assert np.array_equal(png16_read(out / "pq16.png")[..., 0], np.round(pq * 65535).astype(np.int64))
    avif(pq, out / "pq10.avif", 10); avif(hl, out / "hlg10.avif", 10, "HLG")
    video(pq, out / "hevc_pq.mp4", "yuv420p10le", ["-c:v", "libx265", "-crf", "12", "-tag:v", "hvc1",
          "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:log-level=error"])
    video(hl, out / "hevc_hlg.mp4", "yuv420p10le", ["-c:v", "libx265", "-crf", "12", "-tag:v", "hvc1",
          "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error"],
          trc="arib-std-b67")
    rows = [("F0", "SDR PNG: control, the band stays plain white", '<img src="sdr.png" width="1080" height="1080" alt="SDR pattern">'),
            ("F1", "Gain-map JPEG (ISO 21496-1 / Ultra HDR)", '<img src="gainmap.jpg" width="1080" height="1080" alt="gain-map pattern">'),
            ("F2", "16-bit PNG, PQ (<code>cICP</code>)", '<img src="pq16.png" width="1080" height="1080" alt="PNG PQ pattern">'),
            ("F3", "AVIF 10-bit, PQ", '<img src="pq10.avif" width="1080" height="1080" alt="AVIF PQ pattern">'),
            ("F4", "AVIF 10-bit, HLG", '<img src="hlg10.avif" width="1080" height="1080" alt="AVIF HLG pattern">'),
            ("F5", "HEVC 10-bit video, PQ (HDR10)", '<video src="hevc_pq.mp4" autoplay muted loop playsinline controls width="1080" height="1080"></video>'),
            ("F6", "HEVC 10-bit video, HLG", '<video src="hevc_hlg.mp4" autoplay muted loop playsinline controls width="1080" height="1080"></video>')]
    body = ("<h1>Which HDR formats does this screen show?</h1><p>The same pattern in each format. HDR is working if the "
            "<b>2x, 4x and 8x squares appear inside the white band</b>. In SDR the band is plain white (F0).</p>"
            + "".join(f'<div class="row"><p><span class="id">{i}</span> {t}</p>{h}</div>' for i, t, h in rows))
    page(out / "index.html", "Format check", body)
    return ["pattern"]


def svg_wrap(href: str, n: int = 1080) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n + 70}" width="{n}" height="{n + 70}">'
            f'<rect width="{n}" height="{n + 70}" fill="#1a1a1a"/>'
            f'<text x="{n // 2}" y="48" fill="#ffffff" font-family="sans-serif" font-size="36" text-anchor="middle">'
            'vector SVG text; raster pattern below</text>'
            f'<image href="{href}" x="0" y="70" width="{n}" height="{n}"/></svg>')


def build_svg() -> list[str]:
    out = CHECK / "svg"; out.mkdir(parents=True, exist_ok=True); pat = CHECK / "pattern"
    uri = lambda p, t: f"data:{t};base64," + base64.b64encode(p.read_bytes()).decode()
    gm, pq = uri(pat / "gainmap.jpg", "image/jpeg"), uri(pat / "pq10.avif", "image/avif")
    (out / "gainmap.svg").write_text(svg_wrap(gm), encoding="utf-8")
    (out / "pq.svg").write_text(svg_wrap(pq), encoding="utf-8")
    m1 = pq_encode(np.array(4 * WHITE)).item()
    fills = [("V1", "color(srgb-linear 4 4 4)"), ("V2", "color(rec2100-linear 4 4 4)"),
             ("V3", f"color(rec2100-pq {m1:.4f} {m1:.4f} {m1:.4f})")]
    rows = [("S0", "SDR PNG: control", '<img src="../pattern/sdr.png" width="1080" height="1080" alt="SDR pattern">'),
            ("S1", "gain-map JPEG in <code>&lt;img&gt;</code>: positive control", '<img src="../pattern/gainmap.jpg" width="1080" height="1080" alt="gain-map pattern">'),
            ("S2", "gain-map JPEG in an inline SVG", svg_wrap(gm)),
            ("S3", "gain-map JPEG in an inline SVG, linked file", svg_wrap("../pattern/gainmap.jpg")),
            ("S4", "standalone <code>.svg</code> file with the gain-map JPEG", '<img src="gainmap.svg" width="1080" height="1150" alt="SVG file, gain map">'),
            ("S5", "AVIF PQ in an inline SVG", svg_wrap(pq)),
            ("S6", "standalone <code>.svg</code> file with the AVIF PQ", '<img src="pq.svg" width="1080" height="1150" alt="SVG file, AVIF PQ">')]
    vec = "".join(f'<div class="row"><p><span class="id">{i}</span> <code>{c}</code></p>'
                  f'<svg viewBox="0 0 300 150" width="300" height="150"><rect width="300" height="150" fill="#ffffff"/>'
                  f'<rect x="100" y="25" width="100" height="100" style="fill: #000000; fill: {c}"/></svg></div>'
                  for i, c in fills)
    body = ("<h1>Does HDR survive inside SVG?</h1><p>The pattern placed inside SVG in different ways. HDR works if "
            "the <b>squares appear inside the white band</b>.</p>"
            + "".join(f'<div class="row"><p><span class="id">{i}</span> {t}</p>{h}</div>' for i, t, h in rows)
            + "<h2>Vector fills above white</h2><p class=\"muted\">A white rectangle with a square filled at 4x white. "
              "<b>Black</b>: colour not recognised. <b>Same white</b>: clamped to SDR. <b>Brighter</b>: vector HDR.</p>" + vec)
    page(out / "index.html", "SVG check", body)
    return ["svg"]


def build_video() -> list[str]:
    out = CHECK / "video"; out.mkdir(parents=True, exist_ok=True)
    pq = pq_encode(pattern() * WHITE)
    x265 = ["-c:v", "libx265", "-crf", "12", "-tag:v", "hvc1",
            "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:log-level=error"]
    aom = ["-c:v", "libaom-av1", "-crf", "20", "-cpu-used", "8", "-row-mt", "1"]
    jobs = [("V1", "HEVC Main 10: control", "hevc10.mp4", "yuv420p10le", x265),
            ("V2", "HEVC Main 12", "hevc12.mp4", "yuv420p12le", x265),
            ("V3", "HEVC Main 4:4:4 12", "hevc444_12.mp4", "yuv444p12le", x265),
            ("V4", "AV1 10-bit (Main): control", "av1_10.mp4", "yuv420p10le", aom),
            ("V5", "AV1 12-bit (Professional)", "av1_12.mp4", "yuv420p12le", aom[:2] + ["-profile:v", "2"] + aom[2:])]
    for _, _, f, fmt, codec in jobs:
        video(pq, out / f, fmt, codec)
    body = ("<h1>Does 12-bit HDR video play?</h1><p>For each: does it <b>play</b>, and do the <b>squares show inside "
            "the white band</b>? V1 and V4 are 10-bit controls.</p>"
            + "".join(f'<div class="row"><p><span class="id">{i}</span> {t}</p><video src="{f}" autoplay muted loop '
                      f'playsinline controls width="1080" height="1080"></video></div>' for i, t, f, _, _ in jobs))
    page(out / "index.html", "Video check", body)
    return ["video"]


def build_precision(seed: int = 927) -> list[str]:
    out = CHECK / "precision"; out.mkdir(parents=True, exist_ok=True)
    w, h = 1080, 150
    depths = [8, 9, 10, 11, 12, 16]
    ranges = {"D": ("Near black", 0.5, 3.0), "W": ("Around SDR white", 150.0, 260.0), "B": ("Above white", 500.0, 900.0)}
    rng = np.random.default_rng(seed); key = {}; sections = []
    for p, (title, lo, hi) in ranges.items():
        sig = pq_encode(np.geomspace(lo, hi, w))[None, :].repeat(h, 0)
        rows = []
        for letter, bits in zip("ABCDEF", rng.permutation(depths)):
            n = 2**int(bits) - 1
            q = np.round(np.round(sig * n) / n * 65535.0) / 65535.0
            f = out / f"{p}{letter}.png"; png16_pq(q, f)
            assert np.array_equal(png16_read(f)[h // 2, :, 0], np.round(q[h // 2] * 65535).astype(np.int64))
            key[p + letter] = int(bits)
            rows.append(f'<div class="row"><p><span class="id">{p}{letter}</span></p>'
                        f'<img src="{f.name}" width="{w}" height="{h}" alt="ramp {p}{letter}"></div>')
        mid = float(np.sqrt(lo * hi))
        jnd = ", ".join(f"{b}-bit {float(pq_step_in_jnd(mid, b)):.2f}" for b in depths)
        sections.append(f'<h2>{title} <span class="muted">({lo:g}–{hi:g} cd/m²)</span></h2>{"".join(rows)}')
        print(f"precision {title}: JND per step {jnd}")
    # the key is kept out of the page (and out of docs/) so the test stays blind for the reader
    (ROOT / "tools" / "precision_key.json").write_text(json.dumps({"seed": seed, "key": key}, indent=1))
    body = ("<h1>Which strips have bands?</h1><p>Blind test. Every strip is the same smooth grey ramp in HDR, stored as a "
            "16-bit PNG but rounded to a different precision, in shuffled order. Some <b>should</b> show bands. For each "
            "letter, note <b>bands</b>, <b>faint</b> or <b>smooth</b>. Use a dark room and mid brightness.</p>"
            + "".join(sections))
    page(out / "index.html", "Precision check", body)
    return ["precision"]


def build_index(names: list[str]) -> None:
    items = {"pattern": ("Which HDR formats does this screen show?", "One referenced pattern in gain-map JPEG, HDR PNG, AVIF and HDR video."),
             "svg": ("Does HDR survive inside SVG?", "Inline, linked and standalone SVG; vector colours above white."),
             "video": ("Does 12-bit HDR video play?", "HEVC and AV1 at 10 and 12 bits."),
             "precision": ("Which strips have bands?", "A blind banding test: PQ ramps rounded to 8 to 16 bits.")}
    lis = "".join(f'<div class="row"><p><a href="{n}/index.html"><b>{items[n][0]}</b></a></p><p class="muted">{items[n][1]}</p></div>'
                  for n in names)
    body = ("<h1>Device checks</h1><p>Test pages from the profile's Annex A. Each has a control that must not glow and a "
            "positive control that must. If a control fails, the result doesn't count.</p>"
            "<p class=\"muted\">Record your device, OS and browser versions, brightness setting and room light with your "
            "result. Remote-desktop sessions deliver SDR, so don't use them.</p>" + lis)
    doc_path = CHECK / "index.html"
    page(doc_path, "Device checks", body, depth=1)
    s = doc_path.read_text(encoding="utf-8").replace('<a href="../index.html">device checks</a>', "device checks")
    doc_path.write_text(s, encoding="utf-8")


if __name__ == "__main__":
    names = build_pattern() + build_svg() + build_video() + build_precision()
    build_index(names)
    total = sum(f.stat().st_size for f in CHECK.rglob("*") if f.is_file())
    print("docs/check:", len(list(CHECK.rglob("*.html"))), "pages,", total // 1024, "KiB")
