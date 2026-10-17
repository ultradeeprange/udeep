"""The Rust core built as WebAssembly, called in node through its binding (udeepwasm.js), as a page would call it.

The module must reproduce every golden case through its C interface, and what it writes must be what the Python
reference reads: a 16-bit PQ PNG with the reference's codes, a gain-map JPEG the validator passes. The checker
(check_golden_wasm.mjs) is itself under test, as the JavaScript port's is: it must fail when it has checked nothing,
when it meets a kind of case it has never heard of, and when the binding hands the core the wrong bytes.

Skipped without node, cargo, or the wasm32-unknown-unknown target (rustup target add wasm32-unknown-unknown).
"""
import collections
import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from udeep import core

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
JS = ROOT / "src" / "udeep" / "js"
GOLDEN = ROOT / "src" / "udeep" / "golden.json"
TABLES = ROOT / "src" / "udeep" / "tables"
NODE, CARGO, RUSTC = shutil.which("node"), shutil.which("cargo"), shutil.which("rustc")
TARGET = "wasm32-unknown-unknown"

# What the C interface reaches: every kind. A kind given no function there would go in NOT_IN_THE_INTERFACE.
REACHED = {"scale", "tone", "pq_encode", "srgb_oetf", "cmap", "table", "mapping", "levels", "mask_isolated",
           "digest_data", "digest_pixels", "xmp", "caption", "iso21496"}
NOT_IN_THE_INTERFACE: set[str] = set()
IN_PART: set[str] = set()               # a kind the interface would give only some outputs of; none now


def target_installed() -> bool:
    if RUSTC is None:
        return False
    found = subprocess.run([RUSTC, "--print", "target-libdir", "--target", TARGET], capture_output=True, text=True,
                           check=False)
    return found.returncode == 0 and Path(found.stdout.strip()).is_dir()


pytestmark = pytest.mark.skipif(NODE is None or CARGO is None or not target_installed(),
                                reason=f"needs node, cargo and the {TARGET} target")


@pytest.fixture(scope="session")
def wasm() -> Path:
    built = subprocess.run([CARGO, "build", "--release", "--target", TARGET, "--features", "jpeg,png"], cwd=CORE,
                           capture_output=True, text=True, check=False)
    assert built.returncode == 0, built.stderr[-2000:]
    return CORE / "target" / TARGET / "release" / "udeep_core.wasm"


@pytest.fixture(autouse=True)
def reference_only():
    """The Python side of these tests is the NumPy and Pillow reference, whatever core this machine has."""
    was = core.enabled()
    core.enable(False)
    yield
    core.enable(was)


def check(wasm: Path, golden: Path = GOLDEN, tables: Path = TABLES, js: Path = JS) -> subprocess.CompletedProcess:
    return subprocess.run([NODE, str(js / "check_golden_wasm.mjs"), str(golden), str(tables), str(wasm)],
                          capture_output=True, text=True, check=False)


