"""Gain-map JPEG (Ultra HDR v1 layout, the model ISO 21496-1 standardises) for scientific plots.

The primary image is the ordinary SDR plot; a gain map records how much brighter each pixel is in the
HDR plot. A display applies the gain in proportion to its own headroom (HDRCapacityMin..Max), so one
file shows the SDR plot on SDR screens, part of the gain on a 2x panel, all of it at `headroom` and
beyond -- a static, declared per-display fit.

Spec: developer.android.com/media/platform/hdr-image-format. Deliberate choices for plots: the gain
map is full resolution and 3-channel (the spec recommends 1/4 size, which blurs sharp data into
halos, and a luminance-only map cannot carry the colour change above white); JPEGs are 4:4:4 so thin
lines and text keep their colour. Layout: primary [SOI, APP1 XMP container, APP2 MPF, ...] followed
by the gain-map JPEG [SOI, APP1 XMP hdrgm, ...].
"""
from __future__ import annotations

import io
import re
import struct
from pathlib import Path

import numpy as np
from PIL import Image

from udeep.export import srgb_oetf

OFFSET = 1.0 / 64.0                      # spec default offset_sdr = offset_hdr
XMP_NS = b"http://ns.adobe.com/xap/1.0/\x00"
ISO_NS = b"urn:iso:std:iso:ts:21496:-1\x00"          # APP2 identifier of the ISO 21496-1 binary metadata
ISO_DENOMINATOR = 1_000_000                           # every rational is written as n / 1e6
ISO_BASE_COLOUR_SPACE = 0x40                          # flags bit 6: the gain is applied in the base image's colour
                                                      # space (libultrahdr: kUseBaseColorSpaceMask)
GAIN_SPAN_MIN = 1e-6                     # the least GainMapMax: a figure that lifts nothing still needs a range
GAIN_PASS_LIMIT = 64                     # correcting passes before the gain map is refused
GAIN_MARGIN = 0.5                        # extra lowering on a correcting pass, in gain codes, to start with ...
GAIN_MARGIN_EVERY = 8                    # ... and that much more after every this many passes


def _srgb_eotf(v):
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _jpeg(rgb8: np.ndarray, quality: int) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb8, "RGB").save(buf, "JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def _segment(marker: int, payload: bytes) -> bytes:
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("JPEG segment too large")
    return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload


def _primary_xmp(gain_length: int, extra: bytes = b"") -> bytes:
    xml = ('<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="udeep">'
           '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description rdf:about=""'
           ' xmlns:Container="http://ns.google.com/photos/1.0/container/"'
           ' xmlns:Item="http://ns.google.com/photos/1.0/container/item/"'
           ' xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/" hdrgm:Version="1.0">'
           '<Container:Directory><rdf:Seq>'
           '<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="Primary" Item:Mime="image/jpeg"/></rdf:li>'
           '<rdf:li rdf:parseType="Resource"><Container:Item Item:Semantic="GainMap" Item:Mime="image/jpeg"'
           f' Item:Length="{gain_length}"/></rdf:li>'
           '</rdf:Seq></Container:Directory></rdf:Description>')
    return XMP_NS + xml.encode() + extra + b"</rdf:RDF></x:xmpmeta>"


def _gain_xmp(gmin: float, gmax: float, cap_max: float) -> bytes:
    xml = ('<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="udeep">'
           '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           '<rdf:Description rdf:about="" xmlns:hdrgm="http://ns.adobe.com/hdr-gain-map/1.0/"'
           ' hdrgm:Version="1.0"'
           f' hdrgm:GainMapMin="{gmin:.8g}" hdrgm:GainMapMax="{gmax:.8g}" hdrgm:Gamma="1"'
           f' hdrgm:OffsetSDR="{OFFSET:.8g}" hdrgm:OffsetHDR="{OFFSET:.8g}"'
           f' hdrgm:HDRCapacityMin="0" hdrgm:HDRCapacityMax="{cap_max:.8g}"'
           ' hdrgm:BaseRenditionIsHDR="False"/></rdf:RDF></x:xmpmeta>')
    return XMP_NS + xml.encode()


def _iso_metadata(gmin: float, gmax: float, cap_max: float) -> bytes:
    """ISO 21496-1 gain-map metadata, in the byte layout libultrahdr reads (lib/src/gainmapmetadata.cpp):
    minimum_version and writer_version (u16, both 0); flags (u8: bit 7 = per-channel values, bit 6 = gain applied
    in the base image's colour space; here bit 6 alone: the same values for all channels, and the gain is in the
    base's colour space, which is what the Ultra HDR XMP beside it means too); base and alternate HDR headroom as
    u32 numerator / u32 denominator; then per channel gain-map min, max, gamma, base offset and alternate offset
    as (s32 or u32 numerator, u32 denominator). Headrooms, min and max are log2; all big-endian."""
    n = lambda v: round(v * ISO_DENOMINATOR)
    d = ISO_DENOMINATOR
    return (struct.pack(">HHB", 0, 0, ISO_BASE_COLOUR_SPACE)
            + struct.pack(">IIII", 0, d, n(cap_max), d)
            + struct.pack(">iIiIIIiIiI", n(gmin), d, n(gmax), d, d, d, n(OFFSET), d, n(OFFSET), d))


def _mpf(primary_len: int, gain_len: int, gain_offset: int) -> bytes:
    """CIPA DC-007 Multi-Picture Format index: two images, offsets from the TIFF header."""
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8)
    entries_at = 8 + 2 + 3 * 12 + 4
    ifd = struct.pack(">H", 3)
    ifd += struct.pack(">HHI4s", 0xB000, 7, 4, b"0100")               # MPFVersion
    ifd += struct.pack(">HHII", 0xB001, 4, 1, 2)                       # NumberOfImages
    ifd += struct.pack(">HHII", 0xB002, 7, 32, entries_at)             # MPEntry
    ifd += struct.pack(">I", 0)                                        # no next IFD
    entries = struct.pack(">IIIHH", 0x030000, primary_len, 0, 0, 0)    # baseline MP primary image
    entries += struct.pack(">IIIHH", 0x000000, gain_len, gain_offset, 0, 0)
    return b"MPF\x00" + tiff + ifd + entries


