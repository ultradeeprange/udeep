# Figures with Makie: what save_hdr lifts and what it leaves, what the file declares, and what it refuses.

using CairoMakie

CairoMakie.activate!(type = "png", px_per_unit = 1)

const EXT = Base.get_extension(UDeepCore, :UDeepCoreMakieExt)
const LINEAR = srgb_decode((0:255) ./ 255)

"A field below the knee, a block above it, one bright pixel alone. 60 along x, 40 along y."
function scene_data()
    data = [20.0 + 25.0 * ((7 * i + 13 * j) % 10) / 10.0 for i in 1:60, j in 1:40]
    data[20:45, 10:30] .= [60.0 + i + j / 2 for i in 1:26, j in 1:21]
    data[5, 5] = 95.0
    return data
end

const DECLARED = (unit = "K", knee_rule = "preset; half of the declared range", noise = (:constant, 2.5))

function scene(; mode = :highlight, cmap = "fire", bar = true, declared = DECLARED, kw...)
    m = Mapping(:linear, 0.0, 100.0, 50.0, 4.0)
    fig = Figure(size = (420, 300))
    ax = Axis(fig[1, 1]; title = "synthetic")
    plot = hdrimage!(ax, scene_data(), m; mode, cmap, declared..., kw...)
    text!(ax, 32.5, 20.0; text = "label", align = (:center, :center), color = :black)
    bar && hdrcolorbar!(fig[1, 2], plot; label = "K")
    return fig, ax, plot, m
end

declared_in(file) = String(copy(file))