def kinds() -> collections.Counter:
    return collections.Counter(c["kind"] for c in json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"].values())


def changed_binding(tmp_path: Path, old: str, new: str) -> Path:
    """A copy of the binding and its checker with one piece of udeepwasm.js replaced."""
    js = tmp_path / "js"
    js.mkdir()
    source = (JS / "udeepwasm.js").read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    (js / "udeepwasm.js").write_text(source.replace(old, new), encoding="utf-8", newline="\n")
    shutil.copy(JS / "check_golden_wasm.mjs", js / "check_golden_wasm.mjs")
    return js


def run_js(wasm: Path, tmp_path: Path, body: str) -> dict:
    """Run `body` in node and return the one line of JSON it prints. It has `core` (the binding, loaded), `raw`
    (the module's own exports, a second instance), `thrown`, and `doubles` and `floats` to read a file of them."""
    script = tmp_path / "probe.mjs"
    script.write_text(
        "import { readFileSync, writeFileSync } from 'node:fs';\n"
        f"import {json.dumps((JS / 'udeepwasm.js').as_uri())};\n"
        f"const wasm = readFileSync({json.dumps(str(wasm))});\n"
        "const core = await globalThis.UDeepWasm.load(wasm);\n"
        "const raw = (await WebAssembly.instantiate(await WebAssembly.compile(wasm), {})).exports;\n"
        "const thrown = f => { try { f(); return null; } catch (e) { return [e.constructor.name, e.code ?? null]; } };\n"
        "const whole = path => { const b = readFileSync(path); return b.buffer.slice(b.byteOffset, b.byteOffset + b.length); };\n"
        "const doubles = path => new Float64Array(whole(path));\n"
        "const save = (path, array) => writeFileSync(path, new Uint8Array(array.buffer, array.byteOffset, array.byteLength));\n"
        + body, encoding="utf-8")
    r = subprocess.run([NODE, str(script)], capture_output=True, text=True, check=False, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout)


# ---- the module

def test_the_module_imports_nothing_and_exports_every_function_of_the_header(wasm, tmp_path):
    out = run_js(wasm, tmp_path, """
const module = new WebAssembly.Module(wasm);
console.log(JSON.stringify({ imports: WebAssembly.Module.imports(module).length, version: core.version, has: core.has,
  functions: WebAssembly.Module.exports(module).filter(e => e.kind === 'function').map(e => e.name) }));
""")
    header = (CORE / "include" / "udeep_core.h").read_text(encoding="utf-8")
    declared = set(re.findall(r"\b(udeep_\w+)\(", re.sub(r"/\*.*?\*/", "", header, flags=re.DOTALL)))
    assert out["imports"] == 0 and out["version"] == 300 and out["has"] == {"jpeg": True, "png": True}
    assert set(out["functions"]) == declared and {"udeep_buffer_alloc", "udeep_buffer_free"} <= declared


def test_buffers_are_zeroed_aligned_and_null_for_nothing(wasm, tmp_path):
    out = run_js(wasm, tmp_path, """
const at = [1, 7, 8, 24, 1000003].map(size => {
  const p = raw.udeep_buffer_alloc(size) >>> 0, bytes = new Uint8Array(raw.memory.buffer, p, size);
  const zeroed = bytes.every(b => b === 0);
  bytes.fill(255); raw.udeep_buffer_free(p, size);
  return [p % 8, p !== 0, zeroed];
});
raw.udeep_buffer_free(0, 16);                                       // null is ignored
console.log(JSON.stringify({ at, nothing: raw.udeep_buffer_alloc(0) }));
""")
    assert out["at"] == [[0, True, True]] * 5 and out["nothing"] == 0


def test_a_call_that_asks_for_threads_runs_in_the_callers_thread_with_the_same_result(wasm, tmp_path):
    """There are no threads in WebAssembly: starting one would stop the module. Enough values to be worth eight
    threads anywhere else must come back, and be what the plain loop gives."""
    out = run_js(wasm, tmp_path, """
const n = 300001, record = raw.udeep_buffer_alloc(64), x = raw.udeep_buffer_alloc(8 * n), a = raw.udeep_buffer_alloc(8 * n),
  b = raw.udeep_buffer_alloc(8 * n);
const view = new DataView(raw.memory.buffer);
[0, 20, 1.2, 4, 0.1, 0, 1].forEach((v, i) => view.setFloat64(record + 8 * i, v, true));
view.setUint32(record + 56, 2, true);                               // asinh
const values = new Float64Array(raw.memory.buffer, x, n);
for (let i = 0; i < n; i++) values[i] = (i % 1009 === 0) ? NaN : -1 + 22 * ((i * 7919) % n) / n;
const status = [raw.udeep_light(record, x, a, n), raw.udeep_light_parallel(record, x, b, n, 8),
  raw.udeep_light_parallel(record, x, b, n, 0), raw.udeep_sdr_light_parallel(record, x, x, n, 8)];
const one = new Float64Array(raw.memory.buffer, a, n), eight = new Float64Array(raw.memory.buffer, b, n);
console.log(JSON.stringify({ status, same: one.every((v, i) => Object.is(v, eight[i])), lit: one.filter(v => v > 1).length }));
""")
    assert out["status"] == [0, 0, 0, 0] and out["same"] and out["lit"] > 1000


# ---- the golden vectors

def test_the_wasm_core_reproduces_every_golden_case(wasm):
    r = check(wasm)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout
    found = kinds()
    assert set(found) == REACHED | NOT_IN_THE_INTERFACE, "golden.json has a kind this test has not been told about"
    assert int(re.search(r"^(\d+) cases reproduced", r.stdout, re.MULTILINE).group(1)) == sum(found[k] for k in REACHED) > 0
    in_part = json.loads(re.search(r"of them in part \(only what the interface returns\): (\{.*\})", r.stdout).group(1))
    assert in_part == {kind: found[kind] for kind in sorted(IN_PART)}
    outside = json.loads(re.search(r"not reachable through the C interface: (\{.*\})", r.stdout).group(1))
    assert outside == {kind: found[kind] for kind in NOT_IN_THE_INTERFACE}


def test_the_check_catches_a_changed_map(wasm, tmp_path):
    """The check is not vacuous: a 1 % change to one colour-map table must fail it."""
    tables = tmp_path / "tables"
    shutil.copytree(TABLES, tables)
    file = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]["cmap_fire_4"]["args"]["table"]
    (np.fromfile(tables / file, dtype="<f4") * np.float32(1.01)).tofile(tables / file)
    r = check(wasm, tables=tables)
    assert r.returncode != 0 and "FAIL cmap_fire_4" in r.stdout


