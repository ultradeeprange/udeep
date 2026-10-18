//! Any bytes, asked for a gain-map JPEG's size.
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = udeep_core::gainmap::gainmap_size(data);
});
