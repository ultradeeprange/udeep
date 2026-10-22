"""The JS port of udeep reproduces golden.json, and every page ships the canonical copy.

The checker (check_golden.mjs) is itself under test here: it must fail when it has checked nothing, when it meets a
kind of case it has never heard of, and when the port returns NaN where a number is expected; and what it checks
must be the shipped port (udeepcore.js), not code of its own.
"""
import collections
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src" / "udeep" / "js"
GOLDEN = ROOT / "src" / "udeep" / "golden.json"
TABLES = ROOT / "src" / "udeep" / "tables"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

# What the port implements, and what it is known not to: the levels, the declaration's text and the caption.
IMPLEMENTED = {"scale", "tone", "pq_encode", "srgb_oetf", "cmap", "table", "mapping", "mask_isolated", "digest_data",
               "digest_pixels", "digest_data_sha3", "digest_pixels_sha3", "iso21496"}
NOT_IN_THE_PORT = {"levels", "levels_pq16", "xmp", "caption"}


def check(golden: Path = GOLDEN, tables: Path = TABLES, js: Path = JS) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, str(js / "check_golden.mjs"), str(golden), str(tables)], capture_output=True, text=True,
                          check=False)


def reproduced(stdout: str) -> int:
    return int(re.search(r"^(\d+) cases reproduced", stdout, re.MULTILINE).group(1))


def kinds() -> collections.Counter:
    return collections.Counter(c["kind"] for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"].values())


def changed_port(tmp_path: Path, old: str, new: str) -> Path:
    """A copy of the port and its checker with one piece of udeepcore.js replaced."""
    js = tmp_path / "js"
    js.mkdir()
    source = (JS / "udeepcore.js").read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    (js / "udeepcore.js").write_text(source.replace(old, new), encoding="utf-8", newline="\n")
    shutil.copy(JS / "check_golden.mjs", js / "check_golden.mjs")
    return js


def run_js(tmp_path: Path, body: str) -> dict:
    """Run `body` (an ES module that has UDeepCore, and prints one line of JSON) in node; returns what it printed."""
    script = tmp_path / "probe.mjs"
    script.write_text(f"import {json.dumps((JS / 'udeepcore.js').as_uri())};\nconst H = globalThis.UDeepCore;\n"
                      "const thrown = f => { try { f(); return null; } catch (e) { return e.constructor.name; } };\n"
                      + body, encoding="utf-8")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


# ---- the golden vectors

@needs_node
def test_js_core_reproduces_golden():
    r = check()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout
    found = kinds()
    assert set(found) == IMPLEMENTED | NOT_IN_THE_PORT, "golden.json has a kind this test has not been told about"
    assert reproduced(r.stdout) >= sum(n for kind, n in found.items() if kind in IMPLEMENTED) > 0
    skipped = json.loads(re.search(r"skipped \(not in this port\): (\{.*\})", r.stdout).group(1))
    assert skipped == {kind: found[kind] for kind in sorted(NOT_IN_THE_PORT)}


@needs_node
def test_golden_check_catches_a_changed_map(tmp_path):
    """The check is not vacuous: a 1 % change to one colour-map table must fail it."""
    import numpy as np
    tables = tmp_path / "tables"
    shutil.copytree(TABLES, tables)
    changed = np.fromfile(tables / "fire-1-h4.f32", dtype="<f4") * np.float32(1.01)
    changed.tofile(tables / "fire-1-h4.f32")
    r = check(tables=tables)
    assert r.returncode != 0 and "FAIL" in r.stdout


@needs_node
def test_golden_check_fails_when_it_has_reproduced_nothing(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"format": 2, "cases": {}}), encoding="utf-8")
    r = check(golden=empty)
    assert r.returncode != 0 and "PASS" not in r.stdout
    assert "0 cases reproduced" in r.stdout and "FAIL: no case was reproduced" in r.stdout
    only_skipped = tmp_path / "skipped.json"
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    only_skipped.write_text(json.dumps({"format": 2, "cases": {k: c for k, c in cases.items() if c["kind"] == "caption"}}),
                            encoding="utf-8")
    r = check(golden=only_skipped)
    assert r.returncode != 0 and "PASS" not in r.stdout and "FAIL: no case was reproduced" in r.stdout


