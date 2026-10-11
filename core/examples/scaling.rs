//! How the parallel loop scales with image size and thread count, measured inside Rust (no host language).
//!
//!     cargo run --release --example scaling
//!
//! Written to chase an anomaly seen from Python: good scaling at 4 and 64 megapixels, almost none at 16. The
//! second table repeats the 16-megapixel case with the output buffer shifted by a few bytes, to test whether the
//! relative position of the two buffers in memory matters.

use std::time::Instant;

use udeep_core::{Mapping, Stretch, par};

fn best(mut f: impl FnMut(), repeat: usize) -> f64 {
    (0..repeat)
        .map(|_| {
            let t = Instant::now();
            f();
            t.elapsed().as_secs_f64() * 1e3
        })
        .fold(f64::INFINITY, f64::min)
}

fn main() {
    let m = Mapping { stretch_width: 0.1, ..Mapping::new(Stretch::Asinh, 0.0, 20.0, 1.2, 4.0) };
    let threads = [1usize, 2, 4, 8, 16];
    println!("megapixels | {}", threads.map(|t| format!("{t:>2} threads")).join(" | "));
    for megapixels in [4usize, 8, 12, 15, 16, 17, 20, 32, 64] {
        let n = megapixels * 1_000_000;
        let x: Vec<f64> = (0..n).map(|i| 20.0 * (i as f64 * 0.618_033_988_75).fract()).collect();
        let mut out = vec![0.0; n];
        let row: Vec<String> =
            threads.iter().map(|&t| format!("{:8.1} ms", best(|| par::light_into(&m, &x, &mut out, t), 5))).collect();
        println!("{megapixels:>10} | {}", row.join(" | "));
    }
    println!("\n16 megapixels, output buffer shifted by k values (8 bytes each):");
    let n = 16_000_000;
    let x: Vec<f64> = (0..n).map(|i| 20.0 * (i as f64 * 0.618_033_988_75).fract()).collect();
    let mut out = vec![0.0; n + 1024];
    println!("  input at {:p}, output at {:p}", x.as_ptr(), out.as_ptr());
    // interleaved and repeated, so that an effect of the shift can be told from the machine being busy
    for shift in [0usize, 100, 0, 100, 511, 0, 64, 72, 80, 96, 128, 200, 300, 400, 511, 512, 0] {
        let row: Vec<String> = [1usize, 2, 8, 16]
            .iter()
            .map(|&t| format!("{t:>2}: {:7.1} ms", best(|| par::light_into(&m, &x, &mut out[shift..shift + n], t), 5)))
            .collect();
        println!("  shift {shift:>3} | {}", row.join(" | "));
    }
}
