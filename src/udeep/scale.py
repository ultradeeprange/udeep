"""Data -> normalised position t in [0, 1]. Element-wise; limits are passed in, never inferred.

Float64 (design decision D8), and written against the Python Array API standard (D22): the same code runs on
any array that follows it, on a CPU or a GPU.
"""
from __future__ import annotations

import math

import numpy as np

KINDS = ("linear", "log", "asinh")


def namespace(x):
    """The array library `x` belongs to, per the Array API standard; NumPy for anything else."""
    get = getattr(x, "__array_namespace__", None)
    return np if get is None else get()


def as_float(x):
    """(library, `x` as a float64 array of that library). Lists and scalars become NumPy arrays."""
    xp = namespace(x)
    if xp is np:
        return np, np.asarray(x, dtype=np.float64)
    return xp, xp.astype(x, xp.float64)


CHUNK = 1 << 16                  # values mapped at a time: bounds the working memory, whatever the image's size


def in_chunks(function, x, tail: tuple = (), dtype=np.float64):
    """`function(x)` for a large NumPy array, worked through CHUNK values at a time into one output array of shape
    x.shape + tail. Each step of the mapping makes several arrays as large as its input on the way to its answer;
    done whole, those are many times the image. The result is the same, value for value: every step is a function
    of single values. Small arrays, and arrays of other libraries, go straight through.

    `dtype` is what the output is kept as. The arithmetic is float64 either way; float32 rounds each result once,
    as it is stored."""
    if namespace(x) is not np or not isinstance(x, np.ndarray) or x.size <= CHUNK:
        out = function(x)
        return out if dtype is np.float64 or not isinstance(out, np.ndarray) else out.astype(dtype)
    flat = x.reshape(-1)
    out = np.empty((flat.size, *tail), dtype=dtype)
    for start in range(0, flat.size, CHUNK):
        out[start:start + CHUNK] = function(flat[start:start + CHUNK])
    return out.reshape(*x.shape, *tail)


def _edge(v: float, kind: str, width: float) -> float:
    return v if kind == "linear" else math.log(v) if kind == "log" else math.asinh(v / width)


def _check(kind: str, lo: float, hi: float, width: float) -> None:
    if kind not in KINDS:
        raise ValueError(f"unknown scale kind {kind!r}")
    if not hi > lo:
        raise ValueError(f"need hi > lo, got lo={lo}, hi={hi}")
    if kind == "log" and not lo > 0.0:
        raise ValueError("log scale needs lo > 0")
    if not width > 0.0:
        raise ValueError("width must be > 0")


def scale(x, kind: str, lo: float, hi: float, width: float = 1.0):
    """Map x to t in [0, 1]: `lo` -> 0, `hi` -> 1, clipped; NaN stays NaN.

    kind: "linear", "log" (needs lo > 0; x <= 0 -> 0) or "asinh" (linear within ~`width`
    of zero, logarithmic beyond).
    """
    _check(kind, lo, hi, width)
    xp, v = as_float(x)
    f_lo, f_hi = _edge(lo, kind, width), _edge(hi, kind, width)
    if kind == "linear":
        f = v
    elif kind == "log":
        positive = v > 0.0
        f = xp.where(positive, xp.log(xp.where(positive, v, 1.0)), -math.inf)
    else:
        f = xp.asinh(v / width)
    t = xp.clip((f - f_lo) / (f_hi - f_lo), 0.0, 1.0)
    return xp.where(xp.isnan(v), math.nan, t)


def unscale(t, kind: str, lo: float, hi: float, width: float = 1.0):
    """The inverse of `scale` on [0, 1]: the value at position t."""
    _check(kind, lo, hi, width)
    xp, t = as_float(t)
    f = _edge(lo, kind, width) + t * (_edge(hi, kind, width) - _edge(lo, kind, width))
    return f if kind == "linear" else xp.exp(f) if kind == "log" else width * xp.sinh(f)
