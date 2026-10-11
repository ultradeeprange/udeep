"""The mapping on a GPU: the reference shader (shaders/mapping.wgsl) and a way to run it and read it back.

    uniform_block(mapping)      the shader's parameter block, worked out in float64 and packed as float32
    run(mapping, x)             position, light, fair SDR light and colours for an array of values, from the GPU
    expected_error(mapping)     how far float32 can be expected to fall from the reference, in units of the tolerances
    check(mapping)              ValueError if that is more than a tolerance, or a number does not fit float32

`run` needs the optional `wgpu` package (WebGPU for Python, on Vulkan, Metal or DirectX). It exists to hold the
shader to the golden vectors and to map large batches; a viewer uses the shader's three functions in its own
render pipeline instead. The GPU renders and the CPU reference defines (design decision D15): given the same
float32 values, results agree with the float64 reference to 2e-6 x (1 + |value|) in position and light, and to
1e-5 x (1 + |value|) in colour (D8), not exactly. Where float32 cannot do that, `uniform_block` and `run` raise
ValueError: they do not return a worse answer.

Colour has the wider tolerance because it is judged by what reaches a screen, and because a steep stretch of a
colour-map table multiplies the light's error: fire/1 at headroom 2 has one step, seventeen nodes below its top,
where blue climbs 19 units per unit of light. 1e-5 is about a fiftieth of half a 10-bit code (0.5 / 1023 = 4.9e-4);
the largest difference measured in any accepted mapping is 0.007 of an 8-bit code.

What float32 can hold

The ends of the range and the offset need not be float32 numbers: 273.15..303.15 or 20000.1..20001.1 are held
as well as 0..1, because every constant that is subtracted is passed as a pair of float32 (`uniform_block`).
Three things remain, and `check` refuses each. The positions below are of the knee in the range, for the profile's
reference form above the knee (exit slope 1), and are where the rule draws the line, not where the shader fails:
run regardless, a mapping just past one of them measured about half its tolerance.

1. A number that does not fit a float32 at all: a size above about 3.4e38, or below about 1.2e-38 and not zero.
   That covers the ends of the range, its width and the reciprocal of its width, and for a log stretch the ratio
   of the ends.
2. log and asinh. The function's result is itself a float32, so the position carries a rounding that does not
   shrink with the range: about 2.2e-7 for a log, and for an asinh between 2.6e-7 of the stretched value (where
   that is below 1) and 1e-7 of it (above about 4), each divided by the stretched span. A log range must
   therefore span a ratio of 1.12 at the very least, and more once the curve multiplies the error: with the knee
   in the middle, 1.25 at headroom 2, 1.4 at 4 and 7.4 at 16. An asinh range must span a ratio of 1.5 where its low
   end is no more than the width, 1.7 where it is ten widths and 2.1 where it is a hundred (knee in the middle,
   headroom 4). A knee low in a log or a narrow asinh range is steep enough to break the tolerance: below position
   0.012 of a log range of four decades. A linear stretch has no such limit: its error is a fraction of the
   position.
3. Steepness. The light is out by the position's error times the slope of the curve, and a colour by the light's
   error times the slope of the colour-map table. So a knee near the top is refused, sooner with more headroom.
   With a grey map the knee may be as high as

       headroom                              2       4       8       16
       linear                                0.955   0.905   0.84    0.75
       log, four decades                     0.93    0.845   0.745   0.62
       asinh, 0 to 200 widths                0.935   0.85    0.755   0.63

   The shipped colour maps, fire/1 and ice/1, are held at every shipped headroom (1, 2, 4, 8 and 16): none is
   refused outright. At headrooms 4, 8 and 16 their limits are those of the grey map, and at headroom 1 there is
   none. At headroom 2 the table is steep enough to bring the limit down:

       headroom 2                            linear  log     asinh
       ice/1                                 0.95    0.92    0.92
       fire/1                                0.85    0.75    0.76
       fire/1, exit slope 2                  0.71    0.51    0.53
       fire/1, exit slope 3                  0.565   0.27    0.30

   With any colour map, a headroom between 1 and about 1.03 squeezes the whole upper table into a sliver of light,
   and is refused. A headroom of exactly 1, and a grey map at any headroom, are not.

`expected_error` is the rule itself: it follows those errors through the chain and `check` refuses when any is
above 1. Its nine constants are measured, on an NVIDIA RTX 5070 Ti through Vulkan, not derived, and other
hardware may differ: the tests measure again on whatever GPU they find. On 1800 mappings drawn at random (linear,
log and asinh; grey, fire and ice; headrooms 1 to 16) and sampled closely at both ends and just above the knee,
1433 were let through and none of those was out: the largest error was 0.79 of its tolerance (light) and 0.54
(colour), the median 0.17. Of the 367 refused, 203 were out when run regardless and 164 were not: the rule errs
towards refusing, and most of all for colour, where it adds up the worst each step can do. Of 20 mappings refused
for their colours alone, 3 were out. Two closer looks at the colour limit found nothing out either: 250 accepted
mappings whose expected colour error was above 0.6 measured 0.65 at most, and 122 accepted with a colour map at a
headroom between 1.005 and 1.1 measured 0.58 at most.
"""
from __future__ import annotations

