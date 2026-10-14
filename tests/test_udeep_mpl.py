"""udeep.mpl: matplotlib-style entry point -- imshow, colorbar, savefig_hdr (gain-map JPEG)."""
import dataclasses

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pytest

from udeep import mpl as hp
from udeep import read_gainmap_jpeg

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}


def data():
    d = np.full((40, 60), 20.0); d[10:20, 10:30] = 95.0; return d


def fig_with_image(**kw):
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    img = hp.imshow(ax, data(), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts",
                    knee_rule="preset; half of full scale", **kw)
    ax.set_title("title")
    return fig, ax, img


def test_imshow_draws_the_fair_sdr_and_registers_the_hdr_layer():
    fig, ax, img = fig_with_image()
    assert img in ax.get_images()
    shown = np.asarray(img.get_array(), float)
    assert shown.max() <= 1.0 + 1e-9                      # matplotlib only ever sees the SDR plot
    assert len(hp.layers(fig)) == 1
    plt.close(fig)


def test_savefig_hdr_base_is_the_ordinary_figure(tmp_path):
    fig, _ax, _img = fig_with_image()
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p, headroom=2.0, tight=False)
    base = read_gainmap_jpeg(p, display_boost=1.0)
    fig.canvas.draw(); ref = np.asarray(fig.canvas.buffer_rgba(), float)[..., :3] / 255.0
    ref = np.where(ref <= 0.04045, ref / 12.92, ((ref + 0.055) / 1.055) ** 2.4)
    assert base.shape == ref.shape
    assert np.median(np.abs(base - ref)) < 0.01
    plt.close(fig)


def test_only_the_image_goes_above_white(tmp_path):
    fig, _ax, img = fig_with_image()
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p, headroom=2.0, tight=False)
    full = read_gainmap_jpeg(p, display_boost=2.0)
    above = full.max(-1) > 1.1
    assert above.any()
    x0, y0, x1, y1 = img.get_window_extent().extents       # display coords, origin bottom-left
    h = full.shape[0]
    ys, xs = np.nonzero(above)
    assert xs.min() >= x0 - 2 and xs.max() <= x1 + 2       # every lifted pixel lies on the image
    assert (h - ys).min() >= y0 - 2 and (h - ys).max() <= y1 + 2
    assert full[:5, :5].max() <= 1.0 + 0.02                 # figure background stays SDR white
    plt.close(fig)


def test_colorbar_is_an_hdr_layer_with_data_ticks_and_white_marker():
    fig, ax, img = fig_with_image()
    cax = hp.colorbar(img, ax=ax, label="value")
    assert len(hp.layers(fig)) == 2
    labels = [t.get_text() for t in cax.get_yticklabels()]
    assert any(lab.startswith("SDR white") for lab in labels)
    assert any("100" in lab for lab in labels)
    plt.close(fig)


def test_tight_crop_keeps_colourbar_labels_and_aligns_gain(tmp_path):
    fig, ax, img = fig_with_image()
    hp.colorbar(img, ax=ax, label="a long colour-bar label")
    loose, tight = tmp_path / "l.jpg", tmp_path / "t.jpg"
    hp.savefig_hdr(fig, loose, headroom=2.0, tight=False)
    hp.savefig_hdr(fig, tight, headroom=2.0, tight=True)
    a, b = read_gainmap_jpeg(loose, 2.0), read_gainmap_jpeg(tight, 2.0)
    assert b.shape != a.shape                                   # the crop happened
    assert (b.max(-1) > 1.1).sum() == pytest.approx((a.max(-1) > 1.1).sum(), rel=0.05)   # same lifted area
    plt.close(fig)


def test_savefig_hdr_without_layers_is_refused(tmp_path):
    fig, _ax = plt.subplots()
    with pytest.raises(ValueError):
        hp.savefig_hdr(fig, tmp_path / "x.jpg", headroom=2.0)
    plt.close(fig)


