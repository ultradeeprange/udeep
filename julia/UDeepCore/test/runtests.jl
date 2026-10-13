# The Julia binding must reproduce the project's golden vectors (src/udeep/golden.json), each case to the
# tolerance the case itself states, through the Rust core's C interface.

using UDeepCore
using JSON
using Test

const ROOT = normpath(joinpath(@__DIR__, "..", "..", "..", "src", "udeep"))
const GOLDEN = JSON.parsefile(joinpath(ROOT, "golden.json"))

number(v) = v === nothing ? NaN : Float64(v)          # null is NaN
numbers(v) = Float64[number(e) for e in v]

function agrees(got::Real, want, case)
    want === nothing && return isnan(got)
    tol = case["tol"]
    return abs(got - want) <= tol["abs"] + tol["rel"] * abs(want)
end
agrees(got::AbstractArray, want, case) = length(got) == length(want) && all(agrees(g, w, case) for (g, w) in zip(got, want))

function mapping(m)
    exit_slope = parse(Float64, split(m["above_knee"], "exit=")[2])
    return Mapping(Symbol(m["stretch"]), m["range_low"], m["range_high"], m["knee"], m["reference_headroom"];
                   width = m["stretch_width"], offset = m["stretch_offset"], exit_slope)
end

table(file) = collect(reinterpret(Float32, read(joinpath(ROOT, "tables", file))))

# Bit patterns, not values: NaN == NaN is false, so `==` on results that hold a NaN can never pass, and a test
# that falls back to comparing where the NaNs are checks no number at all.
bits(a) = collect(reinterpret(UInt64, vec(a)))

