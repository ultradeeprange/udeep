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

/// A registered map's colour-map table made at any reference headroom, by the generator the published tables were
/// made with: `name` is `name_length` bytes, `fire` or `ice`; `version` 1, 2 or 3; `out` receives `2 * nodes * 3`
/// `float`s, as [`udeep_colours`] takes a table. For a figure at a headroom with no published table, which the file
/// must then carry (`udeep:ColourMapTable`, profile 6.10). A registered map at a registered headroom (2, 4, 8, 16)
/// is taken from its published table, never from this. Refused as [`crate::cmap::generate`] refuses.
///
/// # Safety
/// `name` must point to `name_length` readable bytes and `out` to `2 * nodes * 3` writable `float`s.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_colour_table(
    name: *const u8,
    name_length: usize,
    version: u32,
    headroom: f64,
    nodes: usize,
    out: *mut f32,
) -> i32 {
    if name.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    let Ok(name) = std::str::from_utf8(unsafe { std::slice::from_raw_parts(name, name_length) }) else {
        return Error::BadTable as i32;
    };
    match crate::cmap::generate(name, version, headroom, nodes) {
        Ok(table) => {
            unsafe { out.copy_from_nonoverlapping(table.as_ptr(), table.len()) };
            OK
        }
        Err(e) => e as i32,
    }
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
    // and for one shared buffer alike.
    let (from, to) = (par::Shared(x.cast_mut()), par::Shared(out));
    par::in_ranges(n, threads, |range| {
        let (x, out) = (from.get().cast_const(), to.get());
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
    let (from, to) = (par::Shared(light.cast_mut()), par::Shared(rgb));
    par::in_ranges(n, par::threads_for(threads, n), |range| {
        let (light, rgb) = (from.get().cast_const(), to.get());
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

/// Memory for a host that has none of its own the library can reach: a WebAssembly host, whose buffers must lie
/// inside the module's memory. `size` bytes, zeroed and aligned for a `double`; null when `size` is 0 or the
/// memory is not to be had. Release it with [`udeep_buffer_free`] and the same size. Only in a WebAssembly
/// build: anywhere else the caller's own buffers are passed as they are.
#[cfg(target_family = "wasm")]
#[unsafe(no_mangle)]
pub extern "C" fn udeep_buffer_alloc(size: usize) -> *mut u8 {
    match std::alloc::Layout::from_size_align(size, BUFFER_ALIGN) {
        Ok(layout) if size > 0 => unsafe { std::alloc::alloc_zeroed(layout) },
        _ => std::ptr::null_mut(),
    }
}

/// Release memory that [`udeep_buffer_alloc`] gave. Null is ignored.
///
/// # Safety
/// `data` must be what `udeep_buffer_alloc(size)` returned for this same `size`, and not already released.
#[cfg(target_family = "wasm")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_buffer_free(data: *mut u8, size: usize) {
    if data.is_null() {
        return;
    }
    if let Ok(layout) = std::alloc::Layout::from_size_align(size, BUFFER_ALIGN) {
        unsafe { std::alloc::dealloc(data, layout) };
    }
}

/// The alignment of memory from [`udeep_buffer_alloc`]: that of a `double`, the widest thing the interface takes.
#[cfg(target_family = "wasm")]
const BUFFER_ALIGN: usize = 8;

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
    unsafe {
        levels_of(
            mapping,
            table,
            nodes,
            scaled_by,
            samples,
            noise_model,
            noise_parameter,
            max_data_step,
            rounding_to_noise,
            std::ptr::null_mut(),
            false,
        )
    }
}

/// The most samples the levels functions take: 2^26, 64 times what the reference uses for a 16-bit file. The
/// work grows with the count, and a count near the top of a `size_t` would keep a call busy for years.
pub const MAX_SAMPLES: usize = 1 << 26;

/// The levels of a file of this mapping and the noise they are judged against: what [`udeep_levels`],
/// [`udeep_levels_pq16`] and [`udeep_levels_criterion`] share. `scaled_by` is the headroom the 8-bit file is written
/// for, or the white level in cd/m^2 of the 16-bit one. More than [`MAX_SAMPLES`] samples are refused.
#[allow(clippy::too_many_arguments)]
unsafe fn found_levels(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    scaled_by: f64,
    samples: usize,
    noise_model: u32,
    noise_parameter: f64,
    pq16: bool,
) -> Result<(record::Levels, Option<record::Noise>), Error> {
    let m = unsafe { mapping.as_ref() }.ok_or(Error::NullPointer)?;
    m.validate()?;
    if samples > MAX_SAMPLES {
        return Err(Error::BadDeclaration);
    }
    let lookup = if table.is_null() {
        None
    } else {
        Some(Table::new(unsafe { std::slice::from_raw_parts(table, nodes.saturating_mul(6)) }, m.reference_headroom)?)
    };
    let noise = match noise_model {
        0 => None,
        1 => Some(record::Noise::Constant(noise_parameter)),
        2 => Some(record::Noise::Poisson(noise_parameter)),
        3 => Some(record::Noise::Relative(noise_parameter)),
        4 => Some(record::Noise::Tolerance(noise_parameter)),
        _ => return Err(Error::BadDeclaration),
    };
    if noise.is_some() && !(noise_parameter.is_finite() && noise_parameter > 0.0) {
        return Err(Error::BadDeclaration);
    }
    let levels = if pq16 {
        record::levels_pq16(m, lookup.as_ref(), scaled_by, samples)
    } else {
        record::levels(m, lookup.as_ref(), scaled_by, samples)
    };
    Ok((levels, noise))
}

#[allow(clippy::too_many_arguments)]
unsafe fn levels_of(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    scaled_by: f64,
    samples: usize,
    noise_model: u32,
    noise_parameter: f64,
    max_data_step: *mut f64,
    rounding_to_noise: *mut f64,
    count: *mut usize,
    pq16: bool,
) -> i32 {
    if mapping.is_null() || max_data_step.is_null() || rounding_to_noise.is_null() {
        return Error::NullPointer as i32;
    }
    let (levels, noise) =
        match unsafe { found_levels(mapping, table, nodes, scaled_by, samples, noise_model, noise_parameter, pq16) } {
            Ok(found) => found,
            Err(e) => return e as i32,
        };
    let ratio = noise.map_or(f64::NAN, |n| record::rounding_to_noise(&levels, n).worst.min(record::UNBOUNDED));
    unsafe {
        max_data_step.write(levels.max_data_step());
        rounding_to_noise.write(ratio);
        if !count.is_null() {
            count.write(levels.width.len());
        }
    }
    OK
}

/// The stored levels of a file and the data criterion on them, in full (`udeep_criterion` in the header). The
/// numbers the criterion gives are NaN, and `meets` 0, when the noise is unknown.
#[derive(Clone, Copy, Debug)]
#[repr(C)]
pub struct CCriterion {
    /// How many stored levels there are.
    pub count: u64,
    /// The widest, in data units: `udeep:MaxDataStep`.
    pub max_data_step: f64,
    /// Where the first level starts, in data units.
    pub first_start: f64,
    /// The worst ratio of rounding error to uncertainty, at most 1e9: `udeep:RoundingToNoise`.
    pub rounding_to_noise: f64,
    /// The data value where the uncertainty was taken for the worst ratio.
    pub at: f64,
    /// The fraction of the declared range over the limit.
    pub failing_fraction: f64,
    /// The ratio a data record must not exceed: 0.1 for a noise model, 1 for a tolerance.
    pub limit: f64,
    /// 1 if no level exceeds the limit, else 0.
    pub meets: u32,
    pub reserved: u32,
}

/// The levels and the data criterion in full, as the reference's `data_steps` and `rounding_to_noise` give them:
/// [`udeep_levels`] (`pq16` = 0, `scaled_by` the headroom the 8-bit file is written for) or [`udeep_levels_pq16`]
/// (`pq16` = 1, `scaled_by` the white level in cd/m^2), with everything the criterion finds. The arguments and
/// the errors are theirs.
///
/// # Safety
/// As [`udeep_levels`]; `out` must point to a writable `udeep_criterion`.
#[allow(clippy::too_many_arguments)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_levels_criterion(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    scaled_by: f64,
    samples: usize,
    noise_model: u32,
    noise_parameter: f64,
    pq16: u32,
    out: *mut CCriterion,
) -> i32 {
    if mapping.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    if pq16 == 1 && !(scaled_by.is_finite() && scaled_by > 0.0) {
        return Error::BadDeclaration as i32;
    }
    if pq16 > 1 {
        return Error::BadDeclaration as i32;
    }
    let found =
        unsafe { found_levels(mapping, table, nodes, scaled_by, samples, noise_model, noise_parameter, pq16 == 1) };
    let (levels, noise) = match found {
        Ok(found) => found,
        Err(e) => return e as i32,
    };
    let criterion = noise.map(|n| record::rounding_to_noise(&levels, n));
    let number = |f: fn(&record::Criterion) -> f64| criterion.as_ref().map_or(f64::NAN, f);
    let result = CCriterion {
        count: levels.width.len() as u64,
        max_data_step: levels.max_data_step(),
        first_start: levels.start.first().copied().unwrap_or(f64::NAN),
        rounding_to_noise: number(|c| c.worst.min(record::UNBOUNDED)),
        at: number(|c| c.at),
        failing_fraction: number(|c| c.failing_fraction),
        limit: number(|c| c.limit),
        meets: u32::from(criterion.as_ref().is_some_and(|c| c.meets)),
        reserved: 0,
    };
    unsafe { out.write(result) };
    OK
}

/// As [`udeep_levels`], for a 16-bit PQ file with SDR white at `white_nits` (the reference uses 203): there
/// is one code for each colour and no split at the knee. `samples` should be about a million (the reference
/// uses 1048576), since such a file has tens of thousands of levels. `count`, if not null, receives the number
/// of levels found.
///
/// # Safety
/// As for [`udeep_levels`]; `count` must be null or point to a writable `size_t`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_levels_pq16(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    white_nits: f64,
    samples: usize,
    noise_model: u32,
    noise_parameter: f64,
    max_data_step: *mut f64,
    rounding_to_noise: *mut f64,
    count: *mut usize,
) -> i32 {
    if !(white_nits.is_finite() && white_nits > 0.0) {
        return Error::BadDeclaration as i32;
    }
    unsafe {
        levels_of(
            mapping,
            table,
            nodes,
            white_nits,
            samples,
            noise_model,
            noise_parameter,
            max_data_step,
            rounding_to_noise,
            count,
            true,
        )
    }
}

/// A 16-bit PQ PNG of `rgb` (`height * width * 3` `double`s, row by row: linear RGB, BT.709 primaries, multiples
/// of SDR white), with `1.0` written at `white_nits`. `xmp` is an XMP packet to carry (the declaration), of
/// `xmp_length` bytes, or null with a length of 0. The bytes are returned in `data` and `length`; release them
/// with [`udeep_bytes_free`]. A declaration (a packet in the `udeep` namespace) is written only with white at 203
/// cd/m^2 (profile 9.1.1): at any other white, `UDEEP_ERROR_BAD_DECLARATION`.
///
/// # Safety
/// `rgb` must point to `height * width * 3` readable `double`s, `xmp` to `xmp_length` readable bytes (or be null
/// with a length of 0), `data` and `length` to writable locations.
#[cfg(feature = "png")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_png_pq(
    rgb: *const f64,
    width: usize,
    height: usize,
    white_nits: f64,
    xmp: *const u8,
    xmp_length: usize,
    data: *mut *mut u8,
    length: *mut usize,
) -> i32 {
    if rgb.is_null() || data.is_null() || length.is_null() || (xmp.is_null() && xmp_length > 0) {
        return Error::NullPointer as i32;
    }
    if !(white_nits.is_finite() && white_nits > 0.0) {
        return Error::BadImage as i32;
    }
    let Some(count) = width.checked_mul(height).and_then(|n| n.checked_mul(3)) else { return Error::BadImage as i32 };
    let values = unsafe { std::slice::from_raw_parts(rgb, count) };
    let packet = if xmp_length == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(xmp, xmp_length) } };
    match crate::png::write_png_pq(values, width, height, white_nits, packet) {
        Ok(bytes) => {
            let boxed = bytes.into_boxed_slice();
            unsafe {
                length.write(boxed.len());
                data.write(Box::into_raw(boxed).cast::<u8>());
            }
            OK
        }
        Err(crate::png::PngError::White) => Error::BadDeclaration as i32,
        Err(_) => Error::BadImage as i32,
    }
}