def test_the_check_fails_when_it_has_reproduced_nothing_or_meets_an_unknown_kind(wasm, tmp_path):
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"format": 2, "cases": {}}), encoding="utf-8")
    r = check(wasm, golden=empty)
    assert r.returncode != 0 and "PASS" not in r.stdout and "FAIL: no case was reproduced" in r.stdout
    renamed = sum(c["kind"] == "levels" for c in golden["cases"].values())
    for case in golden["cases"].values():
        if case["kind"] == "levels":
            case["kind"] = "levels_v2"
    path = tmp_path / "renamed.json"
    path.write_text(json.dumps(golden), encoding="utf-8")
    r = check(wasm, golden=path)
    assert r.returncode != 0 and "PASS" not in r.stdout and f"FAIL: unknown kind levels_v2 ({renamed} cases)" in r.stdout


@pytest.mark.parametrize(("old", "new", "caught_in"), [
    ("v.setUint32(at + 56, code, true);", "v.setUint32(at + 60, code, true);", "mapping_log_ice"),       # the stretch
    (".forEach((number, i) => v.setFloat64(at + 8 * i, number, true));",
     ".forEach((number, i) => v.setFloat64(at + 8 * i, number, false));", "mapping_linear_grey"),          # byte order
    ("v.setUint32(at + 104 + 8 * i, data, true); v.setUint32(at + 108 + 8 * i, length, true);",
     "v.setUint32(at + 104 + 8 * i, data, true); v.setUint32(at + 108 + 8 * i, length - 1, true);", "xmp_description"),
    ("v.setBigUint64(at + 88, BigInt(d.masked ?? 0), true);", "v.setBigUint64(at + 96, BigInt(d.masked ?? 0), true);",
     "caption"),
    ("new Float32Array(x.memory.buffer, at, table.values.length).set(table.values);",
     "new Float32Array(x.memory.buffer, at, table.values.length).set(table.values.subarray(3));", "cmap_ice_4"),
    ("const code = { linear: 0, log: 1, asinh: 2 }[kind];", "const code = { linear: 0, log: 2, asinh: 1 }[kind];",
     "scale_log"),
    ("check(x.udeep_iso21496_block(gainMapMin, gainMapMax, hdrCapacityMax, out));",
     "check(x.udeep_iso21496_block(gainMapMax, gainMapMin, hdrCapacityMax, out));", "iso21496_block"),
    ("tone: (t, knee, headroom, exitSlope = 1) => staged('udeep_tone', t, [knee, headroom, exitSlope]),",
     "tone: (t, knee, headroom, exitSlope = 1) => staged('udeep_tone', t, [knee, headroom, 1]),",
     "rolloff_0.8_2.0"),
    ("check(x[name](record, at, nodes, scaledBy, samples, NOISE[model], parameter ?? 0, out, out + 8, ...rest));",
     "check(x[name](record, at, nodes, scaledBy, samples, NOISE[model], (parameter ?? 0) * 2, out, out + 8, ...rest));",
     "levels_linear_grey"),
    ("NOISE[model], parameter ?? 0, pq16 ? 1 : 0,", "NOISE[model], (parameter ?? 0) * 2, pq16 ? 1 : 0,",
     "levels_linear_grey"),
    ("failingFraction: number(40), limit: number(48),", "failingFraction: number(48), limit: number(40),",
     "levels_linear_grey"),
])
def test_a_change_to_the_binding_fails_the_golden_check(wasm, tmp_path, old, new, caught_in):
    """What the check compares has passed through udeepwasm.js as it is shipped: the records it lays out, the
    table it copies in, the arguments it passes."""
    r = check(wasm, js=changed_binding(tmp_path, old, new))
    assert r.returncode != 0 and f"FAIL {caught_in}" in r.stdout, r.stdout


