"""hamsci-physics expires its own L3 rows in timestd.db (contract 17.7 expire)."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SIG = os.environ.get("SIGMOND_LIB_FOR_TESTS") or str(
    Path(__file__).resolve().parents[2] / "sigmond" / "lib")
if os.path.isdir(_SIG):
    sys.path.insert(0, _SIG)
from hamsci_physics import contract, storage_expire  # noqa: E402

needs_kit = pytest.mark.skipif(storage_expire.load_kit() is None,
                               reason="sigmond client kit absent")
NOW = time.time()


def _iso(days_ago):
    t = datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(days=days_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _db(root, table, ages):
    p = root / "phase2" / "timestd.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, timestamp_utc TEXT, v REAL)")
    con.executemany(f"INSERT INTO {table} (timestamp_utc, v) VALUES (?, 1.0)",
                    [(_iso(a),) for a in ages])
    con.commit()
    con.close()
    return p


def _count(p, table):
    con = sqlite3.connect(p)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


@needs_kit
def test_expires_its_own_table(tmp_path):
    p = _db(tmp_path, "L3_dtec", list(range(400, 0, -1)))
    a = storage_expire.expire_target(tmp_path, "timestd.L3_dtec", 365, 60, now=NOW)
    assert not a["refused"] and a["unit"] == "rows"
    assert a["deleted"] == 35 and a["complete"]
    assert _count(p, "L3_dtec") == 365


def test_refuses_hf_timestds_table(tmp_path):
    p = _db(tmp_path, "L2_tick_phase", [40, 1])
    a = storage_expire.expire_target(tmp_path, "timestd.L2_tick_phase", 30, 60)
    assert a["refused"]
    assert _count(p, "L2_tick_phase") == 2


def test_refuses_other_database(tmp_path):
    a = storage_expire.expire_target(tmp_path, "authority_history.authority_snapshot", 30, 60)
    assert a["refused"]


@needs_kit
def test_gnss_vtec_is_owned(tmp_path):
    p = _db(tmp_path, "L3_gnss_vtec", [40, 1])
    a = storage_expire.expire_target(tmp_path, "timestd.L3_gnss_vtec", 30, 60, now=NOW)
    assert a["deleted"] == 1 and _count(p, "L3_gnss_vtec") == 1


def test_l3b_tables_are_not_claimed():
    assert not any(t.startswith("L3b_") or t.startswith("L3B_")
                   for t in storage_expire.OWN_TABLES)


def test_declared_days():
    d = storage_expire.DAYS
    assert d["L3_dtec_timeseries"] == 30 and d["L3_dtec_diff"] == 30
    assert d["L3_gnss_vtec"] == 30
    assert d["L3_physics"] == 365 and d["L3_dtec"] == 365
    assert set(d) == set(storage_expire.OWN_TABLES)


def test_every_table_sink_declares_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(contract, "GNSS_VTEC_TOML", tmp_path / "none.toml")
    sinks = contract._data_sinks({}, Path("/var/lib/timestd"))
    svc = [s for s in sinks if s["kind"] == "service"]
    assert svc and all(s.get("expires") for s in svc)
    [phys] = [s for s in sinks if s["target"] == "timestd.L3_physics"]
    assert phys["retention_days"] == 365 and phys["mb_per_day"] == 1
    by = {s["target"]: s["retention_days"] for s in svc}
    assert by["timestd.L3_dtec_diff"] == 30 and by["timestd.L3_dtec_timeseries"] == 30


def test_gnss_sink_expires_when_enabled(tmp_path, monkeypatch):
    g = tmp_path / "g.toml"
    g.write_text("[gnss_vtec]\nenabled = true\n")
    monkeypatch.setattr(contract, "GNSS_VTEC_TOML", g)
    sinks = contract._data_sinks({}, Path("/var/lib/timestd"))
    [v] = [s for s in sinks if s["target"] == "timestd.L3_gnss_vtec"]
    assert v["expires"] is True and v["retention_days"] == 30


@needs_kit
def test_expire_drops_to_the_db_owner_before_opening(monkeypatch, tmp_path):
    p = _db(tmp_path, "L3_dtec", [400, 1])
    kit = storage_expire.load_kit()
    calls = []
    monkeypatch.setattr(kit, "drop_to_owner", lambda path: calls.append(("drop", path)))
    real_prune, real_vac = kit.prune_rows, kit.incremental_vacuum
    monkeypatch.setattr(kit, "prune_rows",
                        lambda *a, **k: (calls.append(("prune", a[0])), real_prune(*a, **k))[1])
    monkeypatch.setattr(kit, "incremental_vacuum",
                        lambda *a, **k: (calls.append(("vacuum", a[0])), real_vac(*a, **k))[1])
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda *a, **k: (calls.append(("connect", a[0])),
                                         real_connect(*a, **k))[1])
    storage_expire.expire_target(tmp_path, "timestd.L3_dtec", 365, 60, now=NOW)
    assert calls[0] == ("drop", str(p))
    assert [c[0] for c in calls if c[0] in ("prune", "vacuum")] == ["prune", "vacuum"]
    assert [c[0] for c in calls].index("connect") > 0


@needs_kit
def test_verb_is_wired(tmp_path, monkeypatch, capsys):
    from hamsci_physics import cli
    p = _db(tmp_path, "L3_dtec", [400, 1])
    monkeypatch.setattr(storage_expire, "data_root", lambda: tmp_path)
    args = cli.build_parser().parse_args(
        ["storage", "expire", "--target", "timestd.L3_dtec", "--days", "365", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] == 1
    assert _count(p, "L3_dtec") == 1


def test_verb_without_kit_exits_2(monkeypatch, capsys):
    from hamsci_physics import cli
    monkeypatch.setattr(storage_expire, "load_kit", lambda: None)
    parser = cli.build_parser()
    args = parser.parse_args(["storage", "expire", "--target", "timestd.L3_dtec",
                              "--days", "30"])
    assert args.func(args) == 2