@needs_node
@pytest.mark.parametrize("kind", ["mapping", "levels"])
def test_golden_check_fails_on_a_kind_it_has_never_heard_of(tmp_path, kind):
    """A renamed kind must not turn into a silent skip, whether the port implemented it or not."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    renamed = 0
    for case in golden["cases"].values():
        if case["kind"] == kind:
            case["kind"] = kind + "_v2"
            renamed += 1
    assert renamed
    path = tmp_path / "renamed.json"
    path.write_text(json.dumps(golden), encoding="utf-8")
    r = check(golden=path)
    assert r.returncode != 0 and "PASS" not in r.stdout
    assert f"FAIL: unknown kind {kind}_v2 ({renamed} cases)" in r.stdout


@needs_node
@pytest.mark.parametrize(("old", "new", "caught_in"), [
    # a colour map that gives NaN at and below zero light, where the vectors expect exactly 0
    ("    if (Number.isNaN(y)) return [NaN, NaN, NaN];\n",
     "    if (Number.isNaN(y) || y <= 0) return [NaN, NaN, NaN];\n", "cmap_fire_4"),
    # a log scale that gives NaN at and below zero, where the vectors expect position 0
    ("kind === 'log' ? v => (v > 0 ? Math.log10(v) : -Infinity)", "kind === 'log' ? v => Math.log10(v)", "scale_log"),
])
def test_golden_check_counts_nan_as_a_mismatch(tmp_path, old, new, caught_in):
    r = check(js=changed_port(tmp_path, old, new))
    assert r.returncode != 0 and "PASS" not in r.stdout
    assert re.search(rf"^FAIL {caught_in}: .*NaN", r.stdout, re.MULTILINE), r.stdout


# ---- what is checked is the shipped port

@needs_node
def test_the_checker_has_no_implementation_of_its_own():
    checker = (JS / "check_golden.mjs").read_text(encoding="utf-8")
    assert "node:crypto" not in checker and "createHash" not in checker
    port = (JS / "udeepcore.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import\b|\brequire\(|\bprocess\.|\bBuffer\.(from|alloc|concat)\b", port, re.MULTILINE)


@needs_node
@pytest.mark.parametrize(("old", "new", "caught_in"), [
    ("ascii(`udeep-rgb8-1;${height}x${width};`)", "ascii(`udeep-rgb8-1;${width}x${height};`)", "digest_pixels_2x3"),
    ("else view.setFloat64(8 * i, v + 0, true);", "else view.setFloat64(8 * i, v, true);", "digest_data"),
    ("Boolean(v) && !around(eroded, y, x, false)", "Boolean(v) && !around(eroded, y, x, true)", "mask_isolated"),
    ("offset = n(1 / 64);", "offset = n(1 / 32);", "iso21496_block"),
    ("padded[bytes.length] = 0x80;", "padded[bytes.length] = 0x40;", "table_fire/1@2"),
    ("return { values, nodes: values.length / 6, headroom };", "return { values, nodes: values.length / 3, headroom };",
     "table_ice/1@16"),
])
def test_a_change_to_the_shipped_port_fails_the_golden_check(tmp_path, old, new, caught_in):
    """Digests, the isolated-pixel rule, the ISO block and the tables are checked in udeepcore.js itself."""
    r = check(js=changed_port(tmp_path, old, new))
    assert r.returncode != 0 and f"FAIL {caught_in}:" in r.stdout, r.stdout


@needs_node
def test_the_port_runs_where_there_is_neither_node_nor_a_browser(tmp_path):
    """udeepcore.js is a plain script: in a bare context (no Buffer, no process, no crypto, no require) it still
    loads, and its digests agree with the golden vectors."""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    script = tmp_path / "bare.mjs"
    script.write_text(
        "import vm from 'node:vm';\nimport { readFileSync } from 'node:fs';\n"
        f"const source = readFileSync({json.dumps(str(JS / 'udeepcore.js'))}, 'utf8');\n"
        "const context = vm.createContext({});\nvm.runInContext(source, context);\n"
        "const out = vm.runInContext(`({ globals: [typeof Buffer, typeof process, typeof crypto, typeof require],"
        " pixels: UDeepCore.digestPixels(new Uint8Array([0, 1, 2, 253, 254, 255, 10, 20, 30, 40, 50, 60]), 2, 2),"
        " data: UDeepCore.digestData([2, 3], [0, -0, 1.5, NaN, -2.25, 1e300]),"
        " empty: UDeepCore.sha256(new Uint8Array(0)) })`, context);\n"
        "console.log(JSON.stringify(out));\n", encoding="utf-8")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout)
    assert out["globals"] == ["undefined"] * 4
    assert out["pixels"] == cases["digest_pixels"]["out"] and out["data"] == cases["digest_data"]["out"]
    assert out["empty"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@needs_node
def test_the_ports_sha256_is_sha256(tmp_path):
    import hashlib
    lengths = [0, 1, 3, 55, 56, 57, 63, 64, 65, 119, 120, 128, 1000, 98328]
    got = run_js(tmp_path, f"const lengths = {lengths};\n"
                 "console.log(JSON.stringify(lengths.map(n => H.sha256(Uint8Array.from({ length: n }, (_, i) => (i * 7 + n) % 256)))));\n")
    assert got == [hashlib.sha256(bytes((i * 7 + n) % 256 for i in range(n))).hexdigest() for n in lengths]


# ---- what the port refuses

@needs_node
def test_make_table_takes_only_bytes_of_a_whole_table(tmp_path):
    out = run_js(tmp_path, """
