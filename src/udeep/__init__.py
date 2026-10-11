"""HDR plotting core: data -> multiples of SDR white -> HDR colour -> HDR file.

See docs/superpowers/specs/2026-09-25-hdr-plotting-scope.md.
"""
from udeep.cmap import MAPS, cmap
from udeep.export import pq_encode, write_png_pq, write_png_sdr
from udeep.gainmap import read_gainmap_jpeg, write_gainmap_jpeg
from udeep.mapping import Mapping
from udeep.metadata import Declaration, caption, max_data_step, read_metadata, to_xmp
from udeep.scale import scale
from udeep.tone import knee_at_percentile, tone
from udeep.view import HdrView, hdr_view

__all__ = ["MAPS", "Declaration", "HdrView", "Mapping", "caption", "cmap", "hdr_view", "knee_at_percentile", "max_data_step", "pq_encode",
           "read_gainmap_jpeg", "read_metadata", "scale", "to_xmp", "tone", "write_gainmap_jpeg", "write_png_pq", "write_png_sdr"]
