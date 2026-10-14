//! A 16-bit PQ PNG: the form in which a figure can hold its data to within the data's own uncertainty (profile
//! 7.3), where an 8-bit gain-map JPEG cannot.
//!
//! The file is the one the Python reference writes (`udeep.export.write_png_pq`): 16 bits a channel, RGB, BT.2020
//! primaries, the PQ transfer function, full range, declared in a `cICP` chunk; `1.0` of the figure's light is
//! written at `white_nits`. The rows are stored unfiltered. The compressed bytes are this crate's and not zlib's,
//! so two writers' files differ as bytes and hold the same codes.
//!
//! Behind the `png` feature, for the one crate it needs (`miniz_oxide`, for the compression PNG requires).

use miniz_oxide::deflate::compress_to_vec_zlib;
use miniz_oxide::inflate::decompress_to_vec_zlib_with_limit;

use crate::{SRGB_TO_BT2020, matrix3, pq16_codes};

/// The `cICP` chunk: BT.2020 primaries (9), PQ (16), RGB (0), full range (1).
pub const CICP_BT2020_PQ_FULL: [u8; 4] = [9, 16, 0, 1];
const SIGNATURE: [u8; 8] = [0x89, b'P', b'N', b'G', b'\r', b'\n', 0x1a, b'\n'];
const XMP_KEY: &[u8] = b"XML:com.adobe.xmp\0\0\0\0\0";
const COMPRESSION: u8 = 6;
/// More than deflate can compress by (its limit is 1032 to 1): the bound on how large an image a file can hold.
const DEFLATE_MOST: u128 = 1040;

/// What can go wrong writing or reading one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PngError {
    /// The buffer is not `width * height * 3` values, or a dimension is zero or too large for a PNG.
    Shape,
    /// The file is not a PNG, or is cut short, or a chunk's checksum is wrong.
    Corrupt,
    /// A PNG, but not the kind this module reads: 16 bits a channel, RGB, not interlaced, declared BT.2020 PQ.
    NotPq16,
}

fn inverse(m: &[[f64; 3]; 3]) -> [[f64; 3]; 3] {
    let cofactor = |r: usize, c: usize| {
        let (r1, r2, c1, c2) = ((r + 1) % 3, (r + 2) % 3, (c + 1) % 3, (c + 2) % 3);
        m[r1][c1] * m[r2][c2] - m[r1][c2] * m[r2][c1]
    };
    let determinant = m[0][0] * cofactor(0, 0) + m[0][1] * cofactor(0, 1) + m[0][2] * cofactor(0, 2);
    let mut out = [[0.0; 3]; 3];
    for (r, row) in out.iter_mut().enumerate() {
        for (c, value) in row.iter_mut().enumerate() {
            *value = cofactor(c, r) / determinant;
        }
    }
    out
}

/// PQ signal in `[0, 1]` -> luminance in cd/m^2 (SMPTE ST 2084), the inverse of [`pq_encode`].
pub fn pq_decode(signal: f64) -> f64 {
    const M1: f64 = 2610.0 / 16384.0;
    const M2: f64 = 2523.0 / 4096.0 * 128.0;
    const C1: f64 = 3424.0 / 4096.0;
    const C2: f64 = 2413.0 / 4096.0 * 32.0;
    const C3: f64 = 2392.0 / 4096.0 * 32.0;
    let p = signal.powf(1.0 / M2);
    10000.0 * ((p - C1).max(0.0) / (C2 - C3 * p)).powf(1.0 / M1)
}

fn crc32(parts: &[&[u8]]) -> u32 {
    let mut crc = 0xffff_ffff_u32;
    for part in parts {
        for &byte in *part {
            crc ^= u32::from(byte);
            for _ in 0..8 {
                crc = if crc & 1 == 1 { (crc >> 1) ^ 0xedb8_8320 } else { crc >> 1 };
            }
        }
    }
    !crc
}

