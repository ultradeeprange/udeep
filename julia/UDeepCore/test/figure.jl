# What a figure needs beyond the mapping, against the golden vectors: the mask rule, the stored levels and data
# criterion, the declaration as the file carries it, the caption. Then the file itself, and a figure with Makie.

function declaration(d)
    m = mapping(d)
    text(key) = get(d, key, nothing)
    return Declaration(; mapping = m, data_unit = d["data_unit"], knee_rule = d["knee_rule"],
                       max_data_step = d["max_data_step"], noise_model = d["noise_model"],
                       noise_parameter = get(d, "noise_parameter", nothing),
                       rounding_to_noise = get(d, "rounding_to_noise", nothing), masked = d["masked"],
                       derived_mask = text("derived_mask"), derived_masked = d["derived_masked"], record = d["record"],
                       preset = text("preset"), colour_map = text("colour_map"),
                       colour_map_table = text("colour_map_table"), data_digest = text("data_digest"),
                       data_source = text("data_source"), data_unit_code = text("data_unit_code"),
                       sdr_digest = text("sdr_digest"))
end

@testset "what a figure needs" begin
    @testset "the isolated rule" begin
        case = GOLDEN["cases"]["mask_isolated"]
        grid(rows) = [rows[i][j] == '1' for i in eachindex(rows), j in 1:length(rows[1])]
        above = grid(case["args"]["above"])
        @test isolated(above) == grid(case["out"])
        @test count(isolated(above)) > 0 && isolated(above) != above
        @test isolated(permutedims(above)) == permutedims(grid(case["out"]))            # the rule has no direction
        wide = falses(4, 9); wide[1:3, 5:7] .= true; wide[4, 1] = true                    # not square: rows and columns
        @test isolated(wide) == (wide .& (1:4 .== 4))                                     # must not be confused
    end

    @testset "the sRGB signal and position to value" begin
        codes = (0:255) ./ 255
        @test srgb_encode(srgb_decode(codes)) ≈ codes atol = 1e-14
        @test srgb_decode([0.0, 1.0, 0.04045]) ≈ [0.0, 1.0, 0.04045 / 12.92] rtol = 1e-15
        @test srgb_encode([-1.0, 2.0]) ≈ [0.0, 1.0] atol = 1e-15                        # clipped, as the reference
        @test isnan(srgb_encode([NaN])[1])
        @test size(srgb_encode(zeros(3, 4, 3))) == (3, 4, 3)
        for (_, case) in GOLDEN["cases"]
            case["kind"] == "mapping" || continue
            m = mapping(case["args"]["mapping"])
            t = collect(range(0.0, 1.0; length = 65))
            @test UDeepCore.position(m, value_at(m, t)) ≈ t atol = 1e-9
            @test value_at(m, [0.0, 1.0]) ≈ [m.range_low, m.range_high] .+ m.stretch_offset rtol = 1e-12
        end
    end

    @testset "$name" for (name, case) in sort!([n => c for (n, c) in GOLDEN["cases"] if c["kind"] == "levels"]; by = first)
        a = case["args"]
        a["mapping"]["colour_map_table"] === nothing || continue                         # a carried table: Python only
        m = mapping(a["mapping"])
        cmap = String(split(a["mapping"]["colour_map"], "/")[1])
        noise = (Symbol(a["noise"][1]), a["noise"][2])
        step, ratio = levels(m, colour_table(cmap, m.reference_headroom), m.reference_headroom; noise,
                             samples = a["samples"])
        @test agrees(step, case["out"]["max_data_step"], case)
        want = case["out"]["rounding_to_noise"]
        @test want === nothing ? ratio == UDeepCore.UNBOUNDED : agrees(ratio, min(want, UDeepCore.UNBOUNDED), case)
        @test levels(m, colour_table(cmap, m.reference_headroom), m.reference_headroom; samples = a["samples"]) ==
              (step, nothing)
    end

    @testset "the declaration and the caption" begin
        for name in ("xmp_description", "xmp_description_floats")
            case = GOLDEN["cases"][name]
            @test description(declaration(case["args"]["declaration"])) == case["out"]
        end
        case = GOLDEN["cases"]["caption"]
        d = declaration(case["args"]["declaration"])
        @test caption(d) == case["out"]
        again(; change...) = Declaration(; (n => getfield(d, n) for n in fieldnames(Declaration))..., change...)
        @test description(again()) == description(d)
        for bad in ((; data_unit = ""), (; knee_rule = "because"), (; noise_model = "gaussian"),
                    (; noise_parameter = nothing), (; record = "figure"), (; max_data_step = Inf))
            @test_throws ArgumentError description(again(; bad...))
            @test_throws ArgumentError caption(again(; bad...))
        end
        @test_throws ArgumentError levels(d.mapping, nothing, 4.0; noise = (:constant, nothing))
        @test_throws ArgumentError levels(d.mapping, nothing, 4.0; noise = (:gaussian, 1.0))
        @test_throws ArgumentError levels(d.mapping, nothing, 4.0; noise = (:constant, 0.0))
    end

    @testset "tables and views" begin
        @test colour_table("grey", 3.0) === nothing
        @test length(colour_table("fire", 4.0)) == 2 * 4097 * 3
        @test colour_table("ice", 2.0) !== colour_table("ice", 8.0)
        # the name alone is the current version; an older one stays readable under its own name
        @test colour_table("fire", 4.0) === colour_table("fire/3", 4.0) && colour_table("fire/1", 4.0) != colour_table("fire", 4.0)
        @test colour_table("fire", 4.0)[end - 2:end] == Float32[4, 4, 4]                  # versions 2 and 3 end exactly white
        @test colour_table("fire/1", 4.0)[end - 2:end] != Float32[4, 4, 4]                # version 1 did not
        @test UDeepCore.colour_map_name("ice") == "ice/3" && UDeepCore.colour_map_name("ice/1") == "ice/1"
        @test_throws ArgumentError colour_table("fire/4", 4.0)
        @test_throws ArgumentError colour_table("fire", 3.0)                              # not a registered headroom
        @test_throws ArgumentError colour_table("viridis", 4.0)
        m = Mapping(:linear, 0.0, 100.0, 50.0, 4.0)
        data = [20.0 + 25.0 * ((i * 7 + j * 13) % 10) / 10 for i in 1:40, j in 1:30]     # all below the knee
        data[10:25, 8:20] .= 90.0                                                         # a block above it
        data[35, 3] = 95.0                                                                # and one pixel alone
        mask = falses(size(data)); mask[12, 10] = true
        highlight = hdr_view(data, m; mode = :highlight, cmap = "fire", mask)
        extend = hdr_view(data, m; mode = :extend, cmap = "fire", mask)
        @test size(highlight.sdr) == size(highlight.hdr) == (40, 30, 3)
        @test highlight.masked == extend.masked == 1
        @test highlight.derived_mask == "isolated-3x3" && extend.derived_mask === nothing
        @test extend.derived_masked == 0 && extend.light[35, 3] > 1.0                     # Extend lifts the lone pixel
        @test highlight.light[35, 3] == 1.0 && highlight.derived_masked >= 1              # Highlight holds it at white
        @test highlight.light[12, 10] == 1.0 == extend.light[12, 10]                      # the masked one is never lifted
        @test highlight.light[18, 14] > 1.0
        below = data .< 50.0
        @test highlight.sdr[below, :] == highlight.hdr[below, :] == extend.hdr[below, :]  # the same below the knee
        @test maximum(highlight.sdr) <= 1.0 + 1e-12 < maximum(highlight.hdr) <= 4.0 + 1e-9
        @test highlight.light[.!below .& (highlight.light .> 1)] ≈ light(m, data)[.!below .& (highlight.light .> 1)]
        @test_throws ArgumentError hdr_view(data, m; mode = :both)
        @test_throws DimensionMismatch hdr_view(data, m; mask = falses(3, 3))
    end

    @testset "the file" begin
        m = Mapping(:linear, 0.0, 100.0, 50.0, 4.0)
        data = [100.0 * (j - 1) / 47 for i in 1:32, j in 1:48]                            # 32 rows, 48 columns
        view = hdr_view(data, m; mode = :extend, cmap = "grey")
        d = Declaration(; mapping = m, data_unit = "K", knee_rule = "preset; half of the range",
                        max_data_step = levels(m, nothing, 4.0)[1], colour_map = "grey", preset = "extend",
                        data_digest = digest_data(data))
        file = gainmap_jpeg(view.sdr, view.hdr, 4.0; description = description(d))
        @test file[1:2] == [0xff, 0xd8] && file[end - 1:end] == [0xff, 0xd9]
        text = String(copy(file))
        @test occursin("udeep:DataUnit=\"K\"", text) && occursin(digest_data(data), text)
        @test occursin("urn:iso:std:iso:ts:21496:-1", text) && occursin("hdrgm:GainMapMax", text)
        @test count(==([0xff, 0xd8, 0xff]), [file[i:i + 2] for i in 1:length(file) - 2]) == 2   # the image and its gain map
        bare = gainmap_jpeg(view.sdr, view.hdr, 4.0)
        @test !occursin("udeep:", String(copy(bare))) && length(bare) < length(file)
        @test_throws DimensionMismatch gainmap_jpeg(view.sdr, view.hdr[1:10, :, :], 4.0)
        @test_throws ArgumentError gainmap_jpeg(view.sdr .+ 1.0, view.hdr, 4.0)           # SDR above white
        @test_throws ArgumentError gainmap_jpeg(view.sdr, view.hdr, 1.0)                  # no headroom
    end
end
