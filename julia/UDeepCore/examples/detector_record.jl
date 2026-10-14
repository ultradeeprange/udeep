# The depth half: one 12-bit detector frame stored twice, and what each file can honestly call itself.
#
#     julia --project=. detector_record.jl [output directory]
#
# SYNTHETIC data: a 12-bit frame (0 to 4,095 counts) with a read noise of 3 counts.
#
# The same frame and the same mapping are stored as an 8-bit gain-map JPEG and as a 16-bit PQ PNG. For each, the
# widest run of data values that share one stored level is set against the noise (profile 7.2.1: the rounding
# error, a step over the square root of 12, may be at most a tenth of the noise). The JPEG's levels are far too
# coarse, so it is a rendering. The PNG's are fine enough, so it may be declared a data record, and the script
# shows what that means: it reads the PNG back and takes the counts out of it. The two files are one figure: they
# declare the same data and the same SDR figure of record, which the PNG, holding no SDR image of its own, must name
# (profile 9.1.2). It needs no plotting package. It is the counterpart of examples/detector_record.py.
using UDeepCore

include(joinpath(@__DIR__, "random.jl"))

const FULL_SCALE, READ_NOISE, KNEE, HEADROOM = 4095.0, 3.0, 1000.0, 4.0
const NOISE = (:constant, READ_NOISE)

function frame(; size = 256)
    stream = Stream(20261016)
    image = Matrix{Float64}(undef, size, size)
    for j in 1:size, i in 1:size
        x, y = (i - 1) / size, (j - 1) / size
        v = 200.0 + 500.0 * x + 250.0 * sin(6.0 * y)^2                                # a smooth scene below the knee
        v += 3000.0 * exp(-0.5 * ((x - 0.7)^2 + (y - 0.35)^2) / 0.05^2)               # and one bright feature above it
        image[i, j] = clamp(round(v + READ_NOISE * normal!(stream)), 0.0, FULL_SCALE)
    end
    return image
end

function record(directory)
    mkpath(directory)
    data = frame()
    m = Mapping(:linear, 0.0, FULL_SCALE, KNEE, HEADROOM)
    view = hdr_view(data, m; mode = :extend, cmap = "grey")
    declared(step, ratio, kind) = Declaration(; mapping = m, data_unit = "counts", knee_rule = "preset; white at 1,000 counts",
                                              max_data_step = step, noise_model = "constant", noise_parameter = READ_NOISE,
                                              rounding_to_noise = ratio, record = kind, preset = "extend",
                                              colour_map = "grey", data_digest = digest_data(data),
                                              sdr_digest = digest_sdr(view.sdr))
    kind(ratio) = ratio <= 0.1 ? "data-record" : "rendering"

    step8, ratio8 = levels(m, nothing, HEADROOM; noise = NOISE)
    jpeg = declared(step8, ratio8, kind(ratio8))
    write(joinpath(directory, "detector_record.jpg"), gainmap_jpeg(view.sdr, view.hdr, HEADROOM; description = description(jpeg)))

    step16, ratio16, count16 = levels_pq16(m, nothing; noise = NOISE)
    png = declared(step16, ratio16, kind(ratio16))
    path = joinpath(directory, "detector_record_pq16.png")
    write(path, png_pq(view.hdr; declaration = png))

    back = value(m, read_png_pq(read(path))[:, :, 2])                 # a grey figure: any channel is the light
    worst = maximum(abs.(back .- data))
    println("detector_record.jpg: widest step $(round(step8; sigdigits = 3)) counts; rounding is ",
            "$(round(ratio8; sigdigits = 3)) of the noise (limit 0.1); the file declares itself a $(jpeg.record)")
    println("detector_record_pq16.png: $count16 levels; widest step $(round(step16; sigdigits = 3)) counts; rounding is ",
            "$(round(ratio16; sigdigits = 3)) of the noise (limit 0.1); the file declares itself a $(png.record)")
    println("read back from the PNG: every count within $(round(worst; sigdigits = 3)) of the frame; rounded to whole ",
            "counts, ", round.(back) == data ? "the frame itself" : "NOT the frame")
    return (; jpeg, png, levels = count16, recovered = round.(back) == data, worst, path)
end

abspath(PROGRAM_FILE) == @__FILE__() && record(isempty(ARGS) ? "output" : ARGS[1])
