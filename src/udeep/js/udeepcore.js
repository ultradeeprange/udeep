// JS port of UDeep's core: scale, tone, colour-map tables, mapping records, PQ. Scalar functions, float64.
// Also the parts of a file that are plain bytes: the two digests, the isolated-pixel rule and the gain map's
// ISO 21496-1 metadata. It does not write the declaration, the caption or the levels.
// Contract: reproduce udeep/golden.json, each case to its own stated tolerance (check_golden.mjs).
// A plain script, not an ES module: pages load it with <script src="udeepcore.js"> (the supplementary
// build fingerprints every script a page loads) and read globalThis.UDeepCore; node imports it the same way.
// It uses nothing from node and nothing from the browser, so it runs in both.

(() => {
  
  const REF_WHITE_NITS = 203.0;
  
  const SRGB_TO_XYZ = [[0.4123907992659595, 0.357584339383878, 0.1804807884018343],
    [0.21263900587151036, 0.715168678767756, 0.07219231536073371],
    [0.01933081871559185, 0.11919477979462598, 0.9505321522496606]];
  const XYZ_TO_LMS = [[0.41478972, 0.579999, 0.0146480],
    [-0.2015100, 1.120649, 0.0531008],
    [-0.0166008, 0.264800, 0.6684799]];
  const LMS_TO_IAB = [[0.5, 0.5, 0.0],
    [3.524000, -4.066708, 0.542708],
    [0.199076, 1.096799, -1.295875]];
  
  function inv3(m) {
    const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
    const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    const det = a * A + b * B + c * C;
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
      [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
      [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]];
  }
  const mm = (m, v) => [m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2]];
  const XYZ_TO_SRGB = inv3(SRGB_TO_XYZ), LMS_TO_XYZ = inv3(XYZ_TO_LMS), IAB_TO_LMS = inv3(LMS_TO_IAB);
  
  const B = 1.15, G = 0.66, C1 = 3424 / 4096, C2 = 2413 / 128, C3 = 2392 / 128;
  const N = 2610 / 16384, P = 1.7 * 2523 / 32, D = -0.56, D0 = 1.6295499532821566e-11;
  
  function xyzToJzazbz([x, y, z]) {
    const lms = mm(XYZ_TO_LMS, [B * x - (B - 1) * z, G * y - (G - 1) * x, z]);
    const lp = lms.map(v => { const q = (Math.max(v, 0) / 10000) ** N; return ((C1 + C2 * q) / (1 + C3 * q)) ** P; });
    const [iz, az, bz] = mm(LMS_TO_IAB, lp);
    return [(1 + D) * iz / (1 + D * iz) - D0, az, bz];
  }
  
  function jzazbzToXyz([j, a, b]) {
    const jz = j + D0, iz = jz / (1 + D - D * jz);
    const lms = mm(IAB_TO_LMS, [iz, a, b]).map(v => {
      const r = Math.max(v, 0) ** (1 / P);
      return 10000 * (Math.max(r - C1, 0) / (C2 - C3 * r)) ** (1 / N);
    });
    const [xp, yp, z] = mm(LMS_TO_XYZ, lms);
    const x = (xp + (B - 1) * z) / B;
    return [x, (yp + (G - 1) * x) / G, z];
  }
  
  function scale(x, kind, lo, hi, width = 1.0) {
    if (!(hi > lo)) throw new Error('need hi > lo');
    if (kind === 'log' && !(lo > 0)) throw new Error('log scale needs lo > 0');
    const f = kind === 'linear' ? v => v
      : kind === 'log' ? v => (v > 0 ? Math.log10(v) : -Infinity)
        : kind === 'asinh' ? v => Math.asinh(v / width)
          : null;
    if (!f) throw new Error(`unknown scale kind ${kind}`);
    if (Number.isNaN(x)) return NaN;
    const t = (f(x) - f(lo)) / (f(hi) - f(lo));
    return Math.min(Math.max(t, 0), 1);
  }
  
  function tone(t, knee, headroom, exitSlope = 1) {
    if (!(knee > 0 && knee <= 1)) throw new Error('knee must be in (0, 1]');
    if (!(headroom >= 1)) throw new Error('headroom must be >= 1');
    if (!(exitSlope >= 0 && exitSlope <= 3)) throw new Error('exitSlope must be in [0, 3] secants');
    if (Number.isNaN(t)) return NaN;
    if (knee >= 1 || t <= knee) return Math.min(Math.max(t / knee, 0), headroom);
    // C1 Hermite shoulder from 1 to headroom (see udeep/tone.py): entry slope 1/knee, capped
    // at 3x the secant for monotonicity; exit slope = exitSlope secants (1: the declared, invertible
    // mapping; 0: the flat BT.2390-style roll-off, for display rendering only).
    const w = Math.max(1 - knee, 1e-6), secant = (headroom - 1) / w, m0 = Math.min(1 / knee, 3 * secant);
    const u = Math.min(Math.max((t - knee) / w, 0), 1), u2 = u * u, u3 = u2 * u;
    const y = (2 * u3 - 3 * u2 + 1) + (u3 - 2 * u2 + u) * w * m0 + (-2 * u3 + 3 * u2) * headroom
      + (u3 - u2) * w * exitSlope * secant;
    return Math.min(Math.max(y, 0), headroom);
  }
  
// A colour map is a table (udeep/tables/*.f32): two segments, light 0..1 and 1..headroom, each `nodes` x 3
  // little-endian float32 of linear RGB in multiples of SDR white. Colours between nodes are interpolated.
  // `buffer` is the file's bytes: an ArrayBuffer, a Uint8Array or a node Buffer. Anything else is refused: an
  // array of numbers would be read as one byte each, and give a table of nonsense.
  function makeTable(buffer, headroom) {
    const bytes = asBytes(buffer, 'a colour-map table');
    if (bytes.length % 24 !== 0 || bytes.length < 48) {
      throw new RangeError(`a table is two segments of nodes x 3 float32, at least two nodes each: ${bytes.length} bytes is not that`);
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const values = new Float32Array(bytes.length / 4);             // a copy, read as little-endian whatever the platform
    for (let i = 0; i < values.length; i++) values[i] = view.getFloat32(4 * i, true);
    return { values, nodes: values.length / 6, headroom };
  }

  function cmap(y, table) {
    if (Number.isNaN(y)) return [NaN, NaN, NaN];
    const { values, nodes, headroom } = table;
    const v = Math.min(Math.max(y, 0), Math.max(headroom, 1));
    const high = v > 1;
    const p = (high ? (v - 1) / Math.max(headroom - 1, 1e-12) : v) * (nodes - 1);
    const i = Math.min(Math.floor(p), nodes - 2), f = p - i;
    const at = 3 * (i + (high ? nodes : 0));
    return [0, 1, 2].map(k => values[at + k] * (1 - f) + values[at + 3 + k] * f);
  }

// A mapping record (udeep/mapping.py): the fields a file declares, under the same names.
  const kindOf = m => (m.stretch === 'proportional' ? 'linear' : m.stretch);
  function exitSlope(m) {                                          // "hermite; exit=<number>", the number in 0..3
    const found = /^hermite; exit=([0-9.]+)$/.exec(m.above_knee);
    const slope = found ? Number(found[1]) : NaN;
    if (!(slope >= 0 && slope <= 3)) throw new Error(`unknown form above the knee: ${m.above_knee}`);
    return slope;
  }
  const position = (m, x) => scale(x - m.stretch_offset, kindOf(m), m.range_low, m.range_high, m.stretch_width);
  // Where the knee is drawn. The curve cannot place a knee within 1e-6 of either end of the range, and a record
  // that asks for one is refused, not drawn somewhere else. A knee exactly at the top is the full-range SDR plot.
  function kneePosition(m) {
    const t = position(m, m.knee);
    if (!(t >= 1e-6) || (t > 1 - 1e-6 && t < 1)) throw new Error(`the knee ${m.knee} is at position ${t} of the declared range`);
    return t;
  }
  const lightAt = (m, t, headroom) => (headroom === undefined
    ? tone(t, kneePosition(m), m.reference_headroom, exitSlope(m)) : tone(t, kneePosition(m), headroom, 1));
  const light = (m, x, headroom) => lightAt(m, position(m, x), headroom);
  function valueAt(m, t) {
    const edge = v => (kindOf(m) === 'linear' ? v : kindOf(m) === 'log' ? Math.log(v) : Math.asinh(v / m.stretch_width));
    const f = edge(m.range_low) + t * (edge(m.range_high) - edge(m.range_low));
    return (kindOf(m) === 'linear' ? f : kindOf(m) === 'log' ? Math.exp(f) : m.stretch_width * Math.sinh(f)) + m.stretch_offset;
  }
  function valueOfLight(m, y) {                                  // the inverse of light: 60 bisection steps
    if (Number.isNaN(y)) return NaN;
    const target = Math.min(Math.max(y, 0), m.reference_headroom);
    let lo = 0, hi = 1;
    for (let k = 0; k < 60; k++) { const mid = 0.5 * (lo + hi); if (lightAt(m, mid) < target) lo = mid; else hi = mid; }
    return valueAt(m, 0.5 * (lo + hi));
  }
  const colours = (m, y, table) => (m.colour_map === 'grey' ? [y, y, y] : cmap(y, table));

  // The shader's `Mapping` uniform block (udeep/shaders/mapping.wgsl, and udeep/gpu.py which this follows
  // line for line): 64 bytes, twelve float32 then four uint32, little-endian. Everything is worked out in
  // float64 first. A number that is subtracted comes as a pair: the nearest float32, and what is left of the
  // float64 number after it, already multiplied by the factor the shader applies between the two subtractions.
  // `nodes` is the colour-map table's nodes per segment, 0 for grey. This does not refuse the mappings float32
  // cannot hold to tolerance (gpu.py's `check` does): a viewer shows them, to float32's precision.
  function uniformBlock(m, nodes = 0) {
    const kind = kindOf(m), low = m.range_low, high = m.range_high, width = m.stretch_width;
    let offset, k, fLow, invSpan;
    if (kind === 'linear') { offset = m.stretch_offset + low; k = 1 / (high - low); fLow = 0; invSpan = 1; }
    else if (kind === 'log') { offset = m.stretch_offset; k = 1 / low; fLow = 0; invSpan = 1 / Math.log(high / low); }
    else { offset = m.stretch_offset; k = 1 / width; fLow = Math.asinh(low / width); invSpan = 1 / (Math.asinh(high / width) - fLow); }
    const knee = kneePosition(m), headroom = m.reference_headroom;
    const shoulder = knee < 1 ? 1 - knee : 1;
    const pair = v => { const hi = Math.fround(v); return [hi, Number.isFinite(hi) ? v - hi : 0]; };
    const [offsetHi, offsetLo] = pair(offset), [lowHi, lowLo] = pair(fLow), [kneeHi, kneeLo] = pair(knee);
    const numbers = [offsetHi, offsetLo * k, k, lowHi, lowLo * invSpan, invSpan, kneeHi, kneeLo / shoulder, shoulder,
      headroom, headroom > 1 ? 1 / (headroom - 1) : 0, exitSlope(m)];
    const bytes = new Uint8Array(64), view = new DataView(bytes.buffer);
    numbers.forEach((v, i) => view.setFloat32(4 * i, v, true));
    const code = { linear: 0, log: 1, asinh: 2, proportional: 3 }[m.stretch];
    if (code === undefined) throw new Error(`unknown stretch: ${m.stretch}`);
    [code, nodes, 0, 0].forEach((v, i) => view.setUint32(48 + 4 * i, v, true));
    return bytes;
  }
  const srgbOetf = v => { const c = Math.min(Math.max(v, 0), 1); return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055; };

  const M1 = 2610 / 16384, M2 = 2523 / 4096 * 128, P1 = 3424 / 4096, P2 = 2413 / 4096 * 32, P3 = 2392 / 4096 * 32;
  function pqEncode(nits) {
    const y = (Math.min(Math.max(nits, 0), 10000) / 10000) ** M1;
    return ((P1 + P2 * y) / (1 + P3 * y)) ** M2;
  }
  
  // ---- bytes

  // An ArrayBuffer, a Uint8Array or a node Buffer (which is a Uint8Array), as a Uint8Array over the same memory.
  function asBytes(buffer, what) {
    const type = Object.prototype.toString.call(buffer);
    if (type === '[object ArrayBuffer]') return new Uint8Array(buffer);
    if (type === '[object Uint8Array]') return buffer;
    throw new TypeError(`${what} must be bytes (an ArrayBuffer, a Uint8Array or a Buffer), got ${type.slice(8, -1)}`);
  }

  const ascii = text => Uint8Array.from(text, c => c.charCodeAt(0));
  function joined(parts) {
    const all = new Uint8Array(parts.reduce((n, part) => n + part.length, 0));
    let at = 0;
    for (const part of parts) { all.set(part, at); at += part.length; }
    return all;
  }

  // SHA-256 (FIPS 180-4) of bytes, as 64 hex digits. Written out here, so that the file needs neither node's
  // crypto module nor a browser's (which is asynchronous, and absent from a page that is not served securely).
  const SHA_K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]);
  function sha256(buffer) {
    const bytes = asBytes(buffer, 'what is digested');
    const padded = new Uint8Array((bytes.length + 9 + 63) & ~63);  // the bytes, 0x80, zeros, the length in bits
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(bytes.length / 0x20000000));
    view.setUint32(padded.length - 4, (bytes.length << 3) >>> 0);
    const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
    const w = new Uint32Array(64), rot = (x, n) => (x >>> n) | (x << (32 - n));
    for (let block = 0; block < padded.length; block += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(block + 4 * i);
      for (let i = 16; i < 64; i++) {
        const s0 = rot(w[i - 15], 7) ^ rot(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rot(w[i - 2], 17) ^ rot(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const t1 = (hh + (rot(e, 6) ^ rot(e, 11) ^ rot(e, 25)) + ((e & f) ^ (~e & g)) + SHA_K[i] + w[i]) | 0;
        const t2 = ((rot(a, 2) ^ rot(a, 13) ^ rot(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      [a, b, c, d, e, f, g, hh].forEach((v, i) => { h[i] += v; });
    }
    return Array.from(h, v => v.toString(16).padStart(8, '0')).join('');
  }

  // The digest of an array's values and shape (udeep/metadata.py, digest_data): the text
  // "udeep-data-1;<shape, joined by x>;", then each value as a little-endian float64, in row order. -0 counts
  // as 0 and every NaN as the same NaN. `values` is flat: an array, or a typed array, of numbers.
  function digestData(shape, values) {
    const count = shape.reduce((n, size) => n * size, 1);
    if (values.length !== count) throw new RangeError(`shape ${shape.join('x')} needs ${count} values, got ${values.length}`);
    const body = new Uint8Array(8 * count), view = new DataView(body.buffer);
    for (let i = 0; i < count; i++) {
      const v = values[i];
      if (typeof v !== 'number') throw new TypeError(`only numbers have a data digest, got ${v} at ${i}`);
      if (Number.isNaN(v)) view.setUint32(8 * i + 4, 0x0000f87f);  // the bytes of the one NaN: 00 00 00 00 00 00 f8 7f
      else view.setFloat64(8 * i, v + 0, true);
    }
    return 'sha256:' + sha256(joined([ascii(`udeep-data-1;${shape.join('x')};`), body]));
  }

  // The digest of an 8-bit RGB image's pixels (digest_pixels): the text "udeep-rgb8-1;<height>x<width>;", then
  // the bytes, row by row. `pixels` is bytes, height x width x 3 of them.
  function digestPixels(pixels, height, width) {
    const bytes = asBytes(pixels, 'pixels');
    if (bytes.length !== height * width * 3) throw new RangeError(`${height} x ${width} x 3 pixels are not ${bytes.length} bytes`);
    return 'sha256:' + sha256(joined([ascii(`udeep-rgb8-1;${height}x${width};`), bytes]));
  }

  // The isolated-pixel rule (udeep/masks.py, "isolated-3x3"): the pixels of `above` that belong to no full
  // 3 x 3 block of it. `above` is rows of true and false; outside the image counts as false. Returns the same shape.
  function isolated(above) {
    const h = above.length, w = h ? above[0].length : 0;
    if (above.some(row => row.length !== w)) throw new RangeError('the isolated-pixel rule needs rows of one length');
    const set = (grid, y, x) => y >= 0 && y < h && x >= 0 && x < w && Boolean(grid[y][x]);
    const around = (grid, y, x, every) => {
      let all = true, any = false;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const v = set(grid, y + dy, x + dx); all = all && v; any = any || v; }
      return every ? all : any;
    };
    const eroded = above.map((row, y) => Array.from(row, (_, x) => around(above, y, x, true)));
    return above.map((row, y) => Array.from(row, (v, x) => Boolean(v) && !around(eroded, y, x, false)));
  }

  // The gain map's ISO 21496-1 metadata (udeep/gainmap.py, _iso_metadata), as bytes: versions (0) and flags
  // (0x40: the gain is applied in the base image's colour space),
  // base and alternate headroom, then gain-map min, max, gamma and the two offsets, each a numerator over
  // 1000000, big-endian. Headrooms, min and max are log2; gamma is 1 and both offsets are 1/64.
  function iso21496Metadata(gainMapMin, gainMapMax, hdrCapacityMax) {
    const d = 1000000, n = v => Math.round(v * d), offset = n(1 / 64);
    for (const v of [gainMapMin, gainMapMax, hdrCapacityMax]) {
      if (!Number.isFinite(v)) throw new RangeError(`gain-map metadata needs finite numbers, got ${v}`);
    }
    const bytes = new Uint8Array(5 + 14 * 4), view = new DataView(bytes.buffer);
    bytes[4] = 0x40;
    [0, d, n(hdrCapacityMax), d].forEach((v, i) => view.setUint32(5 + 4 * i, v));
    [[n(gainMapMin), true], [d], [n(gainMapMax), true], [d], [d], [d], [offset, true], [d], [offset, true], [d]]
      .forEach(([v, signed], i) => (signed ? view.setInt32(21 + 4 * i, v) : view.setUint32(21 + 4 * i, v)));
    return bytes;
  }

  globalThis.UDeepCore = { REF_WHITE_NITS, xyzToJzazbz, jzazbzToXyz, scale, tone, makeTable, cmap, pqEncode, srgbOetf,
    position, kneePosition, light, lightAt, valueAt, valueOfLight, colours, exitSlope, uniformBlock, sha256, digestData,
    digestPixels, isolated, iso21496Metadata };
})();
