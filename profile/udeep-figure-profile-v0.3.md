# UDeep Figure Profile — draft v0.3

Status: **working draft**, 2026-10-12. Not endorsed by any standards body. Editor: Mathew Newman
(ORCID 0000-0003-1784-3250). Reference implementation: `udeep` (Python), with a JavaScript port, a Rust core and
golden test vectors (§10). Supersedes v0.2 (2026-09-27); changes from v0.2 are listed in Annex D, and changes made
since the first v0.3 draft of 2026-10-06 in Annex E.

**The name.** UDeep is short for *Ultra Deep Range*: range is how far above white a figure reaches, and depth is how many levels it
holds. A UDeep figure has more of both than an ordinary one. Until 2026-10-11 this
draft was titled the Scientific HDR Figure Profile, its reference implementation was `hdrplot` and its metadata prefix
was `scihdr`; v0.1 and v0.2 are kept under that title.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as in RFC 2119.

## 1. Scope

Turning scientific data into light has no standard. Figures discard range that the data holds and that
modern screens could show, and they do not record how data became brightness. This profile specifies a
**data-referred** figure: one whose brightness is a declared, invertible function of measured data. It
covers how that figure is stored, checked and delivered, so that it

1. states inside the file how data became brightness, with the data's units and uncertainty,
2. remains a complete, correct figure on standard-dynamic-range (SDR) displays and in print,
3. can use the extended range and precision of high-dynamic-range (HDR) displays, and
4. keeps its value as displays improve.

The profile applies to figures that show values as brightness or colour: images, heatmaps, spectrograms
and 2-D histograms. Line, scatter and bar plots carry their value by position; there, HDR MAY be used only
as a flag (for example a threshold crossed), never to carry the value.

It is a profile of existing standards. It defines no new image or file format.

## 2. Normative references

- ISO 22028-1 — extended colour encodings: architecture and image states.
- ISO/TS 22028-5:2023 — HDR/WCG colour encodings for still images (BT.2100 PQ and HLG; nominal diffuse
  white; scene- and display-referred metadata). A revision to a full International Standard is in progress.
- ISO 21496-1:2025 — gain-map metadata for image conversion (dynamic-range conversion).
- ITU-R BT.2100 — HDR image parameter values (PQ, HLG, BT.2020 primaries).
- SMPTE ST 2084 — the Perceptual Quantizer (PQ) transfer function.
- ITU-R BT.2408 — HDR operational practice; reference (diffuse) white 203 cd/m².
- ITU-R BT.2390 — HDR report; the EETF roll-off for fitting content to a display.
- ITU-T H.273 / ISO/IEC 23091-2 — coding-independent code points (CICP).
- DICOM PS3.14 — Grayscale Standard Display Function (GSDF; Barten model).
- ISO 16684-1 (XMP) — metadata packets.
- W3C SVG 2; W3C CSS Color 4 and the CSS Color HDR draft; PNG (third edition, `cICP` chunk).
- W3C WCAG 2.1, SC 2.3.1; ITU-R BT.1702 — flashing thresholds.

## 3. Terms

- **Nominal diffuse white** (ISO 22028-5): the display's reference white for ordinary content. In this
  profile it has the value 1.0 and is also called **SDR white**. Its default luminance is 203 cd/m² (BT.2408).
- **Data-referred image state**: an image whose light is a declared function of measured or computed data
  values. It stands beside the scene-referred and display-referred states of ISO 22028-1.
- **Declared range**: the interval of data values, from *low* to *high*, over which the mapping is defined.
- **Mapping**: the declared function from a data value to light, in multiples of nominal diffuse white.
- **Knee**: the data value mapped to nominal diffuse white. Values above it may be shown brighter than white.
- **Headroom**: the ratio of a display's peak to its nominal diffuse white. The **reference headroom** is the
  value the figure was authored for.
- **Fair SDR rendering**: the figure rendered with values above the knee clipped at SDR white.
- **Data step** Δx_q: the change in data value that corresponds to one code step of an encoding (§7.2).
- **Master**: the stored data and the declared mapping, from which all renderings are produced.
- **Data layer**: the part of a figure that carries values as brightness (the image, the colour bar).
- **Furniture**: axes, ticks, labels, legends and annotations.

## 4. The model

A conforming figure is described in five layers. Layers 1 to 3 are normative and can be checked from the
file. Layers 4 and 5 are guidance.

| Layer | Content | Status |
|---|---|---|
| 1 · Data | values, units, uncertainty, validity mask (§5) | normative |
| 2 · Mapping | a declared, invertible function from value to light (§6) | normative |
| 3 · Precision | encoding precision judged against the data (§7) | normative |
| 4 · Display rendering | fitting to a particular screen and viewer (§12) | informative, except the viewer rules in §11 |
| 5 · Presets | named mappings such as Highlight and Extend (§13) | informative |

Perception enters only at layer 4. No requirement in layers 1 to 3 is based on how a figure looks.

## 5. Layer 1: the data

- 5.1 The master MUST keep the data at its original precision (float where the data is float), with its
  **unit**.
- 5.2 The master MUST state the data's **uncertainty** as one of these noise models, with its parameters:

  | Model | Meaning | Parameter |
  |---|---|---|
  | `constant` | the same standard deviation everywhere | σ, in data units |
  | `poisson` | counting statistics, σ = √(value × scale) | scale |
  | `relative` | σ proportional to the value | the fraction |
  | `tolerance` | no noise; a declared numerical tolerance | the tolerance, in data units |
  | `unknown` | not stated | — |

  Every parameter is a finite number greater than zero. A `tolerance` is absolute: the same number of data units
  at every value. A tolerance that is a fraction of the value cannot be declared with this model.
  With `unknown`, the figure cannot claim to be a data record (§7.3).
- 5.3 The master MUST carry a **validity mask** for saturated, clipped, bloomed or otherwise invalid pixels.
  Masked pixels MUST NOT be rendered above SDR white. They SHOULD be marked, for example with a HiLo-style
  indicator.

## 6. Layer 2: the mapping

- 6.1 **Units.** The mapping's output MUST be linear light in multiples of nominal diffuse white. Absolute
  luminance MUST NOT appear in the declared mapping; a display's diffuse-white luminance converts to it.
- 6.2 **Global.** The mapping MUST be a single function of the data value, the same everywhere in the image
  (after the mask). A figure of record MUST NOT use local or content-adaptive operators, such as local tone
  mapping, zoom-adaptive stretches or CFAR-style local thresholds. Exploratory views that are labelled as such
  MAY use them.
- 6.2a **Masks are the only neighbourhood operation.** *Added 2026-10-08.* The mapping is applied to each pixel
  alone. Where a figure of record needs a rule that looks at neighbours (for example, holding isolated pixels
  above the knee so that noise cannot sparkle), that rule MUST run before the mapping and produce a mask, and
  the file MUST declare the rule and the number of pixels it holds (`udeep:DerivedMask`, `DerivedMasked`). Masked
  pixels take their fair SDR light. The rule `isolated-3x3` considers the pixels that are above the knee and not
  in the validity mask. It holds every such pixel that lies in no 3 × 3 block made up entirely of such pixels;
  positions outside the image do not count. *Amended 2026-10-08:* pixels in the validity mask no longer count
  towards a block.
