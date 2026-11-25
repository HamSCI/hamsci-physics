import json
import numpy as np
from hamsci_physics.grape.decimated_buffer import DayMetadata, DecimatedBuffer, SAMPLES_PER_MINUTE

DAY = "20260904"; T0 = 1_788_480_000.0      # 2026-09-04T00:00:00Z


def test_day_summary_carries_the_decimator_record(tmp_path):
    buf = DecimatedBuffer(tmp_path, "SHARED 5000")
    buf.write_minute(minute_utc=T0, decimated_iq=np.zeros(SAMPLES_PER_MINUTE, np.complex64), filter_edge=True)
    buf.set_decimator(DAY, {"name": "grape-cic-fir", "version": 2})
    buf.flush_metadata()
    meta = json.loads(next(tmp_path.rglob("*_meta.json")).read_text())
    assert meta["summary"]["decimator"]["version"] == 2
    assert meta["minutes"]["0"]["filter_edge"] is True
    assert DayMetadata.from_dict(meta).decimator["version"] == 2


def test_old_day_files_read_as_having_no_decimator_record():
    assert DayMetadata.from_dict({"channel": "x", "date": "2026-09-04"}).decimator is None


def test_discard_day_removes_both_files(tmp_path):
    buf = DecimatedBuffer(tmp_path, "SHARED 5000")
    buf.write_minute(minute_utc=T0, decimated_iq=np.zeros(SAMPLES_PER_MINUTE, np.complex64))
    buf.flush_metadata()
    buf.discard_day(DAY)
    assert not list(tmp_path.rglob(f"{DAY}*"))


# --- Task 4: the pipeline writes each 10 Hz sample under its own label ------
import pytest
from hamsci_physics.grape import decimation_pipeline
from hamsci_physics.grape.decimation import StatefulDecimator

RATE = 24000
N = RATE * 60
CHANNEL = "SHARED 5000"


def _ts(minute):
    return int(T0) + minute * 60


def _install_reader(monkeypatch, day_minutes, extra=(), epoch_of=None, signal_of=None):
    """A reader holding day_minutes (listed for the day) and extra minutes
    (readable but not listed: the neighbouring days)."""
    epoch_of = dict(epoch_of or {})
    signal_of = dict(signal_of or {})
    held = {_ts(m) for m in day_minutes} | {_ts(m) for m in extra}

    class FakeReader:
        def __init__(self, *a, **k): pass

        def get_sample_rate(self, date_str): return RATE

        def get_available_minutes(self, date_str):
            return sorted(_ts(m) for m in day_minutes)

        def read_minute(self, ts):
            if ts not in held:
                return None, None
            minute = (ts - int(T0)) // 60
            samples = signal_of.get(minute)
            if samples is None:
                samples = np.zeros(N, np.complex64)
            return (samples.copy(),
                    {"timing": {"schema": "v2", "origin": "sysclock", "u_epoch_ns": 8_030_000,
                                "counter_epoch_id": epoch_of.get(minute, "pair-1")}})
    monkeypatch.setattr(decimation_pipeline, "RawBinaryReader", FakeReader)


def _run(tmp_path):
    decimation_pipeline.DecimationPipeline(tmp_path)._process_channel_day(DAY, CHANNEL)
    d = tmp_path / "products/SHARED_5000/decimated"
    meta = json.loads((d / f"{DAY}_meta.json").read_text())
    return np.fromfile(d / f"{DAY}.bin", np.complex64), meta


def _impulse_at(second):
    x = np.zeros(N, np.complex64)
    x[second * RATE] = 1.0
    return x


def test_an_impulse_lands_at_its_label_in_the_day_file(tmp_path, monkeypatch):
    # minutes 10..12 present, impulse at 00:11:30
    _install_reader(monkeypatch, [10, 11, 12], signal_of={11: _impulse_at(30)})
    day, _ = _run(tmp_path)
    assert int(np.argmax(np.abs(day))) == (11 * 60 + 30) * 10


def test_minutes_beside_a_gap_carry_filter_edge(tmp_path, monkeypatch):
    # minutes 10..12 present, 9 and 13 missing
    _install_reader(monkeypatch, [10, 11, 12])
    _, meta = _run(tmp_path)
    m = meta["minutes"]
    assert [m[str(k)]["filter_edge"] for k in (10, 11, 12)] == [True, False, True]


def test_day_edges_borrow_a_minute_from_each_neighbouring_day(tmp_path, monkeypatch):
    # day minutes 0, 1, 1438, 1439 present; the reader also holds T0-60 and T0+86400, same epoch
    _install_reader(monkeypatch, [0, 1, 1438, 1439], extra=[-1, 1440])
    _, meta = _run(tmp_path)
    m = meta["minutes"]
    assert m["0"]["filter_edge"] is False and m["1439"]["filter_edge"] is False
    assert m["1"]["filter_edge"] is True and m["1438"]["filter_edge"] is True


