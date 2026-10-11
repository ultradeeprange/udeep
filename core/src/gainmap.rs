//! A gain-map JPEG writer (Ultra HDR layout, with the ISO 21496-1 block beside the XMP), behind the `jpeg`
//! feature: it is the one part of the core that needs other crates, an encoder and a decoder for JPEG.
//!
//! The file is the ordinary SDR figure followed by a second JPEG, the gain map, which says how much brighter each
//! pixel is in the HDR figure. As in the reference writer: the gain is computed against the base image **as
//! decoded**, so that JPEG error in the base is not multiplied by the gain; the gain map is full resolution and
//! three-channel; both JPEGs are 4:4:4.
//!
//! A pixel the figure does not lift is stored with a gain of exactly 1, so the smallest gain is the constant 0.
//! And before the file is written its gain map is decoded again: where the figure a reader would show is brighter
//! than the brightest value given, the gain is lowered and the map coded again, until nothing is over
//! ([`gain_within_peak`]). A file that reads above its own peak is never written.

use jpeg_encoder::{ColorType, Encoder, SamplingFactor};

use crate::{format_g, iso21496_block, par, srgb_eotf, srgb_oetf};

const OFFSET: f64 = 1.0 / 64.0;
const XMP_NS: &[u8] = b"http://ns.adobe.com/xap/1.0/\0";
const ISO_NS: &[u8] = b"urn:iso:std:iso:ts:21496:-1\0";
/// The least `GainMapMax`: a figure that lifts nothing still needs a range.
const GAIN_SPAN_MIN: f64 = 1e-6;
/// Correcting passes before the gain map is refused.
pub const GAIN_PASS_LIMIT: u32 = 64;
/// Extra lowering on a correcting pass, in gain codes, to start with ...
const GAIN_MARGIN: f64 = 0.5;
/// ... and that much more after every this many passes.
const GAIN_MARGIN_EVERY: u32 = 8;
/// How far under the peak, in gain codes, a block corrected on its own must read before it is left: see
/// [`gain_within_peak`].
const BLOCK_GUARD: f64 = 0.25;

/// Why a file could not be written.
#[derive(Debug)]
pub enum WriteError {
    /// The two images are not both `height * width * 3` values, or the size does not fit a JPEG (65,535 a side).
    Shape,
    /// The SDR figure has values outside 0..=1, the HDR figure has an infinite value, or the headroom is not
    /// above 1.
    Range,
    /// A metadata segment would exceed a JPEG segment's 65,533 bytes.
    SegmentTooLarge,
    /// The JPEG encoder or decoder failed.
    Codec(String),
    /// The gain map could not be kept within the headroom at this gain quality (use 100).
    Headroom(String),
}

fn jpeg(rgb: &[u8], width: usize, height: usize, quality: u8) -> Result<Vec<u8>, WriteError> {
    let mut out = Vec::new();
    let mut encoder = Encoder::new(&mut out, quality);
    encoder.set_sampling_factor(SamplingFactor::R_4_4_4);
    encoder.encode(rgb, width as u16, height as u16, ColorType::Rgb).map_err(|e| WriteError::Codec(e.to_string()))?;
    Ok(out)
}

fn decode(jpeg: &[u8], count: usize) -> Result<Vec<u8>, WriteError> {
    let decoded = jpeg_decoder::Decoder::new(jpeg).decode().map_err(|e| WriteError::Codec(e.to_string()))?;
    if decoded.len() != count {
        return Err(WriteError::Codec("an image did not decode to RGB of the same size".into()));
    }
    Ok(decoded)
}

/// log2 gains in `0..=gain_max` as the 8-bit codes the gain map stores, rounded to nearest.
fn gain_codes(log_gain: &[f64], gain_max: f64) -> Vec<u8> {
    let mut codes = vec![0u8; log_gain.len()];
    par::fill(&mut codes, 0, |i| ((log_gain[i] / gain_max).clamp(0.0, 1.0) * 255.0 + 0.5).floor() as u8);
    codes
}

/// The linear light of each 8-bit sRGB code: a decoded image has only these 256 values.
fn linear_of_codes() -> [f64; 256] {
    std::array::from_fn(|code| srgb_eotf(code as f64 / 255.0))
}

/// One correcting pass: the margin to lower by, after counting the pass. Refused at the limit.
fn next_margin(passes: &mut u32, step: f64, gain_quality: u8, pass_limit: u32) -> Result<f64, WriteError> {
    if *passes >= pass_limit {
        return Err(WriteError::Headroom(format!(
            "the gain map could not be kept within the headroom at this gain quality ({gain_quality}): after \
             {passes} passes a lifted pixel still reads above the figure's brightest value. Use gain quality 100"
        )));
    }
    let margin = GAIN_MARGIN * f64::from(1 + *passes / GAIN_MARGIN_EVERY) * step;
    *passes += 1;
    Ok(margin)
}