import math
import struct
from functools import lru_cache
from pathlib import Path

import numpy as np

from udeep.mapping import Mapping

SHADER = Path(__file__).with_name("shaders") / "mapping.wgsl"
STRETCH_CODE = {"linear": 0, "log": 1, "asinh": 2, "proportional": 3}
WORKGROUP = 64
MAX_VALUES = WORKGROUP * 65_535          # one dispatch along x; larger batches are split by the caller


FIELDS = ("offset_hi", "offset_lo", "scale", "low_hi", "low_lo", "inv_span", "knee_hi", "knee_lo", "shoulder_width",
          "reference_headroom", "inv_headroom_span", "exit_slope", "stretch", "nodes", "reserved0", "reserved1")
LAYOUT = "<12f4I"                        # the shader's `Mapping` struct: FIELDS in order, 64 bytes
PAIRS = ("offset_lo", "low_lo", "knee_lo")           # the small halves of the pairs: these may be as small as they like

TOLERANCE = 2e-6                         # D8: relative and absolute, in position and light
COLOUR_TOLERANCE = 1e-5                  # D8: relative and absolute, in colour: judged by what reaches a screen
GRID = 16_385                            # positions at which `expected_error` looks; its constants were fitted at this
# What float32 arithmetic costs, as measured on an NVIDIA RTX 5070 Ti through Vulkan (see `expected_error`):
POSITION_RELATIVE = 1.5e-7               # the position, as a fraction of itself: two or three roundings
LOG_ABSOLUTE = 2.2e-7                    # log(v): its result is out by this much whatever its size ...
STRETCH_RELATIVE = {"linear": 0.0, "log": 0.8e-7, "asinh": 0.6e-7}    # ... and by this fraction of itself
ASINH_ARGUMENT = 2.0e-7                  # asinh(v): v is out by this fraction of itself, passed on in full up to v = 1
LIGHT_BELOW = 1.2e-7                     # t / knee, as a fraction of the light, where the light's error is largest
LIGHT_ABOVE = 1.3e-7                     # the cubic above the knee, likewise, as a fraction of (light + headroom) / 2
LIGHT_ANYWHERE = 2.5e-7                  # either of those, at the single place where it is largest (2.46e-7 measured)
COLOUR_RELATIVE = 1.5e-7                 # the interpolation between two table nodes, as a fraction of the colour


def _pair(value: float) -> tuple[float, float]:
    """A float64 as two float32: the nearest one, and what is left over."""
    with np.errstate(over="ignore"):
        high = float(np.float32(value))
    return high, value - high if math.isfinite(high) else 0.0


