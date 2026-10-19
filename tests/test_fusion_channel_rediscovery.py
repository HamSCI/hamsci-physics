"""Channel rediscovery: its cadence, and what a failed query may not do.

Row 15 of v3.73.  The service now re-runs channel discovery while it
runs: every 30 s while it knows no channels, every 600 s once it does.
A discovery query that FAILS (a locked or corrupt store) must keep the
channel set it had; only a query that succeeds may shrink it.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from hamsci_physics.physics_fusion_service import PhysicsFusionService

LOGGER = "hamsci_physics.physics_fusion_service"


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _store(tmp_path: Path, channels) -> Path:
    db = tmp_path / "timestd.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS L2_timing_measurements ("
                " channel TEXT NOT NULL, timestamp_utc TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_chan_ts"
                " ON L2_timing_measurements (channel, timestamp_utc)")
    con.executemany("INSERT INTO L2_timing_measurements VALUES (?, ?)",
                    [(c, "2026-10-19T21:40:30+00:00") for c in channels])
    con.commit()
    con.close()
    return db


def _service(tmp_path: Path, db: Path, clock: FakeClock) -> PhysicsFusionService:
    data_root = tmp_path / "data"
    (data_root / "phase2").mkdir(parents=True, exist_ok=True)
    (tmp_path / "out").mkdir(exist_ok=True)
    return PhysicsFusionService(data_root=data_root, output_dir=tmp_path / "out",
                                storage_config={"sqlite_path": str(db)},
                                clock=clock)


def _delete_channel(db: Path, channel: str) -> None:
    con = sqlite3.connect(db)
    con.execute("DELETE FROM L2_timing_measurements WHERE channel = ?", (channel,))
    con.commit()
    con.close()


def test_empty_set_retries_after_30_s_not_sooner(tmp_path):
    clock = FakeClock(1000.0)
    db = _store(tmp_path, [])
    svc = _service(tmp_path, db, clock)
    assert svc.channels == []
    _store(tmp_path, ["SHARED_10000"])
    clock.t = 1029.0
    svc._refresh_channels()
    assert svc.channels == [], "rediscovered before the 30 s interval"
    clock.t = 1030.0
    svc._refresh_channels()
    assert svc.channels == ["SHARED_10000"]


def test_populated_set_refreshes_after_600_s_not_sooner(tmp_path):
    clock = FakeClock(1000.0)
    db = _store(tmp_path, ["SHARED_10000"])
    svc = _service(tmp_path, db, clock)
    assert svc.channels == ["SHARED_10000"]
    _store(tmp_path, ["WWV_20000"])
    clock.t = 1599.0
    svc._refresh_channels()
    assert svc.channels == ["SHARED_10000"], "refreshed before 600 s"
    clock.t = 1600.0
    svc._refresh_channels()
    assert svc.channels == ["SHARED_10000", "WWV_20000"]
    # ...and the next refresh comes 600 s after THAT one, not on every call.
    _store(tmp_path, ["BPM_10000"])
    clock.t = 2199.0
    svc._refresh_channels()
    assert "BPM_10000" not in svc.channels, "the interval was not rescheduled"
    clock.t = 2200.0
    svc._refresh_channels()
    assert "BPM_10000" in svc.channels


def test_a_successful_query_with_fewer_channels_shrinks_the_set(tmp_path):
    clock = FakeClock(1000.0)
    db = _store(tmp_path, ["SHARED_10000", "WWV_20000"])
    svc = _service(tmp_path, db, clock)
    _delete_channel(db, "WWV_20000")
    clock.t += 600
    svc._refresh_channels()
    assert svc.channels == ["SHARED_10000"]


def test_a_failed_query_keeps_the_set_and_warns_once(tmp_path, caplog):
    clock = FakeClock(1000.0)
    db = _store(tmp_path, ["SHARED_10000", "WWV_20000"])
    svc = _service(tmp_path, db, clock)
    db.write_bytes(b"this is not a sqlite database" * 100)   # query now fails
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    for _ in range(3):
        clock.t += 600
        svc._refresh_channels()
        assert svc.channels == ["SHARED_10000", "WWV_20000"]
    warns = [r for r in caplog.records if r.name == LOGGER
             and r.levelno == logging.WARNING and "discovery" in r.getMessage()]
    assert len(warns) == 1, [r.getMessage() for r in warns]
    # The cadence holds: the next try waits the populated interval.
    assert svc._next_channel_discovery == clock.t + 600


def test_a_failed_query_on_an_empty_set_keeps_the_30_s_cadence(tmp_path):
    clock = FakeClock(1000.0)
    db = _store(tmp_path, [])
    svc = _service(tmp_path, db, clock)
    db.write_bytes(b"this is not a sqlite database" * 100)
    clock.t += 30
    svc._refresh_channels()
    assert svc.channels == []
    assert svc._next_channel_discovery == clock.t + 30


def test_discovery_reports_failure_as_none_not_as_empty(tmp_path):
    clock = FakeClock(1000.0)
    db = _store(tmp_path, [])
    svc = _service(tmp_path, db, clock)
    assert svc._query_channels() == []
    db.write_bytes(b"this is not a sqlite database" * 100)
    assert svc._query_channels() is None
