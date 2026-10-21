# An HDR figure with Makie, start to finish. Run it as
#
#     julia --project=. figure.jl [output directory]
#
# It writes figure.jpg (the gain-map JPEG), figure.png (the ordinary SDR figure the file's digest refers to) and
# caption.txt; and the same figure at a reference headroom of 3 (figure-h3.jpg, figure-h3.png), for which no table
# is published, so the core makes one and the file carries it (udeep:ColourMapTable). The data is a made-up test pattern, not a measurement: a textured field below the knee, a block of
# structure above it, and a few single bright pixels that the Highlight preset holds at white.
using CairoMakie
using UDeepCore

const PX_PER_UNIT = 2

"The test pattern. Whole-number arithmetic, so that any language can make the same array bit for bit."
function pattern()
    data = [20.0 + 25.0 * ((7 * i + 13 * j) % 10) / 10.0 for i in 1:96, j in 1:72]
    for i in 30:69, j in 20:49
        data[i, j] = 55.0 + (i - 30) * 0.9 + (j - 20) / 4.0
    end
    for (i, j) in ((8, 8), (88, 12), (12, 64), (84, 60))
        data[i, j] = 97.0
    end
    return data
end

function figure(headroom = 4.0)
    data = pattern()
    m = Mapping(:linear, 0.0, 100.0, 50.0, headroom)
    fig = Figure(size = (640, 440))
    ax = Axis(fig[1, 1]; title = "Test pattern (synthetic)", xlabel = "x (pixel)", ylabel = "y (pixel)",
              aspect = DataAspect())
    plot = hdrimage!(ax, data, m; mode = :highlight, cmap = "fire", unit = "K", unit_code = "K",
                     knee_rule = "preset; half of the declared range", noise = (:constant, 2.5),
                     source = "julia/UDeepCore/examples/figure.jl")
    text!(ax, 49.5, 34.5; text = "above white", align = (:center, :center), color = :black)
    hdrcolorbar!(fig[1, 2], plot; label = "temperature (K)")
    return fig
end

function main(directory)
    CairoMakie.activate!(type = "png", px_per_unit = PX_PER_UNIT)
    fig = figure()
    mkpath(directory)
    save(joinpath(directory, "figure.png"), fig; px_per_unit = PX_PER_UNIT)
    save_hdr(joinpath(directory, "figure.jpg"), fig; px_per_unit = PX_PER_UNIT)
    write(joinpath(directory, "caption.txt"), hdr_caption(fig))
    println(hdr_caption(fig))
    other = figure(3.0)
    save(joinpath(directory, "figure-h3.png"), other; px_per_unit = PX_PER_UNIT)
    save_hdr(joinpath(directory, "figure-h3.jpg"), other; px_per_unit = PX_PER_UNIT)
end

main(isempty(ARGS) ? "." : ARGS[1])
