//! The C interface under hostile and heavy use. A host may hand it anything the types allow: null pointers, NaNs
//! and infinities, sizes whose product overflows, text that is not UTF-8, the same buffer in and out, and calls
//! from many threads at once. Every such call must come back with a code. The library is built to abort on a
//! panic, which would take the host with it, so a panic anywhere here is a bug.
//!
//! Under Miri (`cargo +nightly miri test --test ffi_stress`) the same calls are checked for undefined behaviour and
//! leaks, with smaller counts; the image writers and readers, which Miri runs far too slowly, are left out there.
//! Leaks in a native build are counted by tests/ffi_leaks.rs.

use std::ptr::{null, null_mut};

use udeep_core::Mapping;
use udeep_core::ffi::*;

const OK: i32 = 0;
const NULL: i32 = 1;

struct Stream(u64);

impl Stream {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(0x5851_f42d_4c95_7f2d).wrapping_add(0x1405_7b7e_f767_814f);
        self.0 >> 11
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n.max(1)
    }
    /// A number a careless or hostile host might pass: ordinary values, edges, zero, NaN, infinities, raw bits.
    fn number(&mut self) -> f64 {
        match self.below(8) {
            0 => f64::NAN,
            1 => {
                [f64::INFINITY, f64::NEG_INFINITY, f64::MAX, f64::MIN, f64::MIN_POSITIVE, -0.0][self.below(6) as usize]
            }
            2 => 0.0,
            3 => f64::from_bits(self.next() << 11 | self.next() & 0x7ff),
            4 => [1e-6, 1.0 - 1e-6, 1.0, 1e300, -1e300][self.below(5) as usize],
            _ => (self.below(2_000_001) as f64 - 1_000_000.0) / 1000.0,
        }
    }
}

const fn rounds(native: usize, miri: usize) -> usize {
    if cfg!(miri) { miri } else { native }
}

fn grey() -> Mapping {
    Mapping {
        range_low: -160.0,
        range_high: 700.0,
        knee: 240.0,
        reference_headroom: 4.0,
        stretch_width: 1.0,
        stretch_offset: 0.0,
        exit_slope: 1.0,
        stretch: 0,
        reserved: 0,
    }
}

fn random_mapping(s: &mut Stream) -> Mapping {
    let mut m = grey();
    // change some fields at random, so that most records are near a valid one and reach past the first check
    for _ in 0..1 + s.below(3) {
        match s.below(9) {
            0 => m.range_low = s.number(),
            1 => m.range_high = s.number(),
            2 => m.knee = s.number(),
            3 => m.reference_headroom = s.number(),
            4 => m.stretch_width = s.number(),
            5 => m.stretch_offset = s.number(),
            6 => m.exit_slope = s.number(),
            7 => m.stretch = s.below(6) as u32,
            _ => m.reserved = s.next() as u32,
        }
    }
    m
}

/// Every function over a record and values gives a code, never a panic, for any record; a valid record gives
/// numbers or NaN, and the parallel forms give the plain forms' bits.
#[test]
fn any_mapping_record_comes_back_as_a_code_or_as_numbers() {
    let mut s = Stream(1);
    let x: Vec<f64> = (0..64).map(|_| s.number()).collect();
    let (mut a, mut b) = (vec![0.0; 64], vec![0.0; 64]);
    let mut levels = [0.0f64; 2];
    let mut found = std::mem::MaybeUninit::<CCriterion>::uninit();
    let mut count = 0usize;
    for round in 0..rounds(20_000, 60) {
        let m = random_mapping(&mut s);
        let valid = unsafe { udeep_mapping_validate(&m) };
        assert!((0..=11).contains(&valid), "code {valid}");
        type Each = unsafe extern "C" fn(*const Mapping, *const f64, *mut f64, usize) -> i32;
        let each: [Each; 5] = [udeep_position, udeep_light, udeep_sdr_light, udeep_value, udeep_value_at];
        for f in each {
            assert_eq!(unsafe { f(&m, x.as_ptr(), a.as_mut_ptr(), x.len()) }, valid);
        }
        let light = unsafe { udeep_light(&m, x.as_ptr(), a.as_mut_ptr(), x.len()) };
        let parallel = unsafe { udeep_light_parallel(&m, x.as_ptr(), b.as_mut_ptr(), x.len(), 1 + round % 9) };
        assert_eq!(light, parallel);
        if valid == OK {
            assert!(a.iter().zip(&b).all(|(p, q)| p.to_bits() == q.to_bits()));
            assert!(a.iter().all(|v| v.is_nan() || (0.0..=m.reference_headroom).contains(v)), "light out of range");
        }
        // the levels look at the whole range: a modest count of samples keeps this quick
        let noise = s.below(6) as u32;
        let parameter = s.number();
        let samples = s.below(300) as usize;
        let short = unsafe {
            udeep_levels(&m, null(), 0, s.number(), samples, noise, parameter, &mut levels[0], &mut levels[1])
        };
        let full = unsafe {
            udeep_levels_criterion(
                &m,
                null(),
                0,
                4.0,
                samples,
                noise,
                parameter,
                (round % 3) as u32,
                found.as_mut_ptr(),
            )
        };
        let pq = unsafe {
            udeep_levels_pq16(
                &m,
                null(),
                0,
                s.number(),
                samples,
                noise,
                parameter,
                &mut levels[0],
                &mut levels[1],
                &mut count,
            )
        };
        for code in [short, full, pq] {
            assert!((0..=11).contains(&code), "code {code}");
        }
    }
}

