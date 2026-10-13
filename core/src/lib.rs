//! The mapping from scientific data to light and colour, as the UDeep Figure Profile defines it.
//!
//! ```text
//! value x  ->  position t in [0, 1]  ->  light y, in multiples of SDR white  ->  linear RGB, same unit
//! ```
//!
//! This crate is a port of the Python reference (`udeep`), checked against the same golden vectors
//! (`src/udeep/golden.json`, run by `tests/golden.rs`). It follows the project's design decisions:
//!
//! * every stage is a pure function of one value: nothing looks at a neighbour, so any slice of an image can be
//!   mapped alone, in any order, on any number of threads (D1, D2, D4);
//! * one record, [`Mapping`], holds the parameters a file declares, under the names the metadata uses (D3);
//! * arithmetic is `f64`, as in the reference (D8);
//! * a colour map is a table, [`Table`], interpolated linearly (D9);
//! * the library draws nothing and encodes no image formats (D5, D6), and depends on no other crate: its
//!   SHA-256 ([`sha256`]) and its threads ([`par`]) are the standard library's or its own.
//!
//! The C interface is in [`ffi`]; its header is `include/udeep_core.h`.

// Range checks are written as `!(a > b)` on purpose: a NaN must fail them, and `a <= b` would let it through.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

pub mod declaration;
pub mod ffi;
#[cfg(feature = "jpeg")]
pub mod gainmap;
pub mod par;
#[cfg(feature = "png")]
pub mod png;
pub mod record;
pub mod sha256;

pub use declaration::{Declaration, caption, description, float_text, format_g};
pub use record::{
    Criterion, Levels, Noise, UNBOUNDED, digest_data, digest_pixels, levels, levels_pq16, rounding_to_noise,
};

/// How close to either end of the range the curve can place a knee. A record whose knee is nearer the bottom than
/// this, or nearer the top without being exactly at it, is refused ([`Mapping::validate`]).
pub const KNEE_MIN: f64 = 1e-6;
/// A knee placed exactly at the limit may land this far inside it once its position is worked out.
pub const KNEE_ROUNDING: f64 = 1e-12;
/// How closely a proportional mapping's `range_high` must equal `knee * reference_headroom` (relative).
pub const PROPORTIONAL_RTOL: f64 = 1e-9;
/// Bisection steps used to invert the mapping. The curve is strictly increasing, so 60 halvings reach `f64`.
pub const INVERSE_STEPS: u32 = 60;

/// Why a mapping, a table or a call was refused.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(i32)]
pub enum Error {
    /// A required pointer was null (C interface only).
    NullPointer = 1,
    /// The stretch code is not one of the four defined.
    UnknownStretch = 2,
    /// `range_high` is not above `range_low`, or a log stretch starts at or below zero.
    BadRange = 3,
    /// The knee is not inside the declared range, or is within [`KNEE_MIN`] of its bottom, or of its top without
    /// being exactly at the top.
    KneeOutsideRange = 4,
    /// The reference headroom is below 1, or the exit slope is outside 0..=3.
    BadHeadroom = 5,
    /// The asinh width is not positive, or a proportional mapping has an offset, a non-zero start, a top that is
    /// not `knee * reference_headroom`, or an exit slope other than 1.
    BadStretchParameter = 6,
    /// A table does not hold two segments of `nodes` x 3 numbers, or has fewer than two nodes.
    BadTable = 7,
    /// An image's size or values cannot be written: sizes that do not match, SDR outside 0..=1, an infinite HDR
    /// value, headroom not above 1.
    BadImage = 8,
    /// The image codec failed, a metadata segment was too large for the file format, or the gain map could not be
    /// kept within the headroom at the gain quality asked for.
    Codec = 9,
    /// A number in the mapping record is NaN or infinite.
    NotFinite = 10,
    /// A declaration the profile would refuse: a required text missing or not UTF-8, an unknown noise model, a
    /// number that means nothing.
    BadDeclaration = 11,
}

/// How data values are spread over the declared range.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u32)]
pub enum Stretch {
    Linear = 0,
    Log = 1,
    Asinh = 2,
    /// Linear from zero with no offset: light is proportional to the value (data that is itself light).
    Proportional = 3,
}

impl Stretch {
    pub fn from_code(code: u32) -> Result<Self, Error> {
        match code {
            0 => Ok(Self::Linear),
            1 => Ok(Self::Log),
            2 => Ok(Self::Asinh),
            3 => Ok(Self::Proportional),
            _ => Err(Error::UnknownStretch),
        }
    }

