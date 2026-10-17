"""udeep.gainmap: Ultra HDR / ISO 21496-1-style gain-map JPEG, checked by round trip.

Spec: developer.android.com/media/platform/hdr-image-format (Ultra HDR v1). The reader implements the
spec's display-time formula, so the round trip tests the maths, not just the file layout.
"""
import math
import re
import struct

import numpy as np
import pytest

from udeep import read_gainmap_jpeg, write_gainmap_jpeg
from udeep.gainmap import ISO_NS, read_iso_metadata


@pytest.fixture(autouse=True)
def the_python_writer_and_reader():
    """This file tests the writer and reader written in Python, down to their internals. When the Rust core is in
    use they are not what runs, so it is turned off here; the core's own are tested in test_udeep_core.py and
    test_udeep_rust.py."""
    from udeep import core
    was = core.enabled()
    core.enable(False)
    yield
    core.enable(was)


def scene(h=48, w=64):
    """SDR plot in [0, 1] and an HDR version equal below white with a lifted block and a gradient."""
    sdr = np.tile(np.linspace(0.05, 1.0, w), (h, 1))[..., None].repeat(3, -1)
    hdr = sdr.copy()
    hdr[10:20, 10:30] = 1.8                              # a feature at 1.8x SDR white
    hdr[30:40, :] = sdr[30:40, :] * np.linspace(1.0, 2.0, w)[None, :, None]
    return sdr.astype(np.float32), hdr.astype(np.float32)


def segments(data: bytes):
    """(marker, payload) for each JPEG segment up to start-of-scan."""
    i, out = 2, []
    while i < len(data) and data[i] == 0xFF and data[i + 1] != 0xDA:
        m, n = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        out.append((m, data[i + 4:i + 2 + n])); i += 2 + n
    return out