- 6.3 **Invertible.** The mapping MUST be strictly increasing over the declared range, so that every light
  level corresponds to exactly one data value. Its slope MUST be greater than zero from *low* to *high*.
  A mapping that flattens within the declared range does not conform. The roll-off of the BT.2390 EETF, which
  ends flat at the peak, is such a mapping: it belongs to display rendering (§12), not to the declared mapping.
- 6.4 **Stated inverse.** The file MUST declare the mapping completely enough to compute its inverse: the
  stretch, its end points, the knee, the form of the segment above the knee, and the reference headroom (§8).
  Every number in the declaration MUST be finite.
- 6.5 **The knee.** The knee MUST be given in data units together with its rule, which is one of:
  - a physical threshold (for example GOES flare class M, 1e-5 W m⁻²);
  - a statistical threshold (for example a stated false-alarm probability);
  - a domain preset (for example the top of a CT window);
  - a percentile. A percentile knee MUST be labelled exploratory.

  *Added 2026-10-08.* The knee MUST lie inside the declared range and above its low end. Its position in the
  range, measured after the stretch (0 at *low*, 1 at *high*), MUST be at least 10⁻⁶, and MUST be either exactly 1
  or no more than 1 − 10⁻⁶. A knee closer to either end cannot be drawn where it is declared, so such a
  declaration is refused, not adjusted. A knee exactly at the top is allowed: it is the ordinary plot, with nothing
  above white.
- 6.6 **Data that is light.** Where the data is itself luminance or radiance (calibrated images, luminance maps,
  surface brightness), the mapping SHOULD be **proportional**: light = value ÷ (value at diffuse white). A display
  then shows true ratios within its range. *Added 2026-10-08.* A proportional mapping is a linear stretch from
  zero with no offset. Its declared range MUST end at knee × reference headroom (to one part in 10⁹), and its
  segment above the knee MUST be the reference form of 6.7, which is then the same straight line as below the
  knee. Light is then value ÷ knee over the whole range.
- 6.7 **Above the knee.** The mapping MUST NOT exceed the reference headroom at the top of the declared range.
  It SHOULD be continuous in slope at the knee. The **reference form** is a cubic Hermite segment from the knee
  (light 1) to the top of the range (light = reference headroom). Its entry slope equals the slope below the knee,
  capped at three times the segment's secant slope. Its exit slope equals the secant slope. Its slope is then never
  below min(a, (4 − a)/3) of the secant, where *a* is the entry slope in units of the secant; that is at least one
  third. It is recorded as `udeep:AboveKnee = "hermite; exit=1"`.
  - The exit slope MAY be declared as another number, `exit=`*s*, in units of the secant slope, with 0 ≤ *s* ≤ 3.
    *s* = 0 is the flat-ended curve, which does not conform (6.3).
  - *Added 2026-10-08.* The reference headroom belongs to the mapping: the segment above the knee and the colours
    above white (6.10) are made for it. A writer MUST NOT store a figure under a reference headroom other than
    the one its data was mapped at. To change the headroom, the data is mapped again.
- 6.8 **The SDR rendering.** Every figure MUST have a fair SDR rendering. Below the knee it is identical to the
  HDR rendering; above it, values are clipped at SDR white. All conclusions drawn from the figure MUST be
  supported by the SDR rendering alone. In publication it is the figure of record.
- 6.9 **Colour.** HDR deliveries SHOULD use BT.2020 primaries. A colour map used for a data-referred figure
  MUST have strictly increasing lightness, so that the mapping stays invertible. *Stated 2026-10-10:* lightness is
  Jz of Jzazbz, compared from each node of the map's table to the next; a registered map MUST also rise so under
  simulated protanopia, deuteranopia and tritanopia (Machado et al. 2009, full severity), so that order can be
  read from lightness alone by a viewer with any of them.
- 6.10 **A colour map is a table.** *Added 2026-10-08.* A map from light to colour is defined by a table of two
  segments, light 0 to 1 and 1 to the reference headroom, each of N evenly spaced nodes holding linear RGB
  (BT.709 primaries, multiples of diffuse white). Colours between nodes are linear interpolations in linear light.
  The table is the definition: every implementation that interpolates the same numbers gets the same colours.
  - **Registered maps** are named `name/version` and are published as tables of 4,097 nodes per segment for the
    reference headrooms 2, 4, 8 and 16. Below white a registered map does not depend on the headroom. A version,
    once published, is never changed and never withdrawn: a correction is a new version. Registered today:
    `fire/1`, `ice/1`, `fire/2`, `ice/2`, `fire/3` and `ice/3`; version 3 is the one to use (§10, point 10).
  - *Added 2026-10-11.* An implementation MUST take a registered map from its published table, and MUST NOT
    compute it afresh. The program that made the tables does not give the same numbers on every machine: its
    results depend on which processor-specific routines its numerical libraries choose. Where a version-2 map
    holds a channel at the edge of the gamut, the table has a value that is almost zero (below 4e-8 of white),
    and there the recomputed tables differed from the published ones, by at most 7e-13 of white (observed on
    Linux, 2026-10-11; the tables were made on Windows). That is far below anything a screen shows, but it is not
    the same table, and the table's digest would differ. *Added 2026-10-12.* Version 3 has no such values (§10,
    point 10), and the program gave its tables bit for bit on every machine and setting tried. The rule stands all
    the same: the published table is the definition, and the program is only how it was made.
  - **Any other map, or any other reference headroom,** MUST be carried in the file as its table
    (`udeep:ColourMapTable`). An embedded table MAY have fewer nodes.
  - `grey` is the identity: colour equals light on all three channels.
- 6.11 **Where other marks cover the data.** *Added 2026-10-08.* In a figure made of a data layer and furniture, a
  pixel MAY be shown above SDR white only where the data layer alone determines its colour. A pixel to which
  anything else contributes (a legend, a partly transparent patch, text, the soft edge of a line, a data layer
  drawn with transparency) MUST have its fair SDR colour, and so the data under it is not lifted. Values above
  the knee are therefore shown no brighter than SDR white wherever something is drawn over them.

## 7. Layer 3: precision

Two budgets are kept apart: what the data holds, and what the eye can see.

### 7.1 Display precision (visibility)

The smallest visible luminance step, from the DICOM GSDF, is about 0.65 % above ~100 cd/m². It is coarser in the
dark: 2.4 % at 1 cd/m². One PQ code step is:

| PQ bits | Step in GSDF JNDs, 10–3000 cd/m² | Near black (0.5–3 cd/m²) | Reading |
|---|---|---|---|
| 8 | 4.5–5.7 | 3.4 | visible banding |
| 10 | 1.1–1.4 | 0.82 | at threshold |
| 12 | 0.27–0.35 | 0.20 | below threshold |

(Computed by `udeep.jnd`. The GSDF target is not the most sensitive pattern, so these are lower bounds.)

- 7.1.1 A 10-bit (or coarser) HDR delivery MUST be dithered wherever it contains smooth gradients.
- 7.1.2 Where smooth gradients matter, a delivery SHOULD use ≥ 12-bit PQ, or 16-bit PNG with `cICP`. Viewers
  may reduce the precision of some codecs (Annex B, B6).

### 7.2 Data precision (uncertainty)

For an encoding with code step ΔE, the data step is Δx_q(x) = ΔE / |dE/dx|, where E(x) is the code value of
the mapped light. Rounding adds error with standard deviation Δx_q / √12.