def test_colorbar_drops_a_regular_tick_that_would_collide_with_the_white_marker():
    # knee at t = 0.52: the regular 0.5 tick would overprint "SDR white ..."
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    img = hp.imshow(ax, data(), mode="extend", knee=52.0, stretch=STRETCH, cmap="grey")
    cax = hp.colorbar(img, ax=ax, label="value")
    ticks = np.asarray(cax.get_yticks())
    labels = [t.get_text() for t in cax.get_yticklabels()]
    assert sum(lab.startswith("SDR white") for lab in labels) == 1
    gaps = np.diff(np.sort(ticks))
    assert gaps.min() >= 0.08                                   # no two ticks closer than 8 % of the bar
    plt.close(fig)


# ---- the faster render path must change nothing (performance scope 3.2 to 3.4)

def _compose_the_slow_way(fig, head, tight, pad):
    """The composition without the savings (default PNG round trip, float decoding per pixel), by the rule that
    only a pixel made by an udeep image alone is lifted: black where the images are black, white where white."""
    import io

    from PIL import Image

    from udeep.export import srgb_oetf

    def render():
        if not tight:
            fig.canvas.draw()
            return np.asarray(fig.canvas.buffer_rgba(), dtype=np.float64)[..., :3] / 255.0
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=pad, facecolor=fig.get_facecolor())
        return np.asarray(Image.open(buf).convert("RGB"), dtype=np.float64) / 255.0

    def render_with(colours):
        for layer in ls:
            layer.image.set_data(colours(layer))
        return render()

    eotf = lambda v: np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    ls = hp.layers(fig)
    raw_base = render()
    raw_scaled = render_with(lambda layer: srgb_oetf(np.clip(layer.hdr / head, 0.0, 1.0)))
    raw_black = render_with(lambda layer: np.zeros_like(layer.sdr))
    raw_white = render_with(lambda layer: np.ones_like(layer.sdr))
    render_with(lambda layer: srgb_oetf(layer.sdr))
    base, lifted = eotf(raw_base), eotf(raw_scaled) * head
    image_only = (np.all(raw_black < 0.5 / 255.0, axis=-1, keepdims=True)
                  & np.all(raw_white > 254.5 / 255.0, axis=-1, keepdims=True))
    hdr = np.where(image_only & (lifted > base * (1.0 + hp.LIFT_TOLERANCE) + 1e-4), lifted, base)
    return np.clip(base, 0.0, 1.0), hdr, np.round(srgb_oetf(base) * 255.0).astype(np.uint8)


@pytest.mark.parametrize("tight", [False, True])
def test_the_fast_composition_is_identical_to_the_slow_one(tight):
    fig, ax, img = fig_with_image()
    hp.colorbar(img, ax=ax, label="counts")
    ax.plot([12, 28], [15, 15], label="trace"); ax.legend(loc="upper left")     # an overlay across the lifted block
    slow = _compose_the_slow_way(fig, 2.0, tight, 0.1)
    assert (slow[1] > 1.1).any()                                                # the comparison is of a lifted figure
    fast = hp._compose(fig, 2.0, tight, 0.1, with_codes=True)
    for a, b in zip(fast, slow, strict=True):
        assert a.shape == b.shape and np.array_equal(a, b)
    plt.close(fig)


def test_the_figure_is_the_ordinary_figure_again_after_saving(tmp_path):
    from PIL import Image
    fig, ax, img = fig_with_image()
    hp.colorbar(img, ax=ax)
    before, after = tmp_path / "before.png", tmp_path / "after.png"
    fig.savefig(before)
    hp.savefig_hdr(fig, tmp_path / "f.jpg", headroom=2.0)
    fig.savefig(after)
    assert np.array_equal(np.asarray(Image.open(before)), np.asarray(Image.open(after)))
    fig.canvas.draw()                                             # and a plain redraw shows the SDR figure, not the HDR pass
    assert np.array_equal(np.asarray(fig.canvas.buffer_rgba())[..., :3], np.asarray(Image.open(before).convert("RGB")))
    plt.close(fig)


# ---- the headroom is fixed when the image is made