/// `base` is the base image as decoded, in 8-bit sRGB codes: what the gains are measured against.
///
/// The gain-map JPEG of the log2 gains `log_gain` (`0..=gain_max`) in which no lifted pixel reads above `peak`,
/// the largest value of the HDR figure; and the number of correcting passes it took.
///
/// The gain map's own JPEG moves its codes, by up to 4 at quality 100 where a colour map makes the three channels'
/// gains very different, and each code is 1 to 2 % of light. So the map is decoded as a reader will decode it, the
/// figure the reader would show is rebuilt, and where that is above `peak` the gain is lowered by the excess and a
/// margin; then it is coded again, until nothing is over. A sample already at the smallest gain is never counted:
/// it cannot be lowered, and a figure that lifts nothing takes no pass at all.
///
/// Lowering one pixel moves its neighbours in the same JPEG block, so the brightest value does not fall steadily;
/// the margin therefore grows as the passes go on. The map is refused ([`WriteError::Headroom`]) if
/// `pass_limit` passes are not enough (the writer allows [`GAIN_PASS_LIMIT`]); and if, once the passes are done, a sample that could not be lowered
/// reads above both `peak` and the headroom (at a low gain quality the map's JPEG spreads gain onto unlifted
/// pixels beside lifted ones).
///
/// In a 4:4:4 baseline JPEG a block's decoded values depend on that block alone, to within the codec's rounding.
/// So after the whole map has been coded and decoded, only the blocks holding a sample that is over are coded
/// again, side by side in a small image, and on each pass only those of them that are still over; then the whole
/// map is coded once more and checked, and corrected again if anything is still over. That last check is on the
/// whole map as it will be written, so the result does not rest on the blocks being independent: with this
/// crate's codec a block coded alone was seen to differ from the same block in a whole image by one code in one
/// sample of 768 (random 8-bit noise, quality 100): its decoder converts colour with vector code except at the
/// end of each row. The small image is therefore laid out so that every block meets the code it meets in the
/// whole image; on the processors that rule was read for (x86 with SSSE3) the two ways then give the same bytes,
/// and the whole map passes its check the first time. Where they do not, a block is from then on asked to clear
/// the peak by a quarter of a gain code more each round (`BLOCK_GUARD`), which keeps the passes from running on
/// (49 against 28 in one test image before any of this). `whole_image`
/// codes the whole map on every pass instead: the plain form of the loop, far slower on a large figure, kept so
/// that tests can hold the fast form against it.
#[allow(clippy::too_many_arguments)] // the gains, what they are measured against, the size, and four settings
pub fn gain_within_peak(
    mut log_gain: Vec<f64>,
    base: &[u8],
    width: usize,
    height: usize,
    gain_max: f64,
    peak: f64,
    headroom: f64,
    gain_quality: u8,
    whole_image: bool,
    pass_limit: u32,
) -> Result<(Vec<u8>, u32), WriteError> {
    let count = log_gain.len();
    let mut passes = 0u32;
    let mut file = jpeg(&gain_codes(&log_gain, gain_max), width, height, gain_quality)?;
    if !log_gain.iter().any(|&g| g > 0.0) {
        return Ok((file, passes)); // nothing is lifted: there is nothing to check or to lower
    }
    // the maximum as the metadata states it, which is what a reader uses
    let shown_max: f64 = format_g(gain_max, 8).parse().map_err(|_| WriteError::Range)?;
    // reading above the peak is the same as a decoded log2 gain above this, sample by sample; and the excess, in
    // log2, is the difference. It depends only on the base's 8-bit code, so it is worked out for the 256 codes.
    let ceiling_of: [f64; 256] = linear_of_codes().map(|b| ((peak + OFFSET) / (b + OFFSET)).log2());
    let ceiling = |i: usize| ceiling_of[usize::from(base[i])];
    let step = gain_max / 255.0;
    let mut round = 0u32;
    let unlifted_allowance = ((peak.max(headroom) + OFFSET) / (peak + OFFSET)).log2();
    let shown_of = |code: u8| f64::from(code) / 255.0 * shown_max;
    loop {
        let codes = decode(&file, count)?;
        let mut over = vec![false; count];
        par::fill(&mut over, 0, |i| shown_of(codes[i]) > ceiling(i) && log_gain[i] > 0.0);
        if !over.iter().any(|&o| o) {
            if par::any(count, 0, |i| shown_of(codes[i]) > ceiling(i) + unlifted_allowance) {
                return Err(WriteError::Headroom(format!(
                    "the gain map could not be kept within the headroom at this gain quality ({gain_quality}): a \
                     pixel the figure does not lift reads above the headroom. Use gain quality 100"
                )));
            }
            return Ok((file, passes));
        }
        if whole_image {
            let margin = next_margin(&mut passes, step, gain_quality, pass_limit)?;
            for i in (0..count).filter(|&i| over[i]) {
                log_gain[i] = (log_gain[i] - (shown_of(codes[i]) - ceiling(i)) - margin).max(0.0);
            }
        } else {
            // No guard on the first round: where the small image decodes as the whole one does (see below), a
            // block that clears the peak there clears it in the file. Only if the whole map is over again after
            // that, on a decoder whose rule is another, are blocks asked to clear the peak by a margin.
            let guard = BLOCK_GUARD * f64::from(round) * shown_max / 255.0;
            round += 1;
            // the 8 x 8 blocks that hold a sample that is over
            let (down, across) = (height.div_ceil(8), width.div_ceil(8));
            let mut blocks: Vec<(usize, usize)> = Vec::new();
            for row in 0..down {
                for column in 0..across {
                    let any = (row * 8..(row * 8 + 8).min(height)).any(|y| {
                        (column * 8..(column * 8 + 8).min(width)).any(|x| (0..3).any(|c| over[(y * width + x) * 3 + c]))
                    });
                    if any {
                        blocks.push((row, column));
                    }
                }
            }
            // A block that runs past the right or the bottom edge is filled, by JPEG, with the last column and then
            // the last row repeated. Reading it through these indices fills it the same way, so the small image
            // holds what the whole image's coder will see.
            let at = |(row, column): (usize, usize), i: usize, j: usize, c: usize| {
                ((row * 8 + i).min(height - 1) * width + (column * 8 + j).min(width - 1)) * 3 + c
            };
            let inside =
                |(row, column): (usize, usize), i: usize, j: usize| row * 8 + i < height && column * 8 + j < width;
            let local = |i: usize, j: usize, c: usize| (i * 8 + j) * 3 + c;
            let take = |from: &dyn Fn(usize) -> f64| -> Vec<[f64; 192]> {
                blocks
                    .iter()
                    .map(|&b| {
                        let mut out = [0.0; 192];
                        for i in 0..8 {
                            for j in 0..8 {
                                for c in 0..3 {
                                    out[local(i, j, c)] = from(at(b, i, j, c));
                                }
                            }
                        }
                        out
                    })
                    .collect()
            };
            let mut part = take(&|k| log_gain[k]);
            let mut seen = take(&|k| shown_of(codes[k]));
            let top = take(&|k| ceiling(k));
            let mut live: Vec<usize> = (0..blocks.len()).collect(); // the blocks still over: the others stay as they are
            loop {
                let still = |n: usize, i: usize, j: usize, c: usize| {
                    let k = local(i, j, c);
                    seen[n][k] + guard > top[n][k] && part[n][k] > 0.0 && inside(blocks[n], i, j)
                };
                let is_over = |n: usize| (0..8).any(|i| (0..8).any(|j| (0..3).any(|c| still(n, i, j, c))));
                live.retain(|&n| is_over(n));
                if live.is_empty() {
                    break;
                }
                let margin = next_margin(&mut passes, step, gain_quality, pass_limit)?;
                for &n in &live {
                    let b = blocks[n];
                    for i in 0..8 {
                        for j in 0..8 {
                            for c in 0..3 {
                                let k = local(i, j, c);
                                if seen[n][k] + guard > top[n][k] && part[n][k] > 0.0 && inside(b, i, j) {
                                    part[n][k] = (part[n][k] - (seen[n][k] + guard - top[n][k]) - margin).max(0.0);
                                }
                            }
                        }
                    }
                    // fill the part of the block outside the image again, from the changed pixels at its edge
                    let (last_i, last_j) = ((height - 1 - b.0 * 8).min(7), (width - 1 - b.1 * 8).min(7));
                    for i in 0..8 {
                        for j in 0..8 {
                            for c in 0..3 {
                                part[n][local(i, j, c)] = part[n][local(i.min(last_i), j.min(last_j), c)];
                            }
                        }
                    }
                }
                // The live blocks side by side as one small image, each where the decoder will treat it as it does
                // in the whole image. The decoder converts colour eight pixels at a time with vector code, except
                // the last eight (and any odd ones) of each row, which go through scalar code that rounds a
                // little differently (jpeg-decoder 0.3, arch/ssse3.rs: `num / 8 - 1` vectors a row). So a block
                // from the last full column of the image, or from a part column beyond it, goes in the last place
                // of a row here, and every other block in an earlier place.
                let last_vector_column = (width / 8).saturating_sub(1);
                let (scalar, vector): (Vec<usize>, Vec<usize>) =
                    live.iter().partition(|&&n| blocks[n].1 >= last_vector_column);
                let per_row =
                    if vector.is_empty() { 1 } else { ((live.len() as f64).sqrt().ceil() as usize).max(1) + 1 };
                let rows =
                    if per_row == 1 { scalar.len() } else { vector.len().div_ceil(per_row - 1).max(scalar.len()) };
                let (small_width, small_height) = (per_row * 8, rows * 8);
                let slots: Vec<(usize, usize, usize)> = vector
                    .iter()
                    .enumerate()
                    .map(|(k, &n)| (n, k / (per_row - 1).max(1), k % (per_row - 1).max(1)))
                    .chain(scalar.iter().enumerate().map(|(k, &n)| (n, k, per_row - 1)))
                    .collect();
                let mut small = vec![0u8; small_width * small_height * 3];
                let place = |row: usize, column: usize, i: usize, j: usize, c: usize| {
                    ((row * 8 + i) * small_width + column * 8 + j) * 3 + c
                };
                for &(n, row, column) in &slots {
                    let block_codes = gain_codes(&part[n], gain_max);
                    for i in 0..8 {
                        for j in 0..8 {
                            for c in 0..3 {
                                small[place(row, column, i, j, c)] = block_codes[local(i, j, c)];
                            }
                        }
                    }
                }
                let read = decode(&jpeg(&small, small_width, small_height, gain_quality)?, small.len())?;
                for &(n, row, column) in &slots {
                    for i in 0..8 {
                        for j in 0..8 {
                            for c in 0..3 {
                                seen[n][local(i, j, c)] = shown_of(read[place(row, column, i, j, c)]);
                            }
                        }
                    }
                }
            }
            for (n, &b) in blocks.iter().enumerate() {
                for i in 0..8 {
                    for j in 0..8 {
                        if inside(b, i, j) {
                            for c in 0..3 {
                                log_gain[at(b, i, j, c)] = part[n][local(i, j, c)];
                            }
                        }
                    }
                }
            }
        }
        file = jpeg(&gain_codes(&log_gain, gain_max), width, height, gain_quality)?;
    }
}

