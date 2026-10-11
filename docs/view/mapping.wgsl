// The mapping of the UDeep Figure Profile, as a shader (design decision D23).
//
//     value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit
//
// The three functions `position`, `tone` and `colour` are the mapping. A viewer calls them from its own fragment
// shader; the compute entry point at the end exists so that they can be run on a GPU and read back, which is how
// they are held to the golden vectors (tests/test_udeep_gpu.py).
//
// This is the float32 form of the float64 reference (decision D8). The host works out, in float64, everything
// that does not depend on the pixel and passes it in the uniform block: see udeep/gpu.py, `uniform_block`, which
// documents the same layout and is tested against it. The GPU renders; the CPU reference defines (D15).
//
// A float32 constant cannot hold a number such as 273.15, and the difference `value - 273.15` then carries the
// whole rounding error of the constant. So the constants that are subtracted come as a pair of float32: the
// nearest float32 (`_hi`) and what is left of the float64 number after that (`_lo`). `_hi` is subtracted first,
// which is exact or nearly so; the result is multiplied by the factor that follows it in the chain; and `_lo`,
// which the host has multiplied by the same factor, is subtracted last:
//
//     (value - c_hi) * k - c_lo        for        (value - c) * k
//
// The multiplication stands between the two subtractions on purpose. Written as (value - c_hi) - c_lo, a shader
// compiler is free to add the two constants first, and at least one does: the pair then does nothing at all.
//
// What float32 cannot do, the host refuses (gpu.py, `check`): the shader may assume a mapping it is given is one
// for which the tolerances of D8 can hold: 2e-6, relative and absolute, in position and light, and 1e-5 in
// colour. The limits, found by measurement and set out in full in gpu.py's docstring, are:
//   - every number in the block is an ordinary float32: finite, and zero or at least about 1.2e-38 in size;
//   - log and asinh: the result of the function is itself a float32, out by about 2.2e-7 (log) or 1e-7 to 2.6e-7
//     of itself (asinh) whatever the range, so a narrow stretched range is too coarse: a log range must span a
//     ratio of 1.25 to 1.4 at least (knee in the middle, headroom 2 to 4). A linear stretch has no such limit;
//   - the light is out by the position's error times the slope of the curve, and a colour by the light's error
//     times the slope of the table. So a knee near the top is refused (grey, linear: above about position 0.95
//     at headroom 2, 0.75 at 16; fire/1 at headroom 2, whose table is steep near its top: above 0.85), as is a
//     knee low in a log range, and a colour map at a headroom between 1 and about 1.03.
//
// Outside the contract: NaN. A shader may assume there is none. Invalid pixels are the mask's job (D2): the host
// holds them at white before or after this shader, and never passes them through it expecting NaN back.

// 64 bytes: twelve float32, then four uint32.
struct Mapping {
    offset_hi: f32,            // subtracted from the value first. log, asinh: stretch_offset.
    offset_lo: f32,            //   linear, proportional: stretch_offset + range_low. `_lo` is times `scale`
    scale: f32,                // linear: 1 / (range_high - range_low). log: 1 / range_low. asinh: 1 / stretch_width
    low_hi: f32,               // subtracted after the stretch. asinh: asinh(range_low / width). linear and log:
    low_lo: f32,               //   0, because their low end is already at 0. `_lo` is times `inv_span`
    inv_span: f32,             // 1 / (stretch(range_high) - stretch(range_low)); 1 for linear, whose `scale` has it
    knee_hi: f32,              // where the knee sits in [0, 1]
    knee_lo: f32,              //   `_lo` is divided by `shoulder_width`
    shoulder_width: f32,       // 1 - knee position (1 when the knee is at the top and there is no shoulder)
    reference_headroom: f32,   // light at the top of the range
    inv_headroom_span: f32,    // 1 / (reference_headroom - 1); 0 when the headroom is 1
    exit_slope: f32,           // form above the knee; 1 is the profile's reference
    stretch: u32,              // 0 linear, 1 log, 2 asinh, 3 proportional
    nodes: u32,                // colour-map table nodes per segment; 0 means grey
    reserved0: u32,
    reserved1: u32,
}

