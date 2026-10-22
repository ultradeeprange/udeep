# Performance: scope

Status: scoping, 2026-10-08. Nothing in the library has changed. Measurements come from
`project/spikes/performance_spike.py` (log beside it) and a one-off stage timing, on one desktop machine that was
doing other work: single runs or best of two or three. Treat each number as good to perhaps ±30 %. The ranking
does not depend on that.

## 1. Where the time goes

One-megapixel image, Python reference implementation (UDeep 0.3.0.dev0).

| Stage | Time | Note |
|---|---|---|
| scale (data → position) | 7 ms | |
| tone curve (position → light) | 63 ms | |
| **colour map `fire` (light → colour), one pass** | **6,000–17,000 ms** | a 24-step search per pixel for the most saturated colour inside the gamut |
| digest of the data | 10 ms | |
| levels and precision (`data_steps`), grey / fire | 33–57 ms / 1,700 ms | the same colour-map cost, on 65,536 samples |
| gain-map JPEG (two encodes, one decode) | ~510 ms | already compiled code (libjpeg-turbo inside Pillow) |

A whole figure (8 × 8 inch, 150 dpi; 1169 × 956 pixels saved):

| | build image + colour bar | `savefig_hdr` | plain matplotlib `savefig` |
|---|---|---|---|
| grey | 0.7 s | 2.8–3.0 s | 0.6 s |
| fire | ~80 s | ~11 s | 1.0 s |

Inside `savefig_hdr` for the grey figure (2,758 ms):

| Part | Time | What it is |
|---|---|---|
| compose | 1,813 ms | three matplotlib renders and the arithmetic between them |
| · one render cropped to the labels | 508 ms each, twice | matplotlib draw (163 ms) + PNG encode and decode (~345 ms) |
| · one render to restore the figure | 163 ms | not needed for the file |
| · arithmetic | ~630 ms | sRGB decoding and comparisons in float64 on values that are only 8-bit |
| gain-map JPEG | 507 ms | |
| declaration | 36 ms | |

## 2. What limits performance, in order

1. **The colour-map search.** Over 95 % of the time of any colour figure. It also makes the test suite and the
   gallery build slow.
2. **Rendering through PNG.** Two thirds of each cropped render is spent compressing and decompressing a PNG
   that never leaves memory.
3. **Float arithmetic on 8-bit values.** The render gives 256 possible values per channel; decoding them one
   pixel at a time in float64 is wasted work.
4. **JPEG coding.** Half a second, in compiled code already.
5. **The second matplotlib render** itself (163 ms). Inherent to the render-twice method.

The language is not on this list. Items 1 to 3 are choices of method, and they are the same choices a Rust or
Julia version would have to make.

## 3. Options

### 3.1 The colour map as a table (item 1)

A colour map is a function of light alone. Compute it once at N nodes and interpolate. The table has two
segments, 0 to 1 and 1 to the headroom, because the map has a corner at white.

Measured against the exact search, at 4× headroom, on 500,000 values:

| Nodes per segment | Build | Worst error, 8-bit codes (below white / HDR pass) | Values that round to a different code |
|---|---|---|---|
| 257 | 11–25 ms | 2.93 / 0.73 | 0.2 % |
| 1,025 | 41–68 ms | 0.52 / 0.29 | 0.01–0.03 % |
| **4,097** | **~100–130 ms** | **0.22 / 0.08** | **0.001–0.002 %** |
| 16,385 | 0.7–1.3 s | 0.04 / 0.05 | 0.001 % |

- **Speed:** one megapixel in 316 ms against 16,645 ms for the search in the same run: about 50× faster, with an
  unoptimised lookup. A colour figure would go from roughly 90 s to a few seconds.
- **Invertibility:** the table's luminance never decreases below white, for both maps (checked).
- **Cost:** a table for each (map, headroom) pair, built on first use and cached.

**It also answers a profile question.** Open issue 8 says the colour map is declared by name only, so a reader
that does not know the name cannot invert it. If the *table* is the definition of the map (N nodes, linear
interpolation between them), then:

- every implementation gets the same colours by interpolating the same numbers, with no need to port the
  gamut search to JavaScript, Rust or Julia;
- the table can be published as data, registered, or embedded in the file.

So the recommendation is stronger than "cache it": make the table normative and keep the search as the tool
that generates it.

**Risk:** values change by up to 0.22 of an 8-bit code, and about 1 in 100,000 pixels rounds differently. The
golden vectors for colour maps would be regenerated, and the gallery files rebuilt. The change must be stated in
the profile's change list. (Done: profile Annex E.)

### 3.2 Render without the PNG round trip (item 2)