fn segment(marker: u8, payload: &[u8]) -> Result<Vec<u8>, WriteError> {
    if payload.len() + 2 > 0xFFFF {
        return Err(WriteError::SegmentTooLarge);
    }
    let mut out = vec![0xFF, marker];
    out.extend_from_slice(&((payload.len() + 2) as u16).to_be_bytes());
    out.extend_from_slice(payload);
    Ok(out)
}

fn primary_xmp(gain_length: usize, description: &str) -> Vec<u8> {
    let xml = format!(
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" x:xmptk=\"udeep\">\
         <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\
         <rdf:Description rdf:about=\"\" xmlns:Container=\"http://ns.google.com/photos/1.0/container/\" \
         xmlns:Item=\"http://ns.google.com/photos/1.0/container/item/\" \
         xmlns:hdrgm=\"http://ns.adobe.com/hdr-gain-map/1.0/\" hdrgm:Version=\"1.0\">\
         <Container:Directory><rdf:Seq>\
         <rdf:li rdf:parseType=\"Resource\"><Container:Item Item:Semantic=\"Primary\" Item:Mime=\"image/jpeg\"/></rdf:li>\
         <rdf:li rdf:parseType=\"Resource\"><Container:Item Item:Semantic=\"GainMap\" Item:Mime=\"image/jpeg\" \
         Item:Length=\"{gain_length}\"/></rdf:li>\
         </rdf:Seq></Container:Directory></rdf:Description>{description}</rdf:RDF></x:xmpmeta>"
    );
    [XMP_NS, xml.as_bytes()].concat()
}

