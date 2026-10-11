//! The C interface (design decision D17): plain functions over buffers, a fixed-layout record, error codes
//! returned, nothing thrown, no global state. Every function may be called from several threads at once.
//!
//! Each buffer function takes a count `n`, reads `n` values and writes `n` results (or `3 * n` for colours).
//! Input and output may be the same buffer where their element types match. Return value: 0 on success, else
//! an [`Error`] code. The header is `include/udeep_core.h`.

use crate::{Error, Mapping, Table, par, record};

const OK: i32 = 0;

/// The library's version as `major * 10000 + minor * 100 + patch`: 0.3.0 is 300.
#[unsafe(no_mangle)]
pub extern "C" fn udeep_version() -> u32 {
    300
}

/// Check a mapping record. 0 if it is valid.
///
/// # Safety
/// `mapping` must be null or point to a readable `udeep_mapping`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_mapping_validate(mapping: *const Mapping) -> i32 {
    match unsafe { mapping.as_ref() } {
        None => Error::NullPointer as i32,
        Some(m) => m.validate().map_or_else(|e| e as i32, |_| OK),
    }
}

unsafe fn each(
    mapping: *const Mapping,
    x: *const f64,
    out: *mut f64,
    n: usize,
    f: impl Fn(&Mapping, f64, f64) -> f64,
) -> i32 {
    let Some(m) = (unsafe { mapping.as_ref() }) else { return Error::NullPointer as i32 };
    if n > 0 && (x.is_null() || out.is_null()) {
        return Error::NullPointer as i32;
    }
    if let Err(e) = m.validate() {
        return e as i32;
    }
    let knee = m.knee_position(); // once, not per value
    for i in 0..n {
        // read before write, so that `x` and `out` may be the same buffer
        let v = unsafe { x.add(i).read() };
        unsafe { out.add(i).write(f(m, knee, v)) };
    }
    OK
}

/// Data values -> position in `[0, 1]`.
///
/// # Safety
/// `mapping` must point to a readable record; `x` and `out` to `n` readable and writable `double`s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_position(mapping: *const Mapping, x: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { each(mapping, x, out, n, |m, _knee, v| m.position(v)) }
}

/// Data values -> light, in multiples of SDR white: the declared mapping.
///
/// # Safety
/// As [`udeep_position`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_light(mapping: *const Mapping, x: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { each(mapping, x, out, n, Mapping::light_with) }
}

/// Data values -> the fair SDR light: the same below the knee, SDR white above it.
///
/// # Safety
/// As [`udeep_position`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_sdr_light(mapping: *const Mapping, x: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { each(mapping, x, out, n, Mapping::sdr_light_with) }
}

/// Light -> data values: the inverse of `udeep_light`.
///
/// # Safety
/// As [`udeep_position`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_value(mapping: *const Mapping, light: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { each(mapping, light, out, n, Mapping::value_with) }
}

/// Position in `[0, 1]` -> data values: the inverse of `udeep_position` inside the declared range.
///
/// # Safety
/// As [`udeep_position`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_value_at(mapping: *const Mapping, t: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { each(mapping, t, out, n, |m, _knee, t| m.value_at(t)) }
}

/// Light -> linear RGB through a colour-map table. `table` holds `2 * nodes * 3` `float`s (the segment for light
/// 0..1, then the one for 1..headroom); pass null for grey. `rgb` receives `3 * n` values.
///
/// # Safety
/// `light` must point to `n` readable `double`s, `rgb` to `3 * n` writable ones, and `table`, if not null, to
/// `2 * nodes * 3` readable `float`s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_colours(
    table: *const f32,
    nodes: usize,
    headroom: f64,
    light: *const f64,
    rgb: *mut f64,
    n: usize,
) -> i32 {
    if n > 0 && (light.is_null() || rgb.is_null()) {
        return Error::NullPointer as i32;
    }
    let lookup = if table.is_null() {
        None
    } else {
        match Table::new(unsafe { std::slice::from_raw_parts(table, nodes.saturating_mul(6)) }, headroom) {
            Ok(t) => Some(t),
            Err(e) => return e as i32,
        }
    };
    for i in 0..n {
        let colour = crate::colours(unsafe { light.add(i).read() }, lookup.as_ref());
        for (k, c) in colour.into_iter().enumerate() {
            unsafe { rgb.add(3 * i + k).write(c) };
        }
    }
    OK
}