def test_the_binding_states_the_profile_version_and_the_sizes_the_reference_states():
    from udeep import metadata
    source = (JS / "udeepwasm.js").read_text(encoding="utf-8")
    assert f"const VERSION = '{metadata.VERSION}', IMAGE_STATE = '{metadata.IMAGE_STATE}';" in source
    texts = re.findall(r"'(\w+)'", re.search(r"const TEXTS = \[(.*?)\];", source, re.DOTALL).group(1))
    header = (CORE / "include" / "udeep_core.h").read_text(encoding="utf-8")
    declared = re.sub(r"/\*.*?\*/", "", header.partition("uint64_t derived_masked;")[2].partition("} udeep_declaration;")[0],
                      flags=re.DOTALL)
    assert texts == re.findall(r"\b(?!udeep_text\b)(\w+)\b", declared)
    # on wasm32 a pointer and a size are four bytes each: the mapping, five 8-byte numbers, a pair for each text
    assert f"DECLARATION_BYTES = {64 + 5 * 8 + 8 * len(texts)}," in source
    assert not re.search(r"^\s*import\b|\brequire\(|\bprocess\.|\bBuffer\.(from|alloc|concat)\b", source, re.MULTILINE)


def test_what_the_golden_vectors_leave_out_of_the_binding_agrees_with_the_reference(wasm, tmp_path):
    """Position back to data, the sRGB signal back to light, and the levels of a 16-bit PQ file."""
    from udeep import Mapping
    from udeep.metadata import data_steps_pq16, rounding_to_noise
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    records = {name: c["args"]["mapping"] for name, c in cases.items() if c["kind"] == "mapping"}
    fire = Mapping.from_stretch({"kind": "linear", "lo": 0.0, "hi": 4095.0}, 1000.0, 4.0, "fire")
    (tmp_path / "fire.f32").write_bytes(fire.table.to_bytes())
    t = np.linspace(0.0, 1.0, 41)
    out = run_js(wasm, tmp_path, f"const records = {json.dumps(records)}, t = {json.dumps(t.tolist())};"
                 f"const fire = {json.dumps(dataclasses.asdict(fire))};" + """
const signal = Array.from({ length: 101 }, (_, i) => i / 100);
const table = globalThis.UDeepWasm.makeTable(readFileSync('fire.f32'), 4);
console.log(JSON.stringify({
  value_at: Object.fromEntries(Object.entries(records).map(([name, m]) => [name, Array.from(core.valueAt(m, t))])),
  round_trip: Array.from(core.srgbDecode(core.srgbEncode(signal))), signal,
  grey: core.levelsPq16({ ...fire, colour_map: 'grey' }, null, 203, 1 << 18, ['constant', 3]),
  fire: core.levelsPq16(fire, table, 203, 1 << 18, ['poisson', 1]),
  unknown: core.levelsPq16(fire, table, 203, 1 << 18),
}));
""")
    for name, m in records.items():
        assert np.allclose(out["value_at"][name], Mapping(**m).value_at(t), rtol=1e-12, atol=1e-12), name
    assert np.allclose(out["round_trip"], out["signal"], rtol=0.0, atol=1e-12)
    for key, mapping, noise in (("grey", Mapping(**{**dataclasses.asdict(fire), "colour_map": "grey"}), ("constant", 3.0)),
                                ("fire", fire, ("poisson", 1.0))):
        steps = data_steps_pq16(mapping, samples=1 << 18)
        found = out[key]
        assert abs(found["count"] - len(steps[1])) <= max(2, len(steps[1]) // 2000), key   # a level at a rounding tie
        assert found["maxDataStep"] == pytest.approx(float(steps[1].max()), rel=0.02), key
        assert found["roundingToNoise"] == pytest.approx(min(rounding_to_noise(mapping, noise, steps=steps)["worst"], 1e9),
                                                         rel=0.02), key
    assert out["unknown"]["roundingToNoise"] is None and out["unknown"]["count"] == out["fire"]["count"]


# ---- what is refused

def test_what_the_core_refuses_comes_back_as_an_error_with_its_code(wasm, tmp_path):
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    good = cases["mapping_linear_grey"]["args"]["mapping"]
    declared = cases["caption"]["args"]["declaration"]
    out = run_js(wasm, tmp_path, f"const m = {json.dumps(good)}, d = {json.dumps(declared)};" + """
console.log(JSON.stringify({
  good: core.validate(m), knee: core.validate({ ...m, knee: 9000 }), range: core.validate({ ...m, range_high: -1000 }),
  not_finite: core.validate({ ...m, range_low: NaN }), headroom: core.validate({ ...m, reference_headroom: 0.5 }),
  light: thrown(() => core.light({ ...m, knee: 9000 }, [1])), stretch: thrown(() => core.light({ ...m, stretch: 'cubic' }, [1])),
  form: thrown(() => core.light({ ...m, above_knee: 'spline; exit=1' }, [1])),
  slope: thrown(() => core.light({ ...m, above_knee: 'hermite; exit=3.5' }, [1])),
  a_null: thrown(() => core.light(m, [1, null])), text: thrown(() => core.light(m, 'abc')),
  none: Array.from(core.light(m, [])), typed: Array.from(core.light(m, new Float32Array([240]))),
  table: thrown(() => core.colours([0.5], { values: new Float32Array(6), nodes: 1, headroom: 4 })),
  wrong_count: thrown(() => core.digestData([2, 2], [1, 2, 3])), null_as_data: thrown(() => core.digestData([2], [1, null])),
  wrong_size: thrown(() => core.digestPixels(new Uint8Array(12), 2, 3)),
  floats_as_pixels: thrown(() => core.digestPixels(new Float32Array(12), 2, 2)),
  no_unit: thrown(() => core.description({ ...d, data_unit: null })), no_rule: thrown(() => core.caption({ ...d, knee_rule: null })),
  model: thrown(() => core.description({ ...d, noise_model: 'gaussian' })),
  other_version: thrown(() => core.description({ ...d, version: '0.2' })),
  unit_as_number: thrown(() => core.description({ ...d, data_unit: 7 })),
  noise: thrown(() => core.levels(m, null, 4, 4096, ['gaussian', 1])),
  noise_parameter: thrown(() => core.levels(m, null, 4, 4096, ['constant', 0])),
  rgb_count: thrown(() => core.pngPq(new Float64Array(11), 2, 2)),
  // a declaration is written with white at 203 cd/m2 and no other (profile 9.1.1); a file without one, at any white
  white: thrown(() => core.pngPq(new Float64Array(12), 2, 2, { whiteNits: 240, xmp: new TextEncoder().encode(
    '<x:xmpmeta><rdf:Description xmlns:udeep="https://ultradeeprange.github.io/udeep/ns/0.3/"/></x:xmpmeta>') })),
  own_screen: core.pngPq(new Float64Array(12), 2, 2, { whiteNits: 240 }).length > 0,
  not_a_png: thrown(() => core.readPngPq(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]))),
  not_a_jpeg: thrown(() => core.readGainmap(new Uint8Array(64), 2)),
  infinite: thrown(() => core.gainmapJpeg([0.5, 0.5, 0.5], [Infinity, 1, 1], 1, 1, 4)),
  // a table that says it holds 2^31 numbers: 8 GiB, which a 32-bit size would carry as 0
  wraps: thrown(() => core.colours([0.5], { values: { length: 2 ** 31 }, nodes: 2 ** 30, headroom: 4 })),
  kind: thrown(() => core.scale([1], 'cubic', 0, 1)), log_from_zero: thrown(() => core.scale([1], 'log', 0, 10)),
  width: thrown(() => core.scale([1], 'asinh', 0, 1, 0)), knee_zero: thrown(() => core.tone([0.5], 0, 4)),
  steep: thrown(() => core.tone([0.5], 0.5, 4, 3.5)), no_headroom: thrown(() => core.tone([0.5], 0.5, undefined)),
  iso_nan: thrown(() => core.iso21496Metadata(NaN, 2, 2)), iso_large: thrown(() => core.iso21496Metadata(0, 2200, 2)),
  digest_text: thrown(() => core.digestBytes('abc')), empty: core.digestBytes(new Uint8Array(0)),
}));
""")
    assert (out["good"], out["knee"], out["range"], out["not_finite"], out["headroom"]) == (0, 4, 3, 10, 5)
    assert out["light"] == ["Error", 4] and out["slope"] == ["Error", 5] and out["table"] == ["Error", 7]
    assert out["stretch"] == out["form"] == out["other_version"] == out["noise"] == ["Error", None]
    assert out["a_null"] == out["text"] == out["null_as_data"] == out["floats_as_pixels"] == ["TypeError", None]
    assert out["unit_as_number"] == ["TypeError", None]
    assert out["wrong_count"] == out["wrong_size"] == out["rgb_count"] == ["RangeError", None]
    assert out["no_unit"] == out["no_rule"] == out["model"] == out["noise_parameter"] == out["white"] == ["Error", 11]
    assert out["own_screen"] is True
    assert out["none"] == [] and out["typed"] == [1.0]                         # the knee of that record is 240
    assert out["not_a_png"][0] == "Error" and out["not_a_png"][1] in (8, 9)
    assert out["not_a_jpeg"] == ["Error", 8] and out["infinite"] == ["Error", 8] and out["wraps"] == ["RangeError", None]
    assert out["kind"] == ["Error", None] and out["log_from_zero"] == ["Error", 3] and out["width"] == ["Error", 6]
    assert out["knee_zero"] == ["Error", 4] and out["steep"] == ["Error", 5] and out["no_headroom"] == ["Error", 10]
    assert out["iso_nan"] == ["Error", 10] and out["iso_large"] == ["Error", 8] and out["digest_text"] == ["TypeError", None]
    assert out["empty"] == "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_damaged_files_are_refused_with_a_code_and_never_stop_the_module(wasm, tmp_path):
    """Files come from anyone (D24). In WebAssembly a panic or a failed allocation is a trap that leaves the module
    unusable, so every damaged copy must come back as an image or as an error code, and the module must still work
    afterwards. Among them a PNG and a JPEG whose headers claim vast images: refused before room is made."""
    import importlib.util
    import zlib
    spec = importlib.util.spec_from_file_location("malformed", ROOT / "tests" / "test_malformed.py")
    malformed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(malformed)
    png, jpeg = malformed.seeds(tmp_path)
    vast = bytearray(png)
    vast[16:24] = (60000).to_bytes(4, "big") * 2                 # 60000 x 60000: 86 GB as float64, if believed
    vast[29:33] = zlib.crc32(bytes(vast[12:29])).to_bytes(4, "big")
    claims = bytearray(jpeg)
    at = claims.index(b"\xff\xc0")                              # the first frame header: height, then width
    claims[at + 5:at + 9] = (65535).to_bytes(2, "big") * 2
    rng = np.random.default_rng(20261014)
    files = [(bytes(vast), "png"), (bytes(claims), "jpeg")]
    files += [(malformed.damaged((png, jpeg)[i % 2], rng), ("png", "jpeg")[i % 2]) for i in range(1200)]
    offsets = np.cumsum([0] + [len(f) for f, _ in files]).tolist()
    (tmp_path / "damaged.bin").write_bytes(b"".join(f for f, _ in files))
    (tmp_path / "damaged.json").write_text(json.dumps([[offsets[i], len(f), kind] for i, (f, kind) in enumerate(files)]))
    good = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]["mapping_linear_grey"]["args"]["mapping"]
    out = run_js(wasm, tmp_path, f"const m = {json.dumps(good)};" + """
const all = new Uint8Array(whole('damaged.bin')), outcome = { read: 0, codes: {}, other: [] };
for (const [from, length, kind] of JSON.parse(readFileSync('damaged.json', 'utf8'))) {
  const file = all.subarray(from, from + length);
  try {
    const image = kind === 'png' ? core.readPngPq(file) : core.readGainmap(file, 4);
    if ((image.light ?? image.rgb).length !== image.width * image.height * 3) outcome.other.push('a short image');
    outcome.read++;
  } catch (e) {
    if (typeof e.code === 'number') outcome.codes[e.code] = (outcome.codes[e.code] ?? 0) + 1;
    else outcome.other.push(`${e.constructor.name}: ${e.message}`);
  }
}
const first = (kind, file) => thrown(() => (kind === 'png' ? core.readPngPq(file) : core.readGainmap(file, 4)));
const [vast, claims] = JSON.parse(readFileSync('damaged.json', 'utf8')).slice(0, 2);
console.log(JSON.stringify({ ...outcome, vast: first('png', all.subarray(vast[0], vast[0] + vast[1])),
  claims: first('jpeg', all.subarray(claims[0], claims[0] + claims[1])), still: Array.from(core.light(m, [240])) }));
""")
    assert out["other"] == [], out["other"][:5]
    assert set(out["codes"]) <= {"8", "9"} and sum(out["codes"].values()) > 300 and out["read"] > 0, out
    assert out["vast"] in (["Error", 8], ["Error", 9]) and out["claims"] == ["Error", 8]
    assert out["still"] == [1.0]                                 # the module still works


