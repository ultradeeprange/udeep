"""
Figures for UDeepCore, with Makie: `hdrimage!`, `hdrcolorbar!`, `save_hdr`, `hdr_declaration`, `hdr_caption`.

A figure is drawn as the ordinary SDR figure. To save it, it is rendered four times with only the HDR images'
colours changed: as it is, with the HDR images scaled into range, with them black, and with them white. A pixel
that is black in the third and white in the fourth is made by an HDR image alone, and only such a pixel is lifted
above white; every other pixel is the ordinary figure's. This is the rule of the Python reference
(`udeep.mpl.savefig_hdr`), and the arithmetic is the Rust core's.
"""
module UDeepCoreMakieExt

using UDeepCore
using UDeepCore: Mapping, Declaration, HdrView
using Makie
using Printf: @sprintf

import UDeepCore: hdrimage!, hdrcolorbar!, save_hdr, hdr_declaration, hdr_caption, hdr_theme

const LIFT_TOLERANCE = 0.01          # relative: a pixel must be >1 % brighter in the HDR render to be lifted
const TICK_GAP = 0.08                # colour-bar ticks closer than this fraction of the bar would collide
# The figure's furniture: (ground, ink, faint ink for grid lines). The same colours as udeep.mpl.THEMES.
const THEMES = (light = ("#ffffff", "#15191d", "#b9c0c7"), dark = ("#111316", "#e4e8ec", "#4a535d"))

function hdr_theme(name::Symbol = :dark; ground = nothing, ink = nothing)
    haskey(THEMES, name) || throw(ArgumentError("theme must be one of $(keys(THEMES)), got :$name"))
    default_ground, default_ink, faint = THEMES[name]
    ground, ink = something(ground, default_ground), something(ink, default_ink)
    return Theme(; backgroundcolor = ground, textcolor = ink, linecolor = ink,
                 Axis = (; backgroundcolor = ground, bottomspinecolor = ink, topspinecolor = ink, leftspinecolor = ink,
                         rightspinecolor = ink, xtickcolor = ink, ytickcolor = ink, xminortickcolor = ink,
                         yminortickcolor = ink, xgridcolor = faint, ygridcolor = faint, xminorgridcolor = faint,
                         yminorgridcolor = faint),
                 Legend = (; backgroundcolor = ground, framecolor = faint))
end

struct Layer
    axis::Any
    plot::Any
    sdr::Array{Float64, 3}           # linear RGB in multiples of SDR white, as the plot holds it: x, y, channel
    hdr::Array{Float64, 3}
    mapping::Mapping
    cmap::String
    declare::Union{Nothing, NamedTuple}      # nothing for a colour bar: it shows the mapping, it is not data
end

# Keyed by the figure's scene: a Figure is an immutable wrapper, and only a mutable object can be a weak key.
const LAYERS = WeakKeyDict{Any, Vector{Layer}}()

registered(fig::Figure) = get!(LAYERS, fig.scene, Layer[])

function figure_of(ax)
    parent = ax.parent
    parent isa Figure || throw(ArgumentError("the axis must belong to a Figure, so that save_hdr can find the image"))
    return parent
end

alive(layer::Layer, fig) = layer.axis in fig.content && layer.plot in layer.axis.scene.plots

"The figure's HDR layers that are still drawn, in drawing order."
function layers(fig::Figure)
    return filter!(layer -> alive(layer, fig), registered(fig))
end

# A NaN has no colour: it is drawn black, in every rendering.
shown(v) = isnan(v) ? 0.0f0 : Float32(v)

"Linear RGB as the sRGB colours Makie draws; with `divide_by`, the HDR image scaled into range first."
function encoded(linear::Array{Float64, 3}, divide_by::Union{Nothing, Float64} = nothing)
    signal = srgb_encode(divide_by === nothing ? linear : linear ./ divide_by)
    return [RGBf(shown(signal[i, j, 1]), shown(signal[i, j, 2]), shown(signal[i, j, 3]))
            for i in axes(signal, 1), j in axes(signal, 2)]
end

function hdrimage!(ax, x, y, data::AbstractMatrix{<:Real}, m::Mapping; mode::Symbol = :highlight,
                   cmap::AbstractString = "fire", mask = nothing, unit = nothing, knee_rule = nothing,
                   noise::Tuple = (:unknown, nothing), source = nothing, unit_code = nothing, kw...)
    fig = figure_of(ax)
    view = hdr_view(data, m; mode, cmap, mask)
    plot = image!(ax, x, y, encoded(view.sdr); interpolate = false, kw...)
    declare = (; unit, unit_code, knee_rule, noise = (Symbol(noise[1]), noise[2]), mode, source,
               masked = view.masked, derived_mask = view.derived_mask, derived_masked = view.derived_masked,
               data_digest = digest_data(data), data_digest_sha3 = digest_data(data; algorithm = "sha3-256"))
    push!(registered(fig), Layer(ax, plot, view.sdr, view.hdr, m, String(cmap), declare))
    return plot
end

