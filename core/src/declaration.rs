//! What a file declares (profile section 8), its serialised form, and the caption generated from it.
//!
//! [`description`] must give the reference implementation's bytes exactly, and [`caption`] its text exactly: both
//! are golden cases. That fixes the order of the properties, the escaping, and how numbers are written: in the
//! metadata the shortest text that reads back to the same `f64` ([`float_text`]), so that a file states the
//! mapping that made its pixels and not one rounded from it; in the caption six significant figures.
//!
//! A declaration is checked before it is written ([`Declaration::validate`]), by the reference's rules: a file
//! this crate wrote must not be one the reference's reader refuses. Reading metadata back is not in this port,
//! so unlike the reference [`description`] does not parse its own output to compare.

use crate::{Error, Mapping, Stretch};

/// The profile version this crate writes, and the only image state it defines.
pub const VERSION: &str = "0.3";
pub const IMAGE_STATE: &str = "data-referred";
const NOISE_MODELS: [&str; 5] = ["constant", "poisson", "relative", "tolerance", "unknown"];
const KNEE_RULES: [&str; 4] = ["physical", "statistical", "preset", "percentile"];
const RECORDS: [&str; 2] = ["data-record", "rendering"];

/// The declared metadata. Field names and order follow the reference's record; `None` fields are not written.
#[derive(Clone, Debug, PartialEq)]
pub struct Declaration {
    pub data_unit: String,
    pub stretch: String,
    pub range_low: f64,
    pub range_high: f64,
    pub knee: f64,
    pub knee_rule: String,
    pub reference_headroom: f64,
    pub max_data_step: f64,
    pub noise_model: String,
    pub noise_parameter: Option<f64>,
    pub stretch_width: f64,
    pub stretch_offset: f64,
    pub above_knee: String,
    pub masked: u64,
    pub derived_mask: Option<String>,
    pub derived_masked: u64,
    pub record: String,
    pub preset: Option<String>,
    pub colour_map: Option<String>,
    pub colour_map_table: Option<String>,
    pub rounding_to_noise: Option<f64>,
    pub data_digest: Option<String>,
    pub data_source: Option<String>,
    pub data_unit_code: Option<String>,
    pub sdr_digest: Option<String>,
    pub data_digest_sha3: Option<String>,
    pub sdr_digest_sha3: Option<String>,
    pub version: String,
    pub image_state: String,
}

/// The exit slope in `udeep:AboveKnee`: the number in `hermite; exit=<number>`, if the text is exactly that form
/// (digits and points only) and the number is one the curve accepts, 0 to 3.
pub fn exit_slope(above_knee: &str) -> Option<f64> {
    let number = above_knee.strip_prefix("hermite; exit=")?;
    if number.is_empty() || !number.bytes().all(|c| c.is_ascii_digit() || c == b'.') {
        return None;
    }
    number.parse().ok().filter(|slope| (0.0..=3.0).contains(slope))
}

fn why(e: Error) -> String {
    let text = match e {
        Error::UnknownStretch => "stretch must be linear, log, asinh or proportional",
        Error::NotFinite => "the range, the knee, the headroom, the stretch width and the offset must be finite",
        Error::BadRange => "the declared range needs range_high > range_low, and range_low > 0 for a log stretch",
        Error::KneeOutsideRange => "the knee must lie inside the declared range, and not within 1e-6 of either end",
        Error::BadHeadroom => "reference_headroom must be >= 1",
        Error::BadStretchParameter => {
            "stretch_width must be > 0; a proportional mapping starts at zero with no offset, ends at knee x \
             reference_headroom and has above_knee 'hermite; exit=1'"
        }
        _ => "the mapping is refused",
    };
    text.to_string()
}

impl Declaration {
    /// The part of the declaration that defines the light: the record every computation takes, checked.
    pub fn mapping(&self) -> Result<Mapping, String> {
        let stretch = Stretch::from_name(&self.stretch).map_err(why)?;
        let exit_slope = exit_slope(&self.above_knee).ok_or_else(|| {
            format!("unknown form above the knee: {:?} (need 'hermite; exit=<number>' in 0..3)", self.above_knee)
        })?;
        let record = Mapping {
            stretch_width: self.stretch_width,
            stretch_offset: self.stretch_offset,
            exit_slope,
            ..Mapping::new(stretch, self.range_low, self.range_high, self.knee, self.reference_headroom)
        };
        record.validate().map_err(why)?;
        Ok(record)
    }