fn gain_xmp(gain_min: f64, gain_max: f64, capacity_max: f64) -> Vec<u8> {
    let g = |v: f64| format_g(v, 8);
    let xml = format!(
        "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" x:xmptk=\"udeep\">\
         <rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\">\
         <rdf:Description rdf:about=\"\" xmlns:hdrgm=\"http://ns.adobe.com/hdr-gain-map/1.0/\" hdrgm:Version=\"1.0\" \
         hdrgm:GainMapMin=\"{}\" hdrgm:GainMapMax=\"{}\" hdrgm:Gamma=\"1\" hdrgm:OffsetSDR=\"{}\" hdrgm:OffsetHDR=\"{}\" \
         hdrgm:HDRCapacityMin=\"0\" hdrgm:HDRCapacityMax=\"{}\" hdrgm:BaseRenditionIsHDR=\"False\"/></rdf:RDF></x:xmpmeta>",
        g(gain_min),
        g(gain_max),
        g(OFFSET),
        g(OFFSET),
        g(capacity_max)
    );
    [XMP_NS, xml.as_bytes()].concat()
}

/// A CIPA DC-007 Multi-Picture Format index for two images; offsets count from its TIFF header.
fn mpf(primary_length: u32, gain_length: u32, gain_offset: u32) -> Vec<u8> {
    let mut out = b"MPF\0MM\0\x2a".to_vec();
    out.extend_from_slice(&8u32.to_be_bytes());
    let entries_at = 8 + 2 + 3 * 12 + 4;
    out.extend_from_slice(&3u16.to_be_bytes());
    for (tag, kind, count, value) in [
        (0xB000u16, 7u16, 4u32, *b"0100"),
        (0xB001, 4, 1, 2u32.to_be_bytes()),
        (0xB002, 7, 32, (entries_at as u32).to_be_bytes()),
    ] {
        out.extend_from_slice(&tag.to_be_bytes());
        out.extend_from_slice(&kind.to_be_bytes());
        out.extend_from_slice(&count.to_be_bytes());
        out.extend_from_slice(&value);
    }
    out.extend_from_slice(&0u32.to_be_bytes()); // no further directory
    for (attribute, length, offset) in [(0x030000u32, primary_length, 0u32), (0, gain_length, gain_offset)] {
        out.extend_from_slice(&attribute.to_be_bytes());
        out.extend_from_slice(&length.to_be_bytes());
        out.extend_from_slice(&offset.to_be_bytes());
        out.extend_from_slice(&[0; 4]);
    }
    out
}