/// A sample count that would take years is refused at once, in all three levels functions.
#[test]
fn a_sample_count_beyond_the_limit_is_refused_at_once() {
    let m = grey();
    let (mut step, mut ratio, mut count) = (0.0, 0.0, 0usize);
    let mut found = std::mem::MaybeUninit::<CCriterion>::uninit();
    for samples in [MAX_SAMPLES + 1, usize::MAX] {
        unsafe {
            assert_eq!(udeep_levels(&m, null(), 0, 4.0, samples, 1, 1.0, &mut step, &mut ratio), 11);
            assert_eq!(udeep_levels_pq16(&m, null(), 0, 203.0, samples, 1, 1.0, &mut step, &mut ratio, &mut count), 11);
            assert_eq!(udeep_levels_criterion(&m, null(), 0, 4.0, samples, 1, 1.0, 0, found.as_mut_ptr()), 11);
        }
    }
}

/// A null pointer where something is needed is refused; with nothing to do it is not an error.
#[test]
fn null_pointers_are_refused_and_empty_calls_are_not_errors() {
    let m = grey();
    let mut out = [0.0f64; 4];
    let x = [1.0f64; 4];
    unsafe {
        assert_eq!(udeep_mapping_validate(null()), NULL);
        assert_eq!(udeep_light(null(), x.as_ptr(), out.as_mut_ptr(), 4), NULL);
        assert_eq!(udeep_light(&m, null(), out.as_mut_ptr(), 4), NULL);
        assert_eq!(udeep_light(&m, x.as_ptr(), null_mut(), 4), NULL);
        assert_eq!(udeep_light(&m, null(), null_mut(), 0), OK);
        assert_eq!(udeep_light_parallel(&m, null(), null_mut(), 0, 0), OK);
        assert_eq!(udeep_colours(null(), 0, 4.0, null(), out.as_mut_ptr(), 4), NULL);
        assert_eq!(udeep_colours(null(), 0, 4.0, null(), null_mut(), 0), OK);
        assert_eq!(udeep_colours_parallel(null(), 0, 4.0, x.as_ptr(), null_mut(), 1, 4), NULL);
        let mut table = [0.0f32; 18];
        assert_eq!(udeep_colour_table(null(), 4, 3, 3.0, 3, table.as_mut_ptr()), NULL);
        assert_eq!(udeep_colour_table(b"fire".as_ptr(), 4, 3, 3.0, 3, null_mut()), NULL);
        assert_eq!(udeep_srgb_encode(null(), out.as_mut_ptr(), 4), NULL);
        assert_eq!(udeep_srgb_decode(null(), null_mut(), 0), OK);
        assert_eq!(udeep_pq_encode(x.as_ptr(), null_mut(), 4), NULL);
        assert_eq!(udeep_scale(0, 0.0, 1.0, 1.0, null(), out.as_mut_ptr(), 4), NULL);
        assert_eq!(udeep_tone(0.5, 4.0, 1.0, x.as_ptr(), null_mut(), 4), NULL);
        let mut digest = [0u8; 72];
        assert_eq!(udeep_digest_data(null(), 1, x.as_ptr(), digest.as_mut_ptr()), NULL);
        assert_eq!(udeep_digest_data(null(), 0, null(), digest.as_mut_ptr()), NULL);
        assert_eq!(udeep_digest_data([1usize].as_ptr(), 1, x.as_ptr(), null_mut()), NULL);
        assert_eq!(udeep_digest_pixels(1, 1, null(), digest.as_mut_ptr()), NULL);
        assert_eq!(udeep_digest_bytes(null(), 3, digest.as_mut_ptr()), NULL);
        assert_eq!(udeep_digest_bytes(null(), 0, digest.as_mut_ptr()), OK);
        assert_eq!(udeep_iso21496_block(0.0, 1.0, 1.0, null_mut()), NULL);
        assert_eq!(udeep_mask_isolated(null(), 2, 2, null_mut()), NULL);
        assert_eq!(udeep_mask_isolated(null(), 0, 5, null_mut()), OK);
        let (mut step, mut ratio) = (0.0, 0.0);
        assert_eq!(udeep_levels(null(), null(), 0, 4.0, 10, 0, 0.0, &mut step, &mut ratio), NULL);
        assert_eq!(udeep_levels(&m, null(), 0, 4.0, 10, 0, 0.0, null_mut(), &mut ratio), NULL);
        assert_eq!(udeep_levels_criterion(&m, null(), 0, 4.0, 10, 0, 0.0, 0, null_mut()), NULL);
        let codes = [0u16; 6];
        assert_eq!(udeep_values_pq16(null(), null(), 0, 203.0, 10, codes.as_ptr(), 2, out.as_mut_ptr()), NULL);
        assert_eq!(udeep_values_pq16(&m, null(), 0, 203.0, 10, null(), 2, out.as_mut_ptr()), NULL);
        assert_eq!(udeep_values_pq16(&m, null(), 0, 203.0, 10, codes.as_ptr(), 2, null_mut()), NULL);
        assert_eq!(udeep_values_pq16(&m, null(), 0, 203.0, 10, null(), 0, null_mut()), OK);
        assert_eq!(udeep_values_pq16(&m, null(), 0, 0.0, 10, codes.as_ptr(), 2, out.as_mut_ptr()), 11);
        assert_eq!(udeep_values_pq16(&m, null(), 0, 203.0, usize::MAX, codes.as_ptr(), 2, out.as_mut_ptr()), 11);
        let (mut data, mut length) = (null_mut::<u8>(), 0usize);
        assert_eq!(udeep_description(null(), &mut data, &mut length), NULL);
        assert_eq!(udeep_caption(null(), &mut data, &mut length), NULL);
        udeep_bytes_free(null_mut(), 0);
        udeep_bytes_free(null_mut(), 99);
    }
}

