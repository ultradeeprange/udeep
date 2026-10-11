// The live view: a fragment shader that draws an image, and its colour bar, through the mapping of mapping.wgsl.
// This text is added after mapping.wgsl, whose functions `position`, `tone` and `colour` are the mapping itself and
// whose `m`, `table` and `values` it uses. Nothing here changes what a value's light is: it only decides which
// value a screen pixel shows, and writes the light in the form the canvas takes.

struct View {
    image: vec2u,              // the data's size in pixels: `values` holds image.x * image.y numbers, row by row
    sdr_only: u32,             // 1: the fair SDR plot (the same below the knee, white above it)
    use_mask: u32,             // 1: `held` says which pixels are held at white, or have no data
    canvas: vec2f,             // the canvas in device pixels
    bar: vec2f,                // the colour bar: its width, and the gap between it and the image, in device pixels
}

@group(1) @binding(0) var<uniform> view: View;
// One number for each data pixel: 0 as mapped, 1 held at SDR white (a mask), 2 no data (drawn black).
@group(1) @binding(1) var<storage, read> held: array<u32>;

@vertex
fn view_vertex(@builtin(vertex_index) index: u32) -> @builtin(position) vec4f {
    // one triangle that covers the canvas
    let corner = vec2f(f32((index << 1u) & 2u), f32(index & 2u));
    return vec4f(corner * 2.0 - 1.0, 0.0, 1.0);
}

// Linear light -> the canvas's signal. The canvas is float, in the sRGB colour space with values allowed above 1:
// the sRGB curve is carried on past white, so that 1 is SDR white and 2 is twice as bright.
fn signal(linear: vec3f) -> vec3f {
    let c = max(linear, vec3f(0.0));
    let low = 12.92 * c;
    let high = 1.055 * pow(c, vec3f(1.0 / 2.4)) - 0.055;
    return select(high, low, c <= vec3f(0.0031308));
}

fn shown(t: f32, state: u32) -> vec3f {
    if (state == 2u) {
        return vec3f(0.0);
    }
    let fair = view.sdr_only == 1u || state == 1u;
    let y = select(tone(t, m.reference_headroom, m.exit_slope), tone(t, 1.0, 1.0), fair);
    return colour(y);
}

@fragment
fn view_fragment(@builtin(position) at: vec4f) -> @location(0) vec4f {
    let image_width = view.canvas.x - view.bar.x - view.bar.y;
    if (at.x >= image_width) {
        if (at.x < image_width + view.bar.y) {
            discard;                                  // the gap: the page shows through
        }
        // the colour bar: even in position, the top of the range at the top
        let t = 1.0 - at.y / view.canvas.y;
        return vec4f(signal(shown(t, 0u)), 1.0);
    }
    // the nearest data pixel: no interpolation, a value is never invented between two
    let px = min(vec2u(at.xy / vec2f(image_width, view.canvas.y) * vec2f(view.image)), view.image - vec2u(1u));
    let i = px.y * view.image.x + px.x;
    var state = 0u;
    if (view.use_mask == 1u) {
        state = held[i];
    }
    return vec4f(signal(shown(position(values[i]), state)), 1.0);
}
