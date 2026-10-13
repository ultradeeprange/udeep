# A fluorescence frame with dim structure and a few saturated beads. Extend, with the saturated pixels held.
#
#     julia --project=. microscope_frame.jl [output directory]
#
# SYNTHETIC data: a 12-bit camera frame. Faint filaments reach about 900 counts; four beads are far brighter, and
# the brightest two saturate the camera at 4,095 counts.
#
# The knee is a preset: white is put at 1,000 counts, just above the brightest filament, so the filaments use the
# whole of the ordinary figure and the beads go above white. A saturated pixel is not a measurement, only a lower
# bound, so those pixels are given as a mask: the figure holds them at white, never lifts them, and the file
# counts them. It is the Makie counterpart of examples/microscope_frame.py.
include(joinpath(@__DIR__, "common.jl"))

const FULL_SCALE, READ_NOISE = 4095.0, 15.0

function frame(; size = 360)
    stream = Stream(20261014)
    filaments = [(uniform!(stream) * 6.28, -0.6 + 1.2 * uniform!(stream), 350.0 + 430.0 * uniform!(stream)) for _ in 1:7]
    beads = ((0.25, 0.3, 2600.0), (0.7, 0.2, 9000.0), (0.6, 0.72, 15000.0), (0.2, 0.8, 1900.0))
    image = Matrix{Float64}(undef, size, size)
    for j in 1:size, i in 1:size
        x, y = (i - 1) / size, (j - 1) / size
        v = 120.0                                                         # camera offset and background
        for (k, (phase, slope, height)) in enumerate(filaments)           # filaments: faint curved lines
            line = 0.15 + 0.11 * (k - 1) + slope * (x - 0.5) + 0.03 * sin(9 * x + phase)
            v += height * exp(-0.5 * ((y - line) / 0.006)^2)
        end
        for (cx, cy, peak) in beads
            v += peak * exp(-0.5 * ((x - cx)^2 + (y - cy)^2) / 0.012^2)
        end
        image[i, j] = clamp(round(v + READ_NOISE * normal!(stream)), 0.0, FULL_SCALE)   # the camera's 12 bits
    end
    return image
end

function build()
    data = frame()
    saturated = data .>= FULL_SCALE
    m = Mapping(:linear, 0.0, FULL_SCALE, 1000.0, 4.0)
    fig = Figure(size = (600, 520))
    ax = Axis(fig[1, 1]; title = "Synthetic frame: $(count(saturated)) saturated pixels held at white",
              xlabel = "x (pixel)", ylabel = "y (pixel)", aspect = DataAspect())
    plot = hdrimage!(ax, data, m; mode = :extend, cmap = "grey", mask = saturated, unit = "counts",
                     knee_rule = "preset; white at 1,000 counts, just above the brightest filament",
                     noise = (:constant, READ_NOISE))
    hdrcolorbar!(fig[1, 2], plot; label = "counts")
    rowsize!(fig.layout, 1, Aspect(1, 1.0))                             # a square image, and a bar of its height
    resize_to_layout!(fig)
    return fig
end

is_script(@__FILE__) && write_figure(build, "microscope_frame", isempty(ARGS) ? "output" : ARGS[1])
