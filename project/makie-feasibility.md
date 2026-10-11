# Makie feasibility (work item 3.1)

Status: done, 2026-10-06. Spike: `project/spikes/makie_render_twice.jl` (output in the `.log` beside it).
Run with Makie 0.24.15, CairoMakie 0.15.15, Julia 1.13.0.

## Question

The Python reference writes an HDR figure by rendering the figure twice: once as the ordinary (fair SDR)
figure, and once with only the data layers scaled into range. It then lifts just the pixels that changed.
Does that work in Makie?

## Result: yes, for CairoMakie raster output

| Check | Result |
|---|---|
| The same figure rendered twice | pixel-identical |
| Two separately built, identical figures | pixel-identical |
| Swap only the data layers (image and colour bar) | 864,960 of 1,331,200 pixels change (65 %), in exactly two column bands |
| Changed pixels outside the two image rectangles | **0** |
| Restore the data and render again | identical to the first render |

So layout, text and axes are stable across renders, and a data-layer swap touches only the data layers.

## Traps found

1. **`Makie.colorbuffer(fig)` returns a reused buffer.** Without `copy(...)`, an earlier "render" is silently
   overwritten by the next one, and every comparison passes trivially. The first two runs of this spike reported
   "identical" for exactly that reason. Always `copy` the buffer before rendering again.
2. **Updating a plot's data.** In Makie 0.24, use `Makie.update!(plot, arg3 = new_image)`. Assigning through
   `plot[3][] = …` did not change the render.
3. **Output is 8-bit** (`ARGB32`). As in Python, the HDR pass therefore quantises the above-white part to
   headroom/255 of SDR white. A float render path would need changes inside the backends.

## Julia packages available (General registry, checked 2026-10-06)

| Need | Package | Note |
|---|---|---|
| PNG, 16-bit | PNGFiles.jl | whether it can write a `cICP` chunk is unchecked; the chunk can be written by hand, as in Python |
| JPEG | JpegTurbo.jl | the MPF and XMP assembly for gain maps would be by hand, as in Python |
| AVIF | libavif_jll (the library), AvifSupport.jl | writing PQ/HLG with the right tags needs checking |
| HEVC / AV1 video | FFMPEG_jll, VideoIO.jl | for HDR video |
| Float images | OpenEXR.jl | for a float master |
| JPEG XL | none found | no archive format in Julia yet |

## Conclusion

Option M1 (a pure-Julia `UDeep.jl` plus a Makie extension that renders twice) is feasible with CairoMakie
and existing packages. Still open:
- vector output (the hybrid SVG);
- GLMakie and WGLMakie;
- a live HDR window.