def test_a_headroom_other_than_the_images_is_refused(tmp_path):
    fig, _ax, _img = fig_with_image()                             # made at the default headroom, 2
    for call in (lambda: hp.savefig_hdr(fig, tmp_path / "f.jpg", headroom=4),
                 lambda: hp.savefig_hdr(fig, tmp_path / "f.jpg", headroom=1.5),
                 lambda: hp.savefig_hdr(fig, tmp_path / "f.jpg", headroom=4, metadata=False),
                 lambda: hp.declaration(fig, 4), lambda: hp.caption(fig, headroom=4)):
        with pytest.raises(ValueError, match="headroom"):
            call()
    assert not (tmp_path / "f.jpg").exists()
    plt.close(fig)


def test_the_images_own_headroom_may_be_given_and_changes_nothing(tmp_path):
    fig, _ax, _img = fig_with_image(headroom=4.0)
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    hp.savefig_hdr(fig, a); hp.savefig_hdr(fig, b, headroom=4)
    assert a.read_bytes() == b.read_bytes()
    assert hp.caption(fig) == hp.caption(fig, 4.0) and hp.declaration(fig, 4).reference_headroom == 4.0
    assert hp.validate(fig, a).ok
    plt.close(fig)


def test_images_at_different_headrooms_cannot_share_a_file(tmp_path):
    fig, (one, two) = plt.subplots(1, 2, figsize=(6, 3), dpi=80)
    for ax, head in ((one, 2.0), (two, 4.0)):
        hp.imshow(ax, data(), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", headroom=head)
    with pytest.raises(ValueError, match="headroom"):
        hp.savefig_hdr(fig, tmp_path / "f.jpg", metadata=False)
    plt.close(fig)


# ---- an image that has left the figure has left its file

@pytest.mark.parametrize("clear", ["clf", "cla", "remove"])
def test_an_image_no_longer_in_the_figure_is_not_a_layer(tmp_path, clear):
    from udeep.metadata import digest_data, read_metadata
    fig, ax, img = fig_with_image()
    if clear == "clf":
        fig.clf(); ax = fig.add_subplot()
    elif clear == "cla":
        ax.cla()
    else:
        img.remove()
    assert hp.layers(fig) == [] and fig._udeep_layers == []     # and the stored list is pruned, not only filtered
    other = data() + 1.0
    new = hp.imshow(ax, other, mode="extend", knee=8.0, stretch=STRETCH, cmap="grey", unit="counts",
                    knee_rule="preset; another knee")
    assert [layer.image for layer in hp.layers(fig)] == [new]
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p)                # one image, one mapping: nothing to refuse
    assert read_metadata(p).data_digest == digest_data(other)
    plt.close(fig)


def test_a_colour_bar_stays_a_layer_while_it_is_drawn():
    fig, ax, img = fig_with_image()
    hp.colorbar(img, ax=ax)
    assert len(hp.layers(fig)) == 2 and len(hp.layers(fig)) == 2  # asking does not lose a live layer
    plt.close(fig)


# ---- only a pixel made by an udeep image alone is lifted

def fig_with_legend_below_the_knee():
    """A ramp whose left three quarters are below the knee, with matplotlib's default legend (a translucent frame)
    wholly over that part. Returns the figure and a mask of the pixels left of the knee, inside the image."""
    fig, ax = plt.subplots(figsize=(6, 4), dpi=100)
    ramp = np.tile(np.linspace(0.0, 790.0, 80), (60, 1))
    img = hp.imshow(ax, ramp, mode="extend", knee=600.0, stretch={"kind": "linear", "lo": 0.0, "hi": 790.0},
                    headroom=2.0, aspect="auto", unit="counts", knee_rule="preset; three quarters of full scale")
    ax.plot([5, 20], [30, 30], label="trace"); ax.legend(loc="upper left")
    fig.canvas.draw()
    x0, y0, x1, y1 = img.get_window_extent().extents              # display coordinates, origin bottom-left
    h, w = fig.canvas.get_width_height()[::-1]
    below = np.zeros((h, w), bool)
    below[int(h - y1):int(h - y0), int(x0):int(x0 + (x1 - x0) * 0.7)] = True     # the knee is at 0.76 of the width
    return fig, below


