//! What a file records beyond the mapping: digests that tie it to its data and to its SDR figure, the stored
//! levels of an 8-bit rendering, and the data criterion (profile 7.2.1).

use crate::{Mapping, Table, colours, sha256::Sha256, srgb_oetf};

/// A digest of an array's values and shape, whatever type it was stored as: `"sha256:"` and 64 hex digits.
///
/// The values are taken as `f64`, little-endian, in row order, after the text `udeep-data-1;<shape>;` where the
/// shape is its sizes joined by `x`. Every NaN counts as the same value and `-0` as `0`.
///
/// A single value with no dimensions (an empty `shape`) is digested as a list of one, shape `1`: the reference
/// does so, and the two must give one digest for one scalar.
pub fn digest_data(shape: &[usize], values: &[f64]) -> String {
    let mut h = Sha256::new();
    let shape = if shape.is_empty() { &[1][..] } else { shape };
    let sizes: Vec<String> = shape.iter().map(usize::to_string).collect();
    h.update(format!("udeep-data-1;{};", sizes.join("x")).as_bytes());
    for &v in values {
        let canonical = if v.is_nan() { f64::NAN } else { v + 0.0 };
        h.update(&canonical.to_le_bytes());
    }
    format!("sha256:{}", h.hex())
}

/// A digest of an 8-bit RGB image's pixels (`height` x `width` x 3, row order), independent of how a file
/// stores them, after the text `udeep-rgb8-1;<height>x<width>;`.
pub fn digest_pixels(height: usize, width: usize, rgb: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(format!("udeep-rgb8-1;{height}x{width};").as_bytes());
    h.update(rgb);
    format!("sha256:{}", h.hex())
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
