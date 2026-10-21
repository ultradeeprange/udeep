//! The colour-map generator: how the registered tables were made, ported from the Python reference
//! (`udeep.cmap.search` and `generate`), so that a figure at a reference headroom with no published table can make
//! one and carry it in the file (profile 6.10, `udeep:ColourMapTable`).
//!
//! A registered map at a registered headroom is never made here: the published table is its definition (6.10).
//! The generator works in Jzazbz (Safdar et al., Opt. Express 25(13), 15131, 2017). For light `y <= 1` a colour's
//! lightness is that of a grey of luminance `y * top`; above white, luminance rises from `top` to the headroom
//! while chroma tapers to nothing. Chroma is then reduced, by fixed-step bisection, until the colour fits
//! `[0, 1]` (below white) or `[0, headroom]` (above). The module documentation of `udeep.cmap` gives the history
//! of the three versions.
//!
//! The arithmetic follows the reference step for step, in `f64`, but a table made here is checked against the
//! reference's to a tolerance (`tests/cmap.rs`), not bit for bit: the file carries whichever table drew it, so a
//! reader needs no generator at all.

use crate::{Error, matrix3};

/// SDR white in cd/m^2 (BT.2408): Jzazbz takes absolute light.
const WHITE_NITS: f64 = 203.0;
/// Halvings of the chroma scale, from 0..1, for each colour.
pub const BISECTION_STEPS: u32 = 24;
/// Nodes per segment of a table made for a headroom with no published table and carried in the file (it must fit
/// a JPEG metadata segment). A published table has 4097.
pub const EMBED_NODES: usize = 257;
/// The most nodes a segment may be asked for: far more than any table needs, and few enough to make in a second.
pub const MAX_NODES: usize = 1 << 16;

/// A map's definition: `top` is the grey luminance at SDR white; hue (unwrapped degrees, Jzazbz) and chroma are
/// given at `s = Jz / Jz(top)` and interpolated linearly between.
struct Definition {
    top: f64,
    s: [f64; 4],
    hue: [f64; 4],
    chroma: [f64; 4],
}

const S: [f64; 4] = [0.0, 0.45, 0.75, 1.0];
const FIRE: Definition =
    Definition { top: 0.8, s: S, hue: [300.0, 405.0, 445.0, 462.0], chroma: [0.0, 0.14, 0.16, 0.10] };
const ICE_1: Definition =
    Definition { top: 0.7, s: S, hue: [290.0, 257.0, 225.0, 204.0], chroma: [0.0, 0.16, 0.12, 0.07] };
const ICE_2: Definition = Definition { chroma: [0.0, 0.13, 0.12, 0.07], ..ICE_1 };

/// A registered map by name and version: its definition, whether its chroma is measured from the grey of the same
/// lightness (versions 2 and 3), and the value below which a channel is stored as zero (version 3).
struct Map {
    definition: &'static Definition,
    grey_axis: bool,
    floor: f64,
}

fn map(name: &str, version: u32) -> Option<Map> {
    let definition = match (name, version) {
        ("fire", 1..=3) => &FIRE,
        ("ice", 1) => &ICE_1,
        ("ice", 2 | 3) => &ICE_2,
        _ => return None,
    };
    Some(Map { definition, grey_axis: version >= 2, floor: if version >= 3 { 1e-7 } else { 0.0 } })
}

/// Whether `name` at `version` is a registered map: `fire` or `ice`, versions 1 to 3.
pub fn is_registered(name: &str, version: u32) -> bool {
    map(name, version).is_some()
}

const SRGB_TO_XYZ: [[f64; 3]; 3] = [
    [0.4123907992659595, 0.357584339383878, 0.1804807884018343],
    [0.21263900587151036, 0.715168678767756, 0.07219231536073371],
    [0.01933081871559185, 0.11919477979462598, 0.9505321522496606],
];
// The inverses are the reference's, as NumPy computes them, to the last bit.
const XYZ_TO_SRGB: [[f64; 3]; 3] = [
    [3.240969941904521, -1.537383177570093, -0.4986107602930033],
    [-0.9692436362808796, 1.8759675015077202, 0.041555057407175626],
    [0.05563007969699358, -0.2039769588889765, 1.0569715142428786],
];
const B: f64 = 1.15;
const G: f64 = 0.66;
const C1: f64 = 3424.0 / 4096.0;
const C2: f64 = 2413.0 / 128.0;
const C3: f64 = 2392.0 / 128.0;
const N: f64 = 2610.0 / 16384.0;
const P: f64 = 1.7 * 2523.0 / 32.0;
const D: f64 = -0.56;
const D0: f64 = 1.6295499532821566e-11;
const XYZ_TO_LMS: [[f64; 3]; 3] =
    [[0.41478972, 0.579999, 0.0146480], [-0.2015100, 1.120649, 0.0531008], [-0.0166008, 0.264800, 0.6684799]];