unsafe fn each_parallel(
    mapping: *const Mapping,
    x: *const f64,
    out: *mut f64,
    n: usize,
    threads: usize,
    f: impl Fn(&Mapping, f64, f64) -> f64 + Sync,
) -> i32 {
    let Some(m) = (unsafe { mapping.as_ref() }) else { return Error::NullPointer as i32 };
    if n > 0 && (x.is_null() || out.is_null()) {
        return Error::NullPointer as i32;
    }
    if let Err(e) = m.validate() {
        return e as i32;
    }
    let threads = par::threads_for(threads, n);
    let knee = m.knee_position();
    // Each thread owns a range of indices and, for each, reads `x` then writes `out`: sound for separate buffers
    // and for one shared buffer alike. The addresses cross the thread boundary as integers.
    let (from, to) = (x as usize, out as usize);
    par::in_ranges(n, threads, |range| {
        let (x, out) = (from as *const f64, to as *mut f64);
        for i in range {
            let v = unsafe { x.add(i).read() };
            unsafe { out.add(i).write(f(m, knee, v)) };
        }
    });
    OK
}

/// As [`udeep_light`], on `threads` threads (0: every available core). The result is identical. `x` and `out`
/// may be the same buffer. No thread is started when one is asked for, or when there are too few values to be
/// worth a second ([`par::threads_for`]): the loop then runs in the caller's thread.
///
/// # Safety
/// As [`udeep_position`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_light_parallel(
    mapping: *const Mapping,
    x: *const f64,
    out: *mut f64,
    n: usize,
    threads: usize,
) -> i32 {
    unsafe { each_parallel(mapping, x, out, n, threads, Mapping::light_with) }
}

/// As [`udeep_sdr_light`], on `threads` threads: see [`udeep_light_parallel`].
///
/// # Safety
/// As [`udeep_position`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_sdr_light_parallel(
    mapping: *const Mapping,
    x: *const f64,
    out: *mut f64,
    n: usize,
    threads: usize,
) -> i32 {
    unsafe { each_parallel(mapping, x, out, n, threads, Mapping::sdr_light_with) }
}

/// As [`udeep_colours`], on `threads` threads (0: every available core). The result is identical. `light` and
/// `rgb` must not overlap.
///
/// # Safety
/// As [`udeep_colours`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_colours_parallel(
    table: *const f32,
    nodes: usize,
    headroom: f64,
    light: *const f64,
    rgb: *mut f64,
    n: usize,
    threads: usize,
) -> i32 {
    if n > 0 && (light.is_null() || rgb.is_null()) {
        return Error::NullPointer as i32;
    }
    let lookup = if table.is_null() {
        None
    } else {
        match Table::new(unsafe { std::slice::from_raw_parts(table, nodes.saturating_mul(6)) }, headroom) {
            Ok(t) => Some(t),
            Err(e) => return e as i32,
        }
    };
    let lookup = lookup.as_ref();
    let (from, to) = (light as usize, rgb as usize);
    par::in_ranges(n, par::threads_for(threads, n), |range| {
        let (light, rgb) = (from as *const f64, to as *mut f64);
        for i in range {
            let colour = crate::colours(unsafe { light.add(i).read() }, lookup);
            for (k, c) in colour.into_iter().enumerate() {
                unsafe { rgb.add(3 * i + k).write(c) };
            }
        }
    });
    OK
}

/// The length of a digest as text, without the terminating zero: `"sha256:"` and 64 hex digits.
pub const DIGEST_LENGTH: usize = 71;