# ---- files: what the module writes, the reference reads, and the other way

def codes_in(path: Path) -> np.ndarray:
    """A 16-bit PNG's codes, read from the bytes by neither of the package's readers."""
    import struct
    import zlib
    raw = path.read_bytes()
    at, parts, size = 8, [], None
    while at < len(raw):
        length, tag = struct.unpack(">I4s", raw[at:at + 8])
        if tag == b"IHDR":
            size = struct.unpack(">II", raw[at + 8:at + 16])
        elif tag == b"IDAT":
            parts.append(raw[at + 8:at + 8 + length])
        at += 12 + length
    rows = np.frombuffer(zlib.decompress(b"".join(parts)), dtype=np.uint8).reshape(size[1], 1 + size[0] * 6)
    assert not rows[:, 0].any()                                  # the writers leave the rows unfiltered
    return rows[:, 1:].copy().view(">u2").reshape(size[1], size[0], 3)


def test_a_png_written_in_wasm_holds_the_reference_codes_and_each_reads_the_others_file(wasm, tmp_path):
    from udeep import read_png_pq, write_png_pq
    from udeep.export import pq16_codes
    rgb = np.random.default_rng(11).uniform(0.0, 4.0, (37, 53, 3))
    rgb[3, 4] = [0.0, 0.0, 0.0]; rgb[5, 6] = [4.0, 4.0, 4.0]; rgb[7, 8, 1] = np.nan
    rgb.astype("<f8").tofile(tmp_path / "rgb.bin")
    (tmp_path / "packet.xml").write_bytes(b"<x:xmpmeta>a packet</x:xmpmeta>")
    write_png_pq(rgb, tmp_path / "reference.png")
    out = run_js(wasm, tmp_path, """
const file = core.pngPq(doubles('rgb.bin'), 53, 37, { xmp: readFileSync('packet.xml') });
writeFileSync('wasm.png', file);
const own = core.readPngPq(file), other = core.readPngPq(readFileSync('reference.png'));
save('own.bin', own.light); save('other.bin', other.light);
console.log(JSON.stringify({ own: [own.width, own.height], other: [other.width, other.height] }));
""")
    assert out == {"own": [53, 37], "other": [53, 37]}
    raw = (tmp_path / "wasm.png").read_bytes()
    assert b"cICP" in raw and b"XML:com.adobe.xmp" in raw and b"a packet" in raw
    # as with the core built for this machine: its sum of three products is taken in another order than NumPy's
    off = codes_in(tmp_path / "wasm.png").astype(int) - pq16_codes(rgb).astype(int)
    assert np.abs(off).max() <= 1 and (off != 0).mean() < 1e-3
    for name, file in (("own", "wasm.png"), ("other", "reference.png")):         # the two readers, on each file
        light = np.fromfile(tmp_path / f"{name}.bin", dtype="<f8").reshape(37, 53, 3)
        assert np.allclose(light, read_png_pq(tmp_path / file), rtol=1e-9, atol=1e-12), name
    assert np.abs(read_png_pq(tmp_path / "wasm.png") - np.nan_to_num(rgb)).max() < 6e-4 * 5


