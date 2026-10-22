# What a figure needs beyond the mapping: colour-map tables, the two renderings of an image, what the file
# declares, and the file itself. Every number still comes from the Rust core.

const REGISTERED_HEADROOMS = (2.0, 4.0, 8.0, 16.0)
const COLOUR_MAPS = ("fire", "ice")
const COLOUR_MAP_VERSION = 3             # what new figures use; versions 1 and 2 stay readable
const COLOUR_MAP_VERSIONS = (1, 2, 3)
const ISOLATED = "isolated-3x3"
const NOISE_MODELS = (unknown = 0x00000000, constant = 0x00000001, poisson = 0x00000002, relative = 0x00000003,
                      tolerance = 0x00000004)
const UNBOUNDED = 1e9
const EMBED_NODES = 257                  # per segment, in a table made for a headroom with no published one
const TABLES = Dict{Tuple{String, Float64}, Vector{Float32}}()

function buffers(name::Symbol, x::AbstractArray{<:Real})
    input = convert(Array{Float64}, x)
    out = similar(input)
    check(ccall(symbol(name), Int32, (Ptr{Float64}, Ptr{Float64}, Csize_t), input, out, length(input)))
    return out
end

"`srgb_encode(linear)`: linear light -> sRGB signal, both in [0, 1]; values outside are clipped, NaN stays NaN."
srgb_encode(linear::AbstractArray{<:Real}) = buffers(:udeep_srgb_encode, linear)

"`srgb_decode(signal)`: sRGB signal -> linear light, the inverse of [`srgb_encode`](@ref)."
srgb_decode(signal::AbstractArray{<:Real}) = buffers(:udeep_srgb_decode, signal)

"""
    digest_sdr(sdr)

The `udeep:SdrDigest` of a fair SDR rendering given as light (`height x width x 3`, linear, 0 to 1: the `sdr` of
an [`HdrView`](@ref)), taken over the 8-bit sRGB codes the writers store for it, with NaN as 0. A file with no SDR
base of its own, such as a 16-bit PQ PNG, must declare it (profile 9.1.2); the gain-map JPEG of the same figure
then declares the same value. `algorithm = "sha3-256"` gives `udeep:SdrDigestSha3`.
"""
digest_sdr(sdr::AbstractArray{<:Real, 3}; algorithm::AbstractString = "sha256") =
    digest_pixels(round.(UInt8, srgb_encode(replace(Float64.(sdr), NaN => 0.0)) .* 255); algorithm)

"`value_at(m, t)`: position in [0, 1] -> data values, the inverse of `UDeepCore.position` inside the range."
value_at(m::Mapping, t::AbstractArray{<:Real}) = each(:udeep_value_at, m, t)

"""
    isolated(above)

The `isolated-3x3` mask rule: the pixels of the boolean image `above` (light above SDR white) that lie in no full
3 x 3 block of it. The Highlight preset holds these at white, so that noise cannot sparkle.
"""
function isolated(above::AbstractMatrix{Bool})
    given = convert(Vector{UInt8}, row_order(above))
    out = similar(given)
    check(ccall(symbol(:udeep_mask_isolated), Int32, (Ptr{UInt8}, Csize_t, Csize_t, Ptr{UInt8}),
                given, size(above, 1), size(above, 2), out))
    return permutedims(reshape(out .!= 0, size(above, 2), size(above, 1)))
end

function tables_path()
    haskey(ENV, "UDEEP_TABLES") && return ENV["UDEEP_TABLES"]
    return normpath(joinpath(@__DIR__, "..", "..", "..", "src", "udeep", "tables"))
end

