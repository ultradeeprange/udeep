"""Where a file stores its declaration (profile 8.8): one packet, in the one place each format keeps XMP, and a reader
that takes it from there and nowhere else, whatever else the file carries."""
import re
import zlib

import numpy as np
import pytest
from PIL import Image

from udeep import write_gainmap_jpeg, write_png_pq
from udeep.metadata import Declaration, description, packet, parse, read_metadata, replace_packet, to_xmp
from udeep.validate import validate

BASE = {"data_unit": "counts", "stretch": "linear", "range_low": 0.0, "range_high": 100.0, "knee": 50.0,
        "knee_rule": "preset; half of full scale", "reference_headroom": 2.0, "max_data_step": 1.0, "colour_map": "grey"}


def jpeg(tmp_path, name="f.jpg"):
    sdr = np.full((32, 32, 3), 0.5); hdr = sdr.copy(); hdr[8:24, 8:24] = 1.5; sdr[8:24, 8:24] = 1.0
    path = tmp_path / name
    write_gainmap_jpeg(sdr, hdr, path, headroom=2.0, description=description(Declaration(**BASE)))
    return path


def png(tmp_path, compressed=False):
    rgb = np.full((8, 8, 3), 0.5)
    d = Declaration(**{**BASE, "sdr_digest": "sha256:" + "cd" * 32})
    path = tmp_path / "f.png"
    write_png_pq(rgb, path, xmp=to_xmp(d))
    if compressed:                                       # the same XMP chunk, its text deflated (flag 1)
        raw = path.read_bytes()
        i = raw.index(b"iTXt") - 4
        n = int.from_bytes(raw[i:i + 4], "big")
        body = raw[i + 8:i + 8 + n]
        key, rest = body.split(b"\x00", 1)
        text = rest[4:]                                  # after the flag, the method, and the empty language and keyword
        assert text.startswith(b"<x:xmpmeta")
        new = key + b"\x00\x01\x00\x00\x00" + zlib.compress(text)
        chunk = len(new).to_bytes(4, "big") + b"iTXt" + new + zlib.crc32(b"iTXt" + new).to_bytes(4, "big")
        path.write_bytes(raw[:i] + chunk + raw[i + 12 + n:])
    return path


def segment(marker: bytes, body: bytes) -> bytes:
    return marker + (len(body) + 2).to_bytes(2, "big") + body


def test_a_file_as_written_has_its_one_declaration_where_the_profile_keeps_it(tmp_path):
    """The control for every test below: the reference writers store the packet in the right place, once."""
    for path in (jpeg(tmp_path), png(tmp_path)):
        assert read_metadata(path) is not None and validate(path).status("metadata") == "pass", path


def test_a_declaration_stored_elsewhere_in_a_jpeg_is_not_taken(tmp_path):
    """Moved from the XMP segment into a comment segment of the same image: still in the file, not where 8.8 says."""
    path = jpeg(tmp_path)
    raw = path.read_bytes()
    xmp = packet(raw)
    plain = re.sub(rb"<rdf:Description[^>]*xmlns:udeep=[^>]*/>", b"", xmp)          # the packet without the declaration
    assert plain != xmp and b"udeep:" not in plain
    moved = replace_packet(raw, plain)
    moved = moved[:2] + segment(b"\xff\xfe", xmp) + moved[2:]                       # a COM segment, before the MPF index
    path.write_bytes(moved)
    assert Image.open(path).size == (32, 32)                                        # still a JPEG any decoder reads
    assert read_metadata(path) is None
    r = validate(path)
    assert r.status("metadata") == "fail" and "outside the primary image's XMP segment" in str(r), str(r)


def test_two_declarations_are_refused_in_one_packet_or_in_two(tmp_path):
    path = jpeg(tmp_path)
    raw = path.read_bytes()
    xmp = packet(raw)
    one = re.search(rb"<rdf:Description[^>]*xmlns:udeep=[^>]*/>", xmp).group(0)
    path.write_bytes(replace_packet(raw, xmp.replace(one, one + one)))
    with pytest.raises(ValueError, match="2 udeep declarations"):
        read_metadata(path)
    assert "2 udeep declarations" in str(validate(path))
    second = segment(b"\xff\xe1", b"http://ns.adobe.com/xap/1.0/\x00" + xmp)            # a second XMP segment, the same
    path.write_bytes(raw[:2] + second + raw[2:])
    with pytest.raises(ValueError, match="2 udeep metadata packets"):
        read_metadata(path)
    assert validate(path).status("metadata") == "fail"


def test_a_compressed_xmp_chunk_in_a_png_is_not_read_and_says_so(tmp_path):
    path = png(tmp_path, compressed=True)
    assert read_metadata(path) is None
    r = validate(path)
    assert r.status("metadata") == "fail" and "compressed" in str(r), str(r)


def test_properties_written_as_elements_read_as_attributes_do(tmp_path):
    """Another XMP tool may rewrite the attributes as child elements; the declaration is the same."""
    d = Declaration(**BASE)
    attrs = description(d).decode()
    props = re.findall(r' udeep:(\w+)="([^"]*)"', attrs)
    elements = ('<rdf:Description rdf:about="" xmlns:udeep="https://ultradeeprange.github.io/udeep/ns/0.3/">'
                + "".join(f"<udeep:{k}>{v}</udeep:{k}>" for k, v in props) + "</rdf:Description>")
    xmp = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
           + elements.encode() + b"</rdf:RDF></x:xmpmeta>")
    assert parse(xmp) == d


def test_a_packet_too_large_for_one_jpeg_segment_is_refused(tmp_path):
    path = jpeg(tmp_path)
    raw = path.read_bytes()
    xmp = packet(raw)
    big = xmp.replace(b"</rdf:RDF>", b"<!--" + b"x" * 70_000 + b"--></rdf:RDF>")
    with pytest.raises(ValueError, match="more than one JPEG segment holds"):
        replace_packet(raw, big)
    assert replace_packet(raw, xmp) == raw                                          # unchanged, byte for byte
