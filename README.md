<img src="https://ultradeeprange.github.io/udeep/assets/logo.png" alt="UDeep logo: three bars, grey, white and amber, the amber one tallest" width="88" align="right">

# UDeep

[![tests](https://github.com/ultradeeprange/udeep/actions/workflows/tests.yml/badge.svg)](https://github.com/ultradeeprange/udeep/actions/workflows/tests.yml) [![coverage](https://codecov.io/gh/ultradeeprange/udeep/graph/badge.svg)](https://codecov.io/gh/ultradeeprange/udeep) [![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/ultradeeprange/udeep/badge)](https://scorecard.dev/viewer/?uri=github.com/ultradeeprange/udeep)

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

<picture><source media="(prefers-color-scheme: dark)" srcset="https://ultradeeprange.github.io/udeep/assets/intro_range_dark_sdr.png"><img src="https://ultradeeprange.github.io/udeep/assets/intro_range_sdr.png" alt="Two panels of a synthetic CT phantom. Left, the ordinary figure: the bone ring is flat white. Right, a simulation of an HDR display, all four times dimmer: the bone ring shows its texture." width="720"></picture>

*Range. Synthetic CT phantom. The ordinary figure places 240 HU at white, so the bone above it is flat. An HDR
display shows the same file with the bone brighter than white. The right-hand panel only simulates this, by
dimming everything fourfold; the file itself is [`docs/assets/extend_hdr.jpg`](https://ultradeeprange.github.io/udeep/assets/extend_hdr.jpg).*

<picture><source media="(prefers-color-scheme: dark)" srcset="https://ultradeeprange.github.io/udeep/assets/intro_depth_dark_sdr.png"><img src="https://ultradeeprange.github.io/udeep/assets/intro_depth_sdr.png" alt="Two panels of the same smooth synthetic field seen through a narrow window of its range. Stored in 8 bits it is seven flat bands; stored in 12 bits it is smooth." width="720"></picture>

*Depth. Synthetic, noise-free data. A window 2.6 % of the full range wide is stretched to fill the screen, as one
would when looking for faint structure. Stored in 8 bits the window holds 7 levels; in 12 bits, 107. Whether the
coarser steps matter depends on the data's noise, which is the comparison the file records.*

- **Site and demos:** <https://ultradeeprange.github.io/udeep/>, including device checks you can run on your
  own screen
- **Profile (draft standard):** [`profile/udeep-figure-profile-v0.3.md`](https://github.com/ultradeeprange/udeep/blob/main/profile/udeep-figure-profile-v0.3.md)
- **Python reference:** <https://ultradeeprange.github.io/udeep/reference/>, every public function and class
- **Worked examples:** [`examples/`](https://github.com/ultradeeprange/udeep/tree/main/examples/): three on where to put white, and one on depth, in which a 16-bit
  file gives a detector frame back to the whole count; the same four in Julia

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
# in a notebook, end a cell with  hp.inline(fig)  to show that file, with its caption, in the cell's output
```

Three things to know about that example:

- **The headroom is given when the image is made** (`hp.imshow(..., headroom=4)`), because the mapping and the
  colours above white are built for it. `savefig_hdr` needs no headroom; given one that differs, it refuses.
  All udeep images in a figure must share one headroom.
- **Only the image itself goes above white.** Anything drawn over it (a legend, a contour, text, a translucent
  patch) keeps its ordinary colour, and so does the image under it.
- **The SDR figure of record is the one saved with the same cropping** (`bbox_inches="tight", pad_inches=0.1`).
  The JPEG records a digest of exactly those pixels, which is what `--sdr figure.png` checks.

Install (no PyPI release yet; needs Python 3.13 or later and NumPy 2.1 or later; following
[SPEC 0](https://scientific-python.org/specs/spec-0000/), as NumPy and matplotlib do, each Python is supported for
three years after its release):

```
pip install "udeep[mpl] @ git+https://github.com/ultradeeprange/udeep"
```

On an older Python, pip will not install it, and says why ("require a different python version"); pip 25.0 and
25.0.1 alone say only "No matching distribution found for udeep", which means the same. `python --version` shows
which you have; [uv](https://docs.astral.sh/uv/), conda or the installer from python.org give a newer one beside it.

For a dark page, build the figure inside `with plt.rc_context(hp.theme("dark")):`. The background and the text
change; the data, its colours, the mapping and what the file declares do not, so a light and a dark copy are two
files of one figure (profile 8.7). The project's site shows whichever suits the viewer's theme.

That command builds the Rust core as it installs, if Rust (`cargo`) is on the machine, and installs without it if
not. `python -m udeep.selftest` then says which you have, and checks that udeep works on your machine: it makes a
figure from made-up data, checks the file against the profile, and reads a data record back. Wheels that carry the core, for
Linux, Windows and macOS, are built by the repository's `wheels` workflow; there is no release of them yet.

A 16-bit PQ PNG is written by `udeep.write_png_pq`, with white at 203 cd/m² as the profile requires, and read
back as light by `udeep.read_png_pq` or as data by `udeep.read_png_data`, whatever its colour map. The levels such
a file holds are worked out by `udeep.metadata.data_steps_pq16`, and `python -m udeep.validate FILE.png` checks a
file that declares itself a data record against that. Such a file holds no SDR image, so it names the one that
goes with it (`udeep.metadata.digest_sdr`); `--sdr` checks that link.

Wherever the core is present (in an installed wheel, or built in a checkout with `cargo build --release --features
jpeg` in `core/`), the package uses it: the mapping and colours run on every processor core (a 64-megapixel view in under 4 s where NumPy takes about 30; the NumPy code runs on several threads too, fastest on a free-threaded Python; `udeep.core.THREADS` sets how many),
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
| `src/udeep/` | scaling, the tone curve (a strictly increasing, invertible shoulder; the flat BT.2390-style roll-off is kept for display rendering), Jzazbz HDR colour maps, PQ PNG, gain-map JPEG writer/reader, matplotlib front end, `jnd` (PQ steps against the DICOM GSDF), a JavaScript port (`js/udeepcore.js`), and a JavaScript binding to the core built as WebAssembly (`js/udeepwasm.js`) |
| `core/` | the Rust core with a C interface, which also builds as WebAssembly (see `core/README.md`) |
| `docs/view/` | a live view in the browser (WebGPU): data drawn through the reference shader, above white on an HDR screen |
| `julia/UDeepCore/` | a Julia binding to the core, with a Makie extension that saves HDR figures (CairoMakie) |
| `src/udeep/shaders/mapping.wgsl` | the mapping as a WebGPU shader; `udeep.gpu` runs it (needs `pip install wgpu`) |
| `src/udeep/golden.json` | the contract: 98 reference cases of 15 kinds, each with its stated tolerance, the 16-bit PQ PNG's levels among them. Python reproduces all of them; the JavaScript port the 55 it implements; the Rust core all 98; the core built as WebAssembly, called through its C interface, all 98 as well |
| `tests/` | pytest suite (`pip install -e ".[test]" && pytest`) |
| `examples/` | four worked examples on synthetic data, with their figures (`examples/README.md`) |
| `profile/` | the UDeep Figure Profile |
| `evidence/` | device reports: one record each in `devices/`, and the table made from them by `tools/evidence.py` |
| `docs/` | the GitHub Pages site: demos and device checks |
| `tools/` | `make_demos.py` (synthetic demos), `make_showcase.py` (real-data gallery; needs `astropy h5py scipy pydicom`), `make_device_tests.py` (device-check pages; needs `imagecodecs` and `imageio-ffmpeg`), `precision_budget.py` |

## Report your device

Run the [device checks](https://ultradeeprange.github.io/udeep/check/) and
[open a device report](https://github.com/ultradeeprange/udeep/issues/new?template=device-report.yml): a form that
asks for your device, system and browser versions, your brightness setting and room light, and what each check
showed. It takes about two minutes.

Each report is kept as a record in [`evidence/devices/`](https://github.com/ultradeeprange/udeep/tree/main/evidence/), and [`evidence/README.md`](https://github.com/ultradeeprange/udeep/blob/main/evidence/README.md)
is the table made from them. A page of checks counts only if its controls behaved, and a value a report did not
give stays "not recorded".

## Taking part

[`CONTRIBUTING.md`](https://github.com/ultradeeprange/udeep/blob/main/CONTRIBUTING.md) says what help is most useful and how to work on the code. Short answers to
the usual doubts are on the [questions page](https://ultradeeprange.github.io/udeep/questions/); ask anything else in the
[Q&A discussions](https://github.com/ultradeeprange/udeep/discussions/categories/q-a). What has changed is in [`CHANGELOG.md`](https://github.com/ultradeeprange/udeep/blob/main/CHANGELOG.md).

## Citing

See [`CITATION.cff`](https://github.com/ultradeeprange/udeep/blob/main/CITATION.cff) (GitHub shows a "Cite this repository" button). Author: Mathew Newman,
[ORCID 0000-0003-1784-3250](https://orcid.org/0000-0003-1784-3250).

## Licence

Code: BSD-3-Clause ([`LICENSE`](https://github.com/ultradeeprange/udeep/blob/main/LICENSE)). Text, profile and site content: CC BY 4.0
([`LICENSE-docs.txt`](https://github.com/ultradeeprange/udeep/blob/main/LICENSE-docs.txt)).

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