"""
    colour_table(name, headroom)

The colour-map table for `"fire"` or `"ice"` at a reference headroom; `nothing` for `"grey"`, which needs none. The
name alone is the map's current version (3); `"fire/1"` names an older one, for reproducing a figure made with it.

At a registered headroom (2, 4, 8 or 16) this is the published table, as the project ships it (profile 6.10), read
from `ENV["UDEEP_TABLES"]`, or else from `src/udeep/tables` of the repository this package sits in. At headroom 1,
an SDR figure, it is the lower segment of the published table for 2. At any other headroom the Rust core makes the
table, at $EMBED_NODES nodes a segment, by the generator the published tables were made with, and a file drawn with it
must carry it: [`colour_table_text`](@ref) gives what it declares, and the figure writers declare it themselves.
"""
function colour_table(name::AbstractString, headroom::Real)
    name == "grey" && return nothing
    name, version = map_and_version(name)
    h = Float64(headroom)
    h >= 1.0 || throw(ArgumentError("the reference headroom must be at least 1, not $headroom"))
    return get!(TABLES, ("$name/$version", h)) do
        if h in REGISTERED_HEADROOMS || h == 1.0
            file = joinpath(tables_path(), "$name-$version-h$(Int(max(h, 2.0))).f32")
            isfile(file) || error("the colour-map table $file is missing: set ENV[\"UDEEP_TABLES\"]")
            t = collect(reinterpret(Float32, read(file)))
            h == 1.0 || return t
            below = t[1:length(t) ÷ 2]                   # nothing above white: the top of the lower segment, repeated
            return vcat(below, repeat(below[end - 2:end], length(below) ÷ 3))
        end
        made = Vector{Float32}(undef, 6 * EMBED_NODES)
        check(ccall(symbol(:udeep_colour_table), Int32, (Ptr{UInt8}, Csize_t, UInt32, Float64, Csize_t, Ptr{Float32}),
                    name, sizeof(name), version, h, EMBED_NODES, made))
        made
    end
end

"""
    colour_table_text(name, headroom)

What a file declares as `udeep:ColourMapTable` for a map at a reference headroom: the table of
[`colour_table`](@ref) as the node count, `;`, and base64 of its numbers (little-endian float32), as the Python
reference's `Table.to_text` writes it. `nothing` where the file need not carry one: `"grey"`, or a registered map at
headroom 1, 2, 4, 8 or 16.
"""
function colour_table_text(name::AbstractString, headroom::Real)
    h = Float64(headroom)
    (name == "grey" || h == 1.0 || h in REGISTERED_HEADROOMS) && return nothing
    table = colour_table(name, h)
    return string(length(table) ÷ 6, ";", base64encode(reinterpret(UInt8, htol.(table))))
end

"`colour_map_name(name)`: what a file declares for a map: `\"grey\"`, or name/version such as `\"fire/3\"`."
colour_map_name(name::AbstractString) = name == "grey" ? "grey" : join(map_and_version(name), "/")

"A map's name and version: `\"fire\"` is the current version, `\"fire/1\"` that one."
function map_and_version(name::AbstractString)
    parts = split(name, "/")
    version = length(parts) == 2 ? tryparse(Int, parts[2]) : length(parts) == 1 ? COLOUR_MAP_VERSION : nothing
    (parts[1] in COLOUR_MAPS && version in COLOUR_MAP_VERSIONS) ||
        throw(ArgumentError("colour map must be \"grey\", one of $COLOUR_MAPS, or one of those with a version from " *
                            "$COLOUR_MAP_VERSIONS such as \"fire/1\"; got \"$name\""))
    return String(parts[1]), version
end

"""
The two renderings of an image, as linear RGB in multiples of SDR white (`size(data)..., 3`): `sdr` is the fair SDR
plot (the same below the knee, white above it) and `hdr` the HDR plot. `light` is the HDR light before colour.
"""
struct HdrView
    sdr::Array{Float64, 3}
    hdr::Array{Float64, 3}
    light::Matrix{Float64}
    masked::Int                              # pixels in the validity mask the caller supplied
    derived_mask::Union{Nothing, String}     # rule name of a mask derived from neighbourhoods, if any
    derived_masked::Int                      # pixels that rule holds, beyond the validity mask
    above_white::Float64                     # fraction of pixels lifted above white
end

"""
    hdr_view(data, m; mode = :highlight, cmap = "fire", mask = nothing)

Map an image by the two presets of the profile (section 13), as the Python reference's `hdr_view` does:

- `:highlight`: sparse extremes go above SDR white; pixels above the knee that belong to no 3 x 3 block of such
  pixels are held at white (the derived mask `isolated-3x3`).
- `:extend`: a second range of real structure goes above white; no area filter.

Pixels where `mask` is true (saturated, clipped, invalid) are never lifted. The mapping itself is pointwise.
"""
function hdr_view(data::AbstractMatrix{<:Real}, m::Mapping; mode::Symbol = :highlight, cmap::AbstractString = "fire",
                  mask::Union{Nothing, AbstractMatrix{Bool}} = nothing)
    mode in (:highlight, :extend) || throw(ArgumentError("mode must be :highlight or :extend, got :$mode"))
    mask === nothing || size(mask) == size(data) || throw(DimensionMismatch("mask and data differ in size"))
    table = colour_table(cmap, m.reference_headroom)
    y_sdr, y = sdr_light(m, data), light(m, data)
    valid = mask === nothing ? falses(size(data)) : convert(BitMatrix, mask)
    # the rule sees only the pixels that would be lifted: a masked one is held, so it cannot support a block
    derived = mode === :highlight ? isolated(collect((y .> 1.0) .& .!valid)) : falses(size(data))
    held = valid .| derived
    y[held] .= y_sdr[held]
    return HdrView(colours(table, m.reference_headroom, y_sdr), colours(table, m.reference_headroom, y), y,
                   count(valid), mode === :highlight ? ISOLATED : nothing, count(derived),
                   count(>(1.0 + 1e-9), y) / length(y))
