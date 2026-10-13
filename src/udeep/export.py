"""Write HDR (PQ-tagged 16-bit PNG) and SDR (8-bit sRGB PNG) images from linear sRGB.

Input rgb is relative linear sRGB (1.0 = SDR white), shape (H, W, 3), row 0 at the top.
The HDR file is BT.2020 primaries, PQ transfer (BT.2100), signalled by a PNG `cICP` chunk
(PNG 3rd edition), which Chromium-based browsers display as HDR.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from udeep.color import _SRGB_TO_BT2020, REF_WHITE_NITS, srgb_to_bt2020

_M1, _M2 = 2610 / 16384, 2523 / 4096 * 128
_C1, _C2, _C3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
CICP_BT2020_PQ_FULL = bytes([9, 16, 0, 1])  # primaries, transfer, matrix (RGB), full range


def pq_encode(nits) -> np.ndarray:
    y = (np.clip(np.asarray(nits, dtype=np.float64), 0.0, 10000.0) / 10000.0) ** _M1
    return ((_C1 + _C2 * y) / (1 + _C3 * y)) ** _M2


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def pq_decode(signal) -> np.ndarray:
    """PQ signal in [0, 1] -> luminance in cd/m^2 (SMPTE ST 2084): the inverse of pq_encode."""
    p = np.asarray(signal, dtype=np.float64) ** (1.0 / _M2)
    return 10000.0 * (np.maximum(p - _C1, 0.0) / (_C2 - _C3 * p)) ** (1.0 / _M1)


def pq16_codes(rgb, sdr_white_nits: float = REF_WHITE_NITS) -> np.ndarray:
    """The 16-bit codes a PQ PNG stores for linear RGB in multiples of SDR white: to BT.2020, to PQ with white at
    `sdr_white_nits`, rounded to 16 bits. A NaN is stored as 0."""
    rgb = np.nan_to_num(np.asarray(rgb, dtype=np.float64), nan=0.0)
    return np.round(pq_encode(np.maximum(srgb_to_bt2020(rgb), 0.0) * sdr_white_nits) * 65535.0).astype(np.uint16)


def write_png_pq(rgb, path, sdr_white_nits: float = REF_WHITE_NITS, xmp: bytes | None = None) -> None:
    """A 16-bit PQ PNG of `rgb` (height x width x 3, linear, multiples of SDR white). PQ is absolute, so 1.0 is
    written at `sdr_white_nits`. Use the display's SDR white level (Windows: Settings > Display > HDR > SDR
    content brightness) so non-highlight content matches the desktop; the BT.2408 default (203) suits an unknown
    display, and is the one to use for a file that is to be read back as data. `xmp` is an XMP packet
    (udeep.metadata.to_xmp), stored in an iTXt chunk.

    Written by the Rust core when it is in use (udeep.core): the codes are the same as this function's own, and
    the compressed bytes differ. read_png_pq reads either."""
    from udeep import core
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must be height x width x 3")
    if core.png():
        Path(path).write_bytes(core.write_png(rgb, float(sdr_white_nits), xmp or b""))
        return
    h, w, _ = rgb.shape
    img = pq16_codes(rgb, sdr_white_nits).astype(">u2")
    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
           + _chunk(b"cICP", CICP_BT2020_PQ_FULL)
           + (_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp) if xmp else b"")
           + _chunk(b"IDAT", zlib.compress(raw, 9))
           + _chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def _chunks(raw: bytes):
    at = 8
    while at + 12 <= len(raw):
        length, tag = struct.unpack(">I4s", raw[at:at + 8])
        yield tag, raw[at + 8:at + 8 + length]
        at += 12 + length


def _unfilter(rows: np.ndarray, bpp: int = 6) -> np.ndarray:
    """PNG's row filters undone: `rows` is height x (1 + line), a filter byte and then the filtered bytes."""
    out = np.zeros((rows.shape[0], rows.shape[1] - 1), dtype=np.uint8)
    above = np.zeros(out.shape[1], dtype=np.uint8)
    for y, row in enumerate(rows):
        kind, line = int(row[0]), row[1:]
        if kind == 0:
            out[y] = line
        elif kind == 1:                                   # each byte is over the one a pixel to its left
            out[y] = np.cumsum(line.reshape(-1, bpp), axis=0, dtype=np.uint8).reshape(-1)
        elif kind == 2:
            out[y] = line + above
        elif kind in (3, 4):                              # Average and Paeth depend on the row as it is rebuilt
            now, up = np.zeros(len(line) + bpp, dtype=np.int64), np.concatenate([np.zeros(bpp, np.int64), above])
            for i, value in enumerate(line.astype(np.int64)):
                left, top, corner = now[i], up[i + bpp], up[i]
                if kind == 3:
                    predicted = (left + top) // 2
                else:
                    p = left + top - corner
                    pa, pb, pc = abs(p - left), abs(p - top), abs(p - corner)
                    predicted = left if pa <= pb and pa <= pc else top if pb <= pc else corner
                now[i + bpp] = (value + predicted) & 255
            out[y] = now[bpp:]
        else:
            raise ValueError(f"not a PNG this reader can read: row filter {kind}")
        above = out[y]
    return out


def read_png_pq(path, sdr_white_nits: float = REF_WHITE_NITS) -> np.ndarray:
    """A 16-bit PQ PNG as light: float64, height x width x 3, linear RGB (BT.709 primaries) in multiples of the
    white written at `sdr_white_nits`. It is what write_png_pq was given, to within the file's 16-bit steps: for a
    grey figure, any channel is the light, and Mapping.value turns it back into data.

    ValueError for a PNG of another kind: it must be 16 bits a channel, RGB, not interlaced, and declared BT.2020
    PQ in a cICP chunk. Read by the Rust core when it is in use, otherwise by the code here."""
    from udeep import core
    raw = Path(path).read_bytes()
    if core.png():
        light = core.read_png(raw, float(sdr_white_nits))
        if light is None:
            raise ValueError(f"{path}: not a 16-bit PQ PNG (16 bits a channel, RGB, not interlaced, cICP BT.2020 PQ)")
        return light
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    size, declared, parts = None, False, []
    for tag, body in _chunks(raw):
        if tag == b"IHDR":
            if body[8:13] != bytes([16, 2, 0, 0, 0]):
                raise ValueError(f"{path}: not a 16-bit PQ PNG (16 bits a channel, RGB, not interlaced, cICP BT.2020 PQ)")
            size = struct.unpack(">II", body[:8])
        elif tag == b"cICP":
            declared = body == CICP_BT2020_PQ_FULL
        elif tag == b"IDAT":
            parts.append(body)
    if size is None or not declared:
        raise ValueError(f"{path}: not a 16-bit PQ PNG (16 bits a channel, RGB, not interlaced, cICP BT.2020 PQ)")
    width, height = size
    rows = np.frombuffer(zlib.decompress(b"".join(parts)), dtype=np.uint8).reshape(height, 1 + width * 6)
    codes = _unfilter(rows).view(">u2").reshape(height, width, 3)
    wide = pq_decode(codes / 65535.0) / float(sdr_white_nits)
    return np.einsum("ij,...j->...i", np.linalg.inv(_SRGB_TO_BT2020), wide)


def srgb_oetf(v) -> np.ndarray:
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055)


def write_png_sdr(rgb, path) -> None:
    rgb = np.nan_to_num(np.asarray(rgb, dtype=np.float64), nan=0.0)
    Image.fromarray(np.round(srgb_oetf(rgb) * 255.0).astype(np.uint8), "RGB").save(path)
