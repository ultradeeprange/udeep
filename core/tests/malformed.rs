//! Files come from anyone. A reader may refuse a file, and must not panic on one: the library is built to abort
//! on a panic, which would take the program that called it down with it.
//!
//! This is not a fuzzer: it makes a few thousand damaged copies of valid files with a fixed stream of random
//! numbers, so that it runs the same every time and in a second. A panic here aborts the test.
#![cfg(all(feature = "jpeg", feature = "png"))]

use udeep_core::gainmap::{gainmap_size, read_gainmap_jpeg, write_gainmap_jpeg};
use udeep_core::png::{png_pq_size, read_png_pq, read_png_pq_codes, write_png_pq};

struct Stream(u64);

impl Stream {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_mul(0x5851_f42d_4c95_7f2d).wrapping_add(0x1405_7b7e_f767_814f);
        self.0 >> 16
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }
}

/// One damaged copy of `file`: bits flipped, a run overwritten, cut short, a slice repeated or removed, or a
/// four-byte or two-byte length field set to something large.
fn damaged(file: &[u8], stream: &mut Stream) -> Vec<u8> {
    let mut out = file.to_vec();
    match stream.below(7) {
        0 => {
            for _ in 0..1 + stream.below(4) {
                let at = stream.below(out.len());
                out[at] ^= 1 << stream.below(8);
            }
        }
        1 => {
            let (at, run) = (stream.below(out.len()), 1 + stream.below(32));
            for byte in out.iter_mut().skip(at).take(run) {
                *byte = stream.next() as u8;
            }
        }
        2 => out.truncate(stream.below(out.len())),
        3 => {
            let (at, run) = (stream.below(out.len()), 1 + stream.below(64));
            let slice: Vec<u8> = out.iter().skip(at).take(run).copied().collect();
            let to = stream.below(out.len());
            out.splice(to..to, slice);
        }
        4 => {
            let (at, run) = (stream.below(out.len()), 1 + stream.below(64));
            let end = (at + run).min(out.len());
            out.drain(at..end);
        }
        5 => {
            let at = stream.below(out.len().saturating_sub(4));
            let value = [0xffff_ffff_u32, 0x7fff_ffff, 0x0001_0000, 0, 1 << stream.below(31)][stream.below(5)];
            out[at..at + 4].copy_from_slice(&value.to_be_bytes());
        }
        _ => {
            let at = stream.below(out.len().saturating_sub(2));
            let value = [0xffff_u16, 0, 1, 2, 3, stream.next() as u16][stream.below(6)];
            out[at..at + 2].copy_from_slice(&value.to_be_bytes());
        }
    }
    out
}

fn picture(width: usize, height: usize, top: f64) -> Vec<f64> {
    (0..width * height * 3).map(|i| top * ((i * 37) % 251) as f64 / 250.0).collect()
}

#[test]
fn a_damaged_png_is_refused_or_read_and_never_panics() {
    let mut stream = Stream(20261014);
    let files = [
        write_png_pq(&picture(2, 2, 4.0), 2, 2, 203.0, &[]).unwrap(),
        write_png_pq(&picture(5, 3, 4.0), 5, 3, 203.0, b"<x:xmpmeta>a packet</x:xmpmeta>").unwrap(),
        write_png_pq(&picture(31, 17, 1.0), 31, 17, 100.0, &[]).unwrap(),
    ];
    let (mut refused, mut read) = (0, 0);
    for round in 0..6000 {
        let file = damaged(&files[round % files.len()], &mut stream);
        let size = png_pq_size(&file);
        let codes = read_png_pq_codes(&file);
        let light = read_png_pq(&file, 203.0);
        assert_eq!(codes.is_ok(), light.is_ok());
        match (size, codes) {
            (Ok((width, height)), Ok((codes, w, h))) => {
                assert_eq!((w, h, codes.len()), (width, height, width * height * 3));
                read += 1;
            }
            (_, Err(_)) => refused += 1,
            (Err(_), Ok(_)) => panic!("a file whose size cannot be read was read"),
        }
    }
    assert!(refused > 4000 && read > 0, "refused {refused}, read {read}");
}

