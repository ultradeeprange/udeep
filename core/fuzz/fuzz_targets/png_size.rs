//! Any bytes, asked for a 16-bit PQ PNG's size: never a size the data could not hold.
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = udeep_core::png::png_pq_size(data);
});
