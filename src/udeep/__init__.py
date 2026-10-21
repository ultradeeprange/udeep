"""HDR plotting core: data -> multiples of SDR white -> HDR colour -> HDR file.

See profile/udeep-figure-profile-v0.3.md.
"""
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from udeep.cmap import MAPS, cmap
from udeep.export import pq_encode, read_png_pq, write_png_pq, write_png_sdr
from udeep.gainmap import read_gainmap_jpeg, write_gainmap_jpeg
from udeep.mapping import Mapping
from udeep.metadata import Declaration, caption, max_data_step, read_metadata, read_png_data, to_xmp
from udeep.scale import scale
from udeep.tone import knee_at_percentile, tone
from udeep.view import HdrView, hdr_view

try:
    __version__ = _version("udeep")
except PackageNotFoundError:                                  # imported from a source tree that is not installed
    __version__ = "unknown"

__all__ = ["MAPS", "Declaration", "HdrView", "Mapping", "caption", "cmap", "hdr_view", "knee_at_percentile", "max_data_step", "pq_encode",
           "read_gainmap_jpeg", "read_metadata", "read_png_data", "read_png_pq", "scale", "to_xmp", "tone", "write_gainmap_jpeg", "write_png_pq", "write_png_sdr"]
