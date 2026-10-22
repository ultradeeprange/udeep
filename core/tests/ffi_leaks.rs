//! The C interface leaks nothing. Every function is called many times, with good inputs and with inputs it
//! refuses, and the bytes it returns are released as the header says; afterwards the memory in use must be what
//! it was before. The allocator here counts every allocation and release, so a leak of a single byte shows.
//!
//! One test in its own binary: the count is of the whole process, and another test running beside it would
//! move it.
//!
//! Not under Miri, which finds leaks itself and does not accept this counting wrapper round the Windows system
//! allocator (it reports the allocator's own header read as a borrow violation).
#![cfg(not(miri))]

use std::alloc::{GlobalAlloc, Layout, System};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicIsize, Ordering};

use udeep_core::Mapping;
use udeep_core::ffi::*;

struct Counting;

static LIVE: AtomicIsize = AtomicIsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let p = unsafe { System.alloc(layout) };
        if !p.is_null() {
            LIVE.fetch_add(layout.size() as isize, Ordering::SeqCst);
        }
        p
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let p = unsafe { System.alloc_zeroed(layout) };
        if !p.is_null() {
            LIVE.fetch_add(layout.size() as isize, Ordering::SeqCst);
        }
        p
    }
    unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
        unsafe { System.dealloc(p, layout) };
        LIVE.fetch_sub(layout.size() as isize, Ordering::SeqCst);
    }
    unsafe fn realloc(&self, p: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        let q = unsafe { System.realloc(p, layout, size) };
        if !q.is_null() {
            LIVE.fetch_add(size as isize - layout.size() as isize, Ordering::SeqCst);
        }
        q
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

fn text(s: &'static [u8]) -> Text {
    Text { data: s.as_ptr(), length: s.len() }
}

fn declaration(m: Mapping, unit: Text) -> CDeclaration {
    let none = Text { data: null(), length: 0 };
    CDeclaration {
        mapping: m,
        max_data_step: 0.4,
        noise_parameter: f64::NAN,
        rounding_to_noise: f64::NAN,
        masked: 0,
        derived_masked: 0,
        data_unit: unit,
        knee_rule: text(b"preset"),
        noise_model: text(b"unknown"),
        record: text(b"rendering"),
        derived_mask: none,
        preset: none,
        colour_map: none,
        colour_map_table: none,
        data_digest: none,
        data_source: none,
        data_unit_code: none,
        sdr_digest: none,
        data_digest_sha3: none,
        sdr_digest_sha3: none,
    }
}

/// Bytes the library hands back: released at once. Returns whether there were any.
unsafe fn release(status: i32, data: *mut u8, length: usize) -> bool {
    if status == 0 {
        unsafe { udeep_bytes_free(data, length) };
    }
    status == 0
}

/// One call of everything, good and refused. The buffers the test itself holds are made before the count is taken.
unsafe fn everything(images: &Images) {
    let m = Mapping {
        range_low: -160.0,
        range_high: 700.0,
        knee: 240.0,
        reference_headroom: 4.0,
        stretch_width: 1.0,
        stretch_offset: 0.0,
        exit_slope: 1.0,
        stretch: 2,
        reserved: 0,
    };
    let mut bad = m;
    bad.knee = 9000.0;
    let x = &images.values;
    let mut out = [0.0f64; 300];
    let mut rgb = [0.0f64; 900];
    let mut found = std::mem::MaybeUninit::<CCriterion>::uninit();
    let (mut step, mut ratio, mut count) = (0.0, 0.0, 0usize);
    let mut digest = [0u8; 72];
    let mut block = [0u8; ISO21496_LENGTH];
    let mut mask = [0u8; 300];
    unsafe {
        for record in [&m, &bad] {
            udeep_mapping_validate(record);
            udeep_light(record, x.as_ptr(), out.as_mut_ptr(), 300);
            udeep_value(record, x.as_ptr(), out.as_mut_ptr(), 300);
            udeep_light_parallel(record, x.as_ptr(), out.as_mut_ptr(), 300, 4);
            udeep_levels(record, null(), 0, 4.0, 4097, 1, 3.0, &mut step, &mut ratio);
            udeep_levels_pq16(record, null(), 0, 203.0, 4097, 2, 1.0, &mut step, &mut ratio, &mut count);
            udeep_levels_criterion(record, images.table.as_ptr(), 2, 4.0, 4097, 4, 0.5, 0, found.as_mut_ptr());
            udeep_levels_criterion(record, null(), 0, 203.0, 4097, 3, 0.1, 1, found.as_mut_ptr());
            for unit in [text(b"K"), text(b"\xff")] {
                let d = declaration(*record, unit);
                let (mut data, mut length) = (null_mut::<u8>(), 0usize);
                release(udeep_description(&d, &mut data, &mut length), data, length);
                release(udeep_caption(&d, &mut data, &mut length), data, length);
            }
        }
        udeep_colours(images.table.as_ptr(), 2, 4.0, x.as_ptr(), rgb.as_mut_ptr(), 300);
        udeep_colours_parallel(images.table.as_ptr(), 2, 4.0, x.as_ptr(), rgb.as_mut_ptr(), 300, 4);
        udeep_colours(images.table.as_ptr(), 1, 4.0, x.as_ptr(), rgb.as_mut_ptr(), 300); // refused: too few nodes
        udeep_scale(1, 1.0, 1e4, 1.0, x.as_ptr(), out.as_mut_ptr(), 300);
        udeep_tone(0.8, 4.0, 1.0, x.as_ptr(), out.as_mut_ptr(), 300);
        udeep_pq_encode(x.as_ptr(), out.as_mut_ptr(), 300);
        udeep_srgb_encode(x.as_ptr(), out.as_mut_ptr(), 300);
        udeep_digest_data([10usize, 30].as_ptr(), 2, x.as_ptr(), digest.as_mut_ptr());
        udeep_digest_pixels(10, 10, images.pixels.as_ptr(), digest.as_mut_ptr());
        udeep_digest_bytes(images.pixels.as_ptr(), 300, digest.as_mut_ptr());
        udeep_iso21496_block(-0.01, 2.0, 2.0, block.as_mut_ptr());
        udeep_mask_isolated(images.pixels.as_ptr(), 10, 30, mask.as_mut_ptr());

        #[cfg(feature = "jpeg")]
        {
            let (w, h) = (images.width, images.height);
            for headroom in [4.0, f64::INFINITY] {
                let (mut data, mut length) = (null_mut::<u8>(), 0usize);
                let made = udeep_gainmap_jpeg(
                    images.sdr.as_ptr(),
                    images.hdr.as_ptr(),
                    w,
                    h,
                    headroom,
                    95,
                    100,
                    b"<rdf:Description/>".as_ptr(),
                    18,
                    &mut data,
                    &mut length,
                );
                if made == 0 {
                    let file = std::slice::from_raw_parts(data, length);
                    let (mut width, mut height) = (0usize, 0usize);
                    udeep_gainmap_size(file.as_ptr(), file.len(), &mut width, &mut height);
                    let mut shown = vec![0f32; width * height * 3];
                    udeep_gainmap_read(file.as_ptr(), file.len(), 4.0, shown.as_mut_ptr(), shown.len(), 0);
                    udeep_gainmap_read(file.as_ptr(), file.len() / 2, 4.0, shown.as_mut_ptr(), shown.len(), 0);
                    drop(shown);
                    udeep_bytes_free(data, length);
                }
            }
            let (mut data, mut length, mut lifted) = (null_mut::<u8>(), 0usize, 0usize);
            let r = images.renders.as_ptr();
            release(
                udeep_gainmap_from_renders(
                    r,
                    r,
                    r,
                    r,
                    w,
                    h,
                    4.0,
                    95,
                    100,
                    null(),
                    0,
                    &mut data,
                    &mut length,
                    &mut lifted,
                ),
                data,
                length,
            );
        }
        #[cfg(feature = "png")]
        {
            let (w, h) = (images.width, images.height);
            let (mut data, mut length) = (null_mut::<u8>(), 0usize);
            if udeep_png_pq(images.hdr.as_ptr(), w, h, 203.0, b"<x/>".as_ptr(), 4, &mut data, &mut length) == 0 {
                let file = std::slice::from_raw_parts(data, length);
                let (mut width, mut height) = (0usize, 0usize);
                udeep_png_pq_size(file.as_ptr(), file.len(), &mut width, &mut height);
                let mut light = vec![0f64; width * height * 3];
                udeep_png_pq_read(file.as_ptr(), file.len(), 203.0, light.as_mut_ptr(), light.len());
                udeep_png_pq_read(file.as_ptr(), file.len() - 7, 203.0, light.as_mut_ptr(), light.len());
                drop(light);
                udeep_bytes_free(data, length);
            }
            udeep_png_pq(images.hdr.as_ptr(), w, h, -1.0, null(), 0, &mut data, &mut length); // refused
        }
    }
}

struct Images {
    width: usize,
    height: usize,
    values: Vec<f64>,
    table: Vec<f32>,
    pixels: Vec<u8>,
    sdr: Vec<f64>,
    hdr: Vec<f64>,
    renders: Vec<u8>,
}

#[test]
fn nothing_the_c_interface_does_is_left_allocated() {
    let (width, height) = (24usize, 16usize);
    let n = width * height * 3;
    let images = Images {
        width,
        height,
        values: (0..300).map(|i| -200.0 + 3.1 * i as f64).collect(),
        table: (0..12).map(|i| (i % 6) as f32 / 5.0 * if i >= 6 { 4.0 } else { 1.0 }).collect(),
        pixels: (0..300).map(|i| (i * 37 % 256) as u8).collect(),
        sdr: (0..n).map(|i| (i % 89) as f64 / 89.0).collect(),
        hdr: (0..n).map(|i| (i % 89) as f64 / 89.0 * if i % 7 == 0 { 3.5 } else { 1.0 }).collect(),
        renders: (0..n).map(|i| (i * 13 % 256) as u8).collect(),
    };
    // first call: whatever the library or the standard library sets up once (the count of cores, codec tables)
    unsafe { everything(&images) };
    let before = LIVE.load(Ordering::SeqCst);
    for _ in 0..200 {
        unsafe { everything(&images) };
    }
    let after = LIVE.load(Ordering::SeqCst);
    assert_eq!(after - before, 0, "{} bytes left allocated after 200 rounds", after - before);
}
