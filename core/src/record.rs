//! What a file records beyond the mapping: digests that tie it to its data and to its SDR figure, the stored
//! levels of an 8-bit rendering, and the data criterion (profile 7.2.1).

use crate::{Mapping, Table, colours, sha3::Sha3_256, sha256::Sha256, srgb_oetf};

/// A hash function the digests are taken with, and the name a digest's text begins with (profile 8.6).
trait Hasher: Default {
    const NAME: &'static str;
    fn add(&mut self, data: &[u8]);
    fn finish(self) -> String;
}

impl Hasher for Sha256 {
    const NAME: &'static str = "sha256";
    fn add(&mut self, data: &[u8]) {
        self.update(data);
    }
    fn finish(self) -> String {
        self.hex()
    }
}

impl Hasher for Sha3_256 {
    const NAME: &'static str = "sha3-256";
    fn add(&mut self, data: &[u8]) {
        self.update(data);
    }
    fn finish(self) -> String {
        self.hex()
    }
}

fn data_digest<H: Hasher>(shape: &[usize], values: &[f64]) -> String {
    let mut h = H::default();
    let shape = if shape.is_empty() { &[1][..] } else { shape };
    let sizes: Vec<String> = shape.iter().map(usize::to_string).collect();
    h.add(format!("udeep-data-1;{};", sizes.join("x")).as_bytes());
    for &v in values {
        let canonical = if v.is_nan() { f64::NAN } else { v + 0.0 };
        h.add(&canonical.to_le_bytes());
    }
    format!("{}:{}", H::NAME, h.finish())
}

fn pixels_digest<H: Hasher>(height: usize, width: usize, rgb: &[u8]) -> String {
    let mut h = H::default();
    h.add(format!("udeep-rgb8-1;{height}x{width};").as_bytes());
    h.add(rgb);
    format!("{}:{}", H::NAME, h.finish())
}

/// A digest of an array's values and shape, whatever type it was stored as: `"sha256:"` and 64 hex digits.
///
/// The values are taken as `f64`, little-endian, in row order, after the text `udeep-data-1;<shape>;` where the
/// shape is its sizes joined by `x`. Every NaN counts as the same value and `-0` as `0`.
///
/// A single value with no dimensions (an empty `shape`) is digested as a list of one, shape `1`: the reference
/// does so, and the two must give one digest for one scalar.
pub fn digest_data(shape: &[usize], values: &[f64]) -> String {
    data_digest::<Sha256>(shape, values)
}

/// The same bytes as [`digest_data`] digested with SHA3-256 (`udeep:DataDigestSha3`): `"sha3-256:"` and 64 hex
/// digits. A second hash function of another design, so that the link to the data does not rest on one.
pub fn digest_data_sha3(shape: &[usize], values: &[f64]) -> String {
    data_digest::<Sha3_256>(shape, values)
}

/// A digest of an 8-bit RGB image's pixels (`height` x `width` x 3, row order), independent of how a file
/// stores them, after the text `udeep-rgb8-1;<height>x<width>;`.
pub fn digest_pixels(height: usize, width: usize, rgb: &[u8]) -> String {
    pixels_digest::<Sha256>(height, width, rgb)
}

/// The same bytes as [`digest_pixels`] digested with SHA3-256 (`udeep:SdrDigestSha3`).
pub fn digest_pixels_sha3(height: usize, width: usize, rgb: &[u8]) -> String {
    pixels_digest::<Sha3_256>(height, width, rgb)
}

/// The stored levels of a rendering across the declared range: where each run of data values sharing one stored
/// code starts, and how wide it is, both in data units.
#[derive(Clone, Debug, PartialEq)]
pub struct Levels {
    pub start: Vec<f64>,
    pub width: Vec<f64>,
}

impl Levels {
    /// The widest level: `udeep:MaxDataStep`.
    pub fn max_data_step(&self) -> f64 {
        self.width.iter().copied().fold(0.0, f64::max)
    }
}

fn code(rgb: [f64; 3], divide_by: f64) -> [u8; 3] {
    rgb.map(|c| (srgb_oetf((c / divide_by).clamp(0.0, 1.0)) * 255.0).round_ties_even() as u8)
}