def _gain_codes(lg: np.ndarray, gmax: float) -> np.ndarray:
    """log2 gains in 0..gmax as the 8-bit codes the gain map stores, rounded to nearest."""
    return np.floor(np.clip(lg / gmax, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _decoded_gain(jpeg: bytes, shown_max: float) -> np.ndarray:
    """The log2 gains a reader takes from a gain-map JPEG whose minimum is 0 and whose stated maximum is
    `shown_max`."""
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB"), dtype=np.float64) / 255.0 * shown_max


def _mosaic(blocks: np.ndarray) -> tuple[np.ndarray, int]:
    """n x 8 x 8 x 3 blocks laid side by side as one image, and how many there are in each of its rows."""
    n = len(blocks); across = int(np.ceil(np.sqrt(n))); down = -(-n // across)
    full = np.zeros((down * across, 8, 8, 3), blocks.dtype); full[:n] = blocks
    return full.reshape(down, across, 8, 8, 3).swapaxes(1, 2).reshape(down * 8, across * 8, 3), across


def _unmosaic(image: np.ndarray, across: int, n: int) -> np.ndarray:
    down = image.shape[0] // 8
    return image.reshape(down, 8, across, 8, 3).swapaxes(1, 2).reshape(down * across, 8, 8, 3)[:n]


def _gain_within_peak(lg: np.ndarray, sdr_q: np.ndarray, gmax: float, hmax: float, headroom: float,
                      gain_quality: int, whole_image: bool = False) -> tuple[bytes, int]:
    """The gain-map JPEG of the log2 gains `lg` (0..gmax) in which no lifted pixel reads above `hmax`, the largest
    value of the HDR figure; and the number of correcting passes it took.

    The gain map's own JPEG moves its codes, by up to 4 at quality 100 where a colour map makes the three channels'
    gains very different, and each code is 1 to 2 % of light. So the map is decoded as a reader will decode it, the
    figure the reader would show is rebuilt, and where that is above `hmax` the gain is lowered by the excess and
    a margin; then it is coded again, until nothing is over. A sample already at the smallest gain is never
    counted: it cannot be lowered, and a figure that lifts nothing must take no pass at all.

    Lowering one pixel moves its neighbours in the same JPEG block, so the brightest value does not fall steadily;
    the margin therefore grows as the passes go on (GAIN_MARGIN codes more after every GAIN_MARGIN_EVERY passes).
    The map is refused, with ValueError, if GAIN_PASS_LIMIT passes are not enough; and if, once the passes are done,
    a sample that could not be lowered reads above both `hmax` and the headroom (at a low gain quality the map's
    JPEG spreads gain onto unlifted pixels beside lifted ones). A file over its own headroom is never written.

    In a 4:4:4 baseline JPEG a block's decoded values depend on that block alone. So after the whole map has been
    coded and decoded, only the blocks holding a sample that is over are coded again, side by side in a small
    image, and on each pass only those of them that are still over; then the whole map is coded once more and
    checked. `whole_image` codes the whole map on every pass instead: the same bytes (a test holds the two
    together), and far slower on a large figure."""
    passes = 0
    jpeg = _jpeg(_gain_codes(lg, gmax), gain_quality)
    if not (lg > 0.0).any():                           # nothing is lifted: there is nothing to check or to lower
        return jpeg, passes
    shown_max = float(f"{gmax:.8g}")                   # the maximum as the XMP states it, which is what a reader uses
    # reading above hmax is the same as a decoded log2 gain above this, sample by sample; and the excess, in log2,
    # is the difference
    ceiling = np.log2((hmax + OFFSET) / (sdr_q + OFFSET))
    step = gmax / 255.0
    h, w = lg.shape[:2]
    refused = lambda why: ValueError(f"the gain map could not be kept within the headroom at this gain quality "
                                     f"({gain_quality}): {why}. Use gain_quality=100")

    def lower(gains, shown, top, over):
        """One correcting pass over the samples `over` of any part of the map, given its gains, the gains a reader
        decodes and the ceilings: the gains to code next."""
        nonlocal passes
        if passes >= GAIN_PASS_LIMIT:
            raise refused(f"after {passes} passes a lifted pixel still reads above the figure's brightest value")
        margin = GAIN_MARGIN * (1 + passes // GAIN_MARGIN_EVERY) * step
        passes += 1
        return np.where(over, np.maximum(gains - (shown - top) - margin, 0.0), gains)

    while True:
        shown = _decoded_gain(jpeg, shown_max)
        over = (shown > ceiling) & (lg > 0.0)
        if not over.any():
            if (shown > ceiling + np.log2((max(hmax, headroom) + OFFSET) / (hmax + OFFSET))).any():
                raise refused("a pixel the figure does not lift reads above the headroom")
            return jpeg, passes
        if whole_image:
            lg = lower(lg, shown, ceiling, over)
        else:
            blocks = np.pad(over.any(-1), ((0, -h % 8), (0, -w % 8))).reshape(-(-h // 8), 8, -(-w // 8), 8).any((1, 3))
            rows, cols = np.nonzero(blocks)
            at = np.arange(8)
            # A block that runs past the right or the bottom edge is filled, by JPEG, with the last column and then
            # the last row repeated. Reading it through these indices fills it the same way, so the small image
            # holds what the whole image's coder will see.
            y = np.minimum(rows[:, None] * 8 + at, h - 1)[:, :, None]; x = np.minimum(cols[:, None] * 8 + at, w - 1)[:, None, :]
            fill = (np.arange(len(rows))[:, None, None], y - rows[:, None, None] * 8, x - cols[:, None, None] * 8)
            inside = ((rows[:, None] * 8 + at < h)[:, :, None] & (cols[:, None] * 8 + at < w)[:, None, :])[..., None]
            part, seen, top = lg[y, x], shown[y, x], ceiling[y, x]
            live = np.arange(len(part))                # the blocks still over: one that is not stays as it is
            while True:
                still = (seen[live] > top[live]) & (part[live] > 0.0) & inside[live]
                keep = still.any((1, 2, 3)); live, still = live[keep], still[keep]
                if not len(live):
                    break
                again = lower(part[live], seen[live], top[live], still)
                again = again[fill[0][:len(live)], fill[1][live], fill[2][live]]
                image, across = _mosaic(_gain_codes(again, gmax))
                part[live] = again
                seen[live] = _unmosaic(_decoded_gain(_jpeg(image, gain_quality), shown_max), across, len(live))
            lg = lg.copy(); lg[y, x] = part
        jpeg = _jpeg(_gain_codes(lg, gmax), gain_quality)


def write_gainmap_jpeg(sdr, hdr, path, headroom: float, quality: int = 95, gain_quality: int = 100,
                       description: bytes = b"", iso: bool = True) -> None:
    """sdr: linear RGB in [0, 1] (the SDR plot); hdr: linear RGB in multiples of SDR white.

    A pixel the figure does not lift (hdr no greater than sdr in all three channels) is stored with a gain of
    exactly 1, so GainMapMin is 0 and the gain map is flat wherever the figure is not lifted. No pixel is darkened:
    a channel that is lower in `hdr` than in the stored SDR plot keeps the SDR value.

    Before the file is written its gain map is decoded again and the figure a reader would show is rebuilt; where
    that is brighter than the largest value in `hdr` the gain is lowered and the map coded again, until nothing is
    over. The cost is time (several coding passes on a coloured figure) and a top of the range that reads a little
    low. If it cannot be done the file is not written and ValueError is raised: a file is never written that
    reads above the brightest value it was given, on this module's reader.

    The gain map is stored at `gain_quality` (default 100): its errors are exponentiated, so JPEG ringing
    at sharp gain edges (a colour bar beside a white background) otherwise pushes values past the headroom.
    Below 100 it is unreliable on coloured maps (fire, ice: where one SDR channel is small the gain range is wide
    and each code a large step, with this writer or any other): the passes may not converge, or gain may spread
    onto unlifted pixels, and the call then raises ValueError; a file that is written may still fail the
    validator's checks on its pixels. Leave it at 100 unless the figure is grey.

    `description` is an extra rdf:Description for the primary image's XMP packet: the profile's declared
    metadata, from udeep.metadata.description. iso=True also writes the gain-map metadata in the binary form of
    ISO 21496-1, beside the Ultra HDR XMP, for decoders that read only that."""
    from udeep import core
    if iso and core.files():                           # the Rust core writes the same file, faster
        sdr, hdr = _checked(sdr, hdr, headroom)
        data = core.write_gainmap(sdr, hdr, headroom, quality, gain_quality, description)
    else:
        data, _passes = _gainmap_file(sdr, hdr, headroom, quality, gain_quality, description, iso)
    Path(path).write_bytes(data)                       # only once the whole file is known to be within its peak


def _checked(sdr, hdr, headroom: float) -> tuple[np.ndarray, np.ndarray]:
    """The two figures as float64 arrays, refused if they cannot be a gain-map JPEG's."""
    sdr = np.asarray(sdr, dtype=np.float64); hdr = np.asarray(hdr, dtype=np.float64)
    if sdr.shape != hdr.shape or sdr.ndim != 3 or sdr.shape[-1] != 3:
        raise ValueError("sdr and hdr must both be H x W x 3")
    if np.nanmin(sdr) < -1e-6 or np.nanmax(sdr) > 1.0 + 1e-6:
        raise ValueError("the SDR plot must lie in [0, 1]")
    if not headroom > 1.0:
        raise ValueError("headroom must be > 1")
    return sdr, hdr


def _gainmap_file(sdr, hdr, headroom: float, quality: int = 95, gain_quality: int = 100, description: bytes = b"",
                  iso: bool = True, whole_image: bool = False) -> tuple[bytes, int]:
    """The bytes write_gainmap_jpeg writes, and the number of correcting passes the gain map took: 0 when nothing
    read above the figure's brightest value, and always 0 for a figure that lifts nothing."""
    sdr, hdr = _checked(sdr, hdr, headroom)
    sdr = np.clip(np.nan_to_num(sdr), 0.0, 1.0); hdr = np.clip(np.nan_to_num(hdr), 0.0, None)

    base8 = np.round(srgb_oetf(sdr) * 255.0).astype(np.uint8)
    base_jpeg = _jpeg(base8, quality)
    decoded = np.asarray(Image.open(io.BytesIO(base_jpeg)).convert("RGB"), dtype=np.float64)
    sdr_q = _srgb_eotf(decoded / 255.0)                # gain relative to the base AS DECODED: JPEG error in
                                                       # the base would otherwise be multiplied by the gain
    with np.errstate(over="ignore"):
        lg = np.log2((hdr + OFFSET) / (sdr_q + OFFSET))
    if not np.isfinite(lg).all():
        raise ValueError("the HDR figure holds a value too large to store as a gain (infinite, or near the largest "
                         "number there is)")
    # Hold: a pixel the figure does not lift gets a gain of exactly 1, and no pixel is darkened. The smallest gain
    # is then the constant 0 rather than the JPEG error of the base, which used to set the bottom of the 8-bit
    # range and so made every code a larger step; and the unlifted part of the map is flat, which JPEG keeps flat.
    held = np.all(hdr <= sdr + 1e-6, axis=-1)
    lg = np.where(held[..., None], 0.0, np.maximum(lg, 0.0))
    gmin = 0.0; gmax = max(float(lg.max()), GAIN_SPAN_MIN)
    gain_jpeg, passes = _gain_within_peak(lg, sdr_q, gmax, float(hdr.max()), headroom, gain_quality, whole_image)
    cap_max = float(np.log2(headroom))
    iso_gain = _segment(0xE2, ISO_NS + _iso_metadata(gmin, gmax, cap_max)) if iso else b""
    iso_primary = _segment(0xE2, ISO_NS + struct.pack(">HH", 0, 0)) if iso else b""    # the versions only
    gain = gain_jpeg[:2] + _segment(0xE1, _gain_xmp(gmin, gmax, cap_max)) + iso_gain + gain_jpeg[2:]
    app1 = _segment(0xE1, _primary_xmp(len(gain), description))
    mpf_len = len(_segment(0xE2, _mpf(0, 0, 0)))
    primary_len = 2 + len(app1) + len(iso_primary) + mpf_len + len(base_jpeg) - 2
    tiff_at = 2 + len(app1) + len(iso_primary) + 4 + 4     # SOI, APP1, ISO APP2, MPF marker+length, "MPF\0"
    app2 = _segment(0xE2, _mpf(primary_len, len(gain), primary_len - tiff_at))
    primary = base_jpeg[:2] + app1 + iso_primary + app2 + base_jpeg[2:]
    assert len(primary) == primary_len
    return primary + gain, passes


def _attr(xmp: bytes, name: str, default: float | None = None) -> float:
    m = re.search(rb"hdrgm:" + name.encode() + rb'="([-0-9.eE+]+)"', xmp)
    if m is None:
        if default is None:
            raise ValueError(f"gain map is missing hdrgm:{name}")
        return default
    return float(m.group(1))


def read_iso_metadata(path) -> dict | None:
    """The gain map's ISO 21496-1 binary metadata, as numbers (log2 for the headrooms, min and max), or None if
    the file has none. Reads the single-channel, separate-denominator layout that this module writes. Files
    written before 2026-10-10 have flags 0 (bit 6 not set); they are read the same."""
    data = Path(path).read_bytes()
    m = re.search(rb'Item:Semantic="GainMap"[^>]*?Item:Length="(\d+)"', data, re.DOTALL)
    gain = data if m is None else data[-int(m.group(1)):]
    at = gain.find(ISO_NS)
    if at < 0:
        return None
    body = gain[at + len(ISO_NS):]
    min_version, _writer, flags = struct.unpack(">HHB", body[:5])
    if min_version != 0 or flags not in (0, ISO_BASE_COLOUR_SPACE):
        raise ValueError(f"unsupported ISO 21496-1 metadata (minimum_version {min_version}, flags {flags:#04x})")
    v = struct.unpack(">IIIIiIiIIIiIiI", body[5:5 + 14 * 4])
    names = ("base_hdr_headroom", "alternate_hdr_headroom", "gain_map_min", "gain_map_max", "gamma", "base_offset",
             "alternate_offset")
    return {name: v[2 * i] / v[2 * i + 1] for i, name in enumerate(names)}


def read_gainmap_jpeg(path, display_boost: float) -> np.ndarray:
    """Linear RGB (multiples of SDR white) as a display with `display_boost` headroom shows it.

    Read by the Rust core when it is in use (udeep.core), with the JPEG decoder its writer checks files against;
    otherwise, and for any file the core does not take, by the code below with Pillow's decoder. The two decoders
    differ by a code or two at some pixels, which is a few per cent of a lifted pixel where one SDR channel is
    small: a file reads exactly as its writer left it only on the decoder its writer used."""
    from udeep import core
    data = Path(path).read_bytes()
    if core.files():
        shown = core.read_gainmap(data, display_boost)
        if shown is not None:
            return shown
    m = re.search(rb'Item:Semantic="GainMap"[^>]*?Item:Length="(\d+)"', data, re.DOTALL)
    if m is None:
        raise ValueError("no gain-map container directory in the primary XMP")
    n = int(m.group(1)); primary, gain = data[:-n], data[-n:]
    sdr = _srgb_eotf(np.asarray(Image.open(io.BytesIO(primary)).convert("RGB"), dtype=np.float64) / 255.0)
    g_img = Image.open(io.BytesIO(gain)).convert("RGB")
    if g_img.size != (sdr.shape[1], sdr.shape[0]):
        g_img = g_img.resize((sdr.shape[1], sdr.shape[0]), Image.BILINEAR)
    rec = np.asarray(g_img, dtype=np.float64) / 255.0
    gmin, gmax = _attr(gain, "GainMapMin", 0.0), _attr(gain, "GainMapMax")
    gamma = _attr(gain, "Gamma", 1.0)
    o_sdr, o_hdr = _attr(gain, "OffsetSDR", OFFSET), _attr(gain, "OffsetHDR", OFFSET)
    cmin, cmax = _attr(gain, "HDRCapacityMin", 0.0), _attr(gain, "HDRCapacityMax")
    weight = float(np.clip((np.log2(display_boost) - cmin) / (cmax - cmin), 0.0, 1.0))
    log_rec = rec ** (1.0 / gamma)
    log_boost = gmin * (1.0 - log_rec) + gmax * log_rec
    return ((sdr + o_sdr) * np.exp2(log_boost * weight) - o_hdr).astype(np.float32)
