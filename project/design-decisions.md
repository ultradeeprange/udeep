# Design decisions

Status: decided 2026-10-08, at Mathew's direction ("make design choices now that will help the future").
These are choices of shape, made before the Rust core, the GPU path and the Julia package exist, so that all
three fit without rework. Each is short: the decision, why, and what it costs. Section 4 lists where today's code
does not yet follow them. Nothing here is implemented by this document.

A decision can be reopened, but only by writing down what changed.

## 1. The shape of the thing

**D1. Three pointwise stages, with fixed boundaries.**

    value x  →  position t in [0, 1]  →  light y, in multiples of diffuse white  →  linear RGB, same unit

Scale, tone curve, colour map. Each stage is a pure function of one pixel's value and the mapping parameters.
*Why:* this is what a shader, a parallel loop and a formula in a standard all need. *Cost:* none; it is the
current structure, now a rule.

**D2. Nothing in the mapping looks at a neighbour.**
Any operation that needs a neighbourhood (the Highlight preset's removal of isolated pixels, a saturation
bloom mask, a region of interest) runs *before* the mapping and produces a **mask**. The mapping takes the mask
as a second input and holds masked pixels at or below white.
*Why:* keeps the mapping global (profile 6.2), invertible, tileable and one line of shader. *Cost:* the
Highlight filter moves out of `hdr_view` into a mask step, and the file must say that a mask was derived that
way.

**D3. The declaration is the parameter block.**
One flat record holds the mapping: stretch kind, range low and high, stretch width and offset, knee, reference
headroom, form above the knee, colour-map identifier. The same record, with the same field names, is:

- the file's metadata (`udeep:` properties),
- the argument of every core function,
- the C struct of the native library,
- the uniform block of the shader,
- a JSON object for sidecars and tests.

*Why:* a figure is then reproducible from its own metadata by construction, and no implementation can take a
parameter the file does not record. *Cost:* today's `stretch` dictionary and loose arguments are replaced by
the record; fields are numbers and small enumerations only (no strings in the hot path).

**D4. Buffers in, buffers out.**
Core functions take a pointer and a length, not an image: values (and a mask) in, light or RGB out. Row-major,
top row first, contiguous.
*Why:* any slice of an image is a valid input, so rows can be split across cores, large images tiled, and video
streamed, with no change to the core. *Cost:* none.

**D5. Drawing is not the core's job.**
Axes, text, layout and the composition of a figure from several renders belong to each front end (matplotlib,
Makie). The core never
rasterises anything.
*Why:* it is the part that cannot be shared, and it keeps the core small enough to port. *Cost:* each front end
writes its own composition step.

**D6. Encoding is separate from mapping.**
Gain map, PQ, JPEG, PNG, AVIF and the metadata serialisers are a second layer that consumes the core's output.
Image codecs are dependencies behind an interface, never inside the core.
*Why:* a viewer needs the mapping and no encoder; a converter needs the reverse. *Cost:* none.

## 2. Numbers

**D7. Light is always in multiples of diffuse white.**
Absolute luminance appears in exactly one place: the PQ encode, which takes the white level as a parameter.
*Why:* profile 6.1; and it keeps every table and vector display-independent.

**D8. The reference is float64; float32 is allowed with a written tolerance.**
The Python reference computes the mapping in float64. Other implementations, the GPU above all, may use
float32. Each golden vector carries its tolerance. Starting values: 2 × 10⁻⁶ relative in light, and no more
than 0.5 of a code in any 8-bit or 10-bit output. *Why:* a tolerance that is written down can be tested; one
that is discovered later becomes an argument. *Cost:* `tone` returns float64 (it returns float32 today).
*Amended 2026-10-09:* colour on a GPU is held to 1 × 10⁻⁵ (relative and absolute); position and light stay at
2 × 10⁻⁶. Colour is judged by what reaches the screen, and a steep stretch of a colour-map table multiplies the
light's error, so that at 2 × 10⁻⁶ the shipped map fire/1 could not be drawn at headroom 2 for an excess of 0.003
of an 8-bit code; 1 × 10⁻⁵ is still about fifty times finer than half a 10-bit code.