@group(0) @binding(0) var<uniform> m: Mapping;
// The colour-map table: the segment for light 0..1, then the one for 1..headroom, each `nodes` x 3 numbers.
@group(0) @binding(1) var<storage, read> table: array<f32>;

// Data value -> position in [0, 1]. For a log stretch a value at or below zero is position 0.
fn position(value: f32) -> f32 {
    let v = (value - m.offset_hi) * m.scale - m.offset_lo;
    var f = v;                                 // linear: already the position
    if (m.stretch == 1u) {
        if (v <= 0.0) {
            return 0.0;
        }
        f = log(v);                            // v is value / range_low, so this is 0 at the bottom of the range
    } else if (m.stretch == 2u) {
        f = asinh(v);
    }
    return clamp((f - m.low_hi) * m.inv_span - m.low_lo, 0.0, 1.0);
}

// Position -> light. Below the knee, t / knee. Above it, a cubic Hermite segment from 1 to `headroom` that starts
// with the slope of the part below (capped at three times its own average slope) and ends with `exit_slope` times
// that average slope. tone(t, 1.0, 1.0) is the fair SDR light: the same below the knee, white above it.
fn tone(t: f32, headroom: f32, exit_slope: f32) -> f32 {
    let knee = m.knee_hi;
    let width = m.shoulder_width;
    let past = (t - m.knee_hi) / width - m.knee_lo;       // how far along the shoulder, 0 to 1
    if (knee >= 1.0 || past <= 0.0) {
        return clamp(t / knee, 0.0, headroom);
    }
    let secant = (headroom - 1.0) / width;
    let entry = min(1.0 / knee, 3.0 * secant);
    let u = min(past, 1.0);
    let u2 = u * u;
    let u3 = u2 * u;
    let y = (2.0 * u3 - 3.0 * u2 + 1.0)
        + (u3 - 2.0 * u2 + u) * width * entry
        + (-2.0 * u3 + 3.0 * u2) * headroom
        + (u3 - u2) * width * exit_slope * secant;
    return clamp(y, 0.0, headroom);
}

// Light -> linear RGB (BT.709 primaries, multiples of SDR white): linear interpolation between table nodes.
// The interpolation is written out, not left to texture filtering, whose weights are too coarse.
fn colour(y: f32) -> vec3f {
    if (m.nodes == 0u) {
        return vec3f(y);
    }
    let v = clamp(y, 0.0, max(m.reference_headroom, 1.0));
    let high = v > 1.0;
    let along = select(v, (v - 1.0) * m.inv_headroom_span, high);
    let p = along * f32(m.nodes - 1u);
    let i = min(u32(floor(p)), m.nodes - 2u);
    let f = p - f32(i);
    let at = 3u * (i + select(0u, m.nodes, high));
    let a = vec3f(table[at], table[at + 1u], table[at + 2u]);
    let b = vec3f(table[at + 3u], table[at + 4u], table[at + 5u]);
    return a * (1.0 - f) + b * f;
}

// ---- the test harness: map a buffer of values and write six numbers for each

@group(0) @binding(2) var<storage, read> values: array<f32>;
// For each value: position, light, fair SDR light, red, green, blue.
@group(0) @binding(3) var<storage, read_write> results: array<f32>;

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) id: vec3u) {
    let i = id.x;
    if (i >= arrayLength(&values)) {
        return;
    }
    let t = position(values[i]);
    let y = tone(t, m.reference_headroom, m.exit_slope);
    let rgb = colour(y);
    results[6u * i] = t;
    results[6u * i + 1u] = y;
    results[6u * i + 2u] = tone(t, 1.0, 1.0);
    results[6u * i + 3u] = rgb.r;
    results[6u * i + 4u] = rgb.g;
    results[6u * i + 5u] = rgb.b;
}