unsafe fn put_digest(text: &str, out: *mut u8) -> i32 {
    if out.is_null() {
        return Error::NullPointer as i32;
    }
    debug_assert_eq!(text.len(), DIGEST_LENGTH);
    unsafe {
        std::ptr::copy_nonoverlapping(text.as_ptr(), out, DIGEST_LENGTH);
        out.add(DIGEST_LENGTH).write(0);
    }
    OK
}

/// The digest of an array's values and shape (`udeep:DataDigest`). `shape` holds `dimensions` sizes whose
/// product is the number of values. `out` receives 71 characters and a terminating zero: 72 bytes.
///
/// `dimensions = 0` is a single value with no shape; `shape` may then be null. It is digested as a list of one
/// value (shape `1`), as the reference digests a scalar.
///
/// # Safety
/// `shape` must point to `dimensions` readable sizes, `values` to their product in `double`s, `out` to 72
/// writable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_digest_data(
    shape: *const usize,
    dimensions: usize,
    values: *const f64,
    out: *mut u8,
) -> i32 {
    if (shape.is_null() && dimensions > 0) || values.is_null() {
        return Error::NullPointer as i32;
    }
    // a slice may not be made from a null pointer, even an empty one
    let shape = if dimensions == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(shape, dimensions) } };
    let Some(count) = shape.iter().try_fold(1usize, |n, &s| n.checked_mul(s)) else { return Error::BadTable as i32 };
    let values = unsafe { std::slice::from_raw_parts(values, count) };
    unsafe { put_digest(&record::digest_data(shape, values), out) }
}

/// The digest of an 8-bit RGB image's pixels (`udeep:SdrDigest`): `height * width * 3` bytes, row order.
///
/// # Safety
/// `rgb` must point to `height * width * 3` readable bytes, `out` to 72 writable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_digest_pixels(height: usize, width: usize, rgb: *const u8, out: *mut u8) -> i32 {
    if rgb.is_null() {
        return Error::NullPointer as i32;
    }
    let Some(count) = height.checked_mul(width).and_then(|n| n.checked_mul(3)) else { return Error::BadTable as i32 };
    let rgb = unsafe { std::slice::from_raw_parts(rgb, count) };
    unsafe { put_digest(&record::digest_pixels(height, width, rgb), out) }
}

/// Write a gain-map JPEG into memory owned by the library. `sdr` (linear RGB in 0..1) and `hdr` (linear RGB in
/// multiples of SDR white) each hold `height * width * 3` values, row by row. `description` is `description_length`
/// bytes of UTF-8: an `rdf:Description` element for the file's metadata, or nothing. On success `*data` and
/// `*length` describe the file's bytes; release them with [`udeep_bytes_free`].
///
/// # Safety
/// `sdr` and `hdr` must point to `height * width * 3` readable `double`s, `description` to `description_length`
/// readable bytes (it may be null when the length is 0), `data` and `length` to writable locations.
#[cfg(feature = "jpeg")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_gainmap_jpeg(
    sdr: *const f64,
    hdr: *const f64,
    width: usize,
    height: usize,
    headroom: f64,
    quality: u32,
    gain_quality: u32,
    description: *const u8,
    description_length: usize,
    data: *mut *mut u8,
    length: *mut usize,
) -> i32 {
    use crate::gainmap::{WriteError, write_gainmap_jpeg};
    if sdr.is_null()
        || hdr.is_null()
        || data.is_null()
        || length.is_null()
        || (description.is_null() && description_length > 0)
    {
        return Error::NullPointer as i32;
    }
    let Some(count) = width.checked_mul(height).and_then(|n| n.checked_mul(3)) else { return Error::BadImage as i32 };
    let (sdr, hdr) = unsafe { (std::slice::from_raw_parts(sdr, count), std::slice::from_raw_parts(hdr, count)) };
    let text = if description_length == 0 {
        &[][..]
    } else {
        unsafe { std::slice::from_raw_parts(description, description_length) }
    };
    let Ok(text) = std::str::from_utf8(text) else { return Error::BadImage as i32 };
    let level = |q: u32| q.clamp(1, 100) as u8;
    match write_gainmap_jpeg(sdr, hdr, width, height, headroom, level(quality), level(gain_quality), text) {
        Ok(bytes) => {
            let boxed = bytes.into_boxed_slice();
            unsafe {
                length.write(boxed.len());
                data.write(Box::into_raw(boxed).cast::<u8>());
            }
            OK
        }
        Err(WriteError::Shape | WriteError::Range) => Error::BadImage as i32,
        Err(WriteError::SegmentTooLarge | WriteError::Codec(_) | WriteError::Headroom(_)) => Error::Codec as i32,
    }
}