/// The size of a 16-bit PQ PNG's image. `UDEEP_ERROR_BAD_IMAGE` if the bytes are a PNG of another kind,
/// `UDEEP_ERROR_CODEC` if they are not a whole PNG.
///
/// # Safety
/// `file` must point to `length` readable bytes; `width` and `height` to writable locations.
#[cfg(feature = "png")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_png_pq_size(
    file: *const u8,
    length: usize,
    width: *mut usize,
    height: *mut usize,
) -> i32 {
    use crate::png::PngError;
    if file.is_null() || width.is_null() || height.is_null() {
        return Error::NullPointer as i32;
    }
    match crate::png::png_pq_size(unsafe { std::slice::from_raw_parts(file, length) }) {
        Ok((w, h)) => {
            unsafe {
                width.write(w);
                height.write(h);
            }
            OK
        }
        Err(PngError::Corrupt) => Error::Codec as i32,
        Err(_) => Error::BadImage as i32,
    }
}

/// A 16-bit PQ PNG as light: `out` receives `height * width * 3` `double`s, linear RGB (BT.709 primaries) in
/// multiples of the white written at `white_nits`, row by row. `n` is the room in `out`, in values; it must be
/// exactly that many. Errors as for [`udeep_png_pq_size`], and `UDEEP_ERROR_BAD_IMAGE` if `n` is wrong.
///
/// # Safety
/// `file` must point to `length` readable bytes and `out` to `n` writable `double`s.
#[cfg(feature = "png")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_png_pq_read(
    file: *const u8,
    length: usize,
    white_nits: f64,
    out: *mut f64,
    n: usize,
) -> i32 {
    use crate::png::PngError;
    if file.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    if !(white_nits.is_finite() && white_nits > 0.0) {
        return Error::BadImage as i32;
    }
    match crate::png::read_png_pq(unsafe { std::slice::from_raw_parts(file, length) }, white_nits) {
        Ok((values, _, _)) if values.len() == n => {
            unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), out, n) };
            OK
        }
        Err(PngError::Corrupt) => Error::Codec as i32,
        Ok(_) | Err(_) => Error::BadImage as i32,
    }
}

