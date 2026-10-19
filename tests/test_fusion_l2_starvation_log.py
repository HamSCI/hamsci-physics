"""A run loop that reads no L2 while petting the watchdog must say so.

Row 15 of v3.73: B4's fusion service read zero channels for four days.
It logged "no L2 data" only at DEBUG and kept petting the systemd
watchdog, so the journal held nothing and every health signal stayed
green.  The service must log a WARNING when it goes starved (or starts
starved past a grace period), repeat a count every 10 minutes while the
starvation lasts, and log one INFO line when data returns.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from hamsci_physics.physics_fusion_service import PhysicsFusionService

LOGGER = "hamsci_physics.physics_fusion_service"
T0 = 1_760_000_000.0


def _service(tmp_path: Path) -> PhysicsFusionService:
    db = tmp_path / "timestd.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE L2_timing_measurements ("
                " channel TEXT NOT NULL, timestamp_utc TEXT NOT NULL)")
    con.commit()
    con.close()
    data_root = tmp_path / "data"
    (data_root / "phase2").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    svc = PhysicsFusionService(data_root=data_root, output_dir=tmp_path / "out",
                               storage_config={"sqlite_path": str(db)})
    svc._last_l2_data_at = T0          # as if the loop started at T0
    return svc


def _records(caplog, level):
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == level
            and "L2 data" in r.getMessage()]


def test_starting_starved_warns_once_after_the_grace(tmp_path, caplog):
    svc = _service(tmp_path)
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    svc._check_l2_starvation(T0 + 60)              # inside the grace
    assert _records(caplog, logging.WARNING) == []
    svc._check_l2_starvation(T0 + svc._l2_starved_after_s + 1)
    assert len(_records(caplog, logging.WARNING)) == 1
    for dt in (10, 60, 300):                       # no repeat within 10 min
        svc._check_l2_starvation(T0 + svc._l2_starved_after_s + dt)
    assert len(_records(caplog, logging.WARNING)) == 1


def test_persistent_starvation_repeats_a_count_every_ten_minutes(tmp_path, caplog):
    svc = _service(tmp_path)
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    first = T0 + svc._l2_starved_after_s + 1
    svc._check_l2_starvation(first)
    svc._check_l2_starvation(first + 600)
    warnings = _records(caplog, logging.WARNING)
    assert len(warnings) == 2
    assert "for 13 min" in warnings[1].getMessage(), warnings[1].getMessage()


def test_recovery_logs_one_info_line(tmp_path, caplog):
    svc = _service(tmp_path)
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    svc._check_l2_starvation(T0 + 3600)
    svc._note_l2_read(T0 + 3601, got_data=True)
    svc._note_l2_read(T0 + 3660, got_data=True)
    infos = _records(caplog, logging.INFO)
    assert len(infos) == 1
    assert "returned" in infos[0].getMessage()


def test_producing_then_starved_warns_on_the_transition(tmp_path, caplog):
    svc = _service(tmp_path)
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    t = T0 + 10_000
    svc._note_l2_read(t, got_data=True)
    svc._note_l2_read(t + 30, got_data=False)
    svc._check_l2_starvation(t + 60)
    assert _records(caplog, logging.WARNING) == []
    svc._check_l2_starvation(t + svc._l2_starved_after_s + 1)
    assert len(_records(caplog, logging.WARNING)) == 1
