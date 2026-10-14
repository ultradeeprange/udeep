// node check_golden_wasm.mjs <golden.json> <tables dir> <udeep_core.wasm> : the Rust core, built as WebAssembly
// and called as a page would call it (udeepwasm.js), must reproduce the golden vectors (format 2).
//
// Everything that is compared is worked out inside the module: this file only reads the vectors, calls the
// binding and compares, each case to its own tolerance (or exactly). What it can reach is what the core's C
// interface offers, which is every kind of case. A kind given no function there would be listed by name as not
// reachable (NOT_IN_THE_INTERFACE; none is), and checked by the core's own tests (core/tests/golden.rs). A levels
// case is compared in the two numbers the interface returns, the widest level and the worst ratio, and is counted
// apart for that reason.
//
// It fails, with exit status 1, when a case is out; when it reproduced no case at all; and when it meets a kind
// of case it has never heard of. It prints how many cases it reproduced, for the test that runs it to hold it to
// (tests/test_udeep_wasm.py).
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import './udeepwasm.js';

const { load, makeTable } = globalThis.UDeepWasm;

const NOT_IN_THE_INTERFACE = [];
const IN_PART = { levels: ['max_data_step', 'rounding_to_noise'] };   // the outputs the interface returns

const golden = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const tablesDir = process.argv[3];
if (golden.format !== 2) { console.log(`FAIL: golden format ${golden.format}, this check reads format 2`); process.exit(1); }
const core = await load(readFileSync(process.argv[4]));
const num = v => (v === null ? NaN : v);                           // the vectors write NaN as null
const table = (file, headroom) => makeTable(readFileSync(join(tablesDir, file)), headroom);
const tableOf = c => (c.tables.length ? table(c.tables[0], c.args.mapping.reference_headroom) : null);
const list = a => Array.from(a);
const triples = a => Array.from({ length: a.length / 3 }, (_, i) => list(a.subarray(3 * i, 3 * i + 3)));

const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');

const KINDS = {
  scale: a => list(core.scale(a.x.map(num), a.kind, a.lo, a.hi, a.width)),
  tone: a => list(core.tone(a.t.map(num), a.knee, a.headroom, a.exit_slope ?? 1)),
  pq_encode: a => list(core.pqEncode(a.nits)),
  srgb_oetf: a => list(core.srgbEncode(a.v)),
  table: a => { const bytes = readFileSync(join(tablesDir, a.file));
    return { sha256: core.digestBytes(bytes), nodes: makeTable(bytes, a.headroom).nodes, bytes: bytes.length }; },
  iso21496: a => hex(core.iso21496Metadata(a.gain_map_min, a.gain_map_max, a.hdr_capacity_max)),
  cmap: a => triples(core.colours(a.y.map(num), table(a.table, a.headroom))),
  mapping: (a, c) => { const m = a.mapping, x = a.x.map(num), y = core.light(m, x);
    return { knee_position: core.kneePosition(m), position: list(core.position(m, x)), light: list(y),
      sdr_light: list(core.sdrLight(m, x)), value_of_light: list(core.valueOfLight(m, y)),
      colours: triples(core.colours(y, tableOf(c))) }; },
  levels: (a, c) => { const found = core.levels(a.mapping, tableOf(c), a.mapping.reference_headroom, a.samples, a.noise);
    return { max_data_step: found.maxDataStep, rounding_to_noise: found.roundingToNoise }; },
  mask_isolated: a => { const width = a.above[0].length, height = a.above.length;
    const held = core.isolated(Uint8Array.from(a.above.join(''), ch => Number(ch === '1')), height, width);
    return a.above.map((_, row) => list(held.subarray(row * width, (row + 1) * width)).join('')); },
  digest_data: a => core.digestData(a.shape, a.values.flat(Infinity).map(num)),
  digest_pixels: a => core.digestPixels(Uint8Array.from(a.values.flat(Infinity)), a.shape[0], a.shape[1]),
  xmp: a => core.description(a.declaration),
  caption: a => core.caption(a.declaration),
};

// A NaN the core returns agrees only with a null in the vectors, and a null only with a NaN: wherever a number is
// expected, anything that is not a number within the tolerance is a mismatch. `only` names the outputs compared.
function agree(got, want, c, where, problems, only) {
  if (want !== null && typeof want === 'object' && !Array.isArray(want)) {
    for (const k of only ?? Object.keys(want)) agree(got?.[k], want[k], c, `${where}.${k}`, problems);
  } else if (Array.isArray(want)) {
    if (!Array.isArray(got) || got.length !== want.length) problems.push(`${where}: length differs`);
    else want.forEach((w, i) => agree(got[i], w, c, `${where}[${i}]`, problems));
  } else if (want === null) {
    if (!(typeof got === 'number' && Number.isNaN(got))) problems.push(`${where}: ${got}, expected NaN`);
  } else if (typeof want !== 'number' || c.exact) {
    if (got !== want) problems.push(`${where}: ${got} != ${want}`);
  } else if (typeof got !== 'number' || !(Math.abs(got - want) <= c.tol.abs + c.tol.rel * Math.abs(want))) {
    problems.push(`${where}: ${got} != ${want}`);
  }
}

let failed = 0, passed = 0;
const unreachable = {}, unknown = {}, partial = {};
for (const [name, c] of Object.entries(golden.cases)) {
  if (NOT_IN_THE_INTERFACE.includes(c.kind)) { unreachable[c.kind] = (unreachable[c.kind] ?? 0) + 1; continue; }
  if (!Object.hasOwn(KINDS, c.kind)) { unknown[c.kind] = (unknown[c.kind] ?? 0) + 1; continue; }
  const problems = [];
  try {
    agree(KINDS[c.kind](c.args, c), c.out, c, name, problems, IN_PART[c.kind]);
  } catch (error) {
    problems.push(`${name}: the core threw ${error}`);
  }
  if (problems.length) { failed++; console.log(`FAIL ${name}: ${problems.slice(0, 3).join('; ')}`); continue; }
  passed++;
  if (IN_PART[c.kind]) partial[c.kind] = (partial[c.kind] ?? 0) + 1;
}
console.log(`${passed} cases reproduced; of them in part (only what the interface returns): ${JSON.stringify(partial)}`);
console.log(`not reachable through the C interface: ${JSON.stringify(unreachable)}`);
const verdicts = [];
if (failed) verdicts.push(`FAIL: ${failed} cases out of tolerance`);
for (const [kind, n] of Object.entries(unknown)) verdicts.push(`FAIL: unknown kind ${kind} (${n} cases): neither in the interface nor listed as outside it`);
if (!passed) verdicts.push('FAIL: no case was reproduced');
console.log(verdicts.length ? verdicts.join('\n') : 'PASS');
process.exit(verdicts.length ? 1 : 0);