Ask matplotlib for raw pixels and crop them, in place of saving a PNG to memory and decoding it. Saves about
345 ms per cropped render, twice. **Open:** the cropped layout can be larger than the canvas, which is why
`savefig` is used today; the raw path has to reproduce that exactly, pixel for pixel, or the gain map
misaligns. The existing alignment test covers it.

### 3.3 Decode 8-bit values through a 256-entry table (item 3)

Replace per-pixel float decoding with a lookup. Exact, not approximate: there are only 256 inputs. Saves most of
~630 ms.

### 3.4 Drop the restoring render (item 5, part)

After the HDR pass the figure's images are set back, and then drawn once more. The draw is not needed for the
file. Saves ~163 ms. **Check:** that an interactive figure still redraws correctly afterwards.

### 3.5 JPEG (item 4)

Little to gain. The base is encoded, decoded (to compute the gain against what a viewer will see) and the gain
map encoded at full quality. All three are needed.

### 3.6 A GPU path (added 2026-10-08, at Mathew's direction)

The first draft of this scope left the GPU out, on the grounds that a still figure costs about a second once
3.1 to 3.4 are done. That is true of figure export and wrong as a design position: the mapping should have a GPU
form from the start, beside a parallel CPU form. Where each pays:

| Use | Does a GPU help? | Why |
|---|---|---|
| Saving one figure through matplotlib | **no** | the remaining time is matplotlib's own drawing and JPEG coding; the data would cross to the GPU and back for a few milliseconds of work |
| A live viewer (pan, zoom, drag the knee) | **yes, decisively** | the mapping must run every frame; in a shader it is free, and the screen is where the pixels are going anyway |
| HDR output to the screen itself | **yes, and it is the only route** | a float HDR surface (WebGPU `rgba16float` with extended tone mapping, K6 of the vector scope) is drawn by the GPU |
| Video and time series | **yes** | thousands of frames through one mapping |
| Very large images (whole-slide microscopy, mosaics, volumes) | **yes** | tens to thousands of megapixels |

**The design makes it cheap.** After 3.1 the whole mapping is three steps that are natural in a shader:

1. scale: a closed-form function of the value (linear, log, asinh);
2. tone curve: one division below the knee, one cubic above it;
3. colour map: a lookup in a small one-dimensional texture, which is the table of 3.1, interpolated by the GPU.

No search, no branches that depend on neighbours, nothing that cannot be written in a dozen lines of WGSL or
GLSL. The gain-map and PQ encodes are likewise per-pixel arithmetic.

**What a GPU path consists of**

| Piece | Form | Checked by |
|---|---|---|
| Reference shader for the mapping | WGSL (WebGPU) first; GLSL from it | the golden vectors, run on the GPU and read back, within a stated float32 tolerance |
| The colour-map table as a texture | the same numbers as the CPU table | same |
| A GPU array path in Python | optional back end (CuPy or PyTorch) behind the same functions, data staying on the GPU | same vectors; a benchmark at 1, 16 and 256 megapixels |
| A live HDR view | a WebGPU canvas page that draws a dataset with the shader, to an HDR surface | the device checks (Annex A), since this is a claim about a screen |

**What a parallel CPU path consists of.** The table lookup and the tone curve are independent per pixel, so they
split across cores by rows. In Python that means a compiled inner loop (the Rust core, or NumPy calls that
release the lock); in Rust it is a parallel iterator. The JPEG step stays single-threaded per image.

**Rules to keep it honest**

- The GPU and CPU paths MUST give the same result within a stated tolerance, tested on the golden vectors.
  Float32 on the GPU against float64 on the CPU will differ in the last digits; the tolerance is written down,
  not discovered.
- A figure of record is produced by the CPU reference path. The GPU path renders; it does not define.
- Every speed claim comes with a benchmark and its spread (this machine: RTX 5070 Ti, 16 GB).

**State (updated 2026-10-09).** The reference shader exists (`src/udeep/shaders/mapping.wgsl`) and runs on this
machine's RTX 5070 Ti through WebGPU for Python (`wgpu`, on Vulkan), driven by `udeep.gpu.run`. It is held to
the tolerances of decision D8: 2 x 10^-6 in position and light, 1 x 10^-5 in colour. All eight golden mapping
cases are accepted and hold them; sampled across whole images, the largest error among them is 0.25 of the
tolerance in light and 0.22 in colour. Float32 cannot hold every mapping, and `udeep.gpu.check` refuses those
it cannot instead of answering less well: a knee near the top of the range (linear, grey: above position 0.955 at
headroom 2, 0.75 at 16), a log or asinh range that is too narrow or has its knee too low, and a colour map at a
headroom between 1 and about 1.03. The shipped maps fire/1 and ice/1 are accepted at every shipped headroom; the
tightest is fire/1 at headroom 2, with the knee up to position 0.85 (linear) or 0.75 (log). Of 1,800 random
mappings, 1,433 were accepted and none of those was out of tolerance (largest error 0.79 of it, and 0.007 of an
8-bit code); of the 367 refused, 203 were out when run regardless. The limits are set out in the docstring of
`src/udeep/gpu.py`.
Four megapixels, data to colour, take 51 ms including upload and read-back, against 922 ms for the
NumPy reference. The parallel CPU path exists in the Rust core (`core/src/par.rs`). Not built: the live HDR
view, a GPU array back end for the Python functions, and batches above 4.19 million values per call.