/// The levels of the 8-bit path the reference writer takes for a gain-map JPEG: below the knee the code is the
/// 8-bit sRGB colour of the SDR figure; above it, that of the HDR figure divided by `scaled_by` (the headroom the
/// file is written for). JPEG compression adds error on top and is not included. The range is sampled at
/// `samples` evenly spaced positions; fewer than two cannot span the range, and are taken as two.
pub fn levels(mapping: &Mapping, table: Option<&Table>, scaled_by: f64, samples: usize) -> Levels {
    let samples = samples.max(2);
    let knee = mapping.knee_position();
    let step = 1.0 / (samples - 1) as f64;
    let position = |i: usize| if i == samples - 1 { 1.0 } else { i as f64 * step };
    let stored = |i: usize| {
        let t = position(i);
        let light = crate::tone(t, knee, mapping.reference_headroom, mapping.exit_slope);
        if light > 1.0 {
            (code(colours(light, table), scaled_by), true)
        } else {
            (code(colours(crate::tone(t, knee, 1.0, 1.0), table), 1.0), false)
        }
    };
    let mut edges = vec![0.0];
    let mut previous = stored(0);
    for i in 1..samples {
        let now = stored(i);
        if now != previous {
            edges.push(position(i));
            previous = now;
        }
    }
    edges.push(1.0);
    let x: Vec<f64> = edges.into_iter().map(|t| mapping.value_at(t)).collect();
    Levels { start: x[..x.len() - 1].to_vec(), width: x.windows(2).map(|w| w[1] - w[0]).collect() }
}

/// The levels of a 16-bit PQ file ([`crate::pq16_codes`]) with white at `white_nits`: one colour for each value,
/// the whole range through the one curve, with no split at the knee. There are tens of thousands of them, so the
/// range wants about a million samples (the reference uses 2^20).
pub fn levels_pq16(mapping: &Mapping, table: Option<&Table>, white_nits: f64, samples: usize) -> Levels {
    let samples = samples.max(2);
    let knee = mapping.knee_position();
    let step = 1.0 / (samples - 1) as f64;
    let position = |i: usize| if i == samples - 1 { 1.0 } else { i as f64 * step };
    let stored = |i: usize| {
        let light = crate::tone(position(i), knee, mapping.reference_headroom, mapping.exit_slope);
        crate::pq16_codes(colours(light, table), white_nits)
    };
    let mut edges = vec![0.0];
    let mut previous = stored(0);
    for i in 1..samples {
        let now = stored(i);
        if now != previous {
            edges.push(position(i));
            previous = now;
        }
    }
    edges.push(1.0);
    let x: Vec<f64> = edges.into_iter().map(|t| mapping.value_at(t)).collect();
    Levels { start: x[..x.len() - 1].to_vec(), width: x.windows(2).map(|w| w[1] - w[0]).collect() }
}

/// Halvings that find where a 16-bit code changes, between two samples: to a millionth of their spacing.
const EDGE_STEPS: u32 = 20;
/// How often [`values_pq16`] may halve a gap between samples that skips a code.
pub const REFINE_STEPS: u32 = 64;
/// The most colours [`values_pq16`] looks for between two neighbouring points.
const BETWEEN: usize = 6;

/// Whether two colours' codes are more than one step apart in some channel, so that codes lie between them.
fn skips(a: [u16; 3], b: [u16; 3]) -> bool {
    (0..3).any(|k| a[k].abs_diff(b[k]) > 1)
}

/// A point of the sampled range, and the codes stored there.
type Point = (f64, [u16; 3]);

/// The pieces of the gap from `lo` to `hi`, in order: halved, and halved again, while a piece skips a code, at most
/// [`REFINE_STEPS`] times and never below what `f64` can halve. Each piece goes to `piece` with the codes at its ends.
fn pieces(
    (lo, low): Point,
    (hi, high): Point,
    depth: u32,
    code_at: &dyn Fn(f64) -> [u16; 3],
    piece: &mut dyn FnMut(Point, Point),
) {
    let mid = 0.5 * (lo + hi);
    if depth < REFINE_STEPS && skips(low, high) && mid > lo && mid < hi {
        let between = code_at(mid);
        pieces((lo, low), (mid, between), depth + 1, code_at, piece);
        pieces((mid, between), (hi, high), depth + 1, code_at, piece);
    } else {
        piece((lo, low), (hi, high));
    }
}

