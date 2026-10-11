"""Write the shipped colour-map tables (src/udeep/tables/) from the generator, with an index of digests.

A map's definition is never changed once shipped: a change is a new version beside the old ones
(udeep.cmap.DEFINITIONS), and every version is written here, so that this run must reproduce the old tables bit
for bit. Run it after adding a version:

    python tools/make_tables.py

Format: one file per (map, reference headroom). Two segments of NODES nodes x 3 numbers, little-endian float32:
first light 0..1, then light 1..headroom; nodes evenly spaced in light; linear RGB, BT.709 primaries, in multiples
of SDR white. Colours between nodes are linear interpolations.
"""
from __future__ import annotations

import importlib
import json

cm = importlib.import_module("udeep.cmap")           # the package also exports a function called cmap


def main() -> None:
    cm.TABLES.mkdir(exist_ok=True)
    index = {"format": "two segments (0..1, 1..headroom) of `nodes` x 3 little-endian float32, linear BT.709 RGB in "
                       "multiples of SDR white; linear interpolation between nodes",
             "cicp": {"colour_primaries": 1, "transfer_characteristics": 8,
                      "note": "ITU-T H.273 code points: 1 = BT.709 primaries, 8 = linear"},
             "version": cm.VERSION, "nodes": cm.NODES, "tables": {}}
    for version in sorted(cm.DEFINITIONS):
        for name in sorted(cm.DEFINITIONS[version]):
            for headroom in cm.REGISTERED_HEADROOMS:
                t = cm.generate(name, headroom, version=version)
                file = cm._file(name, headroom, version)
                path = cm.TABLES / file
                if path.exists() and path.read_bytes() != t.to_bytes() and version != cm.VERSION:
                    raise SystemExit(f"{file}: a shipped version no longer reproduces: its definition was changed")
                path.write_bytes(t.to_bytes())
                index["tables"][f"{cm.identifier(name, version)}@{headroom:g}"] = {
                    "file": file, "headroom": headroom, "sha256": t.digest}
                print(file, len(t.to_bytes()), "bytes", t.digest[:19])
    (cm.TABLES / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