**Where it sits in the order.** After 3.1, because the table is what the shader samples. It then overlaps with
two existing plan items and should be built with them, not apart: the WebAssembly/JS core (the live view) and
the app work, level 2 (an HDR canvas in a viewer).

### 3.7 Not proposed

- **A GPU path for figure export.** See the first row of the table in 3.6.
- **Rewriting in Rust for speed.** See section 2. Rust remains worth doing for other reasons (a library other
  apps can call; an independent implementation, and the parallel CPU path), and it would inherit 3.1 and 3.3
  as its design.

## 4. Expected result

| | Now | After 3.1–3.4 (estimate) |
|---|---|---|
| grey figure, `savefig_hdr` | 2.8 s | ~1.2 s |
| fire figure, build + save | ~90 s | ~3 s |
| test suite | ~30 s | under 10 s |
| gallery build (six datasets) | minutes | mostly data loading |

The "after" column is an estimate from the parts above, not a measurement.

## 4a. Result of 3.1, measured (2026-10-08)

The colour-map table is in (`udeep.cmap`, tables in `src/udeep/tables/`). Same machine, same script, same
caveat about spread.

| | Before | After |
|---|---|---|
| colour map `fire`, one megapixel, one pass | 6,070–16,645 ms | 111 ms |
| whole mapping (`hdr_view`), fire | ~36,000 ms | 561 ms |
| levels and precision (`data_steps`), fire | 1,724 ms | 47 ms |
| figure with `fire`: build image and colour bar | ~80 s | 1.2 s |
| figure with `fire`: `savefig_hdr` | ~11 s | 3.9 s |
| gallery build, six datasets | minutes | 54 s |
| demo build, two figures | — | 12 s |

Effect on the output, checked on the rebuilt gallery: in the SDR figures of GW250114 and M51, 0.010 % of pixels
changed, each by one 8-bit code. Every number in the gallery report is unchanged, and all nine site files pass
the validator.

`savefig_hdr` is now the largest cost, and it is the part 3.2 to 3.4 address. The grey figure read 5.4 s in this
run against 2.8 s earlier with no change to that path, which is the spread of this machine, not a regression;
3.2 to 3.4 need a quieter benchmark before their gains are quoted.

## 4b. Result of 3.2 to 3.4, measured (2026-10-08)

All three are in, and all three are exact: a test rebuilds the composition the old way and requires identical
arrays, and the rebuilt gallery is byte-identical (same files, same digests).

- **3.2** took a simpler form than proposed. The cropped render still goes through an in-memory PNG, because
  that is what guarantees `savefig`'s own layout, but the PNG is now written uncompressed.
- **3.3** as proposed: 8-bit renders are decoded through a 256-entry table.
- **3.4** as proposed: the restoring draw is gone; a test confirms the figure saves and redraws as the SDR figure
  afterwards.

`tools/benchmark.py`, same machine, same caveat about spread:

| | Before | After |
|---|---|---|
| one cropped render | 508 ms | 344–385 ms |
| compose (the renders and the arithmetic) | 1,813 ms | 940–1,110 ms |
| `savefig_hdr`, whole | 2.8–5.4 s (grey), 3.9 s (fire) | 1.5–2.3 s |
| gallery build, six datasets | 54 s | 48 s |

What is left in `savefig_hdr` is two matplotlib renders (about 0.7 s) and the gain-map JPEG (about 0.65 s). Both
are compiled code already; there is no further cheap saving.

**New in this benchmark:** running the mapping backwards (`Mapping.value`) on a whole megapixel takes 7.8 s in
Python: 60 bisection steps, each a full tone-curve pass. It is rarely applied to whole images, and the Rust core
does it per value in a tight loop, but if it becomes a hot path the cubic should be inverted in closed form.

## 4c. After the review of 2026-10-08: four renders, not two

`savefig_hdr` now renders the figure four times: as it is, with the images scaled, with the images black and
with the images white. The last two show which pixels the image alone determines, and only those are lifted
(design decision D28). This is a correction, not an optimisation, and it costs two more cropped renders.