/// Release bytes returned by this library. Null is ignored.
///
/// # Safety
/// `data` and `length` must be exactly what a function of this library returned, and not already released.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_bytes_free(data: *mut u8, length: usize) {
    if !data.is_null() {
        drop(unsafe { Box::from_raw(std::ptr::slice_from_raw_parts_mut(data, length)) });
    }
}

/// Text passed in: `length` bytes of UTF-8 at `data`. A null `data` means the property is absent.
#[derive(Clone, Copy)]
#[repr(C)]
pub struct Text {
    pub data: *const u8,
    pub length: usize,
}

/// What a file declares (profile section 8), for the C interface: the mapping record, the numbers, and the text
/// properties. The profile's version and image state are the library's own; the stretch and the form above the
/// knee are taken from the mapping record.
#[repr(C)]
pub struct CDeclaration {
    pub mapping: Mapping,
    pub max_data_step: f64,
    /// NaN when the noise model is `unknown`.
    pub noise_parameter: f64,
    /// NaN when the noise model is `unknown`.
    pub rounding_to_noise: f64,
    pub masked: u64,
    pub derived_masked: u64,
    pub data_unit: Text,
    pub knee_rule: Text,
    pub noise_model: Text,
    pub record: Text,
    pub derived_mask: Text,
    pub preset: Text,
    pub colour_map: Text,
    pub colour_map_table: Text,
    pub data_digest: Text,
    pub data_source: Text,
    pub data_unit_code: Text,
    pub sdr_digest: Text,
}

unsafe fn optional(text: Text) -> Result<Option<String>, Error> {
    if text.data.is_null() {
        return Ok(None);
    }
    let bytes = unsafe { std::slice::from_raw_parts(text.data, text.length) };
    std::str::from_utf8(bytes).map(|s| Some(s.to_string())).map_err(|_| Error::BadDeclaration)
}

unsafe fn required(text: Text) -> Result<String, Error> {
    unsafe { optional(text) }?.ok_or(Error::BadDeclaration)
}

unsafe fn declaration(c: *const CDeclaration) -> Result<crate::Declaration, Error> {
    let c = unsafe { c.as_ref() }.ok_or(Error::NullPointer)?;
    let stretch = c.mapping.validate()?;
    let number = |v: f64| if v.is_nan() { None } else { Some(v) };
    let name = match stretch {
        crate::Stretch::Linear => "linear",
        crate::Stretch::Log => "log",
        crate::Stretch::Asinh => "asinh",
        crate::Stretch::Proportional => "proportional",
    };
    let exit = crate::declaration::float_text(c.mapping.exit_slope).ok_or(Error::NotFinite)?;
    Ok(crate::Declaration {
        data_unit: unsafe { required(c.data_unit) }?,
        stretch: name.to_string(),
        range_low: c.mapping.range_low,
        range_high: c.mapping.range_high,
        knee: c.mapping.knee,
        knee_rule: unsafe { required(c.knee_rule) }?,
        reference_headroom: c.mapping.reference_headroom,
        max_data_step: c.max_data_step,
        noise_model: unsafe { required(c.noise_model) }?,
        noise_parameter: number(c.noise_parameter),
        stretch_width: c.mapping.stretch_width,
        stretch_offset: c.mapping.stretch_offset,
        above_knee: format!("hermite; exit={exit}"),
        masked: c.masked,
        derived_mask: unsafe { optional(c.derived_mask) }?,
        derived_masked: c.derived_masked,
        record: unsafe { required(c.record) }?,
        preset: unsafe { optional(c.preset) }?,
        colour_map: unsafe { optional(c.colour_map) }?,
        colour_map_table: unsafe { optional(c.colour_map_table) }?,
        rounding_to_noise: number(c.rounding_to_noise),
        data_digest: unsafe { optional(c.data_digest) }?,
        data_source: unsafe { optional(c.data_source) }?,
        data_unit_code: unsafe { optional(c.data_unit_code) }?,
        sdr_digest: unsafe { optional(c.sdr_digest) }?,
        version: "0.3".to_string(),
        image_state: "data-referred".to_string(),
    })
}