const LMS_TO_IAB: [[f64; 3]; 3] = [[0.5, 0.5, 0.0], [3.524000, -4.066708, 0.542708], [0.199076, 1.096799, -1.295875]];
const LMS_TO_XYZ: [[f64; 3]; 3] = [
    [1.9242264357876069, -1.0047923125953657, 0.037651404030617994],
    [0.35031676209499907, 0.7264811939316552, -0.06538442294808501],
    [-0.09098281098284754, -0.3127282905230739, 1.5227665613052603],
];
const IAB_TO_LMS: [[f64; 3]; 3] = [
    [1.0, 0.1386050432715393, 0.05804731615611882],
    [1.0, -0.13860504327153927, -0.05804731615611891],
    [1.0, -0.09601924202631895, -0.811891896056039],
];

/// The larger and the smaller of two numbers as NumPy's `maximum` and `minimum` give them: a NaN stays NaN, where
/// `f64::max` and `min` would drop it, so that arithmetic that fails shows in the table instead of passing as 0.
fn maximum(a: f64, b: f64) -> f64 {
    if a.is_nan() { a } else { a.max(b) }
}

fn minimum(a: f64, b: f64) -> f64 {
    if a.is_nan() { a } else { a.min(b) }
}

/// CIE XYZ (D65) in cd/m^2 -> Jzazbz. A cone response below zero is taken as zero.
fn xyz_to_jzazbz([x, y, z]: [f64; 3]) -> [f64; 3] {
    let lms = matrix3(&XYZ_TO_LMS, [B * x - (B - 1.0) * z, G * y - (G - 1.0) * x, z]);
    let pq = lms.map(|v| {
        let q = (maximum(v, 0.0) / 10000.0).powf(N);
        ((C1 + C2 * q) / (1.0 + C3 * q)).powf(P)
    });
    let [iz, a, b] = matrix3(&LMS_TO_IAB, pq);
    [(1.0 + D) * iz / (1.0 + D * iz) - D0, a, b]
}

/// Jzazbz -> CIE XYZ in cd/m^2: the inverse of [`xyz_to_jzazbz`].
fn jzazbz_to_xyz([jz, a, b]: [f64; 3]) -> [f64; 3] {
    let jz = jz + D0;
    let iz = jz / (1.0 + D - D * jz);
    let r = matrix3(&IAB_TO_LMS, [iz, a, b]).map(|v| maximum(v, 0.0).powf(1.0 / P));
    let lms = r.map(|v| 10000.0 * (maximum(v - C1, 0.0) / (C2 - C3 * v)).powf(1.0 / N));
    let [xp, yp, z] = matrix3(&LMS_TO_XYZ, lms);
    let x = (xp + (B - 1.0) * z) / B;
    [x, (yp + (G - 1.0) * x) / G, z]
}

/// The Jzazbz of the grey of luminance `lum`, in multiples of SDR white.
fn grey(lum: f64) -> [f64; 3] {
    xyz_to_jzazbz(matrix3(&SRGB_TO_XYZ, [1.0; 3]).map(|w| lum * w * WHITE_NITS))
}

fn to_rgb(jab: [f64; 3]) -> [f64; 3] {
    matrix3(&XYZ_TO_SRGB, jzazbz_to_xyz(jab)).map(|v| v / WHITE_NITS)
}

/// Linear interpolation as NumPy's `interp` does it: the end value outside, the node's own value on a node.
fn interp(x: f64, xp: &[f64; 4], fp: &[f64; 4]) -> f64 {
    if x >= xp[3] {
        return fp[3];
    }
    if !(x >= xp[0]) {
        return fp[0];
    }
    let j = (0..3).rev().find(|&j| xp[j] <= x).unwrap_or(0);
    if xp[j] == x {
        return fp[j];
    }
    (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j]) * (x - xp[j]) + fp[j]
}