`tools/benchmark.py`, the same 8 × 8 inch figure at 150 dpi, best of 2. Two runs were made, the first when the
change went in and the second a little later, both on a machine doing other work, so they show the spread more
than the cost:

| | `savefig_hdr` before | `savefig_hdr` after | of which compose, after |
|---|---|---|---|
| grey, first run | 1,136 ms | 1,499 ms | 1,120 ms |
| fire, first run | 1,026 ms | 1,482 ms | 1,057 ms |
| grey, second run | not measured | 3,223 ms | 2,461 ms |
| fire, second run | not measured | 3,888 ms | 2,497 ms |

The first run puts the added cost at 0.36 to 0.46 s. One cropped render took about 0.2 s in the first run and 0.41
to 0.47 s in the second. The statement in 4b that two renders are left is superseded: four are, and with the
gain-map JPEG they are still all of the cost. A quieter benchmark is needed before a figure is quoted.

## 4c. Memory (2026-10-09)

Memory, not processor time, is what limits the size of image the Python reference can take. Measured with
`tracemalloc`, in bytes per pixel of the data (float64 data is 8):

| | Before | After |
|---|---|---|
| building a view (`hdr_view`), peak | 228 | 67 to 76 |
| building a view, held afterwards | 80 | 56 |
| `hp.imshow` of 9 megapixels, peak | not measured | 110 (990 MB) |
| held by the figure after `imshow` | not measured | 80 (720 MB) |
| `savefig_hdr` of that figure, peak | not measured | 148 (1,333 MB) |

What changed, none of it changing a value (a test compares the pieces with the whole; the rebuilt gallery is
byte-identical):

- The three whole-image steps (position, light, colour) and the sRGB encoding are worked through 65,536 values
  at a time. Each makes several arrays as large as its input on the way to its answer; done whole, those were
  most of the peak.
- A view no longer keeps the full-range SDR rendering. Nothing used it, and it was a third copy of the image in
  colour.
- The position array is released as soon as the light is known.

What is left is the result itself: two renderings in float64 colour (48 bytes a pixel), the light (8), and
matplotlib's own copy of what it draws (24). Going lower means holding the renderings in float32, which would
change output values and so needs a decision, or not holding both at once.

**Decided 2026-10-10: float32 renderings are an option, off by default** (`hdr_view(..., float32=True)`,
`udeep.mpl.imshow(..., float32=True)`). The mapping is still worked out in float64; each colour is rounded once
as it is stored. Measured on a megapixel: a view holds 32 bytes a pixel in place of 56, and peaks at 44 (grey) to
52 (fire) in place of 66 to 76. A float32 rendering is the float64 one cast, within 1.2 parts in 10 million; in a
saved figure under one pixel in a hundred moved, by one 8-bit code, which changes the SDR digest. That is why it
is not the default: the reference's numbers stay the exact ones.

So, as a guide: a 16-megapixel image needs about 1.1 GB to map and about 2.4 GB to save as a figure; a
100-megapixel one needs about 15 GB and wants the Rust core or tiling instead.

## 4d. Stress run, 2026-10-10: is the fast path worth it?

`tools/stress.py 64`, on the development machine (12th-generation Core i7, 20 threads, 32 GB, RTX 5070 Ti), other
work running. Best of two or three runs; a single number is good to perhaps 30 % (the 16-megapixel row of the first
table caught a slow stretch), the ratios between columns are steadier.

    
    THE MAPPING, asinh stretch, fire map, headroom 4. Seconds; megapixels a second in brackets. 20 threads.
    
        MP |    NumPy light     Rust 1 thr       Rust all |   NumPy colour    Rust colour |   GPU, all three
      ------------------------------------------------------------------------------------------------------
         1 |   0.08 (   12)   0.03 (   39)   0.01 (  132) |   0.15 (    7)   0.01 (   79) |     0.01 (   91)
         4 |   0.32 (   12)   0.16 (   24)   0.04 (  113) |   0.38 (   10)   0.03 (  116) |     0.08 (   50)
        16 |   2.00 (    8)   1.22 (   13)   0.26 (   61) |   2.69 (    6)   0.24 (   67) |     0.35 (   46)
        64 |   5.18 (   12)   3.70 (   17)   0.26 (  251) |   9.44 (    7)   0.80 (   80) |     1.18 (   54)
    
    A VIEW (hdr_view, highlight, fire): the mapping, the mask and both renderings. Bytes a pixel from tracemalloc.
    
        MP |  float64 s   peak   held |  float32 s   peak   held
         1 |       0.44     76     56 |       0.44     52     32
         4 |       1.44     69     56 |       1.21     45     32
        16 |       6.31     67     56 |       5.66     43     32
        64 |      28.71     66     56 |      37.04     42     32
    
    A FILE: the gain-map JPEG writer on a figure-like image, fire map, headroom 4 (seconds; correcting passes).
    
        MP |  Python s passes |   Rust s | Python / Rust
         1 |      0.65      8 |     0.56 |           1.2
         4 |      3.65     10 |     1.60 |           2.3
        16 |     15.13     16 |     9.21 |           1.6
    
    A WHOLE FIGURE with matplotlib (1 MP of data, fire): where a save's time goes, in seconds.
    
            figure |  imshow plain savefig savefig_hdr | of which: 4 renders declaration  writer
            1.1 MP |    0.58          0.43        3.46 |                1.90        0.03    0.88
            4.5 MP |    0.68          0.91        8.46 |                3.32        0.03    2.91
           16.3 MP |    0.59          3.07       24.41 |               11.00        0.04    9.57