unsafe fn give(text: String, data: *mut *mut u8, length: *mut usize) -> i32 {
    if data.is_null() || length.is_null() {
        return Error::NullPointer as i32;
    }
    let boxed = text.into_bytes().into_boxed_slice();
    unsafe {
        length.write(boxed.len());
        data.write(Box::into_raw(boxed).cast::<u8>());
    }
    OK
}

/// The declaration as the `rdf:Description` element a file carries (UTF-8, not zero-terminated), in memory owned
/// by the library: release it with [`udeep_bytes_free`]. A declaration the profile would refuse returns
/// `BadDeclaration`, or the mapping record's own error.
///
/// # Safety
/// `declaration` must point to a readable record whose texts point to readable bytes; `data` and `length` to
/// writable locations.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_description(c: *const CDeclaration, data: *mut *mut u8, length: *mut usize) -> i32 {
    match unsafe { declaration(c) } {
        Err(e) => e as i32,
        Ok(d) => match crate::description(&d) {
            Ok(text) => unsafe { give(text, data, length) },
            Err(_) => Error::BadDeclaration as i32,
        },
    }
}

/// The caption paragraph generated from the declaration (profile 8.2), as UTF-8 in memory owned by the library.
///
/// # Safety
/// As [`udeep_description`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_caption(c: *const CDeclaration, data: *mut *mut u8, length: *mut usize) -> i32 {
    match unsafe { declaration(c) } {
        Err(e) => e as i32,
        Ok(d) if d.validate().is_err() => Error::BadDeclaration as i32,
        Ok(d) => unsafe { give(crate::caption(&d), data, length) },
    }
}

/// The stored levels of an 8-bit gain-map rendering of this mapping: the widest (`udeep:MaxDataStep`), and the
/// data criterion's worst ratio (`udeep:RoundingToNoise`, at most 1e9) for a noise model: 0 unknown (the ratio is
/// then NaN), 1 constant, 2 poisson, 3 relative, 4 tolerance. `table` is a colour-map table of `nodes` nodes per
/// segment, or null for grey; `scaled_by` the headroom the file is written for; `samples` the positions looked at
/// (the reference uses 65536).
///
/// # Safety
/// `mapping` must point to a readable record, `table` (if not null) to `2 * nodes * 3` readable `float`s, and the
/// two outputs to writable `double`s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_levels(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    scaled_by: f64,
    samples: usize,
    noise_model: u32,
    noise_parameter: f64,
    max_data_step: *mut f64,
    rounding_to_noise: *mut f64,
) -> i32 {
    let Some(m) = (unsafe { mapping.as_ref() }) else { return Error::NullPointer as i32 };
    if max_data_step.is_null() || rounding_to_noise.is_null() {
        return Error::NullPointer as i32;
    }
    if let Err(e) = m.validate() {
        return e as i32;
    }
    let lookup = if table.is_null() {
        None
    } else {
        match Table::new(unsafe { std::slice::from_raw_parts(table, nodes.saturating_mul(6)) }, m.reference_headroom) {
            Ok(t) => Some(t),
            Err(e) => return e as i32,
        }
    };
    let noise = match noise_model {
        0 => None,
        1 => Some(record::Noise::Constant(noise_parameter)),
        2 => Some(record::Noise::Poisson(noise_parameter)),
        3 => Some(record::Noise::Relative(noise_parameter)),
        4 => Some(record::Noise::Tolerance(noise_parameter)),
        _ => return Error::BadDeclaration as i32,
    };
    if noise.is_some() && !(noise_parameter.is_finite() && noise_parameter > 0.0) {
        return Error::BadDeclaration as i32;
    }
    let levels = record::levels(m, lookup.as_ref(), scaled_by, samples);
    let ratio = noise.map_or(f64::NAN, |n| record::rounding_to_noise(&levels, n).worst.min(record::UNBOUNDED));
    unsafe {
        max_data_step.write(levels.max_data_step());
        rounding_to_noise.write(ratio);
    }
    OK
}

