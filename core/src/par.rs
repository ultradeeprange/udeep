//! The parallel CPU path. The mapping is a pure function of one value (design decisions D1, D2), so a buffer can
//! be cut anywhere and its pieces mapped on separate threads with the same result, bit for bit. Standard-library
//! threads only: the core keeps no dependencies.

use std::{num::NonZero, ops::Range, sync::OnceLock, thread};

use crate::Mapping;

/// The number of threads to use for `n` values: `requested`, or every available core when it is 0, but never
/// more than one thread per `MIN_PER_THREAD` values (starting a thread costs more than mapping a few thousand).
pub fn threads_for(requested: usize, n: usize) -> usize {
    const MIN_PER_THREAD: usize = 16_384;
    let most = n / MIN_PER_THREAD;
    if most <= 1 {
        return 1; // too few values for a second thread: the cores are not even counted
    }
    let available = if requested == 0 { cores() } else { requested };
    available.min(most).max(1)
}

/// The cores available to this process, counted once. On Linux the count was measured at tens of microseconds
/// (it is understood to read the process's limits from files): more than a small call. A limit changed while the
/// process runs is not seen.
fn cores() -> usize {
    static CORES: OnceLock<usize> = OnceLock::new();
    *CORES.get_or_init(|| thread::available_parallelism().map_or(1, NonZero::get))
}

/// Run `work` over `0..n` cut into `threads` ranges of nearly equal length, each on a thread of its own. With one
/// thread nothing is started: `work` runs in the caller's thread, so a small call costs no more than the loop.
/// `threads` is the number already decided ([`threads_for`]).
pub fn in_ranges(n: usize, threads: usize, work: impl Fn(Range<usize>) + Sync) {
    if threads <= 1 {
        return work(0..n);
    }
    let chunk = n.div_ceil(threads).max(1);
    let work = &work;
    thread::scope(|scope| {
        for first in (0..n).step_by(chunk) {
            scope.spawn(move || work(first..(first + chunk).min(n)));
        }
    });
}

/// [`Mapping::light_into`] on `threads` threads (0: every available core). The result is identical to the
/// single-threaded one.
pub fn light_into(mapping: &Mapping, x: &[f64], out: &mut [f64], threads: usize) {
    let n = x.len().min(out.len());
    let threads = threads_for(threads, n);
    if threads == 1 {
        return mapping.light_into(&x[..n], &mut out[..n]);
    }
    let chunk = n.div_ceil(threads);
    thread::scope(|scope| {
        for (xs, os) in x[..n].chunks(chunk).zip(out[..n].chunks_mut(chunk)) {
            scope.spawn(move || mapping.light_into(xs, os));
        }
    });
}

/// `out[i] = f(i)` for every `i`, on `threads` threads (0: every available core). Each thread fills a run of
/// `out` of its own, so the result does not depend on the number of threads.
pub fn fill<T: Send>(out: &mut [T], threads: usize, f: impl Fn(usize) -> T + Sync) {
    let n = out.len();
    let threads = threads_for(threads, n);
    if threads <= 1 {
        for (i, o) in out.iter_mut().enumerate() {
            *o = f(i);
        }
        return;
    }
    let chunk = n.div_ceil(threads);
    let f = &f;
    thread::scope(|scope| {
        for (k, part) in out.chunks_mut(chunk).enumerate() {
            scope.spawn(move || {
                for (j, o) in part.iter_mut().enumerate() {
                    *o = f(k * chunk + j);
                }
            });
        }
    });
}

/// Whether `f(i)` holds for any `i` in `0..n`, on `threads` threads.
pub fn any(n: usize, threads: usize, f: impl Fn(usize) -> bool + Sync) -> bool {
    use std::sync::atomic::{AtomicBool, Ordering};
    let found = AtomicBool::new(false);
    in_ranges(n, threads_for(threads, n), |range| {
        for i in range {
            if f(i) {
                found.store(true, Ordering::Relaxed);
                return;
            }
        }
    });
    found.load(Ordering::Relaxed)
}