What it says:

- **The mapping.** The Rust core on all threads maps 60 to 250 megapixels a second, against 8 to 12 for NumPy:
  5 to 20 times. Its colour lookup is about 10 times NumPy's on one thread. Both implementations agree to 1e-12,
  and the threaded result is the single-threaded one bit for bit (checked in the run).
- **The GPU does not beat the threaded core** for making files: 45 to 90 megapixels a second with upload and
  read-back, in float32. Its place is a live view, where the result stays on the card.
- **None of that is where a figure's time goes.** With a megapixel of data the mapping and both renderings take
  about 0.4 s. Saving the figure takes 3.5 s at 1 megapixel of figure and 24 s at 16, eight times a plain
  `savefig`: about half is matplotlib drawing the figure four times, and the rest is the gain-map writer's
  correcting passes.
- **The Rust writer is 1.2 to 2.3 times the Python one**, no more: both spend their time in the JPEG codec.
- **float32 renderings save memory, not time**: 32 bytes a pixel held for 56, 42 to 52 at the peak for 66 to 76.

So, for what is worth it: the Rust core earns its place for large images (16 megapixels and up) and for anything
interactive, but the Python front end does not call it yet, so today only Julia gets that speed. For an ordinary
figure the next gain is not in the mapping at all: it is in drawing the figure fewer than four times, and in the
writer's passes.

**Acted on, 2026-10-11: Python can use the core.** `udeep.core.enable()` makes `hdr_view`, and so
`udeep.mpl.imshow`, map and colour through the Rust library on every processor core. Off by default, because
the core agrees with NumPy to about 1e-12 and not bit for bit, so an 8-bit code could turn at a few pixels and a
figure's SDR digest with it. A whole view (highlight, fire), one run of each on the same machine, other work
running:

| megapixels | NumPy | with the core | times faster |
|---|---|---|---|
| 1 | 0.69 s | 0.06 s | 12 |
| 4 | 2.31 s | 0.95 s | 2.4 |
| 16 | 7.19 s | 1.66 s | 4.3 |
| 64 | 36.4 s | 3.59 s | 10 |

The spread between rows is the machine, not the method: throughout this run and the stress run above, another
long job was using about twelve of the machine's twenty threads (found afterwards), so every time here is on a
loaded machine and the threaded figures are likely to be pessimistic. They have not been repeated on a quiet one. What a figure's save costs after that is unchanged: the
four renders and the writer.

## 4e. The Rust writer, optimised (2026-10-11)

Timing every JPEG call inside the writer showed that about half its time was not in the codec: it was arithmetic
done once for each sample on one thread (a power for the sRGB signal, a logarithm for the gain, another for the
ceiling). Two changes, measured by the best of four runs on fixed inputs, alternating with the old build:

| input | before | per-sample work on all threads | and the small image laid out for the decoder |
|---|---|---|---|
| figure, 1.1 megapixels | 445 ms | 167 ms | 128 ms |
| figure, 4.5 megapixels | 2,322 ms | 932 ms | 443 ms |
| noisy image, 4 megapixels | 1,936 ms | 1,609 ms | 632 ms |