/// The data values that the codes of a 16-bit PQ file stand for ([`record::values_pq16`]), whatever its colour map:
/// the file's mapping, its colour-map table (null for grey) and its white level, as for [`udeep_levels_pq16`].
/// `codes` holds `3 * n` codes, a pixel's three channels in turn; `out` receives `n` values, NaN for a pixel that
/// is no colour of this mapping. Refused: more than [`MAX_SAMPLES`] samples, a white level that is not a positive
/// number, or two runs of data values that share one code (`UDEEP_ERROR_BAD_DECLARATION`); `3 * n` too large for a
/// `size_t` (`UDEEP_ERROR_BAD_IMAGE`); and the mapping record's own codes.
///
/// # Safety
/// `mapping` must point to a readable record, `table` (if not null) to `2 * nodes * 3` readable `float`s, `codes`
/// to `3 * n` readable `uint16_t`s and `out` to `n` writable `double`s.
#[allow(clippy::too_many_arguments)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_values_pq16(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    white_nits: f64,
    samples: usize,
    codes: *const u16,
    n: usize,
    out: *mut f64,
) -> i32 {
    let Some(count) = n.checked_mul(3) else { return Error::BadImage as i32 };
    if n > 0 && (codes.is_null() || out.is_null()) {
        return Error::NullPointer as i32;
    }
    let given = if n == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(codes, count) } };
    match unsafe { values_of(mapping, table, nodes, white_nits, samples, given) } {
        Ok(values) => {
            if n > 0 {
                unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), out, n) };
            }
            OK
        }
        Err(e) => e as i32,
    }
}

