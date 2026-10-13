"""
    UDeepCore

The mapping from scientific data to light and colour of the UDeep Figure Profile, for Julia.

    value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit

This is a thin binding to the project's Rust core (`core/`, through its C interface), not a second
implementation: the arithmetic lives in one place and is held to one set of golden vectors. The library is found
at `ENV["UDEEP_CORE_LIB"]`, or else in `core/target/release` of the repository this package sits in (build it
with `cargo build --release`).

Arrays are mapped element by element, so any array shape works and its shape is kept. Digests are defined on
values in row order (the last index fastest), so a Julia array is walked that way when it is digested.
"""
module UDeepCore

using Libdl
using SHA: sha256

# `position` is not exported: Base has a function of that name (for streams). Call it as UDeepCore.position.
export Mapping, light, light!, sdr_light, value, colours, digest_data, digest_pixels, core_version
export value_at, srgb_encode, srgb_decode, isolated, colour_table, hdr_view, HdrView, levels, Declaration
export description, caption, gainmap_jpeg, gainmap_from_renders, hdrimage!, hdrcolorbar!, save_hdr, hdr_declaration, hdr_caption, hdr_theme
export levels_pq16, png_pq, read_png_pq, xmp_packet

const STRETCHES = (linear = 0x00000000, log = 0x00000001, asinh = 0x00000002, proportional = 0x00000003)
const ERRORS = Dict(
    1 => "a required pointer was null",
    2 => "unknown stretch",
    3 => "the declared range needs range_high > range_low (and range_low > 0 for a log stretch)",
    4 => "the knee must lie inside the declared range, at least 1e-6 of it from the bottom, and either at the " *
         "top or at least 1e-6 of it below",
    5 => "reference_headroom must be >= 1 and exit_slope within 0..3",
    6 => "stretch_width must be > 0; a proportional mapping starts at zero with no offset, has " *
         "range_high = knee x reference_headroom (light is then value / knee throughout) and exit_slope 1",
    7 => "a colour-map table needs two segments of nodes x 3 numbers, at least two nodes each",
    8 => "the image cannot be written: sizes that do not match, SDR outside 0..1, an infinite HDR value, or " *
         "headroom not above 1",
    9 => "the image codec failed, a metadata segment was too large for the file format, or the gain map could " *
         "not be kept within the headroom at this gain quality (use gain_quality = 100)",
    10 => "the range, the knee, the headroom, the stretch width and the offset must all be finite numbers",
    11 => "a declaration the profile would refuse: a required text empty or not UTF-8, an unknown noise model or " *
          "knee rule, a noise parameter without its model, or a number that means nothing",
)
const DIGEST_LENGTH = 71
const HANDLE = Ref{Ptr{Cvoid}}(C_NULL)

"""
    Mapping(stretch, range_low, range_high, knee, reference_headroom; width = 1, offset = 0, exit_slope = 1)

The mapping a file declares, as one record. `stretch` is `:linear`, `:log`, `:asinh` or `:proportional`. The
layout is that of the C interface's `udeep_mapping`: seven `Float64`, then two `UInt32`, 64 bytes.
"""
struct Mapping
    range_low::Float64
    range_high::Float64
    knee::Float64
    reference_headroom::Float64
    stretch_width::Float64
    stretch_offset::Float64
    exit_slope::Float64
    stretch::UInt32
    reserved::UInt32
end

function Mapping(stretch::Symbol, range_low::Real, range_high::Real, knee::Real, reference_headroom::Real;
                 width::Real = 1.0, offset::Real = 0.0, exit_slope::Real = 1.0)
    haskey(STRETCHES, stretch) || throw(ArgumentError("stretch must be one of $(keys(STRETCHES)), got :$stretch"))
    m = Mapping(range_low, range_high, knee, reference_headroom, width, offset, exit_slope, STRETCHES[stretch], 0)
    check(ccall(symbol(:udeep_mapping_validate), Int32, (Ref{Mapping},), m))
    return m
end

function library_path()
    haskey(ENV, "UDEEP_CORE_LIB") && return ENV["UDEEP_CORE_LIB"]
    name = Sys.iswindows() ? "udeep_core.dll" : Sys.isapple() ? "libudeep_core.dylib" : "libudeep_core.so"
    return normpath(joinpath(@__DIR__, "..", "..", "..", "core", "target", "release", name))
end

function handle()
    if HANDLE[] == C_NULL
        path = library_path()
        isfile(path) || error("the udeep core library is not at $path: build it with `cargo build --release " *
                              "--features jpeg,png` in core/, or set ENV[\"UDEEP_CORE_LIB\"]")
        HANDLE[] = Libdl.dlopen(path)
    end
    return HANDLE[]
end

symbol(name::Symbol) = Libdl.dlsym(handle(), name)

check(code::Integer) = code == 0 ? nothing : throw(ArgumentError(get(ERRORS, Int(code), "udeep core error $code")))

