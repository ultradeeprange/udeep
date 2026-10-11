# udeep-core

The mapping from scientific data to light and colour, as the UDeep Figure Profile defines it, in Rust,
with a C interface. It is a port of the Python reference (`../src/udeep`) and is held to the same golden
vectors (`../src/udeep/golden.json`).

    value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit

**Status: the whole contract.** Every golden case is reproduced. Python remains the reference implementation (design decision D16).

| In the core | Not yet |
|---|---|
| scale and its inverse; the tone curve | a PNG (PQ) writer |
| the `Mapping` record: position, light, fair SDR light, and the inverse (light back to data) | the validator |
| colour-map tables: lookup by linear interpolation; their digests | a WebAssembly build |
| PQ and sRGB encoding | a Python wheel (Julia binds through the C interface: `../julia/UDeepCore`) |
| the `isolated-3x3` mask rule | reading metadata back (parsing XMP) |
| the bytes of the ISO 21496-1 gain-map block | |
| the data and pixel digests (its own SHA-256, checked against the FIPS 180-4 examples) | |
| stored levels and the data criterion, for each noise model | |
| a parallel loop on standard-library threads, bit-identical to the serial one | |
| the declared metadata's serialised form and the generated caption, byte for byte | |
| the reference's checks on a mapping record and on a declaration | |
| a C interface over buffers, with error codes | |
| **with the `jpeg` feature:** a gain-map JPEG writer (Ultra HDR layout, ISO 21496-1 block, the declared metadata) | |

By default the library depends on no other crate and reads or writes no image formats. The gain-map JPEG writer
is behind the `jpeg` feature, which brings in two pure-Rust crates, `jpeg-encoder` and `jpeg-decoder` (both
MIT or Apache-2.0). Files it writes are read and validated by the Python reference in the tests: the declaration
comes back field for field, the validator passes, and the pixels agree with the reference writer's to within
the difference between two JPEG encoders. It draws nothing.

## Build and test

    cargo test                      # all 82 golden cases, each to its own stated tolerance
    cargo test --features jpeg      # the same, and the gain-map JPEG writer's own test
    cargo build --release           # target/release/udeep_core.dll (.so, .dylib)
    cargo build --release --features jpeg     # the same, with the gain-map JPEG writer
    cargo clippy --all-targets --features jpeg -- -D warnings

The test fails if any case is skipped, and a second test fails if the golden file no longer holds the kinds and
numbers of cases this port was written against.

## What is refused

The core refuses what the reference refuses, so that a record or a file made here is one the reference reads.

A mapping record (`Mapping::validate`, `udeep_mapping_validate`, and every buffer function):

| Code | Refused |
|---|---|
| 2 `UNKNOWN_STRETCH` | a stretch code that is not one of the four |
| 10 `NOT_FINITE` | a NaN or an infinity in the range, the knee, the headroom, the stretch width or the offset |
| 3 `BAD_RANGE` | `range_high` not above `range_low`; a log stretch starting at or below zero |
| 6 `BAD_STRETCH_PARAMETER` | a stretch width not above zero; a proportional mapping that starts away from zero, has an offset, does not end at `knee * reference_headroom` (to a relative 1e-9), or has an exit slope other than 1 |
| 4 `KNEE_OUTSIDE_RANGE` | a knee outside the range, or at a position within 1e-6 of the bottom, or within 1e-6 of the top without being exactly at it |
| 5 `BAD_HEADROOM` | a headroom below 1; an exit slope outside 0..3 |

A proportional mapping is light = value / knee over the whole range (profile 6.6); the conditions on it are the
ones under which the curve above the knee is that straight line.

A declaration (`Declaration::validate`, which `description` runs first and so returns a `Result`): a version
other than 0.3 or an image state other than `data-referred`; `max_data_step` or `noise_parameter` that is not a
finite number above zero; `rounding_to_noise` that is not finite or is negative; pixels held by a derived mask
that is not named; and everything the reference checked before (unit, noise model, mapping, knee rule, headroom
above 1, record). The core does not parse XMP, so unlike the reference it does not read its own text back.