/// The data values that the codes of a 16-bit PQ file stand for, under its mapping and colour map: the declared
/// mapping turned back through the file's own codes, as the reference's `udeep.metadata.values_pq16` does it.
/// `codes` holds a pixel's three codes in turn, as [`crate::png::read_png_pq_codes`] gives them; the result has one
/// value for each pixel.
///
/// Each stored level is a run of data values that share one code in all three channels ([`levels_pq16`]). A pixel
/// is given the middle of the run whose code it holds, so it comes back to within half that run's width, which is
/// at most half of `udeep:MaxDataStep`. Near black PQ codes change faster than any even sampling can follow: there,
/// where two neighbouring samples are more than one code apart in some channel, the gap between them is halved, and
/// halved again, until no piece of it skips a code (at most [`REFINE_STEPS`] times). Where two neighbouring points
/// differ, the first change of code between them is found to within a millionth of their spacing; if the code just
/// past it is not yet the second point's, the next change is found the same way, and so on, so that a colour held
/// between two channels' changes is a level of its own. A writer that rounds a sum of three products in another order can leave a rare code one step from this
/// one's: a pixel whose code is in no run takes the nearest run that is a step away in each channel at most (fewest
/// channels moved, the first in order of -1, 0, +1 on each channel), and a pixel with none so near is not a colour
/// of this mapping and is NaN.
///
/// [`Error::BadDeclaration`] when two separate runs share one code, since that code would not say which value it
/// stands for (the reference refuses it too; no registered map has done it), and [`Error::BadImage`] when `codes`
/// is not whole pixels.
pub fn values_pq16(
    mapping: &Mapping,
    table: Option<&Table>,
    white_nits: f64,
    samples: usize,
    codes: &[u16],
) -> Result<Vec<f64>, crate::Error> {
    if !codes.len().is_multiple_of(3) {
        return Err(crate::Error::BadImage);
    }
    let samples = samples.max(2);
    let knee = mapping.knee_position();
    let code_at = |t: f64| {
        let light = crate::tone(t, knee, mapping.reference_headroom, mapping.exit_slope);
        crate::pq16_codes(colours(light, table), white_nits)
    };
    let step = 1.0 / (samples - 1) as f64;
    let position = |i: usize| if i == samples - 1 { 1.0 } else { i as f64 * step };
    let first = code_at(0.0);
    let (mut runs, mut ends) = (vec![first], vec![0.0]);
    // a run ends between two neighbouring points whose codes differ: found by halving the space between them
    // each change of code between two neighbouring points, in turn, as far as the second point's colour
    let mut piece = |(mut lo, mut current): Point, (top, high): Point| {
        if current == high {
            return;
        }
        for _ in 0..BETWEEN {
            let mut hi = top;
            for _ in 0..EDGE_STEPS {
                let mid = 0.5 * (lo + hi);
                if code_at(mid) == current {
                    lo = mid;
                } else {
                    hi = mid;
                }
            }
            let past = code_at(hi); // the colour just past the change
            ends.push(hi);
            runs.push(past);
            if past == high {
                break;
            }
            (lo, current) = (hi, past);
        }
    };
    let mut before = (0.0, first);
    for i in 1..samples {
        let t = position(i);
        let now = (t, code_at(t));
        pieces(before, now, 0, &code_at, &mut piece);
        before = now;
    }
    ends.push(1.0);
    let x: Vec<f64> = ends.into_iter().map(|t| mapping.value_at(t)).collect();
    let key = |c: [u16; 3]| u64::from(c[0]) << 32 | u64::from(c[1]) << 16 | u64::from(c[2]);
    let mut run_of = std::collections::HashMap::with_capacity(runs.len());
    for (i, &c) in runs.iter().enumerate() {
        if run_of.insert(key(c), i).is_some() {
            return Err(crate::Error::BadDeclaration);
        }
    }
    let middle = |i: usize| 0.5 * (x[i] + x[i + 1]);
    let steps: Vec<[i32; 3]> =
        (0..27).map(|n| [n / 9 - 1, n / 3 % 3 - 1, n % 3 - 1]).filter(|s| *s != [0, 0, 0]).collect();
    Ok(codes
        .as_chunks::<3>()
        .0
        .iter()
        .map(|&pixel| {
            if let Some(&i) = run_of.get(&key(pixel)) {
                return middle(i);
            }
            let mut best: Option<(i32, usize)> = None; // channels moved, and the run
            for s in &steps {
                let moved = s.iter().map(|d| d.abs()).sum::<i32>();
                let near = [0, 1, 2].map(|k| (i32::from(pixel[k]) + s[k]).clamp(0, 65535) as u16);
                if let Some(&i) = run_of.get(&key(near))
                    && best.is_none_or(|(fewest, _)| moved < fewest)
                {
                    best = Some((moved, i));
                }
            }
            best.map_or(f64::NAN, |(_, i)| middle(i))
        })
        .collect())
}

