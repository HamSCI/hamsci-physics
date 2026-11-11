"""RawBinaryReader reads F16 raw chunks as complex64.

hf-timestd may store a chunk as zstd of interleaved little-endian binary16
I/Q at a fixed 2**12 scale (sidecar ``dtype: complex-f16``, ``scale: 4096``).
The reader divides the scale out and returns complex64, so everything
downstream sees F32 as before.

The fixture is generated into tmp_path.  The reader's chunk index only
accepts whole-minute chunks, so the sub-minute 1 s chunks go through
``_try_read_file``, the layer that decodes; one 60 s chunk covers
``read_minute``.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import zstandard

from hamsci_physics.grape.decimation import StatefulDecimator
from hamsci_physics.grape.iq_codec import decode_iq
from hamsci_physics.grape.raw_reader import RawBinaryReader

RATE = 24000
CHANNEL = "TESTCHAN"
DAY = "20260906"
T0 = 1788729000  # 2026-09-06 21:10:00 UTC, a minute boundary
F16_SCALE = 4096.0


def _tone(n: int, amplitude: float, start: int = 0) -> np.ndarray:
    t = (np.arange(n) + start) / RATE
    return (amplitude * np.exp(2j * np.pi * 1000.0 * t)).astype(np.complex64)


def _sidecar(ts: int, seconds: int, dtype: str, **extra) -> dict:
    meta = {
        "minute_boundary": ts,
        "channel_name": CHANNEL,
        "frequency_hz": 10000000.0,
        "sample_rate": RATE,
        "samples_written": RATE * seconds,
        "samples_expected": RATE * seconds,
        "file_duration_sec": seconds,
        "completeness_pct": 100.0,
        "gap_count": 0,
        "gap_samples": 0,
        "start_system_time": float(ts),
        "dtype": dtype,
        "byte_order": "little",
        "compression": "zstd",
    }
    meta.update(extra)
    return meta


def _write_chunk(day_dir: Path, ts: int, samples: np.ndarray, kind: str) -> None:
    """kind: 'f16', 'f32' or 'overflow' (complex64 written after an F16 overflow)."""
    seconds = len(samples) // RATE
    if kind == "f16":
        raw = (samples.view(np.float32) * np.float32(F16_SCALE)).astype("<f2").tobytes()
        meta = _sidecar(ts, seconds, "complex-f16", scale=F16_SCALE)
    else:
        raw = samples.astype(np.complex64).tobytes()
        extra = {"f16_fallback": "overflow"} if kind == "overflow" else {}
        meta = _sidecar(ts, seconds, "complex64", **extra)
    (day_dir / f"{ts}.bin.zst").write_bytes(zstandard.ZstdCompressor().compress(raw))
    (day_dir / f"{ts}.json").write_text(json.dumps(meta))


@pytest.fixture
def mixed_day(tmp_path):
    """Three 1 s chunks of one channel in one day: F16, F32, overflow fallback."""
    day_dir = tmp_path / "raw_buffer" / CHANNEL / DAY
    day_dir.mkdir(parents=True)
    truth = {
        T0: _tone(RATE, 1.0, 0),
        T0 + 1: _tone(RATE, 1.0, RATE),
        T0 + 2: _tone(RATE, 100.0, 2 * RATE),  # x4096 overflows binary16
    }
    for ts, kind in zip(truth, ("f16", "f32", "overflow")):
        _write_chunk(day_dir, ts, truth[ts], kind)
    return tmp_path, day_dir, truth


def _reader(root: Path) -> RawBinaryReader:
    return RawBinaryReader(root, CHANNEL, hot_buffer_root=root / "no-hot-buffer")


def test_reads_f16_chunk(mixed_day):
    root, day_dir, truth = mixed_day
    samples, meta = _reader(root)._try_read_file(day_dir, str(T0))
    assert meta["dtype"] == "complex-f16"
    assert samples.dtype == np.complex64
    assert len(samples) == RATE
    # amplitude is divided by the scale: ~1.0, not ~4096
    assert np.max(np.abs(samples)) == pytest.approx(1.0, abs=1e-2)
    # binary16 keeps 11 significant bits
    np.testing.assert_allclose(samples, truth[T0], atol=2e-3)


def test_f32_and_overflow_chunks_read_unscaled(mixed_day):
    root, day_dir, truth = mixed_day
    reader = _reader(root)
    for ts in (T0 + 1, T0 + 2):
        samples, meta = reader._try_read_file(day_dir, str(ts))
        assert meta["dtype"] == "complex64"
        np.testing.assert_array_equal(samples, truth[ts])
    assert meta["f16_fallback"] == "overflow"


def test_read_minute_returns_complex64_for_f16_minute(tmp_path):
    day_dir = tmp_path / "raw_buffer" / CHANNEL / DAY
    day_dir.mkdir(parents=True)
    minute = _tone(RATE * 60, 1.0)
    _write_chunk(day_dir, T0, minute, "f16")
    samples, meta = _reader(tmp_path).read_minute(T0)
    assert samples.dtype == np.complex64
    assert len(samples) == RATE * 60
    assert meta["dtype"] == "complex-f16"
    np.testing.assert_allclose(samples, minute, atol=2e-3)


def test_mixed_day_decimates(mixed_day):
    """Reader output of F16, F32 and fallback chunks feeds the decimator.

    DecimationPipeline works in whole minutes (the reader's index skips
    sub-minute chunks), so this tests the reader-to-decimator hand-off
    at the 1 s unit: the decimator is the pipeline's per-minute step.
    """
    root, day_dir, truth = mixed_day
    reader = _reader(root)
    decimator = StatefulDecimator(input_rate=RATE, output_rate=10)
    outputs = []
    for ts in truth:
        samples, _ = reader._try_read_file(day_dir, str(ts))
        out = decimator.process(samples)
        assert out is not None
        outputs.append(out)
    decimated = np.concatenate(outputs)
    assert decimated.dtype == np.complex64
    assert len(decimated) == 3 * 10
    assert np.all(np.isfinite(decimated))


# 8 complex samples and the bytes hf-timestd's encode_f16 produced for them
# (computed once with hf_timestd.core.iq_codec), with hf-timestd's decode.
VECTOR_BYTES = bytes.fromhex(
    "0068006400ec0070194425ca487270f100000000007900f9df5f4c6700ea005c"
)
VECTOR_DECODED = np.array(
    [
        0.5 + 0.25j,
        -1 + 2j,
        0.0010004043579101562 - 0.0030002593994140625j,
        3.140625 - 2.71875j,
        0j,
        10 - 10j,
        0.12298583984375 + 0.4560546875j,
        -0.75 + 0.0625j,
    ],
    dtype=np.complex64,
)


def test_codec_copy_matches_hf_timestd():
    decoded = decode_iq(VECTOR_BYTES, "complex-f16", 4096.0)
    assert decoded.dtype == np.complex64
    np.testing.assert_array_equal(decoded, VECTOR_DECODED)
    with pytest.raises(ValueError):
        decode_iq(VECTOR_BYTES, "complex-f8")


def _orphan_minute(tmp_path, sidecar):
    """One 60 s F16 chunk; ``sidecar`` None writes no JSON at all."""
    day_dir = tmp_path / "raw_buffer" / CHANNEL / DAY
    day_dir.mkdir(parents=True)
    _write_chunk(day_dir, T0, _tone(RATE * 60, 1.0), "f16")
    if sidecar is None:
        (day_dir / f"{T0}.json").unlink()
    else:
        (day_dir / f"{T0}.json").write_text(json.dumps(sidecar))
    return day_dir


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]


def test_zst_without_sidecar_counts_as_missing(tmp_path, caplog):
    """No sidecar means no dtype: complex64 would reinterpret half-floats."""
    day_dir = _orphan_minute(tmp_path, None)
    reader = _reader(tmp_path)
    with caplog.at_level("WARNING"):
        assert reader.get_available_minutes(DAY) == []
        assert reader.read_minute(T0) == (None, None)
        assert reader.read_minute(T0) == (None, None)
        samples, _ = reader._try_read_file(day_dir, str(T0))
    assert samples is None
    sidecar_warnings = [m for m in _warnings(caplog) if "sidecar" in m]
    assert len(sidecar_warnings) == 1


def test_f16_sidecar_without_scale_counts_as_missing(tmp_path, caplog):
    meta = _sidecar(T0, 60, "complex-f16")      # no "scale"
    day_dir = _orphan_minute(tmp_path, meta)
    reader = _reader(tmp_path)
    with caplog.at_level("WARNING"):
        assert reader.get_available_minutes(DAY) == []
        assert reader.read_minute(T0) == (None, None)
        samples, _ = reader._try_read_file(day_dir, str(T0))
    assert samples is None
    assert len([m for m in _warnings(caplog) if "scale" in m]) == 1


def test_codec_copy_decode_f32_is_zero_copy():
    x = VECTOR_DECODED.copy()
    raw = x.tobytes()
    got = decode_iq(raw, "complex64")
    assert np.shares_memory(got, np.frombuffer(raw, dtype=np.uint8))
    np.testing.assert_array_equal(got, x)
