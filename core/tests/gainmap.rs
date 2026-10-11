//! The gain-map JPEG writer keeps a file within the brightest value it was given: the file is read back here with
//! the decoder the writer uses and the standard formula, and must never be over. (That the Python reference reads
//! and validates these files is tested from Python, in `tests/test_udeep_rust.py`.)
#![cfg(feature = "jpeg")]

use std::{fs, path::PathBuf};

use udeep_core::{
    Mapping, Stretch, Table, colours,
    gainmap::{GAIN_PASS_LIMIT, WriteError, gainmap_file, write_gainmap_jpeg},
    srgb_eotf,
};

const OFFSET: f64 = 1.0 / 64.0;

fn table(name: &str, headroom: f64) -> Vec<f32> {
    let file = format!("{name}-1-h{headroom}.f32");
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("src").join("udeep").join("tables").join(file);
    fs::read(path).expect("a shipped table").as_chunks::<4>().0.iter().map(|b| f32::from_le_bytes(*b)).collect()
}

/// Noisy data below the knee with flat blocks at the very top of the range and hard edges: the case that read up
/// to 9 % over its headroom with the fire and ice maps. The second block runs to the right and bottom edges, so
/// with sides that are not multiples of 8, JPEG blocks that run past the image are in play. `top` is the value of
/// the blocks; one below the knee (50) gives a figure that lifts nothing.
fn scene(map: &str, headroom: f64, width: usize, height: usize, top: f64) -> (Vec<f64>, Vec<f64>) {
    let m = Mapping::new(Stretch::Linear, 0.0, 100.0, 50.0, headroom);
    let values = table(map, headroom);
    let lookup = Table::new(&values, headroom).expect("a table");
    let (mut sdr, mut hdr) = (Vec::new(), Vec::new());
    let mut state = 12345u64; // a small generator: the same noise on every run
    for row in 0..height {
        for column in 0..width {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            let noise = (state >> 40) as f64 / (1u64 << 24) as f64;
            let block = (row >= height / 4 && row < height / 2 && column >= width / 3 && column < 2 * width / 3)
                || (row >= height - 11 && column >= width - 19);
            let x = if block { top } else { 10.0 + 35.0 * noise };
            sdr.extend(colours(m.sdr_light(x), Some(&lookup)));
            hdr.extend(colours(m.light(x), Some(&lookup)));
        }
    }
    (sdr, hdr)
}

fn attribute(xmp: &[u8], name: &str) -> f64 {
    let text = String::from_utf8_lossy(xmp);
    let key = format!("{name}=\"");
    let at = text.rfind(&key).unwrap_or_else(|| panic!("no {name}")) + key.len();
    text[at..].split('"').next().unwrap().parse().unwrap()
}

/// The figure an HDR reader shows at the file's own headroom, by the standard formula, and the stated minimum.
fn read(file: &[u8]) -> (Vec<f64>, f64) {
    let text = String::from_utf8_lossy(file);
    let key = "Item:Semantic=\"GainMap\" Item:Mime=\"image/jpeg\" Item:Length=\"";
    let at = text.find(key).expect("the container directory") + key.len();
    let length: usize = text[at..].split('"').next().unwrap().parse().unwrap();
    let (primary, gain) = file.split_at(file.len() - length);
    let decode = |jpeg: &[u8]| jpeg_decoder::Decoder::new(jpeg).decode().expect("a JPEG");
    let (low, high) = (attribute(gain, "hdrgm:GainMapMin"), attribute(gain, "hdrgm:GainMapMax"));
    let shown = decode(primary)
        .iter()
        .zip(decode(gain))
        .map(|(&base, code)| {
            let gain = low + (high - low) * f64::from(code) / 255.0;
            (srgb_eotf(f64::from(base) / 255.0) + OFFSET) * gain.exp2() - OFFSET
        })
        .collect();
    (shown, low)
}

fn largest(values: &[f64]) -> f64 {
    values.iter().copied().fold(f64::NEG_INFINITY, f64::max)
}

#[test]
fn a_flat_block_at_the_top_of_a_coloured_map_reads_within_its_peak() {
    let mut corrected = 0;
    for map in ["fire", "ice"] {
        for headroom in [4.0, 8.0] {
            let (width, height) = (99, 72);
            let (sdr, hdr) = scene(map, headroom, width, height, 100.0);
            let peak = largest(&hdr);
            assert!(peak > 0.98 * headroom);
            let (file, passes) =
                gainmap_file(&sdr, &hdr, width, height, headroom, 95, 100, "", false, GAIN_PASS_LIMIT).unwrap();
            let (shown, low) = read(&file);
            assert!(largest(&shown) <= peak * (1.0 + 1e-9), "{map} {headroom}: {} over {peak}", largest(&shown));
            assert_eq!(low, 0.0);
            assert!(passes <= GAIN_PASS_LIMIT);
            corrected += u32::from(passes > 0);
            // held under the top, not thrown away: the middle of the block is still near the top
            let middle = ((height * 3 / 8) * width + width / 2) * 3;
            assert!(largest(&shown[middle..middle + 3]) > 0.9 * peak, "{map} {headroom}");
        }
    }
    assert!(corrected >= 2, "the correction was not needed in these cases, so they do not test it");
}