hdrimage!(ax, data::AbstractMatrix{<:Real}, m::Mapping; kw...) =
    hdrimage!(ax, 0 .. size(data, 1), 0 .. size(data, 2), data, m; kw...)

function layer_of(plot)
    for (_, ls) in LAYERS, layer in ls
        layer.plot === plot && return layer
    end
    throw(ArgumentError("this plot was not made by hdrimage!"))
end

short(v) = @sprintf("%.4g", v)

function hdrcolorbar!(position, plot; label::AbstractString = "", n::Integer = 256, width::Real = 18)
    layer = layer_of(plot)
    m, head = layer.mapping, layer.mapping.reference_headroom
    table = colour_table(layer.cmap, head)
    x = value_at(m, range(0.0, 1.0; length = n))                         # bottom of the bar = the bottom of the range
    sdr = reshape(colours(table, head, sdr_light(m, x)), 1, n, 3)
    hdr = reshape(colours(table, head, light(m, x)), 1, n, 3)
    knee_t = UDeepCore.position(m, [m.knee])[1]
    marked = 0.02 < knee_t < 0.98
    ticks = [q for q in (0.0, 0.5, 1.0) if !(marked && abs(q - knee_t) < TICK_GAP)]   # none under the marker
    labels = [short(v) for v in value_at(m, ticks)]
    if marked
        push!(ticks, knee_t); push!(labels, "SDR white " * short(m.knee))
    end
    order = sortperm(ticks)
    cax = Axis(position; width, yaxisposition = :right, yticks = (ticks[order], labels[order]), ylabel = label,
               limits = (0, 1, 0, 1), xticksvisible = false, xticklabelsvisible = false, xgridvisible = false,
               ygridvisible = false)
    bar = image!(cax, 0 .. 1, 0 .. 1, encoded(sdr); interpolate = false)
    push!(registered(figure_of(cax)), Layer(cax, bar, sdr, hdr, m, layer.cmap, nothing))
    return cax
end

"The figure as the backend renders it: `height x width x 3` 8-bit sRGB codes, top row first."
function render(fig::Figure, px_per_unit::Real)
    image = Makie.colorbuffer(fig; px_per_unit)
    codes = Array{UInt8}(undef, size(image, 1), size(image, 2), 3)
    code(v) = round(UInt8, 255 * Float64(v))
    for index in CartesianIndices(image)
        colour = image[index]
        codes[index, 1] = code(Makie.Colors.red(colour))
        codes[index, 2] = code(Makie.Colors.green(colour))
        codes[index, 3] = code(Makie.Colors.blue(colour))
    end
    return codes
end

function render_with(fig::Figure, ls, colours_of, px_per_unit::Real)
    for layer in ls
        Makie.update!(layer.plot; arg3 = colours_of(layer))
    end
    return render(fig, px_per_unit)
end

function headroom_of(ls)
    isempty(ls) && throw(ArgumentError("no HDR images in this figure: use hdrimage!"))
    heads = unique!(sort!([layer.mapping.reference_headroom for layer in ls]))
    length(heads) == 1 || throw(ArgumentError("the HDR images in this figure were made at different headrooms " *
                                              "($heads), and a file has only one: save them separately"))
    return only(heads)
end

"""
The four 8-bit renders a save is made from: the figure as it is; with each HDR image scaled into range; and with
the images black, and white. Also the headroom and the layers. The figure is left as it was.
"""
function renders(fig::Figure, px_per_unit::Real)
    ls = layers(fig)
    head = headroom_of(ls)
    base = render(fig, px_per_unit)
    local scaled, black, white
    try
        scaled = render_with(fig, ls, layer -> encoded(layer.hdr, head), px_per_unit)
        black = render_with(fig, ls, layer -> fill(RGBf(0, 0, 0), size(layer.sdr, 1), size(layer.sdr, 2)), px_per_unit)
        white = render_with(fig, ls, layer -> fill(RGBf(1, 1, 1), size(layer.sdr, 1), size(layer.sdr, 2)), px_per_unit)
    finally
        for layer in ls
            Makie.update!(layer.plot; arg3 = encoded(layer.sdr))         # the figure is the SDR one again
        end
    end
    return base, scaled, black, white, head, ls
end

"""
The fair SDR figure and the HDR figure as linear RGB in multiples of SDR white, and the SDR figure's 8-bit codes.
`save_hdr` does not make these two figures (the core goes from the renders to the file): this is the same rule
written out, for looking at what a save will hold and for the tests.
"""
function compose(fig::Figure, px_per_unit::Real)
    base_codes, scaled, black, white, head, ls = renders(fig, px_per_unit)
    linear = srgb_decode((0:255) ./ 255)                                 # 8-bit renders: only 256 answers
    base = Array{Float64}(undef, size(base_codes))
    hdr = similar(base)
    for row in axes(base, 1), column in axes(base, 2)
        image_only = all(black[row, column, c] == 0x00 && white[row, column, c] == 0xff for c in 1:3)
        for c in 1:3
            below = linear[base_codes[row, column, c] + 1]
            lifted = linear[scaled[row, column, c] + 1] * head
            base[row, column, c] = below
            hdr[row, column, c] = image_only && lifted > below * (1.0 + LIFT_TOLERANCE) + 1e-4 ? lifted : below
        end
    end
    return base, hdr, base_codes, head, ls