def test_a_neighbour_from_another_epoch_is_not_borrowed(tmp_path, monkeypatch):
    # minutes 0 and 1 present; T0-60 carries a different counter_epoch_id
    _install_reader(monkeypatch, [0, 1], extra=[-1], epoch_of={-1: "pair-0"})
    _, meta = _run(tmp_path)
    m = meta["minutes"]
    assert m["0"]["filter_edge"] is True


def test_rerun_starts_the_day_afresh(tmp_path, monkeypatch):
    # pre-write minute 5 with write_minute + flush_metadata, then run with only 10..12 present
    buf = DecimatedBuffer(tmp_path, CHANNEL)
    buf.write_minute(minute_utc=float(_ts(5)), decimated_iq=np.ones(SAMPLES_PER_MINUTE, np.complex64))
    buf.flush_metadata()
    _install_reader(monkeypatch, [10, 11, 12])
    day, meta = _run(tmp_path)
    m = meta["minutes"]
    assert "5" not in m
    assert not day[5 * 600:6 * 600].any()


def test_the_day_records_its_decimator(tmp_path, monkeypatch):
    _install_reader(monkeypatch, [10, 11, 12])
    _, meta = _run(tmp_path)
    assert meta["summary"]["decimator"]["group_delay_removed"] is True


FAIL_MARK = 7.0


class _FailingDecimator(StatefulDecimator):
    """The real decimator, whose filters raise on any input whose first
    sample carries FAIL_MARK.  process() then returns None through its own
    except branch, leaving the stream's bookkeeping as a real failure does."""

    def _filter(self, iq_samples):
        if len(iq_samples) and iq_samples[0] == FAIL_MARK:
            raise FloatingPointError("injected numerical failure")
        return super()._filter(iq_samples)


def test_a_failed_minute_is_a_gap_and_shifts_no_later_label(tmp_path, monkeypatch):
    # minutes 10..14 present; minute 12 fails in the decimator.  Impulses at
    # 00:11:58 (in the 31 samples the flush returns for minute 11) and 00:13:30.
    bad = np.zeros(N, np.complex64)
    bad[0] = FAIL_MARK
    _install_reader(monkeypatch, [10, 11, 12, 13, 14],
                    signal_of={11: _impulse_at(58), 12: bad, 13: _impulse_at(30)})
    monkeypatch.setattr(decimation_pipeline, "StatefulDecimator", _FailingDecimator)
    day, meta = _run(tmp_path)
    m = meta["minutes"]
    assert 11 * 600 + int(np.argmax(np.abs(day[11 * 600:12 * 600]))) == (11 * 60 + 58) * 10
    assert 13 * 600 + int(np.argmax(np.abs(day[13 * 600:14 * 600]))) == (13 * 60 + 30) * 10
    assert int(np.argmax(np.abs(day))) in ((11 * 60 + 58) * 10, (13 * 60 + 30) * 10)
    assert "12" not in m
    assert [m[str(k)]["filter_edge"] for k in (10, 11, 13, 14)] == [True, True, True, True]
    assert not day[12 * 600:13 * 600].any()


# --- Final-review fixes: a re-run never loses a product it cannot rebuild ---

def _existing_day(tmp_path, minutes):
    """Write a prior product holding `minutes`; return its two files' bytes."""
    buf = DecimatedBuffer(tmp_path, CHANNEL)
    for m in minutes:
        buf.write_minute(minute_utc=float(_ts(m)), decimated_iq=np.full(SAMPLES_PER_MINUTE, 1 + m, np.complex64))
    buf.set_decimator(DAY, {"name": "grape-cic-fir", "version": 2})
    buf.flush_metadata()
    return _day_bytes(tmp_path)


def _day_bytes(tmp_path):
    d = tmp_path / "products/SHARED_5000/decimated"
    return (d / f"{DAY}.bin").read_bytes(), (d / f"{DAY}_meta.json").read_bytes()


def _no_staging_left(tmp_path):
    return not (tmp_path / "products/SHARED_5000/decimated/.staging").exists() or \
        not any((tmp_path / "products/SHARED_5000/decimated/.staging").iterdir())


def test_rerun_without_raw_keeps_the_existing_product(tmp_path, monkeypatch):
    before = _existing_day(tmp_path, range(20))
    _install_reader(monkeypatch, [])
    decimation_pipeline.DecimationPipeline(tmp_path)._process_channel_day(DAY, CHANNEL)
    assert _day_bytes(tmp_path) == before
    assert _no_staging_left(tmp_path)