"The core library's version, as a `VersionNumber`."
function core_version()
    v = ccall(symbol(:udeep_version), UInt32, ())
    return VersionNumber(v ÷ 10000, (v ÷ 100) % 100, v % 100)
end

function each(name::Symbol, m::Mapping, x::AbstractArray{<:Real})
    input = convert(Array{Float64}, x)
    out = similar(input)
    check(ccall(symbol(name), Int32, (Ref{Mapping}, Ptr{Float64}, Ptr{Float64}, Csize_t), m, input, out, length(input)))
    return out
end

"`UDeepCore.position(m, x)`: data values -> position in [0, 1]. Not exported (see the export list)."
position(m::Mapping, x::AbstractArray{<:Real}) = each(:udeep_position, m, x)

"`sdr_light(m, x)`: data values -> the fair SDR light: the same below the knee, SDR white above it."
sdr_light(m::Mapping, x::AbstractArray{<:Real}) = each(:udeep_sdr_light, m, x)

"`value(m, y)`: light -> data values, the inverse of [`light`](@ref)."
value(m::Mapping, y::AbstractArray{<:Real}) = each(:udeep_value, m, y)

"""
    light(m, x; threads = 0)

Data values -> light, in multiples of SDR white: the declared mapping. `threads = 0` uses every available core,
`threads = 1` none but the caller's; the result is the same, bit for bit. An array of fewer than 32768 values is
mapped on the caller's thread whatever `threads` is: starting another would cost more than it saved.

`x` is any array of real numbers, as for the other functions; the result is an `Array{Float64}` of its size.
"""
function light(m::Mapping, x::AbstractArray{<:Real}; threads::Integer = 0)
    # not similar(x, Float64): for a wrapper such as x' that is another wrapper, which light! cannot fill
    return light!(Array{Float64}(undef, size(x)), m, x; threads)
end

"`light!(out, m, x; threads = 0)`: as [`light`](@ref), into `out`."
function light!(out::Array{Float64}, m::Mapping, x::AbstractArray{<:Real}; threads::Integer = 0)
    length(out) == length(x) || throw(DimensionMismatch("out has $(length(out)) elements, x has $(length(x))"))
    input = convert(Array{Float64}, x)
    check(ccall(symbol(:udeep_light_parallel), Int32, (Ref{Mapping}, Ptr{Float64}, Ptr{Float64}, Csize_t, Csize_t),
                m, input, out, length(input), threads))
    return out
end

"""
    colours(table, headroom, y)

Light -> linear RGB (BT.709 primaries, multiples of SDR white). `table` is a colour-map table as the project ships
them (`Vector{Float32}` of `2 * nodes * 3` numbers) or `nothing` for grey. Returns an array of the shape of `y`
with a last dimension of 3.
"""
function colours(table::Union{Nothing, Vector{Float32}}, headroom::Real, y::AbstractArray{<:Real})
    input = convert(Array{Float64}, y)
    rgb = Array{Float64}(undef, 3, length(input))
    pointer_, nodes = table === nothing ? (Ptr{Float32}(C_NULL), 0) : (pointer(table), length(table) ÷ 6)
    GC.@preserve table check(ccall(symbol(:udeep_colours), Int32,
                                   (Ptr{Float32}, Csize_t, Float64, Ptr{Float64}, Ptr{Float64}, Csize_t),
                                   pointer_, nodes, headroom, input, rgb, length(input)))
    return permutedims(reshape(rgb, 3, size(input)...), (ntuple(i -> i + 1, ndims(input))..., 1))
end

row_order(a::AbstractArray) = vec(permutedims(a, ndims(a):-1:1))

function digest(call)
    out = zeros(UInt8, DIGEST_LENGTH + 1)
    check(call(out))
    return String(out[1:DIGEST_LENGTH])
end

"""
    digest_data(a)

The digest that ties a file to its data (`udeep:DataDigest`): of the values and shape of `a`, whatever type it is
stored as. Every NaN counts as the same value. An array with no dimensions, such as `fill(1.5)`, has the digest of
the one-element vector `[1.5]`: the Python reference digests a scalar so, and the core follows it.
"""
function digest_data(a::AbstractArray{<:Real})
    values, shape = convert(Vector{Float64}, row_order(a)), collect(Csize_t, size(a))
    return digest(out -> ccall(symbol(:udeep_digest_data), Int32, (Ptr{Csize_t}, Csize_t, Ptr{Float64}, Ptr{UInt8}),
                               shape, length(shape), values, out))
end

"`digest_pixels(rgb)`: the digest of an 8-bit RGB image, `height x width x 3` (`udeep:SdrDigest`)."
function digest_pixels(rgb::AbstractArray{UInt8, 3})
    size(rgb, 3) == 3 || throw(ArgumentError("pixels must be height x width x 3"))
    bytes = row_order(rgb)
    return digest(out -> ccall(symbol(:udeep_digest_pixels), Int32, (Csize_t, Csize_t, Ptr{UInt8}, Ptr{UInt8}),
                               size(rgb, 1), size(rgb, 2), bytes, out))
end

include("figure.jl")
include("png.jl")

end # module
