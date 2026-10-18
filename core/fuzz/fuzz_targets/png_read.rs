//! Any bytes, read as a 16-bit PQ PNG: an error is fine, a panic, a crash or a hang is not.
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = udeep_core::png::read_png_pq(data, 203.0);
});
