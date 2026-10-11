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

from udeep.color import REF_WHITE_NITS, srgb_to_bt2020

_M1, _M2 = 2610 / 16384, 2523 / 4096 * 128
_C1, _C2, _C3 = 3424 / 4096, 2413 / 4096 * 32, 2392 / 4096 * 32
CICP_BT2020_PQ_FULL = bytes([9, 16, 0, 1])  # primaries, transfer, matrix (RGB), full range


def pq_encode(nits) -> np.ndarray:
    y = (np.clip(np.asarray(nits, dtype=np.float64), 0.0, 10000.0) / 10000.0) ** _M1
    return ((_C1 + _C2 * y) / (1 + _C3 * y)) ** _M2


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png_pq(rgb, path, sdr_white_nits: float = REF_WHITE_NITS, xmp: bytes | None = None) -> None:
    """PQ is absolute, so 1.0 is written at `sdr_white_nits`. Use the display's SDR white
    level (Windows: Settings > Display > HDR > SDR content brightness) so non-highlight
    content matches the desktop; the BT.2408 default (203) suits an unknown display.
    `xmp` is an XMP packet (udeep.metadata.to_xmp), stored in an iTXt chunk."""
    rgb = np.nan_to_num(np.asarray(rgb, dtype=np.float64), nan=0.0)
    h, w, _ = rgb.shape
    signal = pq_encode(np.maximum(srgb_to_bt2020(rgb), 0.0) * sdr_white_nits)
    img = np.round(signal * 65535.0).astype(">u2")
    raw = b"".join(b"\x00" + img[r].tobytes() for r in range(h))
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
           + _chunk(b"cICP", CICP_BT2020_PQ_FULL)
           + (_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp) if xmp else b"")
           + _chunk(b"IDAT", zlib.compress(raw, 9))
           + _chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def srgb_oetf(v) -> np.ndarray:
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055)


def write_png_sdr(rgb, path) -> None:
    rgb = np.nan_to_num(np.asarray(rgb, dtype=np.float64), nan=0.0)
    Image.fromarray(np.round(srgb_oetf(rgb) * 255.0).astype(np.uint8), "RGB").save(path)
