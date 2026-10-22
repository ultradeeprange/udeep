// The Rust core of UDeep (core/) as WebAssembly, behind functions that take and return typed arrays.
//
//     const core = await UDeepWasm.load(bytesOfUdeepCoreWasm);
//     const light = core.light(mapping, values);                 // Float64Array, in multiples of SDR white
//
// The module is the core's C interface (core/include/udeep_core.h) built for wasm32-unknown-unknown:
//     cargo build --release --target wasm32-unknown-unknown --features jpeg,png      (in core/)
// It imports nothing. This file only moves numbers in and out of its memory: every result is worked out by
// the core, the same code the Python and Julia packages call. It is not udeepcore.js, the port written in
// JavaScript, which works one value at a time and needs no module; the two are checked against the same
// golden vectors (check_golden_wasm.mjs, check_golden.mjs).
//
// A mapping record is an object with the fields a file declares, under the same names (udeep/mapping.py):
// stretch, range_low, range_high, knee, reference_headroom, stretch_width, stretch_offset, above_knee. A
// declaration is one with the fields of udeep/metadata.py's Declaration. A colour-map table is what makeTable
// gives from a table's bytes, or colourTable makes at a headroom with no published table; null is grey. A call the core refuses throws an Error whose `code` is the UDEEP_ERROR_* number.
//
// There are no threads in a WebAssembly build: the core runs every loop in the caller's thread.
// A plain script, like udeepcore.js: pages load it with <script src="udeepwasm.js"> and read
// globalThis.UDeepWasm; node imports it the same way. It needs WebAssembly, TextEncoder and TextDecoder.