    /// The checks the reference makes when a declaration is built, in its order: what the profile fixes (section
    /// 8), and numbers that would mean nothing. `Err` says which property is at fault.
    pub fn validate(&self) -> Result<(), String> {
        let positive = |v: f64| v.is_finite() && v > 0.0;
        if self.data_unit.is_empty() {
            return Err("data_unit must be stated (use \"1\" for a dimensionless quantity)".into());
        }
        if !NOISE_MODELS.contains(&self.noise_model.as_str()) {
            return Err(format!("noise_model must be one of {NOISE_MODELS:?}, got {:?}", self.noise_model));
        }
        if (self.noise_model == "unknown") != self.noise_parameter.is_none() {
            return Err("noise_parameter is needed for every noise model except 'unknown', and only then".into());
        }
        if self.version != VERSION {
            return Err(format!("version must be {VERSION:?}, got {:?}", self.version));
        }
        if self.image_state != IMAGE_STATE {
            return Err(format!("image_state must be {IMAGE_STATE:?}, got {:?}", self.image_state));
        }
        if !positive(self.max_data_step) {
            return Err(format!("max_data_step must be a finite number > 0, got {}", self.max_data_step));
        }
        if let Some(parameter) = self.noise_parameter.filter(|&v| !positive(v)) {
            return Err(format!("noise_parameter must be a finite number > 0, got {parameter}"));
        }
        if let Some(ratio) = self.rounding_to_noise.filter(|&v| !(v.is_finite() && v >= 0.0)) {
            return Err(format!("rounding_to_noise must be a finite number >= 0, got {ratio}"));
        }
        if self.derived_masked > 0 && self.derived_mask.as_deref().unwrap_or("").is_empty() {
            return Err(format!("derived_mask must name the rule that holds {} pixels at white", self.derived_masked));
        }
        for (sha3, sha256, name) in [
            (&self.data_digest_sha3, &self.data_digest, "data_digest"),
            (&self.sdr_digest_sha3, &self.sdr_digest, "sdr_digest"),
        ] {
            if sha3.is_some() && sha256.is_none() {
                return Err(format!("{name}_sha3 is declared only with {name} (profile 8.5)"));
            }
        }
        self.mapping()?;
        if !KNEE_RULES.contains(&self.knee_rule.split(';').next().unwrap_or("").trim()) {
            return Err(format!("knee_rule must start with one of {KNEE_RULES:?}, then '; ' and a description"));
        }
        if !(self.reference_headroom > 1.0) {
            return Err("reference_headroom must be > 1".into());
        }
        if !RECORDS.contains(&self.record.as_str()) {
            return Err(format!("record must be one of {RECORDS:?}"));
        }
        if self.record == "data-record" && self.noise_model == "unknown" {
            return Err("a file with an unknown noise model cannot be a data-record (profile 7.3.1)".into());
        }
        Ok(())
    }
}

/// The namespace of the `udeep` properties for a profile version. The address is provisional.
pub fn namespace(version: &str) -> String {
    format!("https://ultradeeprange.github.io/udeep/ns/{version}/")
}

/// `x` as C's `%.{precision}g` writes it (and Python's `:.{precision}g`).
pub fn format_g(x: f64, precision: usize) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let precision = precision.max(1);
    let scientific = format!("{:.*e}", precision - 1, x); // correctly rounded to `precision` significant digits
    let (mantissa, exponent) = scientific.split_once('e').expect("an exponent");
    let exponent: i32 = exponent.parse().expect("a number");
    let trim = |s: &str| {
        if s.contains('.') { s.trim_end_matches('0').trim_end_matches('.').to_string() } else { s.to_string() }
    };
    if exponent < -4 || exponent >= precision as i32 {
        format!("{}e{}{:02}", trim(mantissa), if exponent < 0 { '-' } else { '+' }, exponent.abs())
    } else {
        trim(&format!("{:.*}", (precision as i32 - 1 - exponent).max(0) as usize, x))
    }
}

/// The digits of `text` (as `{:e}` writes a number) without the point, and its exponent.
fn digits_and_exponent(text: &str) -> (String, i32) {
    let (mantissa, exponent) = text.split_once('e').expect("an exponent");
    (mantissa.chars().filter(|c| *c != '.').collect(), exponent.parse().expect("a number"))
}

/// The shortest decimal digits that read back to `x` (finite, not negative), and the exponent of the first.
///
/// Rust's own formatting gives them, except in one case. When `x` lies exactly half way between the two nearest
/// numbers of that many digits, both read back, and the choice is a convention: Rust takes the upper, Python the
/// one whose last digit is even (884938148863529.25 is `...529.3` in Rust and `...529.2` in Python). The
/// metadata's text is Python's, so that case is found from the exact digits of `x` and decided Python's way.
fn shortest_digits(x: f64) -> (String, i32) {
    const EXACT: usize = 767; // no f64 has more significant decimal digits than this
    let (shortest, exponent) = digits_and_exponent(&format!("{x:e}"));
    let (exact, _) = digits_and_exponent(&format!("{x:.*e}", EXACT, x = x));
    let (below, rest) = exact.split_at(shortest.len());
    let half_way = rest.starts_with('5') && rest[1..].bytes().all(|c| c == b'0');
    if !half_way {
        return (shortest, exponent);
    }
    let last_is_even = (below.as_bytes()[below.len() - 1] - b'0').is_multiple_of(2);
    let chosen = if last_is_even { Some(below.to_string()) } else { next_up(below) };
    // only if it reads back: at the edge of a binade one of the two may not
    let reads_back = |d: &String| format!("{}.{}e{exponent}", &d[..1], &d[1..]).parse() == Ok(x);
    (chosen.filter(reads_back).unwrap_or(shortest), exponent)
}