1. **The per-sample work runs on every processor core, with tables of 256 entries** where a value depends only
   on an 8-bit code (the base's linear light, the ceiling). The files are the same, byte for byte.
2. **The small image of blocks is laid out as the decoder will treat them.** The JPEG decoder converts colour
   eight pixels at a time with vector code, except the last eight or more of each row, which go through scalar
   code that rounds differently (`jpeg-decoder` 0.3, `arch/ssse3.rs`). That was why a block corrected on its own
   did not quite decode as it does in the whole image, and why the whole map had to be coded four to six times.
   Blocks from the end of a row now go at the end of a row of the small image. On x86 with SSSE3 the fast way and
   the plain way give the same bytes (tested on 18 sizes and maps), the whole map is coded twice, and the
   quarter-code margin is no longer applied unless a round fails.

On the 271-figure set nothing reads above its headroom on the core's decoder, as before, and the top of the
range reads a little less low (worst 1.8 % for 2.1 %). What is left in the writer is three encodes and three
decodes of the whole image by a single-threaded codec.

## 4f. A figure's save, shortened (2026-10-11)

Three changes to `savefig_hdr`. The file written is the same, byte for byte, and the SDR render is still
`savefig(bbox_inches="tight")`'s, pixel for pixel (both tested).

1. **The figure is laid out once for a save.** `savefig(bbox_inches="tight")` lays the figure out with nothing
   drawn, to find its box, before every render; that pass still resamples every image. The box is now worked out
   once, as savefig works it out, and given to each of the four renders: five passes over the figure for eight.
2. **Each render is taken as raw pixels**, not as a PNG written to memory and read back.
3. **The core goes from the four renders to the file** (`gainmap_from_renders`). The two float64 figures that
   were made from the renders, 48 bytes a pixel, are not made, and the base image is not turned to linear light
   and back to the codes it started as.

Old save against new, alternating in one process, best of six:

| figure | processor time, old | new |
|---|---|---|
| 1.1 megapixels | 1,781 ms | 1,047 ms |
| 4.5 megapixels | 4,391 ms | 2,734 ms |

Processor time, because elapsed time on the machine that day ran from 3 to 46 s for the same save. It counts
every thread, so it understates the gain where the new path works on several at once. The core's reader also
decodes its two JPEGs on a thread each. What is left is matplotlib drawing the figure four times.

## 4g. What the first run on Linux found (2026-10-11)

The tests had only ever run on one Windows machine. Their first run on GitHub's Linux machines failed in two
places, and both were faults of ours, not of the tests' machine. The causes below were then isolated the same day
on a Linux machine in the cloud (Ubuntu 24.04, 4 virtual processors with AVX-512, glibc 2.39, NumPy 2.4.6 with
OpenBLAS 0.3.31, rustc 1.97.0). It is a shared machine, so every timing is given with its spread. An earlier
version of this section gave both causes as assumptions, and described the table difference wrongly as "a few
units in the last place"; it is corrected here.

### A small call into the core was slow

With `threads = 0` (every available core) the core asked the system how many cores it had on every call.

- **The count itself takes about 13 µs.** `std::thread::available_parallelism()` alone: median 12.9 to 13.7 µs a
  call over three runs of 200 batches of 1,000 calls (90th percentile 17 to 21 µs).
- **It reads files every time.** Traced with `strace`: 22 system calls a call, among them opening and reading
  `/proc/self/cgroup` and the process's CPU limit files, and `sched_getaffinity`. The machine had version-1
  control groups; Rust's source notes that this case may also scan the mount points.
- **Rust says not to do what we did.** The standard library's own description of the function (rustc 1.97,
  `library/std/src/thread/functions.rs`) says the value is "not cached and instead recomputed every time this
  function is called. It should not be called from hot code."
- **The fix removes it.** The core now counts the cores once, and not at all when there are too few values for a
  second thread (`par::threads_for`). One value, median microseconds a call, with the 10th and 90th percentiles
  (201 samples, each the mean of 300 calls, the two versions interleaved):

  | | serial | threads = 1 | threads = 0 | threads = 8 |
  |---|---|---|---|---|
  | before | 0.66 [0.64, 0.99] | 0.77 [0.74, 1.16] | 13.0 [12.6, 17.1] | 0.77 [0.74, 1.17] |
  | after | 0.67 [0.64, 1.01] | 0.77 [0.74, 1.18] | 0.78 [0.74, 1.19] | 0.76 [0.73, 1.19] |

- **The parallel path is unchanged.** Four million values, median milliseconds (21 samples): serial 18.5 before
  and 18.2 after; `threads = 0` 6.8 before and 7.3 after, a difference inside the spread (5.2 to 8.6).
- **What caching gives up.** A CPU limit changed while the process runs is not seen. The failing test on GitHub
  had measured 36.9 µs against 1.56 µs for the serial call: one measurement, on another shared machine.

### The colour tables are not regenerated bit for bit

- **Only the version-2 tables, and only where they are almost zero.** Of the 24,582 numbers in a table, 975
  (`fire/2`) and 1,789 (`ice/2`) differed from the shipped ones, all in the segment below white, the same at every
  headroom. Every one of them is a value below 4e-8 of white, and the largest difference is 7e-13 of white. As a
  fraction of such a small number that is large: up to 0.3 %, or about 42,000 units in the last place of a float32.
  Nothing a screen shows is affected, and the test's tolerance (2e-6, absolute) is far wider.
