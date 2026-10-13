# The worked examples (examples/): each one runs, writes its files for both themes, and declares what its text says.

const EXAMPLES = normpath(joinpath(@__DIR__, "..", "examples"))

"An example, included into a module of its own: they all define `build`."
function example(name)
    space = Module(Symbol("example_", name))
    Core.eval(space, :(include(path) = Base.include($space, path)))       # the example includes common.jl
    Base.include(space, joinpath(EXAMPLES, name * ".jl"))
    return space
end

@testset "worked examples" begin
    logger = Base.CoreLogging.NullLogger()                                # each save warns that the file is a rendering
    @testset "$name" for name in ("spectral_map", "microscope_frame", "simulation_field")
        space = example(name)
        folder = mktempdir()
        result = Base.CoreLogging.with_logger(logger) do
            redirect_stdout(devnull) do
                Base.invokelatest(space.write_figure, space.build, name, folder)
            end
        end
        d = result.declaration
        @test d.record == "rendering" && d.mapping.reference_headroom == 4.0 && !isempty(result.caption)
        for suffix in ("", "_dark")
            file = read(joinpath(folder, name * suffix * ".jpg"))
            @test file[1:2] == [0xff, 0xd8] && filesize(joinpath(folder, name * suffix * ".png")) > 10_000
            @test occursin("udeep:DataDigest=\"$(d.data_digest)\"", String(copy(file)))    # one figure, two themes
        end
        if name == "spectral_map"
            knee = Base.invokelatest(space.poisson_threshold, 20.0, 1e-4)
            tail(k) = 1.0 - sum(exp(-20.0) * 20.0^i / factorial(big(i)) for i in 0:k - 1)   # P(X >= k)
            @test tail(knee) <= 1e-4 < tail(knee - 1) && d.mapping.knee == knee && d.noise_model == "poisson"
        elseif name == "microscope_frame"
            data = Base.invokelatest(space.frame)
            @test d.masked == count(>=(4095.0), data) > 50 && all(isinteger, data) && d.colour_map == "grey"
        else
            @test d.mapping.stretch == UDeepCore.STRETCHES.log && d.noise_model == "unknown" && d.mapping.range_low < d.mapping.knee < d.mapping.range_high
        end
    end
    @testset "the stream of random numbers is the same everywhere" begin
        space = example("spectral_map")
        s = Base.invokelatest(space.Stream, UInt64(1))
        @test [Base.invokelatest(space.uniform!, s) for _ in 1:3] ≈ [0.4232091708727133, 0.5094074428837208, 0.6483593939634307] rtol = 1e-15
    end
end
