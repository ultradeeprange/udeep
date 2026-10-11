/* The live view. Data goes to the graphics card once; the mapping runs there for every screen pixel on every frame
   (mapping.wgsl, the project's reference shader, with view.wgsl added to draw it), so moving the knee is instant
   and nothing is rendered twice or written to a file. The numbers read out under the pointer come from udeepcore.js
   in float64, the same code that is held to the project's test vectors: the picture is the shader's, the numbers
   are the reference's. All data on this page is made here or opened from your own computer; none is sent anywhere. */
(function () {
  "use strict";
  var H = globalThis.UDeepCore;
  var $ = function (id) { return document.getElementById(id); };
  var canvas = $("view"), statusBox = $("status");
  var HEADROOMS = [2, 4, 8, 16];
  var BAR = 26, GAP = 10;                                   // the colour bar and the gap beside it, in CSS pixels
  var state = { data: null, width: 0, height: 0, unit: "", name: "", mapping: null, table: null, mode: "extend",
                sdrOnly: false, device: null, context: null, format: null, hdr: false, pipeline: null,
                buffers: {}, tables: {}, frame: 0 };

  function say(lines) { statusBox.textContent = ""; lines.forEach(function (l) {
    var li = document.createElement("li"); li.textContent = l[1]; li.className = l[0]; statusBox.appendChild(li); }); }

  /* ---- data made here: seeded, so the page shows the same thing every time. SYNTHETIC, not measurements. */
  function generator(seed) {                                 // mulberry32
    var a = seed >>> 0;
    return function () { a = (a + 0x6D2B79F5) >>> 0; var t = a; t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  }
  function normal(rand) { return Math.sqrt(-2 * Math.log(1 - rand())) * Math.cos(2 * Math.PI * rand()); }
  function phantom(n) {                                      // a CT-style phantom, in HU: soft tissue, lesions, a bone ring
    var rand = generator(20260927), d = new Float32Array(n * n);
    var inside = function (x, y, cx, cy, a, b) { var u = (x - cx) / a, v = (y - cy) / b; return u * u + v * v <= 1; };
    for (var j = 0; j < n; j++) for (var i = 0; i < n; i++) {
      var x = i / n * 2 - 1, y = j / n * 2 - 1, hu = -1000;
      if (inside(x, y, 0, 0, 0.85, 0.7)) {
        hu = 40;
        [[-0.35, 0.2, 0.10, 25], [0.3, 0.25, 0.07, -30], [0.05, -0.35, 0.08, 45], [0.4, -0.2, 0.05, 60]].forEach(function (l) {
          if (inside(x, y, l[0], l[1], l[2], l[2])) hu += l[3]; });
        var texture = 250 + 900 * Math.pow(0.5 + 0.5 * Math.sin(i / 3.1) * Math.sin(j / 4.3), 2);
        if (inside(x, y, 0, 0.05, 0.42, 0.34) && !inside(x, y, 0, 0.05, 0.36, 0.28)) hu = 700 + texture;
        if (inside(x, y, 0, 0.05, 0.30, 0.24) && !inside(x, y, 0, 0.05, 0.27, 0.21)) hu = 300 + 0.6 * texture;
        hu += 6 * normal(rand);
      }
      d[j * n + i] = hu;
    }
    return { data: d, width: n, height: n, unit: "HU", name: "Synthetic CT phantom",
             mapping: { stretch: "linear", range_low: -160, range_high: 1850, knee: 240, reference_headroom: 4,
                        stretch_width: 1, stretch_offset: 0, above_knee: "hermite; exit=1", colour_map: "grey" },
             mode: "extend", rule: "preset; the top of the W400/L40 soft-tissue window" };
  }
  function chirp(nt, nf) {                                   // noise power with a faint chirp, in units of the mean noise power
    var rand = generator(7), d = new Float32Array(nt * nf);
    for (var k = 0; k < d.length; k++) d[k] = -Math.log(1 - rand());
    for (var t = 0; t < nt; t++) {
      var f = Math.round(40 + 0.0012 * t * t);
      if (f < nf - 2) [0.4, 1.0, 0.4].forEach(function (w, o) { d[(nf - 1 - (f - 1 + o)) * nt + t] += 10 * w; });
    }
    return { data: d, width: nt, height: nf, unit: "× mean noise power", name: "Synthetic spectrogram: a chirp in noise",
             mapping: { stretch: "linear", range_low: 0, range_high: 20, knee: 9.21, reference_headroom: 4,
                        stretch_width: 1, stretch_offset: 0, above_knee: "hermite; exit=1", colour_map: "fire/2" },
             mode: "highlight", rule: "statistical; noise alone exceeds it once in 10,000 tiles (ln 10⁴ × the mean)" };
  }

  /* ---- a NumPy .npy file from the reader's own computer: two dimensions, float32 or float64, little-endian */
  function parseNpy(buffer) {
    var bytes = new Uint8Array(buffer);
    if (bytes.length < 12 || bytes[0] !== 0x93 || String.fromCharCode.apply(null, bytes.subarray(1, 6)) !== "NUMPY") {
      throw new Error("this is not a .npy file"); }
    var view = new DataView(buffer), major = bytes[6];
    var headerLength = major === 1 ? view.getUint16(8, true) : view.getUint32(8, true), start = major === 1 ? 10 : 12;
    var header = String.fromCharCode.apply(null, bytes.subarray(start, start + headerLength));
    var descr = /'descr':\s*'([^']+)'/.exec(header), shape = /'shape':\s*\(([^)]*)\)/.exec(header);
    if (!descr || !shape) throw new Error("the .npy header cannot be read");
    if (/'fortran_order':\s*True/.test(header)) throw new Error("the array is stored column by column: save it with numpy.ascontiguousarray");
    var dims = shape[1].split(",").map(function (v) { return v.trim(); }).filter(Boolean).map(Number);
    if (dims.length !== 2) throw new Error("the array must have two dimensions; this one has " + dims.length);
    var size = { "<f4": 4, "<f8": 8 }[descr[1]];
    if (!size) throw new Error("the numbers must be float32 or float64, little-endian; this file holds " + descr[1]);
    var count = dims[0] * dims[1], at = start + headerLength;
    if (count > 16e6) throw new Error("the image is larger than 16 megapixels");
    if (bytes.length < at + count * size) throw new Error("the file is shorter than its header says");
    var data = new Float32Array(count);
    for (var i = 0; i < count; i++) data[i] = size === 4 ? view.getFloat32(at + 4 * i, true) : view.getFloat64(at + 8 * i, true);
    return { data: data, width: dims[1], height: dims[0] };
  }
  function percentiles(data, list) {                        // of the finite values, from a sample of at most 200,000
    var step = Math.max(1, Math.floor(data.length / 200000)), sample = [];
    for (var i = 0; i < data.length; i += step) if (Number.isFinite(data[i])) sample.push(data[i]);
    sample.sort(function (a, b) { return a - b; });
    return list.map(function (p) { return sample[Math.min(sample.length - 1, Math.round(p / 100 * (sample.length - 1)))]; });
  }

  /* ---- the mapping, as the controls have it */
  function mappingFromControls() {
    var m = { stretch: $("stretch").value, range_low: Number($("low").value), range_high: Number($("high").value),
              knee: Number($("knee").value), reference_headroom: Number($("headroom").value),
              stretch_width: Number($("width").value) || 1, stretch_offset: 0, above_knee: "hermite; exit=1",
              colour_map: $("map").value };
    if (!(m.range_high > m.range_low)) throw new Error("the top of the range must be above the bottom");
    if (m.stretch === "log" && !(m.range_low > 0)) throw new Error("a log stretch needs a bottom above zero");
    if (!(m.knee > m.range_low && m.knee <= m.range_high)) throw new Error("the knee must lie inside the range");
    H.kneePosition(m);                                     // refuses a knee too near either end
    return m;
  }
  function setControls(m, mode) {
    $("stretch").value = m.stretch; $("low").value = m.range_low; $("high").value = m.range_high;
    $("width").value = m.stretch_width; $("headroom").value = String(m.reference_headroom); $("map").value = m.colour_map;
    $("mode").value = mode;
    var slider = $("kneeSlider");
    slider.min = 0.001; slider.max = 1; slider.step = 0.001;
    $("knee").value = m.knee;
    slider.value = H.position(m, m.knee);
    $("widthRow").hidden = $("widthLabel").hidden = m.stretch !== "asinh";
  }
  function fmt(v) { return Number.isFinite(v) ? Number(v.toPrecision(4)).toString() : "no data"; }

  /* ---- tables: the registered maps, as the project ships them */
  function tableFor(name, headroom) {
    if (name === "grey") return Promise.resolve(null);
    var file = "tables/" + name.replace("/", "-") + "-h" + headroom + ".f32";
    if (state.tables[file]) return Promise.resolve(state.tables[file]);
    return fetch(file).then(function (r) {
      if (!r.ok) throw new Error("the colour-map table " + file + " could not be loaded");
      return r.arrayBuffer();
    }).then(function (buffer) { return (state.tables[file] = H.makeTable(buffer, headroom)); });
  }

  /* ---- which pixels are held: no data, and for Highlight the pixels above the knee that stand alone */
  function heldPixels(m) {
    var n = state.width * state.height, out = new Uint32Array(n), any = false, kneeT = H.kneePosition(m);
    var above = state.mode === "highlight" ? [] : null;
    for (var j = 0; j < state.height; j++) {
      var row = above ? new Array(state.width) : null;
      for (var i = 0; i < state.width; i++) {
        var v = state.data[j * state.width + i];
        if (!Number.isFinite(v)) { out[j * state.width + i] = 2; any = true; if (row) row[i] = false; continue; }
        if (row) row[i] = H.position(m, v) > kneeT;
      }
      if (above) above.push(row);
    }
    var alone = 0;
    if (above) H.isolated(above).forEach(function (row, j) { row.forEach(function (lone, i) {
      if (lone) { out[j * state.width + i] = 1; any = true; alone++; } }); });
    return { values: out, any: any, alone: alone };
  }

  /* ---- the graphics card */
  function start() {
    if (!navigator.gpu) return Promise.reject(new Error("this browser has no WebGPU"));
    return navigator.gpu.requestAdapter({ powerPreference: "high-performance" }).then(function (adapter) {
      if (!adapter) throw new Error("no graphics adapter was offered");
      return adapter.requestDevice();
    }).then(function (device) {
      state.device = device;
      state.context = canvas.getContext("webgpu");
      // A float canvas that may show values above 1 as brighter than white. Where that is refused, an ordinary one.
      try {
        state.context.configure({ device: device, format: "rgba16float", alphaMode: "premultiplied",
                                  toneMapping: { mode: "extended" } });
        var got = state.context.getConfiguration ? state.context.getConfiguration() : null;
        state.format = "rgba16float";
        state.hdr = !got || !got.toneMapping || got.toneMapping.mode === "extended";
      } catch (e) {
        state.format = navigator.gpu.getPreferredCanvasFormat();
        state.context.configure({ device: device, format: state.format, alphaMode: "premultiplied" });
        state.hdr = false;
      }
      return Promise.all([fetch("mapping.wgsl").then(function (r) { return r.text(); }),
                          fetch("view.wgsl").then(function (r) { return r.text(); })]);
    }).then(function (sources) {
      var module = state.device.createShaderModule({ code: sources[0] + "\n" + sources[1] });
      state.pipeline = state.device.createRenderPipeline({ layout: "auto",
        vertex: { module: module, entryPoint: "view_vertex" },
        fragment: { module: module, entryPoint: "view_fragment", targets: [{ format: state.format }] },
        primitive: { topology: "triangle-list" } });
    });
  }
  function buffer(name, data, usage) {
    var old = state.buffers[name];
    if (old && old.size === Math.max(data.byteLength, 16)) { state.device.queue.writeBuffer(old, 0, data); return old; }
    if (old) old.destroy();
    var made = state.device.createBuffer({ size: Math.max(Math.ceil(data.byteLength / 4) * 4, 16), usage: usage | GPUBufferUsage.COPY_DST });
    state.device.queue.writeBuffer(made, 0, data);
    return (state.buffers[name] = made);
  }
  function draw() {
    if (!state.pipeline || !state.data || !state.mapping) return;
    // the image keeps its own shape: the canvas is as wide as the page gives it, and as tall as that makes the image
    var wide = canvas.clientWidth;
    canvas.style.height = Math.max(1, (wide - BAR - GAP) * state.height / state.width) + "px";
    var ratio = window.devicePixelRatio || 1, box = canvas.getBoundingClientRect();
    var w = Math.max(1, Math.round(box.width * ratio)), h = Math.max(1, Math.round(box.height * ratio));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    var m = state.mapping, table = state.table, G = GPUBufferUsage;
    var block = buffer("mapping", H.uniformBlock(m, table ? table.nodes : 0), G.UNIFORM);
    var nodes = buffer("table", table ? table.values : new Float32Array(6), G.STORAGE);
    var view = new ArrayBuffer(32), u = new Uint32Array(view), f = new Float32Array(view);
    u[0] = state.width; u[1] = state.height; u[2] = state.sdrOnly ? 1 : 0; u[3] = state.held.any ? 1 : 0;
    f[4] = w; f[5] = h; f[6] = BAR * ratio; f[7] = GAP * ratio;
    var viewBuffer = buffer("view", view, G.UNIFORM);
    var held = buffer("held", state.held.any ? state.held.values : new Uint32Array(4), G.STORAGE);
    var device = state.device;
    var groups = [device.createBindGroup({ layout: state.pipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: { buffer: block } }, { binding: 1, resource: { buffer: nodes } },
        { binding: 2, resource: { buffer: state.buffers.values } }] }),
      device.createBindGroup({ layout: state.pipeline.getBindGroupLayout(1), entries: [
        { binding: 0, resource: { buffer: viewBuffer } }, { binding: 1, resource: { buffer: held } }] })];
    var encoder = device.createCommandEncoder();
    var pass = encoder.beginRenderPass({ colorAttachments: [{ view: state.context.getCurrentTexture().createView(),
      clearValue: { r: 0, g: 0, b: 0, a: 0 }, loadOp: "clear", storeOp: "store" }] });
    pass.setPipeline(state.pipeline); pass.setBindGroup(0, groups[0]); pass.setBindGroup(1, groups[1]);
    pass.draw(3); pass.end();
    device.queue.submit([encoder.finish()]);
    state.frame++;
    $("frames").textContent = String(state.frame);
  }

  /* ---- what the page says about the mapping */
  function describe() {
    var m = state.mapping, kneeT = H.kneePosition(m);
    var ticks = [[1, fmt(m.range_high)], [kneeT, "SDR white " + fmt(m.knee)], [0, fmt(m.range_low)]];
    var bar = $("ticks"); bar.textContent = "";
    ticks.forEach(function (t) {
      if (t[0] !== kneeT && Math.abs(t[0] - kneeT) < 0.06) return;        // a regular tick must not overprint the marker
      var s = document.createElement("span"); s.textContent = t[1]; s.style.top = (100 * (1 - t[0])) + "%"; bar.appendChild(s); });
    var above = 0, finite = 0;
    for (var i = 0; i < state.data.length; i += Math.max(1, Math.floor(state.data.length / 100000))) {
      if (Number.isFinite(state.data[i])) { finite++; if (state.data[i] > m.knee) above++; } }
    var rows = [["DataUnit", state.unit || "(not stated)"], ["Stretch", m.stretch], ["RangeLow", fmt(m.range_low)],
      ["RangeHigh", fmt(m.range_high)], ["Knee", fmt(m.knee) + " " + state.unit], ["KneeRule", state.rule],
      ["ReferenceHeadroom", String(m.reference_headroom)], ["ColourMap", m.colour_map], ["Preset", state.mode]];
    if (m.stretch === "asinh") rows.splice(2, 0, ["StretchWidth", fmt(m.stretch_width)]);
    if (state.mode === "highlight") rows.push(["DerivedMask", "isolated-3x3: " + state.held.alone + " pixels held at white"]);
    var list = $("declared"); list.textContent = "";
    rows.forEach(function (r) { var dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = "udeep:" + r[0]; dd.textContent = r[1]; list.appendChild(dt); list.appendChild(dd); });
    $("share").textContent = finite ? (100 * above / finite).toFixed(above / finite < 0.1 ? 2 : 1) + " %" : "none";
    $("title").textContent = state.name + ".";
  }
  function refresh(reload) {
    var m;
    try { m = mappingFromControls(); } catch (e) { $("problem").textContent = e.message; return Promise.resolve(); }
    $("problem").textContent = "";
    state.mode = $("mode").value;
    return tableFor(m.colour_map, m.reference_headroom).then(function (table) {
      state.mapping = m; state.table = table;
      state.held = heldPixels(m);
      if (state.device && (reload || !state.buffers.values)) buffer("values", state.cleaned, GPUBufferUsage.STORAGE);
      describe(); draw();
    }).catch(function (e) { $("problem").textContent = e.message; });
  }
  function show(set) {
    state.data = set.data; state.width = set.width; state.height = set.height; state.unit = set.unit; state.name = set.name;
    state.rule = set.rule; state.synthetic = !!set.synthetic;
    // the card is given finite numbers only: a pixel with no data is marked as such and drawn black
    state.cleaned = new Float32Array(set.data.length);
    for (var i = 0; i < set.data.length; i++) state.cleaned[i] = Number.isFinite(set.data[i]) ? set.data[i] : set.mapping.range_low;
    setControls(set.mapping, set.mode);
    return refresh(true);
  }

  /* ---- the reference's numbers under the pointer */
  canvas.addEventListener("pointermove", function (e) {
    if (!state.mapping) return;
    var box = canvas.getBoundingClientRect(), imageWidth = box.width - BAR - GAP;
    var x = e.clientX - box.left, y = e.clientY - box.top, m = state.mapping, out = $("readout");
    if (x >= imageWidth || x < 0 || y < 0 || y >= box.height) { out.textContent = "Move over the image for its values."; return; }
    var i = Math.min(state.width - 1, Math.floor(x / imageWidth * state.width));
    var j = Math.min(state.height - 1, Math.floor(y / box.height * state.height));
    var v = state.data[j * state.width + i];
    if (!Number.isFinite(v)) { out.textContent = "pixel (" + i + ", " + j + "): no data"; return; }
    var isHeld = state.held.values[j * state.width + i] === 1;
    var light = H.light(m, v, isHeld || state.sdrOnly ? 1 : undefined);
    out.textContent = "pixel (" + i + ", " + j + "): " + fmt(v) + " " + state.unit + " → position " + H.position(m, v).toFixed(4) +
      " → " + light.toFixed(3) + " × SDR white" + (isHeld ? " (held: it stands alone above the knee)" : "");
  });

  /* ---- a check the page makes of itself: the shader against the reference, on a strip across the range */
  function selfCheck() {
    if (state.format !== "rgba16float") return Promise.resolve("not made: the canvas is not a float one here");
    var device = state.device, n = 256, m = state.mapping, table = state.table;
    var xs = new Float32Array(n);
    for (var i = 0; i < n; i++) xs[i] = H.valueAt(m, i / (n - 1));
    var G = GPUBufferUsage, usage = G.COPY_DST;
    var mk = function (data, use) { var b = device.createBuffer({ size: Math.max(Math.ceil(data.byteLength / 4) * 4, 16), usage: use | usage });
      device.queue.writeBuffer(b, 0, data); return b; };
    var view = new ArrayBuffer(32), u = new Uint32Array(view), f = new Float32Array(view);
    u[0] = n; u[1] = 1; u[2] = 0; u[3] = 0; f[4] = n; f[5] = 1; f[6] = 0; f[7] = 0;
    var target = device.createTexture({ size: [n, 1], format: "rgba16float", usage: GPUTextureUsage.RENDER_ATTACHMENT | GPUTextureUsage.COPY_SRC });
    var groups = [device.createBindGroup({ layout: state.pipeline.getBindGroupLayout(0), entries: [
        { binding: 0, resource: { buffer: mk(H.uniformBlock(m, table ? table.nodes : 0), G.UNIFORM) } },
        { binding: 1, resource: { buffer: mk(table ? table.values : new Float32Array(6), G.STORAGE) } },
        { binding: 2, resource: { buffer: mk(xs, G.STORAGE) } }] }),
      device.createBindGroup({ layout: state.pipeline.getBindGroupLayout(1), entries: [
        { binding: 0, resource: { buffer: mk(view, G.UNIFORM) } }, { binding: 1, resource: { buffer: mk(new Uint32Array(4), G.STORAGE) } }] })];
    var out = device.createBuffer({ size: n * 8, usage: G.COPY_DST | G.MAP_READ });
    var encoder = device.createCommandEncoder();
    var pass = encoder.beginRenderPass({ colorAttachments: [{ view: target.createView(), loadOp: "clear", storeOp: "store",
      clearValue: { r: 0, g: 0, b: 0, a: 0 } }] });
    pass.setPipeline(state.pipeline); pass.setBindGroup(0, groups[0]); pass.setBindGroup(1, groups[1]); pass.draw(3); pass.end();
    encoder.copyTextureToBuffer({ texture: target }, { buffer: out, bytesPerRow: n * 8 }, [n, 1]);
    device.queue.submit([encoder.finish()]);
    return out.mapAsync(GPUMapMode.READ).then(function () {
      var half = new Uint16Array(out.getMappedRange().slice(0)), worst = 0;
      var toFloat = function (h) { var s = (h & 0x8000) ? -1 : 1, e = (h >> 10) & 31, q = h & 1023;
        return e === 0 ? s * Math.pow(2, -14) * (q / 1024) : e === 31 ? (q ? NaN : s * Infinity) : s * Math.pow(2, e - 15) * (1 + q / 1024); };
      var linear = function (c) { return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
      for (var i = 0; i < n; i++) {
        // the reference maps the number the card was given: the float32 nearest the value
        var want = H.colours(m, H.light(m, xs[i]), table);
        for (var c = 0; c < 3; c++) {
          var got = linear(toFloat(half[4 * i + c]));
          worst = Math.max(worst, Math.abs(got - want[c]) / Math.max(want[c], 0.05));
        }
      }
      out.unmap();
      return "the shader agrees with the float64 reference to " + (100 * worst).toFixed(2) + " % at worst, over " + n +
        " values across the range (a 16-bit float canvas resolves about 0.1 %)";
    });
  }

  /* ---- wiring */
  var sets = { phantom: function () { var s = phantom(512); s.synthetic = true; return s; },
               chirp: function () { var s = chirp(400, 300); s.synthetic = true; return s; } };
  ["stretch", "low", "high", "width", "headroom", "map", "mode"].forEach(function (id) {
    $(id).addEventListener("change", function () {
      $("widthRow").hidden = $("widthLabel").hidden = $("stretch").value !== "asinh";
      refresh(false).then(report);
    }); });
  $("knee").addEventListener("change", function () {
    refresh(false).then(function () { if (state.mapping) $("kneeSlider").value = H.position(state.mapping, state.mapping.knee); });
  });
  $("kneeSlider").addEventListener("input", function () {
    // the slider is the knee's position in the range; the box beside it holds the data value that is
    var range = { stretch: $("stretch").value, range_low: Number($("low").value), range_high: Number($("high").value),
                  stretch_width: Number($("width").value) || 1, stretch_offset: 0 };
    var value = H.valueAt(range, Number($("kneeSlider").value));
    if (Number.isFinite(value)) { $("knee").value = fmt(value); refresh(false); }
  });
  $("dataset").addEventListener("change", function () { show(sets[$("dataset").value]()).then(report); });
  $("sdr").addEventListener("change", function () { state.sdrOnly = $("sdr").checked; draw(); });
  $("file").addEventListener("change", function () {
    var file = $("file").files[0];
    if (!file) return;
    file.arrayBuffer().then(function (buffer) {
      var set = parseNpy(buffer), p = percentiles(set.data, [0.5, 95, 99.9]);
      if (!(p[2] > p[0])) throw new Error("the array holds one value only");
      set.unit = ""; set.name = file.name; set.mode = "extend";
      set.rule = "percentile; the 95th percentile of the values (exploratory: set a meaningful knee)";
      set.mapping = { stretch: "linear", range_low: p[0], range_high: p[2], knee: Math.min(Math.max(p[1], p[0] + 0.01 * (p[2] - p[0])), p[2]),
                      reference_headroom: 4, stretch_width: 1, stretch_offset: 0, above_knee: "hermite; exit=1", colour_map: "grey" };
      return show(set).then(report);
    }).catch(function (e) { $("problem").textContent = e.message; });
  });
  window.addEventListener("resize", draw);
  if (window.matchMedia) {
    var query = window.matchMedia("(dynamic-range: high)");
    if (query.addEventListener) query.addEventListener("change", report);
  }

  function report() {
    var high = window.matchMedia && window.matchMedia("(dynamic-range: high)").matches;
    var lines = [];
    if (!state.device) return;
    lines.push(state.hdr ? ["yes", "The canvas is a float one that may show values above white as brighter than white."]
                         : ["no", "This browser gave an ordinary canvas: values above white are shown as white. The mapping is the same."]);
    lines.push(high ? ["yes", "The screen reports a high dynamic range to the page."]
                    : ["no", "The screen does not report a high dynamic range to the page, so nothing can be brighter than white here."]);
    lines.push(["note", "How much brighter than white this screen can go is not something a page is told."]);
    say(lines.concat([["note", "Checking the shader against the reference…"]]));
    return selfCheck().then(function (text) {
      say(lines.concat([[/agrees/.test(text) ? "yes" : "note", "Self-check: " + text + "."]]));
      document.documentElement.setAttribute("data-view", "ready");
    }).catch(function (e) { say(lines.concat([["no", "Self-check failed to run: " + e.message]]));
      document.documentElement.setAttribute("data-view", "ready"); });
  }

  // a link may name the data to open with: view/#chirp
  var first = sets[location.hash.slice(1)] ? location.hash.slice(1) : "phantom";
  $("dataset").value = first;
  start().then(function () { return show(sets[first]()); }).then(report).catch(function (e) {
    say([["no", "The live view needs WebGPU, and it is not available here: " + e.message + ". " +
          "The figures in the gallery need nothing but an image viewer."]]);
    canvas.hidden = true;
    document.documentElement.setAttribute("data-view", "unavailable");
  });
})();