unsafe fn values_of(
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    white_nits: f64,
    samples: usize,
    codes: &[u16],
) -> Result<Vec<f64>, Error> {
    let m = unsafe { mapping.as_ref() }.ok_or(Error::NullPointer)?;
    m.validate()?;
    if samples > MAX_SAMPLES || !(white_nits.is_finite() && white_nits > 0.0) {
        return Err(Error::BadDeclaration);
    }
    let lookup = if table.is_null() {
        None
    } else {
        Some(Table::new(unsafe { std::slice::from_raw_parts(table, nodes.saturating_mul(6)) }, m.reference_headroom)?)
    };
    if codes.is_empty() {
        return Ok(Vec::new()); // no pixels: the levels need not be found
    }
    record::values_pq16(m, lookup.as_ref(), white_nits, samples, codes)
}

/// A 16-bit PQ PNG read back as data: its codes ([`udeep_png_pq_read`] reads the same file as light) turned into the
/// data values they stand for, as [`udeep_values_pq16`] does, under the mapping and colour map the file declares,
/// which the caller reads from its declaration. `out` receives `height * width` values, row by row; `n` must be
/// exactly that many. Errors as for [`udeep_png_pq_size`] and [`udeep_values_pq16`], and
/// `UDEEP_ERROR_BAD_IMAGE` if `n` is wrong.
///
/// # Safety
/// `file` must point to `length` readable bytes, `mapping` to a readable record, `table` (if not null) to
/// `2 * nodes * 3` readable `float`s and `out` to `n` writable `double`s.
#[cfg(feature = "png")]
#[allow(clippy::too_many_arguments)]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_png_pq_values(
    file: *const u8,
    length: usize,
    mapping: *const Mapping,
    table: *const f32,
    nodes: usize,
    white_nits: f64,
    samples: usize,
    out: *mut f64,
    n: usize,
) -> i32 {
    use crate::png::PngError;
    if file.is_null() || out.is_null() {
        return Error::NullPointer as i32;
    }
    let codes = match crate::png::read_png_pq_codes(unsafe { std::slice::from_raw_parts(file, length) }) {
        Ok((codes, width, height)) if width.checked_mul(height) == Some(n) => codes,
        Err(PngError::Corrupt) => return Error::Codec as i32,
        Ok(_) | Err(_) => return Error::BadImage as i32,
    };
    match unsafe { values_of(mapping, table, nodes, white_nits, samples, &codes) } {
        Ok(values) => {
            unsafe { std::ptr::copy_nonoverlapping(values.as_ptr(), out, n) };
            OK
        }
        Err(e) => e as i32,
    }
}

