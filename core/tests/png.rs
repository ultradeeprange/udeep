//! The 16-bit PQ PNG: what is written is read back to within the file's own steps, every one of PNG's row filters
//! is read, and what is not such a file is refused.
#![cfg(feature = "png")]

use udeep_core::png::{PngError, png_pq_size, pq_decode, read_png_pq, read_png_pq_codes, write_png_pq};
use udeep_core::record::Noise;
use udeep_core::{Mapping, Stretch, levels_pq16, pq_encode, pq16_codes, rounding_to_noise};

const WHITE: f64 = 203.0;

fn picture(width: usize, height: usize) -> Vec<f64> {
    // a deterministic pattern from 0 to 4 x white, with colour, and one NaN
    let mut rgb = Vec::with_capacity(width * height * 3);
    for y in 0..height {
        for x in 0..width {
            let v = 4.0 * ((x * 37 + y * 101) % 997) as f64 / 996.0;
            rgb.extend_from_slice(&[v, v * 0.5 + 0.01 * (x % 7) as f64, (y as f64 / height as f64) * 2.0]);
        }
    }
    rgb[5] = f64::NAN;
    rgb
}

#[test]
fn what_is_written_is_read_back_to_within_a_sixteen_bit_step() {
    let (width, height) = (61, 43);
    let rgb = picture(width, height);
    let file = write_png_pq(&rgb, width, height, WHITE, b"<x:xmpmeta>a packet</x:xmpmeta>").unwrap();
    assert_eq!(&file[..8], b"\x89PNG\r\n\x1a\n");
    assert!(file.windows(4).any(|w| w == b"cICP") && file.windows(17).any(|w| w == b"XML:com.adobe.xmp"));
    assert_eq!(png_pq_size(&file), Ok((width, height)));

    let (codes, w, h) = read_png_pq_codes(&file).unwrap();
    assert_eq!((w, h, codes.len()), (width, height, width * height * 3));
    for (pixel, stored) in rgb.as_chunks::<3>().0.iter().zip(codes.as_chunks::<3>().0) {
        assert_eq!(pq16_codes(*pixel, WHITE), *stored);
    }
    let (back, _, _) = read_png_pq(&file, WHITE).unwrap();
    for (i, (was, is)) in rgb.iter().zip(&back).enumerate() {
        let was = if was.is_nan() { 0.0 } else { *was };
        // a 16-bit PQ step near 800 cd/m^2 is under 0.04 % of the light; the matrix mixes three channels' steps
        assert!((was - is).abs() <= 6e-4 * (1.0 + was), "value {i}: wrote {was}, read {is}");
    }
}

#[test]
fn pq_decode_is_the_inverse_of_pq_encode() {
    for nits in [0.0, 0.005, 1.0, 100.0, 203.0, 812.0, 4000.0, 10000.0] {
        assert!((pq_decode(pq_encode(nits)) - nits).abs() <= 1e-9 * (1.0 + nits), "{nits}");
    }
}

fn crc(parts: &[&[u8]]) -> [u8; 4] {
    let mut crc = 0xffff_ffff_u32;
    for part in parts {
        for &byte in *part {
            crc ^= u32::from(byte);
            for _ in 0..8 {
                crc = if crc & 1 == 1 { (crc >> 1) ^ 0xedb8_8320 } else { crc >> 1 };
            }
        }
    }
    (!crc).to_be_bytes()
}

fn chunk(tag: &[u8; 4], data: &[u8]) -> Vec<u8> {
    [&(data.len() as u32).to_be_bytes()[..], tag, data, &crc(&[tag, data])].concat()
}

/// A PNG of the given pixel bytes with every row stored under one filter, as another program might write it.
fn filtered(pixels: &[u8], width: usize, height: usize, filter: u8, header: [u8; 5], cicp: Option<[u8; 4]>) -> Vec<u8> {
    let (line, bpp) = (width * 6, 6);
    let mut raw = Vec::new();
    for y in 0..height {
        raw.push(filter);
        for i in 0..line {
            let at = |row: usize, col: usize| pixels[row * line + col];
            let left = if i >= bpp { at(y, i - bpp) } else { 0 };
            let up = if y > 0 { at(y - 1, i) } else { 0 };
            let corner = if y > 0 && i >= bpp { at(y - 1, i - bpp) } else { 0 };
            let predicted = match filter {
                0 => 0,
                1 => left,
                2 => up,
                3 => ((u16::from(left) + u16::from(up)) / 2) as u8,
                _ => {
                    let p = i32::from(left) + i32::from(up) - i32::from(corner);
                    let (pa, pb, pc) =
                        ((p - i32::from(left)).abs(), (p - i32::from(up)).abs(), (p - i32::from(corner)).abs());
                    if pa <= pb && pa <= pc {
                        left
                    } else if pb <= pc {
                        up
                    } else {
                        corner
                    }
                }
            };
            raw.push(at(y, i).wrapping_sub(predicted));
        }
    }
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&(width as u32).to_be_bytes());
    ihdr.extend_from_slice(&(height as u32).to_be_bytes());
    ihdr.extend_from_slice(&header);
    let mut out = b"\x89PNG\r\n\x1a\n".to_vec();
    out.extend(chunk(b"IHDR", &ihdr));
    if let Some(c) = cicp {
        out.extend(chunk(b"cICP", &c));
    }
    let packed = miniz_oxide::deflate::compress_to_vec_zlib(&raw, 6);
    let (first, second) = packed.split_at(packed.len() / 2); // the data may come in several chunks
    out.extend(chunk(b"IDAT", first));
    out.extend(chunk(b"IDAT", second));
    out.extend(chunk(b"IEND", &[]));
    out
}