def test_file_layout_primary_then_gain_map(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    b = p.read_bytes()
    assert b[:2] == b"\xff\xd8"
    seg = segments(b)
    xmp = next(pl for m, pl in seg if m == 0xE1 and b"Container:Directory" in pl)
    length = int(re.search(rb'Item:Semantic="GainMap"[^>]*?Item:Length="(\d+)"', xmp, re.DOTALL).group(1))
    gain = b[-length:]
    assert gain[:2] == b"\xff\xd8" and b"hdrgm:Version=\"1.0\"" in gain
    mpf = next(pl for m, pl in seg if m == 0xE2 and pl.startswith(b"MPF\x00"))
    assert struct.unpack(">H", mpf[4 + 8:4 + 10])[0] >= 3      # IFD with MPF version, count, entries


def test_gain_map_metadata(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    gain = p.read_bytes()
    attr = lambda k: float(re.search(rb'hdrgm:' + k + rb'="([-0-9.eE+]+)"', gain).group(1))
    assert attr(b"HDRCapacityMax") == pytest.approx(1.0)          # log2(headroom 2)
    assert attr(b"HDRCapacityMin") == pytest.approx(0.0)
    o = 1 / 64                                                     # spec default offsets
    stored = read_gainmap_jpeg(p, display_boost=1.0)               # the SDR image the file stores, as decoded
    expected = np.log2(((hdr + o) / (stored + o)).max())
    assert attr(b"GainMapMax") == pytest.approx(expected, abs=1e-4)
    # exactly 0, in both forms: a pixel that is not lifted has a gain of 1, and JPEG error in the base (which is
    # below 0 somewhere in this scene) no longer sets the bottom of the range
    assert float(np.log2(((hdr + o) / (stored + o)).min())) < -1e-3
    assert gain.count(b'hdrgm:GainMapMin="0"') == 1
    assert read_iso_metadata(p)["gain_map_min"] == 0.0


def test_round_trip_full_headroom_reproduces_hdr(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    out = read_gainmap_jpeg(p, display_boost=2.0)
    assert out.shape == hdr.shape
    lifted = hdr.max(-1) > 1.05
    rel = np.abs(out[lifted] - hdr[lifted]) / hdr[lifted]
    assert float(np.median(rel)) < 0.03                    # JPEG-level agreement where HDR differs
    assert np.abs(out[~lifted] - hdr[~lifted]).max() < 0.06  # unchanged elsewhere


def test_sdr_display_sees_the_sdr_plot(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    base = read_gainmap_jpeg(p, display_boost=1.0)         # weight 0: no gain applied
    assert np.abs(base - sdr).max() < 0.03 and base.max() <= 1.0 + 1e-6


def test_partial_headroom_is_monotonic_between(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    lo, mid, hi = (read_gainmap_jpeg(p, display_boost=b)[15, 20].mean() for b in (1.0, 2 ** 0.5, 2.0))
    assert lo < mid < hi


@pytest.mark.parametrize("stops", [0.25, 0.5, 0.75])
def test_partial_headroom_applies_that_fraction_of_the_log_gain(tmp_path, stops):
    """Ultra HDR: weight = (log2(display boost) - HDRCapacityMin) / (HDRCapacityMax - HDRCapacityMin), and the display
    shows (sdr + offset) * 2 ** (log2 gain * weight) - offset. With headroom 2 (capacity 0..1 stop) a display with
    `stops` of headroom has weight = stops, so what it shows is the weighted geometric mean of the SDR and the HDR
    values, offsets included."""
    sdr, hdr = scene(); p = tmp_path / "g.jpg"; o = 1 / 64
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    block = (slice(12, 18), slice(12, 28))                         # inside the block at 1.8 x white
    shown = read_gainmap_jpeg(p, display_boost=2.0 ** stops)[block].astype(np.float64)
    from_scene = (sdr[block] + o) ** (1.0 - stops) * (1.8 + o) ** stops - o
    assert shown == pytest.approx(from_scene, rel=0.03)           # against the scene itself, to JPEG error
    base, full = (read_gainmap_jpeg(p, display_boost=b)[block].astype(np.float64) for b in (1.0, 2.0))
    assert shown == pytest.approx((base + o) ** (1.0 - stops) * (full + o) ** stops - o, rel=1e-5)   # and exactly


def test_rejects_sdr_outside_unit_range(tmp_path):
    sdr, hdr = scene()
    with pytest.raises(ValueError):
        write_gainmap_jpeg(sdr * 1.5, hdr, tmp_path / "x.jpg", headroom=2.0)


def test_sharp_edges_never_exceed_the_headroom(tmp_path):
    """At sharp gain edges, decoded values stay within the headroom.

    An 8-bit JPEG gain map cannot be exact: its error is exponentiated on display. Before the fixes (gain
    computed against the pre-JPEG base, gain map at quality 95) this scene overshot by 20 %; with the base
    decoded first and the gain map at quality 100 the worst pixel was still ~5 % over. Now the writer decodes its
    own gain map and lowers it wherever the figure would read above its brightest value, so nothing is over.

    Models the top of an HDR colour bar: SDR saturated yellow (little blue) that becomes 4x white in HDR,
    so the blue channel needs ~4 stops of gain, beside a white background (no gain) and a black axis line."""
    h, w = 96, 128
    sdr = np.ones((h, w, 3)); hdr = np.ones((h, w, 3))                      # white figure background
    sdr[:, 60:62] = 0.0; hdr[:, 60:62] = 0.0                                # black axis line
    sdr[10:90, 62:80] = [1.0, 0.85, 0.05]; hdr[10:90, 62:80] = 4.0          # colour-bar top: yellow -> 4x white
    sdr[10:90, 20:58] = [0.9, 0.3, 0.02]; hdr[10:90, 20:58] = [3.5, 1.2, 0.1]  # lifted data beside the line
    p = tmp_path / "edges.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=4.0)
    out = read_gainmap_jpeg(p, display_boost=4.0)
    assert out.max() <= 4.0
    assert np.median(out[20:80, 66:76].max(-1)) > 4.0 * 0.97              # and the bar's top is not simply dimmed


# ---- unlifted pixels are held at a gain of 1, and the writer checks its own gain map before it writes

STRETCH = {"kind": "linear", "lo": 0.0, "hi": 100.0}


def top_block(cmap, headroom, shape=(72, 99), seed=3):
    """A view of noisy data below the knee with a flat block at the very top of the range and a hard edge: the case
    that read up to 9 % over its headroom with the fire and ice maps. The sides are not multiples of 8 and the
    block runs to the right-hand edge, so JPEG blocks that run past the image are in play."""
    from udeep.view import hdr_view
    rng = np.random.default_rng(seed)
    d = 10.0 + 35.0 * rng.random(shape)
    d[20:50, 30:70] = 100.0; d[60:, 80:] = 100.0
    v = hdr_view(d, "extend", 50.0, STRETCH, headroom, cmap)
    return np.asarray(v.sdr, np.float64), np.asarray(v.hdr, np.float64)


@pytest.mark.parametrize("cmap", ["fire", "ice"])
@pytest.mark.parametrize("headroom", [4.0, 8.0])
def test_a_flat_block_at_the_top_of_a_coloured_map_reads_within_the_headroom(tmp_path, cmap, headroom):
    sdr, hdr = top_block(cmap, headroom); p = tmp_path / "top.jpg"
    assert hdr.max() > 0.98 * headroom
    write_gainmap_jpeg(sdr, hdr, p, headroom=headroom)
    out = read_gainmap_jpeg(p, display_boost=headroom).astype(np.float64)
    assert out.max() <= headroom
    assert out.max() <= hdr.max() * (1.0 + 1e-6)                  # the brightest value given, to float32 rounding
    block = out[24:46, 34:66].max(-1) / hdr[24:46, 34:66].max(-1)
    assert np.median(block) > 0.97 and block.min() > 0.90         # held under the top, not thrown away


@pytest.mark.parametrize("cmap", ["fire", "ice", "grey"])
def test_unlifted_pixels_read_the_same_in_the_hdr_and_sdr_renderings(tmp_path, cmap):
    """Away from lifted pixels: in a JPEG block that holds none. (Beside a lifted pixel the gain map's own JPEG
    spreads a little gain into its block.) The base's JPEG error at these noisy pixels used to be stored as gain."""
    sdr, hdr = top_block(cmap, 4.0, shape=(72, 96)); p = tmp_path / "held.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=4.0)
    lifted = np.any(hdr > sdr + 1e-6, axis=-1)
    quiet = ~lifted.reshape(9, 8, 12, 8).any((1, 3))              # the 8 x 8 blocks with no lifted pixel
    assert 20 < quiet.sum() < quiet.size
    quiet = np.repeat(np.repeat(quiet, 8, 0), 8, 1)
    shown, base = (read_gainmap_jpeg(p, display_boost=b) for b in (4.0, 1.0))
    assert np.abs(shown[quiet].astype(np.float64) - base[quiet]).max() < 1e-6
    assert np.abs(shown[~quiet].astype(np.float64) - base[~quiet]).max() > 0.5     # the lifted part is lifted


def test_a_figure_with_nothing_lifted_takes_no_pass_and_never_decodes_its_gain_map(tmp_path, monkeypatch):
    from udeep import gainmap
    rng = np.random.default_rng(0)
    sdr = rng.random((40, 56, 3))                                  # noise to full white: the base's JPEG error is large
    monkeypatch.setattr(gainmap, "_decoded_gain", lambda *a: pytest.fail("the gain map was decoded"))
    data, passes = gainmap._gainmap_file(sdr, sdr, 4.0)
    assert passes == 0
    p = tmp_path / "flat.jpg"; p.write_bytes(data)
    assert np.array_equal(read_gainmap_jpeg(p, 4.0), read_gainmap_jpeg(p, 1.0))


def test_a_pixel_already_at_the_smallest_gain_is_never_counted_as_over():
    """One dim pixel is lifted a little, so the figure's brightest value is an unlifted pixel's. JPEG error in the
    base puts other unlifted pixels above that value; they have no gain to lower, and must not start a pass that
    could never end."""
    from udeep import gainmap
    rng = np.random.default_rng(1)
    sdr = 0.2 + 0.3 * rng.random((40, 56, 3))
    hdr = sdr.copy(); sdr[5, 5] = 0.1; hdr[5, 5] = 0.15
    _data, passes = gainmap._gainmap_file(sdr, hdr, 4.0)
    assert passes == 0


def test_the_pass_count_is_seen_and_the_correction_is_what_holds_the_peak(tmp_path, monkeypatch):
    """The same figure with the correcting passes switched off (a limit no figure reaches, and the check made blind)
    reads over its headroom: the passes are what the result rests on, not the hold alone."""
    from udeep import gainmap
    sdr, hdr = top_block("ice", 8.0)
    data, passes = gainmap._gainmap_file(sdr, hdr, 8.0)
    assert 1 <= passes <= gainmap.GAIN_PASS_LIMIT
    p = tmp_path / "a.jpg"; p.write_bytes(data)
    assert read_gainmap_jpeg(p, 8.0).max() <= 8.0
    monkeypatch.setattr(gainmap, "_decoded_gain", lambda jpeg, shown_max: np.zeros(sdr.shape))
    blind, none = gainmap._gainmap_file(sdr, hdr, 8.0)
    p.write_bytes(blind)
    assert none == 0 and read_gainmap_jpeg(p, 8.0).max() > 8.0 * 1.01


def test_a_gain_map_that_cannot_be_held_is_refused_and_no_file_is_written(tmp_path, monkeypatch):
    from udeep import gainmap
    sdr, hdr = top_block("ice", 8.0); p = tmp_path / "refused.jpg"
    monkeypatch.setattr(gainmap, "GAIN_PASS_LIMIT", 1)             # this figure needs more than one pass
    with pytest.raises(ValueError, match="could not be kept within the headroom.*gain_quality=100"):
        write_gainmap_jpeg(sdr, hdr, p, headroom=8.0)
    assert not p.exists()


def white_beside(top, shape=(40, 51)):
    """A white page with a saturated block (almost no blue) lifted to `top` x white in every channel: the blue gain
    is several stops, hard against pixels with none."""
    sdr = np.ones(shape + (3,)); hdr = np.ones(shape + (3,))
    sdr[11:29, 13:37] = [1.0, 0.5, 0.02]; hdr[11:29, 13:37] = top
    return sdr, hdr


def test_gain_spread_onto_unlifted_pixels_at_a_low_gain_quality_is_refused(tmp_path):
    """At gain quality 50 the gain map's JPEG rings across the block's edge, and white pixels beside it would read
    above the headroom. They have no gain of their own to lower, so no pass can mend it: the file is refused."""
    sdr, hdr = white_beside(2.0); p = tmp_path / "low.jpg"
    with pytest.raises(ValueError, match="does not lift reads above the headroom. Use gain_quality=100"):
        write_gainmap_jpeg(sdr, hdr, p, headroom=2.0, gain_quality=50)
    assert not p.exists()
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)                  # and at the default quality it is written
    assert read_gainmap_jpeg(p, 2.0).max() <= 2.0


def test_unlifted_pixels_in_a_block_being_corrected_are_left_alone():
    """The block is lifted to barely above white, so the white pixels sharing its JPEG blocks read above the
    figure's brightest value however the block is corrected. Whether the passes work on the few blocks or on the
    whole map, those pixels are not counted, and the bytes are the same."""
    from udeep import gainmap
    sdr, hdr = white_beside(1.05)
    part, passes = gainmap._gainmap_file(sdr, hdr, 2.0)
    whole, whole_passes = gainmap._gainmap_file(sdr, hdr, 2.0, whole_image=True)
    assert 0 < passes == whole_passes < gainmap.GAIN_PASS_LIMIT and part == whole


@pytest.mark.parametrize("shape", [(72, 99), (72, 96), (67, 96), (8, 8), (5, 3)])
def test_correcting_only_the_blocks_that_are_over_gives_the_same_bytes_as_coding_the_whole_map(shape):
    """A 4:4:4 baseline JPEG's blocks decode independently, which is what lets the passes work on a few blocks."""
    from udeep import gainmap
    if min(shape) < 60:
        rng = np.random.default_rng(2)
        sdr = rng.random(shape + (3,)) * [1.0, 0.6, 0.05]; hdr = sdr * 4.0
    else:
        sdr, hdr = top_block("fire", 8.0, shape)
    part, passes = gainmap._gainmap_file(sdr, hdr, 8.0)
    whole, whole_passes = gainmap._gainmap_file(sdr, hdr, 8.0, whole_image=True)
    assert passes == whole_passes and (passes > 0 or min(shape) < 60)
    assert part == whole


def test_the_sdr_base_is_the_jpeg_of_the_sdr_figure_alone(tmp_path):
    """Byte for byte what the writer stored before it held or corrected anything: the 8-bit sRGB figure through
    Pillow at the given quality, 4:4:4. Nothing done to the gain map may reach the image an SDR viewer shows."""
    import io

    from PIL import Image

    from udeep.export import srgb_oetf
    sdr, hdr = top_block("fire", 4.0); p = tmp_path / "base.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=4.0, quality=90)
    primary, _gain = split(p)
    buf = io.BytesIO()
    Image.fromarray(np.round(srgb_oetf(sdr) * 255.0).astype(np.uint8), "RGB").save(buf, "JPEG", quality=90, subsampling=0)
    alone = buf.getvalue()
    added = sum(4 + len(pl) for m, pl in segments(primary)[:3])   # the XMP, the ISO version block and the MPF index
    assert [m for m, _pl in segments(primary)[:3]] == [0xE1, 0xE2, 0xE2]
    assert primary[:2] + primary[2 + added:] == alone


# ---- ISO 21496-1 binary metadata (layout as libultrahdr reads and writes it: lib/src/gainmapmetadata.cpp, jpegr.cpp)

def split(path):
    b = path.read_bytes()
    n = int(re.search(rb'Item:Semantic="GainMap"[^>]*?Item:Length="(\d+)"', b, re.DOTALL).group(1))
    return b[:-n], b[-n:]


def test_iso_block_in_the_primary_is_the_version_only(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    primary, _gain = split(p)
    seg = segments(primary)
    kinds = ["xmp" if m == 0xE1 else "iso" if pl.startswith(ISO_NS) else "mpf" if pl.startswith(b"MPF\x00") else "other"
             for m, pl in seg if m in (0xE1, 0xE2)]
    assert kinds == ["xmp", "iso", "mpf"]                          # the order libultrahdr writes
    iso = next(pl for m, pl in seg if m == 0xE2 and pl.startswith(ISO_NS))
    assert ISO_NS == b"urn:iso:std:iso:ts:21496:-1\x00"
    assert iso[len(ISO_NS):] == b"\x00\x00\x00\x00"               # minimum_version 0, writer_version 0


def test_iso_block_in_the_gain_map_matches_the_xmp(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    _primary, gain = split(p)
    iso = next(pl for m, pl in segments(gain) if m == 0xE2 and pl.startswith(ISO_NS))[len(ISO_NS):]
    assert len(iso) == 2 + 2 + 1 + 4 * 4 + 10 * 4                  # versions, flags, two headrooms, one channel
    assert iso[:5] == b"\x00\x00\x00\x00\x40"                    # versions 0; flags: one channel (bit 7 clear), the
                                                                 # gain in the base's colour space (bit 6), as the XMP
    attr = lambda k: float(re.search(rb'hdrgm:' + k + rb'="([-0-9.eE+]+)"', gain).group(1))
    m = read_iso_metadata(p)
    assert m["base_hdr_headroom"] == 0.0 and m["alternate_hdr_headroom"] == pytest.approx(1.0, abs=1e-6)
    assert m["gain_map_min"] == pytest.approx(attr(b"GainMapMin"), abs=1e-6)
    assert m["gain_map_max"] == pytest.approx(attr(b"GainMapMax"), abs=1e-6)
    assert m["gamma"] == 1.0
    assert m["base_offset"] == pytest.approx(1 / 64, abs=1e-9) and m["alternate_offset"] == pytest.approx(1 / 64, abs=1e-9)


def test_mpf_still_points_at_the_gain_map(tmp_path):
    sdr, hdr = scene(); p = tmp_path / "g.jpg"
    write_gainmap_jpeg(sdr, hdr, p, headroom=2.0)
    b = p.read_bytes(); primary, gain = split(p)
    tiff = b.index(b"MPF\x00") + 4                                 # offsets count from the TIFF header
    entries = tiff + 8 + 2 + 3 * 12 + 4
    _a1, size1, off1, _d1, _d2 = struct.unpack(">IIIHH", b[entries:entries + 16])
    _a2, size2, off2, _d3, _d4 = struct.unpack(">IIIHH", b[entries + 16:entries + 32])
    assert (size1, off1) == (len(primary), 0)
    assert size2 == len(gain) and b[tiff + off2:tiff + off2 + 2] == b"\xff\xd8" and tiff + off2 == len(primary)


def test_the_iso_block_changes_no_pixel(tmp_path):
    sdr, hdr = scene(); a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    write_gainmap_jpeg(sdr, hdr, a, headroom=2.0); write_gainmap_jpeg(sdr, hdr, b, headroom=2.0, iso=False)
    assert read_iso_metadata(b) is None
    for boost in (1.0, 1.5, 2.0):
        assert np.array_equal(read_gainmap_jpeg(a, boost), read_gainmap_jpeg(b, boost))


@pytest.mark.parametrize("headroom", [math.inf, math.nan, 1.0, 0.5, -4.0])
def test_a_headroom_that_is_not_a_finite_number_above_one_is_refused(tmp_path, headroom):
    """An infinite headroom once reached the Rust writer, which wrote it into the metadata as an infinity; the
    reference raised OverflowError for it. Both refuse it now, the reference with ValueError."""
    sdr, hdr = scene()[:2]
    with pytest.raises(ValueError, match="headroom"):
        write_gainmap_jpeg(sdr, hdr, tmp_path / "f.jpg", headroom=headroom)
    assert not (tmp_path / "f.jpg").exists()
