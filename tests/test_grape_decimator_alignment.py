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


from hamsci_physics.grape.decimation import DECIMATOR_VERSION, SUPPORTED_INPUT_RATES

def _run(d, x, minute):
    out = [d.process(x[i:i + minute]) for i in range(0, len(x), minute)]
    return np.concatenate(out + [d.flush()])


@pytest.mark.parametrize("rate", sorted(SUPPORTED_INPUT_RATES))
def test_impulse_lands_on_its_label(rate):
    x = np.zeros(rate * 120, np.complex64); x[rate * 50] = 1
    y = _run(StatefulDecimator(rate, 10), x, rate * 60)
    assert len(y) == 1200
    assert int(np.argmax(np.abs(y))) == 500
    assert abs(y[499] - y[501]) < 1e-9


@pytest.mark.parametrize("freq_hz", [1.0, 4.0])
def test_tone_phase_matches_its_labels(freq_hz):
    y = _run(StatefulDecimator(24000, 10), _tone(freq_hz, 120), 24000 * 60)
    i = np.arange(len(y))
    err = np.angle(y * np.exp(-2j * np.pi * freq_hz * i / 10))[100:-100]
    assert np.max(np.abs(err)) < 1e-6


@pytest.mark.parametrize("freq_hz", [0.5, 2.0, 4.0, 4.9])
def test_passband_is_flat_to_5_hz(freq_hz):
    y = _run(StatefulDecimator(24000, 10), _tone(freq_hz, 120), 24000 * 60)[100:-100]
    assert abs(20 * np.log10(np.sqrt(np.mean(np.abs(y) ** 2)))) < 0.1


def test_every_minute_yields_600_samples_once_flushed():
    d = StatefulDecimator(24000, 10)
    first = d.process(np.zeros(1_440_000, np.complex64))
    assert len(first) == 600 - d.latency_outputs
    assert len(d.process(np.zeros(1_440_000, np.complex64))) == 600
    assert len(d.flush()) == d.latency_outputs


def test_flush_with_lookahead_equals_a_continuous_run():
    x = _tone(1.0, 180); m = 1_440_000
    whole = _run(StatefulDecimator(24000, 10), x, m)[:1200]
    d = StatefulDecimator(24000, 10)
    part = np.concatenate([d.process(x[:m]), d.process(x[m:2 * m]), d.flush(lookahead=x[2 * m:])])
    np.testing.assert_allclose(part, whole, atol=1e-6)


def test_prime_removes_the_start_transient():
    # Lookahead for the tail too: flush() with zeros alone would put an
    # end transient on the last outputs, which is not what this checks.
    x = _tone(1.0, 180); m = 1_440_000
    ref = np.exp(2j * np.pi * 1.0 * np.arange(600) / 10)
    primed = StatefulDecimator(24000, 10); primed.prime(x[:m])
    y = np.concatenate([primed.process(x[m:2 * m]), primed.flush(lookahead=x[2 * m:])])
    assert np.max(np.abs(y[:600] - ref)) < 1e-3
    cold = _run(StatefulDecimator(24000, 10), x[m:2 * m], m)
    assert np.abs(cold[0] - ref[0]) > 0.1


def test_prime_after_process_is_refused():
    d = StatefulDecimator(24000, 10); d.process(np.zeros(2400, np.complex64))
    with pytest.raises(RuntimeError):
        d.prime(np.zeros(2400, np.complex64))


def test_describe_states_the_correction():
    d = StatefulDecimator(24000, 10); r = d.describe()
    assert r["version"] == DECIMATOR_VERSION == 2
    assert r["group_delay_removed"] is True and r["residual_delay_s"] == 0.0
    assert r["group_delay_s"] == pytest.approx(d.group_delay_input_samples / 24000)
    assert r["stopband_hz"] == 6.0 and r["stopband_rejection_db"] >= 90.0
