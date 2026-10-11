"""matplotlib entry point: udeep.mpl.imshow / colorbar / savefig_hdr.

    import udeep.mpl as hp
    img = hp.imshow(ax, data, mode="extend", knee=240, stretch={"kind": "linear", "lo": -160, "hi": 1200},
                    unit="HU", knee_rule="preset; top of the soft-tissue window", noise=("constant", 10.0))
    hp.colorbar(img, ax=ax, label="HU")
    hp.savefig_hdr(fig, "figure.jpg")                 # one file: the ordinary figure + a gain map
    print(hp.caption(fig))                            # a caption generated from the declared mapping

matplotlib only ever draws the fair SDR plot, so the figure, `savefig` PNG/PDF and the gain-map base are
all the ordinary figure. `savefig_hdr` renders the figure four times: as it is; with each HDR layer scaled
into range; and with the layers all black and then all white, which shows the pixels that an HDR layer
alone makes. Only those pixels are lifted, and only where the scaled render is brighter. Axes, text and
background stay at SDR white, and so does anything drawn over an image, such as a legend, with the image
under it. The HDR render goes through 8-bit Agg, so gain above white is quantised to about headroom/255 of
SDR white.

The headroom is fixed when the image is made (`imshow(headroom=...)`): the mapping, the colours and the
declaration all follow from it, so a file cannot be saved at another.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import io
import warnings
from dataclasses import dataclass

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from mpl_toolkits.axes_grid1 import make_axes_locatable
from PIL import Image

from udeep.export import srgb_oetf
from udeep.gainmap import write_gainmap_jpeg
from udeep.metadata import Declaration, data_steps, description, digest_data, digest_pixels, rounding_to_noise
from udeep.metadata import caption as _caption
from udeep.scale import CHUNK
from udeep.view import hdr_view

_ATTR = "_udeep_layers"


class PrecisionWarning(UserWarning):
    """The file's levels are too coarse for the data's stated uncertainty (profile 7.2.1)."""


LIFT_TOLERANCE = 0.01          # relative: a pixel must be >1 % brighter in the HDR render to be lifted
TICK_GAP = 0.08                # colour-bar ticks closer than this fraction of the bar would collide
THEMES = {                     # the figure's furniture: (ground, ink, faint ink for grid lines)
    "light": ("#ffffff", "#15191d", "#b9c0c7"),
    "dark": ("#111316", "#e4e8ec", "#4a535d"),
}


def theme(name: str = "dark", ground: str | None = None, ink: str | None = None) -> dict:
    """matplotlib settings for a figure's furniture on a light or a dark page: the background, and the text, axes
    and ticks drawn on it. Use it around the code that builds the figure:

        with plt.rc_context(hp.theme("dark")):
            fig, ax = plt.subplots()
            ...
            hp.savefig_hdr(fig, "figure_dark.jpg")

    `ground` and `ink` replace the theme's background and text colours, to match a particular page.

    A theme changes nothing about the data: the image, its colours, the mapping and everything the file declares
    about them are the same in every theme, and so is the data's digest. Only the SDR figure differs, so each
    theme is its own file with its own SDR digest (profile 8.7). Text and lines are never lifted above white in
    either theme."""
    if name not in THEMES:
        raise ValueError(f"theme must be one of {tuple(THEMES)}, got {name!r}")
    default_ground, default_ink, faint = THEMES[name]
    ground, ink = ground or default_ground, ink or default_ink
    return {"figure.facecolor": ground, "figure.edgecolor": ground, "savefig.facecolor": ground,
            "savefig.edgecolor": ground, "axes.facecolor": ground, "axes.edgecolor": ink, "axes.labelcolor": ink,
            "axes.titlecolor": ink, "text.color": ink, "xtick.color": ink, "ytick.color": ink,
            "xtick.labelcolor": ink, "ytick.labelcolor": ink, "grid.color": faint, "legend.facecolor": ground,
            "legend.edgecolor": faint, "legend.labelcolor": ink}


@dataclass
class _Layer:
    image: object                # matplotlib AxesImage
    sdr: np.ndarray              # linear RGB 0..1 (what matplotlib draws, before sRGB encoding)
    hdr: np.ndarray              # linear RGB in multiples of SDR white
    headroom: float
    declare: dict | None = None  # what the image's caller declared about the data; None for colour bars


def _root(fig):
    """The figure a subfigure belongs to (a figure is its own root): layers are kept on the root, where savefig looks."""
    while getattr(fig, "figure", None) is not None and fig.figure is not fig:
        fig = fig.figure
    return fig


def _all_axes(fig) -> list:
    """Every axes the figure draws: its own (subfigures' included) and their inset axes, to any depth."""
    found, waiting = [], list(fig.axes)
    while waiting:
        ax = waiting.pop()
        found.append(ax)
        waiting.extend(ax.child_axes)
    return found


def _in_figure(layer: _Layer, fig) -> bool:
    ax = layer.image.axes
    return ax is not None and any(ax is a for a in _all_axes(fig)) and any(layer.image is im for im in ax.images)


def layers(fig) -> list:
    """The figure's HDR layers whose images it still holds. A layer whose image was removed, or whose axes or
    figure was cleared, is dropped from the figure's list: its data is no longer part of what the file shows."""
    fig = _root(fig)
    kept = getattr(fig, _ATTR, None)
    if kept is None:
        return []
    kept[:] = [layer for layer in kept if _in_figure(layer, fig)]
    return kept


def _register(fig, layer: _Layer) -> None:
    fig = _root(fig)
    if not hasattr(fig, _ATTR):
        setattr(fig, _ATTR, [])
    getattr(fig, _ATTR).append(layer)


def _real(data):
    """The data as the numbers that are mapped: a boolean image is drawn as 0 and 1, so that is what is digested."""
    a = np.asarray(data)
    return a.astype(np.float64) if a.dtype == bool else data


def _encoded(linear, divide_by: float | None = None) -> np.ndarray:
    """An image's linear RGB as the sRGB values matplotlib draws: srgb_oetf(linear), or with `divide_by` the HDR
    image scaled into range, srgb_oetf(clip(linear / divide_by, 0, 1)). Worked through a piece at a time: done whole,
    the steps on the way need several arrays the size of the image. The values are the same."""
    linear = np.asarray(linear)                          # kept as it is stored: float64, or float32 if asked for
    rows, out = linear.reshape(-1, linear.shape[-1]), np.empty(linear.shape, dtype=linear.dtype)
    target = out.reshape(-1, linear.shape[-1])
    for start in range(0, len(rows), CHUNK):
        part = rows[start:start + CHUNK].astype(np.float64)          # the encoding itself is always float64
        target[start:start + CHUNK] = srgb_oetf(part if divide_by is None else np.clip(part / divide_by, 0.0, 1.0))
    return out


def _eotf(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def imshow(ax, data, mode: str, knee, stretch: dict, headroom: float = 2.0, cmap: str = "fire",
           mask=None, unit: str | None = None, knee_rule: str | None = None, noise: tuple = ("unknown", None),
           proportional: bool = False, source: str | None = None, unit_code: str | None = None,
           float32: bool = False, **imshow_kw):
    """Like ax.imshow, but for UDeep's two modes; returns the AxesImage.

    unit, knee_rule and noise are what the file will declare (profile section 8): the data's unit ("1" if it has
    none); why the knee is where it is, as "physical; ...", "statistical; ..." or "preset; ..."; and the noise
    model with its parameter, such as ("constant", 10.0). proportional=True declares a linear stretch from zero
    as proportional (data that is itself light, profile 6.6); its top must then be knee x headroom. source says
    where the data is kept (a DOI, URL or file name); the file also records a digest of `data`, so a reader holding
    the data can check it is the same. unit_code is the unit in machine-readable form (a UCUM code such as "K"),
    beside the text in `unit`. headroom is fixed here: savefig_hdr writes the file at this headroom and no other.
    float32=True keeps the image's two renderings in half the memory (udeep.view): for very large images. The
    colours then differ from the default's by about one part in 10 million, so the default is float64."""
    if proportional and stretch["kind"] != "linear":
        raise ValueError("a proportional mapping is a linear stretch from zero, with no offset")
    v = hdr_view(data, mode, knee=knee, stretch=stretch, headroom=headroom, cmap=cmap, mask=mask,
                 proportional=proportional, float32=float32)
    img = ax.imshow(_encoded(v.sdr), **{"interpolation": "nearest", **imshow_kw})
    img._udeep = {"view": v}
    if knee is None:
        knee_rule = "percentile; " + v.warnings[0].split(":")[0]
    declare = {"unit": unit, "knee_rule": knee_rule, "noise": tuple(noise), "mode": mode, "mapping": v.mapping,
               "masked": v.masked, "derived_mask": v.derived_mask, "derived_masked": v.derived_masked, "data_digest": digest_data(_real(data)), "source": source, "unit_code": unit_code}
    _register(ax.figure, _Layer(img, np.asarray(v.sdr), np.asarray(v.hdr), headroom, declare))
    return img


def colorbar(img, ax, label: str = "", size: str = "4%", pad: float = 0.08, n: int = 256):
    """An HDR colour bar for an udeep image: data-unit ticks and the value rendered at SDR white."""
    v = img._udeep["view"]; m = v.mapping; head = m.reference_headroom
    t = np.linspace(0.0, 1.0, n)[::-1]                          # top of the bar = t 1
    sdr = m.colours(m.light_at(t, 1.0))[:, None, :]
    hdr = m.colours(m.light_at(t))[:, None, :]
    cax = make_axes_locatable(ax).append_axes("right", size=size, pad=pad)
    bar = cax.imshow(srgb_oetf(sdr), aspect="auto", extent=(0, 1, 0, 1), interpolation="nearest")
    _register(ax.figure, _Layer(bar, sdr, hdr, head))
    values = np.asarray(v.colourbar["value"]); ts = np.asarray(v.colourbar["t"])
    ticks = [0.0, 0.5, 1.0]
    if 0.02 < v.knee_t < 0.98:
        ticks = [q for q in ticks if abs(q - v.knee_t) >= TICK_GAP]      # a regular tick must not overprint the marker
    labels = [f"{np.interp(q, ts, values):.4g}" for q in ticks]
    if 0.02 < v.knee_t < 0.98:
        ticks.append(v.knee_t); labels.append(f"SDR white {v.knee_value:.4g}")
    cax.set_xticks([]); cax.yaxis.tick_right(); cax.yaxis.set_label_position("right")
    cax.set_yticks(ticks, labels)
    if label:
        cax.set_ylabel(label)
    return cax


def _crop(fig, pad_inches: float):
    """The box savefig(bbox_inches="tight", pad_inches=...) would crop this figure to, in inches, worked out as
    savefig works it out (matplotlib.backend_bases.FigureCanvasBase.print_figure): the figure is laid out once with
    nothing drawn, and the box round everything in it is padded. Given this box, savefig need not lay the figure out
    again before each render, which was half of every render's work."""
    from matplotlib.backend_bases import _get_renderer

    renderer = _get_renderer(fig)
    with getattr(renderer, "_draw_disabled", contextlib.nullcontext)():
        fig.draw(renderer)
    return fig.get_tightbbox(renderer).padded(pad_inches)


def _render(fig, tight: bool = False, pad_inches: float = 0.1, crop=None) -> np.ndarray:
    """The figure as matplotlib's own savefig renders it (Agg), as an H x W x 3 array of 8-bit sRGB codes.

    With tight, the layout is that of savefig(bbox_inches="tight"). `crop` is the box to crop to if it is already
    known (_crop); the render is then taken as savefig's raw pixels at that box. Its size in pixels is the box's
    size at the figure's resolution, cut to whole pixels; should the raw pixels ever not be that many, or no box be
    given, the render is taken the sure way, as a PNG of the tight figure, which states its own size."""
    if not tight:
        canvas = fig.canvas if hasattr(fig.canvas, "buffer_rgba") else FigureCanvasAgg(fig)
        canvas.draw()
        return np.array(canvas.buffer_rgba(), dtype=np.uint8)[..., :3]         # a copy: the canvas reuses its buffer
    buf = io.BytesIO()
    if crop is not None:
        fig.savefig(buf, format="rgba", bbox_inches=crop, facecolor=fig.get_facecolor())
        raw = np.frombuffer(buf.getbuffer(), dtype=np.uint8)
        height, width = int(crop.height * fig.dpi), int(crop.width * fig.dpi)
        if raw.size == height * width * 4:
            return np.ascontiguousarray(raw.reshape(height, width, 4)[..., :3])
        buf = io.BytesIO()
    # uncompressed: the PNG never leaves memory, and compressing it was two thirds of this function's time
    fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=pad_inches, facecolor=fig.get_facecolor(),
                pil_kwargs={"compress_level": 0})
    return np.asarray(Image.open(buf).convert("RGB"), dtype=np.uint8)


# The renders are 8-bit, so decoding them to linear light has only 256 possible answers: look them up.
_LINEAR = _eotf(np.arange(256, dtype=np.float64) / 255.0)


def _data_digest(ls) -> str:
    """One image: the digest of its data. Several: a digest of their digests, in drawing order."""
    parts = [layer.declare["data_digest"] for layer in ls]
    return parts[0] if len(parts) == 1 else "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _sdr_digest(raw_base) -> str:
    """The digest of the fair SDR figure's pixels, from its 8-bit render."""
    return digest_pixels(raw_base)


def _headroom(fig, headroom: float | None = None) -> float:
    """The one headroom of the figure's HDR layers. It was fixed when the images were made: the mapping, the colours
    and the declared ReferenceHeadroom follow from it, so a different one given at saving time is refused."""
    heads = sorted({float(layer.headroom) for layer in layers(fig)})
    if not heads:
        raise ValueError("no udeep images in this figure: use udeep.mpl.imshow")
    if len(heads) > 1:
        raise ValueError(f"the udeep images in this figure were made at different headrooms ({heads}), and a file "
                         "has only one: give udeep.mpl.imshow the same headroom= for each, or save them separately")
    if headroom is not None and float(headroom) != heads[0]:
        raise ValueError(f"headroom={headroom:g} was asked for, but this figure's images were made at headroom "
                         f"{heads[0]:g}. The headroom is fixed when the image is made: pass headroom={headroom:g} to "
                         "udeep.mpl.imshow, or leave it out here")
    return heads[0]


def declaration(fig, headroom: float | None = None) -> Declaration:
    """What the figure declares about its data and mapping (profile section 8), for a gain-map JPEG.

    A file carries one declaration, so every udeep image in the figure must share one mapping. `headroom` need
    not be given; if it is, it must be the headroom the images were made at."""
    head = _headroom(fig, headroom)
    ls = [layer for layer in layers(fig) if layer.declare is not None]
    if not ls:
        raise ValueError("no udeep images in this figure: use udeep.mpl.imshow")
    first = ls[0]; d = first.declare
    same = ("unit", "unit_code", "knee_rule", "noise", "mode", "mapping", "derived_mask")
    if any(layer.headroom != first.headroom or any(layer.declare[k] != d[k] for k in same) for layer in ls[1:]):
        raise ValueError("this figure has more than one mapping, and a file can declare only one: "
                         "save the images separately, or pass metadata=False")
    for name in ("unit", "knee_rule"):
        if not d[name]:
            raise ValueError(f"udeep.mpl.imshow needs {name}= so the file can declare it (or pass metadata=False)")
    m = d["mapping"]
    steps = data_steps(m, scaled_by=head)
    against_noise = rounding_to_noise(m, d["noise"], steps=steps)
    return Declaration(
        **dataclasses.asdict(m), data_unit=d["unit"], data_unit_code=d["unit_code"], noise_model=d["noise"][0],
        noise_parameter=None if d["noise"][1] is None else float(d["noise"][1]), knee_rule=d["knee_rule"],
        masked=sum(layer.declare["masked"] for layer in ls), derived_mask=d["derived_mask"],
        derived_masked=sum(layer.declare["derived_masked"] for layer in ls), max_data_step=float(steps[1].max()),
        rounding_to_noise=None if against_noise is None else min(against_noise["worst"], 1e9),
        data_digest=_data_digest(ls),
        data_source="; ".join(dict.fromkeys(x.declare["source"] for x in ls if x.declare["source"])) or None,
        record="rendering", preset=d["mode"])                  # an 8-bit gain-map JPEG is always a rendering (7.3.3)

def caption(fig, headroom: float | None = None) -> str:
    """A caption paragraph generated from the figure's declaration (profile 8.2). `headroom` is as for
    `declaration`: it need not be given, and must be the images' own if it is."""
    return _caption(declaration(fig, headroom))


def savefig_hdr(fig, path, headroom: float | None = None, quality: int = 95, tight: bool = True,
                pad_inches: float = 0.1, gain_quality: int = 100, metadata: bool = True) -> None:
    """Write the figure as a gain-map JPEG: the ordinary figure, plus gain on the HDR images only.

    The file carries the profile's declared metadata (unit, mapping, knee and its rule, precision) unless
    metadata=False; without it the file does not conform to the profile.

    The file's headroom is the one the images were made at (udeep.mpl.imshow's headroom=). `headroom` need not
    be given; a value that differs from the images' is refused, because the file would not be what it declares.

    tight=True lays the figure out like savefig(bbox_inches="tight") -- expanding to take in labels
    outside the canvas -- and renders every pass that way, so the gain stays aligned with the base. The SDR
    figure this file's digest refers to is then fig.savefig(..., bbox_inches="tight", pad_inches=pad_inches),
    not a plain fig.savefig(...).

    Before the file is written the gain map is decoded again and lowered wherever the figure would read above its
    brightest value (udeep.gainmap.write_gainmap_jpeg), which takes several coding passes on a coloured figure.
    `gain_quality` below 100 is unreliable with coloured maps (fire, ice), with this writer or any other: where
    one SDR channel is small each gain code is a large step. The passes may then not converge, and ValueError is
    raised and no file written rather than a file that reads over its headroom. Leave it at 100 unless the figure
    is grey."""
    from udeep import core
    head = _headroom(fig, headroom)
    raw_base, raw_scaled, raw_black, raw_white = _renders(fig, head, tight, pad_inches)
    extra = b""
    if metadata:
        declared = dataclasses.replace(declaration(fig, head), sdr_digest=_sdr_digest(raw_base))
        extra = description(declared)
        limit = 1.0 if declared.noise_model == "tolerance" else 0.1
        if declared.rounding_to_noise is not None and declared.rounding_to_noise > limit:
            warnings.warn(f"rounding to this file's 8-bit levels is {declared.rounding_to_noise:.2g} of the stated "
                          f"uncertainty, over the {limit:g} a data record needs (profile 7.2.1). The file is a rendering: "
                          "keep the data itself alongside it.", PrecisionWarning, stacklevel=2)
    if core.files():
        # the Rust core goes from the four renders to the file, without the two float figures in between
        data, lifted = core.write_from_renders(raw_base, raw_scaled, raw_black, raw_white, head, quality, gain_quality,
                                               extra)
        base = hdr = None
    else:
        base, hdr = _combined(raw_base, raw_scaled, raw_black, raw_white, head)
        lifted = int(np.any(hdr > base, axis=-1).sum())
    if lifted == 0 and any(float(np.nanmax(layer.hdr)) > 1.0 + 1e-6 for layer in layers(fig)):
        # e.g. imshow(alpha=0.5), or an image wholly under something else: no pixel is the image's alone
        raise ValueError("nothing in this figure can be shown above white: its HDR images are translucent or covered, "
                         "and a pixel is lifted only where an image alone determines it (profile 6.11). Draw the "
                         "image opaque and uncovered, or save an ordinary figure with fig.savefig")
    if base is None:
        with open(path, "wb") as file:
            file.write(data)
    else:
        write_gainmap_jpeg(base, hdr, path, headroom=head, quality=quality, gain_quality=gain_quality,
                           description=extra)


def _render_with(fig, ls, colours, tight: bool, pad_inches: float, crop=None) -> np.ndarray:
    """The figure rendered with each HDR layer's image replaced by colours(layer), as sRGB values in 0..1."""
    for layer in ls:
        layer.image.set_data(colours(layer))
    return _render(fig, tight, pad_inches, crop)


def _flat(layer: _Layer, value: float) -> np.ndarray:
    """An image of one value to stand in for a layer's own. Drawn without interpolation, one pixel of it covers
    exactly what the whole image covers (the place is set by the image's extent, not by its size), and matplotlib
    has a million fewer pixels to resample. With any other interpolation the edge of an image depends on its size,
    so the stand-in is then as large as the image."""
    small = layer.image.get_interpolation() in ("nearest", "none")
    return np.full((1, 1, 3) if small else layer.sdr.shape, value)


def _renders(fig, head: float, tight: bool, pad_inches: float) -> tuple:
    """The four 8-bit renders a save is made from: the figure as it is; with each HDR layer scaled into range; and
    with the layers all black, and all white. The figure is left as it was."""
    ls = layers(fig)
    shown = [layer.image.get_array() for layer in ls]      # what each image is drawing now: put back as it is
    crop = _crop(fig, pad_inches) if tight else None       # the images' colours do not move the layout: one box
    raw_base = _render(fig, tight, pad_inches, crop)
    if crop is not None and raw_base.shape[:2] != (int(crop.height * fig.dpi), int(crop.width * fig.dpi)):
        crop = None                                        # the box did not give the size expected: the sure way
    try:
        raw_scaled = _render_with(fig, ls, lambda layer: _encoded(layer.hdr, head), tight, pad_inches, crop)
        raw_black = _render_with(fig, ls, lambda layer: _flat(layer, 0.0), tight, pad_inches, crop)
        raw_white = _render_with(fig, ls, lambda layer: _flat(layer, 1.0), tight, pad_inches, crop)
    finally:
        for layer, was in zip(ls, shown):
            layer.image.set_data(was)                      # this marks the figure stale: its next draw is the SDR one
    return raw_base, raw_scaled, raw_black, raw_white


def _combined(raw_base, raw_scaled, raw_black, raw_white, head: float) -> tuple:
    """The fair SDR figure and the HDR figure from the four renders, as linear RGB in multiples of SDR white."""
    base, lifted = _LINEAR[raw_base], _LINEAR[raw_scaled] * head
    image_only = (np.all(raw_black == 0, axis=-1, keepdims=True) & np.all(raw_white == 255, axis=-1, keepdims=True))
    return base, np.where(image_only & (lifted > base * (1.0 + LIFT_TOLERANCE) + 1e-4), lifted, base)


def _compose(fig, head: float, tight: bool, pad_inches: float, with_codes: bool = False):
    """The fair SDR figure and the HDR figure, as linear RGB in multiples of SDR white (and, with_codes, the SDR
    figure's 8-bit render as well).

    Multiplying the scaled render by the headroom gives the HDR figure only where an HDR layer is all there is to
    the pixel: under a legend, a translucent patch or the soft edge of a line it would lift the overlay's own light
    too. So the figure is also rendered with the layers black and then white. A pixel that is exactly black in the
    first and exactly white in the second is made by a layer alone, and only such a pixel is lifted; every other
    pixel is held at the SDR figure."""
    raw_base, raw_scaled, raw_black, raw_white = _renders(fig, head, tight, pad_inches)
    base, hdr = _combined(raw_base, raw_scaled, raw_black, raw_white, head)
    return (base, hdr, raw_base) if with_codes else (base, hdr)


def validate(fig, path, tight: bool = True, pad_inches: float = 0.1, sdr=None):
    """Check a file written by savefig_hdr against the figure: every conformance point of the profile,
    including the pixels (udeep.validate.validate checks what the file alone can show). Pass the same
    `tight` and `pad_inches` the file was saved with. `sdr` is the SDR figure file to check as the figure of
    record; without it the figure's own SDR rendering is used."""
    from udeep.metadata import read_metadata
    from udeep.validate import validate as _validate

    d = read_metadata(path)
    if d is None or not layers(fig):
        return _validate(path)
    expected = _compose(fig, d.reference_headroom, tight, pad_inches, with_codes=True)
    declared = [layer for layer in layers(fig) if layer.declare is not None]
    return _validate(path, expected=expected[:2], sdr=sdr, data_digest=_data_digest(declared) if declared else None,
                     sdr_digest=None if sdr is not None else _sdr_digest(expected[2]))
