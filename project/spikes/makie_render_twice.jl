# Feasibility spike (work item 3.1): can CairoMakie render a figure twice with pixel-identical layout,
# so that swapping only the data layer changes only the data layer's pixels?
using CairoMakie
CairoMakie.activate!(type = "png", px_per_unit = 2)

function build()
    fig = Figure(size = (640, 520))
    ax = Axis(fig[1, 1]; title = "spike: data layer swap", xlabel = "x (pixel)", ylabel = "y (pixel)")
    n = 64
    sdr = [RGBf(clamp(i / n, 0, 1), clamp(i / n, 0, 1), clamp(i / n, 0, 1)) for i in 1:n, j in 1:n]
    plt = image!(ax, 0 .. 1, 0 .. 1, sdr; interpolate = false)
    bar = [RGBf(j / 32, j / 32, j / 32) for i in 1:1, j in 1:32]
    cax = Axis(fig[1, 2]; width = 18, yaxisposition = :right, xticksvisible = false, xticklabelsvisible = false)
    bplt = image!(cax, 0 .. 1, 0 .. 1, bar; interpolate = false)
    return fig, plt, bplt, sdr, bar, ax, cax
end

fig, plt, bplt, sdr, bar, ax, cax = build()
a = copy(Makie.colorbuffer(fig))
b = copy(Makie.colorbuffer(fig))
println("eltype = ", eltype(a), "  size = ", size(a))
println("same figure rendered twice identical: ", a == b)

fig2, = build()
c = copy(Makie.colorbuffer(fig2))
println("two separately built figures identical: ", a == c)

# swap only the data layers (as the HDR pass would: the layer scaled into range)
scaled = [RGBf(0.5f0 * p.r, 0.5f0 * p.g, 0.5f0 * p.b) for p in sdr]
Makie.update!(plt, arg3 = scaled)
Makie.update!(bplt, arg3 = [RGBf(0.5f0 * p.r, 0.5f0 * p.g, 0.5f0 * p.b) for p in bar])
d = copy(Makie.colorbuffer(fig))
changed = a .!= d
rows = findall(vec(any(changed, dims = 2))); cols = findall(vec(any(changed, dims = 1)))
println("pixels changed: ", count(changed), " of ", length(changed), "  (", round(100 * count(changed) / length(changed), digits = 1), " %)")
isempty(rows) ? println("NO PIXELS CHANGED") : println("changed bounding box rows ", extrema(rows), " cols ", extrema(cols))
# everything outside the two image rectangles must be untouched: count changed pixels per column band
println("changed columns form ", count(diff(cols) .> 1) + 1, " separate band(s)")

# restore and confirm we get the first render back exactly
Makie.update!(plt, arg3 = sdr); Makie.update!(bplt, arg3 = bar)
e = copy(Makie.colorbuffer(fig))
println("restored render identical to the first: ", a == e)
# where did the pixels change?  They must all lie inside the two image rectangles.
ax_px = Makie.viewport(ax.scene)[]; cax_px = Makie.viewport(cax.scene)[]
println("axis viewport (px, before scale): ", ax_px, "   colour-bar viewport: ", cax_px)
H = size(a, 1); sc = 2
inside(r, c, vp) = (minimum(vp)[1] * sc - 1 <= c <= maximum(vp)[1] * sc + 1) && (H - maximum(vp)[2] * sc - 1 <= r <= H - minimum(vp)[2] * sc + 1)
idx = findall(changed)
outside = count(I -> !(inside(I[1], I[2], ax_px) || inside(I[1], I[2], cax_px)), idx)
println("changed pixels outside both image rectangles: ", outside)
println("Makie ", pkgversion(Makie), "  CairoMakie ", pkgversion(CairoMakie), "  Julia ", VERSION)