def scene():
    """A fair SDR view and its HDR view of a small image: a ramp, a block above the knee, noise below it."""
    from udeep.view import hdr_view
    rng = np.random.default_rng(5)
    d = 20.0 + 25.0 * rng.random((72, 96)); d[20:50, 30:70] = np.linspace(55.0, 99.0, 40)[None, :]
    return d, hdr_view(d, "extend", 50.0, {"kind": "linear", "lo": 0.0, "hi": 100.0}, 4.0, "fire")


def test_a_gain_map_written_in_wasm_is_read_and_validated_by_python(wasm, tmp_path):
    """The independent check on both sides, as for the core built for this machine: the module's writer, and the
    declaration it serialises, against Python's reader and validator."""
    from udeep import read_gainmap_jpeg, read_metadata, write_gainmap_jpeg
    from udeep.metadata import Declaration, data_steps, description, digest_data, parse, to_xmp
    from udeep.validate import validate
    d, v = scene()
    m = v.mapping
    declared = Declaration(**dataclasses.asdict(m), data_unit="counts", knee_rule="preset; half of full scale",
                           max_data_step=float(data_steps(m)[1].max()), preset="extend", data_digest=digest_data(d))
    v.sdr.astype("<f8").tofile(tmp_path / "sdr.bin"); v.hdr.astype("<f8").tofile(tmp_path / "hdr.bin")
    fields = {k: value for k, value in dataclasses.asdict(declared).items() if value is not None}
    out = run_js(wasm, tmp_path, f"const d = {json.dumps(fields)};" + """
const element = core.description(d), sdr = doubles('sdr.bin'), hdr = doubles('hdr.bin');
const file = core.gainmapJpeg(sdr, hdr, 96, 72, 4, { description: element });
writeFileSync('wasm.jpg', file);
const shown = core.readGainmap(file, 4), base = core.readGainmap(file, 1);
console.log(JSON.stringify({ element, size: [shown.width, shown.height], peak: Math.max(...shown.rgb),
  base_peak: Math.max(...base.rgb), given: Math.max(...hdr) }));
""")
    assert out["element"] == description(declared).decode("utf-8")              # the module's own text of it
    path = tmp_path / "wasm.jpg"
    assert read_metadata(path) == parse(to_xmp(declared))
    report = validate(path, data=d)
    assert report.ok, str(report)
    assert [report.status(n) for n in ("metadata", "mapping", "headroom", "precision", "data link")] == ["pass"] * 5
    # on the decoder it was written with, the file reads nowhere above the brightest value it was given
    assert out["size"] == [96, 72] and out["peak"] <= out["given"] * (1 + 1e-6) and out["base_peak"] <= 1.0 + 1e-6
    base, full = read_gainmap_jpeg(path, 1.0).astype(float), read_gainmap_jpeg(path, 4.0).astype(float)
    reference = tmp_path / "python.jpg"; write_gainmap_jpeg(v.sdr, v.hdr, reference, headroom=4.0)
    reference_error = np.abs(read_gainmap_jpeg(reference, 1.0).astype(float) - v.sdr).mean()
    assert np.abs(base - v.sdr).mean() < 1.5 * reference_error, (np.abs(base - v.sdr).mean(), reference_error)
    lifted = v.hdr.max(-1) > 1.05
    assert np.median(np.abs(full.max(-1)[lifted] / v.hdr.max(-1)[lifted] - 1.0)) < 0.03
    assert full.max() <= 4.0 * 1.05