def test_a_legend_over_the_image_below_the_knee_is_not_lifted(tmp_path):
    fig, below = fig_with_legend_below_the_knee()
    legend = fig.axes[0].get_legend().get_window_extent()
    base, hdr = hp._compose(fig, 2.0, False, 0.1)
    assert below[int(below.shape[0] - legend.y1) + 2, int(legend.x0) + 2]        # the legend is in the region tested
    assert hdr[below].max() <= 1.0                                # nothing below the knee exceeds SDR white
    assert np.abs(hdr - base)[below].max() <= 0.02                # and it is the SDR plot there, to 8-bit rounding
    assert (hdr[~below] > 1.5).any()                              # while the highlights are still lifted
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p, tight=False)
    assert read_gainmap_jpeg(p, display_boost=2.0)[below].max() <= 1.05          # and so in the file (JPEG error)
    assert hp.validate(fig, p, tight=False).ok
    plt.close(fig)


def test_pixels_under_an_overlay_are_exactly_the_sdr_figure():
    fig, below = fig_with_legend_below_the_knee()
    ax = fig.axes[0]
    ax.axvspan(70, 78, color="white", alpha=0.5)                  # a translucent patch over the highlights themselves
    fig.canvas.draw()
    (x0, y0), (x1, y1) = ax.transData.transform([(71, 50), (77, 10)])
    h = below.shape[0]
    patch = (slice(int(h - y1) + 1, int(h - y0) - 1), slice(int(x0) + 1, int(x1) - 1))
    base, hdr = hp._compose(fig, 2.0, False, 0.1)
    assert np.array_equal(hdr[patch], base[patch]) and hdr[patch].size > 0
    plt.close(fig)


def _compose_as_it_was(fig, head, tight, pad_inches, with_codes=False):
    """The composition before the rule above: whatever is brighter in the scaled render is lifted, overlays too."""
    from udeep.export import srgb_oetf
    ls = hp.layers(fig)
    raw_base = hp._render(fig, tight, pad_inches)
    for layer in ls:
        layer.image.set_data(srgb_oetf(np.clip(layer.hdr / head, 0.0, 1.0)))
    raw_scaled = hp._render(fig, tight, pad_inches)
    for layer in ls:
        layer.image.set_data(srgb_oetf(layer.sdr))
    base, lifted = hp._LINEAR[raw_base], hp._LINEAR[raw_scaled] * head
    changed = np.any(raw_scaled != raw_base, axis=-1, keepdims=True)
    hdr = np.where(changed & (lifted > base * (1.0 + hp.LIFT_TOLERANCE) + 1e-4), lifted, base)
    return (base, hdr, raw_base) if with_codes else (base, hdr)


def test_validate_fails_a_file_whose_legend_was_lifted(tmp_path, monkeypatch):
    fig, below = fig_with_legend_below_the_knee()
    old, new = tmp_path / "old.jpg", tmp_path / "new.jpg"
    # a file made by the composition as it was, with the declaration a save gives it
    from udeep.gainmap import write_gainmap_jpeg
    from udeep.metadata import description
    base, hdr, codes = _compose_as_it_was(fig, 2.0, False, 0.1, with_codes=True)
    declared = dataclasses.replace(hp.declaration(fig, 2.0), sdr_digest=hp._sdr_digest(codes))
    write_gainmap_jpeg(base, hdr, old, headroom=2.0, description=description(declared))
    hp.savefig_hdr(fig, new, tight=False)
    assert (read_gainmap_jpeg(old, display_boost=2.0)[below] > 1.1).sum() > 500  # the file does carry the fault
    report = hp.validate(fig, old, tight=False)
    assert not report.ok and report.status("held pixels") == "fail"
    assert hp.validate(fig, new, tight=False).ok
    plt.close(fig)