import { readFileSync } from 'node:fs';
const whole = new ArrayBuffer(48), inside = new Uint8Array(100).subarray(4, 52);
new DataView(inside.buffer).setFloat32(4 + 4, 0.25, true);
const table = H.makeTable(whole, 4);
console.log(JSON.stringify({
  floats: thrown(() => H.makeTable(new Float32Array(24).fill(0.5), 4)),
  numbers: thrown(() => H.makeTable(Array(48).fill(0), 4)),
  view: thrown(() => H.makeTable(new DataView(whole), 4)),
  nothing: thrown(() => H.makeTable(undefined, 4)),
  text: thrown(() => H.makeTable('0'.repeat(48), 4)),
  not_24: thrown(() => H.makeTable(new Uint8Array(28), 4)),
  not_4: thrown(() => H.makeTable(new Uint8Array(50), 4)),
  one_node: thrown(() => H.makeTable(new Uint8Array(24), 4)),
  empty: thrown(() => H.makeTable(new Uint8Array(0), 4)),
  nodes: table.nodes, a_copy: table.values.buffer !== whole, cmap: H.cmap(0.5, table),
  offset: H.makeTable(inside, 4).values[1],
  shipped: H.makeTable(readFileSync(SHIPPED), 4).nodes,
}));
""".replace("SHIPPED", json.dumps(str(TABLES / "fire-1-h4.f32"))))
    assert (out["floats"], out["numbers"], out["view"], out["nothing"], out["text"]) == ("TypeError",) * 5
    assert (out["not_24"], out["not_4"], out["one_node"], out["empty"]) == ("RangeError",) * 4
    assert (out["nodes"], out["a_copy"], out["cmap"], out["offset"], out["shipped"]) == (2, True, [0, 0, 0], 0.25, 4097)


@needs_node
def test_the_digests_refuse_what_is_not_theirs(tmp_path):
    out = run_js(tmp_path, """