/// Sizes whose product does not fit a `size_t` are refused before anything is read or allocated.
#[test]
fn sizes_whose_product_overflows_are_refused() {
    let one = [0.5f64; 3];
    let bytes = [0u8; 12];
    let mut digest = [0u8; 72];
    let mut out = [0u8; 12];
    let big = usize::MAX / 2 + 1;
    unsafe {
        assert_ne!(udeep_digest_pixels(big, 3, bytes.as_ptr(), digest.as_mut_ptr()), OK);
        assert_ne!(udeep_digest_data([big, 4].as_ptr(), 2, one.as_ptr(), digest.as_mut_ptr()), OK);
        assert_ne!(udeep_mask_isolated(bytes.as_ptr(), big, 4, out.as_mut_ptr()), OK);
        let mut values = [0.0f64; 4];
        let m = grey();
        let codes = [0u16; 12];
        assert_eq!(udeep_values_pq16(&m, null(), 0, 203.0, 16, codes.as_ptr(), big, values.as_mut_ptr()), 8);
    }
}

/// A colour-map table asked of the generator: any name, version, headroom and node count a host might pass comes
/// back as a code, and a table that is made is finite and inside its segment's range.
#[test]
fn any_table_asked_of_the_generator_comes_back_as_a_code_or_a_table() {
    let mut s = Stream(11);
    let mut table = [0.0f32; 18];
    let names: [&[u8]; 5] = [b"fire", b"ice", b"grey", b"\xff\xfe", b""];
    unsafe {
        for &nodes in &[0usize, 1, usize::MAX, usize::MAX / 6 + 1, 1 << 17] {
            assert_eq!(udeep_colour_table(b"fire".as_ptr(), 4, 3, 3.0, nodes, table.as_mut_ptr()), 7, "{nodes}");
        }
        for _ in 0..if cfg!(miri) { 6 } else { 200 } {
            let name = names[s.below(5) as usize];
            let (version, headroom) = (s.below(5) as u32, s.number());
            let status = udeep_colour_table(name.as_ptr(), name.len(), version, headroom, 3, table.as_mut_ptr());
            assert!([OK, 5, 7, 10].contains(&status), "{status}");
            if status == OK {
                assert!(table[..9].iter().all(|&v| (0.0..=1.0).contains(&v)), "{table:?}");
                assert!(table[9..].iter().all(|&v| v >= 0.0 && f64::from(v) <= headroom * (1.0 + 1e-6)), "{table:?}");
            }
        }
    }
}