**D9. A colour map is a table, and the table is its definition.**
- Two segments, light 0 to 1 and 1 to the reference headroom, because the map has a corner at white.
- 4,097 nodes per segment, evenly spaced in light. Linear interpolation between nodes, in linear light.
- Values are linear RGB with BT.709 primaries, in multiples of white, stored as float32.
- The search in Jzazbz that makes today's maps becomes the *generator*: a tool that writes tables, not part of
  any runtime.

Measured: at 4,097 nodes the worst departure from the exact search is 0.22 of an 8-bit code, and the lookup is
about 50 times faster (`performance-scope.md`).
*Why:* speed; one texture on a GPU; identical colours in every language without porting the search; and a map
that can be published or embedded, which closes profile open issue 8. *Cost:* colours shift by a fraction of a
code, so golden vectors and gallery files are regenerated once.

**D10. Reference headrooms for registered maps are 2, 4, 8 and 16.**
A map's colours above white depend on the headroom it was built for, so a table belongs to a (map, headroom)
pair. Registered maps ship tables for those four. A file with any other reference headroom, or any other map,
**embeds its table**.
*Why:* a finite set can be shipped, tested and loaded as textures; arbitrary values would need the generator at
run time. *Cost:* authors pick from four headrooms unless they embed a table. Displays with other headrooms are
unaffected: a gain map already scales to the display.

**D11. Identifiers carry versions.**
A colour map is named `name/version` (for example `fire/1`). The profile version is in the namespace. Golden
vectors carry the version they were made by. A table has a digest.
*Why:* a file made today must mean the same thing in ten years.

**D12. The mapping is deterministic.**
No random numbers. Dither, when a 10-bit delivery needs it, is a separate declared step with a fixed pattern,
so two runs give the same file.

**D13. Digests are defined on values, not on files.**
Already so (`digest_data`, `digest_pixels`): little-endian float64 or 8-bit RGB, preceded by a short text header
that carries the shape. Any language can reproduce them.
*Amended 2026-10-08:* the profile now gives the bytes in full (section 8.6): the header text, the treatment of
negative zero and NaN, and the digest of a figure with several images. `digest_pixels` takes only unsigned 8-bit
arrays, and `digest_data` only real numbers that float64 holds exactly.

## 3. Proof and process

**D14. The golden vectors are the contract.**
One language-neutral JSON file. Every implementation runs it: Python, JavaScript, and later Rust, Julia and the
shader (rendered and read back). It grows to cover each layer: stages, tables, digests, metadata serialisation,
and the bytes of the ISO 21496-1 block.
*Why:* "independent implementations passing the same tests" is what a standards body asks for, and it is the
only way to keep four languages in step.

**D15. The CPU reference path defines; the GPU path renders.**
A figure of record is produced by the reference path. GPU output is checked against it within D8's tolerance.

**D16. Python is the reference implementation until profile 1.0.**
The Rust core becomes the shared library when it passes every vector; the reference role moves only by an
explicit decision.

*Decided 2026-10-11 (Mathew): everything runs on the Rust core.* The core passes every vector, so from this date
it is what the Python package uses wherever the library is found: the mapping, the colours, writing a gain-map
JPEG and reading one back. The NumPy and Pillow code stays, as the second implementation that keeps the vectors
honest and as what runs where the library is absent; `udeep.core.enable(False)` chooses it. What has not moved:
the golden vectors are still generated by the NumPy code, so the contract's numbers are its numbers, and the core
is held to them at each case's stated tolerance. The two therefore agree to about 1e-12 in the mapping and not
bit for bit, and an installation with the core and one without can give a figure SDR digests that differ.

