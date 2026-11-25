import numpy as np
import pytest
from hamsci_physics.grape import decimation as dec
from hamsci_physics.grape.decimation import (
    ALIAS_REJECTION_FLOOR_DB, StatefulDecimator, stopband_rejection_db)


def _tone(freq_hz, seconds, rate=24000):
    n = np.arange(int(seconds * rate))
    return np.exp(2j * np.pi * freq_hz * n / rate).astype(np.complex64)


def test_final_fir_rejects_aliases_by_90_db_from_6_hz():
    taps = StatefulDecimator(24000, 10).final_taps
    assert stopband_rejection_db(taps, 400, 6.0) >= ALIAS_REJECTION_FLOOR_DB
    assert len(taps) % 2 == 1


@pytest.mark.parametrize("freq_hz", [6.0, 7.0, 10.0, 50.0])
def test_tones_above_6_hz_leave_the_decimator_90_db_down(freq_hz):
    y = StatefulDecimator(24000, 10).process(_tone(freq_hz, 90))
    rms_db = 20 * np.log10(np.sqrt(np.mean(np.abs(y[100:]) ** 2)))
    assert rms_db <= -ALIAS_REJECTION_FLOOR_DB


def test_dead_helpers_are_gone():
    for name in ("decimate_for_upload", "decimate_for_upload_simple",
                 "get_decimator", "DECIMATION_FUNCTION"):
        assert not hasattr(dec, name)