#[test]
fn correcting_only_the_blocks_that_are_over_does_what_coding_the_whole_map_does() {
    // Both ways must hold the peak, everywhere. On x86 with SSSE3, where the small image is laid out to meet the
    // decoder's code as the whole image does, they must also give the same bytes in the same number of passes.
    let exact = cfg!(target_arch = "x86_64") && std::is_x86_feature_detected!("ssse3");
    let mut passes_seen = 0;
    for (map, headroom) in [("ice", 8.0), ("fire", 4.0)] {
        for (width, height) in [(99, 72), (51, 40), (64, 64), (21, 13), (16, 16), (15, 9), (200, 120), (8, 40), (33, 8)]
        {
            let (sdr, hdr) = scene(map, headroom, width, height, 100.0);
            let peak = largest(&hdr);
            let part = gainmap_file(&sdr, &hdr, width, height, headroom, 95, 100, "", false, GAIN_PASS_LIMIT).unwrap();
            let whole = gainmap_file(&sdr, &hdr, width, height, headroom, 95, 100, "", true, GAIN_PASS_LIMIT).unwrap();
            let (fast, plain) = (read(&part.0).0, read(&whole.0).0);
            assert!(
                largest(&fast) <= peak * (1.0 + 1e-9) && largest(&plain) <= peak * (1.0 + 1e-9),
                "{map} {width} x {height}"
            );
            if exact {
                assert_eq!(part.1, whole.1, "{map} {width} x {height}: passes");
                assert!(part.0 == whole.0, "{map} {width} x {height}: the two files differ");
            } else {
                let mean = |v: &[f64]| v.iter().sum::<f64>() / v.len() as f64;
                assert!((mean(&fast) / mean(&plain) - 1.0).abs() < 0.015, "{map} {width} x {height}");
            }
            passes_seen += part.1.min(whole.1);
        }
    }
    assert!(passes_seen > 20, "too few passes were needed for the two ways to have been compared: {passes_seen}");
}

#[test]
fn a_figure_that_lifts_nothing_takes_no_pass() {
    let (sdr, hdr) = scene("fire", 4.0, 99, 72, 40.0); // the blocks are below the knee
    assert_eq!(sdr, hdr);
    let (file, passes) = gainmap_file(&sdr, &hdr, 99, 72, 4.0, 95, 100, "", false, GAIN_PASS_LIMIT).unwrap();
    assert_eq!(passes, 0);
    assert!(String::from_utf8_lossy(&file).contains("hdrgm:GainMapMin=\"0\""));
    let (shown, _) = read(&file);
    assert!(largest(&shown) <= 1.0 + 1e-9);
}

#[test]
fn pixels_the_figure_does_not_lift_are_stored_with_no_gain() {
    let (width, height) = (96, 72);
    let (sdr, hdr) = scene("fire", 4.0, width, height, 100.0);
    let file = write_gainmap_jpeg(&sdr, &hdr, width, height, 4.0, 95, 100, "").unwrap();
    let (shown, _) = read(&file);
    // the top rows hold no lifted pixel and no JPEG block that holds one: there the HDR figure is the SDR base
    let base = {
        let text = String::from_utf8_lossy(&file);
        let key = "Item:Length=\"";
        let at = text.find(key).unwrap() + key.len();
        let length: usize = text[at..].split('"').next().unwrap().parse().unwrap();
        jpeg_decoder::Decoder::new(&file[..file.len() - length]).decode().unwrap()
    };
    for i in 0..(8 * width * 3) {
        let stored = srgb_eotf(f64::from(base[i]) / 255.0);
        assert!((shown[i] - stored).abs() < 1e-12, "sample {i}: {} against {stored}", shown[i]);
    }
    assert!(largest(&shown) > 3.0); // and the block is lifted
}

#[test]
fn a_file_is_within_its_peak_or_it_is_refused() {
    let (width, height) = (99, 72);
    let (sdr, hdr) = scene("ice", 8.0, width, height, 100.0);
    let peak = largest(&hdr);
    let (mut refused, mut written) = (0, 0);
    for gain_quality in [100, 95, 90, 80, 60, 40, 20] {
        match write_gainmap_jpeg(&sdr, &hdr, width, height, 8.0, 95, gain_quality, "") {
            Ok(file) => {
                written += 1;
                assert!(largest(&read(&file).0) <= peak.max(8.0) * (1.0 + 1e-9), "gain quality {gain_quality}");
            }
            Err(WriteError::Headroom(why)) => {
                refused += 1;
                assert!(why.contains("gain quality 100"), "{why}");
            }
            Err(other) => panic!("gain quality {gain_quality}: {other:?}"),
        }
    }
    assert!(written >= 1, "quality 100 must be written");
    assert!(refused >= 1, "no quality was refused, so the refusal is not tested: {written} written");
}

#[test]
fn a_gain_map_that_needs_more_passes_than_allowed_is_refused() {
    let (width, height) = (99, 72);
    let (sdr, hdr) = scene("ice", 8.0, width, height, 100.0);
    for whole_image in [false, true] {
        let needed = gainmap_file(&sdr, &hdr, width, height, 8.0, 95, 100, "", whole_image, GAIN_PASS_LIMIT).unwrap().1;
        assert!(needed >= 2, "this case must need passes: {needed}");
        match gainmap_file(&sdr, &hdr, width, height, 8.0, 95, 100, "", whole_image, needed - 1) {
            Err(WriteError::Headroom(why)) => assert!(why.contains("still reads above"), "{why}"),
            other => panic!("one pass too few was not refused: {:?}", other.map(|(_, passes)| passes)),
        }
        assert_eq!(gainmap_file(&sdr, &hdr, width, height, 8.0, 95, 100, "", whole_image, needed).unwrap().1, needed);
    }
}