- **Why version 2 and not version 1.** Where a colour is held at the edge of the gamut, version 1 leaves an exact
  zero in the channel; version 2 leaves the small remainder of the arithmetic (about 3,000 such values in a
  table). A remainder near zero is the difference of nearly equal numbers, so the last bit of an earlier step
  decides it.
- **The cause is not the operating system's mathematics library.** Two things change those last bits, and both
  are chosen by the processor, not by Linux or Windows:
  - *NumPy's power function.* With its AVX-512 routine, 5 to 8 % of the powers on this path are one unit in the
    last place away from the correctly rounded result (refereed with mpmath at 200 bits). Turning that routine
    off (`NPY_DISABLE_CPU_FEATURES`) changes the tables.
  - *The matrix inverse.* The three inverted colour matrices come from OpenBLAS, which picks a routine for the
    processor; another choice (`OPENBLAS_CORETYPE`) gives inverses that differ in the last bits, and other tables.

  So two machines running the same system can disagree, and the tables shipped are those of the machine that
  made them.
- **Which is nearer right.** Against tables computed with correctly rounded powers, the shipped `fire/2` table
  differs in 251 numbers and the one made on this Linux machine in 822.
- **What would make it reproducible.** Setting to zero every table value below 1e-7 gave the same bytes under
  every combination tried, and the same bytes as the shipped tables treated the same way. That is a change to the
  table, so it is a new version: `fire/3` and `ice/3`, made on 2026-10-12. On the Windows machine (no AVX-512) the
  generator gave the same version-3 bytes with NumPy's AVX2 routines on and off and under three OpenBLAS routines
  (Haswell, Sandybridge, Prescott), two of which do not reproduce version 2 even there; the digest of the eight
  tables is the one the Linux machine had found for the same treatment. The test now requires version 3 to be
  regenerated bit for bit, so every run on GitHub's Linux and Windows machines checks it. Computing with exact inverses and correctly rounded
  functions (mpmath) is also reproducible, at about 43 s for two tables against 0.14 s.

The shipped table is the definition (profile 6.10), so nothing a file means has changed. Two things relied on
exact regeneration and no longer do: the test, which holds the generator to the table tolerance, and
`tools/make_tables.py`, which never rewrites a table that exists.

Neither is a fault in Rust, NumPy or OpenBLAS, and none has been reported to them: Rust documents the cost, and
no library promises correctly rounded powers or bit-identical inverses across processors.

## 4h. Newer language features, measured (2026-10-22)

`tools/speed_rig.py` times each faster way of doing the work against the plain way, on the same input, and
checks the result is the same. Every variant gave the plain way's result: value for value for the NumPy code and
the WebAssembly core, byte for byte for the Rust core's builds, except where the table says otherwise. Machine:
Windows 11, an Intel processor with 20 logical cores, with other work running, which made single runs vary by up
to a few times. So the variants are taken in turn within each round (NumPy, browser), or in rounds of separate
processes (core, where the fastest run is the steadier figure); read differences under about 10 % as none.

**The NumPy code on several threads** (the core turned off; Python 3.13 with the interpreter lock, and the
free-threaded Python 3.14 without it). Medians of 5; the results identical on every count:

| Python | work, 16 MP | 1 thread | 4 threads | 8 threads | 20 threads | peak memory, 1 / 20 threads |
|---|---|---|---|---|---|---|
| 3.13.15 | light | 0.6773 s | 0.2125 s (3.2x) | 0.1464 s (4.6x) | 0.1993 s (3.4x) | 249 / 329 MB |
| 3.13.15 | colours | 1.196 s | 0.3955 s (3.0x) | 0.3184 s (3.8x) | 0.3112 s (3.8x) | 376 / 538 MB |
| 3.13.15 | view | 3.1763 s | 1.1766 s (2.7x) | 0.9392 s (3.4x) | 1.006 s (3.2x) | 1017 / 1181 MB |
| 3.14.7 free-threaded | light | 0.7902 s | 0.2492 s (3.2x) | 0.1511 s (5.2x) | 0.1347 s (5.9x) | 249 / 313 MB |
| 3.14.7 free-threaded | colours | 1.2656 s | 0.4118 s (3.1x) | 0.2698 s (4.7x) | 0.2192 s (5.8x) | 376 / 466 MB |
| 3.14.7 free-threaded | view | 3.6185 s | 1.2113 s (3.0x) | 0.833 s (4.3x) | 0.7052 s (5.1x) | 1017 / 1096 MB |

