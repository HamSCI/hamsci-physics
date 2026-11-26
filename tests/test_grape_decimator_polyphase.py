"""The decimator computes only the samples it keeps, and keeps what lfilter kept.

StatefulDecimator once ran each stage through scipy.signal.lfilter at the
full rate, then threw away all but every R-th sample.  It now computes only
the kept samples.  These tests hold it to the old arithmetic: one pass of
lfilter over the whole stream, from zero state, decimated on the same phases.
"""

import numpy as np
import pytest
from scipy import signal

from hamsci_physics.grape.decimation import (
    FINAL_FIR_DECIMATION, StatefulDecimator, _BlockDecimatingFIR)


def _lfilter_stream(d: StatefulDecimator, x: np.ndarray) -> np.ndarray:
    """The old cascade over the whole input at once: every output, before discard."""
    R = d.cic_decimation
    f = x
    for _ in range(d.cic_order):
        f = signal.lfilter(np.ones(R) / R, [1.0], f)
    f = signal.lfilter(d.comp_taps, [1.0], f[d.cic_phase::R])
    f = signal.lfilter(d.final_taps, [1.0], f)
    return f[d.final_phase::FINAL_FIR_DECIMATION]


def _noise(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)).astype(np.complex64)


def _assert_close(got: np.ndarray, want: np.ndarray) -> None:
    assert len(got) == len(want)
    scale = np.max(np.abs(want))
    # complex64 output: its rounding, not the arithmetic, sets the floor
    assert np.max(np.abs(got - want)) <= 1e-6 * scale


@pytest.mark.parametrize("factor,phase,taps", [(60, 0, 237), (60, 59, 237),
                                               (40, 17, 2427), (50, 23, 197)])
def test_block_fir_matches_lfilter_then_decimate(factor, phase, taps):
    h = np.random.default_rng(taps).standard_normal(taps)
    x = _noise(factor * 300 + 7, seed=factor + phase)
    want = signal.lfilter(h, [1.0], x)[phase::factor]

    fir = _BlockDecimatingFIR(h, factor, phase)
    pieces = [x[:1], x[1:factor * 3 + 5], x[factor * 3 + 5:factor * 3 + 5],
              x[factor * 3 + 5:factor * 100 + 1], x[factor * 100 + 1:]]
    got = np.concatenate([fir(p) for p in pieces])

    assert len(got) == len(want)
    assert np.max(np.abs(got - want)) <= 1e-12 * np.max(np.abs(want))


def test_block_fir_reset_starts_from_zero_history():
    h = np.random.default_rng(3).standard_normal(237)
    fir = _BlockDecimatingFIR(h, 60, 11)
    first = fir(_noise(6000, seed=1))
    fir(_noise(6000, seed=2))
    fir.reset()
    assert np.array_equal(fir(_noise(6000, seed=1)), first)


@pytest.mark.parametrize("rate", [24000, 20000, 16000])
def test_stream_matches_one_lfilter_pass(rate):
    """process() in uneven pieces, then flush(), gives the old outputs."""
    d = StatefulDecimator(input_rate=rate, output_rate=10)
    minute = rate * 60
    x = _noise(3 * minute, seed=rate)

    got = np.concatenate([d.process(x[:minute]),
                          d.process(x[minute:minute + 12345]),
                          d.process(x[minute + 12345:3 * minute]),
                          d.flush()])

    pad = np.zeros(d.input_per_output * (d.latency_outputs + 1), dtype=np.complex64)
    want = _lfilter_stream(d, np.concatenate([x, pad]))
    want = want[d.latency_outputs:d.latency_outputs + len(x) // d.input_per_output]
    _assert_close(got, want.astype(np.complex64))


def test_primed_stream_matches_one_lfilter_pass():
    """prime() history and flush(lookahead) read as one continuous input."""
    rate = 24000
    minute = rate * 60
    d = StatefulDecimator(input_rate=rate, output_rate=10)
    history, body, ahead = (_noise(minute, seed=s) for s in (4, 5, 6))

    d.prime(history)
    got = np.concatenate([d.process(body), d.flush(lookahead=ahead)])

    n_hist = minute // d.input_per_output
    want = _lfilter_stream(d, np.concatenate([history, body, ahead]))
    want = want[n_hist + d.latency_outputs:n_hist + d.latency_outputs + len(got)]
    assert len(got) == minute // d.input_per_output
    _assert_close(got, want.astype(np.complex64))


def test_reset_is_cheap():
    """reset() runs once per stream, so on every gap; it must not solve for zero state."""
    import time
    d = StatefulDecimator(input_rate=24000, output_rate=10)
    t = time.perf_counter()
    for _ in range(20):
        d.reset()
    assert (time.perf_counter() - t) / 20 < 0.02