# ---- found by the recheck of the review fixes

def _block():
    d = np.full((40, 60), 20.0); d[10:30, 10:50] = 95.0
    return d


def test_an_image_in_inset_axes_is_still_part_of_the_figure(tmp_path):
    """Inset axes are not in fig.axes. A layer there was taken for a removed one, dropped for good, and its data left
    out of the file while the validator still passed it."""
    fig, ax = plt.subplots(figsize=(4, 4), dpi=80)
    ax.plot([0, 1])
    inset = ax.inset_axes([0.2, 0.2, 0.6, 0.6])
    hp.imshow(inset, _block(), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts", knee_rule="preset; x")
    assert len(hp.layers(fig)) == 1 and len(hp.layers(fig)) == 1          # and asking twice does not lose it
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p)
    assert read_gainmap_jpeg(p, 2.0).max() > 1.5
    inset.remove()
    assert hp.layers(fig) == []                                           # a removed inset does take its layer away
    plt.close(fig)


def test_an_image_in_a_subfigure_is_found_from_the_figure(tmp_path):
    fig = plt.figure(figsize=(6, 3), dpi=80)
    left, _right = fig.subfigures(1, 2)
    ax = left.subplots()
    hp.imshow(ax, _block(), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts", knee_rule="preset; x")
    assert len(hp.layers(fig)) == 1
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p)
    assert read_gainmap_jpeg(p, 2.0).max() > 1.5
    plt.close(fig)


def test_a_figure_in_which_nothing_can_be_lifted_is_refused_not_written(tmp_path):
    """A translucent image is nowhere the sole source of a pixel, so nothing is lifted. Writing that with full HDR
    metadata gave a file the validator fails; say so when saving."""
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    hp.imshow(ax, _block(), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts", knee_rule="preset; x",
              alpha=0.5)
    with pytest.raises(ValueError, match="translucent or covered"):
        hp.savefig_hdr(fig, tmp_path / "f.jpg")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)                         # data wholly below the knee is not this case
    hp.imshow(ax, np.full((40, 60), 20.0), mode="extend", knee=50.0, stretch=STRETCH, cmap="grey", unit="counts",
              knee_rule="preset; x")
    hp.savefig_hdr(fig, tmp_path / "g.jpg")
    plt.close(fig)


def test_a_boolean_image_can_be_drawn_and_saved(tmp_path):
    truth = np.zeros((40, 60), bool); truth[10:30, 10:50] = True
    fig, ax = plt.subplots(figsize=(4, 3), dpi=80)
    hp.imshow(ax, truth, mode="extend", knee=0.5, stretch={"kind": "linear", "lo": 0.0, "hi": 1.0}, cmap="grey", unit="1",
              knee_rule="preset; x")
    p = tmp_path / "f.jpg"; hp.savefig_hdr(fig, p)
    from udeep.metadata import digest_data, read_metadata
    assert read_metadata(p).data_digest == digest_data(truth.astype(float))
    plt.close(fig)


# ---- themes: the furniture changes, the data does not

def themed_figure(name, tmp_path):
    rng = np.random.default_rng(8)
    d = 20.0 + 25.0 * rng.random((48, 64)); d[14:34, 20:44] = np.linspace(55.0, 99.0, 24)[None, :]
    with plt.rc_context(hp.theme(name)):
        fig, ax = plt.subplots(figsize=(4.2, 3.2), dpi=100)
        img = hp.imshow(ax, d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire", unit="K",
                        knee_rule="preset; half of the range", noise=("constant", 2.5))
        hp.colorbar(img, ax=ax, label="K")
        ax.set_title("themed"); ax.set_xlabel("x"); ax.set_ylabel("y")
        path = tmp_path / f"{name}.jpg"
        hp.savefig_hdr(fig, path)
        report = hp.validate(fig, path)
        base, hdr = hp._compose(fig, 4.0, True, 0.1)
    plt.close(fig)
    return path, report, base, hdr