(() => {
  const ERRORS = {
    1: 'a required pointer was null', 2: 'unknown stretch', 3: 'the declared range is not valid',
    4: 'the knee is not inside the declared range, or is too near one end of it',
    5: 'the headroom or the exit slope is out of range',
    6: 'the stretch width, or a proportional mapping, is not valid',
    7: 'the colour-map table is not two segments of nodes x 3, or names no registered map', 8: 'the image cannot be written or is not of this kind',
    9: 'the image codec failed, or the gain map could not be kept within the headroom',
    10: 'a number in the mapping is not finite',
    11: 'the declaration is one the profile refuses (in a PQ file: one with white at other than 203 cd/m2)',
  };
  const STRETCH = { linear: 0, log: 1, asinh: 2, proportional: 3 };
  const NOISE = { unknown: 0, constant: 1, poisson: 2, relative: 3, tolerance: 4 };
  // the text properties of udeep_declaration, in the order the record holds them
  const TEXTS = ['data_unit', 'knee_rule', 'noise_model', 'record', 'derived_mask', 'preset', 'colour_map',
    'colour_map_table', 'data_digest', 'data_source', 'data_unit_code', 'sdr_digest', 'data_digest_sha3', 'sdr_digest_sha3'];
  const DEFAULTS = { noise_model: 'unknown', record: 'rendering' };  // as the reference's Declaration has them
  const VERSION = '0.3', IMAGE_STATE = 'data-referred';              // the library's own: a record carries neither
  const ABOVE_KNEE = 'hermite; exit=1';
  const MAPPING_BYTES = 64, DECLARATION_BYTES = 216, DIGEST_LENGTH = 71, DIGEST_SHA3_LENGTH = 73, ISO21496_LENGTH = 61, CRITERION_BYTES = 64, REF_WHITE_NITS = 203.0;

  // An ArrayBuffer, a Uint8Array or a node Buffer (which is a Uint8Array), as a Uint8Array over the same memory.
  function asBytes(buffer, what) {
    const type = Object.prototype.toString.call(buffer);
    if (type === '[object ArrayBuffer]') return new Uint8Array(buffer);
    if (type === '[object Uint8Array]') return buffer;
    throw new TypeError(`${what} must be bytes (an ArrayBuffer, a Uint8Array or a Buffer), got ${type.slice(8, -1)}`);
  }

  // Numbers as float64. An array is checked value by value: a null or a string would otherwise become 0 or NaN
  // without a word.
  function asDoubles(values, what) {
    if (ArrayBuffer.isView(values) && !(values instanceof DataView)) return Float64Array.from(values);
    if (!Array.isArray(values)) throw new TypeError(`${what} must be an array or a typed array of numbers`);
    values.forEach((v, i) => { if (typeof v !== 'number') throw new TypeError(`${what} must be numbers, got ${v} at ${i}`); });
    return Float64Array.from(values);
  }

  // A colour-map table from the bytes of its file (udeep/tables/*.f32): two segments, light 0..1 and
  // 1..headroom, each `nodes` x 3 little-endian float32. The same record udeepcore.js makes.
  function makeTable(buffer, headroom) {
    const bytes = asBytes(buffer, 'a colour-map table');
    if (bytes.length % 24 !== 0 || bytes.length < 48) {
      throw new RangeError(`a table is two segments of nodes x 3 float32, at least two nodes each: ${bytes.length} bytes is not that`);
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const values = new Float32Array(bytes.length / 4);
    for (let i = 0; i < values.length; i++) values[i] = view.getFloat32(4 * i, true);
    return { values, nodes: values.length / 6, headroom };
  }

  function exitSlope(m) {                                          // "hermite; exit=<number>": the core checks the number
    const found = /^hermite; exit=([0-9.]+)$/.exec(m.above_knee ?? ABOVE_KNEE);
    const slope = found ? Number(found[1]) : NaN;
    if (Number.isNaN(slope)) throw new Error(`unknown form above the knee: ${m.above_knee}`);
    return slope;
  }

  // The core, from the bytes of udeep_core.wasm or from a WebAssembly.Module already compiled.
  async function load(source) {
    const module = source instanceof WebAssembly.Module ? source : await WebAssembly.compile(asBytes(source, 'the core'));
    const x = (await WebAssembly.instantiate(module, {})).exports;
    if (typeof x.udeep_buffer_alloc !== 'function' || typeof x.udeep_light !== 'function') {
      throw new Error('not a WebAssembly build of the UDeep core: it lacks udeep_buffer_alloc or udeep_light');
    }
    const has = { jpeg: typeof x.udeep_gainmap_jpeg === 'function', png: typeof x.udeep_png_pq === 'function' };
    const utf8 = new TextEncoder(), text = new TextDecoder('utf-8', { fatal: true });

    // The module's memory moves when it grows, and any allocation may grow it: a view is made when it is used
    // and never kept across a call.
    const view = () => new DataView(x.memory.buffer);
    const bytesAt = (at, n) => new Uint8Array(x.memory.buffer, at, n);
    const doublesAt = (at, n) => new Float64Array(x.memory.buffer, at, n);

    // Run `work` with `take(size)`, which gives the address of `size` zeroed bytes in the module's memory, and
    // release all of it afterwards, whatever happens.
    function scoped(work) {
      const held = [];
      const take = size => {
        // a size is passed as 32 bits: a larger one would wrap round to a small one, so it is refused here
        if (!(size < 2 ** 32)) throw new RangeError(`${size} bytes are more than a 32-bit module's memory can hold`);
        const at = x.udeep_buffer_alloc(Math.max(size, 1)) >>> 0;   // never null: null means "absent" to the core
        if (at === 0) throw new RangeError(`the core has no memory for ${size} bytes`);
        held.push([at, Math.max(size, 1)]);
        return at;
      };
      try { return work(take); } finally { for (const [at, size] of held) x.udeep_buffer_free(at, size); }
    }
    function check(status) {
      if (status !== 0) throw Object.assign(new Error(`the core refused the call: ${ERRORS[status] ?? `error ${status}`}`), { code: status });
    }
    const feature = name => { if (!has[name]) throw new Error(`this build of the core has no ${name} feature`); };

    const putBytes = (take, bytes) => { const at = take(bytes.length); bytesAt(at, bytes.length).set(bytes); return at; };
    const putDoubles = (take, values) => { const at = take(8 * values.length); doublesAt(at, values.length).set(values); return at; };
    function putTable(take, table) {                               // [address, nodes]; null is grey
      if (table === null || table === undefined) return [0, 0];
      const at = take(4 * table.values.length);
      new Float32Array(x.memory.buffer, at, table.values.length).set(table.values);
      return [at, table.nodes];
    }
    function writeMapping(at, m) {                                 // udeep_mapping: seven doubles, two uint32
      const code = STRETCH[m.stretch];
      if (code === undefined) throw new Error(`unknown stretch: ${m.stretch}`);
      const v = view();
      [m.range_low, m.range_high, m.knee, m.reference_headroom, m.stretch_width ?? 1, m.stretch_offset ?? 0, exitSlope(m)]
        .forEach((number, i) => v.setFloat64(at + 8 * i, number, true));
      v.setUint32(at + 56, code, true);
      v.setUint32(at + 60, 0, true);
    }
    const putMapping = (take, m) => { const at = take(MAPPING_BYTES); writeMapping(at, m); return at; };
    // udeep_declaration: the mapping, three doubles, two uint64, then a (pointer, length) pair for each text
    function putDeclaration(take, d) {
      if ((d.version ?? VERSION) !== VERSION || (d.image_state ?? IMAGE_STATE) !== IMAGE_STATE) {
        throw new Error(`the core writes version ${VERSION}, ${IMAGE_STATE}: not ${d.version}, ${d.image_state}`);
      }
      const texts = TEXTS.map(name => {
        const value = d[name] ?? DEFAULTS[name];
        if (value === undefined || value === null) return [0, 0];
        if (typeof value !== 'string') throw new TypeError(`${name} must be text, got ${value}`);
        const bytes = utf8.encode(value);
        return [putBytes(take, bytes), bytes.length];
      });
      const at = take(DECLARATION_BYTES);
      writeMapping(at, d);
      const v = view(), absent = number => (number === undefined || number === null ? NaN : number);
      v.setFloat64(at + 64, d.max_data_step, true);
      v.setFloat64(at + 72, absent(d.noise_parameter), true);
      v.setFloat64(at + 80, absent(d.rounding_to_noise), true);
      v.setBigUint64(at + 88, BigInt(d.masked ?? 0), true);
      v.setBigUint64(at + 96, BigInt(d.derived_masked ?? 0), true);
      texts.forEach(([data, length], i) => { v.setUint32(at + 104 + 8 * i, data, true); v.setUint32(at + 108 + 8 * i, length, true); });
      return at;
    }
    // The digests, by either hash function: `call` names the core's function, `length` the text it writes.
    const digestBytes = (bytes, call, length) => scoped(take => {
      const data = asBytes(bytes, 'what is digested'), from = putBytes(take, data), out = take(length + 1);
      check(x[call](from, data.length, out));
      return text.decode(bytesAt(out, length));
    });
    const digestData = (shape, values, call, length) => scoped(take => {
      const numbers = asDoubles(values, 'what is digested');
      const count = shape.reduce((n, size) => n * size, 1);
      if (numbers.length !== count) throw new RangeError(`shape ${shape.join('x')} needs ${count} values, got ${numbers.length}`);
      const sizes = shape.length ? take(4 * shape.length) : 0;
      new Uint32Array(x.memory.buffer, sizes, shape.length).set(shape);
      const from = putDoubles(take, numbers), out = take(length + 1);
      check(x[call](sizes, shape.length, from, out));
      return text.decode(bytesAt(out, length));
    });
    const digestPixels = (pixels, height, width, call, length) => scoped(take => {
      const bytes = render(pixels, width, height, 'pixels');
      const from = putBytes(take, bytes), out = take(length + 1);
      check(x[call](height, width, from, out));
      return text.decode(bytesAt(out, length));
    });
    // Bytes the core returns in memory of its own: copied out, and released.
    function owned(take, call) {
      const cell = take(8);                                        // a pointer and a length
      check(call(cell, cell + 4));
      const v = view(), data = v.getUint32(cell, true), length = v.getUint32(cell + 4, true);
      const copy = bytesAt(data, length).slice();
      x.udeep_bytes_free(data, length);
      return copy;
    }

    // One value in, one value out, through a mapping record: in place, which the core allows.
    const each = name => (m, values) => scoped(take => {
      const numbers = asDoubles(values, 'the values');
      const record = putMapping(take, m), at = putDoubles(take, numbers);
      check(x[name](record, at, at, numbers.length));
      return doublesAt(at, numbers.length).slice();
    });
    // One value in, one value out, after the function's own parameters (none for the sRGB signal and PQ).
    const staged = (name, values, parameters) => scoped(take => {
      const numbers = asDoubles(values, 'the values'), at = putDoubles(take, numbers);
      check(x[name](...parameters, at, at, numbers.length));
      return doublesAt(at, numbers.length).slice();
    });
    const signal = name => values => staged(name, values, []);
    const position = each('udeep_position');

    function levelsOf(name, m, table, scaledBy, samples, noise, counted) {
      return scoped(take => {
        const [model, parameter] = noise ?? ['unknown', null];
        if (NOISE[model] === undefined) throw new Error(`unknown noise model: ${model}`);
        const record = putMapping(take, m), [at, nodes] = putTable(take, table), out = take(24);
        const rest = counted ? [out + 16] : [];
        check(x[name](record, at, nodes, scaledBy, samples, NOISE[model], parameter ?? 0, out, out + 8, ...rest));
        const v = view(), found = { maxDataStep: v.getFloat64(out, true), roundingToNoise: v.getFloat64(out + 8, true) };
        return counted ? { ...found, count: v.getUint32(out + 16, true) } : found;
      });
    }
    const sized = (name, file) => scoped(take => {                 // [width, height] of an image file
      const at = putBytes(take, file), cell = take(8);
      check(x[name](at, file.length, cell, cell + 4));
      return [view().getUint32(cell, true), view().getUint32(cell + 4, true)];
    });
    function image(values, width, height, what) {                  // height x width x 3 doubles, row by row
      const numbers = asDoubles(values, what);
      if (numbers.length !== width * height * 3) throw new RangeError(`${what}: ${height} x ${width} x 3 values are not ${numbers.length}`);
      return numbers;
    }
    function render(pixels, width, height, what) {                 // height x width x 3 bytes
      const bytes = asBytes(pixels, what);
      if (bytes.length !== width * height * 3) throw new RangeError(`${what}: ${height} x ${width} x 3 bytes are not ${bytes.length}`);
      return bytes;
    }

    return Object.freeze({
      version: x.udeep_version(),                                  // major * 10000 + minor * 100 + patch
      has: Object.freeze(has),

      // 0 if the core takes this mapping record, else the UDEEP_ERROR_* code it refuses it with
      validate: m => scoped(take => x.udeep_mapping_validate(putMapping(take, m))),
      position,                                                    // data values -> position in [0, 1]
      light: each('udeep_light'),                                  // data values -> light, in multiples of SDR white
      sdrLight: each('udeep_sdr_light'),                           // the fair SDR light: white above the knee
      valueOfLight: each('udeep_value'),                           // light -> data values: the inverse of light
      valueAt: each('udeep_value_at'),                             // position -> data values
      kneePosition: m => position(m, [m.knee])[0],                 // where the knee is drawn
      // light -> linear RGB (BT.709 primaries, multiples of SDR white): 3 numbers for each value of `light`
      colours: (light, table = null) => scoped(take => {
        const numbers = asDoubles(light, 'the light'), n = numbers.length;
        const [at, nodes] = putTable(take, table), from = putDoubles(take, numbers), to = take(24 * n);
        check(x.udeep_colours(at, nodes, table ? table.headroom : 1, from, to, n));
        return doublesAt(to, 3 * n).slice();
      }),
      // A registered map's table made at any reference headroom, by the generator the published tables were made
      // with: for a headroom with no published table, which the file must then carry (udeep:ColourMapTable,
      // profile 6.10). At 2, 4, 8 or 16 take the published table (makeTable) instead.
      colourTable: (name, headroom, { version = 3, nodes = 257 } = {}) => scoped(take => {
        const bytes = utf8.encode(String(name)), from = putBytes(take, bytes), out = take(24 * nodes);
        check(x.udeep_colour_table(from, bytes.length, version, headroom, nodes, out));
        return { values: new Float32Array(x.memory.buffer, out, 6 * nodes).slice(), nodes, headroom };
      }),
      srgbEncode: signal('udeep_srgb_encode'),                     // linear light -> the sRGB signal, both in [0, 1]
      srgbDecode: signal('udeep_srgb_decode'),

      // The stages of the mapping on their own, for a viewer that draws the curve or a check against the golden
      // vectors: a bare scale (`kind` "linear", "log" or "asinh"), the tone curve, and PQ. They refuse what the
      // reference's scale and tone refuse.
      scale: (values, kind, lo, hi, width = 1) => {
        const code = { linear: 0, log: 1, asinh: 2 }[kind];
        if (code === undefined) throw new Error(`unknown scale kind: ${kind}`);
        return staged('udeep_scale', values, [code, lo, hi, width]);
      },
      tone: (t, knee, headroom, exitSlope = 1) => staged('udeep_tone', t, [knee, headroom, exitSlope]),
      pqEncode: signal('udeep_pq_encode'),                         // cd/m^2 -> PQ signal in [0, 1]
      // "sha256:" and the digest of any bytes: of a colour-map table's file, what the index of the shipped tables
      // records for it
      digestBytes: bytes => digestBytes(bytes, 'udeep_digest_bytes', DIGEST_LENGTH),
      // the same with SHA3-256: "sha3-256:" and 64 hex digits
      digestBytesSha3: bytes => digestBytes(bytes, 'udeep_digest_bytes_sha3', DIGEST_SHA3_LENGTH),
      // The ISO 21496-1 block a gain-map JPEG carries, 61 bytes. The three values are log2, as the XMP gives them.
      iso21496Metadata: (gainMapMin, gainMapMax, hdrCapacityMax) => scoped(take => {
        const out = take(ISO21496_LENGTH);
        check(x.udeep_iso21496_block(gainMapMin, gainMapMax, hdrCapacityMax, out));
        return bytesAt(out, ISO21496_LENGTH).slice();
      }),

      // The isolated-3x3 rule: of height x width bytes in which a non-zero one is a pixel above the knee, 1 where
      // such a pixel lies in no full 3 x 3 block of them, else 0.
      isolated: (above, height, width) => scoped(take => {
        const bytes = asBytes(above, 'the pixels above the knee');
        if (bytes.length !== height * width) throw new RangeError(`${height} x ${width} pixels are not ${bytes.length} bytes`);
        const from = putBytes(take, bytes), to = take(bytes.length);
        check(x.udeep_mask_isolated(from, height, width, to));
        return bytesAt(to, bytes.length).slice();
      }),

      // The two digests a file declares, as "sha256:" and 64 hex digits; the Sha3 ones as "sha3-256:" and 64
      // hex digits (DataDigestSha3, SdrDigestSha3). `values` is flat, in row order.
      digestData: (shape, values) => digestData(shape, values, 'udeep_digest_data', DIGEST_LENGTH),
      digestDataSha3: (shape, values) => digestData(shape, values, 'udeep_digest_data_sha3', DIGEST_SHA3_LENGTH),
      digestPixels: (pixels, height, width) => digestPixels(pixels, height, width, 'udeep_digest_pixels', DIGEST_LENGTH),
      digestPixelsSha3: (pixels, height, width) =>
        digestPixels(pixels, height, width, 'udeep_digest_pixels_sha3', DIGEST_SHA3_LENGTH),

      // The stored levels of a file of this mapping: the widest, in data units, and the data criterion's worst
      // ratio for `noise` = [model, parameter] (NaN when the noise is unknown or not given). `levels` is for an
      // 8-bit gain-map rendering written for the headroom `scaledBy`; `levelsPq16` for a 16-bit PQ file with
      // white at `whiteNits`, and it also counts the levels.
      levels: (m, table = null, scaledBy = m.reference_headroom, samples = 65536, noise = null) =>
        levelsOf('udeep_levels', m, table, scaledBy, samples, noise, false),
      levelsPq16: (m, table = null, whiteNits = REF_WHITE_NITS, samples = 1048576, noise = null) =>
        levelsOf('udeep_levels_pq16', m, table, whiteNits, samples, noise, true),
      // The same levels and the data criterion in full, as the reference's data_steps and rounding_to_noise give
      // them: the count, the widest level, where the first starts, the worst ratio and where it was taken, the
      // fraction over the limit, the limit, and whether the file meets it. `pq16` for a 16-bit PQ file, whose
      // `scaledBy` is then its white level in cd/m^2. The criterion's numbers are NaN when the noise is unknown.
      criterion: (m, table = null, scaledBy = m.reference_headroom, samples = 65536, noise = null, { pq16 = false } = {}) =>
        scoped(take => {
          const [model, parameter] = noise ?? ['unknown', null];
          if (NOISE[model] === undefined) throw new Error(`unknown noise model: ${model}`);
          const record = putMapping(take, m), [at, nodes] = putTable(take, table), out = take(CRITERION_BYTES);
          check(x.udeep_levels_criterion(record, at, nodes, scaledBy, samples, NOISE[model], parameter ?? 0, pq16 ? 1 : 0,
            out));
          const v = view(), number = offset => v.getFloat64(out + offset, true);
          return { count: Number(v.getBigUint64(out, true)), maxDataStep: number(8), firstStart: number(16),
            roundingToNoise: number(24), at: number(32), failingFraction: number(40), limit: number(48),
            meets: v.getUint32(out + 56, true) === 1 };
        }),

      // The data values that a 16-bit PQ file's codes stand for (3 a pixel, in a Uint16Array or an array), as
      // readPngData gives them for a whole file.
      valuesPq16: (m, table, codes, { whiteNits = REF_WHITE_NITS, samples = 1048576 } = {}) => scoped(take => {
        const given = Uint16Array.from(codes);
        if (given.length % 3 !== 0) throw new RangeError(`${given.length} codes are not whole pixels of three`);
        const n = given.length / 3, record = putMapping(take, m), [at, nodes] = putTable(take, table);
        const from = take(2 * given.length), to = take(8 * n);
        new Uint16Array(x.memory.buffer, from, given.length).set(given);
        check(x.udeep_values_pq16(record, at, nodes, whiteNits, samples, from, n, to));
        return doublesAt(to, n).slice();
      }),

      // What a file declares, as the rdf:Description element it carries, and the caption generated from it.
      description: d => scoped(take => text.decode(owned(take, (data, length) => x.udeep_description(putDeclaration(take, d), data, length)))),
      caption: d => scoped(take => text.decode(owned(take, (data, length) => x.udeep_caption(putDeclaration(take, d), data, length)))),

      // A 16-bit PQ PNG of `rgb` (height x width x 3 numbers, row by row: linear RGB, BT.709 primaries, multiples
      // of SDR white), with 1.0 written at `whiteNits`; `xmp` is an XMP packet to carry, as bytes. A packet that
      // holds a declaration is written only with white at 203 cd/m2 (profile 9.1.1; otherwise error 11). And one
      // read back as light, in multiples of the white written at `whiteNits`.
      pngPq: (rgb, width, height, { whiteNits = REF_WHITE_NITS, xmp = null } = {}) => scoped(take => {
        feature('png');
        const numbers = image(rgb, width, height, 'rgb'), packet = xmp === null ? new Uint8Array(0) : asBytes(xmp, 'the XMP packet');
        const from = putDoubles(take, numbers), at = packet.length ? putBytes(take, packet) : 0;
        return owned(take, (data, length) => x.udeep_png_pq(from, width, height, whiteNits, at, packet.length, data, length));
      }),
      readPngPq: (file, whiteNits = REF_WHITE_NITS) => {
        feature('png');
        const bytes = asBytes(file, 'the file'), [width, height] = sized('udeep_png_pq_size', bytes);
        return scoped(take => {
          const n = width * height * 3, from = putBytes(take, bytes), to = take(8 * n);
          check(x.udeep_png_pq_read(from, bytes.length, whiteNits, to, n));
          return { width, height, light: doublesAt(to, n).slice() };
        });
      },
      // The same file read back as data: the data value each pixel stands for, under the mapping `m` and colour
      // map `table` (null for grey) the file declares, whatever its colours. Within half of MaxDataStep; NaN for
      // a pixel that is no colour of this mapping.
      readPngData: (file, m, table = null, { whiteNits = REF_WHITE_NITS, samples = 1048576 } = {}) => {
        feature('png');
        const bytes = asBytes(file, 'the file'), [width, height] = sized('udeep_png_pq_size', bytes);
        return scoped(take => {
          const n = width * height, from = putBytes(take, bytes), record = putMapping(take, m);
          const [at, nodes] = putTable(take, table), to = take(8 * n);
          check(x.udeep_png_pq_values(from, bytes.length, record, at, nodes, whiteNits, samples, to, n));
          return { width, height, values: doublesAt(to, n).slice() };
        });
      },

      // A gain-map JPEG of a figure: `sdr` is linear RGB in 0..1 and `hdr` linear RGB in multiples of SDR white,
      // each height x width x 3 numbers, row by row. `description` is the rdf:Description element to carry
      // (see `description`), or nothing. And one read back as a display with `displayBoost` times SDR white of
      // headroom shows it (1 gives the SDR base): float32, height x width x 3.
      gainmapJpeg: (sdr, hdr, width, height, headroom, { quality = 95, gainQuality = 100, description = '' } = {}) => scoped(take => {
        feature('jpeg');
        const base = putDoubles(take, image(sdr, width, height, 'sdr')), lifted = putDoubles(take, image(hdr, width, height, 'hdr'));
        const element = utf8.encode(description), at = element.length ? putBytes(take, element) : 0;
        return owned(take, (data, length) => x.udeep_gainmap_jpeg(base, lifted, width, height, headroom, quality, gainQuality, at,
          element.length, data, length));
      }),
      readGainmap: (file, displayBoost) => {
        feature('jpeg');
        const bytes = asBytes(file, 'the file'), [width, height] = sized('udeep_gainmap_size', bytes);
        return scoped(take => {
          const n = width * height * 3, from = putBytes(take, bytes), to = take(4 * n);
          check(x.udeep_gainmap_read(from, bytes.length, displayBoost, to, n, 1));
          return { width, height, rgb: new Float32Array(x.memory.buffer, to, n).slice() };
        });
      },
      // A figure's gain-map JPEG straight from four 8-bit renders of it, each height x width x 3 sRGB codes: the
      // figure as it is; with each HDR image divided by the headroom; and with the HDR images black, and white.
      // Returns the file's bytes and the number of pixels lifted above the ordinary figure.
      gainmapFromRenders: (base, scaled, black, white, width, height, headroom, { quality = 95, gainQuality = 100, description = '' } = {}) => scoped(take => {
        feature('jpeg');
        const renders = [[base, 'base'], [scaled, 'scaled'], [black, 'black'], [white, 'white']]
          .map(([pixels, what]) => putBytes(take, render(pixels, width, height, what)));
        const element = utf8.encode(description), at = element.length ? putBytes(take, element) : 0, count = take(4);
        const bytes = owned(take, (data, length) => x.udeep_gainmap_from_renders(...renders, width, height, headroom, quality,
          gainQuality, at, element.length, data, length, count));
        return { bytes, lifted: view().getUint32(count, true) };
      }),
    });
  }

  globalThis.UDeepWasm = { REF_WHITE_NITS, load, makeTable };
})();