/// Write a gain-map JPEG to memory.
///
/// `sdr` is the SDR figure as linear RGB in `0..=1`; `hdr` the HDR figure as linear RGB in multiples of SDR white;
/// both `height * width * 3` values, row by row. `description` is an `rdf:Description` element for the primary
/// image's metadata ([`crate::description`]) or empty.
///
/// A NaN in either figure is written as 0, as the reference writes it: it is how a view marks a pixel with no
/// data. An infinite HDR value is refused ([`WriteError::Range`]): its gain has no bound, so the file's gain-map
/// range could not be stated.
///
/// A pixel the figure does not lift (`hdr` no greater than `sdr` in all three channels) is stored with a gain of
/// exactly 1, and no pixel is darkened. The file never reads above the brightest value of `hdr` on the decoder
/// this crate uses; if that cannot be done the file is refused ([`WriteError::Headroom`]). Keep `gain_quality` at
/// 100 unless the figure is grey: below it the gain map is unreliable on coloured maps, with this writer or any
/// other, and the call may be refused.
#[allow(clippy::too_many_arguments)] // two images, their size, and four settings: a struct would only rename them
pub fn write_gainmap_jpeg(
    sdr: &[f64],
    hdr: &[f64],
    width: usize,
    height: usize,
    headroom: f64,
    quality: u8,
    gain_quality: u8,
    description: &str,
) -> Result<Vec<u8>, WriteError> {
    gainmap_file(sdr, hdr, width, height, headroom, quality, gain_quality, description, false, GAIN_PASS_LIMIT)
        .map(|(file, _)| file)
}