    /// The names the metadata uses (`udeep:Stretch`).
    pub fn from_name(name: &str) -> Result<Self, Error> {
        match name {
            "linear" => Ok(Self::Linear),
            "log" => Ok(Self::Log),
            "asinh" => Ok(Self::Asinh),
            "proportional" => Ok(Self::Proportional),
            _ => Err(Error::UnknownStretch),
        }
    }
}

fn edge(v: f64, stretch: Stretch, width: f64) -> f64 {
    match stretch {
        Stretch::Linear | Stretch::Proportional => v,
        Stretch::Log => v.ln(),
        Stretch::Asinh => (v / width).asinh(),
    }
}

/// Data value -> position in `[0, 1]`: `lo` -> 0, `hi` -> 1, clipped. NaN stays NaN; for a log stretch a value at
/// or below zero is position 0.
pub fn scale(x: f64, stretch: Stretch, lo: f64, hi: f64, width: f64) -> f64 {
    if x.is_nan() {
        return f64::NAN;
    }
    let f = match stretch {
        Stretch::Linear | Stretch::Proportional => x,
        Stretch::Log => {
            if x > 0.0 {
                x.ln()
            } else {
                f64::NEG_INFINITY
            }
        }
        Stretch::Asinh => (x / width).asinh(),
    };
    let (f_lo, f_hi) = (edge(lo, stretch, width), edge(hi, stretch, width));
    ((f - f_lo) / (f_hi - f_lo)).clamp(0.0, 1.0)
}

/// Position -> data value: the inverse of [`scale`] on `[0, 1]`.
pub fn unscale(t: f64, stretch: Stretch, lo: f64, hi: f64, width: f64) -> f64 {
    let (f_lo, f_hi) = (edge(lo, stretch, width), edge(hi, stretch, width));
    let f = f_lo + t * (f_hi - f_lo);
    match stretch {
        Stretch::Linear | Stretch::Proportional => f,
        Stretch::Log => f.exp(),
        Stretch::Asinh => width * f.sinh(),
    }
}

/// Position -> light, in multiples of SDR white.
///
/// Below the knee, `t / knee` (0 to 1). Above it, a cubic Hermite segment from 1 to `headroom` that starts with
/// the slope of the part below (capped at three times its own average slope, which keeps it increasing) and ends
/// with `exit_slope` times that average slope. `exit_slope = 1` is the declared, invertible form of the profile;
/// `0` ends flat, the BT.2390-style roll-off, for display rendering only. The caller checks the parameters
/// ([`Mapping::validate`]).
pub fn tone(t: f64, knee: f64, headroom: f64, exit_slope: f64) -> f64 {
    let below = t / knee;
    if knee >= 1.0 {
        return below.clamp(0.0, headroom);
    }
    let width = (1.0 - knee).max(KNEE_MIN);
    let secant = (headroom - 1.0) / width;
    let entry = (1.0 / knee).min(3.0 * secant);
    let u = ((t - knee) / width).clamp(0.0, 1.0);
    let (u2, u3) = (u * u, u * u * u);
    let shoulder = (2.0 * u3 - 3.0 * u2 + 1.0)
        + (u3 - 2.0 * u2 + u) * width * entry
        + (-2.0 * u3 + 3.0 * u2) * headroom
        + (u3 - u2) * width * exit_slope * secant;
    let y = if t <= knee { below } else { shoulder };
    y.clamp(0.0, headroom)
}

/// The mapping a file declares, as one record. Field names follow the `udeep:` metadata properties.
///
/// The layout is fixed (`repr(C)`): seven `f64`, then two `u32`. It is the C interface's `udeep_mapping`.
#[derive(Clone, Copy, Debug, PartialEq)]
#[repr(C)]
pub struct Mapping {
    /// The declared range, in data units, after `stretch_offset` is subtracted.
    pub range_low: f64,
    pub range_high: f64,
    /// The data value shown at SDR white.
    pub knee: f64,
    /// Light at the top of the range, in multiples of SDR white.
    pub reference_headroom: f64,
    /// Asinh: the width of the linear part. Ignored by the other stretches, but must be positive.
    pub stretch_width: f64,
    /// Subtracted from the data first.
    pub stretch_offset: f64,
    /// The form above the knee: the exit slope of the Hermite segment, in units of its average slope.
    /// `1` is the profile's reference form (`udeep:AboveKnee = "hermite; exit=1"`).
    pub exit_slope: f64,
    /// A [`Stretch`] code.
    pub stretch: u32,
    /// Keeps the record a multiple of eight bytes; must be zero.
    pub reserved: u32,
}