unsafe fn signal(f: impl Fn(f64) -> f64, x: *const f64, out: *mut f64, n: usize) -> i32 {
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

// ---- The stages of the mapping on their own, as the golden vectors give them (scale, tone, PQ), and the bytes a
// file is checked by (a table's digest, the ISO 21496-1 block). A host that keeps its own mapping, a viewer that
// draws the curve, or a port that checks itself against the vectors needs these; the mapping record covers the rest.

/// Data values -> position in `[0, 1]` on a bare scale: `kind` 0 linear, 1 log, 2 asinh. `lo` goes to 0 and `hi`
/// to 1, clipped; NaN stays NaN; on a log scale a value at or below zero is position 0. Refused as the reference's
/// `scale` refuses: another kind (`UnknownStretch`; a proportional mapping is a linear scale), `hi` not above `lo`
/// or a log scale that does not start above zero (`BadRange`), a width not above zero (`BadStretchParameter`); and
/// first, unlike the reference, a parameter that is not finite (`NotFinite`). `x` and `out` may be the same buffer.
///
/// # Safety
/// `x` must point to `n` readable `double`s and `out` to `n` writable ones.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_scale(
    kind: u32,
    lo: f64,
    hi: f64,
    width: f64,
    x: *const f64,
    out: *mut f64,
    n: usize,
) -> i32 {
    if !(lo.is_finite() && hi.is_finite() && width.is_finite()) {
        return Error::NotFinite as i32;
    }
    let stretch = match crate::Stretch::from_code(kind) {
        Ok(s) if kind <= 2 => s,
        _ => return Error::UnknownStretch as i32,
    };
    if !(hi > lo) || (stretch == crate::Stretch::Log && !(lo > 0.0)) {
        return Error::BadRange as i32;
    }
    if !(width > 0.0) {
        return Error::BadStretchParameter as i32;
    }
    unsafe { signal(|v| crate::scale(v, stretch, lo, hi, width), x, out, n) }
}

