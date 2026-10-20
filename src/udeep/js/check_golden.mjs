// node check_golden.mjs <golden.json> <tables dir> : the JS port must reproduce the golden vectors (format 2).
// Each case carries its own tolerance (or is exact). Everything that is compared is worked out by the shipped
// port, udeepcore.js: this file only reads the vectors, calls the port and compares.
//
// It fails, with exit status 1, when a case is out; when it reproduced no case at all; and when it meets a kind
// of case it has never heard of. The kinds the port is known not to implement are listed by name as skipped. It
// prints how many cases it reproduced, for the test that runs it to hold it to (tests/test_udeep_js.py).
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import './udeepcore.js';

const { cmap, makeTable, pqEncode, scale, tone, srgbOetf, position, light, valueOfLight, kneePosition, colours,
  sha256, digestData, digestPixels, isolated, iso21496Metadata } = globalThis.UDeepCore;

const NOT_IN_THIS_PORT = ['levels', 'levels_pq16', 'xmp', 'caption'];           // the levels, the declaration's text, the caption

const golden = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const tablesDir = process.argv[3];
if (golden.format !== 2) { console.log(`FAIL: golden format ${golden.format}, this check reads format 2`); process.exit(1); }
const num = v => (v === null ? NaN : v);                           // the vectors write NaN as null
const table = (file, headroom) => makeTable(readFileSync(join(tablesDir, file)), headroom);
const hex = bytes => Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');

const KINDS = {
  scale: a => a.x.map(x => scale(num(x), a.kind, a.lo, a.hi, a.width)),
  tone: a => a.t.map(t => tone(num(t), a.knee, a.headroom, a.exit_slope ?? 1)),
  pq_encode: a => a.nits.map(pqEncode),
  srgb_oetf: a => a.v.map(srgbOetf),
  cmap: a => { const t = table(a.table, a.headroom); return a.y.map(y => cmap(num(y), t)); },
  table: a => { const bytes = readFileSync(join(tablesDir, a.file));
    return { sha256: 'sha256:' + sha256(bytes), nodes: makeTable(bytes, a.headroom).nodes, bytes: bytes.length }; },
  mapping: (a, c) => { const m = a.mapping, x = a.x.map(num);
    const t = c.tables.length ? table(c.tables[0], m.reference_headroom) : null;
    const y = x.map(v => light(m, v));
    return { knee_position: kneePosition(m), position: x.map(v => position(m, v)), light: y,
      sdr_light: x.map(v => light(m, v, 1)), value_of_light: y.map(v => valueOfLight(m, v)),
      colours: y.map(v => colours(m, v, t)) }; },
  mask_isolated: a => isolated(a.above.map(row => Array.from(row, c => c === '1')))
    .map(row => row.map(v => (v ? '1' : '0')).join('')),
  digest_data: a => digestData(a.shape, a.values.flat(Infinity).map(num)),
  digest_pixels: a => digestPixels(Uint8Array.from(a.values.flat(Infinity)), a.shape[0], a.shape[1]),
  iso21496: a => hex(iso21496Metadata(a.gain_map_min, a.gain_map_max, a.hdr_capacity_max)),
};

// A NaN the port returns agrees only with a null in the vectors, and a null only with a NaN: wherever a number is
// expected, anything that is not a number within the tolerance is a mismatch.
function agree(got, want, c, where, problems) {
  if (want !== null && typeof want === 'object' && !Array.isArray(want)) {
    for (const k of Object.keys(want)) agree(got?.[k], want[k], c, `${where}.${k}`, problems);
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
const skipped = {}, unknown = {};
for (const [name, c] of Object.entries(golden.cases)) {
  if (NOT_IN_THIS_PORT.includes(c.kind)) { skipped[c.kind] = (skipped[c.kind] ?? 0) + 1; continue; }
  if (!Object.hasOwn(KINDS, c.kind)) { unknown[c.kind] = (unknown[c.kind] ?? 0) + 1; continue; }
  const problems = [];
  try {
    agree(KINDS[c.kind](c.args, c), c.out, c, name, problems);
  } catch (error) {
    problems.push(`${name}: the port threw ${error}`);
  }
  if (problems.length) { failed++; console.log(`FAIL ${name}: ${problems.slice(0, 3).join('; ')}`); } else passed++;
}
console.log(`${passed} cases reproduced; skipped (not in this port): ${JSON.stringify(skipped)}`);
const verdicts = [];
if (failed) verdicts.push(`FAIL: ${failed} cases out of tolerance`);
for (const [kind, n] of Object.entries(unknown)) verdicts.push(`FAIL: unknown kind ${kind} (${n} cases): neither in this port nor listed as left out of it`);
if (!passed) verdicts.push('FAIL: no case was reproduced');
console.log(verdicts.length ? verdicts.join('\n') : 'PASS');
process.exit(verdicts.length ? 1 : 0);