- 7.2.1 **Data criterion.** An encoding carries the data faithfully at *x* when

    Δx_q(x) / √12 ≤ K · σ(x),  with K = 0.1

  (the encoding adds at most 1 % to the data's variance). K MAY be set differently if stated.
  For a declared `tolerance` (no noise), the criterion is that the largest rounding error, Δx_q(x) / 2, does not
  exceed the tolerance, which is an absolute tolerance in data units (5.2). *Added 2026-10-07.* A noise model whose
  σ reaches zero inside the declared range (`relative` or `poisson` at a value of zero) cannot meet the criterion
  there with any finite encoding; the range should start above zero, or the file is a rendering.

  *Added 2026-10-08.* For a stored file the criterion is applied to each stored level: the run of data values
  that share one code, with Δx_q its width. The uncertainty is taken at the point of the level where it is
  smallest, so that a level meets the criterion only if every value in it does. For `poisson` and `relative` that
  is the point of the level nearest zero, or zero itself if the level contains it; `constant` noise and a
  `tolerance` are the same throughout a level.
- 7.2.2 **Integer data.** For integer-valued data, a *lossless* encoding has Δx_q ≤ 1 unit over the declared range.
- 7.2.3 A file MUST record the largest data step over the declared range (`MaxDataStep`, §8).

### 7.3 Data records

- 7.3.1 A figure file MAY be described as a **data record** only if its encoding meets 7.2.1, or 7.2.2 for integer
  data, over the whole declared range, and its uncertainty model is not `unknown`.
- 7.3.2 Otherwise the file is a **rendering**. It MUST say so in its metadata, and data needing more precision
  MUST be carried separately: a float master, or data channels such as those of JPEG XL. Data precision is
  never implied by the display encoding.
- 7.3.3 *Guidance.* Worked examples are in Annex C:
  - noisy data (CT, photon counts) needs about 8–13 bits below the knee and 11–16 above it;
  - data with a tolerance tighter than about 1 % needs more bits than any display encoding has;
  - an 8-bit gain-map JPEG is always a rendering.

## 8. Declared metadata

- 8.1 Every stored figure MUST carry the following as XMP in the namespace
  `https://ultradeeprange.github.io/udeep/ns/0.3/` (prefix `udeep`; **provisional URI**, see §10):

  | Property | Type | Meaning |
  |---|---|---|
  | `udeep:Version` | Text | profile version, `"0.3"` |
  | `udeep:ImageState` | Text | `data-referred` |
  | `udeep:DataUnit` | Text | unit of the data values |
  | `udeep:NoiseModel` | Text | `constant`, `poisson`, `relative`, `tolerance` or `unknown` (§5.2) |
  | `udeep:NoiseParameter` | Real | the model's parameter (§5.2), greater than zero; required unless the model is `unknown`, and absent then |
  | `udeep:Stretch` | Text | `linear`, `log`, `asinh` or `proportional` |
  | `udeep:RangeLow`, `udeep:RangeHigh` | Real | the declared range, in data units **after `StretchOffset` has been subtracted** (8.3) |
  | `udeep:StretchWidth`, `udeep:StretchOffset` | Real | the asinh width; the value subtracted from the data before the stretch |
  | `udeep:Knee` | Real | data value mapped to diffuse white: **the data value itself**, before `StretchOffset` is subtracted (8.3) |
  | `udeep:KneeRule` | Text | `physical`, `statistical`, `preset` or `percentile`, then a description |
  | `udeep:AboveKnee` | Text | form of the segment above the knee, with its parameters: `hermite; exit=`*s* (§6.7) |
  | `udeep:ReferenceHeadroom` | Real | headroom the figure was authored for (linear ratio), greater than 1 |
  | `udeep:Masked` | Integer | number of pixels in the validity mask |
  | `udeep:DerivedMask` | Text | required when a mask was derived from neighbourhoods (§6.2a): the rule's name, for example `isolated-3x3`. *Added 2026-10-08.* |
  | `udeep:DerivedMasked` | Integer | the number of pixels that rule holds at white, beyond the validity mask. *Added 2026-10-08.* |
  | `udeep:MaxDataStep` | Real | largest Δx_q over the declared range, in data units |
  | `udeep:Record` | Text | `data-record` or `rendering` (§7.3) |
  | `udeep:Preset` | Text | optional: `highlight`, `extend` or another named preset (informative) |
  | `udeep:RoundingToNoise` | Real | optional: the worst value over the declared range of the rounding error divided by the stated uncertainty (§7.2.1); omitted when the noise model is `unknown`. Writers store 10⁹ where the ratio is larger than that or has no bound. *Added 2026-10-07.* |
  | `udeep:DataDigest` | Text | optional: the digest of the array that was mapped, as defined in 8.6. It ties the file to its data: a reader holding the data can confirm it is the same. Where the figure shows processed data (whitened, binned, calibrated), the digest is of the processed array. *Added 2026-10-08.* |
  | `udeep:DataSource` | Text | optional: where the data is kept: `doi:…` where the data has one, otherwise a URL, otherwise a description; several are separated by `; `. *Added 2026-10-08.* |
  | `udeep:DataUnitCode` | Text | optional: the unit in machine-readable form, as a UCUM code (for example `K`, `s-1`). `DataUnit` remains the text for people. *Added 2026-10-08.* |
  | `udeep:SdrDigest` | Text | optional: the digest of the fair SDR rendering's 8-bit RGB pixels, before any lossy coding, as defined in 8.6. It ties an HDR file to the figure of record (§6.8). *Added 2026-10-08.* |
  | `udeep:ColourMap` | Text | the map from light to colour (§6.10): `grey`, or a registered map as `name/version` (for example `fire/2`). *Added 2026-10-07; versioned 2026-10-08.* |
  | `udeep:ColourMapTable` | Text | required unless the map is `grey` or a registered map at a registered headroom: the table itself, as the node count, `;`, then base64 of the two segments' numbers (little-endian float32, row by row). *Added 2026-10-08.* |

  `NoiseParameter`, `DerivedMask` and `ColourMapTable` are required in the cases their rows state. `Preset`,
  `RoundingToNoise`, `DataDigest`, `DataSource`, `DataUnitCode` and `SdrDigest` are optional. Every other property
  MUST be present, including one whose value is zero or the usual one (`Masked`, `DerivedMasked`, `StretchOffset`,
  `StretchWidth`, `AboveKnee`).

- 8.2 A figure's caption SHOULD be generated from this metadata. The reference caption gives the declared range
  as data values (with `StretchOffset` added back) and writes its numbers to six significant figures: in full
  between a thousandth and a million, where every digit shown is a digit of the number, and with an exponent
  outside that.

- 8.3 **Which numbers have the offset removed.** *Added 2026-10-08.* A reader subtracts `StretchOffset` from a data
  value and places the result in the range `RangeLow` to `RangeHigh`. `Knee` is a data value as measured, so it is
  compared as `Knee` − `StretchOffset`. Example: with `StretchOffset` 10, `RangeLow` 0, `RangeHigh` 90, `Knee` 55,
  a linear stretch and a reference headroom of 2, the figure shows data values from 10 to 100; the data value 10
  is black, 55 is at white (it is 45 after the offset) and 100 is at 2 × white.

- 8.4 **How numbers are written.** *Added 2026-10-08.* An Integer is written in decimal digits. A Real MUST be
  written so that it reads back to the same 64-bit binary floating-point number (IEEE 754 binary64). Infinities
  and NaN cannot be written. The reference form, which the conformance vectors compare character for character,
  is the shortest decimal text that reads back exactly. Take the shortest digits d₁d₂…dₙ that do so and the
  whole number *p* for which the value is 0.d₁d₂…dₙ multiplied by ten to the power *p*:
  - if −4 < *p* ≤ 16, the digits are written in place, with no exponent and with no decimal point after a whole
    number: `4`, `0.1`, `0.0001`, `1700000002.3456`, `1000000000000000`;
  - otherwise the first digit, then a point and the remaining digits if there are any, then `e`, the sign of the
    exponent *p* − 1 (always written) and at least two digits of it: `1e+16`, `1e-05`, `1.5e-07`;
  - a negative number begins with `-`; negative zero is `-0`.

  A writer SHOULD read back what it has written and refuse to store a declaration that does not come back
  unchanged. The reference writer does.

- 8.5 **Declarations that are not valid.** *Added 2026-10-08.* A declaration is not valid, and a reader MUST NOT
  use it, if: `Version` is not `0.3` or `ImageState` is not `data-referred`; the mapping breaks a rule of §6 (the
  range, the knee and its position, the form above the knee, a proportional mapping's end point, a number that is
  not finite); `MaxDataStep` or `NoiseParameter` is not a finite number greater than zero; `RoundingToNoise` is
  negative or not finite; `Masked` or `DerivedMasked` is negative; `DerivedMasked` is above zero and no
  `DerivedMask` is named; or `Record` is `data-record` with an `unknown` noise model. A reader MUST refuse a
  packet that contains a document-type or entity declaration (`<!DOCTYPE`, `<!ENTITY`).

- 8.6 **The digests.** *Defined in full 2026-10-08.* Each digest is SHA-256 (FIPS 180-4) of the sequence of bytes
  given below, written as the text `sha256:` followed by the 64 lower-case hexadecimal digits of the result.
  - **`DataDigest`, the bytes.** First a header in ASCII: `udeep-data-1;`, then the array's dimensions as
    decimal integers joined by `x` (for an image, rows then columns), then `;`. An image of 2 rows and 3 columns
    has the header `udeep-data-1;2x3;`. Then every element as an IEEE 754 binary64 number, least significant
    byte first, in row order: the top row first, each row from left to right (in general, the last index varies
    fastest).
  - **`DataDigest`, the values.** Each element is converted to binary64 whatever type it is stored as, so the same
    numbers held as 16-bit integers or as 32-bit floats give the same digest. Negative zero is written as zero
    (eight zero bytes). Every NaN, whatever its sign and payload, is written as the bytes `00 00 00 00 00 00 F8 7F`.
    Only real numbers that binary64 holds exactly have a digest: floating-point numbers of 64 bits or fewer, and
    integers of magnitude up to 2⁵³. Where the data is held with a mask, the digest is of the stored values, hidden
    ones included; the mask is not part of it.
  - **`DataDigest`, an example.** One row holding 1, −0 and NaN: the header `udeep-data-1;1x3;` and then the 24
    bytes `00 00 00 00 00 00 F0 3F`, `00 00 00 00 00 00 00 00`, `00 00 00 00 00 00 F8 7F` give
    `sha256:449b644ce7f301ef25711531396fe1b992a510c98c6e8430ef6bc094ed94c872`.
  - **Several images in one figure.** Each image has its digest as above. The file's `DataDigest` is then SHA-256
    of the ASCII text made by joining those digests, each complete with its `sha256:`, with `|`, in the order the
    images were added to the figure; it is written in the same way.
  - **`SdrDigest`.** The fair SDR rendering of the whole figure as an 8-bit sRGB image, before any lossy coding.
    First the ASCII header `udeep-rgb8-1;`, the height in pixels, `x`, the width, `;`. Then three bytes for each
    pixel, red, green and blue, each an unsigned 8-bit value, the top row first and each row from left to right.
    Example: one row of two pixels, white then black, has the header `udeep-rgb8-1;1x2;` and the bytes
    `FF FF FF 00 00 00`, and gives `sha256:9c590fe054308e0920383c4b5da555eb22d11afa88da6d9bc20f674bb2b5bfbc`.
- 8.7 **A figure for a light and for a dark page.** *Added 2026-10-10.* A figure MAY be published in more than one
  version that differ only in the furniture and the background it is drawn on.
  The data, the mapping, the colours of the data and every pixel that is above white MUST be the same in each.
  Each version is a file of its own, with its own fair SDR rendering and so its own `SdrDigest`; every other
  property of 8.1, `DataDigest` among them, MUST be the same in each, and that is how a reader can tell that two
  files are versions of one figure. A page MAY choose between them by the viewer's colour scheme; either is the
  figure of record. Furniture is never brighter than SDR white in any version (6.11, 9.2.1), so a dark
  version is not a way to give text or axes more contrast against the data.

## 9. Delivery forms

### 9.1 Raster

| Delivery | Requirement | Notes |
|---|---|---|
| **Compatibility** | MAY be an 8-bit gain-map JPEG per ISO 21496-1 (Ultra HDR layout accepted). Its base image MUST be the fair SDR rendering, and `HDRCapacityMax` = log2(ReferenceHeadroom). | Opens everywhere; SDR viewers show the correct figure. It is always a rendering (§7.3). The gain map SHOULD be full resolution and 3-channel, because a reduced map blurs sharp data into halos. Writers SHOULD include the ISO 21496-1 binary block as well as the XMP. |
| **Precision** | MAY be a 16-bit PNG, PQ, BT.2020, signalled with `cICP` (9, 16, 0, 1). | Lossless and smooth on the tested path. It has no SDR base, so it MUST be accompanied by the fair SDR rendering. |
| **Compact** | MAY be AVIF, PQ or HLG, 10- or 12-bit. An AVIF with an ISO 21496-1 gain map SHOULD be preferred when available, because it carries its own SDR base. | 10-bit MUST be dithered (7.1.1). The gain-map AVIF is untested (Annex B). |
| **Archive** | SHOULD be JPEG XL or 12-bit AVIF, PQ. JPEG XL MAY carry the data itself as extra float channels. | For future displays and decoders. |

### 9.2 Vector (hybrid SVG)

A vector HDR figure is an SVG whose furniture is vector and whose data layer is an embedded HDR raster.

- 9.2.1 Furniture MUST be drawn at or below SDR white. Its colours MUST NOT depend on HDR support.
- 9.2.2 **Standalone `.svg` files.** The data layer MUST be an image with an explicitly signalled HDR transfer
  (AVIF PQ/HLG, or PNG with `cICP`). It MUST NOT rely on a gain-map image. On the tested device, an SVG file
  opened as an image dropped the gain map but kept an AVIF PQ layer (Annex B, B4).
- 9.2.3 **SVG inline in HTML.** The data layer MAY be a gain-map image (B4).
- 9.2.4 **SDR fallback.** Where the data layer has no SDR base (PQ/HLG), the file SHOULD also embed the fair SDR
  rendering. It SHOULD choose between the two with a CSS media query inside the SVG,
  `@media (dynamic-range: high)`. *This mechanism is proposed and not yet tested.*
- 9.2.5 The §8 metadata MUST be carried in the SVG's `<metadata>` element as an RDF/XMP packet.
- 9.2.6 **Vector HDR colour.** Furniture or flags MAY use CSS HDR colours (`color(rec2100-pq …)`,
  `color(rec2100-linear …)`) only after an SDR declaration of the same property, so that viewers without support
  keep the SDR colour. On both tested browsers these colours were not recognised (B5). No vector-only HDR path
  exists today.

### 9.3 Video

- 9.3.1 A time series MAY be delivered as HDR video: HEVC or AV1, 10-bit, PQ or HLG, BT.2020, with the colour
  description signalled in the stream. On the tested device, 10-bit HEVC in PQ and in HLG displayed as HDR (B2).
- 9.3.2 10-bit video MUST be dithered (7.1.1). The mapping MUST be the same for every frame, unless a change is
  declared and shown.
- 9.3.3 The §8 metadata SHOULD be carried as XMP in the container or in a sidecar file. *Carriage inside MP4 is
  proposed and not yet tested.*
- 9.3.4 Video is always a rendering (§7.3). The flashing limits of §14 apply.

### 9.4 Publication

- 9.4.1 In a journal or thesis PDF, the figure of record is the fair SDR rendering (6.8).
- 9.4.2 The caption SHOULD state the knee and its rule. It SHOULD also say that values above it appear brighter in
  the HDR version, with a persistent link or DOI to that version.
- 9.4.3 A PDF MAY embed an HDR image in a future format (for example JPEG XL) alongside the SDR rendering, but
  MUST NOT depend on it.
- 9.4.4 *Status (informative, checked 2026-09-27).* PDF has no portable HDR rendering. The PDF Association's
  application note says HDR data may sit in JPEG, JPEG 2000 or ICC v4.4 data only as "private data", which
  conforming PDF/A and PDF/X viewers must ignore. JPEG XL has been named its preferred HDR image format. Until
  portable HDR rendering is specified, a gain-map JPEG in a PDF shows as its SDR base, which is the fair SDR
  rendering and therefore safe. Source: *FAQ on HDR in PDF: Focus on PDF/A and PDF/X*, Imaging Model TWG,
  July 2025.

## 10. Conformance and open issues

A program can check that a conforming file:
1. carries the §8 metadata, and the metadata is consistent (range, knee, headroom);
2. declares a mapping that is strictly increasing over the declared range (6.3);
3. has an SDR rendering (or gain-map base) that matches the fair SDR rendering within the delivery's
   quantisation;
4. has an HDR rendering at the reference headroom that matches the declared mapping within quantisation, and
   does not exceed the headroom (within the tolerance below);
5. has no masked pixel above SDR white;
6. states `MaxDataStep` correctly, and claims `data-record` only when §7.3.1 holds;
7. where it declares `DataDigest` or `SdrDigest`, matches the data or the SDR figure supplied to the check.

*Tolerance for lossy gain maps.* An 8-bit JPEG gain map cannot be exact, and its error is exponentiated on
display. Writers SHOULD compute the gain against the base image **as decoded**, and SHOULD store the gain map at
the highest quality; without them a colour bar beside a white background overshot by 20–27 %. The check in point 4
allows a lossy gain map 5 % over the headroom.

Those two steps are not enough with a colour map. Where one SDR channel is small its gain is many stops, so the
gain range is wide and one 8-bit gain code is a large step of light: 0.9–2.0 % with the `fire` and `ice` maps on
the test figures below (with `GainMapMin` at 0; up to 2.3 % before), against 0.2–0.8 % with `grey`. The gain map's own JPEG at quality 100 then moves a code
by several codes (up to 4 on one set of test figures). A writer taking only those two steps (the reference writer before 2026-10-10) read back above
1.05 × the headroom on 58 of 190 coloured test figures, at worst 1.093 ×, and 34 of the 108 that carry a
declaration failed point 4; none of 81 `grey` figures read above 1.011 ×. So:

- A writer SHOULD give every pixel it does not lift a gain of exactly 1 before the gain map is coded, so that
  `GainMapMin` is 0. JPEG error in the base then no longer sets the bottom of the gain range, and the map is flat
  wherever the figure is not lifted.
- A writer SHOULD check its own decoded gain map against the headroom before it writes the file: decode it,
  rebuild the HDR rendering a reader would show, and lower the gain wherever that is above the figure's brightest
  value, until nothing is.

The reference writer does both, and refuses to write a file that it cannot bring within its headroom. Read back with its own reader, none of the same 271 figures is then above its
headroom (the largest is 1.000 ×) and none fails the check. The costs, measured on the coloured figures: 8
correcting passes at the median and 39 at most (limit 64); about three times the time to write on these small
figures (3.1 against 1.1 s per megapixel in all) and 1.2 to 3.4 times on four images of 2 and 8 megapixels;
the top 2 % of the range reads 0.4 % low on average and 1.7 % low at worst, and a single lifted pixel up to 7.6 %
low in its brightest channel. Files are a quarter smaller in total. With the gain map below quality 100 the writer
refuses some coloured figures and others fail the check on their pixels: below the highest quality a gain map
is unreliable with a colour map, whatever the writer.

*Other decoders.* All of the above is with one JPEG decoder (Pillow, which is libjpeg-turbo). Another decoder can
read lifted pixels several per cent differently where one SDR channel is small: up to 8.5 % at single pixels
between Pillow and the Rust `jpeg-decoder` crate, while Windows GDI+ matched Pillow exactly. Of the 190 coloured
files that the reference writer holds within the headroom on its own decoder, one read above 1.05 × the headroom
with `jpeg-decoder` (1.080 ×). The other way round, the project's Rust writer checks its files with `jpeg-decoder`:
read with Pillow, none of its 190 was above 1.05 × the headroom (the largest 1.042 ×), and one 8-megapixel image
read 1.048 ×. So a writer's check holds exactly only on the decoder it was made with. No viewer has been tested. From
2026-10-11 the reference package writes and reads with the Rust core's decoder wherever that library is present, so
that its own files are read by the decoder that checked them.

The conformance suite is the reference implementation's golden vectors (`golden.json`, format 2): 90 cases of
14 kinds. They cover the stages, the colour-map tables, mapping records run forwards and backwards, stored levels
and the data criterion under each noise model, the mask rule, the digests, the metadata's serialised form, the
generated caption and the ISO 21496-1 block's bytes. Every case states how closely a result must agree. Four
classes are in use:

| Agreement | Used for | Cases |
|---|---|---|
| 10⁻¹², relative and absolute | float64 arithmetic: the scale, the tone curve, the PQ and sRGB encodings, and mapping records with the `grey` map | 19 |
| 2 × 10⁻⁶, relative and absolute | anything through a float32 colour-map table: the map lookups, and mapping records with a colour map | 7 |
| 10⁻⁹, relative and absolute | stored levels and the data criterion, which are found by sampling the declared range; whether the criterion is met is compared exactly | 32 |
| exact | text, bytes and yes-or-no results: the tables' digests, both digests of §8.6, the metadata text, the caption, the mask rule and the ISO 21496-1 block | 24 |

A case's own statement governs; this table reports what the file holds on 2026-10-10.

Implementations, on 2026-10-12. The Python reference generates the suite and reproduces all 90 cases. The
JavaScript port reproduces 55: every case of the 11 kinds it implements. It does not compute stored levels and
does not write the metadata or the caption. The Rust core implements all 14 kinds; it reproduces all 90 cases.
A Julia binding calls the Rust core and takes the mapping and digest cases from the same file. Decoder checks
against libultrahdr were made on an earlier build and have not been repeated.

The reference validator is `udeep.validate` (`python -m udeep.validate FILE.jpg`). It does not stop at a
file it cannot read: whatever cannot be read or checked is reported as a failed check that says what it was.
From the file alone it checks points 1, 2 and 6 and the headroom limit of point 4, and point 7 against an SDR
figure given with `--sdr`. A file does not carry its data, so points 3, 5 and the rest of 4 need the figure or
the data that made it (`udeep.mpl.validate(fig, path)`); from the file alone they are reported as skipped.

- Point 1 fails when a property that §8 requires is absent, when a carried `ColourMapTable` cannot be decoded,
  when a registered map is declared at a headroom it is not registered for and no table is carried, or when the
  gain map's own headroom differs from `ReferenceHeadroom`. All such problems are reported together.
- Where the colour map is one the reader does not know and the file carries no table, the reader cannot tell
  whether anything is missing. It says so under point 1, and point 6 is skipped, because the stored levels cannot
  be recomputed without the colours.
- Point 2 treats a slope below one thousandth of the mapping's average slope as flat.
- For point 3, "within the delivery's quantisation" is tested by putting the fair SDR rendering through the
  file's own JPEG quantisation tables and comparing decoded pixels.
- Point 6 fails an 8-bit gain-map JPEG that claims to be a data record (7.3.3), whatever else it declares.

Open issues:
1. The namespace URI is a placeholder. It needs an address under the editor's control.
2. The reference implementation's curve conforms to 6.3 from UDeep 0.3 (the reference form of 6.7). Other strictly
   increasing forms, such as linear or logarithmic segments, have not been compared with it.
3. The reference implementation writes the §8 metadata into gain-map JPEGs (and, on request, PQ PNGs) from
   UDeep 0.3. Its `MaxDataStep` covers the 8-bit quantisation of its render path and not JPEG loss. It does not
   yet write an ICC profile. It writes the ISO 21496-1 binary block beside the Ultra HDR XMP; the layout was
   taken from libultrahdr's source and has not yet been read back by an independent decoder. Its flags byte says
   that the gain is applied in the base image's colour space (bit 6), as the XMP beside it means; files written
   before 2026-10-10 have that bit clear. It evaluates the data criterion (7.2.1) when it
   saves, records the result (`RoundingToNoise`) and warns when the file is too coarse. On the synthetic CT of the
   reference site (noise 6 HU, range −160 to 1850 HU, 4× headroom) an 8-bit gain-map JPEG has levels up to
   19.5 HU wide: rounding is 0.94 of the noise at its worst, and over the limit across 92 % of the range.
4. Gain-map AVIF (9.1), the SVG media-query fallback (9.2.4) and metadata inside MP4 (9.3.3) are untested.
5. Precision of colour maps and of gain-map JPEGs has not been analysed (Annex C).
6. Device evidence comes from one phone and one monitor (Annex B). More devices are needed.
7. The noise models of 5.2 have no law for spectral power (exponential, or χ² in general), so the reference
   gallery's gravitational-wave figures declare `unknown`. One declaration per file also means a figure with two
   differently mapped images cannot be stored as one file.
8. *Resolved 2026-10-08 (§6.10).* A colour map is a table: registered maps are published as tables, and any
   other map travels inside the file. Still open: where the registered tables are published for others to fetch
   (today they ship with the reference implementation, `udeep/tables/`), and a check that an embedded table has
   strictly increasing lightness.
10. **The registered maps and 6.9: closed 2026-10-10 by a second version of each map.** Version 1 (`fire/1`,
    `ice/1`) did not fully meet 6.9 (found 2026-10-08, `udeep.vision`). Lightness (Jz) was checked node by node,
    for normal vision and for simulated protanopia, deuteranopia and tritanopia (Machado et al. 2009, full
    severity):
    - `fire/1` rose throughout, except at one node under tritanopia at 2× headroom.
    - `ice/1` dipped for protanopia and deuteranopia near 15 % of white, where it ran along the edge of the gamut;
      at 2× headroom it also fell at the last node before the top, for normal vision too.
    - Every fall was under 0.05 % of the map's lightness range.
    - The top of every table was not white: (1, 0.99909, 0.99870) × headroom. The generator let colour fade
      towards a point that is not quite neutral, and the result was cut off at the headroom. For the same reason
      BT.709 luminance fell at one node just below the top in three tables.

    A table is the definition of its map (6.10), so the correction is a new version beside the old one:
    - `fire/2` and `ice/2` measure chroma from the grey of the same lightness, so the top of every table is exactly
      (1, 1, 1) × headroom; `ice/2` also asks for less chroma near 15 to 45 % of the range, which keeps it off the
      edge of the gamut.
    - In version 2 lightness rises at every node of every registered table, for normal vision and for each of
      the three simulated deficiencies, and so does BT.709 luminance. The reference tests hold this.
    - Perceptual length kept under a deficiency: 83–94 % for `fire/2`, 80–98 % for `ice/2` (74–97 % for `ice/1`).
    - `fire/2` differs from `fire/1` by at most 0.2 % of white in any channel. `ice/2` differs from `ice/1` by up
      to 15 % of white in one channel, in the dark blues, and by under 0.3 % of the headroom above white.
    - *Added 2026-10-12.* `fire/3` and `ice/3` are version 2 with every table value below 1e-7 of white set to
      zero: about 3,000 values in each table, all below 4e-8 of white, in channels held at the edge of the gamut.
      Those values were the remainder of the arithmetic, and depended on the processor the tables were made on
      (6.10). No colour moves by as much as 1e-7 of white, so everything said of version 2 above holds for
      version 3. Its tables were made again bit for bit on two systems, with and without the processor-specific
      routines of the numerical libraries.
    - Versions 1 and 2 stay registered and unchanged, so every file that names one can still be read; new figures
      use version 3.

    6.9 now says what "strictly increasing" is measured on and for whom. Still open: no tolerance is stated for
    an embedded table, and nothing checks one.
9. Points 3 to 5 of the conformance list cannot be checked from the file alone. From UDeep 0.3 the file
   declares digests of its data and of its fair SDR rendering (§8), so a reader who holds either can confirm the
   link (conformance point 7). That proves which data and which figure, not that the pixels follow the mapping:
   for that the data must be re-rendered, and the file does not record where the data sits inside the figure.
   Carrying the data itself (for example as JPEG XL extra channels) is still open. The reference check with the
   figure (`udeep.mpl.validate`) builds the pixels it expects by the same steps as the reference writer (6.11),
   so it is not an independent test of those steps.

## 11. Viewer requirements

- 11.1 Viewers MUST apply the gain or mapping as declared, fitted to the display's headroom.
- 11.2 Viewers MUST NOT apply content-adaptive (dynamic) tone mapping to scientific figures, or SDR-to-HDR
  expansion (inverse tone mapping, for example "Auto HDR").
- 11.3 Text, axes and colour-bar labels SHOULD be held at SDR white (for example CSS
  `dynamic-range-limit: standard`).
- 11.4 An HDR figure SHOULD be shown with a colour bar in data units that marks the knee value. Brightness above
  white shows *where* values are high; the colour bar says *how much*.
- 11.5 Viewers MUST honour a user's luminance limit (§14).

## 12. Layer 4: display rendering (informative)

Rendering for a particular screen happens at view time and never changes the file.

- **Headroom.** A display with less headroom than the figure's reference headroom has to compress the range above
  white. The ISO 21496-1 weighting does this for gain maps. A roll-off of the BT.2390 form is appropriate *here*:
  it avoids a visible clip, at the cost of flattening the top of the range on that screen.
- **Consequence.** Values that a screen flattens cannot be told apart on that screen. The viewer SHOULD indicate
  when the display's headroom is below the reference headroom.
- **Variation.** Headroom changes with the device, the brightness setting and the room light. The same file
  therefore looks different on different screens; the declared mapping does not change.
- **Dither** 10-bit output where gradients are smooth.

## 13. Layer 5: presets (informative)

Presets are named choices within the rules of §6. They are conveniences, not requirements.

| Preset | Knee | Typically above white | Use |
|---|---|---|---|
| **Highlight** | a high, meaningful threshold | ~0.1–1 % of the figure | show where sparse extremes are |
| **Extend** | the top of the main range (for example a window preset's upper edge) | a region with its own structure | show a second range beside the first |

## 14. Comfort and accessibility

- 14.1 Content above SDR white MUST NOT flash or blink. Time-varying HDR figures (video, animation, interactive
  controls) MUST stay within the general flash thresholds of WCAG 2.1 SC 2.3.1 and ITU-R BT.1702: no more than
  three flashes per second over a quarter or more of the screen.
- 14.2 Switching between HDR and SDR renderings MUST be started by the user and MUST NOT cycle automatically.
- 14.3 Pages and applications that show HDR figures SHOULD offer a visible control to limit or turn off HDR. The
  SDR rendering is complete, so nothing is lost.
- 14.4 The area above SDR white SHOULD stay small: under about 10 % of the figure.
- 14.5 *Basis.* Published guidelines limit flashing, not steady brightness; no quantitative limit on static
  brightness was found. The CSS Color HDR draft states that user agents should let users limit maximum luminance.

## 15. Evidence of benefit (informative)

Claims that an HDR rendering improves a figure SHOULD be supported by:
- (a) a perceptual metric with display modelling, gated by null and positive controls (for example
  ColorVideoVDP);
- (b) for publication, a task-based study against the best SDR alternatives, both clipped and compressed.

Preference judgements alone are insufficient: preference and scientific usefulness correlate weakly
(R² ≈ 0.31, Park & Montag 2007).

## Annex A — Device verification protocol (normative for claims about devices)

A statement that a device, application or format "shows HDR" or "shows N bits" MUST be supported as follows.

- A.1 **Reference in the image.** Test patterns MUST contain an SDR-white (1×) reference next to the above-white
  content, for example 2×, 4× and 8× squares inside a 1× white band. A real figure alone is not a valid test
  (see B3).
- A.2 **Controls.** Every test MUST include an SDR control, which must not glow. Every test MUST also include a
  positive control, a stimulus that must show the effect: a format already known to glow, or 8- and 9-bit ramps
  in a banding test. If a control fails, the test is void.
- A.3 **Blinding.** Precision (banding) tests MUST present stimuli in shuffled order under neutral labels. The
  key is held by the experimenter.
- A.4 **Record the conditions.** Record:
  - device and model;
  - OS, browser and application versions;
  - brightness setting and whether automatic brightness was on;
  - ambient light;
  - viewing path. Remote-desktop sessions MUST NOT be used; they deliver 8-bit SDR.
- A.5 **Verify the stimulus.** Before showing a file, decode it and confirm that it holds the intended values.

## Annex B — Device evidence (informative)

Observed by the editor, by eye, 2026-09-26/27, on an iPhone 16 Pro in iOS Photos and in Safari (a claude.ai
artifact page). The OS and browser versions were not recorded (A.4 was not yet in force).

| # | Test | Result |
|---|---|---|
| B1 | Gain-map JPEG (reference writer) in iOS Photos | shows as HDR |
| B2 | Referenced pattern in HEVC PQ and HLG video, AVIF PQ and HLG, 16-bit PNG PQ, and gain-map JPEG, on a web page | all show HDR; SDR control plain |
| B3 | Same page, CT figures at 2× without an in-image reference | judged "not HDR", which was invalid (A.1) |
| B4 | Referenced pattern inside SVG | gain map kept in inline SVG, both as a `data:` URI and as a linked file; **gain map dropped in a standalone `.svg` loaded as an image**; AVIF PQ kept in both |
| B5 | Vector fills at 4× white | `srgb-linear` clamped to SDR white; `rec2100-linear` and `rec2100-pq` not recognised. The same held on a desktop browser (Edge 153, Windows 11). |
| B6 | PQ ramps, 150–260 and 500–900 cd/m² | 10- and 12-bit AVIF banded, with bands narrower than 8-bit and 12-bit the same as 10-bit. Blind 16-bit PNG ramps rounded to 10–16 bits were all smooth, but that run had no 8/9-bit positive control (A.2), so it found no floor. A repeat with controls is pending. |
| B7 | 12-bit HEVC and AV1 video playback | test built; result pending |

No iPhone has a panel bit depth that is officially stated or independently measured. Apple states "true 10-bit
colour" only for the Pro Display XDR.

## Annex C — Data precision: worked examples (informative)

Bits required by the data criterion (7.2.1, K = 0.1), median / maximum, for the reference form of 6.7. **The noise
values are illustrative inputs, not measurements.** Tool: `tools/precision_budget.py`.

| Case | Below the knee | Shoulder | Last 3 % of headroom |
|---|---|---|---|
| CT, σ = 5 HU, headroom 2 | 10.2 / 11.1 | 14.6 / 15.5 | 14.3 / 14.4 |
| CT, σ = 5 HU, headroom 4 | 10.2 / 11.1 | 13.0 / 13.5 | 13.5 / 13.5 |
| CT, σ = 20 HU, headroom 4 | 8.2 / 9.1 | 11.0 / 11.5 | 11.5 / 11.5 |
| Photon counts (Poisson), 1–10⁵, headroom 4 | 9.1 / 12.6 | 12.8 / 15.4 | 15.5 / 15.7 |
| Float, relative tolerance 10⁻², 6 decades | 13.3 / 14.2 | 13.6 / 14.5 | 14.5 / 14.6 |
| Float, relative tolerance 10⁻⁴ | 19.9 / 20.9 | 20.3 / 21.1 | 21.2 / 21.2 |

The two float rows take the uncertainty as a fraction of the value (σ = fraction × value, K = 0.1). In a file
that is the `relative` noise model of 5.2 with that fraction; the `tolerance` model is absolute and cannot express it.

CT: window −160 to 1850 HU, knee 240 HU. For integer HU, a lossless encoding needs 11.9 bits below the knee and
14.3 (headroom 4) to 16.3 (headroom 2) above it.

**Why 6.3 exists.** The same cases with a flat-ended roll-off (exit slope 0, as in v0.2 and the BT.2390 EETF):

| Case | Shoulder | Last 3 % of headroom | Share of the declared range in that last 3 % |
|---|---|---|---|
| CT, σ = 5 HU, headroom 2 | 13.3 / 15.2 | 17.2 / ∞ | 31 % (39 % of the range above the knee); reference form: 5.5 % |
| CT, σ = 5 HU, headroom 4 | 12.6 / 14.5 | 15.4 / 30 | 13 % (16 %); reference form: 3.2 % |
| Photon counts, headroom 4 | 12.5 / 15.9 | 17.0 / 30.5 | 5.0 %; reference form: 1.6 % |

With a flat end the bits required diverge, and part of the range can be neither seen nor recovered. The reference
form needs about one bit more at the middle of the shoulder, because it spreads the range evenly.

Not yet analysed: colour maps, gain-map JPEGs, and measured noise from real datasets.

## Annex D — Changes from v0.2

- **Restructured data-first:** five layers; layers 1–3 normative, display rendering and presets informative.
- **Mapping must be strictly increasing and invertible** over the declared range (6.3, 6.4). The flat-ended
  roll-off moved to display rendering (§12).
- **Two precision budgets** (§7): visibility, and a new data criterion with a data-record/rendering distinction.
- **Data layer** (§5): units, a noise model and a validity mask are required.
- **Metadata** (§8): added the image state, the noise model, the declared range, the above-knee form,
  `MaxDataStep` and `Record`; `StretchLow`/`StretchHigh` renamed `RangeLow`/`RangeHigh`.
- **ISO 22028-5 terms:** nominal diffuse white; a data-referred image state.
- **Proportional mapping** for data that is light (6.6).
- **Video** added as a delivery form (9.3).
- **Comfort and accessibility** (§14).
- **Highlight and Extend** became informative presets (§13).

## Annex E — Changes since the first v0.3 draft (2026-10-06)

Each line is one normative addition or change, with the date it was made.

- 2026-10-07 · **Tolerance and zero uncertainty** (7.2.1): the criterion for a declared tolerance; a noise model
  that reaches zero inside the range cannot be met there.
- 2026-10-07 · **`RoundingToNoise`** (§8): a file may record the worst ratio of rounding error to uncertainty.
- 2026-10-07 · **`ColourMap`** (§8): the file names its map from light to colour; from 2026-10-08 as `name/version`.
- 2026-10-08 · **A colour map is a table** (6.10): registered maps are published tables for headrooms 2, 4, 8
  and 16; any other map or headroom is carried in the file (`ColourMapTable`).
- 2026-10-08 · **Masks are the only neighbourhood operation** (6.2a): a rule that looks at neighbours runs before
  the mapping, and the file declares it (`DerivedMask`, `DerivedMasked`).
- 2026-10-08 · **Digests** (§8, conformance point 7): `DataDigest` and `SdrDigest` tie a file to its data and to
  its SDR figure of record; 8.6 defines both byte for byte, including a figure with several images.
- 2026-10-08 · **`DataSource` and `DataUnitCode`** (§8): where the data is kept, and the unit as a UCUM code.
- 2026-10-08 · **The isolated-pixel rule ignores masked pixels** (6.2a): a pixel in the validity mask no longer
  counts towards a 3 × 3 block.
- 2026-10-08 · **A proportional mapping ends at knee × headroom** (6.6), with the reference form above the knee,
  so that light is value ÷ knee over the whole range.
- 2026-10-08 · **Where the knee may sit** (6.5): not within a millionth of the range from either end; exactly at
  the top is allowed.
- 2026-10-08 · **Finite numbers, and the exit slope** (6.4, 6.7): every declared number is finite; an exit slope is
  a number from 0 to 3.
- 2026-10-08 · **The headroom is fixed when the data is mapped** (6.7): a figure cannot be stored under another
  reference headroom.
- 2026-10-08 · **A pixel is lifted only where the data layer alone determines it** (6.11): anything drawn over
  the data, and the data under it, keeps its SDR colour.
- 2026-10-08 · **Uncertainty is taken where it is smallest within a stored level** (7.2.1).
- 2026-10-08 · **The tolerance model is absolute** (5.2, 7.2.1): "or relative" is removed.
- 2026-10-08 · **Which numbers have the offset removed** (8.3): `RangeLow` and `RangeHigh` do, `Knee` does not.
- 2026-10-08 · **How numbers are written** (8.4): the shortest text that reads back to the same number.
- 2026-10-08 · **Required properties, and declarations that are not valid** (8.1, 8.5): which properties must be
  present, and which values a reader refuses.
- 2026-10-08 · **The caption's numbers** (8.2): the reference caption writes six significant figures.
- 2026-10-11 · **Which code runs** (§10): the reference package uses its Rust core wherever the library is present,
  for the mapping and for writing and reading gain-map JPEGs; the NumPy and Pillow code remains as the second
  implementation. The golden vectors and their tolerances are unchanged.
- 2026-10-10 · **Second versions of the registered maps** (§6.9, §6.10, §10 point 10): `fire/2` and `ice/2` end
  exactly at white and rise in lightness at every node for normal vision and three simulated deficiencies; 6.9
  says how that is measured. Version 1 stays registered. The golden vectors grow from 74 to 82 cases.
- 2026-10-10 · **A figure for a light and for a dark page** (§8.7, new): versions of a figure that differ only in
  what surrounds the data share every declared property but `SdrDigest`.
- 2026-10-10 · **ISO 21496-1 flags** (§10, point 3): the reference writers set the flag that the gain is applied in the
  base image's colour space, which the XMP in the same file already meant. The two forms of the metadata disagreed
  before; no pixel changes.
- 2026-10-10 · **Unlifted pixels, and a gain map checked before it is written** (§10): a writer SHOULD give pixels
  it does not lift a gain of exactly 1 (`GainMapMin` 0), and SHOULD check its own decoded gain map against the
  headroom before writing. The statement that the reference writer stayed within ~5 % of the headroom is
  withdrawn: with a colour map it did not (58 of 190 test figures, at worst 1.093 ×). A note records that another
  JPEG decoder reads lifted pixels several per cent differently.
- 2026-10-11 · **A registered map is taken from its table, never recomputed** (6.10): the generator's
  results depend on the machine it runs on, in the values of a version-2 table that are almost zero.
- 2026-10-12 · **Third versions of the registered maps** (6.10, §10 point 10): `fire/3` and `ice/3` are version 2
  with every table value below 1e-7 of white set to zero, so that the tables can be made again bit for bit on any
  machine. Versions 1 and 2 stay registered. The golden vectors grow from 82 to 90 cases.
- 2026-10-11 · **Renamed** (§8, §10): the profile is the UDeep Figure Profile. The metadata prefix is `udeep` in place of
  `scihdr`, with a new provisional namespace, and the digests are taken over `udeep-data-1` and `udeep-rgb8-1` in
  place of `hdrplot-data-1` and `hdrplot-rgb8-1`, so every digest changes. No file was published under the old names.

## References

- Miller, Nezamabadi & Daly (2013), *Perceptual signal coding for more efficient usage of bit codes*, SMPTE
  Motion Imaging Journal 122(4), doi:10.5594/j18290.
- Boitard, Mantiuk & Pouli (2015), *Evaluation of color encodings for high dynamic range pixels*, Proc. SPIE
  9394, doi:10.1117/12.2077715.
- Kunkel & Reinhard (2010), *A reassessment of the simultaneous dynamic range of the human visual system*,
  APGV 2010, doi:10.1145/1836248.1836251.
- Barten (1999), *Contrast Sensitivity of the Human Eye and Its Effects on Image Quality*; DICOM PS3.14.
- Park & Montag (2007), *Evaluating tone mapping algorithms for rendering non-pictorial (scientific)
  high-dynamic-range images*, J. Visual Communication and Image Representation 18(5), 415–428,
  doi:10.1016/j.jvcir.2007.06.008.
- Mantiuk et al. (2024), *ColorVideoVDP*, ACM TOG 43(4), doi:10.1145/3658144.
- Jordan & Vanderheiden (2024), *International guidelines for photosensitive epilepsy: gap analysis and
  recommendations*, ACM Transactions on Accessible Computing 17, doi:10.1145/3694790.
- PDF Association, Imaging Model TWG (2025), *FAQ on HDR in PDF: Focus on PDF/A and PDF/X*.
- Nature Portfolio, Image integrity and standards.