def _constants(mapping: Mapping) -> dict:
    """Everything in the uniform block that is a real number, in float64, under the shader's names."""
    kind, width, low, high = mapping.kind, mapping.stretch_width, mapping.range_low, mapping.range_high
    if kind == "linear":                 # one subtraction: two in turn would round between them
        offset, scale, f_low, inv_span = mapping.stretch_offset + low, 1.0 / (high - low), 0.0, 1.0
    elif kind == "log":
        offset, scale, f_low, inv_span = mapping.stretch_offset, 1.0 / low, 0.0, 1.0 / math.log(high / low)
    else:
        offset, scale, f_low = mapping.stretch_offset, 1.0 / width, math.asinh(low / width)
        inv_span = 1.0 / (math.asinh(high / width) - f_low)
    knee, headroom = mapping.knee_position, mapping.reference_headroom
    shoulder = 1.0 - knee if knee < 1.0 else 1.0
    (offset_hi, offset_lo), (low_hi, low_lo), (knee_hi, knee_lo) = _pair(offset), _pair(f_low), _pair(knee)
    return {"offset_hi": offset_hi, "offset_lo": offset_lo * scale, "scale": scale, "low_hi": low_hi,
            "low_lo": low_lo * inv_span, "inv_span": inv_span, "knee_hi": knee_hi, "knee_lo": knee_lo / shoulder,
            "shoulder_width": shoulder, "reference_headroom": headroom,
            "inv_headroom_span": 1.0 / (headroom - 1.0) if headroom > 1.0 else 0.0, "exit_slope": mapping.exit_slope}


def _pack(mapping: Mapping) -> bytes:
    numbers = _constants(mapping)
    table = mapping.table
    return struct.pack(LAYOUT, *(numbers[name] for name in FIELDS[:12]), STRETCH_CODE[mapping.stretch],
                       0 if table is None else table.nodes, 0, 0)


def uniform_block(mapping: Mapping) -> bytes:
    """The shader's `Mapping` uniform: 64 bytes, twelve float32 then four uint32, little-endian, in the order of
    FIELDS (the shader file documents each). Everything is worked out in float64 first.

    A number that is subtracted comes as a pair, `_hi` and `_lo`: the nearest float32, and what is left of the
    float64 number after it, already multiplied by the factor the shader applies between the two subtractions.
    Raises ValueError for a mapping the shader cannot hold to the tolerance (`check`)."""
    check(mapping)
    return _pack(mapping)


def _does_not_fit(mapping: Mapping) -> str | None:
    """The first number the shader would need that a float32 cannot hold, as a sentence; None if all fit."""
    smallest, largest = float(np.finfo(np.float32).tiny), float(np.finfo(np.float32).max)
    fits = lambda value: math.isfinite(value) and (value == 0.0 or smallest <= abs(value) <= largest)
    try:
        numbers = _constants(mapping)
    except (OverflowError, ZeroDivisionError):       # the range is too wide or too narrow even for float64
        return "the declared range is too wide or too narrow to work its ends out"
    scale = numbers["scale"] if mapping.kind != "linear" else 1.0
    ends = {"the bottom of the range, as the data has it": mapping.range_low + mapping.stretch_offset,
            "the top of the range, as the data has it": mapping.range_high + mapping.stretch_offset,
            "the bottom of the range, as the stretch is given it": mapping.range_low * scale,
            "the top of the range, as the stretch is given it": mapping.range_high * scale}
    for name, value in {**{n: v for n, v in numbers.items() if n not in PAIRS}, **ends}.items():
        if not fits(value):
            return (f"{name} = {value!r} does not fit a float32, which holds sizes from about {smallest:.2g} to "
                    f"{largest:.2g}, and zero")
    return None