end

"""
    levels(m, table, scaled_by; noise = (:unknown, nothing), samples = 65536)

The stored levels of an 8-bit gain-map rendering of `m` through `table` (`nothing` for grey), written for the
headroom `scaled_by`. Returns `(max_data_step, rounding_to_noise)`: the widest run of data values that share one
stored code (`udeep:MaxDataStep`), and the data criterion's worst ratio of rounding error to the stated
uncertainty (`udeep:RoundingToNoise`, at most 1e9), or `nothing` when the noise is unknown.

`noise` is `(:constant, sigma)`, `(:poisson, scale)`, `(:relative, fraction)`, `(:tolerance, value)` or
`(:unknown, nothing)`.
"""
function levels(m::Mapping, table::Union{Nothing, Vector{Float32}}, scaled_by::Real;
                noise::Tuple{Symbol, Any} = (:unknown, nothing), samples::Integer = 1 << 16)
    haskey(NOISE_MODELS, noise[1]) || throw(ArgumentError("noise model must be one of $(keys(NOISE_MODELS))"))
    (noise[1] === :unknown) == (noise[2] === nothing) ||
        throw(ArgumentError("a noise parameter is needed for every noise model except :unknown, and only then"))
    step, ratio = Ref(0.0), Ref(0.0)
    pointer_, nodes = table === nothing ? (Ptr{Float32}(C_NULL), 0) : (pointer(table), length(table) ÷ 6)
    GC.@preserve table check(ccall(symbol(:udeep_levels), Int32,
                                   (Ref{Mapping}, Ptr{Float32}, Csize_t, Float64, Csize_t, UInt32, Float64,
                                    Ref{Float64}, Ref{Float64}),
                                   m, pointer_, nodes, scaled_by, samples, NOISE_MODELS[noise[1]],
                                   noise[2] === nothing ? 0.0 : Float64(noise[2]), step, ratio))
    return step[], noise[1] === :unknown ? nothing : ratio[]
end

"""
    Declaration(; mapping, data_unit, knee_rule, max_data_step, ...)

What a file declares about its data and mapping (profile section 8), under the names of the `udeep:` properties.
`mapping`, `data_unit`, `knee_rule` and `max_data_step` must be given. The profile's version and image state are
the core's own.
"""
Base.@kwdef struct Declaration
    mapping::Mapping
    data_unit::String
    knee_rule::String
    max_data_step::Float64
    noise_model::String = "unknown"
    noise_parameter::Union{Nothing, Float64} = nothing
    rounding_to_noise::Union{Nothing, Float64} = nothing
    masked::Int = 0
    derived_mask::Union{Nothing, String} = nothing
    derived_masked::Int = 0
    record::String = "rendering"
    preset::Union{Nothing, String} = nothing
    colour_map::Union{Nothing, String} = nothing
    colour_map_table::Union{Nothing, String} = nothing
    data_digest::Union{Nothing, String} = nothing
    data_source::Union{Nothing, String} = nothing
    data_unit_code::Union{Nothing, String} = nothing
    sdr_digest::Union{Nothing, String} = nothing
    data_digest_sha3::Union{Nothing, String} = nothing     # each only with its SHA-256 partner (profile 8.5)
    sdr_digest_sha3::Union{Nothing, String} = nothing
end

struct CText
    data::Ptr{UInt8}
    length::Csize_t
end

# udeep_declaration, field for field as in core/include/udeep_core.h
struct CDeclaration
    mapping::Mapping
    max_data_step::Float64
    noise_parameter::Float64
    rounding_to_noise::Float64
    masked::UInt64
    derived_masked::UInt64
    texts::NTuple{14, CText}