/// The bytes [`write_gainmap_jpeg`] gives, and the number of correcting passes the gain map took: 0 when nothing
/// read above the figure's brightest value, and always 0 for a figure that lifts nothing. `whole_image` and
/// `pass_limit` (the writer uses [`GAIN_PASS_LIMIT`]) are for tests: see [`gain_within_peak`].
#[allow(clippy::too_many_arguments)]
pub fn gainmap_file(
    sdr: &[f64],
    hdr: &[f64],
    width: usize,
    height: usize,
    headroom: f64,
    quality: u8,
    gain_quality: u8,
    description: &str,
    whole_image: bool,
    pass_limit: u32,
) -> Result<(Vec<u8>, u32), WriteError> {
    let count = width.checked_mul(height).and_then(|n| n.checked_mul(3)).ok_or(WriteError::Shape)?;
    if count == 0 || sdr.len() != count || hdr.len() != count || width > 0xFFFF || height > 0xFFFF {
        return Err(WriteError::Shape);
    }
    if !(headroom > 1.0) || sdr.iter().any(|&v| v < -1e-6 || v > 1.0 + 1e-6) {
        return Err(WriteError::Range);
    }
    // Everything below that is done once for each sample is done on every processor core: it was half the
    // writer's time on one.
    let clean = |v: f64, top: f64| if v.is_nan() { 0.0 } else { v.clamp(0.0, top) };
    let mut base8 = vec![0u8; count];
    par::fill(&mut base8, 0, |i| (srgb_oetf(clean(sdr[i], 1.0)) * 255.0).round_ties_even() as u8);
    let hdr_of = |i: usize| clean(hdr[i], f64::INFINITY);
    let held = |pixel: usize| (0..3).all(|c| hdr_of(pixel + c) <= clean(sdr[pixel + c], 1.0) + 1e-6);
    file_from_codes(
        base8,
        &hdr_of,
        &held,
        width,
        height,
        headroom,
        quality,
        gain_quality,
        description,
        whole_image,
        pass_limit,
    )
}

/// The file, from the base image as 8-bit sRGB codes, the HDR figure sample by sample (`hdr_of`, linear, already
/// free of NaN and negative values), and which pixels the figure does not lift (`held`, given a pixel's first
/// sample). What [`gainmap_file`] and [`gainmap_from_renders`] share.
#[allow(clippy::too_many_arguments)]
fn file_from_codes(
    base8: Vec<u8>,
    hdr_of: &(impl Fn(usize) -> f64 + Sync),
    held: &(impl Fn(usize) -> bool + Sync),
    width: usize,
    height: usize,
    headroom: f64,
    quality: u8,
    gain_quality: u8,
    description: &str,
    whole_image: bool,
    pass_limit: u32,
) -> Result<(Vec<u8>, u32), WriteError> {
    let count = base8.len();
    let base_jpeg = jpeg(&base8, width, height, quality)?;
    drop(base8);
    // the gain relative to the base AS DECODED
    let base = decode(&base_jpeg, count)?;
    let linear = linear_of_codes();
    // Hold: a pixel the figure does not lift gets a gain of exactly 1, and no pixel is darkened. The smallest gain
    // is then the constant 0 rather than the JPEG error of the base, which used to set the bottom of the 8-bit
    // range and so made every code a larger step; and the unlifted part of the map is flat, which JPEG keeps flat.
    // A gain that is not finite is kept as it is, to be refused below.
    let mut log_gain = vec![0f64; count];
    par::fill(&mut log_gain, 0, |i| {
        let gain = ((hdr_of(i) + OFFSET) / (linear[usize::from(base[i])] + OFFSET)).log2();
        if !gain.is_finite() {
            gain
        } else if held(i - i % 3) {
            0.0
        } else {
            gain.max(0.0)
        }
    });
    // an HDR value that is infinite, or so large that the ratio overflows, has no gain that can be stored
    if par::any(count, 0, |i| !log_gain[i].is_finite()) {
        return Err(WriteError::Range);
    }
    let gain_min = 0.0;
    let gain_max = log_gain.iter().copied().fold(GAIN_SPAN_MIN, f64::max);
    let peak = (0..count).map(hdr_of).fold(0.0, f64::max);
    let (gain_jpeg, passes) = gain_within_peak(
        log_gain,
        &base,
        width,
        height,
        gain_max,
        peak,
        headroom,
        gain_quality,
        whole_image,
        pass_limit,
    )?;

    let capacity_max = headroom.log2();
    let mut gain = gain_jpeg[..2].to_vec();
    gain.extend(segment(0xE1, &gain_xmp(gain_min, gain_max, capacity_max))?);
    gain.extend(segment(0xE2, &[ISO_NS, &iso21496_block(gain_min, gain_max, capacity_max)[..]].concat())?);
    gain.extend_from_slice(&gain_jpeg[2..]);

    let app1 = segment(0xE1, &primary_xmp(gain.len(), description))?;
    let iso = segment(0xE2, &[ISO_NS, &[0u8; 4][..]].concat())?; // the versions only
    let mpf_length = segment(0xE2, &mpf(0, 0, 0))?.len();
    let primary_length = 2 + app1.len() + iso.len() + mpf_length + base_jpeg.len() - 2;
    let tiff_at = 2 + app1.len() + iso.len() + 4 + 4; // SOI, APP1, ISO APP2, the MPF marker and length, "MPF\0"
    let app2 = segment(0xE2, &mpf(primary_length as u32, gain.len() as u32, (primary_length - tiff_at) as u32))?;
    let mut out = base_jpeg[..2].to_vec();
    out.extend(app1);
    out.extend(iso);
    out.extend(app2);
    out.extend_from_slice(&base_jpeg[2..]);
    debug_assert_eq!(out.len(), primary_length);
    out.extend(gain);
    Ok((out, passes))
}