/// Text that is not UTF-8, a required text left out, and numbers that mean nothing: refused as declarations.
#[test]
fn declarations_with_bad_text_or_numbers_are_refused() {
    let text = |s: &'static [u8]| Text { data: s.as_ptr(), length: s.len() };
    let none = Text { data: null(), length: 0 };
    let good = || CDeclaration {
        mapping: grey(),
        max_data_step: 0.4,
        noise_parameter: f64::NAN,
        rounding_to_noise: f64::NAN,
        masked: 0,
        derived_masked: 0,
        data_unit: text(b"K"),
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
    };
    let (mut data, mut length) = (null_mut::<u8>(), 0usize);
    unsafe {
        assert_eq!(udeep_description(&good(), &mut data, &mut length), OK);
        udeep_bytes_free(data, length);
        let mut s = Stream(7);
        for _ in 0..rounds(3_000, 30) {
            let mut d = good();
            match s.below(9) {
                0 => d.data_unit = text(b"\xff\xfe"),
                1 => d.knee_rule = none,
                2 => d.noise_model = text(b"gaussian"),
                3 => d.max_data_step = s.number(),
                4 => d.noise_parameter = s.number(),
                5 => d.rounding_to_noise = s.number(),
                6 => d.mapping = random_mapping(&mut s),
                7 => d.derived_masked = s.next(),
                _ => d.colour_map_table = text(b"not a table \x80"),
            }
            for f in [udeep_description, udeep_caption] {
                let (mut data, mut length) = (null_mut::<u8>(), 0usize);
                let code = f(&d, &mut data, &mut length);
                assert!((0..=11).contains(&code));
                if code == OK {
                    assert!(std::str::from_utf8(std::slice::from_raw_parts(data, length)).is_ok());
                    udeep_bytes_free(data, length);
                } else {
                    assert!(data.is_null(), "nothing is handed back with an error");
                }
            }
        }
    }
}

/// The functions documented as taking the same buffer in and out give what separate buffers give.
#[test]
fn the_same_buffer_in_and_out_gives_what_separate_ones_give() {
    let m = grey();
    let mut s = Stream(3);
    let x: Vec<f64> = (0..rounds(50_000, 200)).map(|_| s.number()).collect();
    type Each = unsafe extern "C" fn(*const Mapping, *const f64, *mut f64, usize) -> i32;
    let each: [Each; 5] = [udeep_position, udeep_light, udeep_sdr_light, udeep_value, udeep_value_at];
    for f in each {
        let mut apart = vec![0.0; x.len()];
        let mut inside = x.clone();
        unsafe {
            assert_eq!(f(&m, x.as_ptr(), apart.as_mut_ptr(), x.len()), OK);
            assert_eq!(f(&m, inside.as_ptr(), inside.as_mut_ptr(), x.len()), OK);
        }
        assert!(apart.iter().zip(&inside).all(|(p, q)| p.to_bits() == q.to_bits()));
    }
    let mut inside = x.clone();
    let mut apart = vec![0.0; x.len()];
    unsafe {
        assert_eq!(udeep_light_parallel(&m, x.as_ptr(), apart.as_mut_ptr(), x.len(), 8), OK);
        assert_eq!(udeep_light_parallel(&m, inside.as_ptr(), inside.as_mut_ptr(), x.len(), 8), OK);
    }
    assert!(apart.iter().zip(&inside).all(|(p, q)| p.to_bits() == q.to_bits()));
}