/// Position in `[0, 1]` -> light, in multiples of SDR white: the tone curve alone, as the reference's `tone` draws
/// it. `knee` is a position, in `(0, 1]` (`KneeOutsideRange` otherwise); `headroom` at least 1 and `exit_slope` in
/// `0..=3` (`BadHeadroom` otherwise); none of them may be NaN or infinite (`NotFinite`, checked first). `t` and
/// `out` may be the same buffer.
///
/// # Safety
/// `t` must point to `n` readable `double`s and `out` to `n` writable ones.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_tone(
    knee: f64,
    headroom: f64,
    exit_slope: f64,
    t: *const f64,
    out: *mut f64,
    n: usize,
) -> i32 {
    if !(knee.is_finite() && headroom.is_finite() && exit_slope.is_finite()) {
        return Error::NotFinite as i32;
    }
    if !(knee > 0.0 && knee <= 1.0) {
        return Error::KneeOutsideRange as i32;
    }
    if !(headroom >= 1.0 && (0.0..=3.0).contains(&exit_slope)) {
        return Error::BadHeadroom as i32;
    }
    unsafe { signal(|v| crate::tone(v, knee, headroom, exit_slope), t, out, n) }
}

/// Luminance in cd/m^2 -> PQ signal in `[0, 1]` (SMPTE ST 2084); luminance outside 0..10000 is clipped to it.
/// `nits` and `out` may be the same buffer.
///
/// # Safety
/// As [`udeep_srgb_encode`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_pq_encode(nits: *const f64, out: *mut f64, n: usize) -> i32 {
    unsafe { signal(crate::pq_encode, nits, out, n) }
}

/// The digest of any bytes, written as the other digests are: `"sha256:"` and 64 hex digits, then a zero, into
/// 72 bytes at `out`. Of a colour-map table's file it is the digest the index of the shipped tables records.
/// `data` may be null when `length` is 0.
///
/// # Safety
/// `data` must point to `length` readable bytes and `out` to 72 writable ones.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_digest_bytes(data: *const u8, length: usize, out: *mut u8) -> i32 {
    if data.is_null() && length > 0 {
        return Error::NullPointer as i32;
    }
    let bytes = if length == 0 { &[][..] } else { unsafe { std::slice::from_raw_parts(data, length) } };
    unsafe { put_digest(&crate::sha256::digest(bytes), out) }
}

/// The length of the ISO 21496-1 gain-map metadata block this library writes.
pub const ISO21496_LENGTH: usize = 61;

/// The ISO 21496-1 metadata block of a gain map, the 61 bytes a gain-map JPEG carries: `gain_map_min`,
/// `gain_map_max` and `hdr_capacity_max` are log2 values, as the Ultra HDR XMP gives them, each stored as a
/// numerator over 1,000,000. A value that is not finite is refused (`NotFinite`), and one whose numerator does not
/// fit the block (`BadImage`): a 32-bit signed number for the two gains, unsigned for the capacity. The reference
/// refuses both.
///
/// # Safety
/// `out` must point to 61 writable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn udeep_iso21496_block(
    gain_map_min: f64,
    gain_map_max: f64,
    hdr_capacity_max: f64,
    out: *mut u8,
) -> i32 {
    if out.is_null() {
        return Error::NullPointer as i32;
    }
    if !(gain_map_min.is_finite() && gain_map_max.is_finite() && hdr_capacity_max.is_finite()) {
        return Error::NotFinite as i32;
    }
    let numerator = |v: f64| (v * 1_000_000.0).round_ties_even();
    let signed = |v: f64| (f64::from(i32::MIN)..=f64::from(i32::MAX)).contains(&numerator(v));
    if !(signed(gain_map_min)
        && signed(gain_map_max)
        && (0.0..=f64::from(u32::MAX)).contains(&numerator(hdr_capacity_max)))
    {
        return Error::BadImage as i32;
    }
    let block = crate::iso21496_block(gain_map_min, gain_map_max, hdr_capacity_max);
    unsafe { std::ptr::copy_nonoverlapping(block.as_ptr(), out, ISO21496_LENGTH) };
    OK
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