end

texts(d::Declaration) = (d.data_unit, d.knee_rule, d.noise_model, d.record, d.derived_mask, d.preset, d.colour_map,
                         d.colour_map_table, d.data_digest, d.data_source, d.data_unit_code, d.sdr_digest,
                         d.data_digest_sha3, d.sdr_digest_sha3)

function owned_text(name::Symbol, d::Declaration)
    (d.masked >= 0 && d.derived_masked >= 0) || throw(ArgumentError("masked pixel counts cannot be negative"))
    kept = map(t -> t === nothing ? nothing : Vector{UInt8}(codeunits(t)), texts(d))
    data, length_ = Ref(Ptr{UInt8}(C_NULL)), Ref(Csize_t(0))
    absent(v) = v === nothing ? NaN : v
    GC.@preserve kept begin
        c = CDeclaration(d.mapping, d.max_data_step, absent(d.noise_parameter), absent(d.rounding_to_noise), d.masked,
                         d.derived_masked,
                         map(b -> b === nothing ? CText(C_NULL, 0) : CText(pointer(b), length(b)), kept))
        check(ccall(symbol(name), Int32, (Ref{CDeclaration}, Ref{Ptr{UInt8}}, Ref{Csize_t}), c, data, length_))
    end
    text = unsafe_string(data[], length_[])
    ccall(symbol(:udeep_bytes_free), Cvoid, (Ptr{UInt8}, Csize_t), data[], length_[])
    return text
end

"`description(d)`: the declaration as the `rdf:Description` element a file carries, exactly as the reference writes it."
description(d::Declaration) = owned_text(:udeep_description, d)

"`caption(d)`: a caption paragraph generated from the declaration (profile 8.2)."
caption(d::Declaration) = owned_text(:udeep_caption, d)

"""
    combined_digest(digests; algorithm = "sha256")

The data digest of a figure: of one image, its own; of several, a digest of their digests in drawing order, as
the Python reference combines them. With `algorithm = "sha3-256"`, of their SHA3-256 digests, with SHA3-256.
"""
function combined_digest(digests::AbstractVector{<:AbstractString}; algorithm::AbstractString = "sha256")
    hash_of(algorithm)
    isempty(digests) && throw(ArgumentError("no digests to combine"))
    all(d -> startswith(d, algorithm * ":"), digests) ||
        throw(ArgumentError("every digest combined with $algorithm must be a $algorithm digest"))
    length(digests) == 1 && return String(only(digests))
    joined = join(digests, "|")
    return algorithm * ":" * bytes2hex(algorithm == "sha256" ? sha256(joined) : sha3_256(joined))
end

"""
    gainmap_jpeg(sdr, hdr, headroom; quality = 95, gain_quality = 100, description = "")

A gain-map JPEG (Ultra HDR layout, with the ISO 21496-1 block) as bytes. `sdr` and `hdr` are `height x width x 3`
linear RGB in multiples of SDR white: the ordinary figure, and the same figure with light above white. An ordinary
viewer shows `sdr`; an HDR one shows `hdr`, up to `headroom`. `description` is the declared metadata
([`description`](@ref)), or empty for a file that declares nothing and so does not conform to the profile.

A pixel that is not lifted is stored with a gain of exactly 1, and the file never reads above the brightest value
of `hdr` on the core's own decoder: the writer decodes its gain map and lowers it until nothing is over, and throws
if it cannot. Keep `gain_quality` at 100 unless the figure is grey: below it a gain map is unreliable with a
colour map (`"fire"`, `"ice"`), with this writer or any other, and the call may be refused.

Needs a core library built with `--features jpeg,png`.
"""
function gainmap_jpeg(sdr::AbstractArray{<:Real, 3}, hdr::AbstractArray{<:Real, 3}, headroom::Real;
                      quality::Integer = 95, gain_quality::Integer = 100, description::AbstractString = "")
    size(sdr) == size(hdr) && size(sdr, 3) == 3 ||
        throw(DimensionMismatch("sdr and hdr must both be height x width x 3; got $(size(sdr)) and $(size(hdr))"))
    writer = Libdl.dlsym(handle(), :udeep_gainmap_jpeg; throw_error = false)
    writer === nothing && error("this core library has no JPEG writer: build it with `cargo build --release " *
                                "--features jpeg,png`")
    base, lifted = convert(Vector{Float64}, row_order(sdr)), convert(Vector{Float64}, row_order(hdr))
    text = Vector{UInt8}(codeunits(description))
    data, length_ = Ref(Ptr{UInt8}(C_NULL)), Ref(Csize_t(0))
    check(ccall(writer, Int32,
                (Ptr{Float64}, Ptr{Float64}, Csize_t, Csize_t, Float64, UInt32, UInt32, Ptr{UInt8}, Csize_t,
                 Ref{Ptr{UInt8}}, Ref{Csize_t}),
                base, lifted, size(sdr, 2), size(sdr, 1), headroom, quality, gain_quality, text, length(text), data,
                length_))
    bytes = copy(unsafe_wrap(Array, data[], length_[]))
    ccall(symbol(:udeep_bytes_free), Cvoid, (Ptr{UInt8}, Csize_t), data[], length_[])
    return bytes