/// The decimal digits one above `digits` in the last place, or `None` if that takes another digit.
fn next_up(digits: &str) -> Option<String> {
    let mut out = digits.as_bytes().to_vec();
    for d in out.iter_mut().rev() {
        if *d < b'9' {
            *d += 1;
            return String::from_utf8(out).ok();
        }
        *d = b'0';
    }
    None
}

/// A number as the metadata writes it: the shortest text that reads back to the same `f64`. `None` for a NaN or
/// an infinity, which cannot be written.
///
/// It is Python's `repr` of the float without the `.0` that ends a whole number: 4 is `4`, 0.1 is `0.1`,
/// 1700000002.3456 is itself, 1e16 is `1e+16` and 0.00001 is `1e-05`. The digits are the shortest that read back;
/// they are written plainly when the exponent is -4 to 15, and otherwise as the first digit, a point and the rest
/// if there are more, then `e`, the exponent's sign and at least two digits of it.
pub fn float_text(x: f64) -> Option<String> {
    if !x.is_finite() {
        return None;
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    let (digits, exponent) = shortest_digits(x.abs());
    let body = if !(-4..16).contains(&exponent) {
        let rest = if digits.len() > 1 { format!(".{}", &digits[1..]) } else { String::new() };
        format!("{}{rest}e{}{:02}", &digits[..1], if exponent < 0 { '-' } else { '+' }, exponent.abs())
    } else if exponent < 0 {
        format!("0.{}{digits}", "0".repeat((-exponent - 1) as usize))
    } else {
        let whole = exponent as usize + 1; // digits before the point
        if digits.len() <= whole {
            format!("{digits}{}", "0".repeat(whole - digits.len()))
        } else {
            format!("{}.{}", &digits[..whole], &digits[whole..])
        }
    };
    Some(format!("{sign}{body}"))
}

/// An XML attribute value with its quotes, as Python's `xml.sax.saxutils.quoteattr` writes it.
fn quote(text: &str) -> String {
    let escaped = text
        .replace('&', "&amp;")
        .replace('>', "&gt;")
        .replace('<', "&lt;")
        .replace('\n', "&#10;")
        .replace('\r', "&#13;")
        .replace('\t', "&#9;");
    if !escaped.contains('"') {
        format!("\"{escaped}\"")
    } else if !escaped.contains('\'') {
        format!("'{escaped}'")
    } else {
        format!("\"{}\"", escaped.replace('"', "&quot;"))
    }
}

/// The declaration as one `rdf:Description` element, to sit in an XMP packet. `Err` if the declaration is not
/// valid ([`Declaration::validate`]): nothing is written that the reference's reader would refuse.
pub fn description(d: &Declaration) -> Result<String, String> {
    d.validate()?;
    let mut out = format!("<rdf:Description rdf:about=\"\" xmlns:udeep=\"{}\"", namespace(&d.version));
    let mut put = |name: &str, value: Option<String>| {
        if let Some(v) = value {
            out.push_str(&format!(" udeep:{name}={}", quote(&v)));
        }
    };
    let number = float_text; // validate found every number finite
    let text = |v: &str| Some(v.to_string());
    put("DataUnit", text(&d.data_unit));
    put("Stretch", text(&d.stretch));
    put("RangeLow", number(d.range_low));
    put("RangeHigh", number(d.range_high));
    put("Knee", number(d.knee));
    put("KneeRule", text(&d.knee_rule));
    put("ReferenceHeadroom", number(d.reference_headroom));
    put("MaxDataStep", number(d.max_data_step));
    put("NoiseModel", text(&d.noise_model));
    put("NoiseParameter", d.noise_parameter.and_then(number));
    put("StretchWidth", number(d.stretch_width));
    put("StretchOffset", number(d.stretch_offset));
    put("AboveKnee", text(&d.above_knee));
    put("Masked", Some(d.masked.to_string()));
    put("DerivedMask", d.derived_mask.clone());
    put("DerivedMasked", Some(d.derived_masked.to_string()));
    put("Record", text(&d.record));
    put("Preset", d.preset.clone());
    put("ColourMap", d.colour_map.clone());
    put("ColourMapTable", d.colour_map_table.clone());
    put("RoundingToNoise", d.rounding_to_noise.and_then(number));
    put("DataDigest", d.data_digest.clone());
    put("DataSource", d.data_source.clone());
    put("DataUnitCode", d.data_unit_code.clone());
    put("SdrDigest", d.sdr_digest.clone());
    put("DataDigestSha3", d.data_digest_sha3.clone());
    put("SdrDigestSha3", d.sdr_digest_sha3.clone());
    put("Version", text(&d.version));
    put("ImageState", text(&d.image_state));
    out.push_str("/>");
    Ok(out)
}

/// Significant figures in a caption's numbers: enough for a range top such as 16383 to be printed as itself.
const CAPTION_FIGURES: usize = 6;

/// Six significant figures, written out in full between a thousandth and a million (where every digit shown is a
/// digit of the number), with a true minus sign.
fn caption_number(v: f64) -> String {
    let rounded: f64 = format_g(v, CAPTION_FIGURES).parse().expect("a number");
    let text = if rounded == 0.0 || (1e-3..1e6).contains(&rounded.abs()) {
        format!("{rounded}") // the shortest digits that give the number back, never an exponent
    } else {
        format_g(rounded, CAPTION_FIGURES)
    };
    text.replace('-', "\u{2212}")
}

/// One caption paragraph generated from the declaration (profile 8.2 and 9.4.2).
pub fn caption(d: &Declaration) -> String {
    let unit = if d.data_unit == "1" { String::new() } else { format!(" {}", d.data_unit) };
    let (rule, why) = d.knee_rule.split_once(';').unwrap_or((&d.knee_rule, ""));
    let (rule, why) = (rule.trim(), why.trim());
    let scale = if d.stretch == "proportional" {
        "in proportion to the value".to_string()
    } else {
        format!("on {} {} scale", if d.stretch == "asinh" { "an" } else { "a" }, d.stretch)
    };
    let mut parts = vec![
        format!(
            "Colour shows the data {scale} from {} to {}{unit}.",
            caption_number(d.range_low + d.stretch_offset),
            caption_number(d.range_high + d.stretch_offset)
        ),
        format!(
            "White is at {}{unit} {}.",
            caption_number(d.knee),
            if why.is_empty() { format!("(a {rule} threshold)") } else { format!("({why})") }
        ),
        format!(
            "In the HDR version, values above it are brighter, up to {} \u{d7} white at the top of the range; in the \
             ordinary version they are white.",
            caption_number(d.reference_headroom)
        ),
    ];
    if rule == "percentile" {
        parts.push("The white level was set from a percentile of this image, so the figure is exploratory.".into());
    }
    if d.masked > 0 {
        parts.push(format!("{} invalid or saturated pixels are held at or below white.", d.masked));
    }
    match d.derived_mask.as_deref() {
        Some("isolated-3x3") => {
            let held = if d.derived_masked > 0 {
                format!("{} isolated pixels above it are held at white", d.derived_masked)
            } else {
                "no pixel was held by that rule".to_string()
            };
            parts.push(format!(
                "Values above white are shown brighter only where they fill a 3 \u{d7} 3 block of pixels; {held}."
            ));
        }
        Some(other) if !other.is_empty() => {
            parts.push(format!("{} further pixels are held at white by the rule {other}.", d.derived_masked));
        }
        _ => {}
    }
    if d.noise_model != "unknown" {
        let parameter = d.noise_parameter.map_or_else(|| "?".to_string(), caption_number);
        parts.push(format!("Stated uncertainty: {}, parameter {parameter}.", d.noise_model));
    }
    if let Some(ratio) = d.rounding_to_noise {
        let limit = if d.noise_model == "tolerance" { 1.0 } else { 0.1 };
        let worst =
            if ratio > 100.0 { "more than 100 times".to_string() } else { format!("up to {} of", format_g(ratio, 2)) };
        parts.push(format!(
            "Rounding to this file's levels is {worst} the stated uncertainty (a data record needs {} or less).",
            format_g(limit, 6)
        ));
    }
    let kind = if d.record == "data-record" { "a data record" } else { "a rendering, not a data record" };
    parts.push(format!("This file is {kind}: one stored level spans up to {}{unit}.", caption_number(d.max_data_step)));
    if let Some(source) = &d.data_source {
        // profile 9.4.5: where the data is kept, and its digests in full, so the paper itself holds the link
        let digests: Vec<&str> =
            [&d.data_digest, &d.data_digest_sha3].into_iter().flatten().map(String::as_str).collect();
        let with = match digests.len() {
            0 => String::new(),
            1 => format!(", with the digest {}", digests[0]),
            _ => format!(", with the digests {}", digests.join(" and ")),
        };
        parts.push(format!("The data is kept at {source}{with}."));
    }
    parts.join(" ")
}