/// The colour of light `y` on a map at a reference headroom: linear RGB (BT.709 primaries, multiples of white).
fn colour(y: f64, m: &Map, headroom: f64) -> [f32; 3] {
    if y.is_nan() {
        return [f32::NAN; 3];
    }
    let d = m.definition;
    let above = (y - 1.0) * (headroom - d.top) / maximum(headroom - 1.0, 1e-12);
    let lum = if y <= 1.0 { y * d.top } else { d.top + above };
    let start = grey(lum);
    let jz = start[0];
    let (jz_top, jz_max) = (grey(d.top)[0], grey(headroom)[0]);
    let s = minimum(jz / jz_top, 1.0);
    let h = interp(s, &d.s, &d.hue).to_radians();
    let taper = ((jz - jz_top) / maximum(jz_max - jz_top, 1e-12)).clamp(0.0, 1.0);
    let c = interp(s, &d.s, &d.chroma) * (1.0 - taper);
    let upper = if y <= 1.0 { 1.0 } else { headroom };
    // version 1 measured chroma from az = bz = 0 and let a colour stand a millionth outside the gamut; versions 2
    // and 3 measure it from the grey of the same lightness and keep it inside
    let (origin, slack) = if m.grey_axis { (start, 0.0) } else { ([jz, 0.0, 0.0], 1e-6) };
    let at = |scale: f64| {
        let chroma = scale * c;
        to_rgb([origin[0], origin[1] + chroma * h.cos(), origin[2] + chroma * h.sin()])
    };
    let (mut lo, mut hi) = (0.0, 1.0);
    for _ in 0..BISECTION_STEPS {
        let mid = 0.5 * (lo + hi);
        if at(mid).iter().all(|&v| v >= -slack && v <= upper + slack) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    at(lo).map(|v| {
        let v = v.clamp(0.0, upper);
        (if v < m.floor { 0.0 } else { v }) as f32
    })
}

/// `nodes` values evenly spaced from `start` to `stop`, the i-th as NumPy's `linspace` gives it.
fn spaced(start: f64, stop: f64, nodes: usize, i: usize) -> f64 {
    if i == nodes - 1 { stop } else { i as f64 * ((stop - start) / (nodes - 1) as f64) + start }
}

/// A registered map's table, made by the generator at any reference headroom: `2 * nodes * 3` numbers, light
/// `0..=1` then `1..=headroom`, as [`Table::new`](crate::Table::new) takes them. At headroom 1 there is nothing
/// above white, and the second segment repeats the top of the first. Refused: a name or version that is not
/// registered, or fewer than two nodes or more than [`MAX_NODES`] ([`Error::BadTable`]); a headroom below 1
/// ([`Error::BadHeadroom`]); and a headroom infinite, or above the largest `f32` (about 3.4e38), whose colours
/// cannot be stored ([`Error::NotFinite`]; the reference's own table holds infinity or NaN there).
///
/// Profile 6.10: a registered map at a registered headroom (2, 4, 8, 16) is taken from its published table, never
/// made afresh; a table made here is for any other headroom, and the file must carry it (`udeep:ColourMapTable`).
pub fn generate(name: &str, version: u32, headroom: f64, nodes: usize) -> Result<Vec<f32>, Error> {
    let m = map(name, version).ok_or(Error::BadTable)?;
    if headroom.is_infinite() {
        return Err(Error::NotFinite);
    }
    if !(headroom >= 1.0) {
        return Err(Error::BadHeadroom);
    }
    if !(2..=MAX_NODES).contains(&nodes) {
        return Err(Error::BadTable);
    }
    let mut out = Vec::with_capacity(6 * nodes);
    for i in 0..nodes {
        out.extend(colour(spaced(0.0, 1.0, nodes, i), &m, headroom));
    }
    if headroom > 1.0 {
        for i in 0..nodes {
            out.extend(colour(spaced(1.0, headroom, nodes, i), &m, headroom));
        }
    } else {
        let top: [f32; 3] = out[3 * (nodes - 1)..].try_into().expect("three channels");
        (0..nodes).for_each(|_| out.extend(top));
    }
    if out.iter().any(|v| !v.is_finite()) {
        return Err(Error::NotFinite);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jzazbz_round_trips() {
        for xyz in [[20.0, 21.0, 22.0], [150.0, 203.0, 80.0], [1.0, 0.5, 2.0]] {
            let back = jzazbz_to_xyz(xyz_to_jzazbz(xyz));
            for k in 0..3 {
                assert!((back[k] - xyz[k]).abs() < 1e-9 * xyz[k].abs().max(1.0), "{xyz:?} -> {back:?}");
            }
        }
    }

    #[test]
    fn the_top_of_a_map_is_white_and_its_bottom_black() {
        for (name, h) in [("fire", 3.0), ("ice", 6.0)] {
            let t = generate(name, 3, h, EMBED_NODES).unwrap();
            assert_eq!(&t[..3], &[0.0; 3], "{name}");
            let top = &t[t.len() - 3..];
            assert!(top.iter().all(|&v| (f64::from(v) - h).abs() < 1e-6), "{name} at {h}: {top:?}");
        }
    }

    #[test]
    fn what_is_not_a_registered_map_is_refused() {
        assert_eq!(generate("viridis", 3, 4.0, 257), Err(Error::BadTable));
        assert_eq!(generate("fire", 4, 4.0, 257), Err(Error::BadTable));
        assert_eq!(generate("fire", 3, 0.5, 257), Err(Error::BadHeadroom));
        assert_eq!(generate("fire", 3, f64::NAN, 257), Err(Error::BadHeadroom));
        assert_eq!(generate("fire", 3, f64::INFINITY, 257), Err(Error::NotFinite));
        assert_eq!(generate("fire", 3, 4.0, 1), Err(Error::BadTable));
        assert_eq!(generate("fire", 3, 4.0, MAX_NODES + 1), Err(Error::BadTable));
    }
}
