"""Row retention for hamsci-physics' L3 tables (contract 17.7 `storage expire`).

sigmond's retention pass calls `hamsci-physics storage expire --target T
--days N` once per target, each call in its own process.  A target names one
table of timestd.db.  This module lists the tables hamsci-physics owns, the
days each keeps, and the delete.  sigmond never deletes a row itself, and
hamsci-physics never touches an hf-timestd table.

Every owned time column, timestamp_utc, holds ISO-8601 text ("...T...Z"),
because the hamsci_dsp schemas declare it a string.  Such text sorts
lexically against the kit's cut.  The kit refuses a column that is not text.
The L3b_* tables stay out: this module does not claim them.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

SIGMOND_LIB = "/opt/git/sigmond/sigmond/lib"
TIMESTD_DB = "phase2/timestd.db"
TIME_COLUMN = "timestamp_utc"

# Days each table keeps.  The dtec series and VTEC keep 30 (Michael, P5/P4).
DAYS = {
    "L3_physics": 365,
    "L3_tec": 365,
    "L3_dtec": 365,
    "L3_dtec_timeseries": 30,
    "L3_dtec_diff": 30,
    "L3_tid": 365,
    "L3C_propagation_stats": 365,
    "L3_gnss_vtec": 30,
}
OWN_TABLES = tuple(DAYS)


def data_root() -> Path:
    """The data root from the station config, as inventory reads it."""
    from . import cli, contract
    try:
        config = cli._load_config(cli.DEFAULT_CONFIG)
    except Exception:
        config = {}
    return contract._data_root(config or {})


def load_kit():
    """Import sigmond's client kit, or return None when it cannot load."""
    try:
        from sigmond.storage import client_kit
        return client_kit
    except ImportError:
        pass
    mod = sys.modules.get("sigmond")
    if mod is not None and getattr(mod, "__file__", None) is None:
        for k in [k for k in sys.modules if k == "sigmond" or k.startswith("sigmond.")]:
            del sys.modules[k]
    if SIGMOND_LIB not in sys.path:
        sys.path.append(SIGMOND_LIB)
    try:
        from sigmond.storage import client_kit
        return client_kit
    except ImportError:
        return None


def _answer(deleted: int = 0, complete: bool = False, refused: bool = False,
            reason: str = "", freed_pages: int = 0) -> dict:
    return {"deleted": deleted, "complete": complete, "refused": refused,
            "reason": reason, "freed_pages": freed_pages, "unit": "rows"}


def _journal(line: str) -> None:
    try:
        import syslog
        syslog.openlog("hamsci-physics-storage", 0, syslog.LOG_DAEMON)
        syslog.syslog(syslog.LOG_NOTICE, line)
    except Exception:
        pass


def _table_of(target: str) -> Optional[str]:
    db, _, table = target.partition(".")
    return table if db == "timestd" and table in OWN_TABLES else None


def expire_target(data_root: Path, target: str, days: int, max_seconds: int,
                  now: Optional[float] = None) -> dict:
    """Delete rows of one owned table older than `days`; vacuum with the time left."""
    table = _table_of(target)
    if table is None:
        return _answer(refused=True, reason=f"not a hamsci-physics table: {target!r}")
    kit = load_kit()
    if kit is None:
        return _answer(refused=True, reason="sigmond client kit not available")
    db = Path(data_root) / TIMESTD_DB
    if not db.is_file():
        return _answer(refused=True, reason=f"database {db} not found")
    t = now if now is not None else time.time()
    deadline = time.monotonic() + max(0, int(max_seconds))
    # Become the file's owner before any connection: a root-owned -wal/-shm
    # would lock the writers out.
    try:
        kit.drop_to_owner(str(db))
        res = kit.prune_rows(str(db), table, TIME_COLUMN, kit.cutoff_iso(t, days),
                             deadline=deadline)
    except Exception as exc:
        return _answer(refused=True, reason=str(exc))
    try:
        freed = kit.incremental_vacuum(str(db), deadline=deadline)
    except Exception as exc:
        return _answer(res.deleted, res.complete, reason=f"vacuum failed: {exc}")
    if res.deleted:
        _journal(f"storage expire: {target}: deleted {res.deleted} rows older "
                 f"than {days} days, {freed} pages freed"
                 + ("" if res.complete else f" ({res.reason or 'more remains'})"))
    return _answer(res.deleted, res.complete, reason=res.reason, freed_pages=freed)


def add_expire_verb(subparsers) -> None:
    """Wire `storage expire`.  Without the kit, or with a sigmond older than its
    expire verb, the verb still exists and exits 2."""
    kit = load_kit()
    if kit is not None and hasattr(kit, "add_expire_verb"):
        kit.add_expire_verb(
            subparsers,
            expire=lambda target, days, max_seconds: expire_target(
                data_root(), target, days, max_seconds))
        return
    storage = subparsers.add_parser("storage", help="storage verbs (contract 17.7)")
    verbs = storage.add_subparsers(dest="storage_cmd")
    p = verbs.add_parser("expire")
    p.add_argument("--target", required=True)
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--max-seconds", type=int, default=600)
    p.add_argument("--json", action="store_true")

    def no_kit(args) -> int:
        print("sigmond client kit not available", file=sys.stderr)
        return 2
    p.set_defaults(func=no_kit)
