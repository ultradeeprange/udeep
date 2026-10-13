# What the worked examples share: random numbers that are the same on every machine and Julia version, and the
# saving of a figure for a light and for a dark page.
using CairoMakie
using UDeepCore

const PX_PER_UNIT = 2

"A small generator of random numbers (a 64-bit linear congruential one), so that an example's SYNTHETIC data does
not depend on the Julia version. It is for making test data, and for nothing that needs good randomness."
mutable struct Stream
    state::UInt64
end

function uniform!(s::Stream)                       # in (0, 1)
    s.state = s.state * 0x5851f42d4c957f2d + 0x14057b7ef767814f
    return (Float64(s.state >> 11) + 0.5) / 9007199254740992.0
end

normal!(s::Stream) = sqrt(-2.0 * log(uniform!(s))) * cos(2.0 * pi * uniform!(s))

"A Poisson count of the given mean (Knuth's method: fine for the means of tens used here)."
function poisson!(s::Stream, mean::Float64)
    limit, k, product = exp(-mean), 0, uniform!(s)
    while product > limit
        k += 1
        product *= uniform!(s)
    end
    return k
end

"Save `build()` as the ordinary figure (`name.png`) and as the UDeep file (`name.jpg`), and the same again for a
dark page (`name_dark`). Returns the light figure's declaration and caption."
function write_figure(build, name::AbstractString, directory::AbstractString)
    CairoMakie.activate!(type = "png", px_per_unit = PX_PER_UNIT)
    mkpath(directory)
    declared, text = nothing, ""
    for (suffix, theme) in (("", hdr_theme(:light)), ("_dark", hdr_theme(:dark)))
        with_theme(theme) do
            fig = build()
            save(joinpath(directory, name * suffix * ".png"), fig; px_per_unit = PX_PER_UNIT)
            save_hdr(joinpath(directory, name * suffix * ".jpg"), fig; px_per_unit = PX_PER_UNIT)
            if isempty(suffix)
                declared, text = hdr_declaration(fig), hdr_caption(fig)
            end
        end
    end
    println(text)
    return (; declaration = declared, caption = text)
end

"True when the file is being run as a script, and not included by a test."
is_script(file) = abspath(PROGRAM_FILE) == file
