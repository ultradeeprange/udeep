# Scientific HDR Figure Profile — draft v0.2

> **Superseded by [v0.3](scientific-hdr-figure-profile-v0.3.md) (2026-10-06).** Kept as the record.

Status: **working draft**, 2026-09-27. Not endorsed by any standards body. Editor: Mathew Newman.
Reference implementation: `hdrplot` (Python), with a JavaScript port and
golden test vectors. Supersedes v0.1 (2026-09-26); changes are listed in Annex C.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as in RFC 2119.

## 1. Scope

This profile specifies how a scientific figure is stored, rendered and delivered when it shows values
as brightness or colour: images, heatmaps, spectrograms and 2-D histograms. The aims are that the
figure

1. uses the extended range and precision of high-dynamic-range (HDR) displays,
2. remains a complete, correct figure on standard-dynamic-range (SDR) displays and in print,
3. states, inside the file, how data became brightness, so the figure is reproducible and its
   processing is disclosed, and
4. keeps its value as displays improve, up to the limits of human vision.

Figures come in three **forms**: raster (§6), vector (§7) and publication (§8). Line, scatter and
bar plots carry their value by position; there, HDR MAY be used only as a flag (e.g. a threshold
crossed), never to carry the value.

## 2. Normative references

- ITU-R BT.2100 — HDR image parameter values (PQ, HLG, BT.2020 primaries).
- SMPTE ST 2084 — the Perceptual Quantizer (PQ) transfer function.
- ITU-R BT.2408 — HDR operational practice; reference (diffuse) white 203 cd/m².
- ITU-R BT.2390 — HDR report; the EETF roll-off for fitting content to a display.
- ISO 21496-1 — gain-map metadata for image conversion (dynamic-range conversion).
- ISO 22028-1 — extended colour encodings: architecture and image states (scene-, output-referred).
- ISO/TS 22028-5:2023 — HDR/WCG colour encodings for still images (BT.2100 PQ/HLG; nominal diffuse white;
  scene- and display-referred metadata). A full International Standard revision is in progress. This
  profile's "SDR white" corresponds to its nominal diffuse white; v0.3 will adopt its terms and add a
  data-referred image state.
- ITU-T H.273 — coding-independent code points (CICP) for colour signalling.
- DICOM PS3.14 — Grayscale Standard Display Function (GSDF; Barten model).
- ISO 16684-1 (XMP) — metadata packets.
- W3C SVG 2; W3C CSS Color 4 and the CSS Color HDR draft; PNG (third edition, `cICP` chunk).

## 3. Terms

- **SDR white**: the display's reference white for ordinary content; value 1.0 in this profile.
- **Headroom**: the ratio of a display's peak to its SDR white.
- **Knee**: the data value rendered at SDR white; values above it may be rendered brighter than white.
- **Fair SDR rendering**: the figure rendered with values above the knee clipped at SDR white.
- **Master**: the stored data and declared mapping from which all renderings are produced.
- **Data layer**: the part of a figure that carries values as brightness (the image, the colour bar).
- **Furniture**: axes, ticks, labels, legends and annotations.

## 4. Principles (informative)

1. The SDR rendering is a complete figure; HDR adds only what lies above white.
2. The mapping is global, monotonic and declared; it is chosen for scientific meaning, not appearance.
3. The master contains no display's limits; limits are applied only when rendering for a display.
4. Claims that HDR improves a figure need evidence; so do claims that a device shows HDR (Annex A).

## 5. Requirements for every form

### 5.1 Units and colour

- 5.1.1 Rendered values MUST be linear light in multiples of SDR white. Absolute luminance MUST NOT
  appear in the declared mapping. A display's SDR white (default 203 cd/m², BT.2408) converts to it.
- 5.1.2 Rendering references and HDR deliveries SHOULD use BT.2020 primaries.

### 5.2 The SDR rendering