@testset "figures with Makie" begin
    @test EXT !== nothing
    directory = mktempdir()

    @testset "what is lifted, and what is left" begin
        fig, ax, plot, m = scene()
        before = copy(Makie.colorbuffer(fig; px_per_unit = 1))
        base, hdr, codes, head, layers = EXT.compose(fig, 1)
        @test copy(Makie.colorbuffer(fig; px_per_unit = 1)) == before                    # the figure is as it was
        @test head == 4.0 && length(layers) == 2                                          # the image and its bar
        @test size(base) == size(hdr) == size(codes) == (300, 420, 3)
        @test base == LINEAR[codes .+ 1]                                                  # the base is the SDR figure
        @test all(hdr .>= base) && maximum(base) <= 1.0 && 3.0 < maximum(hdr) <= 4.0 + 1e-9
        lifted = dropdims(any(hdr .> base; dims = 3); dims = 3)
        @test 0.03 < count(lifted) / length(lifted) < 0.5
        # where: only inside the two image rectangles (the figure's pixel rows run downwards)
        inside(view_) = [minimum(view_)[1] <= column - 0.5 <= maximum(view_)[1] &&
                         minimum(view_)[2] <= 300 - row + 0.5 <= maximum(view_)[2] for row in 1:300, column in 1:420]
        in_image, in_bar = inside(Makie.viewport(ax.scene)[]), inside(Makie.viewport(layers[2].axis.scene)[])
        @test !any(lifted .& .!(in_image .| in_bar))
        @test count(lifted .& in_image) > 1000 && count(lifted .& in_bar) > 100
        # the label is drawn over the block: its pixels, and the soft pixels round them, stay as the SDR figure has them
        fig_plain, = scene()
        delete!(fig_plain.content[1], fig_plain.content[1].scene.plots[end])              # the same figure, no label
        _, hdr_plain, = EXT.compose(fig_plain, 1)
        label = dropdims(any(hdr .!= hdr_plain; dims = 3); dims = 3)
        @test 50 < count(label) < 5000 && all(in_image[label])
        @test hdr[label, :] == base[label, :]                                             # none of them is lifted
        @test count(dropdims(any(hdr_plain .> 1.0; dims = 3); dims = 3)[label]) == count(label)   # though all would be
        # the lone bright pixel is held by Highlight and lifted by Extend
        column = round(Int, minimum(Makie.viewport(ax.scene)[])[1] + 4.5 / 60 * Makie.widths(Makie.viewport(ax.scene)[])[1])
        row = 300 - round(Int, minimum(Makie.viewport(ax.scene)[])[2] + 4.5 / 40 * Makie.widths(Makie.viewport(ax.scene)[])[2])
        @test maximum(hdr[row, column, :]) < 1.0                                          # not above white
        fig_extend, = scene(; mode = :extend)
        base_e, hdr_e, = EXT.compose(fig_extend, 1)
        @test maximum(hdr_e[row, column, :]) > 3.0 > 1.0 >= maximum(base_e[row, column, :])
        @test base_e == base                                                              # one SDR figure for both presets
    end

    @testset "the file and what it declares" begin
        fig, ax, plot, m = scene(; source = "doi:10.0000/example", unit_code = "K")
        path = joinpath(directory, "figure.jpg")
        @test save_hdr(path, fig; px_per_unit = 1) === nothing
        file = read(path)
        @test file[1:2] == [0xff, 0xd8] && occursin("urn:iso:std:iso:ts:21496:-1", declared_in(file))
        d = hdr_declaration(fig)
        @test d.mapping === m && d.data_unit == "K" && d.preset == "highlight" && d.colour_map == "fire/2"
        @test d.derived_mask == "isolated-3x3" && d.derived_masked == 1 && d.masked == 0
        @test d.data_digest == digest_data(scene_data()) && d.data_source == "doi:10.0000/example"
        @test d.record == "rendering" && d.sdr_digest === nothing && d.data_unit_code == "K"
        @test (d.max_data_step, d.rounding_to_noise) == levels(m, colour_table("fire", 4.0), 4.0; noise = (:constant, 2.5))
        codes = EXT.render(fig, 1)
        written = EXT.with_sdr_digest(d, digest_pixels(codes))
        @test occursin(description(written), declared_in(file))                           # the file carries exactly this
        @test hdr_caption(fig) == caption(d) && occursin("50 K", hdr_caption(fig))
        # the core makes the file from the four renders; from the two figures written out, it is the same file
        base_codes, scaled, black, white, head, = EXT.renders(fig, 1)
        base, hdr, = EXT.compose(fig, 1)
        from_renders, lifted = gainmap_from_renders(base_codes, scaled, black, white, head; description = description(written))
        @test from_renders == file == gainmap_jpeg(base, hdr, head; description = description(written))
        @test lifted == count(dropdims(any(hdr .> base; dims = 3); dims = 3)) > 1000
        @test_throws DimensionMismatch gainmap_from_renders(base_codes, scaled[1:10, :, :], black, white, head)
        bare = joinpath(directory, "bare.jpg")
        save_hdr(bare, fig; px_per_unit = 1, metadata = false)
        @test !occursin("udeep:", declared_in(read(bare)))
        small = filesize(bare)
        save_hdr(bare, fig; px_per_unit = 2, metadata = false)                            # twice the pixels each way
        @test filesize(bare) > 1.5 * small && size(EXT.render(fig, 2)) == (600, 840, 3)
        # a data record needs rounding within a tenth of the noise: a rendering that is not says so
        fine, = scene(; declared = (; DECLARED..., noise = (:constant, 0.01)))
        @test_logs (:warn, r"8-bit levels") save_hdr(joinpath(directory, "fine.jpg"), fine; px_per_unit = 1)
        @test_logs save_hdr(joinpath(directory, "coarse.jpg"), fig; px_per_unit = 1)       # and this one does not warn
    end

    @testset "the colour bar" begin
        fig, ax, plot, m = scene()
        bar = fig.content[2]
        ticks, labels = bar.yticks[]
        @test ticks == [0.0, 0.5, 1.0]                                                    # the knee is at 0.5
        @test labels == ["0", "SDR white 50", "100"]                                      # the knee replaces the tick under it
        low = Mapping(:asinh, 0.0, 5000.0, 100.0, 4.0; width = 40.0)                      # its knee is at 0.30
        fig2 = Figure(); ax2 = Axis(fig2[1, 1])
        plot2 = hdrimage!(ax2, 5000.0 .* rand(20, 20), low; cmap = "ice", mode = :extend)
        bar2 = hdrcolorbar!(fig2[1, 2], plot2)
        ticks2, labels2 = bar2.yticks[]
        @test length(ticks2) == 4 && labels2[2] == "SDR white 100"
        @test labels2[1] == "0" && labels2[end] == "5000" && issorted(ticks2)
        @test_throws ArgumentError hdrcolorbar!(fig2[1, 3], image!(ax2, rand(3, 3)))       # not an hdrimage! plot
    end

    @testset "what is refused" begin
        m = Mapping(:linear, 0.0, 100.0, 50.0, 4.0)
        path = joinpath(directory, "refused.jpg")
        message(f) = try f(); "" catch e; e isa ArgumentError ? e.msg : rethrow() end
        @test occursin("no HDR images", message(() -> save_hdr(path, Figure())))
        translucent, = scene(; alpha = 0.5, bar = false)
        @test occursin("translucent or covered", message(() -> save_hdr(path, translucent; px_per_unit = 1)))
        undeclared, = scene(; declared = (; knee_rule = "preset; x"))
        @test occursin("unit", message(() -> save_hdr(path, undeclared; px_per_unit = 1)))
        @test save_hdr(path, undeclared; px_per_unit = 1, metadata = false) === nothing   # it can still be drawn
        two, ax, = scene(; bar = false)
        hdrimage!(Axis(two[1, 2]), scene_data(), Mapping(:linear, 0.0, 100.0, 60.0, 4.0); DECLARED...)
        @test occursin("more than one mapping", message(() -> hdr_declaration(two)))
        heads, = scene(; bar = false)
        hdrimage!(Axis(heads[1, 2]), scene_data(), Mapping(:linear, 0.0, 100.0, 50.0, 2.0); DECLARED...)
        @test occursin("different headrooms", message(() -> save_hdr(path, heads; px_per_unit = 1)))
        @test occursin("registered", message(() -> scene(; cmap = "fire", bar = false) |> first |> f ->
            hdrimage!(Axis(f[2, 1]), scene_data(), Mapping(:linear, 0.0, 100.0, 50.0, 3.0))))
        @test !isfile(path) || !occursin("more than one", declared_in(read(path)))
    end

    @testset "several images, a removed one, a gap in the data" begin
        fig, ax, plot, m = scene(; bar = false)
        second = hdrimage!(Axis(fig[1, 2]), scene_data() .* 0.9, m; DECLARED...)
        d = hdr_declaration(fig)
        @test d.derived_masked == 2 && startswith(d.data_digest, "sha256:")
        @test d.data_digest != digest_data(scene_data()) && d.data_digest ==
              UDeepCore.combined_digest([digest_data(scene_data()), digest_data(scene_data() .* 0.9)])
        delete!(fig.content[2], second)                                                   # take the second image away
        @test length(EXT.layers(fig)) == 1 && hdr_declaration(fig).data_digest == digest_data(scene_data())
        gap = scene_data(); gap[30:32, 15:17] .= NaN
        holed = Figure(size = (420, 300))
        hdrimage!(Axis(holed[1, 1]), gap, m; DECLARED...)
        base, hdr, = EXT.compose(holed, 1)
        @test all(isfinite, base) && all(isfinite, hdr) && minimum(base) == 0.0           # the gap is drawn black
        @test save_hdr(joinpath(directory, "gap.jpg"), holed; px_per_unit = 1) === nothing
    end

    @testset "themes: the furniture changes, the data does not" begin
        function themed(name)
            with_theme(hdr_theme(name)) do
                m = Mapping(:linear, 0.0, 100.0, 50.0, 4.0)
                fig = Figure(size = (420, 300))
                ax = Axis(fig[1, 1]; title = "synthetic", xlabel = "x", ylabel = "y")
                plot = hdrimage!(ax, scene_data(), m; DECLARED...)
                hdrcolorbar!(fig[1, 2], plot; label = "K")
                path = joinpath(directory, "theme_$name.jpg")
                save_hdr(path, fig; px_per_unit = 1)
                base, hdr, codes, = EXT.compose(fig, 1)
                (; base, hdr, d = hdr_declaration(fig), sdr = digest_pixels(codes), file = read(path))
            end
        end
        light, dark = themed(:light), themed(:dark)
        @test size(light.base) == size(dark.base)
        lifted(f) = dropdims(any(f.hdr .> f.base; dims = 3); dims = 3)
        @test count(lifted(light)) > 1000 && lifted(light) == lifted(dark)                # the same pixels are lifted
        @test light.hdr[lifted(light), :] == dark.hdr[lifted(dark), :]                    # to the same light
        same(a, b) = all(isequal(getfield(a, n), getfield(b, n)) for n in fieldnames(Declaration))
        @test same(light.d, dark.d) && light.sdr != dark.sdr                              # one declaration, two SDR figures
        @test occursin(light.sdr, String(copy(light.file))) && occursin(dark.sdr, String(copy(dark.file)))
        @test minimum(light.base[1:6, 1:6, :]) > 0.95 && maximum(dark.base[1:6, 1:6, :]) < 0.02   # the page it sits on
        @test maximum(dark.hdr[1:6, 1:6, :]) < 0.02
        ink_dark = dropdims(minimum(dark.base; dims = 3); dims = 3) .> 0.6
        ink_light = dropdims(maximum(light.base; dims = 3); dims = 3) .< 0.05
        @test count(ink_dark .& .!lifted(dark)) > 300 && count(ink_light .& .!lifted(light)) > 300   # text in each
        @test maximum(dark.hdr[ink_dark .& .!lifted(dark), :]) <= 1.0                     # furniture is never lifted
        custom = with_theme(hdr_theme(:dark; ground = "#202428")) do
            fig = Figure(size = (200, 150)); Axis(fig[1, 1])
            EXT.render(fig, 1)[1, 1, :]
        end
        @test custom == [0x20, 0x24, 0x28]
        @test_throws ArgumentError hdr_theme(:sepia)
    end
end