unsafe fn signal(f: fn(f64) -> f64, x: *const f64, out: *mut f64, n: usize) -> i32 {
    if n == 0 {
        return OK;
    }
    if x.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    for i in 0..n {
        // one value at a time, so `x` and `out` may be the same buffer
        unsafe { out.add(i).write(f(x.add(i).read())) };
    }
    OK
}

/// Linear light -> sRGB signal, both in `[0, 1]` (values outside are clipped; NaN stays NaN). `x` and `out` may
/// be the same buffer.
///
/// # Safety
/// `x` must point to `n` readable `double`s and `out` to `n` writable ones.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_srgb_encode(x: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { signal(crate::srgb_oetf, x, out, n) }
}

/// sRGB signal -> linear light: the inverse of [`udeep_srgb_encode`].
///
/// # Safety
/// As [`udeep_srgb_encode`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_srgb_decode(x: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { signal(crate::srgb_eotf, x, out, n) }
}

/// The `isolated-3x3` mask rule (`udeep:DerivedMask`): of an image of `height` rows of `width` bytes, row
/// order, in which a non-zero byte is a pixel above the knee, `out` gets 1 where such a pixel lies in no full
/// 3 x 3 block of them, else 0. The two buffers must not overlap.
///
/// # Safety
/// `above` must point to `height * width` readable bytes and `out` to as many writable ones.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_mask_isolated(above: *const u8, height: usize, width: usize, out: *mut u8) -> i32 {
    let Some(n) = height.checked_mul(width) else { return Error::BadImage as i32 };
    if n == 0 {
        return OK;
    }
    if above.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    let lifted: Vec<bool> = unsafe { std::slice::from_raw_parts(above, n) }.iter().map(|&b| b != 0).collect();
    let held = crate::isolated(&lifted, width);
    let target = unsafe { std::slice::from_raw_parts_mut(out, n) };
    for (t, h) in target.iter_mut().zip(held) {
        *t = u8::from(h);
    }
    OK
}

/// The size of a gain-map JPEG's image. `UDEEP_ERROR_BAD_IMAGE` if the bytes are not a gain-map JPEG.
///
/// # Safety
/// `file` must point to `length` readable bytes; `width` and `height` to writable locations.
#[cfg(feature = "jpeg")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_gainmap_size(
    file: *const u8,
    length: usize,
    width: *mut usize,
    height: *mut usize,
) -> i32 {
    if file.is_null() || width.is_null() || height.is_null() {
        return Error::NullPointer as i32;
    }
    match crate::gainmap::gainmap_size(unsafe { std::slice::from_raw_parts(file, length) }) {
        Ok((w, h)) => {
            unsafe {
                width.write(w);
                height.write(h);
            }
            OK
        }
        Err(_) => Error::BadImage as i32,
    }
}

