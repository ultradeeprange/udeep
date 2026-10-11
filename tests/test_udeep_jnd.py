"""udeep.jnd: PQ step visibility against the DICOM PS3.14 GSDF."""
import numpy as np
import pytest

from udeep.export import pq_encode
from udeep.jnd import gsdf, jnd_relative, pq_decode, pq_step_in_jnd


def test_gsdf_matches_the_standard_endpoints():
    assert gsdf(1) == pytest.approx(0.0500, abs=5e-5)          # PS3.14: j=1 -> 0.05 cd/m2
    assert gsdf(1023) == pytest.approx(3993.4, rel=1e-4)       # PS3.14: j=1023 -> 3993.4 cd/m2


def test_jnd_is_about_two_thirds_of_a_percent_at_high_luminance():
    assert 0.0060 < jnd_relative(1000.0) < 0.0070               # ~0.65 % (the profile's figure)
    assert jnd_relative(1.0) > 3 * jnd_relative(1000.0)         # vision is coarser in the dark


def test_pq_decode_inverts_encode():
    nits = np.array([0.01, 1.0, 203.0, 4000.0])
    assert pq_decode(pq_encode(nits)) == pytest.approx(nits, rel=1e-9)


def test_each_two_bits_quarter_the_step():
    for bits in (8, 10):
        r = pq_step_in_jnd(203.0, bits) / pq_step_in_jnd(203.0, bits + 2)
        assert r == pytest.approx(4.0, rel=0.02)


def test_ten_bit_is_near_one_jnd_and_twelve_bit_well_below():
    nits = np.array([10.0, 100.0, 203.0, 1000.0, 3000.0])
    ten, twelve = pq_step_in_jnd(nits, 10), pq_step_in_jnd(nits, 12)
    assert np.all((ten > 1.0) & (ten < 1.5))                   # just visible, not ~4 JNDs
    assert np.all(twelve < 0.4)