def test_a_theme_changes_the_furniture_and_nothing_about_the_data(tmp_path):
    from udeep.metadata import read_metadata
    light = themed_figure("light", tmp_path); dark = themed_figure("dark", tmp_path)
    assert light[1].ok and dark[1].ok, (str(light[1]), str(dark[1]))
    a, b = read_metadata(light[0]), read_metadata(dark[0])
    assert a.sdr_digest != b.sdr_digest                                  # two SDR figures, two files
    assert dataclasses.replace(a, sdr_digest=None) == dataclasses.replace(b, sdr_digest=None)   # one declaration
    (base_l, hdr_l), (base_d, hdr_d) = light[2:], dark[2:]
    assert base_l.shape == base_d.shape
    lifted_l, lifted_d = (hdr_l > base_l).any(-1), (hdr_d > base_d).any(-1)
    assert lifted_l.sum() > 500 and np.array_equal(lifted_l, lifted_d)   # the same pixels are lifted
    assert np.array_equal(hdr_l[lifted_l], hdr_d[lifted_l])              # to the same light
    corner = (slice(0, 6), slice(0, 6))                                  # the page the figure sits on
    assert base_l[corner].min() > 0.95 and base_d[corner].max() < 0.02
    assert hdr_d[corner].max() < 0.02 and hdr_l[corner].max() <= 1.0     # furniture is never lifted, in either
    ink_l = (base_l.max(-1) < 0.05) & ~lifted_l; ink_d = (base_d.min(-1) > 0.6) & ~lifted_d
    assert ink_l.sum() > 300 and ink_d.sum() > 300                       # dark text on light, light text on dark
    assert hdr_d[ink_d].max() <= 1.0


def test_a_theme_takes_a_pages_own_colours_and_refuses_an_unknown_name():
    rc = hp.theme("dark", ground="#202428", ink="#fafafa")
    assert rc["figure.facecolor"] == rc["savefig.facecolor"] == rc["axes.facecolor"] == "#202428"
    assert rc["text.color"] == rc["axes.labelcolor"] == rc["xtick.color"] == "#fafafa"
    assert hp.theme("light")["figure.facecolor"] == "#ffffff"
    with plt.rc_context(hp.theme("light")):                              # every key is one matplotlib knows
        pass
    with pytest.raises(ValueError, match="theme must be one of"):
        hp.theme("sepia")



# ---- the renders a save is made from

def test_a_render_at_the_known_crop_is_savefigs_tight_render_pixel_for_pixel():
    """The figure is laid out once for a save (hp._crop) and each render taken as raw pixels at that box. That must
    be the figure savefig(bbox_inches="tight") writes, or the SDR digest would name another figure."""
    import io

    from PIL import Image
    for size, dpi, pad in (((4.2, 3.2), 100, 0.1), ((5.0, 3.7), 137, 0.25), ((3.3, 4.1), 72, 0.0)):
        rng = np.random.default_rng(8)
        fig, ax = plt.subplots(figsize=size, dpi=dpi)
        img = hp.imshow(ax, 100.0 * rng.random((30, 44)), "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0,
                        "ice", unit="K", knee_rule="preset; half")
        hp.colorbar(img, ax=ax, label="a label outside the canvas")
        ax.set_title("title"); ax.set_xlabel("x label"); ax.set_ylabel("y label")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", pad_inches=pad)
        users = np.asarray(Image.open(buf).convert("RGB"))
        crop = hp._crop(fig, pad)
        assert np.array_equal(hp._render(fig, True, pad, crop), users)
        assert np.array_equal(hp._render(fig, True, pad), users)                 # and the sure way, with no box
        renders = hp._renders(fig, 4.0, True, pad)
        assert all(r.shape == users.shape for r in renders) and np.array_equal(renders[0], users)
        assert np.array_equal(hp._render(fig, True, pad), users)                 # the figure is left as it was
        plt.close(fig)