end

function declaration_of(ls, head::Float64)
    declared = [layer for layer in ls if layer.declare !== nothing]
    isempty(declared) && throw(ArgumentError("no HDR images in this figure: use hdrimage!"))
    first_ = declared[1]
    d = first_.declare
    same(a, b) = all(isequal(getfield(a.declare, k), getfield(b.declare, k))
                     for k in (:unit, :unit_code, :knee_rule, :noise, :mode, :derived_mask)) &&
                 a.mapping === b.mapping && a.cmap == b.cmap
    all(layer -> same(first_, layer), declared) ||
        throw(ArgumentError("this figure has more than one mapping, and a file can declare only one: save the " *
                            "images separately, or pass metadata = false"))
    for name in (:unit, :knee_rule)
        getfield(d, name) === nothing &&
            throw(ArgumentError("hdrimage! needs $name = ... so the file can declare it (or pass metadata = false)"))
    end
    m = first_.mapping
    step, ratio = levels(m, colour_table(first_.cmap, head), head; noise = d.noise)
    sources = unique!([String(layer.declare.source) for layer in declared if layer.declare.source !== nothing])
    return Declaration(; mapping = m, data_unit = String(d.unit), knee_rule = String(d.knee_rule), max_data_step = step,
                       noise_model = String(d.noise[1]),
                       noise_parameter = d.noise[2] === nothing ? nothing : Float64(d.noise[2]),
                       rounding_to_noise = ratio, masked = sum(layer.declare.masked for layer in declared),
                       derived_mask = d.derived_mask,
                       derived_masked = sum(layer.declare.derived_masked for layer in declared),
                       record = "rendering",                     # an 8-bit gain-map JPEG is always a rendering (7.3.3)
                       preset = String(d.mode), colour_map = UDeepCore.colour_map_name(first_.cmap),
                       colour_map_table = UDeepCore.colour_table_text(first_.cmap, head),
                       data_digest = UDeepCore.combined_digest([layer.declare.data_digest for layer in declared]),
                       data_digest_sha3 = UDeepCore.combined_digest([layer.declare.data_digest_sha3 for layer in declared];
                                                                    algorithm = "sha3-256"),
                       data_source = isempty(sources) ? nothing : join(sources, "; "),
                       data_unit_code = d.unit_code === nothing ? nothing : String(d.unit_code))
end

function hdr_declaration(fig::Figure)
    ls = layers(fig)
    return declaration_of(ls, headroom_of(ls))
end

hdr_caption(fig::Figure) = caption(hdr_declaration(fig))

"The declaration with the digests of its SDR figure, from that figure's 8-bit codes: SHA-256 and SHA3-256."
with_sdr_digests(d::Declaration, codes::AbstractArray{UInt8, 3}) =
    Declaration(; (name => getfield(d, name) for name in fieldnames(Declaration))..., sdr_digest = digest_pixels(codes),
                sdr_digest_sha3 = digest_pixels(codes; algorithm = "sha3-256"))

function save_hdr(path::AbstractString, fig::Figure; kw...)
    file, _ = hdr_file(fig; kw...)
    write(path, file)
    return nothing
end

function UDeepCore.hdr_inline(fig::Figure; caption::Bool = true, kw...)
    file, declared = hdr_file(fig; kw...)
    return HdrFigure(file, caption && declared !== nothing ? UDeepCore.caption(declared) : "")
end

"The gain-map JPEG of the figure, as bytes, and the declaration it carries (`nothing` without metadata)."
function hdr_file(fig::Figure; quality::Integer = 95, gain_quality::Integer = 100, px_per_unit::Real = 2,
                  metadata::Bool = true)
    base_codes, scaled, black, white, head, ls = renders(fig, px_per_unit)
    extra, declared = "", nothing
    if metadata
        declared = with_sdr_digests(declaration_of(ls, head), base_codes)
        extra = description(declared)
        limit = declared.noise_model == "tolerance" ? 1.0 : 0.1
        ratio = declared.rounding_to_noise
        if ratio !== nothing && ratio > limit
            @warn "rounding to this file's 8-bit levels is $(@sprintf("%.2g", ratio)) of the stated uncertainty, " *
                  "over the $limit a data record needs (profile 7.2.1). The file is a rendering: keep the data " *
                  "itself alongside it."
        end
    end
    file, lifted = gainmap_from_renders(base_codes, scaled, black, white, head; quality, gain_quality,
                                        description = extra)
    if lifted == 0 && any(layer -> maximum(v -> isnan(v) ? -Inf : v, layer.hdr) > 1.0 + 1e-6, ls)
        # an image with alpha below 1, or wholly under something else: no pixel is the image's alone
        throw(ArgumentError("nothing in this figure can be shown above white: its HDR images are translucent or " *
                            "covered, and a pixel is lifted only where an image alone determines it (profile " *
                            "6.11). Draw the image opaque and uncovered, or save an ordinary figure with `save`"))
    end
    return file, declared
end

end # module
