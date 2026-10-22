"""udeep used from many threads at once: on any Python, and on a free-threaded one (3.13t, 3.14t), where threads
really do run Python at the same time. Whatever the number of threads, the results must be the ones a single thread
gets, value for value; and the package's own settings must not be torn by threads that arrive together."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from udeep import Mapping, core, gainmap
from udeep.view import hdr_view

STRETCH = {"kind": "asinh", "lo": 0.0, "hi": 100.0, "width": 5.0}


@pytest.fixture(autouse=True)
def as_found():
    """Put back the core's state and the thread setting, whatever a test did to them."""
    state, on, threads = dict(core._state), core._state["on"], core.THREADS
    yield
    core._state.clear()
    core._state.update(state)
    core._state["on"], core.THREADS = on, threads


def load_from_many_threads(monkeypatch, loader) -> list:
    """Eight threads ask at once whether the core is there, on a fresh state, while finding the library is slow."""
    found = core.library_path()
    monkeypatch.setattr(core, "library_path", lambda: (time.sleep(0.2), found)[1])
    core._state.update({"library": None, "looked": False, "on": None})
    gate = threading.Barrier(8)

    def ask() -> bool:
        gate.wait()
        return loader()

    with ThreadPoolExecutor(8) as pool:
        return [f.result() for f in [pool.submit(ask) for _ in range(8)]]


@pytest.mark.skipif(core.library_path() is None, reason="needs the core library")
def test_threads_that_arrive_together_all_find_the_core(monkeypatch):
    assert load_from_many_threads(monkeypatch, core.available) == [True] * 8
    assert core.enabled()


@pytest.mark.skipif(core.library_path() is None, reason="needs the core library")
def test_the_way_the_core_was_loaded_before_fails_this_check(monkeypatch):
    """The control: the loader as it was before 22 October 2026, which marked the library as looked for before it
    had found it, so that another thread could take its absence as final and turn the core off for good."""
    def old_load():
        if not core._state["looked"]:
            core._state["looked"] = True
            core._load_now()
        return core._state["library"]

    answers = load_from_many_threads(monkeypatch, lambda: old_load() is not None)
    assert answers.count(True) == 1 and answers.count(False) == 7


def images(count: int) -> list[np.ndarray]:
    rng = np.random.default_rng(9)
    return [rng.exponential(20.0, (300, 400)) for _ in range(count)]


@pytest.mark.parametrize("use_core", [True, False])
def test_views_made_on_many_threads_are_the_ones_one_thread_makes(use_core):
    if use_core and not core.available():
        pytest.skip("needs the core library")
    core.enable(use_core)
    data = images(12)
    alone = [hdr_view(d, "extend", 40.0, STRETCH, 4.0, "fire").hdr for d in data]
    with ThreadPoolExecutor(12) as pool:
        together = list(pool.map(lambda d: hdr_view(d, "extend", 40.0, STRETCH, 4.0, "fire").hdr, data))
    assert all(np.array_equal(a, b) for a, b in zip(alone, together, strict=True))


@pytest.mark.parametrize("use_core", [True, False])
def test_files_written_and_read_on_many_threads_are_the_ones_one_thread_writes(use_core, tmp_path):
    if use_core and not (core.available() and core.files()):
        pytest.skip("needs the core library")
    core.enable(use_core)
    views = [hdr_view(d, "extend", 40.0, STRETCH, 4.0, "fire") for d in images(6)]

    def write_and_read(i: int, folder) -> tuple[bytes, np.ndarray]:
        path = folder / f"{i}.jpg"
        gainmap.write_gainmap_jpeg(views[i].sdr, views[i].hdr, path, 4.0)
        return path.read_bytes(), gainmap.read_gainmap_jpeg(path, 4.0)

    (tmp_path / "alone").mkdir()
    (tmp_path / "together").mkdir()
    alone = [write_and_read(i, tmp_path / "alone") for i in range(6)]
    with ThreadPoolExecutor(6) as pool:
        together = list(pool.map(lambda i: write_and_read(i, tmp_path / "together"), range(6)))
    for (bytes_a, read_a), (bytes_b, read_b) in zip(alone, together, strict=True):
        assert bytes_a == bytes_b and np.array_equal(read_a, read_b)


def test_the_numpy_code_gives_the_same_values_on_any_number_of_threads():
    core.enable(False)
    m = Mapping.from_stretch(STRETCH, 40.0, 4.0, "fire")
    x = np.random.default_rng(2).exponential(20.0, 1_000_003)          # not a whole number of chunks
    x[::997] = np.nan
    results = []
    for count in (1, 2, 3, 8, None):
        core.THREADS = count
        results.append((m.light(x), m.colours(m.light(x))))
    for light, colours in results[1:]:
        assert np.array_equal(light, results[0][0], equal_nan=True)
        assert np.array_equal(colours, results[0][1], equal_nan=True)


def test_work_started_inside_a_worker_runs_there_and_does_not_wait_on_the_pool():
    """A chunk's own work, or a caller's function given to the package's pool, may map a large array itself: it
    runs on that thread, so the pool cannot fill with threads that each wait for another."""
    core.enable(False)
    core.THREADS = 2
    m = Mapping.from_stretch(STRETCH, 40.0, 4.0, "grey")
    x = np.random.default_rng(3).exponential(20.0, 300_000)
    expected = m.light(x)
    pool = core.executor()
    inside = [pool.submit(lambda: (core.in_worker(), m.light(x))) for _ in range(4)]
    for done in inside:
        in_worker, light = done.result(timeout=60)
        assert in_worker and np.array_equal(light, expected)