@lru_cache(maxsize=32)
def expected_error(mapping: Mapping) -> dict:
    """How far the shader's float32 answers can be expected to lie from the float64 reference, for position, light
    and colours, each as a multiple of its own tolerance: 2e-6 x (1 + |value|) for position and light (TOLERANCE),
    1e-5 x (1 + |value|) for colours (COLOUR_TOLERANCE). Each is the largest over the range.

    It follows the error through the chain at GRID positions. The position is out by a fraction of itself
    (POSITION_RELATIVE) and, for log and asinh, by the rounding of the function's argument and of its own float32
    result, divided by the stretched span. The light is out by that times the slope of the curve, plus the rounding
    of the curve's own arithmetic. A colour is out by the light's error at the same place times the slope of the
    colour-map table there, plus the rounding of the interpolation. The constants are measured, not derived: see
    the module docstring."""
    t = np.linspace(0.0, 1.0, GRID)
    numbers = _constants(mapping)
    span = 1.0 / numbers["inv_span"]
    stretched = np.abs(numbers["low_hi"] + t * span) if mapping.kind != "linear" else 0.0
    # The stretch's argument is rounded two or three times on its way in. A log turns that into a fixed amount; an
    # asinh into the same fraction of its result while that is small, and no more than the fraction itself (tanh).
    absolute = {"linear": 0.0, "log": LOG_ABSOLUTE, "asinh": ASINH_ARGUMENT * np.tanh(stretched)}[mapping.kind]
    d_position = (absolute + STRETCH_RELATIVE[mapping.kind] * stretched) / span + POSITION_RELATIVE * t
    light = mapping.light_at(t)
    slope = np.abs(np.gradient(light, t))
    below = t <= mapping.knee_position
    size = np.where(below, light, 0.5 * (light + mapping.reference_headroom))    # what the curve's rounding goes with
    d_light = slope * d_position + np.where(below, LIGHT_BELOW, LIGHT_ABOVE) * size
    worst = lambda error, value, tolerance=TOLERANCE: float(np.max(error / (tolerance * (1.0 + value))))
    expected = {"position": worst(d_position, t), "light": worst(d_light, light)}
    if mapping.table is None:                        # grey: the colour is the light, three times
        return {**expected, "colours": worst(d_light, light, COLOUR_TOLERANCE)}
    # A colour takes the light's error at the place where the table is steep, which need not be where the light's
    # error is largest: LIGHT_BELOW and LIGHT_ABOVE give that largest error rightly and are too small for a place
    # where the curve is flat, so here the curve's rounding is taken at its own largest.
    at_any_place = slope * d_position + LIGHT_ANYWHERE * size
    colours = np.abs(np.asarray(mapping.colours(light), dtype=np.float64))
    d_colours = _table_slope(mapping.table, light) * at_any_place[:, None] + COLOUR_RELATIVE * colours
    return {**expected, "colours": worst(d_colours, colours, COLOUR_TOLERANCE)}


def _table_slope(table, light: np.ndarray) -> np.ndarray:
    """How fast each colour of the table changes with light, at each of `light` (which must not decrease): the
    steepest of the table's own steps between the lights on either side. It is taken from the nodes, not from
    differences of the colours at `light`, because a steep stretch of a table can be a single step wide, and where
    the curve is steep the lights are several steps apart and would average it away."""
    steps = table.nodes - 1
    upper = max(table.headroom - 1.0, 0.0)
    slopes = [np.abs(np.diff(table.below.astype(np.float64), axis=0)) * steps]
    edges = [np.linspace(0.0, 1.0, table.nodes)]
    if upper > 0.0:
        slopes.append(np.abs(np.diff(table.above.astype(np.float64), axis=0)) * steps / upper)
        edges.append(np.linspace(1.0, table.headroom, table.nodes)[1:])
    slopes, edges = np.concatenate([*slopes, np.zeros((1, 3))]), np.concatenate(edges)      # the zeros: see `reduceat`
    before, after = np.concatenate([light[:1], light[:-1]]), np.concatenate([light[1:], light[-1:]])
    first = np.clip(np.searchsorted(edges, before, side="right") - 1, 0, len(edges) - 2)
    last = np.clip(np.searchsorted(edges, after, side="left") - 1, first, len(edges) - 2)
    # the largest of slopes[first], ..., slopes[last] for each light: `reduceat` takes its ranges as a flat list of
    # starts, each range ending where the next starts, so every second result is the one wanted
    return np.maximum.reduceat(slopes, np.stack([first, last + 1], axis=1).ravel(), axis=0)[::2]