impl Mapping {
    pub fn new(stretch: Stretch, range_low: f64, range_high: f64, knee: f64, reference_headroom: f64) -> Self {
        Self {
            range_low,
            range_high,
            knee,
            reference_headroom,
            stretch_width: 1.0,
            stretch_offset: 0.0,
            exit_slope: 1.0,
            stretch: stretch as u32,
            reserved: 0,
        }
    }

    /// The checks the reference makes when a record is built, in its order. Every other method assumes they passed.
    pub fn validate(&self) -> Result<Stretch, Error> {
        let stretch = Stretch::from_code(self.stretch)?;
        // first, because the comparisons below let an infinity through
        let numbers = [
            self.range_low,
            self.range_high,
            self.knee,
            self.reference_headroom,
            self.stretch_width,
            self.stretch_offset,
        ];
        if !numbers.iter().all(|v| v.is_finite()) {
            return Err(Error::NotFinite);
        }
        if !(self.range_high > self.range_low) || (stretch == Stretch::Log && !(self.range_low > 0.0)) {
            return Err(Error::BadRange);
        }
        if !(self.stretch_width > 0.0)
            || (stretch == Stretch::Proportional && (self.range_low != 0.0 || self.stretch_offset != 0.0))
        {
            return Err(Error::BadStretchParameter);
        }
        let knee = self.knee - self.stretch_offset;
        if !(self.range_low < knee && knee <= self.range_high) {
            return Err(Error::KneeOutsideRange);
        }
        if !(self.reference_headroom >= 1.0) || !(0.0..=3.0).contains(&self.exit_slope) {
            return Err(Error::BadHeadroom);
        }
        // The curve cannot place a knee within KNEE_MIN of either end, and would draw another one without saying
        // so. A knee exactly at the top is the full-range SDR plot and is allowed.
        let t = scale(knee, stretch, self.range_low, self.range_high, self.stretch_width);
        if t < KNEE_MIN - KNEE_ROUNDING || (1.0 - KNEE_MIN + KNEE_ROUNDING < t && t < 1.0) {
            return Err(Error::KneeOutsideRange);
        }
        if stretch == Stretch::Proportional && !self.is_a_straight_line() {
            return Err(Error::BadStretchParameter);
        }
        Ok(stretch)
    }

    /// Profile 6.6: a proportional mapping is light = value / knee over the whole range. The part above the knee
    /// is that line only when it ends at knee x headroom and leaves with the slope of its secant.
    fn is_a_straight_line(&self) -> bool {
        let top = self.knee * self.reference_headroom;
        let close = (self.range_high - top).abs() <= PROPORTIONAL_RTOL * self.range_high.abs().max(top.abs());
        close && self.exit_slope == 1.0
    }

    fn kind(&self) -> Stretch {
        Stretch::from_code(self.stretch).unwrap_or(Stretch::Linear)
    }

    /// Where the knee sits in `[0, 1]`. A valid record is never moved by the clamp; it only keeps a record that
    /// was not checked from dividing by zero.
    pub fn knee_position(&self) -> f64 {
        scale(self.knee - self.stretch_offset, self.kind(), self.range_low, self.range_high, self.stretch_width)
            .clamp(KNEE_MIN, 1.0)
    }

    /// Data value -> position in `[0, 1]`.
    pub fn position(&self, x: f64) -> f64 {
        scale(x - self.stretch_offset, self.kind(), self.range_low, self.range_high, self.stretch_width)
    }

    /// Position -> light.
    pub fn light_at(&self, t: f64) -> f64 {
        tone(t, self.knee_position(), self.reference_headroom, self.exit_slope)
    }

    /// Data value -> light, in multiples of SDR white: the declared mapping.
    pub fn light(&self, x: f64) -> f64 {
        self.light_with(self.knee_position(), x)
    }

    /// Data value -> the fair SDR light: the same below the knee, SDR white above it.
    pub fn sdr_light(&self, x: f64) -> f64 {
        self.sdr_light_with(self.knee_position(), x)
    }

    /// Position -> data value.
    pub fn value_at(&self, t: f64) -> f64 {
        unscale(t, self.kind(), self.range_low, self.range_high, self.stretch_width) + self.stretch_offset
    }

    /// Light -> data value: the inverse of [`Mapping::light`]. Light outside `0..=headroom` is clipped to it.
    pub fn value(&self, y: f64) -> f64 {
        self.value_with(self.knee_position(), y)
    }

