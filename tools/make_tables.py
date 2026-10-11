"""Write the shipped colour-map tables (src/udeep/tables/) from the generator, with an index of digests.

A map's definition is never changed once shipped: a change is a new version beside the old ones
(udeep.cmap.DEFINITIONS). Run it after adding a version:

    python tools/make_tables.py

A table that exists is never rewritten. The shipped file is the definition, and the generator does not give the
same bytes on every machine: the values of a version-2 table that are almost zero depend on which processor-specific
routines NumPy and OpenBLAS choose (found on Linux, 2026-10-11; project/performance-scope.md, 4g). Writing a table
again from another machine would change the definition without anyone meaning to. The run checks instead that the generator
still gives each existing table to the table tolerance, and stops if it does not: then the definition was edited,
and wants a new version. A table that was never published can be made again by deleting its file first.

Format: one file per (map, reference headroom). Two segments of NODES nodes x 3 numbers, little-endian float32:
first light 0..1, then light 1..headroom; nodes evenly spaced in light; linear RGB, BT.709 primaries, in multiples
of SDR white. Colours between nodes are linear interpolations.
"""
from __future__ import annotations

import importlib
import json

import numpy as np

TOLERANCE = 2e-6                                     # relative and absolute: udeep.golden.TABLE

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
                state = "written"
                if path.exists():
                    made = np.concatenate([t.below, t.above])
                    kept = np.frombuffer(path.read_bytes(), dtype="<f4")[-made.size:].reshape(made.shape)
                    if not np.allclose(made, kept, rtol=TOLERANCE, atol=TOLERANCE):
                        raise SystemExit(f"{file}: the generator no longer gives this table: its definition was "
                                         "changed. Make a new version, or delete the file if it was never published.")
                    state = "kept" if path.read_bytes() == t.to_bytes() else "kept (this machine gives other bytes)"
                    t = cm.Table(name, headroom, kept[:len(kept) // 2], kept[len(kept) // 2:])
                else:
                    path.write_bytes(t.to_bytes())
                index["tables"][f"{cm.identifier(name, version)}@{headroom:g}"] = {
                    "file": file, "headroom": headroom, "sha256": t.digest}
                print(file, len(t.to_bytes()), "bytes", t.digest[:19], state)
    (cm.TABLES / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
