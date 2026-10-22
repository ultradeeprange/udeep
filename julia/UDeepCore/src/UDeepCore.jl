"""
    UDeepCore

The mapping from scientific data to light and colour of the UDeep Figure Profile, for Julia.

    value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit

This is a thin binding to the project's Rust core (`core/`, through its C interface), not a second
implementation: the arithmetic lives in one place and is held to one set of golden vectors. The library is found
at `ENV["UDEEP_CORE_LIB"]`; or else as the artifact the package was installed with (`Artifacts.toml`, written for
a release); or else in `core/target/release` of the repository this package sits in (build it with
`cargo build --release --features jpeg,png`).

Arrays are mapped element by element, so any array shape works and its shape is kept. Digests are defined on
values in row order (the last index fastest), so a Julia array is walked that way when it is digested.
"""
module UDeepCore

using Artifacts
using Base64: base64encode
using Libdl
using SHA: sha256, sha3_256

# `position` is not exported: Base has a function of that name (for streams). Call it as UDeepCore.position.
export Mapping, light, light!, sdr_light, value, colours, digest_data, digest_pixels, digest_sdr, core_version, core_build
export value_at, srgb_encode, srgb_decode, isolated, colour_table, colour_table_text, hdr_view, HdrView, levels, Declaration
export description, caption, gainmap_jpeg, gainmap_from_renders, hdrimage!, hdrcolorbar!, save_hdr, hdr_declaration, hdr_caption, hdr_theme
export hdr_inline, HdrFigure
export levels_pq16, png_pq, read_png_pq, read_png_data, xmp_packet

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
          "knee rule, a noise parameter without its model, or a number that means nothing; or a declaration for " *
          "a PQ file whose white is not at 203 cd/m2 (profile 9.1.1)",
)
const DIGEST_LENGTH = 71
const DIGEST_SHA3_LENGTH = 73                  # "sha3-256:" and 64 hex digits
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

"""
The core library that came with the package: the artifact `udeep_core` of `Artifacts.toml`, for this machine.
`nothing` when the package has no such file (a checkout between releases), names no archive for this platform,
or the archive is not installed.
"""
function artifact_library(name::AbstractString)
    toml = find_artifacts_toml(@__DIR__)
    toml === nothing && return nothing
    meta = artifact_meta("udeep_core", toml)
    meta === nothing && return nothing
    path = joinpath(artifact_path(Base.SHA1(meta["git-tree-sha1"])), "lib", name)
    return isfile(path) ? path : nothing
end

function library_path()
    haskey(ENV, "UDEEP_CORE_LIB") && return ENV["UDEEP_CORE_LIB"]
    name = Sys.iswindows() ? "udeep_core.dll" : Sys.isapple() ? "libudeep_core.dylib" : "libudeep_core.so"
    installed = artifact_library(name)
    installed === nothing || return installed
    return normpath(joinpath(@__DIR__, "..", "..", "..", "core", "target", "release", name))
end

function handle()
    if HANDLE[] == C_NULL
        path = library_path()
        isfile(path) || error("the udeep core library is not at $path: build it with `cargo build --release " *
                              "--features jpeg,png` in core/, or set ENV[\"UDEEP_CORE_LIB\"]")
        plain = Libdl.dlopen(path)
        HANDLE[] = something(wider_build(plain, path), plain)
    end
    return HANDLE[]
end

"""
The core's x86-64-v3 build beside the plain one (`udeep_core_v3`), opened, where this processor can run it; else
`nothing`. The package's artifact carries both builds on x86-64, as the Python wheels do: the plain one, already open,
is asked (`udeep_processor_x86_64_v3`), since on an older processor the other would stop at its first wider
instruction. The two give the same bytes. `ENV["UDEEP_CORE_V3"] = "0"` keeps the plain one.
"""
function wider_build(plain, path::AbstractString)
    get(ENV, "UDEEP_CORE_V3", "") == "0" && return nothing
    wider = joinpath(dirname(path), replace(basename(path), "udeep_core" => "udeep_core_v3"; count = 1))
    probe = Libdl.dlsym(plain, :udeep_processor_x86_64_v3; throw_error = false)
    (isfile(wider) && probe !== nothing && ccall(probe, UInt32, ()) == 1) || return nothing
    return Libdl.dlopen(wider)
end

"`core_build()`: which build of the core is in use: `\"x86-64-v3\"`, or `\"any processor\"` of its architecture."
function core_build()
    built = Libdl.dlsym(handle(), :udeep_built_for_x86_64_v3; throw_error = false)
    return built !== nothing && ccall(built, UInt32, ()) == 1 ? "x86-64-v3" : "any processor"
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

# The hash functions a digest is taken with (profile 8.6): the core's function for each, and the length of its text.
const HASHES = Dict("sha256" => ("", DIGEST_LENGTH), "sha3-256" => ("_sha3", DIGEST_SHA3_LENGTH))

function hash_of(algorithm::AbstractString)
    haskey(HASHES, algorithm) ||
        throw(ArgumentError("a digest is taken with sha256 or sha3-256, not $(repr(algorithm))"))
    return HASHES[algorithm]
end

function digest(call, length_::Integer)
    out = zeros(UInt8, length_ + 1)
    check(call(out))
    return String(out[1:length_])
end

"""
    digest_data(a; algorithm = "sha256")

The digest that ties a file to its data (`udeep:DataDigest`): of the values and shape of `a`, whatever type it is
stored as. Every NaN counts as the same value. An array with no dimensions, such as `fill(1.5)`, has the digest of
the one-element vector `[1.5]`: the Python reference digests a scalar so, and the core follows it.
`algorithm = "sha3-256"` gives the same bytes digested with SHA3-256 (`udeep:DataDigestSha3`), so that the link to
the data does not rest on one hash function.
"""
function digest_data(a::AbstractArray{<:Real}; algorithm::AbstractString = "sha256")
    suffix, length_ = hash_of(algorithm)
    values, shape = convert(Vector{Float64}, row_order(a)), collect(Csize_t, size(a))
    return digest(out -> ccall(symbol(Symbol(:udeep_digest_data, suffix)), Int32,
                               (Ptr{Csize_t}, Csize_t, Ptr{Float64}, Ptr{UInt8}), shape, length(shape), values, out),
                  length_)
end

"""
`digest_pixels(rgb; algorithm = "sha256")`: the digest of an 8-bit RGB image, `height x width x 3`
(`udeep:SdrDigest`; with `algorithm = "sha3-256"`, `udeep:SdrDigestSha3`).
"""
function digest_pixels(rgb::AbstractArray{UInt8, 3}; algorithm::AbstractString = "sha256")
    suffix, length_ = hash_of(algorithm)
    size(rgb, 3) == 3 || throw(ArgumentError("pixels must be height x width x 3"))
    bytes = row_order(rgb)
    return digest(out -> ccall(symbol(Symbol(:udeep_digest_pixels, suffix)), Int32,
                               (Csize_t, Csize_t, Ptr{UInt8}, Ptr{UInt8}), size(rgb, 1), size(rgb, 2), bytes, out),
                  length_)
end

include("figure.jl")
include("png.jl")

end # module
