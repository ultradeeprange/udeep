# UDeepCore.jl

The UDeep Figure Profile for Julia: the mapping from data to light and colour, and HDR figures with Makie.

It is a thin binding to the project's Rust core (`../../core`, through its C interface), not another
implementation: the arithmetic lives in one place and is held to one set of golden vectors.

**Status: a draft, not installable on its own.** It needs the core library built from this repository. Figures
work with CairoMakie; GLMakie and WGLMakie are not covered.

## A figure

```julia
using CairoMakie, UDeepCore

m = Mapping(:linear, 0.0, 100.0, 50.0, 4.0)            # stretch, range, knee (shown at SDR white), headroom
fig = Figure()
ax = Axis(fig[1, 1])
plot = hdrimage!(ax, temperature, m; mode = :highlight, cmap = "fire",
                 unit = "K", knee_rule = "physical; the melting point", noise = (:constant, 2.5))
hdrcolorbar!(fig[1, 2], plot; label = "temperature (K)")
save_hdr("figure.jpg", fig)                            # a gain-map JPEG that declares its mapping
hdr_caption(fig)                                       # a caption paragraph written from the declaration
```

The figure on screen and in `save("figure.png", fig)` is the ordinary SDR one. `save_hdr` writes a JPEG that any
viewer shows as that same figure, and that an HDR display shows with the values above the knee brighter than
white, up to the headroom. `examples/figure.jl` is a complete script.

- `mode = :highlight` holds single bright pixels at white, so noise cannot sparkle (the file declares how many);
  `:extend` lifts everything above the knee.
- `unit`, `knee_rule` and `noise` are what the file declares. Without the first two `save_hdr` refuses, unless
  `metadata = false`, and then the file does not conform to the profile.
- Text, lines and anything translucent drawn over an image are left as they are: a pixel goes above white only
  where an HDR image alone determines it. An image drawn with `alpha` below 1 therefore cannot be lifted, and
  `save_hdr` says so.
- One file declares one mapping: every `hdrimage!` in a figure must share it.
- For a dark page, build the figure inside `with_theme(hdr_theme(:dark)) do ... end` (`:light` for a light one;
  `ground` and `ink` take a page's own colours). Only the furniture changes: the data, the mapping, the lifted
  pixels and what the file declares are the same, so the two are versions of one figure (profile 8.7).

What is not there yet: the colour maps `"fire"` and `"ice"` work at headrooms 2, 4, 8 and 16 only (`"grey"` at
any), because only the Python reference makes tables; the knee must be given (no percentile fallback); the figure
is saved whole, with no tight crop.

## Arrays

```julia
y = light(m, hu)                                       # data -> light, in multiples of SDR white; any array shape
rgb = colours(colour_table("fire", 4.0), 4.0, y)       # light -> linear RGB; `nothing` for grey
hu_again = value(m, y)                                 # and back
view = hdr_view(hu, m; mode = :extend)                 # the fair SDR and the HDR renderings of an image
digest_data(hu)                                        # "sha256:..." as the Python reference computes it
max_step, ratio = levels(m, nothing, 4.0; noise = (:constant, 8.6))    # what an 8-bit file can resolve
file = gainmap_jpeg(view.sdr, view.hdr, 4.0)           # the bytes of a gain-map JPEG
```

`light` uses every core by default (`threads = 1` for one); the result is the same bit for bit. An array of
fewer than 32768 values is mapped on the calling thread whatever `threads` says. Every function takes any array
of real numbers (a range, a view, a row vector `x'`) and returns a plain `Array{Float64}` of its size.

A `Mapping` is checked when it is made, by the rules of the Python reference: its numbers must be finite; the
knee must sit at least 1e-6 of the range from the bottom, and at the top or at least 1e-6 below it; a
`:proportional` mapping needs `range_high = knee * reference_headroom` (to a relative 1e-9) and `exit_slope = 1`,
so that light is `value / knee` throughout. Anything else throws an `ArgumentError` that says which.
`UDeepCore.position` is not exported, because Base has a function of that name.

`Declaration`, `description` and `caption` give what a file declares, the element it carries and the caption, for
writing a file without Makie.

## Build and test

    cd ../../core && cargo build --release --features jpeg
    julia --project=. -e 'using Pkg; Pkg.test()'        # 365 tests

The tests cover the golden cases the binding reaches (mappings, the mask rule, stored levels and the data
criterion, the declaration and caption, digests), threads, shapes and errors, and figures with CairoMakie: which
pixels are lifted and which are left, what the file declares, what is refused. The repository's Python tests
(`../../tests/test_udeep_julia.py`) run `examples/figure.jl` and have the reference validator read the file; for
that, set up the example once with `julia --project=examples -e 'using Pkg; Pkg.instantiate()'`.

The library is looked for at `ENV["UDEEP_CORE_LIB"]`, then in `core/target/release` of this repository; the
colour-map tables at `ENV["UDEEP_TABLES"]`, then in `src/udeep/tables`. Neither is packaged as an artifact yet.

## How a figure is saved

The figure is rendered four times with only the HDR images' colours changed: as it is; with the HDR images scaled
into range; with them black; with them white. The second, multiplied by the headroom, is the HDR figure, but only
at pixels that are black in the third and white in the fourth: those are made by an HDR image alone. Every other
pixel is the first render's. This is the method of the Python reference (`udeep.mpl.savefig_hdr`); that Makie
renders a figure identically each time was checked first (`../../project/makie-feasibility.md`).

## A note on array order

Julia stores arrays column by column; the profile's digests are defined on values row by row. `digest_data` and
`digest_pixels` walk the array in row order, so a Julia array and the same array in Python give the same digest.
The test checks this against the golden case. An array with no dimensions (`fill(1.5)`) has the digest of the
one-element vector, because that is how the Python reference digests a scalar.

`hdrimage!` draws a matrix as Makie's `image!` does: the first index runs along x, the second along y.
