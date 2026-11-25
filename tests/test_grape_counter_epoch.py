"""MEASUREMENT_MODEL §3: a radiod restart renumbers the samples; no consumer
extrapolates across a change in counter_epoch_id.  The day's metadata names
every boundary."""
import json
import tempfile
from pathlib import Path

import numpy as np

from hamsci_physics.grape.decimated_buffer import DayMetadata, DecimatedBuffer, MinuteMetadata, SAMPLES_PER_MINUTE


def test_minute_metadata_carries_epoch_and_origin_and_defaults_for_old_files():
    m = MinuteMetadata(minute_index=3, utc_timestamp=0.0, d_clock_ms=0.0, uncertainty_ms=1.0,
                       quality_grade="A", gap_samples=0, valid=True)
    assert m.counter_epoch_id is None and m.origin is None
    d = MinuteMetadata(**{**m.to_dict(), "counter_epoch_id": "pair-1", "origin": "sysclock"}).to_dict()
    assert d["counter_epoch_id"] == "pair-1" and d["origin"] == "sysclock"


def test_day_summary_counts_epochs_and_boundaries():
    day = DayMetadata(channel="SHARED_5000", date="2026-09-04")
    for i, (epoch, origin) in enumerate([("pair-1", "sysclock"), ("pair-1", "sysclock"),
                                         ("pair-2", "sysclock"), ("pair-2", "native_anchor")]):
        day.minutes[str(i)] = {"minute_index": i, "valid": True, "gap_samples": 0,
                               "counter_epoch_id": epoch, "origin": origin}
    s = day.to_dict()["summary"]
    assert s["counter_epochs"] == 2 and s["epoch_boundaries"] == [2]
    assert s["origin_switches"] == 1


def test_write_minute_records_the_fields():
    with tempfile.TemporaryDirectory() as d:
        buf = DecimatedBuffer(Path(d), "SHARED_5000")
        ok = buf.write_minute(minute_utc=1_788_566_400.0, decimated_iq=np.zeros(SAMPLES_PER_MINUTE, np.complex64),
                              d_clock_ms=0.0, uncertainty_ms=0.004, quality_grade="A", gap_samples=0,
                              counter_epoch_id="r-2026-09-04T15:53:35Z", origin="native_anchor")
        assert ok
        buf.flush_metadata()
        meta = json.loads(next(Path(d).rglob("*_meta.json")).read_text())
        (minute,) = meta["minutes"].values()
        assert minute["counter_epoch_id"] == "r-2026-09-04T15:53:35Z" and minute["origin"] == "native_anchor"


def test_pipeline_resets_the_decimator_at_an_epoch_boundary(monkeypatch):
    """A new StatefulDecimator is built when counter_epoch_id changes, so no
    filter history spans a re-based counter."""
    from hamsci_physics.grape import decimation_pipeline as dp
    built, flushed = [], []

    class FakeDecimator:
        latency_outputs = 0

        def __init__(self, input_rate, output_rate):
            built.append((input_rate, output_rate))

        def prime(self, s): pass

        def process(self, samples):
            return np.zeros(600, np.complex64)

        def flush(self, lookahead=None):
            flushed.append(lookahead)
            return np.zeros(0, np.complex64)

        def describe(self):
            return {}
    monkeypatch.setattr(dp, "StatefulDecimator", FakeDecimator)
    epochs = iter(["pair-1", "pair-1", "pair-2"])

    class FakeReader:
        def __init__(self, *a, **k): pass

        def get_sample_rate(self, date_str): return 24000

        base = 1_788_480_000                    # 2026-09-04T00:00:00Z, inside the day under test

        def get_available_minutes(self, date_str):
            return [self.base, self.base + 60, self.base + 120]

        def read_minute(self, ts):
            if ts not in self.get_available_minutes(None):
                return None, None
            return (np.zeros(24000 * 60, np.complex64),
                    {"timing": {"schema": "v2", "origin": "sysclock", "u_epoch_ns": 8_030_000,
                                "counter_epoch_id": next(epochs)}})

    class FakeBuffer:
        def __init__(self, *a, **k): self.rows = []

        def write_minute(self, **kw): self.rows.append(kw); return True

        def flush_metadata(self): pass

        def set_decimator(self, date_str, record): pass

        def discard_day(self, date_str): pass

        # The pipeline builds the day aside, then commits it.
        def open_staging(self, date_str): return self

        def discard_staging(self, date_str): pass

        def commit_staging(self, date_str): pass

        def has_day(self, date_str): return False
    monkeypatch.setattr(dp, "RawBinaryReader", FakeReader)
    monkeypatch.setattr(dp, "DecimatedBuffer", FakeBuffer)
    dp.DecimationPipeline(Path("/nonexistent"))._process_channel_day("20260904", "SHARED_5000")
    assert len(built) == 2, "one decimator for pair-1, a fresh one for pair-2"
    assert len(flushed) == 2, "each stream flushed, the epoch change included"
