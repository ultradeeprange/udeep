# Changelog

What changed in the tools and the repository, newest first. Changes to what a figure file *means* are changes to
the profile, and are listed with their dates in the profile itself: Annex D (from v0.2 to v0.3) and Annex E (since
the first v0.3 draft) of [`profile/udeep-figure-profile-v0.3.md`](profile/udeep-figure-profile-v0.3.md).

Nothing has been released yet: there is no tagged version and no package on PyPI, crates.io or the Julia registry.
The version in the source is `0.3.0.dev0`, after the profile draft it implements.

## Unreleased

### 2026-10-14

- **One command remakes every generated file** (`tools/build_all.py`), and GitHub checks on every push that none
  is stale. **A security policy** (`SECURITY.md`) with private reporting. The Rust core's dependencies are checked
  on every push for published advisories, licences and sources (`cargo deny`, `core/deny.toml`), and the actions
  the workflows use are pinned to exact commits.
- **Read by programs we did not write.** Google's reference library for gain-map JPEGs (libultrahdr v2.0.2,
  commit 66821e0) decoded all six gallery and example files tried, took their ISO 21496-1 metadata (which agrees
  with the XMP), and gave the same picture as udeep's reader: on the lifted pixels the two differ by a median of
  0.03 to 0.4 % and a 99th percentile of 0.5 to 3.6 %, with no factor or gamut between them; the cause of that
  residual was not traced. An ordinary decoder (Pillow) opens each file as the SDR figure, within 3 codes of
  udeep's. FFmpeg 9.0.2 reads the 16-bit PNG as 48-bit RGB, BT.2020 primaries, SMPTE 2084, full range, from its
  cICP chunk. pngcheck 3.0.3 calls cICP an unknown chunk: it predates the chunk.
- **Fuzzed.** The four Rust readers ran under cargo-fuzz for six minutes each (over 110 million inputs to the PNG
  reader and its size check, 44,500 to the full JPEG reader), with no panic, crash or hang. The metadata parser
  refuses hostile XML (entity expansion, external entities) before parsing it. What was found, and fixed:
  - a JPEG of 3 KB whose header claims 65,535 x 65,535 pixels made the JPEG decoder ask for 4 GiB before reading
    any data, and a failed allocation ends the program. The readers now refuse a JPEG or PNG that claims more
    pixels than its data could hold, before anything makes room for them, and the size functions do the same so
    that a caller is never handed such a size;
  - the reference JPEG reader let Pillow's own errors out (OSError and others) where it promises ValueError;
  - the reference PNG reader let zlib's error out for a cut-short file;
  - `metadata.parse` raised TypeError for a declaration missing a property; it raises ValueError now.
  Tests that damage valid files thousands of times now run with every build (`core/tests/malformed.rs`,
  `tests/test_malformed.py`). One thing left: the JPEG decoder library leaks a few hundred bytes in its worker
  threads at exit; it is not ours and does not grow.
- **Every public Python name has a docstring**, and a test holds that.

- **The 16-bit PQ PNG is in the Rust core** (feature `png`): a writer, a reader that takes any of PNG's row
  filters, and the stored levels of such a file (`levels_pq16`), with their C functions. The core is now built
  with `--features jpeg,png`. The two writers store the same codes; the compressed bytes are each writer's own.
- **Python:** `udeep.read_png_pq`, `udeep.metadata.data_steps_pq16`, and `write_png_pq` through the core when it
  is in use. The package no longer leaves the 16-bit levels to the caller.
- **The validator checks a 16-bit PQ PNG**, and above all its claim to be a data record: the levels are worked
  out again from the declaration and set against the stated uncertainty, and with the data supplied the values
  are read back out of the file and compared. A PNG used to be reported as failing because it was not a JPEG.
- **Julia:** `png_pq`, `read_png_pq`, `levels_pq16` and `xmp_packet`, and a fourth worked example,
  `detector_record.jl`, which gives the same numbers as the Python one. The Python reference reads the
  Julia-written file's declaration and confirms it.
- **The Julia package can find the core as an installed artifact**, which is what will make it installable without
  a checkout or Rust. The wheels workflow packs the core alone for each platform (`tools/core_tarball.py`), and
  `tools/julia_artifacts.jl` writes the `Artifacts.toml` from such archives. Tried with the archives the workflow
  built: the package's tests pass on them. No `Artifacts.toml` is in the repository yet, since it must point at a
  release and there is none.
