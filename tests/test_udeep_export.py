"""udeep.export: PQ PNG (HDR) and sRGB PNG (SDR)."""
import struct
import zlib

import numpy as np
import pytest
from PIL import Image

from udeep import pq_encode, write_png_pq, write_png_sdr


def chunks(data: bytes):
    i = 8
    while i < len(data):
        n = struct.unpack(">I", data[i:i + 4])[0]
        yield data[i + 4:i + 8], data[i + 8:i + 8 + n]
        i += 12 + n


def test_pq_reference_values():
    # BT.2100 PQ: 203 cd/m^2 (BT.2408 reference white) -> 0.5807; 1000 -> 0.7518; 10000 -> 1
    assert pq_encode(203.0) == pytest.approx(0.5807, abs=1e-4)
    assert pq_encode(1000.0) == pytest.approx(0.7518, abs=1e-4)
    assert pq_encode(10000.0) == pytest.approx(1.0)
    assert pq_encode(0.0) < 1e-5


def test_pq_png_structure_and_white(tmp_path):
    p = tmp_path / "w.png"
    write_png_pq(np.ones((2, 3, 3), np.float32), p)
    data = p.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    kinds = [k for k, _ in chunks(data)]
    assert kinds.index(b"cICP") < kinds.index(b"IDAT")
    body = dict(chunks(data))
    assert body[b"cICP"] == bytes([9, 16, 0, 1])
    w, h, depth, ctype = struct.unpack(">IIBB", body[b"IHDR"][:10])
    assert (w, h, depth, ctype) == (3, 2, 16, 2)
    raw = zlib.decompress(body[b"IDAT"])
    row = raw[1:1 + 3 * 3 * 2]
    assert raw[0] == 0
    vals = np.frombuffer(row, ">u2")
    assert np.all(np.abs(vals.astype(int) - round(0.5807 * 65535)) <= 8)


def test_pq_png_white_follows_display_sdr_white(tmp_path):
    # PQ is absolute: SDR white must be written at the display's SDR white level, or the
    # whole image renders dimmer than the SDR copy (seen on a 240-nit desktop, 2026-09-25).
    p = tmp_path / "w240.png"
    write_png_pq(np.ones((1, 1, 3), np.float32), p, sdr_white_nits=240.0)
    raw = zlib.decompress(dict(chunks(p.read_bytes()))[b"IDAT"])
    vals = np.frombuffer(raw[1:7], ">u2").astype(int)
    assert np.all(np.abs(vals - round(float(pq_encode(240.0)) * 65535)) <= 8)


def test_sdr_png(tmp_path):
    p = tmp_path / "s.png"
    write_png_sdr(np.array([[[1.0, 2.0, 0.5], [0.0, np.nan, 0.0]]], np.float32), p)
    im = np.asarray(Image.open(p))
    assert im.dtype == np.uint8 and im.shape == (1, 2, 3)
    assert list(im[0, 0]) == [255, 255, 188] and list(im[0, 1]) == [0, 0, 0]