end

"""
    gainmap_from_renders(base, scaled, black, white, headroom; quality = 95, gain_quality = 100, description = "")

A figure's gain-map JPEG straight from four 8-bit renders of it, each `height x width x 3` (`UInt8`, sRGB codes):
the figure as it is, which becomes the file's SDR image unchanged; the same with each HDR image divided by the
headroom; and the same with the HDR images black, and white. A pixel that is black in the third and white in the
fourth is made by an HDR image alone, and only such a pixel is lifted (profile 6.11). Returns the file's bytes and
the number of pixels lifted above the ordinary figure.

It is what `save_hdr` uses: the same file as [`gainmap_jpeg`](@ref) would write from the two float figures, without
making them.
"""
function gainmap_from_renders(base::AbstractArray{UInt8, 3}, scaled::AbstractArray{UInt8, 3},
                              black::AbstractArray{UInt8, 3}, white::AbstractArray{UInt8, 3}, headroom::Real;
                              quality::Integer = 95, gain_quality::Integer = 100, description::AbstractString = "")
    all(r -> size(r) == size(base), (scaled, black, white)) && size(base, 3) == 3 ||
        throw(DimensionMismatch("the four renders must all be height x width x 3"))
    writer = Libdl.dlsym(handle(), :udeep_gainmap_from_renders; throw_error = false)
    writer === nothing && error("this core library has no JPEG writer: build it with `cargo build --release " *
                                "--features jpeg,png`")
    rows = map(r -> convert(Vector{UInt8}, row_order(r)), (base, scaled, black, white))
    text = Vector{UInt8}(codeunits(description))
    data, length_, lifted = Ref(Ptr{UInt8}(C_NULL)), Ref(Csize_t(0)), Ref(Csize_t(0))
    check(ccall(writer, Int32,
                (Ptr{UInt8}, Ptr{UInt8}, Ptr{UInt8}, Ptr{UInt8}, Csize_t, Csize_t, Float64, UInt32, UInt32, Ptr{UInt8},
                 Csize_t, Ref{Ptr{UInt8}}, Ref{Csize_t}, Ref{Csize_t}),
                rows[1], rows[2], rows[3], rows[4], size(base, 2), size(base, 1), headroom, quality, gain_quality, text,
                length(text), data, length_, lifted))
    bytes = copy(unsafe_wrap(Array, data[], length_[]))
    ccall(symbol(:udeep_bytes_free), Cvoid, (Ptr{UInt8}, Csize_t), data[], length_[])
    return bytes, Int(lifted[])
end

# ---- Figures: defined by the Makie extension (ext/UDeepCoreMakieExt.jl), which loads with Makie

const NEEDS_MAKIE = "this needs Makie: load a Makie backend first, such as `using CairoMakie`"

"""
    hdrimage!(ax, data, m; mode = :highlight, cmap = "fire", mask = nothing, unit = nothing, knee_rule = nothing,
              noise = (:unknown, nothing), source = nothing, unit_code = nothing, kw...)
    hdrimage!(ax, x, y, data, m; ...)

Like Makie's `image!`, for the profile's two presets: draws the fair SDR plot of `data` under the mapping `m`, and
remembers the HDR one for [`save_hdr`](@ref). Returns the plot. `data[i, j]` is drawn at the i-th place along x
and the j-th along y, as `image!` draws a matrix; `x` and `y` are the intervals it spans (default `0 .. size`).

`unit`, `knee_rule` and `noise` are what the file will declare (profile section 8): the data's unit (`"1"` if it
has none); why the knee is where it is, as `"physical; ..."`, `"statistical; ..."` or `"preset; ..."`; and the noise
model with its parameter, such as `(:constant, 10.0)`. `source` says where the data is kept (a DOI, a URL); the file
also records a digest of `data`. `unit_code` is the unit as a UCUM code. A NaN in `data` is drawn black.

Further keywords go to `image!`. The image must stay opaque and uncovered where it is to go above white.
"""
function hdrimage! end