- 5.2.1 Every figure MUST have a fair SDR rendering. Below the knee it is identical to the HDR
  rendering; above it, values are clipped at SDR white.
- 5.2.2 All conclusions drawn from the figure MUST be supported by the SDR rendering alone. In
  publication, the SDR rendering is the figure of record.

### 5.3 The mapping

- 5.3.1 The mapping from data to rendered value MUST be a single global, monotonic function of the
  data value (after a declared mask). A figure of record MUST NOT use local or content-adaptive
  operators, such as local tone mapping, zoom-adaptive stretches or CFAR-style local thresholds.
  Exploratory views that are labelled as such MAY use them.
- 5.3.2 The knee MUST be given in data units together with its rule, which is one of:
  - a physical threshold (e.g. GOES flare class M, 1e-5 W m⁻²);
  - a statistical threshold (e.g. a stated false-alarm probability);
  - a domain preset (e.g. a CT window);
  - a percentile. A percentile knee MUST be labelled exploratory.
- 5.3.3 Above the knee, the curve SHOULD be continuous in slope (C1) and roll off at the headroom, in
  the form of the BT.2390 EETF (Hermite spline). It MUST NOT exceed the declared headroom.
- 5.3.4 Pixels flagged as saturated, clipped, bloomed or otherwise invalid MUST NOT be rendered above
  white. They SHOULD be marked (e.g. with a HiLo-style indicator).

### 5.4 Declared mapping metadata

- 5.4.1 Every stored figure MUST carry the mapping as XMP in the namespace
  `http://ns.hdrplot.org/scihdr/0.1/` (prefix `scihdr`; **provisional URI**, see §10), with at least:

  | Property | Type | Meaning |
  |---|---|---|
  | `scihdr:Version` | Text | profile version, `"0.2"` |
  | `scihdr:DataUnit` | Text | unit of the data values |
  | `scihdr:Stretch` | Text | `linear`, `log` or `asinh` |
  | `scihdr:StretchLow`, `scihdr:StretchHigh` | Real | data values mapped to 0 and 1 |
  | `scihdr:StretchWidth`, `scihdr:StretchOffset` | Real | asinh width; value subtracted first |
  | `scihdr:Knee` | Real | data value rendered at SDR white |
  | `scihdr:KneeRule` | Text | `physical`, `statistical`, `preset` or `percentile`, then a description |
  | `scihdr:Mode` | Text | `highlight` or `extend` (informative) |
  | `scihdr:ReferenceHeadroom` | Real | headroom the figure was authored for (linear ratio) |
  | `scihdr:Masked` | Integer | number of pixels excluded from lifting |

- 5.4.2 A figure's caption SHOULD be generated from this metadata.

### 5.5 Precision

The smallest visible luminance step, from the DICOM GSDF, is about 0.65 % of luminance above
~100 cd/m². It is coarser in the dark: 2.4 % at 1 cd/m². One PQ code-value step is:

| PQ bits | Steps per GSDF JND, 10–3000 cd/m² | Reading |
|---|---|---|
| 8 | 4.5–5.7 | visible banding |
| 10 | 1.1–1.4 | at threshold |
| 12 | 0.27–0.35 | below threshold |

Computed by `hdrplot.jnd`, which is tested against the standard's endpoints. The GSDF target is not
the most sensitive pattern, so these are lower bounds on visibility.

- 5.5.1 The master MUST keep the data at its original precision (float where the data is float).
- 5.5.2 A rendering reference SHOULD be IEEE half-float or better. Its worst-case spacing (0.098 %) is
  ≥ 6.7× finer than the GSDF JND.
- 5.5.3 A 10-bit (or coarser) HDR delivery MUST be dithered wherever it contains smooth gradients.
- 5.5.4 Where smooth gradients matter, a delivery SHOULD use ≥ 12-bit PQ, or 16-bit PNG with `cICP`.
  Viewers may reduce the precision of some codecs. In one tested case (Annex B, B6), 10- and 12-bit
  AVIF ramps banded where the same values in 16-bit PNG did not.