/// "Every function may be called from several threads at once": many threads, each with its own buffers, all
/// get what one thread alone gets.
#[test]
fn calls_from_many_threads_at_once_agree_with_one_thread() {
    let m = grey();
    let x: Vec<f64> = (0..rounds(20_000, 50)).map(|i| -200.0 + i as f64 * 0.05).collect();
    let mut alone = vec![0.0; x.len()];
    unsafe { assert_eq!(udeep_light(&m, x.as_ptr(), alone.as_mut_ptr(), x.len()), OK) };
    let d_text = |s: &'static [u8]| Text { data: s.as_ptr(), length: s.len() };
    std::thread::scope(|scope| {
        for t in 0..rounds(16, 3) {
            let (x, alone) = (&x, &alone);
            scope.spawn(move || {
                for _ in 0..rounds(20, 2) {
                    let mut out = vec![0.0; x.len()];
                    unsafe {
                        assert_eq!(udeep_light_parallel(&m, x.as_ptr(), out.as_mut_ptr(), x.len(), t % 4), OK);
                    }
                    assert!(out.iter().zip(alone).all(|(p, q)| p.to_bits() == q.to_bits()));
                    let none = Text { data: null(), length: 0 };
                    let d = CDeclaration {
                        mapping: m,
                        max_data_step: 0.4,
                        noise_parameter: f64::NAN,
                        rounding_to_noise: f64::NAN,
                        masked: 0,
                        derived_masked: 0,
                        data_unit: d_text(b"K"),
                        knee_rule: d_text(b"preset"),
                        noise_model: d_text(b"unknown"),
                        record: d_text(b"rendering"),
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
                    };
                    let (mut data, mut length) = (null_mut::<u8>(), 0usize);
                    unsafe {
                        assert_eq!(udeep_caption(&d, &mut data, &mut length), OK);
                        udeep_bytes_free(data, length);
                    }
                }
            });
        }
    });
}