**D17. The native interface is plain C.**
Fixed-layout structs of 32-bit numbers, error codes returned, nothing thrown across the boundary, no global
state, safe to call from several threads. The parameter block (D3) is laid out so that it can be copied
straight into a shader's uniform buffer.
*Amended 2026-10-08:* the record holds doubles, because the reference is float64 (D8). It is seven doubles and
two 32-bit integers, 64 bytes. A shader takes a float32 copy of it.

**D18. Licences stay permissive.**
BSD-3 code, CC BY text, no copyleft dependency in the core. Tools that need GPL software (the EHT pipeline)
download it at build time and are not part of the library.

## 3a. Six further choices (added 2026-10-08)

Chosen because each changes what a file says or how the core is shaped, which is expensive to change later.

**D19. A table states its own primaries.**
A colour-map table records its colour primaries and transfer as ITU-T H.273 code points, the same numbers the PNG
`cICP` chunk uses. Today's tables are 1 (BT.709) and 8 (linear).
*Why:* HDR screens show more than sRGB's gamut; wide-gamut maps can then arrive without a format change.

**D20. Units have a code beside the text.**
`udeep:DataUnit` stays free text for people. `udeep:DataUnitCode` is optional and machine-readable, in UCUM
(for example `K`, `s-1`, `[hnsf'U]` for the Hounsfield unit).
*Why:* free text cannot be parsed, and files written without the code cannot gain it later.

**D21. The data source is a persistent identifier where one exists.**
`udeep:DataSource` is `doi:…` when the data has a DOI, otherwise a URL, otherwise a description. Several
sources are separated by `; `.

**D22. The Python core is written to the Array API standard.**
Scale, tone curve, table lookup and the `Mapping` methods use only functions in the standard, and are tested on
`array_api_strict`, which refuses anything outside it. The same code then runs on NumPy, CuPy or PyTorch arrays.
*Why:* the mapping is per-element arithmetic, so this is the Python GPU path with no second code base.
*Cost:* slightly more careful code; plain NumPy idioms that are not in the standard are not used in the core.