console.log(JSON.stringify({
  floats_as_pixels: thrown(() => H.digestPixels(new Float32Array(12), 2, 2)),
  numbers_as_pixels: thrown(() => H.digestPixels([0, 1, 2], 1, 1)),
  wrong_size: thrown(() => H.digestPixels(new Uint8Array(12), 2, 3)),
  null_as_data: thrown(() => H.digestData([2], [1, null])),
  wrong_count: thrown(() => H.digestData([2, 2], [1, 2, 3])),
  ragged: thrown(() => H.isolated([[true, false], [true]])),
  not_finite: thrown(() => H.iso21496Metadata(NaN, 2, 2)),
  typed: H.digestData([2, 3], new Float64Array([0, -0, 1.5, NaN, -2.25, 1e300])),
}));
""")
    assert (out["floats_as_pixels"], out["numbers_as_pixels"], out["null_as_data"]) == ("TypeError",) * 3
    assert (out["wrong_size"], out["wrong_count"], out["ragged"], out["not_finite"]) == ("RangeError",) * 4
    assert out["typed"] == json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]["digest_data"]["out"]


@needs_node
def test_a_mapping_record_the_reference_refuses_is_refused_here_too(tmp_path):
    """Only where the port already checked: the form above the knee, and a knee the curve cannot draw."""
    out = run_js(tmp_path, """
const m = { stretch: 'linear', range_low: 0, range_high: 1, knee: 0.5, reference_headroom: 4, stretch_width: 1,
  stretch_offset: 0, above_knee: 'hermite; exit=1', colour_map: 'grey' };
const light = change => thrown(() => H.light({ ...m, ...change }, 0.7));
console.log(JSON.stringify({
  as_it_is: light({}), exit_2: light({ above_knee: 'hermite; exit=2' }), knee_at_top: light({ knee: 1 }),
  other_form: light({ above_knee: 'spline; exit=1' }), trailing: light({ above_knee: 'hermite; exit=1 or so' }),
  two_dots: light({ above_knee: 'hermite; exit=1.2.3' }), too_steep: light({ above_knee: 'hermite; exit=3.5' }),
  knee_at_bottom: light({ knee: 1e-9 }), knee_under_top: light({ knee: 1 - 1e-9 }),
  position: thrown(() => H.kneePosition({ ...m, knee: 1e-9 })),
}));
""")
    assert (out["as_it_is"], out["exit_2"], out["knee_at_top"]) == (None, None, None)
    assert {out[k] for k in ("other_form", "trailing", "two_dots", "too_steep", "knee_at_bottom", "knee_under_top",
                             "position")} == {"Error"}


def test_pages_ship_the_canonical_core():
    copies = list((ROOT / "docs").rglob("udeepcore.js"))
    if not copies:
        pytest.skip("no site page ships udeepcore.js yet")
    canonical = (JS / "udeepcore.js").read_bytes()
    for c in copies:
        assert c.read_bytes() == canonical, f"{c} differs from udeep/js/udeepcore.js; re-run tools/build_site.py"


@needs_node
def test_the_ports_uniform_block_is_the_one_the_python_host_packs(tmp_path):
    """A browser viewer hands the shader the block this port builds. It must be, byte for byte, the block
    udeep.gpu packs for the same mapping record, or the viewer would draw another mapping than the one declared."""
    from udeep import Mapping, gpu
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    records = {name: c["args"]["mapping"] for name, c in cases.items() if c["kind"] == "mapping"}
    records["kelvin"] = {**records["mapping_linear_grey"], "range_low": 273.15, "range_high": 303.15, "knee": 290.0}
    assert len(records) == 9
    out = run_js(tmp_path, "const records = " + json.dumps(records) + ";" + """
const hex = b => Array.from(b, v => v.toString(16).padStart(2, '0')).join('');
console.log(JSON.stringify(Object.fromEntries(Object.entries(records).map(([name, m]) =>
  [name, hex(H.uniformBlock(m, m.colour_map === 'grey' ? 0 : 4097))]))));
""")
    for name, record in records.items():
        assert out[name] == gpu._pack(Mapping(**record)).hex(), name
    assert len(set(out.values())) == len(out)                            # the cases do differ