fn chunk(out: &mut Vec<u8>, tag: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(tag);
    out.extend_from_slice(data);
    out.extend_from_slice(&crc32(&[tag, data]).to_be_bytes());
}

/// Write `rgb` (`height * width * 3`, row by row; linear, BT.709 primaries, multiples of SDR white) as a 16-bit
/// PQ PNG with `1.0` at `white_nits`. `xmp` is an XMP packet to carry (the declaration), or empty for none.
pub fn write_png_pq(
    rgb: &[f64],
    width: usize,
    height: usize,
    white_nits: f64,
    xmp: &[u8],
) -> Result<Vec<u8>, PngError> {
    let count = width.checked_mul(height).and_then(|n| n.checked_mul(3)).ok_or(PngError::Shape)?;
    if width == 0 || height == 0 || rgb.len() != count || width > i32::MAX as usize || height > i32::MAX as usize {
        return Err(PngError::Shape);
    }
    let row = 1 + width * 6;
    let mut raw = vec![0u8; height * row];
    for (y, line) in raw.chunks_exact_mut(row).enumerate() {
        for (x, pixel) in line[1..].as_chunks_mut::<6>().0.iter_mut().enumerate() {
            let at = (y * width + x) * 3;
            for (channel, code) in pq16_codes([rgb[at], rgb[at + 1], rgb[at + 2]], white_nits).into_iter().enumerate() {
                pixel[2 * channel..2 * channel + 2].copy_from_slice(&code.to_be_bytes());
            }
        }
    }
    let mut out = SIGNATURE.to_vec();
    let mut header = Vec::with_capacity(13);
    header.extend_from_slice(&(width as u32).to_be_bytes());
    header.extend_from_slice(&(height as u32).to_be_bytes());
    header.extend_from_slice(&[16, 2, 0, 0, 0]);
    chunk(&mut out, b"IHDR", &header);
    chunk(&mut out, b"cICP", &CICP_BT2020_PQ_FULL);
    if !xmp.is_empty() {
        chunk(&mut out, b"iTXt", &[XMP_KEY, xmp].concat());
    }
    chunk(&mut out, b"IDAT", &compress_to_vec_zlib(&raw, COMPRESSION));
    chunk(&mut out, b"IEND", &[]);
    Ok(out)
}

struct Parsed {
    width: usize,
    height: usize,
    data: Vec<u8>,
}

fn parse(file: &[u8]) -> Result<Parsed, PngError> {
    if file.len() < 8 || file[..8] != SIGNATURE {
        return Err(PngError::Corrupt);
    }
    let (mut at, mut size, mut pq, mut data, mut ended) = (8, None, false, Vec::new(), false);
    while at + 12 <= file.len() && !ended {
        let length = u32::from_be_bytes(file[at..at + 4].try_into().unwrap()) as usize;
        let end = at.checked_add(12).and_then(|n| n.checked_add(length)).ok_or(PngError::Corrupt)?;
        if end > file.len() {
            return Err(PngError::Corrupt);
        }
        let (tag, body) = (&file[at + 4..at + 8], &file[at + 8..at + 8 + length]);
        if crc32(&[tag, body]).to_be_bytes() != file[end - 4..end] {
            return Err(PngError::Corrupt);
        }
        match tag {
            b"IHDR" => {
                if length != 13 {
                    return Err(PngError::Corrupt);
                }
                if body[8..13] != [16, 2, 0, 0, 0] {
                    return Err(PngError::NotPq16);
                }
                let side = |b: &[u8]| u32::from_be_bytes(b.try_into().unwrap()) as usize;
                size = Some((side(&body[0..4]), side(&body[4..8])));
            }
            b"cICP" => pq = body == CICP_BT2020_PQ_FULL,
            b"IDAT" => data.extend_from_slice(body),
            b"IEND" => ended = true,
            _ => {}
        }
        at = end;
    }
    let (width, height) = size.ok_or(PngError::Corrupt)?;
    if !ended || width == 0 || height == 0 {
        return Err(PngError::Corrupt);
    }
    if !pq {
        return Err(PngError::NotPq16);
    }
    // A header may claim any size. The compression PNG uses cannot shrink data more than about 1032 times, so an
    // image larger than that many times its data is not in the file: say so now, before anyone makes room for it.
    let claimed = (width as u128 * 6 + 1) * height as u128;
    if claimed > data.len() as u128 * DEFLATE_MOST + 64 {
        return Err(PngError::Corrupt);
    }
    Ok(Parsed { width, height, data })
}