Adopted: the NumPy code works through an image's chunks on a pool of threads (`udeep.scale.in_chunks`), every
core on a free-threaded Python and at most 8 with the lock (`udeep.core.GIL_THREADS`), where more threads wait on
it. One setting, `udeep.core.THREADS` or `UDEEP_THREADS`, now sets the thread count for this and for the core.
The cost is memory: each thread holds a chunk's working arrays, up to about 160 MB more at the peak here. A race in
the core's first load was found on the way and fixed: a thread could take the library as absent while another was
still loading it, and turn the core off for the rest of the process (`tests/test_threads.py`, with the old loader
as its control: 7 of 8 threads got no core).

**The Rust core, by instruction set.** The fastest of 15 runs: as the wheels carry it, for any 64-bit x86
processor; the whole core built for x86-64-v3 (AVX2, FMA); and with the JPEG encoder's own SIMD:

| work | as the wheels carry it | built for x86-64-v3 | with the JPEG encoder's SIMD |
|---|---|---|---|
| light, 1 thread | 0.443 s | 0.4328 s (1.02x) | 0.4471 s (0.99x) |
| light, every core | 0.0664 s | 0.0598 s (1.11x) | 0.0561 s (1.18x) |
| colours, 1 thread | 0.1947 s | 0.1679 s (1.16x) | 0.1945 s (1.00x) |
| colours, every core | 0.0368 s | 0.0354 s (1.04x) | 0.041 s (0.90x) |
| gain-map JPEG, 4 MP | 0.8198 s | 0.7549 s (1.09x) | 0.7866 s (1.04x), other bytes |
| 16-bit PNG, 4 MP | 0.7836 s | 0.8063 s (0.97x) | 0.8065 s (0.97x) |

Not adopted. A wheel built for x86-64-v3 would not run on an older processor, and choosing the core's own loops
at run time instead was tried (`#[target_feature]`, with the processor asked once) and gained at most 5 %,
within the noise, so it was removed. The JPEG encoder's SIMD gains little and writes other bytes, so the same
figure would make a different file on another machine.

**The browser: the JavaScript port and the WebAssembly core**, in node, medians of 7, and the WebAssembly core
built with SIMD (`simd128`):

| work | MP | JavaScript port | WebAssembly core | with simd128 |
|---|---|---|---|---|
| position | 0.26 | 0.0349 s | 0.0086 s (4.1x) | 0.0085 s (1.01x) |
| position | 1 | 0.3152 s | 0.0784 s (4.0x) | 0.0836 s (0.94x) |
| position | 4 | 1.5175 s | 0.362 s (4.2x) | 0.3592 s (1.01x) |
| light | 0.26 | 0.0658 s | 0.0103 s (6.4x) | 0.0104 s (0.99x) |
| light | 1 | 0.5719 s | 0.0916 s (6.2x) | 0.0898 s (1.02x) |
| light | 4 | 2.2505 s | 0.438 s (5.1x) | 0.4332 s (1.01x) |
| colours | 0.26 | 0.0124 s | 0.0076 s (1.6x) | 0.0073 s (1.04x) |
| colours | 1 | 0.0969 s | 0.049 s (2.0x) | 0.0495 s (0.99x) |
| colours | 4 | 0.3935 s | 0.222 s (1.8x) | 0.2791 s (0.80x) |
| held pixels | 0.26 | 0.0417 s | 0.0129 s (3.2x) | 0.0128 s (1.01x) |
| held pixels | 1 | 0.3732 s | 0.1293 s (2.9x) | 0.1261 s (1.03x) |
| held pixels | 4 | 1.6797 s | 0.5645 s (3.0x) | 0.5163 s (1.09x) |

Adopted: the live view's numbers (the held pixels, the readout under the pointer, the self-check's reference)
come from the WebAssembly core, 1.6 to 6.4 times faster than the port, which stays as its fallback. Not adopted:
SIMD, which gains nothing here; the hot loops call `powf`, `asinh` and `ln`, which the compiler does not vectorise.
The site's WebAssembly is built from the source when the site is published (`.github/workflows/pages.yml`), so the
repository holds no binary.

## 5. Order

1. **3.1, colour map as a table.** Largest gain, and it settles how colour maps are defined. Needs: regenerate
   the colour-map golden vectors, update the JavaScript port to interpolate the same table, rebuild the
   gallery, note the change in the profile.
2. **3.3 and 3.4.** Exact and small.
3. **3.2.** Only if the alignment test passes pixel for pixel.
4. Measure again and replace the estimates in section 4 with numbers.
5. **3.6, the GPU path:** the reference shader checked against the golden vectors, then the live HDR view.
   The parallel CPU path comes with the Rust core.

A benchmark script (`tools/benchmark.py`, the stage timings above) should be kept so a later change can be
compared with these figures.