#[test]
fn a_damaged_gain_map_jpeg_is_refused_or_read_and_never_panics() {
    let mut stream = Stream(20261015);
    let file_of = |width: usize, height: usize| {
        let sdr = picture(width, height, 1.0);
        let hdr: Vec<f64> = sdr.iter().enumerate().map(|(i, v)| if (i / 3) % 5 == 0 { v * 3.5 } else { *v }).collect();
        write_gainmap_jpeg(&sdr, &hdr, width, height, 4.0, 90, 100, "").unwrap()
    };
    let files = [file_of(8, 8), file_of(24, 16), file_of(33, 9)];
    let (mut refused, mut read) = (0, 0);
    for round in 0..3000 {
        let file = damaged(&files[round % files.len()], &mut stream);
        let size = gainmap_size(&file);
        match read_gainmap_jpeg(&file, 4.0, 1) {
            Ok((values, width, height)) => {
                assert_eq!(values.len(), width * height * 3);
                assert!(size.is_ok());
                read += 1;
            }
            Err(_) => refused += 1,
        }
    }
    assert!(refused > 500 && read > 0, "refused {refused}, read {read}");
}

/// Every place in a JPEG where a frame header (SOF0, SOF1 or SOF2) gives the image's height and width.
fn frame_sizes(file: &[u8]) -> Vec<usize> {
    (0..file.len().saturating_sub(9))
        .filter(|&i| file[i] == 0xff && matches!(file[i + 1], 0xc0..=0xc2) && file[i + 4] == 8)
        .map(|i| i + 5)
        .collect()
}

#[test]
fn a_small_jpeg_that_declares_a_vast_image_is_refused_before_the_decoder_makes_room() {
    // found by fuzzing, 2026-10-13: with both frames set to 65,535 x 65,535 the decoder asked for 4 GiB, the
    // allocation failed, and the process was ended
    let sdr = picture(8, 8, 1.0);
    let hdr: Vec<f64> = sdr.iter().map(|v| v * 3.0).collect();
    let mut file = write_gainmap_jpeg(&sdr, &hdr, 8, 8, 4.0, 90, 100, "").unwrap();
    let frames = frame_sizes(&file);
    assert_eq!(frames.len(), 2, "the image and its gain map");
    for at in frames {
        file[at..at + 4].copy_from_slice(&[0xff, 0xff, 0xff, 0xff]);
    }
    assert!(gainmap_size(&file).is_err() && read_gainmap_jpeg(&file, 4.0, 1).is_err());
    // and a size that is merely wrong, not vast, is the decoder's to refuse or to read: no panic either way
    let mut file = write_gainmap_jpeg(&sdr, &hdr, 8, 8, 4.0, 90, 100, "").unwrap();
    for at in frame_sizes(&file) {
        file[at..at + 4].copy_from_slice(&[0x00, 0x40, 0x00, 0x40]);
    }
    let _ = (gainmap_size(&file), read_gainmap_jpeg(&file, 4.0, 1));
}

#[test]
fn a_small_file_that_declares_a_vast_image_is_refused_without_the_memory_for_one() {
    // a valid PNG of 2 x 2, its header rewritten to 60000 x 60000 with the checksum put right: 21 GB if believed
    let mut file = write_png_pq(&picture(2, 2, 1.0), 2, 2, 203.0, &[]).unwrap();
    file[16..20].copy_from_slice(&60000u32.to_be_bytes());
    file[20..24].copy_from_slice(&60000u32.to_be_bytes());
    let mut crc = 0xffff_ffff_u32;
    for &byte in &file[12..29] {
        crc ^= u32::from(byte);
        for _ in 0..8 {
            crc = if crc & 1 == 1 { (crc >> 1) ^ 0xedb8_8320 } else { crc >> 1 };
        }
    }
    file[29..33].copy_from_slice(&(!crc).to_be_bytes());
    // the header says so, the data is not there, and the size is not handed to a caller who would make room for it
    assert!(png_pq_size(&file).is_err() && read_png_pq(&file, 203.0).is_err());
}