## 6. Raster form

| Delivery | Requirement | Notes |
|---|---|---|
| **Compatibility** | MAY be an 8-bit gain-map JPEG per ISO 21496-1 (Ultra HDR layout accepted). Its base image MUST be the fair SDR rendering, and `HDRCapacityMax` = log2(ReferenceHeadroom). | Opens everywhere; SDR viewers show the correct figure. The gain map SHOULD be full resolution and 3-channel, because a reduced map blurs sharp data into halos. Writers SHOULD include the ISO 21496-1 binary block as well as the XMP. |
| **Precision** | MAY be a 16-bit PNG, PQ, BT.2020, signalled with `cICP` (9, 16, 0, 1). | Lossless and smooth on the tested path. It has no SDR base, so it MUST be accompanied by the fair SDR rendering. |
| **Compact** | MAY be AVIF, PQ or HLG, 10- or 12-bit. An AVIF with an ISO 21496-1 gain map SHOULD be preferred when available, because it carries its own SDR base. | 10-bit MUST be dithered (5.5.3). The gain-map AVIF is untested (Annex B). |
| **Archive** | SHOULD be JPEG XL or 12-bit AVIF, PQ. JPEG XL MAY carry the data itself as extra float channels. | For future displays and decoders. |

## 7. Vector form (hybrid SVG)

A vector HDR figure is an SVG whose furniture is vector and whose data layer is an embedded HDR raster.

- 7.1 Furniture MUST be drawn at or below SDR white. Its colours MUST NOT depend on HDR support.
- 7.2 **Standalone `.svg` files.** The data layer MUST be an image with an explicitly signalled HDR
  transfer (AVIF PQ/HLG, or PNG with `cICP`). It MUST NOT rely on a gain-map image. On the tested
  device, an SVG file opened as an image dropped the gain map but kept an AVIF PQ layer (Annex B, B4).
- 7.3 **SVG inline in HTML.** The data layer MAY be a gain-map image. On the tested device, it kept
  its gain map inline (B4).
- 7.4 **SDR fallback.** Where the data layer has no SDR base (PQ/HLG), the file SHOULD also embed the
  fair SDR rendering. It SHOULD choose between the two with a CSS media query inside the SVG,
  `@media (dynamic-range: high)`. *This mechanism is proposed and not yet tested.*
- 7.5 The §5.4 metadata MUST be carried in the SVG's `<metadata>` element as an RDF/XMP packet.
- 7.6 **Vector HDR colour.** Furniture or flags MAY use CSS HDR colours (`color(rec2100-pq …)`,
  `color(rec2100-linear …)`) only after an SDR declaration of the same property, so that viewers
  without support keep the SDR colour. On both tested browsers these colours were not recognised, and
  `srgb-linear` values above 1 were clamped (B5). No vector-only HDR path exists today.

## 8. Publication form

- 8.1 In a journal or thesis PDF, the figure of record is the fair SDR rendering (5.2.2).
- 8.2 The caption SHOULD state the knee and its rule. It SHOULD also say that values above it appear
  brighter in the HDR version, with a persistent link or DOI to that version.
- 8.3 A PDF MAY embed an HDR image in a future format (e.g. JPEG XL) alongside the SDR rendering, but
  MUST NOT depend on it.
- 8.4 *Status (informative, checked 2026-09-27).* PDF has no portable HDR rendering. The PDF
  Association's application note says HDR data may sit in JPEG, JPEG 2000 or ICC v4.4 data only as
  "private data". PDF/A and PDF/X conforming viewers must ignore it, and any HDR display is
  "implementation-dependent and not portable". JPEG XL "needs to be converted" to an existing PDF
  image format. Source: *FAQ on HDR in PDF: Focus on PDF/A and PDF/X*, Imaging Model TWG, July 2025.
  The Association has named JPEG XL its preferred HDR image format; its TWG has worked on HDR since
  2023. Until portable HDR rendering is specified, a gain-map JPEG in a PDF shows as its SDR base in
  conforming viewers, which is the fair SDR rendering and therefore safe.