The gain-map writer stores a pixel the figure does not lift with a gain of exactly 1, and before it returns a
file it decodes its own gain map and lowers it wherever the figure would read above the brightest value given,
until nothing does (`gain_within_peak`; 0.1 to 0.2 s per megapixel on the development machine, none of 271 test
figures over its headroom on this crate's decoder, at worst 1.045 x when read with Pillow). A file it cannot bring within its headroom is refused
(`WriteError::Headroom`, code 9), which happens only below gain quality 100.

The gain-map writer refuses an infinite HDR value (`WriteError::Range`, code 8): its gain has no bound. A NaN in
either figure is written as 0, as the reference writes it, because that is how a view marks a pixel with no data.

## How numbers are written

In the metadata, a number is the shortest text that reads back to the same float64 (`float_text`), in the form
of Python's `repr` without the `.0` of a whole number: `4`, `0.1`, `1700000002.3456`, `1e+16`, `1e-05`. Plain
digits when the decimal exponent is -4 to 15, otherwise an exponent with a sign and at least two digits. In the
caption, six significant figures, written out in full between 0.001 and 10,000,000.

One case needs more than Rust's own formatting. When a number lies exactly half way between the two nearest
texts of the shortest length, both read back; Rust writes the upper and Python the one whose last digit is even
(884938148863529.25 is `884938148863529.3` in Rust, `884938148863529.2` in Python). `float_text` finds that case
from the number's exact digits and takes Python's choice. Checked against the reference on 462,849 doubles
(2026-10-09: random bit patterns, short decimals, powers of ten and their neighbours, and dyadic fractions, several
thousand of them the half-way case), with no difference in the metadata text, in `%.6g`, in `%.10g` or in the
caption's numbers. That check is not in the test suite; the half-way case is.

`RoundingToNoise` takes poisson and relative noise at the point of each stored level nearest zero, where that
noise is smallest, so a range that reaches zero cannot meet the criterion under them; a file states an unbounded
ratio as 1e9 (`UNBOUNDED`). `levels` with fewer than two samples takes two.

## The C interface

Header: `include/udeep_core.h` (written by hand; it must match `src/ffi.rs`). One record, `udeep_mapping`
(64 bytes: seven doubles and two 32-bit integers), and functions over buffers of `n` values. Each returns 0 or
an error code, keeps no state, and may be called from several threads.

    udeep_mapping m = { -160.0, 700.0, 240.0, 4.0, 1.0, 0.0, 1.0, UDEEP_STRETCH_LINEAR, 0 };
    if (udeep_mapping_validate(&m) != UDEEP_OK) { /* refuse it */ }
    udeep_light(&m, hu, light, n);                     /* data -> light, in multiples of SDR white */
    udeep_colours(NULL, 0, 4.0, light, rgb, n);        /* grey; pass a table for a colour map */
    udeep_value(&m, light, hu_again, n);               /* and back */

`udeep_light_parallel` starts no thread when it is asked for one, or when there are fewer than 32768 values:
the loop then runs in the caller's thread. `udeep_digest_data` with `dimensions = 0` digests one value as a
list of one, as the reference digests a scalar; `shape` may then be null.

The Python package uses the library through this interface wherever it is found (`udeep.core`): the mapping and
the colour lookup run here on every processor core, through `udeep_light_parallel`, `udeep_sdr_light_parallel`
and `udeep_colours_parallel`; gain-map JPEGs are written by `udeep_gainmap_jpeg` and read back by
`udeep_gainmap_size` and `udeep_gainmap_read`. Writer and reader use one JPEG decoder, so a file reads exactly
as its writer left it.

What a file declares goes through the same interface, so a host needs no second implementation of the metadata:

    udeep_declaration d = { m, max_step, NAN, NAN, 0, 0, TEXT("K"), TEXT("physical; melting point"),
                              TEXT("unknown"), TEXT("rendering") };  /* the other texts absent: { NULL, 0 } */
    udeep_levels(&m, NULL, 0, 4.0, 65536, 0, 0.0, &d.max_data_step, &ratio);  /* MaxDataStep, RoundingToNoise */
    udeep_description(&d, &xmp, &xmp_length);          /* the rdf:Description element the file carries */
    udeep_caption(&d, &text, &text_length);            /* the caption paragraph (profile 8.2) */
    udeep_bytes_free(xmp, xmp_length);                 /* text comes back in memory the library owns */

A declaration the profile would refuse comes back as `UDEEP_ERROR_BAD_DECLARATION`, or as the mapping record's
own code. For drawing there are `udeep_srgb_encode` / `udeep_srgb_decode`, `udeep_value_at` (position to
data value, for colour-bar ticks) and `udeep_mask_isolated` (the `isolated-3x3` rule of the Highlight preset).

`../tests/test_udeep_rust.py` builds the library and calls every function through this interface from Python
(ctypes), comparing with the golden vectors and, record by record, with what the reference accepts and refuses.
No C compiler has been run against the header itself.

## Measured (2026-10-08)

One megapixel, an asinh stretch, one thread, best of five, on one desktop:

| | Rust, through the C interface | Python reference (NumPy) |
|---|---|---|
| data -> light | 34 ms | 120 ms |
| light -> colour (`fire` table) | 9 ms | 86 ms |

Results agree with the reference to 9 x 10^-16 in light, and exactly in colour.

The parallel loop, data -> light, measured inside Rust (`cargo run --release --example scaling`), best of five,
on a 20-thread desktop:

| Image | 1 thread | 2 | 4 | 8 | 16 |
|---|---|---|---|---|---|
| 4 megapixels | 133 ms | 68 ms | 36 ms | 25 ms | 16 ms |
| 16 megapixels | 540 ms | 271 ms | 143 ms | 90 ms | 65 ms |
| 64 megapixels | 2,139 ms | 1,096 ms | 581 ms | 359 ms | 225 ms |

About 8 to 9.5 times on 16 threads at every size from 4 to 64 megapixels.

**An anomaly, tracked down.** A first measurement, made from Python, showed almost no scaling at 16 megapixels.
It was not the image size and not the loop. The example's second table maps the same 16-megapixel buffers again
and again with the output shifted by a few bytes, in interleaved order: for a stretch of a minute or more every
configuration, the single-threaded one included, ran about 2.7 times slower, whatever the shift, and then
recovered. The slow stretches follow time, not the data. The first measurement happened to take its
16-megapixel row inside one. Their cause is **not identified**: this machine was doing other work throughout, and
processor throttling would look the same. Timings here are therefore the best of several runs, and a single run
can be far off.

## A note on the record

Design decision D17 asked for a record of 32-bit numbers that could be copied straight into a shader's uniform
block. The reference is float64 (D8), so the C record holds doubles. A shader will take a float32 copy of it;
that conversion is one function and belongs with the shader.