/// A gain-map JPEG as a display with `display_boost` times SDR white of headroom shows it: `out` receives
/// `height * width * 3` `float`s, linear RGB in multiples of SDR white, row by row. `n` is the room in `out`, in
/// values; it must be exactly that many. `UDEEP_ERROR_BAD_IMAGE` if the bytes are not a gain-map JPEG or `n`
/// is wrong; `UDEEP_ERROR_CODEC` if they are one this reader does not take (a gain map of another size than its
/// base).
///
/// # Safety
/// `file` must point to `length` readable bytes and `out` to `n` writable `float`s.
#[cfg(feature = "jpeg")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_gainmap_read(
    file: *const u8,
    length: usize,
    display_boost: f64,
    out: *mut f32,
    n: usize,
    threads: usize,
) -> i32 {
    use crate::gainmap::{ReadError, read_gainmap_jpeg};
    if file.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    if !(display_boost > 0.0) {
        return Error::BadImage as i32;
    }
    match read_gainmap_jpeg(unsafe { std::slice::from_raw_parts(file, length) }, display_boost, threads) {
        Ok((values, _, _)) if values.len() == n => {
            unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), out, n) };
            OK
        }
        Ok(_) | Err(ReadError::NotAGainMap(_)) => Error::BadImage as i32,
        Err(ReadError::Unsupported(_)) => Error::Codec as i32,
    }
}

/// A figure's gain-map JPEG straight from four 8-bit renders of it: see [`crate::gainmap::gainmap_from_renders`].
/// Each render is `height * width * 3` sRGB codes, row by row. The bytes are returned as by
/// [`udeep_gainmap_jpeg`] (release them with [`udeep_bytes_free`]), with the number of pixels lifted above
/// the ordinary figure in `lifted`.
///
/// # Safety
/// The four renders must each point to `height * width * 3` readable bytes; `description` to
/// `description_length` readable bytes of UTF-8 (or be null with a length of 0); `data`, `length` and `lifted`
/// to writable locations.
#[cfg(feature = "jpeg")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_gainmap_from_renders(
    base: *const u8,
    scaled: *const u8,
    black: *const u8,
    white: *const u8,
    width: usize,
    height: usize,
    headroom: f64,
    quality: u32,
    gain_quality: u32,
    description: *const u8,
    description_length: usize,
    data: *mut *mut u8,
    length: *mut usize,
    lifted: *mut usize,
) -> i32 {
    use crate::gainmap::{WriteError, gainmap_from_renders};
    let renders = [base, scaled, black, white];
    if renders.iter().any(|r| r.is_null())
        || data.is_null()
        || length.is_null()
        || lifted.is_null()
        || (description.is_null() && description_length > 0)
    {
        return Error::NullPointer as i32;
    }
    let Some(count) = width.checked_mul(height).and_then(|n| n.checked_mul(3)) else { return Error::BadImage as i32 };
    if quality == 0 || quality > 100 || gain_quality == 0 || gain_quality > 100 {
        return Error::BadImage as i32;
    }
    let text = if description_length == 0 {
        &[][..]
    } else {
        unsafe { std::slice::from_raw_parts(description, description_length) }
    };
    let Ok(text) = std::str::from_utf8(text) else { return Error::BadImage as i32 };
    let [base, scaled, black, white] = renders.map(|r| unsafe { std::slice::from_raw_parts(r, count) });
    match gainmap_from_renders(
        base,
        scaled,
        black,
        white,
        width,
        height,
        headroom,
        quality as u8,
        gain_quality as u8,
        text,
    ) {
        Ok((file, count_lifted)) => {
            let boxed = file.into_boxed_slice();
            unsafe {
                length.write(boxed.len());
                lifted.write(count_lifted);
                data.write(Box::into_raw(boxed).cast::<u8>());
            }
            OK
        }
        Err(WriteError::Shape | WriteError::Range) => Error::BadImage as i32,
        Err(WriteError::SegmentTooLarge | WriteError::Codec(_) | WriteError::Headroom(_)) => Error::Codec as i32,
    }
}