def test_wasm_reads_the_reference_writers_gain_map_as_the_reference_reads_it(wasm, tmp_path):
    from udeep import read_gainmap_jpeg, write_gainmap_jpeg
    _d, v = scene()
    write_gainmap_jpeg(v.sdr, v.hdr, tmp_path / "python.jpg", headroom=4.0)
    out = run_js(wasm, tmp_path, """
const shown = core.readGainmap(readFileSync('python.jpg'), 4), base = core.readGainmap(readFileSync('python.jpg'), 1);
save('shown.bin', shown.rgb); save('base.bin', base.rgb);
console.log(JSON.stringify([shown.width, shown.height]));
""")
    assert out == [96, 72]
    for name, boost in (("shown", 4.0), ("base", 1.0)):
        ours = np.fromfile(tmp_path / f"{name}.bin", dtype="<f4").reshape(72, 96, 3).astype(float)
        theirs = read_gainmap_jpeg(tmp_path / "python.jpg", boost).astype(float)
        # two JPEG decoders: a code or two apart at about one pixel in a hundred (measured: 1.1 %, mean 1e-4 of
        # white, worst 0.13 at a boost of 4), and no more
        diff = np.abs(ours - theirs)
        assert np.mean(diff > 1e-6) < 0.05 and diff.mean() < 5e-4 * boost and diff.max() < 0.05 * boost, name


