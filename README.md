# UDeep

**Ultra Deep Range scientific figures: more range, more levels, and a record of how data became light.**

An ordinary figure discards data. It holds 256 levels and stops at white, whilst the measurement behind it
would usually hold more of both: values well above whatever was placed at white, and steps finer than a single
level. UDeep is a draft standard, and a set of tools, for figures that keep what they can of this and state in
the file how the data became brightness.

- **Range.** An ordinary figure stops at white. Here, values above a declared, meaningful *knee* appear brighter
  than white on an HDR display. The SDR version remains exactly the figure one would have made anyway; a gain map
  (ISO 21496-1 / Ultra HDR layout) carries the remainder, of which each display applies only as much as its own
  headroom allows. Everything below the knee is untouched.
- **Depth.** More levels are of use only where the data warrants them. The file therefore states how large a step
  in the data one stored level is, set against the data's own uncertainty. A file that holds the data to within
  its noise is a *data record*; one that does not is a *rendering*, and says so. It must be noted that an 8-bit
  gain-map JPEG is always a rendering: 12-bit PQ and 16-bit PNG hold more.
- **Record.** No figure file says how it was made. The mapping from data to light is therefore written into the
  file, so that the figure could be inverted, reproduced and checked.

- **Site and demos:** <https://ultradeeprange.github.io/udeep/>, including device checks you can run on your
  own screen
- **Profile (draft standard):** [`profile/udeep-figure-profile-v0.3.md`](profile/udeep-figure-profile-v0.3.md)

> Status: research preview. The profile is a working draft, not endorsed by any standards body. Its
> device evidence so far comes from one iPhone 16 Pro and one desktop HDR monitor.

## Quick start

```python
import matplotlib.pyplot as plt
import udeep.mpl as hp

fig, ax = plt.subplots()
img = hp.imshow(ax, ct, mode="extend", knee=240,                  # 240 HU = top of the soft-tissue window
                stretch={"kind": "linear", "lo": -160, "hi": 1850}, cmap="grey",
                headroom=4,                                       # the brightest value is 4 x SDR white
                unit="HU", knee_rule="preset; top of the soft-tissue window",   # what the file will declare
                source="doi:...")                                 # where the data is kept; its digest is recorded too
hp.colorbar(img, ax=ax, label="HU")                               # data units, "SDR white 240" marked
fig.savefig("figure.png", bbox_inches="tight", pad_inches=0.1)    # the ordinary figure, cropped as the JPEG will be
hp.savefig_hdr(fig, "figure.jpg")                                 # the same figure + gain map + declared metadata
print(hp.caption(fig))                                            # a caption generated from the declaration

import udeep
udeep.read_metadata("figure.jpg")                               # unit, mapping, knee and its rule, precision
print(hp.validate(fig, "figure.jpg"))                             # check the file against the profile
# from a shell:  python -m udeep.validate figure.jpg --sdr figure.png   (the file, and its link to the SDR figure)
```

Three things to know about that example:

- **The headroom is given when the image is made** (`hp.imshow(..., headroom=4)`), because the mapping and the
  colours above white are built for it. `savefig_hdr` needs no headroom; given one that differs, it refuses.
  All udeep images in a figure must share one headroom.
- **Only the image itself goes above white.** Anything drawn over it (a legend, a contour, text, a translucent
  patch) keeps its ordinary colour, and so does the image under it.
- **The SDR figure of record is the one saved with the same cropping** (`bbox_inches="tight", pad_inches=0.1`).
  The JPEG records a digest of exactly those pixels, which is what `--sdr figure.png` checks.

Install (no PyPI release yet; needs Python 3.11 or later and NumPy 2.1 or later):

```
pip install "udeep[mpl] @ git+https://github.com/ultradeeprange/udeep"
```

For a dark page, build the figure inside `with plt.rc_context(hp.theme("dark")):`. The background and the text
change; the data, its colours, the mapping and what the file declares do not, so a light and a dark copy are two
files of one figure (profile 8.7). The project's site shows whichever suits the viewer's theme.

Wherever the Rust core is built (`cargo build --release --features jpeg` in `core/`), the package uses it: the
mapping and colours run on every processor core (a 64-megapixel view in under 4 s where NumPy takes about 30),
and gain-map JPEGs are written and read by it. Without the library the NumPy and Pillow code does the same work,
and `udeep.core.enable(False)` chooses that code. Both are held to the same test vectors; they agree to about one
part in 10^12, which is not bit for bit.