    /// As [`Mapping::light`], with `knee` = [`Mapping::knee_position`] worked out once by the caller. Loops over
    /// many values use these `_with` forms, so the knee is not recomputed for every value.
    pub fn light_with(&self, knee: f64, x: f64) -> f64 {
        tone(self.position(x), knee, self.reference_headroom, self.exit_slope)
    }

    /// As [`Mapping::sdr_light`], with the knee position supplied.
    pub fn sdr_light_with(&self, knee: f64, x: f64) -> f64 {
        tone(self.position(x), knee, 1.0, 1.0)
    }

    /// As [`Mapping::value`], with the knee position supplied.
    pub fn value_with(&self, knee: f64, y: f64) -> f64 {
        if y.is_nan() {
            return f64::NAN;
        }
        let target = y.clamp(0.0, self.reference_headroom);
        let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
        for _ in 0..INVERSE_STEPS {
            let mid = 0.5 * (lo + hi);
            if tone(mid, knee, self.reference_headroom, self.exit_slope) < target {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        self.value_at(0.5 * (lo + hi))
    }

    /// [`Mapping::light`] over a slice. Any slice of an image may be passed, in any order.
    pub fn light_into(&self, x: &[f64], out: &mut [f64]) {
        let knee = self.knee_position();
        for (o, &v) in out.iter_mut().zip(x) {
            *o = self.light_with(knee, v);
        }
    }
}

/// A colour map as data: two segments, light `0..=1` and `1..=headroom`, each of `nodes` evenly spaced nodes
/// holding linear RGB (BT.709 primaries, multiples of SDR white) as `f32`. Colours between nodes are linear
/// interpolations. `values` is the first segment row by row, then the second: `2 * nodes * 3` numbers.
#[derive(Clone, Copy, Debug)]
pub struct Table<'a> {
    values: &'a [f32],
    nodes: usize,
    headroom: f64,
}

impl<'a> Table<'a> {
    pub fn new(values: &'a [f32], headroom: f64) -> Result<Self, Error> {
        let nodes = values.len() / 6;
        if nodes < 2 || values.len() != nodes * 6 || !(headroom >= 1.0) {
            return Err(Error::BadTable);
        }
        Ok(Self { values, nodes, headroom })
    }

    pub fn nodes(&self) -> usize {
        self.nodes
    }

    /// Light -> linear RGB. Light outside `0..=headroom` takes the end colour; NaN gives NaN.
    pub fn lookup(&self, y: f64) -> [f64; 3] {
        if y.is_nan() {
            return [f64::NAN; 3];
        }
        let v = y.clamp(0.0, self.headroom.max(1.0));
        let high = v > 1.0;
        let along = if high { (v - 1.0) / (self.headroom - 1.0).max(1e-12) } else { v };
        let p = along * (self.nodes - 1) as f64;
        let i = (p.floor() as usize).min(self.nodes - 2);
        let f = p - i as f64;
        let at = 3 * (i + if high { self.nodes } else { 0 });
        let mut rgb = [0.0; 3];
        for (k, c) in rgb.iter_mut().enumerate() {
            *c = f64::from(self.values[at + k]) * (1.0 - f) + f64::from(self.values[at + 3 + k]) * f;
        }
        rgb
    }
}

/// Light -> linear RGB for a mapping: grey (`None`) repeats the light on three channels.
pub fn colours(y: f64, table: Option<&Table>) -> [f64; 3] {
    match table {
        Some(t) => t.lookup(y),
        None => [y; 3],
    }
}

/// Luminance in cd/m^2 -> PQ signal in `[0, 1]` (SMPTE ST 2084).
pub fn pq_encode(nits: f64) -> f64 {
    const M1: f64 = 2610.0 / 16384.0;
    const M2: f64 = 2523.0 / 4096.0 * 128.0;
    const C1: f64 = 3424.0 / 4096.0;
    const C2: f64 = 2413.0 / 4096.0 * 32.0;
    const C3: f64 = 2392.0 / 4096.0 * 32.0;
    let y = (nits.clamp(0.0, 10000.0) / 10000.0).powf(M1);
    ((C1 + C2 * y) / (1.0 + C3 * y)).powf(M2)
}

/// Linear BT.709/sRGB primaries -> linear BT.2020 primaries (ITU-R BT.2087), as the reference has it.
pub const SRGB_TO_BT2020: [[f64; 3]; 3] =
    [[0.6274039, 0.3292830, 0.0433131], [0.0690973, 0.9195404, 0.0113623], [0.0163914, 0.0880133, 0.8955953]];

/// A 3 x 3 matrix times a vector.
pub fn matrix3(m: &[[f64; 3]; 3], v: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|i| m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2])
}

