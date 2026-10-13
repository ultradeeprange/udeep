# A spectral line scan with faint lines in counting noise. Highlight: only what noise alone would rarely reach.
#
#     julia --project=. spectral_map.jl [output directory]
#
# SYNTHETIC data: Poisson counts on a flat background of 20 counts a bin, with three lines added, one of them weak.
#
# The knee is statistical. It is the smallest count that noise alone, at a mean of 20, reaches with a probability
# of one in 10,000 or less: the Poisson tail, worked out below and not assumed. A bin brighter than white is
# therefore one that noise would rarely give. Everything below the knee is the ordinary figure. It is the Makie
# counterpart of examples/spectral_map.py: the same construction, with other random numbers.
include(joinpath(@__DIR__, "common.jl"))

const BACKGROUND, FALSE_ALARM = 20.0, 1e-4

"The smallest count k that a Poisson variable of this mean reaches, or passes, with probability <= tail."
function poisson_threshold(mean, tail)
    k, term, below = 0, exp(-mean), 0.0              # below = P(X < k)
    while 1.0 - below > tail
        below += term
        k += 1
        term *= mean / k
    end
    return k
end

function counts(; positions = 300, channels = 400)
    stream = Stream(20261013)
    lines = ((90, 3.0, 60.0, 0.30), (210, 4.0, 25.0, 0.55), (320, 3.0, 9.0, 0.75))     # centre, width, height, where
    data = Matrix{Float64}(undef, channels, positions)                                 # x = channel, y = position
    for p in 1:positions, c in 1:channels
        at = (p - 1) / (positions - 1)
        expected = BACKGROUND + sum(h * exp(-((at - w) / 0.12)^2) * exp(-0.5 * ((c - 1 - m) / s)^2) for (m, s, h, w) in lines)
        data[c, p] = poisson!(stream, expected)
    end
    return data
end

function build()
    data = counts()
    knee = poisson_threshold(BACKGROUND, FALSE_ALARM)
    m = Mapping(:linear, 0.0, 100.0, Float64(knee), 4.0)
    fig = Figure(size = (640, 460))
    ax = Axis(fig[1, 1]; title = "Synthetic line scan: three lines in counting noise", xlabel = "energy channel",
              ylabel = "position (step)")
    plot = hdrimage!(ax, data, m; mode = :highlight, cmap = "fire", unit = "counts", noise = (:poisson, 1.0),
                     knee_rule = "statistical; Poisson noise of mean 20 reaches this with probability 0.0001 or less")
    hdrcolorbar!(fig[1, 2], plot; label = "counts per bin")
    return fig
end

is_script(@__FILE__) && write_figure(build, "spectral_map", isempty(ARGS) ? "output" : ARGS[1])