- One thing this showed and did not settle: a PQ file does not say what luminance its white is written at. The
  tools write and read at 203 cd/m2 (BT.2408), and the validator says that it assumes so.

### 2026-10-13

- **Worked examples** (`examples/`): a statistical knee on a spectral line scan, a preset knee with saturated pixels
  held by a mask, a logarithmic stretch on a simulated field, and one on depth: a 12-bit detector frame stored as
  an 8-bit gain-map JPEG (a rendering) and as a 16-bit PQ PNG (a data record), with the counts read back out of the
  PNG exactly. All the data is synthetic. A test runs each one.
- **A Python reference page** (`docs/reference/`), written from the docstrings by `tools/build_reference_page.py`.
- **Julia brought level in two places:** the reference page lists the 28 names `UDeepCore` exports, with their
  docstrings, and `julia/UDeepCore/examples/` has Makie counterparts of the three range examples, run by the
  package's tests.
- **The site says what the name means** on the gallery, apps, standardisation and evidence pages, not only on the
  home page.
- **The package builds and carries the Rust core.** Installing builds the core where `cargo` is present and puts it
  inside the package; without `cargo` the package installs as before and uses NumPy and Pillow
  (`UDEEP_BUILD_CORE` = `auto`, `1` or `0`). A wheel that carries the core is tagged `py3-none-<platform>`: one
  wheel serves every Python 3. A `wheels` workflow builds them for Linux, Windows and macOS (Intel and Apple
  silicon) with the source distribution, installs each into a clean environment and checks it there. On a version
  tag it attaches them to a draft release; nothing goes to PyPI. `LICENSE-THIRD-PARTY.txt` lists what is compiled
  into the core.

### 2026-10-12

- **Version 3 of the `fire` and `ice` maps**, now the default. It is version 2 with every table value below 1e-7 of
  white set to zero, which removes the values that depended on the processor the tables were made on. The
  generator gives the version-3 tables bit for bit under every setting tried, and the tests require it. No colour
  moves by as much as 1e-7 of white. Versions 1 and 2 stay registered; a figure can name one (`cmap="fire/2"`).
  The golden vectors grow from 82 to 90 cases, the gallery is rebuilt, and the live view carries version 3.

### 2026-10-11: first public version

- **Published** at <https://github.com/ultradeeprange/udeep>, with the site at
  <https://ultradeeprange.github.io/udeep/>.
- **Named UDeep**, short for Ultra Deep Range. Earlier drafts were called `hdrplot`, and the profile the Scientific
  HDR Figure Profile. The metadata prefix is `udeep:`, and the digest schemes are `udeep-data-1` and `udeep-rgb8-1`.
- **Profile v0.3** (draft): data-first, in layers (the data, the mapping, precision, display rendering, presets),
  with declared metadata, digests that link a figure to its data and to its SDR figure of record, and a validator.
- **Python reference** (`udeep`): the mapping, colour maps as published tables, gain-map JPEG and PQ PNG writers, the
  metadata writer and reader, the validator, a matplotlib interface with light and dark themes.
- **Rust core** (`core/`): the mapping, the metadata and the gain-map JPEG writer and reader behind a C interface.
  Python uses it by default when it is built, and falls back to NumPy and Pillow when it is not.
- **Julia package** (`julia/UDeepCore`) over the core, with a Makie extension.
- **JavaScript port** and a WGSL shader of the mapping; the site's live view draws data through that shader.
- **Golden vectors**: 82 cases of 14 kinds, generated by the Python reference and reproduced by Rust (82) and
  JavaScript (47); 90 and 55 since version 3 of the maps.
- **Tests run on GitHub** for Python (Linux and Windows), the Rust core and the Julia package.
- **Fixed, found by the first run on Linux:** a small call into the Rust core took about twenty times as long there
  as the plain call, because the available cores were counted on every call; they are now counted once, and not at
  all for a small call.
- **Changed, found by the same run:** the colour-table generator does not give the shipped version-2 tables bit
  for bit on another machine. The differences are confined to table values that are almost zero (below 4e-8 of
  white; at most 7e-13 of white apart), and come from processor-specific routines in NumPy and OpenBLAS, not from
  the operating system. The shipped tables are the definition (profile 6.10, which now says an implementation
  must not recompute them); the test compares to the table tolerance, and `tools/make_tables.py` never rewrites a
  table that exists. Details and measurements: `project/performance-scope.md`, section 4g.
- **Device evidence is kept in the repository**: an issue form for reports, one record per report in
  `evidence/devices/`, and a table made from the records.