"""
    hdrcolorbar!(position, plot; label = "", n = 256, width = 18)

An HDR colour bar for an [`hdrimage!`](@ref) plot at a figure position such as `fig[1, 2]`: ticks in data units,
and the value shown at SDR white. Returns its axis.
"""
function hdrcolorbar! end

"""
    save_hdr(path, fig; quality = 95, gain_quality = 100, px_per_unit = 2, metadata = true)

Write the figure as a gain-map JPEG: the ordinary figure, plus gain on the [`hdrimage!`](@ref) plots only. The file
carries the profile's declared metadata unless `metadata = false`; without it the file does not conform.

A pixel is lifted only where an HDR image alone determines it, so text, lines and anything translucent drawn over
an image stay as they are in the ordinary figure. The SDR figure the file's digest refers to is the figure as the
backend renders it at `px_per_unit`. Keep `gain_quality` at 100 with a colour map (see [`gainmap_jpeg`](@ref)).
"""
function save_hdr end

"""
    HdrFigure(data, caption)

A figure as its gain-map JPEG, for a notebook to show ([`hdr_inline`](@ref)): `data` is the file, byte for byte what
[`save_hdr`](@ref) writes, and `caption` the caption generated from its declaration. Jupyter, Pluto and VS Code show
it as an image. The file itself is what the page is given, so a browser that reads gain maps shows the images above
white on an HDR display, and any other shows the ordinary figure. `write(path, f)` keeps the file.
"""
struct HdrFigure
    data::Vector{UInt8}
    caption::String
end

escape_html(s::AbstractString) =
    replace(s, "&" => "&amp;", "<" => "&lt;", ">" => "&gt;", "\"" => "&quot;")

function Base.show(io::IO, ::MIME"text/html", f::HdrFigure)
    text = escape_html(f.caption)
    print(io, "<figure style=\"margin: 0\"><img src=\"data:image/jpeg;base64,", base64encode(f.data), "\" alt=\"",
          text, "\">", isempty(text) ? "" : "<figcaption>$text</figcaption>", "</figure>")
end
Base.show(io::IO, ::MIME"image/jpeg", f::HdrFigure) = write(io, f.data)
Base.show(io::IO, f::HdrFigure) = print(io, "UDeep figure: a gain-map JPEG of ", length(f.data), " bytes")
Base.write(io::IO, f::HdrFigure) = write(io, f.data)

"""
    hdr_inline(fig; caption = true, quality = 95, gain_quality = 100, px_per_unit = 2)

The figure as [`save_hdr`](@ref) writes it, to show in a notebook: end a cell with `hdr_inline(fig)`. With `caption`
the caption generated from the file's declaration is shown under it. Returns an [`HdrFigure`](@ref); `write(path, f)`
keeps the file. Needs a Makie backend loaded (CairoMakie).
"""
function hdr_inline end

"`hdr_declaration(fig)`: what the figure declares ([`Declaration`](@ref)), as `save_hdr` would write it, less the digest of the SDR figure."
function hdr_declaration end

"`hdr_caption(fig)`: a caption paragraph generated from the figure's declaration (profile 8.2)."
function hdr_caption end

"""
    hdr_theme(name = :dark; ground = nothing, ink = nothing)

A Makie `Theme` for a figure's furniture on a light or a dark page: the background, and the text, axes and ticks
drawn on it. `name` is `:light` or `:dark`; `ground` and `ink` replace the background and text colours, to match a
particular page. Use it around the code that builds the figure:

    with_theme(hdr_theme(:dark)) do
        fig = Figure()
        ...
        save_hdr("figure_dark.jpg", fig)
    end

A theme changes nothing about the data: the image, its colours, the mapping and everything the file declares about
them are the same in every theme, and so is the data's digest. Only the SDR figure differs, so each theme is its
own file with its own SDR digest (profile 8.7). The colours are those of the Python reference's
`udeep.mpl.theme`. A colour given to a plot by hand, such as `text!(...; color = :black)`, is not changed.
"""
function hdr_theme end