def test_a_save_lays_the_figure_out_once_and_writes_no_png(monkeypatch, tmp_path):
    """Four renders, one layout: before, every render laid the figure out again and went through a PNG."""
    fig, ax = plt.subplots(figsize=(4.2, 3.2), dpi=100)
    img = hp.imshow(ax, 100.0 * np.random.default_rng(1).random((30, 44)), "extend", 50.0,
                    {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire", unit="K", knee_rule="preset; half")
    hp.colorbar(img, ax=ax)
    formats, layouts = [], []
    real_savefig, real_crop = fig.savefig, hp._crop
    monkeypatch.setattr(fig, "savefig", lambda *a, **k: (formats.append(k.get("format")), real_savefig(*a, **k))[1])
    monkeypatch.setattr(hp, "_crop", lambda *a, **k: (layouts.append(1), real_crop(*a, **k))[1])
    hp.savefig_hdr(fig, tmp_path / "f.jpg")
    assert formats == ["rgba"] * 4 and layouts == [1]
    plt.close(fig)


@pytest.mark.parametrize("kw", [{}, {"origin": "lower", "extent": (0.0, 4.0, 0.0, 300.0), "aspect": "auto"},
                                {"interpolation": "bilinear"}, {"interpolation": "none"}], ids=lambda k: "-".join(k) or "plain")
def test_the_black_and_white_renders_are_those_of_full_size_images(kw):
    """Where an image is drawn without interpolation, the black and the white render use a one-pixel stand-in for
    it. They must be the renders a full-size black or white image gives, or the image-only rule would move."""
    fig, ax = plt.subplots(figsize=(4.3, 3.1), dpi=110)
    img = hp.imshow(ax, 100.0 * np.random.default_rng(2).random((37, 53)), "extend", 50.0,
                    {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire", unit="K", knee_rule="preset; half", **kw)
    hp.colorbar(img, ax=ax); ax.text(0.5, 0.5, "label", transform=ax.transAxes, color="white")
    layers = hp.layers(fig)
    small = img.get_interpolation() in ("nearest", "none")
    assert hp._flat(layers[0], 0.0).shape == ((1, 1, 3) if small else layers[0].sdr.shape)
    _base, _scaled, black, white = hp._renders(fig, 4.0, True, 0.1)
    crop = hp._crop(fig, 0.1)
    for value, got in ((0.0, black), (1.0, white)):
        want = hp._render_with(fig, layers, lambda layer: np.full(layer.sdr.shape, value), True, 0.1, crop)
        assert np.array_equal(got, want)
    assert (black != white).any(-1).mean() > 0.3                          # the images are a good part of the figure
    plt.close(fig)



def test_inline_shows_the_saved_file_with_its_caption_in_a_notebook(tmp_path):
    import base64

    from udeep.metadata import caption, read_metadata
    from udeep.validate import validate
    fig, _ax, _img = fig_with_image()
    hp.savefig_hdr(fig, tmp_path / "saved.jpg")
    shown = hp.inline(fig)
    assert not plt.fignum_exists(fig.number)                    # closed: the notebook does not show it a second time
    assert shown.data == (tmp_path / "saved.jpg").read_bytes()   # the file savefig_hdr writes, byte for byte
    assert shown.caption == caption(read_metadata(tmp_path / "saved.jpg")) != ""
    bundle = shown._repr_mimebundle_()
    assert base64.b64decode(bundle["image/jpeg"]) == shown.data
    assert f'<img src="data:image/jpeg;base64,{bundle["image/jpeg"]}"' in bundle["text/html"]
    assert "<figcaption>" in bundle["text/html"] and "gain-map JPEG" in bundle["text/plain"]
    shown.save(tmp_path / "kept.jpg")
    assert validate(tmp_path / "kept.jpg").ok


def test_inline_can_leave_the_figure_open_and_the_caption_out():
    fig, _ax, _img = fig_with_image()
    shown = hp.inline(fig, caption=False, close=False)
    assert plt.fignum_exists(fig.number) and shown.caption == ""
    assert "<figcaption>" not in shown._repr_mimebundle_()["text/html"]
    plt.close(fig)