/// The three 16-bit codes a 16-bit PQ file stores for one colour: linear RGB (BT.709 primaries, multiples of
/// white), to BT.2020, to PQ with white at `white_nits`, rounded to 16 bits. A NaN is stored as 0, as the
/// reference stores it.
pub fn pq16_codes(rgb: [f64; 3], white_nits: f64) -> [u16; 3] {
    let rgb = rgb.map(|c| if c.is_nan() { 0.0 } else { c });
    matrix3(&SRGB_TO_BT2020, rgb).map(|c| (pq_encode(c.max(0.0) * white_nits) * 65535.0).round_ties_even() as u16)
}

/// Linear light in `[0, 1]` -> sRGB signal; values outside are clipped.
pub fn srgb_oetf(v: f64) -> f64 {
    let c = v.clamp(0.0, 1.0);
    if c <= 0.0031308 { 12.92 * c } else { 1.055 * c.powf(1.0 / 2.4) - 0.055 }
}

/// sRGB signal in `[0, 1]` -> linear light: the inverse of [`srgb_oetf`]. Values outside are clipped.
pub fn srgb_eotf(v: f64) -> f64 {
    let c = v.clamp(0.0, 1.0);
    if c <= 0.04045 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
}

/// The `isolated-3x3` mask rule: the pixels of `above` (row order, `width` per row) that lie in no full 3 x 3
/// block of it. Outside the image counts as not above. This is the one operation that looks at neighbours, and
/// it runs before the mapping, never inside it.
pub fn isolated(above: &[bool], width: usize) -> Vec<bool> {
    let height = above.len().checked_div(width).unwrap_or(0);
    let get = |grid: &[bool], row: isize, col: isize| {
        row >= 0
            && col >= 0
            && (row as usize) < height
            && (col as usize) < width
            && grid[row as usize * width + col as usize]
    };
    let around = |grid: &[bool], row: usize, col: usize, all: bool| {
        let mut every = true;
        let mut any = false;
        for dy in -1..=1 {
            for dx in -1..=1 {
                let v = get(grid, row as isize + dy, col as isize + dx);
                every &= v;
                any |= v;
            }
        }
        if all { every } else { any }
    };
    let mut eroded = vec![false; above.len()];
    for row in 0..height {
        for col in 0..width {
            eroded[row * width + col] = around(above, row, col, true);
        }
    }
    let mut out = vec![false; above.len()];
    for row in 0..height {
        for col in 0..width {
            let i = row * width + col;
            out[i] = above[i] && !around(&eroded, row, col, false);
        }
    }
    out
}

/// The gain map's ISO 21496-1 metadata block for a single-channel description: versions and flags, the two
/// headrooms, then gain-map minimum, maximum, gamma and the two offsets, each as numerator / 1,000,000.
/// `gain_map_min`, `gain_map_max` and `hdr_capacity_max` are log2 values, as the Ultra HDR XMP gives them.
pub fn iso21496_block(gain_map_min: f64, gain_map_max: f64, hdr_capacity_max: f64) -> [u8; 61] {
    const D: u32 = 1_000_000;
    const OFFSET: f64 = 1.0 / 64.0;
    let n = |v: f64| (v * f64::from(D)).round_ties_even();
    let mut out = [0u8; 61];
    out[4] = 0x40; // flags: one set of values for all channels (bit 7 clear), the gain in the base's colour space
    let mut at = 5; // after two u16 versions, both zero, and the flags byte
    let mut put = |bytes: [u8; 4]| {
        out[at..at + 4].copy_from_slice(&bytes);
        at += 4;
    };
    let unsigned = |v: f64| (v as u32).to_be_bytes();
    let signed = |v: f64| (v as i32).to_be_bytes();
    put(0u32.to_be_bytes());
    put(D.to_be_bytes());
    put(unsigned(n(hdr_capacity_max)));
    put(D.to_be_bytes());
    for value in [
        signed(n(gain_map_min)),
        D.to_be_bytes(),
        signed(n(gain_map_max)),
        D.to_be_bytes(),
        D.to_be_bytes(),
        D.to_be_bytes(),
        signed(n(OFFSET)),
        D.to_be_bytes(),
        signed(n(OFFSET)),
        D.to_be_bytes(),
    ] {
        put(value);
    }
    out
}
