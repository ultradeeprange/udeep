# A simulated field that spans several decades. Extend on a logarithmic stretch, with a statistical knee.
#
#     julia --project=. simulation_field.jl [output directory]
#
# SYNTHETIC data: a log-normal field made by adding random waves, most of their power at large scales. It stands
# in for a quantity where most of the volume is quiet and a small part is intense; it is not the output of a
# solver.
#
# The stretch is logarithmic, because the field's structure is in its ratios. The knee is statistical: ten times
# the field's mean. Below it the figure is the ordinary logarithmic plot; the intense events go above white. No
# noise is stated for a simulation, so the noise model is declared unknown, and the file says that it is a
# rendering. It is the Makie counterpart of examples/simulation_field.py.
include(joinpath(@__DIR__, "common.jl"))

mean_of(a) = sum(a) / length(a)

function field(; size = 256, waves = 160)
    stream = Stream(20261015)
    modes = map(1:waves) do _
        kx, ky = rand_wavenumber(stream), rand_wavenumber(stream)
        k = sqrt(kx^2 + ky^2)
        (kx, ky, 2.0 * pi * uniform!(stream), k^-1.7)                   # far more power at large scales
    end
    smooth = [sum(a * cos(2.0 * pi * (kx * i + ky * j) / size + phase) for (kx, ky, phase, a) in modes)
              for i in 1:size, j in 1:size]
    centre = mean_of(smooth)
    spread = sqrt(mean_of((smooth .- centre) .^ 2))
    return exp.(2.0 .* (smooth .- centre) ./ spread)                    # log-normal: a long tail of intense values
end

"A whole wavenumber from -12 to 12, never zero."
function rand_wavenumber(stream)
    k = round(Int, -12.5 + 25.0 * uniform!(stream))
    return k == 0 ? 1 : clamp(k, -12, 12)
end

function build()
    data = field()
    knee = 10.0 * mean_of(data)
    m = Mapping(:log, minimum(data), maximum(data), knee, 4.0)
    share = round(100.0 * count(>(knee), data) / length(data); digits = 1)
    fig = Figure(size = (600, 520))
    ax = Axis(fig[1, 1]; title = "Synthetic field: $share % above ten times the mean",
              xlabel = "x (cell)", ylabel = "y (cell)", aspect = DataAspect())
    plot = hdrimage!(ax, data, m; mode = :extend, cmap = "ice", unit = "1",
                     knee_rule = "statistical; ten times the mean of the field")
    hdrcolorbar!(fig[1, 2], plot; label = "value (arbitrary unit)")
    rowsize!(fig.layout, 1, Aspect(1, 1.0))                             # a square image, and a bar of its height
    resize_to_layout!(fig)
    return fig
end

is_script(@__FILE__) && write_figure(build, "simulation_field", isempty(ARGS) ? "output" : ARGS[1])