def test_a_figure_from_four_renders_in_wasm_lifts_only_what_an_hdr_image_alone_made(wasm, tmp_path):
    """The four renders of a figure whose left half is an HDR image and whose right half is ordinary drawing."""
    from udeep import read_gainmap_jpeg
    from udeep.export import srgb_oetf
    _d, v = scene()
    code = lambda rgb: np.round(srgb_oetf(np.clip(rgb, 0.0, 1.0)) * 255.0).astype(np.uint8)
    image = np.zeros((72, 96), bool); image[:, :48] = True
    furniture = np.full((72, 96, 3), 200, np.uint8)
    base = np.where(image[..., None], code(v.sdr), furniture)
    scaled = np.where(image[..., None], code(v.hdr / 4.0), furniture)
    black = np.where(image[..., None], np.uint8(0), furniture)
    white = np.where(image[..., None], np.uint8(255), furniture)
    for name, render in (("base", base), ("scaled", scaled), ("black", black), ("white", white)):
        render.tofile(tmp_path / f"{name}.bin")
    out = run_js(wasm, tmp_path, """
const [base, scaled, black, white] = ['base', 'scaled', 'black', 'white'].map(name => new Uint8Array(whole(name + '.bin')));
const made = core.gainmapFromRenders(base, scaled, black, white, 96, 72, 4);
writeFileSync('renders.jpg', made.bytes);
console.log(JSON.stringify({ lifted: made.lifted }));
""")
    # the reference's rule, on the same renders
    from udeep.mpl import _combined
    ordinary, figure = _combined(base, scaled, black, white, 4.0)
    raised = np.any(figure != ordinary, axis=-1)
    assert out["lifted"] == int(raised.sum()) > 0 and not raised[:, 48:].any()
    full = read_gainmap_jpeg(tmp_path / "renders.jpg", 4.0).astype(float)
    assert full[:, 56:].max() <= 1.05 and full.max() <= 4.0 * 1.05           # the drawing is not lifted
    far = raised & (figure.max(-1) > 1.5)
    assert far.sum() > 100 and np.median(np.abs(full.max(-1)[far] / figure.max(-1)[far] - 1.0)) < 0.03