#[test]
fn every_row_filter_is_read() {
    let (width, height) = (23, 17);
    let rgb = picture(width, height);
    let (codes, _, _) = read_png_pq_codes(&write_png_pq(&rgb, width, height, WHITE, &[]).unwrap()).unwrap();
    let pixels: Vec<u8> = codes.iter().flat_map(|c| c.to_be_bytes()).collect();
    for filter in 0..=4 {
        let file = filtered(&pixels, width, height, filter, [16, 2, 0, 0, 0], Some([9, 16, 0, 1]));
        assert_eq!(read_png_pq_codes(&file).unwrap().0, codes, "filter {filter}");
    }
}

#[test]
fn what_is_not_a_sixteen_bit_pq_png_is_refused() {
    let (width, height) = (5, 4);
    let file = write_png_pq(&picture(width, height), width, height, WHITE, &[]).unwrap();
    let pixels = vec![7u8; width * height * 6];
    assert_eq!(read_png_pq_codes(b"not a png at all"), Err(PngError::Corrupt));
    assert_eq!(read_png_pq_codes(&file[..file.len() - 5]), Err(PngError::Corrupt)); // cut short
    let mut damaged = file.clone();
    let middle = damaged.len() / 2;
    damaged[middle] ^= 0x55;
    assert_eq!(read_png_pq_codes(&damaged), Err(PngError::Corrupt)); // a checksum fails
    let eight_bit = filtered(&pixels, width, height, 0, [8, 2, 0, 0, 0], Some([9, 16, 0, 1]));
    assert_eq!(read_png_pq_codes(&eight_bit), Err(PngError::NotPq16));
    let interlaced = filtered(&pixels, width, height, 0, [16, 2, 0, 0, 1], Some([9, 16, 0, 1]));
    assert_eq!(read_png_pq_codes(&interlaced), Err(PngError::NotPq16));
    let undeclared = filtered(&pixels, width, height, 0, [16, 2, 0, 0, 0], None); // 16-bit, but not said to be PQ
    assert_eq!(read_png_pq_codes(&undeclared), Err(PngError::NotPq16));
    let srgb = filtered(&pixels, width, height, 0, [16, 2, 0, 0, 0], Some([1, 13, 0, 1]));
    assert_eq!(read_png_pq_codes(&srgb), Err(PngError::NotPq16));
    assert_eq!(write_png_pq(&[0.0; 5], 2, 1, WHITE, &[]), Err(PngError::Shape));
    assert_eq!(write_png_pq(&[], 0, 3, WHITE, &[]), Err(PngError::Shape));
    assert!(read_png_pq(&file, WHITE).is_ok());
}

#[test]
fn a_sixteen_bit_file_holds_a_twelve_bit_frame_to_within_its_noise() {
    // the worked example's case: counts 0 to 4095, white at 1000 counts, headroom 4, read noise 3 counts
    let mapping = Mapping::new(Stretch::Linear, 0.0, 4095.0, 1000.0, 4.0);
    let fine = levels_pq16(&mapping, None, WHITE, 1 << 20);
    let coarse = udeep_core::levels(&mapping, None, 4.0, 1 << 16);
    assert!(fine.width.len() > 30_000 && coarse.width.len() < 500);
    assert!(fine.max_data_step() < 1.0 && coarse.max_data_step() > 10.0);
    let noise = Noise::Constant(3.0);
    assert!(rounding_to_noise(&fine, noise).meets && !rounding_to_noise(&coarse, noise).meets);
    // every count from 0 to 4095 has a code of its own
    let own: Vec<[u16; 3]> = (0..=4095).map(|c| pq16_codes([mapping.light(f64::from(c)); 3], WHITE)).collect();
    assert!(own.windows(2).all(|pair| pair[0] != pair[1]));
}

#[test]
fn a_declaration_is_written_with_white_at_203_and_no_other() {
    // profile 9.1.1: the file cannot say what its white was, so a file that carries a declaration has it at 203
    let rgb = picture(5, 4);
    let declared = br#"<x:xmpmeta><rdf:Description xmlns:udeep="https://ultradeeprange.github.io/udeep/ns/0.3/"/></x:xmpmeta>"#;
    assert_eq!(write_png_pq(&rgb, 5, 4, 240.0, declared), Err(PngError::White));
    assert!(write_png_pq(&rgb, 5, 4, WHITE, declared).is_ok());
    // without a declaration, any white: a file for one's own screen, read back with the same value
    let mine = write_png_pq(&rgb, 5, 4, 240.0, b"<x:xmpmeta>another packet</x:xmpmeta>").unwrap();
    assert!(read_png_pq(&mine, 240.0).is_ok());
}