def test_rerun_with_partly_evicted_raw_keeps_the_fuller_product(tmp_path, monkeypatch):
    before = _existing_day(tmp_path, range(20))
    _install_reader(monkeypatch, [10, 11, 12])
    decimation_pipeline.DecimationPipeline(tmp_path)._process_channel_day(DAY, CHANNEL)
    assert _day_bytes(tmp_path) == before
    assert _no_staging_left(tmp_path)


def test_a_fault_during_the_rebuild_keeps_the_existing_product(tmp_path, monkeypatch):
    before = _existing_day(tmp_path, [5])
    _install_reader(monkeypatch, [10, 11, 12])

    def boom(self, lookahead=None):
        raise MemoryError("injected")
    monkeypatch.setattr(StatefulDecimator, "flush", boom)
    with pytest.raises(MemoryError):
        decimation_pipeline.DecimationPipeline(tmp_path)._process_channel_day(DAY, CHANNEL)
    assert _day_bytes(tmp_path) == before
    assert _no_staging_left(tmp_path)


def test_an_interrupted_commit_rolls_forward_on_the_next_run(tmp_path, monkeypatch):
    # The commit crashes after moving the .bin and before moving the
    # metadata.  The next staging finishes the pair: new .bin, new metadata.
    _existing_day(tmp_path, [5])
    buf = DecimatedBuffer(tmp_path, CHANNEL)
    staged = buf.open_staging(DAY)
    staged.write_minute(minute_utc=float(_ts(9)), decimated_iq=np.ones(SAMPLES_PER_MINUTE, np.complex64))
    staged.flush_metadata()
    from hamsci_physics.grape import decimated_buffer as db
    real_replace, calls = db.os.replace, []

    def crashing_replace(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("power lost")
        real_replace(src, dst)
    monkeypatch.setattr(db.os, "replace", crashing_replace)
    with pytest.raises(OSError):
        buf.commit_staging(DAY)
    monkeypatch.setattr(db.os, "replace", real_replace)
    DecimatedBuffer(tmp_path, CHANNEL).open_staging(DAY)
    meta = json.loads(buf._get_paths(DAY)[1].read_text())
    assert set(meta["minutes"]) == {"9"}
    assert np.fromfile(buf._get_paths(DAY)[0], np.complex64)[9 * 600] == 1


def test_a_faulty_neighbour_borrow_falls_back_to_an_unprimed_edge(tmp_path, monkeypatch):
    # Priming with minute -1 raises; minute 1440's sidecar cannot be parsed.
    # Neither fault may cost the day: both edges run unprimed and say so.
    _install_reader(monkeypatch, [0, 1, 1438, 1439], extra=[-1, 1440],
                    signal_of={1: _impulse_at(30)})
    real_read = decimation_pipeline.RawBinaryReader.read_minute

    def read_minute(self, ts):
        samples, meta = real_read(self, ts)
        if ts == _ts(1440):
            meta = {"uncertainty_ms": 1.0, "d_clock_ms": "corrupt"}
        return samples, meta
    monkeypatch.setattr(decimation_pipeline.RawBinaryReader, "read_minute", read_minute)

    def bad_prime(self, iq):
        self.primed_outputs += 3            # leave the decimator half-primed
        raise ValueError("injected prime fault")
    monkeypatch.setattr(StatefulDecimator, "prime", bad_prime)
    day, meta = _run(tmp_path)
    m = meta["minutes"]
    assert set(m) == {"0", "1", "1438", "1439"}
    assert m["0"]["filter_edge"] is True and m["1439"]["filter_edge"] is True
    assert int(np.argmax(np.abs(day))) == (60 + 30) * 10


def test_a_padded_short_minute_and_the_next_carry_filter_edge(tmp_path, monkeypatch):
    # minutes 10..14 present; minute 12 holds only 40 s of its 60 s
    short = np.zeros(RATE * 40, np.complex64)
    _install_reader(monkeypatch, [10, 11, 12, 13, 14], signal_of={12: short})
    _, meta = _run(tmp_path)
    m = meta["minutes"]
    assert m["12"]["gap_samples"] > 0
    assert [m[str(k)]["filter_edge"] for k in (10, 11, 12, 13, 14)] == [True, False, True, True, True]


def test_a_nearly_empty_padded_minute_flags_the_minute_before(tmp_path, monkeypatch):
    # minute 12 holds 1 s of signal: its zeros reach back past its start
    _install_reader(monkeypatch, [10, 11, 12, 13], signal_of={12: np.zeros(RATE, np.complex64)})
    _, meta = _run(tmp_path)
    m = meta["minutes"]
    assert [m[str(k)]["filter_edge"] for k in (10, 11, 12, 13)] == [True, True, True, True]
