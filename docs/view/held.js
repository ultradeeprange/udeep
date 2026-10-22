/* Which pixels the live view holds: those with no data, and for the Highlight preset the pixels above the knee that
   stand alone (the isolated-3x3 rule), which are held at white so that noise cannot sparkle. Worked out in the
   WebAssembly build of the project's Rust core where the page has it (udeepwasm.js), the whole image in one call,
   and otherwise in the JavaScript port (udeepcore.js), a pixel at a time: the two give the same pixels
   (tests/test_view.py). Nothing here touches the page, so node runs it as the page does. */
(() => {
  "use strict";

  // m: the mapping record; data: the values, row by row; core: the WebAssembly core, or null; port: the port.
  // Returns { values: 0 shown, 1 held, 2 no data, for each pixel; any: whether any is not 0; alone: how many held }.
  function heldPixels(m, data, width, height, highlight, core, port) {
    const n = width * height, out = new Uint32Array(n);
    let any = false, alone = 0;
    const kneeT = (core || port).kneePosition(m);
    const above = highlight ? new Uint8Array(n) : null;
    const position = core ? core.position(m, data) : null;
    for (let i = 0; i < n; i++) {
      if (!Number.isFinite(data[i])) { out[i] = 2; any = true; continue; }
      if (above && (core ? position[i] : port.position(m, data[i])) > kneeT) above[i] = 1;
    }
    if (above) {
      const lone = core ? core.isolated(above, height, width) : portIsolated(port, above, width, height);
      for (let i = 0; i < n; i++) if (lone[i]) { out[i] = 1; any = true; alone++; }
    }
    return { values: out, any, alone };
  }

  // the port's rule takes rows of true and false
  function portIsolated(port, above, width, height) {
    const rows = [];
    for (let j = 0; j < height; j++) rows.push(Array.from(above.subarray(j * width, (j + 1) * width), Boolean));
    const lone = new Uint8Array(width * height);
    port.isolated(rows).forEach((row, j) => row.forEach((v, i) => { if (v) lone[j * width + i] = 1; }));
    return lone;
  }

  globalThis.UDeepViewHeld = heldPixels;
})();