@testset "UDeepCore" begin
    @test GOLDEN["format"] == 2
    @test sizeof(Mapping) == 64
    @test core_version() == v"0.3.0"

    mappings = sort!([name => case for (name, case) in GOLDEN["cases"] if case["kind"] == "mapping"]; by = first)
    @test length(mappings) == 8
    @testset "$name" for (name, case) in mappings
        m, x, want = mapping(case["args"]["mapping"]), numbers(case["args"]["x"]), case["out"]
        y = light(m, x)
        @test agrees(UDeepCore.position(m, x), want["position"], case)
        @test agrees(y, want["light"], case)
        @test agrees(sdr_light(m, x), want["sdr_light"], case)
        @test agrees(value(m, y), want["value_of_light"], case)
        nodes = isempty(case["tables"]) ? nothing : table(case["tables"][1])
        rgb = colours(nodes, m.reference_headroom, y)
        @test size(rgb) == (length(x), 3)
        @test all(agrees(rgb[i, :], want["colours"][i], case) for i in eachindex(x))
        @test any(isnan, y)                                                                # each case has one
        @test bits(light(m, x; threads = 1)) == bits(y) == bits(light(m, x; threads = 4))
    end

    @testset "threads" begin
        # The core gives a thread at least 16384 values, so only a large array is cut up: here 100 000 for each of
        # four threads. The golden cases above, 25 values each, all run on one.
        m = Mapping(:asinh, 0.0, 20.0, 1.2, 4.0; width = 0.1)
        n = 400_000
        x = [-1.0 + 22.0 * ((i * 0.6180339887498949) % 1.0) for i in 1:n]
        x[1:9973:n] .= NaN
        one = light(m, x; threads = 1)
        @test count(isnan, one) == length(1:9973:n)
        @test bits(one) != bits(x) && length(unique(bits(one))) > 100_000                  # the comparison has content
        for threads in (0, 2, 4, 7)
            @test bits(light(m, x; threads)) == bits(one)
        end
        @test bits(light!(zeros(n), m, x; threads = 4)) == bits(one)
        wrong = copy(one); wrong[n - 5] = nextfloat(wrong[n - 5])
        @test bits(wrong) != bits(one)                                                     # one bit in one value shows
        # each quarter mapped alone is that quarter of the whole: the result does not depend on where it was cut
        quarter = n ÷ 4
        @test all(bits(light(m, x[(k * quarter + 1):((k + 1) * quarter)]; threads = 1)) ==
                  bits(one[(k * quarter + 1):((k + 1) * quarter)]) for k in 0:3)
    end

    @testset "digests" begin
        data = GOLDEN["cases"]["digest_data"]
        rows = data["args"]["values"]
        a = [number(rows[i][j]) for i in eachindex(rows), j in eachindex(rows[1])]       # 2 x 3, as the case's shape
        @test collect(size(a)) == data["args"]["shape"]
        @test digest_data(a) == data["out"]
        @test digest_data([1.0 2.0; 3.0 4.0]) != digest_data([1.0 3.0; 2.0 4.0])
        exact = [0.5 -2.25 3.0; 1024.0 0.0 -7.0]                                       # the same in every type below
        @test digest_data(Float32.(exact)) == digest_data(exact) == digest_data(Float16.(exact))
        @test digest_data(Int16[1 2; 3 4]) == digest_data([1.0 2.0; 3.0 4.0]) == digest_data(UInt8[1 2; 3 4])
        @test digest_data(exact) != digest_data(exact .+ 0.25)
        # a value with no dimensions: udeep.metadata.digest_data(numpy.array(1.5)), which is also that of [1.5]
        scalar = "sha256:0931b8449e03fa553804fc425b21f29865b97e5d46ce7cf6961f83ac61783909"
        @test ndims(fill(1.5)) == 0
        @test digest_data(fill(1.5)) == scalar == digest_data([1.5])
        @test digest_data(fill(Float32(1.5))) == scalar
        @test digest_data(fill(1.5, 1, 1)) != scalar                                   # a shape that is stated counts
        @test digest_data(Float64[]) == "sha256:06d633bdb7b9b775e73d8bbb9f92421b83670ba9084512dc4f21042e523ac8f2"
        wide = GOLDEN["cases"]["digest_pixels_2x3"]
        w = wide["args"]["values"]
        @test digest_pixels(UInt8[w[i][j][k] for i in eachindex(w), j in eachindex(w[1]), k in 1:3]) == wide["out"]
        pixels = GOLDEN["cases"]["digest_pixels"]
        v = pixels["args"]["values"]
        rgb = UInt8[v[i][j][k] for i in eachindex(v), j in eachindex(v[1]), k in 1:3]
        @test digest_pixels(rgb) == pixels["out"]
    end

    @testset "shapes and errors" begin
        m = Mapping(:asinh, 0.0, 20.0, 1.2, 4.0; width = 0.1)
        image = rand(40, 60) .* 20
        @test size(light(m, image)) == (40, 60)
        @test size(colours(nothing, 4.0, light(m, image))) == (40, 60, 3)
        @test bits(light(m, image)) == bits(light(m, image; threads = 1))                  # bit-identical
        @test maximum(abs.(value(m, light(m, image)) .- image)) < 1e-9                     # and back
        @test light(m, Float32.(image)) ≈ light(m, image) rtol = 1e-6                      # any real element type
        @test_throws ArgumentError Mapping(:sqrt, 0.0, 1.0, 0.5, 2.0)
        @test_throws ArgumentError Mapping(:linear, 0.0, 1.0, 5.0, 2.0)                    # knee outside the range
        @test_throws ArgumentError Mapping(:log, 0.0, 1.0, 0.5, 2.0)
        # every kind of array the other functions take, light takes too, and gives a plain array of its size
        row = [0.5, 1.2, 15.0]
        for x in (row', transpose(row), view(image, 2:5, 3:9), 1:5, reshape(1:6, 2, 3), Float32.(row)',
                  PermutedDimsArray(image, (2, 1)), fill(3.0))
            y = light(m, x)
            @test y isa Array{Float64} && size(y) == size(x)
            @test bits(y) == bits(light(m, collect(Float64, x)))
            @test size(sdr_light(m, x)) == size(UDeepCore.position(m, x)) == size(value(m, y)) == size(x)
        end
        # what the reference refuses, the core refuses, and the message says why
        refused(f) = try f(); "" catch e; e isa ArgumentError ? e.msg : rethrow() end
        @test occursin("finite", refused(() -> Mapping(:linear, 0.0, Inf, 0.5, 2.0)))
        @test occursin("finite", refused(() -> Mapping(:linear, 0.0, 1.0, NaN, 2.0)))
        @test occursin("finite", refused(() -> Mapping(:linear, 0.0, 1.0, 0.5, Inf)))
        @test occursin("1e-6", refused(() -> Mapping(:linear, 0.0, 1.0, 1e-9, 2.0)))        # a knee at the very bottom
        @test occursin("1e-6", refused(() -> Mapping(:linear, 0.0, 1.0, 1.0 - 1e-9, 2.0)))  # or just short of the top
        @test Mapping(:linear, 0.0, 1.0, 1.0, 2.0).knee == 1.0                          # exactly at the top is allowed
        @test Mapping(:proportional, 0.0, 12.0, 3.0, 4.0).range_high == 12.0
        @test light(Mapping(:proportional, 0.0, 12.0, 3.0, 4.0), [1.5, 3.0, 9.0]) ≈ [0.5, 1.0, 3.0] rtol = 1e-12
        @test occursin("knee x reference_headroom", refused(() -> Mapping(:proportional, 0.0, 10.0, 3.0, 4.0)))
        @test occursin("exit_slope 1", refused(() -> Mapping(:proportional, 0.0, 12.0, 3.0, 4.0; exit_slope = 0.5)))
        @test Set(keys(UDeepCore.ERRORS)) == Set(1:11)                                # every code the core returns
        @test_throws ArgumentError colours(zeros(Float32, 5), 4.0, [0.5])
        @test_throws DimensionMismatch light!(zeros(3), m, image)
    end
end

include("figure.jl")
include("makie.jl")
include("examples.jl")