/// Why a file could not be read.
#[derive(Debug)]
pub enum ReadError {
    /// The file is not a gain-map JPEG this reader knows: no container directory, no gain-map metadata, or a JPEG
    /// that does not decode.
    NotAGainMap(String),
    /// A gain-map JPEG, but of a form this reader does not take: a gain map of another size than its base, or
    /// pixels that are not 8-bit RGB or grey. (The Python reference reads these.)
    Unsupported(String),
}

fn find(haystack: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    haystack.get(from..)?.windows(needle.len()).position(|w| w == needle).map(|at| at + from)
}

/// The number after `name="` in XMP text, if it is there and is a number.
fn attribute(xmp: &[u8], name: &str) -> Option<f64> {
    let key = format!("{name}=\"");
    let at = find(xmp, key.as_bytes(), 0)? + key.len();
    let end = find(xmp, b"\"", at)?;
    std::str::from_utf8(&xmp[at..end]).ok()?.trim().parse().ok()
}

/// Where a file's gain map starts: its length is stated in the primary image's container directory, and it is
/// the last thing in the file.
fn split(file: &[u8]) -> Result<(&[u8], &[u8]), ReadError> {
    let missing = || ReadError::NotAGainMap("no gain-map container directory in the primary XMP".into());
    let item = find(file, b"Item:Semantic=\"GainMap\"", 0).ok_or_else(missing)?;
    let close = find(file, b">", item).ok_or_else(missing)?;
    let length = attribute(&file[item..close], "Item:Length").ok_or_else(missing)? as usize;
    if length == 0 || length >= file.len() {
        return Err(missing());
    }
    Ok(file.split_at(file.len() - length))
}

fn decode_rgb(jpeg: &[u8]) -> Result<(Vec<u8>, usize, usize), ReadError> {
    let mut decoder = jpeg_decoder::Decoder::new(jpeg);
    let pixels = decoder.decode().map_err(|e| ReadError::NotAGainMap(e.to_string()))?;
    let info = decoder.info().ok_or_else(|| ReadError::NotAGainMap("a JPEG with no header".into()))?;
    let (width, height) = (usize::from(info.width), usize::from(info.height));
    match info.pixel_format {
        jpeg_decoder::PixelFormat::RGB24 => Ok((pixels, width, height)),
        jpeg_decoder::PixelFormat::L8 => Ok((pixels.iter().flat_map(|&v| [v, v, v]).collect(), width, height)),
        other => Err(ReadError::Unsupported(format!("pixels of the form {other:?}"))),
    }
}

/// The size of a gain-map JPEG's image, `(width, height)`.
pub fn gainmap_size(file: &[u8]) -> Result<(usize, usize), ReadError> {
    let (primary, _) = split(file)?;
    let mut decoder = jpeg_decoder::Decoder::new(primary);
    decoder.read_info().map_err(|e| ReadError::NotAGainMap(e.to_string()))?;
    let info = decoder.info().ok_or_else(|| ReadError::NotAGainMap("a JPEG with no header".into()))?;
    Ok((usize::from(info.width), usize::from(info.height)))
}