## 9. Viewer behaviour

- 9.1 Viewers MUST apply the gain or mapping as declared, fitting it to the display's headroom (ISO
  21496-1 weighting or a BT.2390-form roll-off).
- 9.2 Viewers MUST NOT apply content-adaptive (dynamic) tone mapping to scientific figures, or
  SDR-to-HDR expansion (inverse tone mapping, e.g. "Auto HDR").
- 9.3 Text, axes and colour-bar labels SHOULD be held at SDR white (e.g. CSS `dynamic-range-limit:
  standard`).
- 9.4 An HDR figure SHOULD be shown with a colour bar in data units that marks the knee value.

## 10. Conformance and open issues

A conforming file:
- carries the §5.4 metadata;
- has an SDR rendering (or gain-map base) that matches the fair SDR rendering within the delivery's
  quantisation;
- has an HDR rendering at the reference headroom that matches the declared mapping within
  quantisation;
- has no masked pixel above SDR white.

*Tolerance for lossy gain maps.* An 8-bit JPEG gain map cannot be exact, and its error is
exponentiated on display. Writers SHOULD therefore:
- compute the gain against the base image **as decoded**, not before compression, because the base's
  own JPEG error would otherwise be multiplied by the gain;
- store the gain map at the highest quality.

With both, the reference writer stays within ~5 % of the headroom at the sharpest gain edges; in the
reference test, fewer than 1 % of pixels exceed it by more than 1 %. Without them, a colour bar beside a
white background overshot by 20–27 %.

The initial conformance suite is the reference implementation's golden vectors (`golden.json`) and
decoder checks against libultrahdr. A conformance validator (`hdrplot.validate`) is planned.

Open issues:
1. The namespace URI is a placeholder. It needs a domain under the editor's control.
2. The reference gain-map writer does not yet emit the ISO 21496-1 binary block or an ICC profile.
3. Gain-map AVIF (6, compact row) and the SVG media-query fallback (7.4) are untested.
4. Device evidence so far comes from one phone and one monitor (Annex B). More devices are needed.

## 11. Evidence of benefit (informative)

Claims that an HDR rendering improves a figure SHOULD be supported by:
- (a) a perceptual metric with display modelling, gated by null and positive controls. For example,
  ColorVideoVDP with checks for identity, noise monotonicity, contrast threshold, peak clipping and
  black floor.
- (b) for publication, a task-based study, such as detection thresholds in dark, mid and bright
  regions (Park & Montag).

Preference judgements alone are insufficient: preference and scientific usefulness correlate weakly
(R² ≈ 0.31, Park & Montag).

## Annex A — Device verification protocol (normative for claims about devices)

A statement that a device, application or format "shows HDR" or "shows N bits" MUST be supported as
follows.

- A.1 **Reference in the image.** Test patterns MUST contain an SDR-white (1×) reference next to the
  above-white content. For example, 2×, 4× and 8× squares inside a 1× white band. A real figure alone
  is not a valid test. On the tested device, CT figures with 2× bone over a small area were judged
  "not HDR", although the same page showed HDR clearly for a referenced pattern (B2).
- A.2 **Controls.** Every test MUST include an SDR control, which must not glow. Every test MUST also
  include a positive control, a stimulus that must show the effect: a format already known to glow,
  or 8- and 9-bit ramps in a banding test. If a control fails, the test is void.
- A.3 **Blinding.** Precision (banding) tests MUST present stimuli in shuffled order under neutral
  labels. The key is held by the experimenter.
- A.4 **Record the conditions.** Record:
  - device and model;
  - OS, browser and application versions;
  - brightness setting and whether automatic brightness was on;
  - ambient light;
  - viewing path. Remote-desktop sessions MUST NOT be used; they deliver 8-bit SDR.