/// The data's stated uncertainty (profile 5.2).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Noise {
    /// The same standard deviation everywhere.
    Constant(f64),
    /// Counting statistics: sigma = sqrt(value x scale).
    Poisson(f64),
    /// Sigma proportional to the value.
    Relative(f64),
    /// No noise; a declared absolute tolerance.
    Tolerance(f64),
}

/// The data criterion applied to a set of levels.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Criterion {
    /// The worst ratio of rounding error to uncertainty over the declared range. It is infinite where the
    /// uncertainty reaches zero; a file states it as `udeep:RoundingToNoise`, no larger than [`UNBOUNDED`].
    pub worst: f64,
    /// The data value where the uncertainty was taken for it (in the first level, if several share the worst).
    pub at: f64,
    /// The ratio a data record must not exceed: 0.1 for a noise model, 1 for a tolerance.
    pub limit: f64,
    /// Whether no level exceeds the limit.
    pub meets: bool,
    /// The fraction of the declared range that exceeds the limit.
    pub failing_fraction: f64,
}

/// What a file states for a ratio that has no bound: the metadata cannot carry an infinity.
pub const UNBOUNDED: f64 = 1e9;

/// For a noise model, each level's rounding error (its width / sqrt(12)) over the data's standard deviation at
/// that level, against a limit of 0.1. For a tolerance, the largest rounding error (half the width) over the
/// tolerance, against a limit of 1. A level where the uncertainty is zero counts as infinitely over.
///
/// The noise is taken at the point of each level where it is smallest, so a level meets the criterion only if all
/// of it does. Poisson and relative noise grow away from zero, so that point is the one nearest zero: the end of
/// the level nearer zero, or zero itself when the level contains it. A range that reaches zero therefore cannot
/// meet the criterion under those two. Constant noise and a tolerance are the same everywhere, and are reported
/// at the middle of the level.
pub fn rounding_to_noise(levels: &Levels, noise: Noise) -> Criterion {
    let (limit, tolerance) = if matches!(noise, Noise::Tolerance(_)) { (1.0, true) } else { (0.1, false) };
    let (mut worst, mut at, mut failing, mut total) = (f64::NEG_INFINITY, f64::NAN, 0.0, 0.0);
    let mut meets = true;
    for (&start, &width) in levels.start.iter().zip(&levels.width) {
        let middle = start + 0.5 * width;
        let nearest_zero = start.max(0.0).min(start + width);
        let error = if tolerance { 0.5 * width } else { width / 12f64.sqrt() };
        let (sigma, taken_at) = match noise {
            Noise::Constant(s) | Noise::Tolerance(s) => (s, middle),
            Noise::Poisson(scale) => ((nearest_zero.max(0.0) * scale).sqrt(), nearest_zero),
            Noise::Relative(fraction) => (fraction * nearest_zero.abs(), nearest_zero),
        };
        let ratio = if sigma > 0.0 { error / sigma } else { f64::INFINITY };
        if ratio > worst {
            (worst, at) = (ratio, taken_at); // strictly greater: the first of several equal levels is kept
        }
        if ratio > limit {
            failing += width;
            meets = false;
        }
        total += width;
    }
    Criterion { worst, at, limit, meets, failing_fraction: failing / total }
}