/// A gain-map JPEG as a display with `display_boost` times SDR white of headroom shows it: linear RGB in
/// multiples of SDR white, `height * width * 3` values row by row, and the size. A boost of 1 gives the SDR base.
/// The formula is the Ultra HDR one, as the Python reference reads a file, with this crate's JPEG decoder: the
/// decoder the writer checks its files with, so a file reads here exactly as its writer left it.
pub fn read_gainmap_jpeg(
    file: &[u8],
    display_boost: f64,
    threads: usize,
) -> Result<(Vec<f32>, usize, usize), ReadError> {
    let (primary, gain) = split(file)?;
    // the two JPEGs are independent: one thread each
    let (base, map) = std::thread::scope(|scope| {
        let other = scope.spawn(|| decode_rgb(gain));
        (decode_rgb(primary), other.join().unwrap_or_else(|_| Err(ReadError::NotAGainMap("the decoder failed".into()))))
    });
    let (base, width, height) = base?;
    let (map, map_width, map_height) = map?;
    if (map_width, map_height) != (width, height) {
        return Err(ReadError::Unsupported("a gain map of another size than its base".into()));
    }
    let number = |name: &str, default: Option<f64>| {
        attribute(gain, &format!("hdrgm:{name}"))
            .or(default)
            .ok_or_else(|| ReadError::NotAGainMap(format!("the gain map states no {name}")))
    };
    let (low, high) = (number("GainMapMin", Some(0.0))?, number("GainMapMax", None)?);
    let gamma = number("Gamma", Some(1.0))?;
    let (offset_sdr, offset_hdr) = (number("OffsetSDR", Some(OFFSET))?, number("OffsetHDR", Some(OFFSET))?);
    let (capacity_min, capacity_max) = (number("HDRCapacityMin", Some(0.0))?, number("HDRCapacityMax", None)?);
    let weight = ((display_boost.log2() - capacity_min) / (capacity_max - capacity_min)).clamp(0.0, 1.0);
    // 8-bit inputs: 256 possible values of each factor, worked out once
    let linear: Vec<f64> = (0..256).map(|code| srgb_eotf(f64::from(code) / 255.0) + offset_sdr).collect();
    let boost: Vec<f64> = (0..256)
        .map(|code| {
            let recovery = (f64::from(code) / 255.0).powf(1.0 / gamma);
            ((low * (1.0 - recovery) + high * recovery) * weight).exp2()
        })
        .collect();
    let mut out = vec![0f32; base.len()];
    let to = out.as_mut_ptr() as usize;
    crate::par::in_ranges(base.len(), crate::par::threads_for(threads, base.len()), |range| {
        let out = to as *mut f32;
        for i in range {
            let value = linear[usize::from(base[i])] * boost[usize::from(map[i])] - offset_hdr;
            unsafe { out.add(i).write(value as f32) };
        }
    });
    Ok((out, width, height))
}

/// Relative: a pixel must be more than this much brighter in the scaled render to be lifted (udeep.mpl).
const LIFT_TOLERANCE: f64 = 0.01;

/// A figure's gain-map JPEG straight from four 8-bit renders of it (`height * width * 3` sRGB codes each, row by
/// row), and the number of pixels lifted above the ordinary figure:
///
/// - `base`: the figure as it is, which becomes the file's SDR image unchanged;
/// - `scaled`: the same with each HDR image divided by the headroom, so that it fits the 8 bits;
/// - `black` and `white`: the same with the HDR images black, and white.
///
/// A pixel that is black in `black` and white in `white` is made by an HDR image alone, and only such a pixel is
/// lifted, to `scaled` times the headroom, and only where that is brighter than `base` by more than the rounding
/// of the renders. Everything else is the base. This is the rule of the Python reference's `savefig_hdr`
/// (profile 6.11), done here without the two float images it would otherwise take.
#[allow(clippy::too_many_arguments)]
pub fn gainmap_from_renders(
    base: &[u8],
    scaled: &[u8],
    black: &[u8],
    white: &[u8],
    width: usize,
    height: usize,
    headroom: f64,
    quality: u8,
    gain_quality: u8,
    description: &str,
) -> Result<(Vec<u8>, usize), WriteError> {
    let count = width.checked_mul(height).and_then(|n| n.checked_mul(3)).ok_or(WriteError::Shape)?;
    if count == 0 || [base, scaled, black, white].iter().any(|r| r.len() != count) || width > 0xFFFF || height > 0xFFFF
    {
        return Err(WriteError::Shape);
    }
    if !(headroom > 1.0) || !headroom.is_finite() {
        return Err(WriteError::Range);
    }
    let linear = linear_of_codes();
    let image_only = |pixel: usize| (0..3).all(|c| black[pixel + c] == 0 && white[pixel + c] == 255);
    let hdr_of = |i: usize| {
        let below = linear[usize::from(base[i])];
        let lifted = linear[usize::from(scaled[i])] * headroom;
        if lifted > below * (1.0 + LIFT_TOLERANCE) + 1e-4 && image_only(i - i % 3) { lifted } else { below }
    };
    let held = |pixel: usize| (0..3).all(|c| hdr_of(pixel + c) <= linear[usize::from(base[pixel + c])] + 1e-6);
    let mut is_lifted = vec![false; count / 3];
    par::fill(&mut is_lifted, 0, |pixel| {
        (0..3).any(|c| hdr_of(pixel * 3 + c) > linear[usize::from(base[pixel * 3 + c])])
    });
    let lifted = is_lifted.iter().filter(|&&l| l).count();
    drop(is_lifted);
    let (file, _) = file_from_codes(
        base.to_vec(),
        &hdr_of,
        &held,
        width,
        height,
        headroom,
        quality,
        gain_quality,
        description,
        false,
        GAIN_PASS_LIMIT,
    )?;
    Ok((file, lifted))
}