- A.5 **Verify the stimulus.** Before showing a file, decode it and confirm that it holds the
  intended values.

## Annex B — Device evidence (informative)

Observed by the editor, by eye, 2026-09-26/27, on an iPhone 16 Pro in iOS Photos and in Safari (a
claude.ai artifact page). The OS and browser versions were not recorded (A.4 was not yet in force).

| # | Test | Result |
|---|---|---|
| B1 | Gain-map JPEG (reference writer) in iOS Photos | shows as HDR |
| B2 | Referenced pattern in HEVC PQ and HLG video, AVIF PQ and HLG, 16-bit PNG PQ, and gain-map JPEG, on a web page | all show HDR; SDR control plain |
| B3 | Same page, CT figures at 2× without an in-image reference | judged "not HDR", which was invalid (A.1) |
| B4 | Referenced pattern inside SVG | gain map kept in inline SVG, both as a `data:` URI and as a linked file; **gain map dropped in a standalone `.svg` loaded as an image**; AVIF PQ kept in both |
| B5 | Vector fills at 4× white | `srgb-linear` clamped to SDR white; `rec2100-linear` and `rec2100-pq` not recognised. The same held on a desktop browser (Edge 153, Windows 11). |
| B6 | PQ ramps, 150–260 and 500–900 cd/m² | 10- and 12-bit AVIF banded, with bands narrower than 8-bit and 12-bit the same as 10-bit. Blind 16-bit PNG ramps rounded to 10–16 bits were all smooth, but that run had no 8/9-bit positive control (A.2), so it found no floor. A repeat with controls is pending. |

No iPhone has a panel bit depth that is officially stated or independently measured. Apple states
"true 10-bit colour" only for the Pro Display XDR. Sources: Apple, iPhone 16 Pro technical
specifications (support.apple.com/en-us/121031); Apple, Pro Display XDR (2019 product page); AnandTech,
iPhone XS review (2018; an unsourced "10-bit panel" statement); DSCC via MacRumors (2020; a pre-launch
report on the iPhone 12 Pro).

## Annex C — Changes from v0.1

- Added the three forms (raster, vector, publication) and §7, the vector form.
- §5.5: the 10-bit step is 1.1–1.4 GSDF JND, not "~4 JNDs". v0.1 quoted the 10-to-12-bit ratio as a
  JND count.
- Added the 16-bit PNG `cICP` delivery and a warning that viewers may reduce codec precision.
- Added Annex A (device verification) and Annex B (evidence).

## References

- Miller, Nezamabadi & Daly (2013), *Perceptual signal coding for more efficient usage of bit codes*,
  SMPTE Motion Imaging Journal 122(4).
- Boitard, Mantiuk & Pouli (2015), *Evaluation of color encodings for high dynamic range pixels*, Proc.
  SPIE 9394 (HVEI XX).
- Kunkel & Reinhard (2010), *A reassessment of the simultaneous dynamic range of the human visual
  system*, APGV 2010, doi:10.1145/1836248.1836251.
- Barten (1999), *Contrast Sensitivity of the Human Eye and Its Effects on Image Quality*; DICOM PS3.14.
- Park & Montag (2007), *Evaluating tone mapping algorithms for rendering non-pictorial (scientific)
  high-dynamic-range images*, J. Visual Communication and Image Representation 18(5), 415–428,
  doi:10.1016/j.jvcir.2007.06.008.
- Mantiuk et al. (2024), *ColorVideoVDP*, ACM TOG 43(4), doi:10.1145/3658144.
- Ward Larson, Rushmeier & Piatko (1997), *A visibility matching tone reproduction operator for HDR
  scenes*, IEEE TVCG.
- Mantiuk, Daly & Kerofsky (2008), *Display adaptive tone mapping*, ACM SIGGRAPH.
- Nature Portfolio, Image integrity and standards.