For a very large image, `hp.imshow(..., float32=True)` keeps the image's renderings in half the memory. The colours
then differ from the default's by about one part in 10 million, which is why it is off unless asked for.

## The rules, in brief

1. **The SDR figure is complete.** Below the knee, HDR is identical to the SDR figure clipped at the same
   white.
2. **The knee means something:** a physical threshold, a statistical one (a false-alarm level) or a
   domain preset (a CT window). A percentile knee is labelled exploratory.
3. **Brightness says where; the colour bar says how much.**
4. **The mapping is global and declared.** It is one mapping, written into the file. Saturated or
   clipped pixels never go above white.

Two modes use the same tone curve:
- **Highlight:** sparse extremes above a high threshold.
- **Extend:** a second range of real structure above the main one.

## What is in the repository

| Path | Contents |
|---|---|
| `src/udeep/` | scaling, the tone curve (a strictly increasing, invertible shoulder; the flat BT.2390-style roll-off is kept for display rendering), Jzazbz HDR colour maps, PQ PNG, gain-map JPEG writer/reader, matplotlib front end, `jnd` (PQ steps against the DICOM GSDF), a JavaScript port (`js/udeepcore.js`) |
| `core/` | the Rust core with a C interface (see `core/README.md`) |
| `docs/view/` | a live view in the browser (WebGPU): data drawn through the reference shader, above white on an HDR screen |
| `julia/UDeepCore/` | a Julia binding to the core, with a Makie extension that saves HDR figures (CairoMakie) |
| `src/udeep/shaders/mapping.wgsl` | the mapping as a WebGPU shader; `udeep.gpu` runs it (needs `pip install wgpu`) |
| `src/udeep/golden.json` | the contract: 82 reference cases of 14 kinds, each with its stated tolerance. Python reproduces all of them; the JavaScript port the 47 it implements; for the Rust core, all 14 kinds are implemented; it reproduces all 74 cases |
| `tests/` | pytest suite (`pip install -e ".[test]" && pytest`) |
| `profile/` | the UDeep Figure Profile |
| `docs/` | the GitHub Pages site: demos and device checks |
| `tools/` | `make_demos.py` (synthetic demos), `make_showcase.py` (real-data gallery; needs `astropy h5py scipy pydicom`), `make_device_tests.py` (device-check pages; needs `imagecodecs` and `imageio-ffmpeg`), `precision_budget.py` |

## Report your device

Run the [device checks](https://ultradeeprange.github.io/udeep/check/) and open an issue with:
- your device, OS and browser versions;
- your brightness setting and room light;
- which items showed HDR.

The profile's evidence annex grows from these reports.

## Citing

See [`CITATION.cff`](CITATION.cff) (GitHub shows a "Cite this repository" button). Author: Mathew Newman,
[ORCID 0000-0003-1784-3250](https://orcid.org/0000-0003-1784-3250).

## Licence

Code: BSD-3-Clause ([`LICENSE`](LICENSE)). Text, profile and site content: CC BY 4.0
([`LICENSE-docs.txt`](LICENSE-docs.txt)).

## Data on the site

The home page's two demos are synthetic (`tools/make_demos.py`). The gallery uses six real, openly licensed datasets,
downloaded and checksum-checked by `tools/make_showcase.py` into `data/` (not committed):

| Figure | Source | Licence |
|---|---|---|
| GW150914 and GW250114 spectrograms | LIGO Hanford strain, Gravitational Wave Open Science Center | CC BY 4.0 |
| Chest CT slice | LIDC-IDRI-0001, The Cancer Imaging Archive | CC BY 3.0 |
| The Sun at 171 Å | SDO/AIA, via the SunPy sample data | NASA, not copyrighted |
| The Whirlpool Galaxy (M51) | Hubble Heritage ACS F555W mosaic, HST programme 10452, MAST | public (NASA/ESA) |
| The black hole in M87 | EHT calibrated data 2019-D01-01, imaged with the EHT pipeline 2019-D01-02 by `tools/run_eht_pipeline.py` (needs its own environment with `ehtim`) | data: ODC-PDDL; pipeline: GPLv3, downloaded at build time, not included here |

The required credits and citations are on the gallery page (`docs/gallery/`). The figures derived from these datasets
carry the same terms as their sources. None is for diagnosis or measurement.
