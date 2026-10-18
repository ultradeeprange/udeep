//! Any bytes, read as a gain-map JPEG at a display boost of 4.
#![no_main]
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let _ = udeep_core::gainmap::read_gainmap_jpeg(data, 4.0, 1);
});
