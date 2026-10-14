# The 16-bit PQ PNG: the form in which a file can hold its data to within the data's own uncertainty (profile 7.3),
# where an 8-bit gain-map JPEG cannot. Written, read and measured by the core.

const REF_WHITE_NITS = 203.0             # the reference white of BT.2408, as the reference implementation has it
const RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"

"""
    xmp_packet(d)

The declaration as a complete XMP packet, as the reference's `udeep.metadata.to_xmp` writes it: the element of
[`description`](@ref) inside the packet's wrapper. It is what a PNG carries in its `iTXt` chunk.
"""
xmp_packet(d::Declaration) =
    "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" x:xmptk=\"udeep\"><rdf:RDF xmlns:rdf=\"$RDF\">" * description(d) *
    "</rdf:RDF></x:xmpmeta>"

"The core's function of this name, or an error that says how to get it."
function png_symbol(name::Symbol)
    found = Libdl.dlsym(handle(), name; throw_error = false)
    found === nothing && error("this core library has no PNG support: build it with `cargo build --release " *
                               "--features jpeg,png`")
    return found
end

"""
    levels_pq16(m, table; white_nits = 203, noise = (:unknown, nothing), samples = 1048576)

The stored levels of a 16-bit PQ PNG of `m` through `table` (`nothing` for grey), with SDR white written at
`white_nits`. Returns `(max_data_step, rounding_to_noise, count)`: the widest run of data values that share one
code, the data criterion's worst ratio of rounding error to the stated uncertainty (`nothing` when the noise is
unknown), and how many levels there are. A file whose ratio is 0.1 or less (1 or less for a tolerance) may be
declared a data record (profile 7.3.1). `noise` is as for [`levels`](@ref), which does the same for the 8-bit
gain-map JPEG.
"""
function levels_pq16(m::Mapping, table::Union{Nothing, Vector{Float32}}; white_nits::Real = REF_WHITE_NITS,
                     noise::Tuple{Symbol, Any} = (:unknown, nothing), samples::Integer = 1 << 20)
    haskey(NOISE_MODELS, noise[1]) || throw(ArgumentError("noise model must be one of $(keys(NOISE_MODELS))"))
    (noise[1] === :unknown) == (noise[2] === nothing) ||
        throw(ArgumentError("a noise parameter is needed for every noise model except :unknown, and only then"))
    step, ratio, count = Ref(0.0), Ref(0.0), Ref(Csize_t(0))
    pointer_, nodes = table === nothing ? (Ptr{Float32}(C_NULL), 0) : (pointer(table), length(table) ÷ 6)
    GC.@preserve table check(ccall(png_symbol(:udeep_levels_pq16), Int32,
                                   (Ref{Mapping}, Ptr{Float32}, Csize_t, Float64, Csize_t, UInt32, Float64,
                                    Ref{Float64}, Ref{Float64}, Ref{Csize_t}),
                                   m, pointer_, nodes, white_nits, samples, NOISE_MODELS[noise[1]],
                                   noise[2] === nothing ? 0.0 : Float64(noise[2]), step, ratio, count))
    return step[], noise[1] === :unknown ? nothing : ratio[], Int(count[])
end

"""
    png_pq(rgb; white_nits = 203, declaration = nothing)

A 16-bit PQ PNG as bytes: `rgb` is `height x width x 3`, linear RGB in multiples of SDR white (the `hdr` of an
[`HdrView`](@ref)), and `1.0` is written at `white_nits`. The file declares BT.2020 primaries and the PQ transfer
function in a `cICP` chunk. With a [`Declaration`](@ref) it carries that as XMP; without one it declares nothing
and does not conform to the profile. A NaN is written as 0.

A file with a declaration is written with white at 203 cd/m² (profile 9.1.1), since it has nowhere to say what
its white was; the core refuses another `white_nits` with one (`ArgumentError`). The declaration must also name
the SDR figure that goes with the file, as `sdr_digest` (profile 9.1.2). Matching a screen's own SDR white is for
the viewer; a file without a declaration may be written at any white, for one's own screen.

This is the form in which a file may be a data record: see [`levels_pq16`](@ref) and [`read_png_pq`](@ref).
"""
function png_pq(rgb::AbstractArray{<:Real, 3}; white_nits::Real = REF_WHITE_NITS,
                declaration::Union{Nothing, Declaration} = nothing)
    size(rgb, 3) == 3 || throw(DimensionMismatch("rgb must be height x width x 3; got $(size(rgb))"))
    values = convert(Vector{Float64}, row_order(rgb))
    packet = declaration === nothing ? UInt8[] : Vector{UInt8}(codeunits(xmp_packet(declaration)))
    data, length_ = Ref(Ptr{UInt8}(C_NULL)), Ref(Csize_t(0))
    check(ccall(png_symbol(:udeep_png_pq), Int32,
                (Ptr{Float64}, Csize_t, Csize_t, Float64, Ptr{UInt8}, Csize_t, Ref{Ptr{UInt8}}, Ref{Csize_t}),
                values, size(rgb, 2), size(rgb, 1), white_nits, packet, length(packet), data, length_))
    bytes = copy(unsafe_wrap(Array, data[], length_[]))
    ccall(symbol(:udeep_bytes_free), Cvoid, (Ptr{UInt8}, Csize_t), data[], length_[])
    return bytes
end

"""
    read_png_pq(file; white_nits = 203)

A 16-bit PQ PNG, given as its bytes, as light: `height x width x 3`, linear RGB in multiples of the white written
at `white_nits`. It is what [`png_pq`](@ref) was given, to within the file's 16-bit steps. For a grey figure any
channel is the light, and [`value`](@ref) turns it back into data. Throws for a PNG of another kind.
"""
function read_png_pq(file::AbstractVector{UInt8}; white_nits::Real = REF_WHITE_NITS)
    bytes = convert(Vector{UInt8}, file)
    width, height = Ref(Csize_t(0)), Ref(Csize_t(0))
    check(ccall(png_symbol(:udeep_png_pq_size), Int32, (Ptr{UInt8}, Csize_t, Ref{Csize_t}, Ref{Csize_t}),
                bytes, length(bytes), width, height))
    values = Vector{Float64}(undef, Int(width[]) * Int(height[]) * 3)
    check(ccall(png_symbol(:udeep_png_pq_read), Int32, (Ptr{UInt8}, Csize_t, Float64, Ptr{Float64}, Csize_t),
                bytes, length(bytes), white_nits, values, length(values)))
    return permutedims(reshape(values, 3, Int(width[]), Int(height[])), (3, 2, 1))     # from row order
end