**D23. The reference shader is WGSL.**
One shader for the browser (WebGPU) and for native code (Rust's `wgpu`). GLSL or others are generated from it.
*Why:* no vendor lock-in; the same file is tested in both places.

**D24. Metadata readers refuse document-type declarations.**
A packet containing `<!DOCTYPE` or `<!ENTITY` is rejected before parsing.
*Why:* the validator reads files from anyone. The Python used here already resists entity expansion and
external entities (tested 2026-10-08), but the rule must hold for every implementation.

## 3b. Rules tightened by the review of 2026-10-08

Each closes a case where the code used to do something other than what the file said. The profile lists them in
its Annex E.

**D25. A record that cannot be drawn as declared is refused, not adjusted.**
`Mapping` refuses a knee within a millionth of the range from either end (exactly at the top is allowed), any
number that is not finite, an exit slope outside 0 to 3, and a proportional mapping that does not end at
knee × headroom with exit slope 1. `Declaration` refuses a wrong version or image state, a step or an
uncertainty that is not positive, negative counts, and held pixels with no rule named.
*Why:* a silent adjustment makes the file state one mapping and show another. *Cost:* callers that relied on the
adjustment now get a `ValueError`.

**D26. Numbers in the metadata are the shortest text that reads back exactly.**
`udeep.metadata.float_text`; the writer reads back what it wrote and refuses on any difference (profile 8.4).
*Why:* ten significant digits could not carry a narrow range on a large pedestal. *Cost:* every port writes
floats by this rule, not by its language's default.

**D27. The headroom is fixed when the data is mapped.**
`savefig_hdr`, `declaration` and `caption` refuse a headroom other than the one given to `imshow`, and refuse a
figure whose images were made at different headrooms (profile 6.7).

**D28. A pixel is lifted only where the data image alone determines it.**
The matplotlib front end renders the figure four times: as it is, with the images scaled, with them black and
with them white. A pixel is lifted only if it is exactly black in the third and exactly white in the fourth;
every other pixel is the SDR figure (profile 6.11). *Cost:* two more renders for each save; highlights under a
translucent overlay show at SDR level.

**D29. The validator reports; it does not raise.**
A file that cannot be read, or a check that fails with an error, becomes a failed check that says what it was.
A required property that is absent fails point 1.
*Not yet everywhere:* `udeep.mpl.validate` reads the metadata first and still raises on a declaration that is
not valid.

**D30. Uncertainty is taken where it is smallest within a stored level.**
For Poisson and relative noise that is the point of the level nearest zero, so a range that reaches zero cannot
meet the data criterion (profile 7.2.1). The tolerance model is absolute, in data units.

**D31. The isolated-pixel rule does not count masked pixels.**
A pixel in the validity mask is not a neighbour for the 3 × 3 rule (profile 6.2a).

## 4. Where today's code does not follow these yet

| Decision | Today | Change needed |
|---|---|---|
| D2 | ~~`hdr_view` removes isolated pixels inside the Highlight mapping~~ | **done 2026-10-08**: `udeep.masks`; the file declares the rule and its count (`DerivedMask`, `DerivedMasked`). Pixels are unchanged |
| D3 | ~~a `stretch` dictionary plus loose arguments~~ | **done 2026-10-08**: `udeep.mapping.Mapping`; the declaration yields it (`Declaration.mapping`), and precision, validation and rendering all take it. The `stretch` dictionary survives only as an authoring convenience at `imshow` and `hdr_view` |
| D8 | ~~`tone` and `scale` return float32~~ | **done 2026-10-08**: float64 throughout; one implementation of the curve |
| D9 | ~~colour maps are searched per pixel~~ | **done 2026-10-08**: tables in `src/udeep/tables/`; `cmap.search` is the generator; the JavaScript port interpolates the same files |
| D10 | ~~any headroom accepted~~ | **done 2026-10-08**: tables shipped for 2, 4, 8, 16; any other headroom gets a 257-node table carried in the file |
| D11 | ~~`udeep:ColourMap` is a bare name~~ | **done 2026-10-08**: `fire/1`, and `udeep:ColourMapTable` |
| D14 | ~~vectors cover scale, tone, colour and the PQ encode only~~ | **done 2026-10-08**: 74 cases in 14 kinds (42 before the review of the same day), each with its own tolerance or marked exact: stages, tables, mapping records (forwards and backwards), stored levels and the data criterion under each noise model, the mask rule, both digests, the metadata's serialised form, the caption, and the ISO 21496-1 block's bytes. Python reproduces all 74. The JavaScript port reproduces 39; it does not implement the levels, the metadata writer or the caption. Rust: all 14 kinds are implemented; it reproduced every case of the suite as it stood before 32 cases were added on 8 October (42 of 42) and is not yet updated for those 32 |

D1, D4 to D7, D12, D13, D15 to D18 already hold.

Of the six added in section 3a: D19 (in the tables' index), D20, D22 and D24 are done; D21 is followed by the
gallery only in part (its sources are URLs and descriptions, and no DOI was verified for each file); D23 is done:
`src/udeep/shaders/mapping.wgsl`, run on a GPU through `udeep.gpu` and held to the golden vectors.

**Also new with D3:** the mapping can be run backwards. `Mapping.value(light)` returns the data value, tested
against `Mapping.light` for every stretch.

## 5. Order that follows

1. **D9, D10, D11:** the colour-map tables. Largest gain in speed, unblocks the shader, settles how maps are named.
2. **D3, D8:** the parameter record, float64 reference. Small, mechanical, and the Rust core starts from it.
3. **D2:** the mask step.
4. **D14:** widen the golden vectors to match.
5. Then the Rust core, the shader and the Julia binding are each a port against a fixed contract.