/// The image writers and readers with sizes that do not match, zero sizes, non-finite values and damaged bytes.
#[cfg(all(feature = "jpeg", feature = "png"))]
#[test]
#[cfg_attr(miri, ignore = "the codecs are far too slow under Miri")]
fn the_image_functions_refuse_what_they_cannot_take() {
    let (w, h) = (16usize, 12usize);
    let sdr: Vec<f64> = (0..w * h * 3).map(|i| (i % 97) as f64 / 97.0).collect();
    let hdr: Vec<f64> = sdr.iter().map(|v| v * 3.0).collect();
    let (mut data, mut length) = (null_mut::<u8>(), 0usize);
    unsafe {
        // sizes: zero, and a product that overflows
        for (width, height) in [(0, h), (w, 0), (usize::MAX, 2), (2, usize::MAX / 2)] {
            assert_ne!(
                udeep_gainmap_jpeg(
                    sdr.as_ptr(),
                    hdr.as_ptr(),
                    width,
                    height,
                    4.0,
                    95,
                    100,
                    null(),
                    0,
                    &mut data,
                    &mut length
                ),
                OK
            );
            assert_ne!(udeep_png_pq(sdr.as_ptr(), width, height, 203.0, null(), 0, &mut data, &mut length), OK);
            let render = [0u8; 4];
            let mut lifted = 0usize;
            assert_ne!(
                udeep_gainmap_from_renders(
                    render.as_ptr(),
                    render.as_ptr(),
                    render.as_ptr(),
                    render.as_ptr(),
                    width,
                    height,
                    4.0,
                    95,
                    100,
                    null(),
                    0,
                    &mut data,
                    &mut length,
                    &mut lifted
                ),
                OK
            );
        }
        // values: NaN is written as 0, an infinity is refused, a headroom or white that means nothing is refused
        let mut nan = hdr.clone();
        nan[5] = f64::NAN;
        assert_eq!(
            udeep_gainmap_jpeg(sdr.as_ptr(), nan.as_ptr(), w, h, 4.0, 95, 100, null(), 0, &mut data, &mut length),
            OK
        );
        udeep_bytes_free(data, length);
        let mut infinite = hdr.clone();
        infinite[5] = f64::INFINITY;
        assert_ne!(
            udeep_gainmap_jpeg(sdr.as_ptr(), infinite.as_ptr(), w, h, 4.0, 95, 100, null(), 0, &mut data, &mut length),
            OK
        );
        for headroom in [f64::NAN, 0.0, 1.0, -4.0, f64::INFINITY] {
            assert_ne!(
                udeep_gainmap_jpeg(
                    sdr.as_ptr(),
                    hdr.as_ptr(),
                    w,
                    h,
                    headroom,
                    95,
                    100,
                    null(),
                    0,
                    &mut data,
                    &mut length
                ),
                OK
            );
        }
        for white in [f64::NAN, 0.0, -203.0, f64::INFINITY] {
            assert_ne!(udeep_png_pq(sdr.as_ptr(), w, h, white, null(), 0, &mut data, &mut length), OK);
        }
        // quality outside 1..100 is clamped, not refused
        for quality in [0u32, 101, u32::MAX] {
            assert_eq!(
                udeep_gainmap_jpeg(
                    sdr.as_ptr(),
                    hdr.as_ptr(),
                    w,
                    h,
                    4.0,
                    quality,
                    100,
                    null(),
                    0,
                    &mut data,
                    &mut length
                ),
                OK
            );
            udeep_bytes_free(data, length);
        }
        // description text that is not UTF-8
        let bad = b"<rdf:Description \xff/>";
        assert_ne!(
            udeep_gainmap_jpeg(
                sdr.as_ptr(),
                hdr.as_ptr(),
                w,
                h,
                4.0,
                95,
                100,
                bad.as_ptr(),
                bad.len(),
                &mut data,
                &mut length
            ),
            OK
        );

        // reading: the right file with the wrong room, and damaged copies
        assert_eq!(
            udeep_gainmap_jpeg(sdr.as_ptr(), hdr.as_ptr(), w, h, 4.0, 95, 100, null(), 0, &mut data, &mut length),
            OK
        );
        let jpeg = std::slice::from_raw_parts(data, length).to_vec();
        udeep_bytes_free(data, length);
        assert_eq!(udeep_png_pq(hdr.as_ptr(), w, h, 203.0, null(), 0, &mut data, &mut length), OK);
        let png = std::slice::from_raw_parts(data, length).to_vec();
        udeep_bytes_free(data, length);
        let mut floats = vec![0f32; w * h * 3];
        let mut doubles = vec![0f64; w * h * 3];
        assert_eq!(udeep_gainmap_read(jpeg.as_ptr(), jpeg.len(), 4.0, floats.as_mut_ptr(), floats.len(), 4), OK);
        assert_ne!(udeep_gainmap_read(jpeg.as_ptr(), jpeg.len(), 4.0, floats.as_mut_ptr(), floats.len() - 1, 4), OK);
        assert_ne!(udeep_gainmap_read(jpeg.as_ptr(), jpeg.len(), f64::NAN, floats.as_mut_ptr(), floats.len(), 4), OK);
        assert_eq!(udeep_png_pq_read(png.as_ptr(), png.len(), 203.0, doubles.as_mut_ptr(), doubles.len()), OK);
        assert_ne!(udeep_png_pq_read(png.as_ptr(), png.len(), 203.0, doubles.as_mut_ptr(), doubles.len() + 1), OK);
        let m = grey();
        let values = |file: &[u8], room: &mut [f64]| {
            udeep_png_pq_values(file.as_ptr(), file.len(), &m, null(), 0, 203.0, 4096, room.as_mut_ptr(), room.len())
        };
        assert_eq!(values(&png, &mut doubles[..w * h]), OK);
        assert_eq!(values(&png, &mut doubles[..w * h - 1]), 8);
        assert!([8, 9].contains(&values(&jpeg, &mut doubles[..w * h]))); // not a PNG at all
        let mut s = Stream(11);
        for round in 0..2_000 {
            let file = if round % 2 == 0 { &jpeg } else { &png };
            let mut damaged = file.clone();
            for _ in 0..1 + s.below(6) {
                let at = s.below(damaged.len() as u64) as usize;
                damaged[at] = s.next() as u8;
            }
            damaged.truncate(damaged.len() - s.below(damaged.len() as u64 / 4) as usize);
            let (mut width, mut height) = (0usize, 0usize);
            if round % 2 == 0 {
                if udeep_gainmap_size(damaged.as_ptr(), damaged.len(), &mut width, &mut height) == OK {
                    let mut room = vec![0f32; width * height * 3];
                    let code =
                        udeep_gainmap_read(damaged.as_ptr(), damaged.len(), 4.0, room.as_mut_ptr(), room.len(), 0);
                    assert!((0..=11).contains(&code));
                }
            } else if udeep_png_pq_size(damaged.as_ptr(), damaged.len(), &mut width, &mut height) == OK {
                let mut room = vec![0f64; width * height * 3];
                let code = udeep_png_pq_read(damaged.as_ptr(), damaged.len(), 203.0, room.as_mut_ptr(), room.len());
                assert!((0..=11).contains(&code));
                assert!((0..=11).contains(&values(&damaged, &mut room[..width * height])));
            }
        }
    }
}