def check(mapping: Mapping) -> None:
    """Raise ValueError unless the shader can hold this mapping to its tolerances: every number it needs fits a
    float32, and no part of `expected_error` is above 1 (position and light in units of TOLERANCE, colours in units
    of COLOUR_TOLERANCE). The module docstring says what that rules out."""
    problem = _does_not_fit(mapping)
    if problem:
        raise ValueError(f"the shader works in float32 and cannot take this mapping: {problem}")
    expected = expected_error(mapping)
    out = [name for name, error in expected.items() if error > 1.0]      # in the order of the chain
    if out:
        name = out[0]                                # the first step that fails; the later ones inherit its error
        tolerance = COLOUR_TOLERANCE if name == "colours" else TOLERANCE
        raise ValueError(f"the shader works in float32 and cannot hold this mapping to its tolerance: {name} would be "
                         f"out by about {expected[name]:.3g} times {tolerance:g} x (1 + |value|). (Expected errors, "
                         f"each in units of its own tolerance, {TOLERANCE:g} for position and light and "
                         f"{COLOUR_TOLERANCE:g} for colours: {', '.join(f'{k} {v:.3g}' for k, v in expected.items())}; "
                         f"the knee is at position {mapping.knee_position:.6g}.) Use the float64 reference, "
                         f"udeep.Mapping, for this one")


@lru_cache(maxsize=1)
def _device_and_pipeline():
    import wgpu

    adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    device = adapter.request_device_sync()
    module = device.create_shader_module(code=SHADER.read_text(encoding="utf-8"))
    pipeline = device.create_compute_pipeline(layout="auto", compute={"module": module, "entry_point": "main"})
    return adapter, device, pipeline


def adapter_name() -> str:
    """The GPU that `run` uses, for reports."""
    info = _device_and_pipeline()[0].info
    return f"{info.get('device', '?')} ({info.get('backend_type', '?')})"


def run(mapping: Mapping, x) -> dict:
    """Map `x` (any shape, no NaN) on the GPU. Returns float32 arrays: position, light, sdr_light (the shape of
    `x`) and colours (that shape with 3 added)."""
    import wgpu

    values = np.ascontiguousarray(x, dtype=np.float32)
    if np.isnan(values).any():
        raise ValueError("NaN is outside the shader's contract: hold invalid pixels with a mask instead")
    if values.size == 0 or values.size > MAX_VALUES:
        raise ValueError(f"run takes between 1 and {MAX_VALUES} values at a time")
    block = uniform_block(mapping)                   # refuses what float32 cannot hold, before any GPU is asked for
    _adapter, device, pipeline = _device_and_pipeline()
    table = mapping.table
    nodes = np.zeros(6, np.float32) if table is None else np.frombuffer(table.to_bytes(), dtype="<f4")
    use = wgpu.BufferUsage
    buffers = [device.create_buffer_with_data(data=block, usage=use.UNIFORM),
               device.create_buffer_with_data(data=nodes.tobytes(), usage=use.STORAGE),
               device.create_buffer_with_data(data=values.tobytes(), usage=use.STORAGE),
               device.create_buffer(size=values.size * 6 * 4, usage=use.STORAGE | use.COPY_SRC)]
    group = device.create_bind_group(layout=pipeline.get_bind_group_layout(0), entries=[
        {"binding": i, "resource": {"buffer": b, "offset": 0, "size": b.size}} for i, b in enumerate(buffers)])
    encoder = device.create_command_encoder()
    compute = encoder.begin_compute_pass()
    compute.set_pipeline(pipeline)
    compute.set_bind_group(0, group)
    compute.dispatch_workgroups(-(-values.size // WORKGROUP))
    compute.end()
    device.queue.submit([encoder.finish()])
    out = np.frombuffer(device.queue.read_buffer(buffers[3]), dtype="<f4").reshape(values.size, 6)
    shape = values.shape
    return {"position": out[:, 0].reshape(shape), "light": out[:, 1].reshape(shape),
            "sdr_light": out[:, 2].reshape(shape), "colours": out[:, 3:].reshape(*shape, 3)}
