# What the worked examples share: random numbers that are the same on every machine and Julia version, and the
# saving of a figure for a light and for a dark page.
using CairoMakie
using UDeepCore

const PX_PER_UNIT = 2

include(joinpath(@__DIR__, "random.jl"))

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