/// The width and height of a 16-bit PQ PNG, without decoding it.
pub fn png_pq_size(file: &[u8]) -> Result<(usize, usize), PngError> {
    parse(file).map(|p| (p.width, p.height))
}

fn paeth(a: u8, b: u8, c: u8) -> u8 {
    let (ia, ib, ic) = (i32::from(a), i32::from(b), i32::from(c));
    let p = ia + ib - ic;
    let (pa, pb, pc) = ((p - ia).abs(), (p - ib).abs(), (p - ic).abs());
    if pa <= pb && pa <= pc {
        a
    } else if pb <= pc {
        b
    } else {
        c
    }
}

/// The 16-bit codes of a 16-bit PQ PNG, `height * width * 3`, row by row; with its width and height. Any of
/// PNG's row filters is read, so that a file written by another program can be.
pub fn read_png_pq_codes(file: &[u8]) -> Result<(Vec<u16>, usize, usize), PngError> {
    const BPP: usize = 6;
    let parsed = parse(file)?;
    let (width, height) = (parsed.width, parsed.height);
    let line = width.checked_mul(BPP).ok_or(PngError::Shape)?;
    let total = line.checked_add(1).and_then(|n| n.checked_mul(height)).ok_or(PngError::Shape)?;
    let raw = decompress_to_vec_zlib_with_limit(&parsed.data, total).map_err(|_| PngError::Corrupt)?;
    if raw.len() != total {
        return Err(PngError::Corrupt);
    }
    let mut pixels = vec![0u8; line * height];
    for y in 0..height {
        let (filter, source) = (raw[y * (line + 1)], &raw[y * (line + 1) + 1..(y + 1) * (line + 1)]);
        let (above, rest) = pixels.split_at_mut(y * line);
        let (previous, row) = (if y == 0 { None } else { Some(&above[(y - 1) * line..]) }, &mut rest[..line]);
        for i in 0..line {
            let left = if i >= BPP { row[i - BPP] } else { 0 };
            let up = previous.map_or(0, |p| p[i]);
            let corner = if i >= BPP { previous.map_or(0, |p| p[i - BPP]) } else { 0 };
            let predicted = match filter {
                0 => 0,
                1 => left,
                2 => up,
                3 => ((u16::from(left) + u16::from(up)) / 2) as u8,
                4 => paeth(left, up, corner),
                _ => return Err(PngError::Corrupt),
            };
            row[i] = source[i].wrapping_add(predicted);
        }
    }
    let codes = pixels.as_chunks::<2>().0.iter().map(|pair| u16::from_be_bytes(*pair)).collect();
    Ok((codes, width, height))
}

/// A 16-bit PQ PNG as light: linear RGB (BT.709 primaries, multiples of the white written at `white_nits`),
/// `height * width * 3`, row by row; with its width and height. It is what [`write_png_pq`] was given, to within
/// the file's 16-bit steps.
pub fn read_png_pq(file: &[u8], white_nits: f64) -> Result<(Vec<f64>, usize, usize), PngError> {
    let (codes, width, height) = read_png_pq_codes(file)?;
    let back = inverse(&SRGB_TO_BT2020);
    let mut rgb = Vec::with_capacity(codes.len());
    for pixel in codes.as_chunks::<3>().0 {
        let wide = [0, 1, 2].map(|i| pq_decode(f64::from(pixel[i]) / 65535.0) / white_nits);
        rgb.extend_from_slice(&matrix3(&back, wide));
    }
    Ok((rgb, width, height))
}
