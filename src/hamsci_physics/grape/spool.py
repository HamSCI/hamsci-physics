"""Retention for the GRAPE upload spool.

``hs-uploader.service`` ships from ``/var/lib/timestd/upload`` with
``retention = "keep"`` -- it never deletes what it sends.  Until
2026-08-26 grape-daily deleted each package itself, immediately after its
own in-process upload.  Removing that second upload path (so the daemon is
the single outbound route for every client) left the spool with no janitor
at all, and it grows ~40 MB/day.

**Age alone is not a safe rule.**  GRAPE uploads were dead from 2026-08-24
to 2026-08-26 while every status surface reported green; had the outage run
past the retention window, an age-only trim would have quietly deleted
science that was never delivered.  "Old" and "delivered" are different
questions and this module insists on both: a package is removed only when
it is older than the window AND the uploader's watermark cursor has
advanced past it.  Anything old-but-undelivered is KEPT and reported --
that is the alarm, not a nuisance.

Reading the cursor
------------------
The cursor is hs_uploader's KEEP cursor: ``st_mtime_ns`` of the newest OBS
dataset the transport has acked, in ``watermarks(source_id, dest_id,
table_name, cursor)``.

We read it with a **read-only** sqlite connection rather than through
``SqliteWatermarkStore``, deliberately.  That class writes on construct
(schema init, a group-write chmod), and its own docstring says
cross-process writers to one watermark db are "an operator config error".
grape-daily is a different process under a different user; it has no
business opening the daemon's cursor store for writing.  A read-only URI
cannot lock, write, or corrupt it.

The cost is a coupling to hs_uploader's schema.  That is bounded by
failing *safe*: any problem reading the cursor yields ``None``, which
means nothing is provably shipped, which means nothing is deleted.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

#: Default retention window for packaged-and-shipped GRAPE datasets.
DEFAULT_MAX_AGE_DAYS = 14

_NS_PER_DAY = 86_400 * 1_000_000_000

#: Identity of the grape pipeline's cursor row.  Must match the
#: [[hs_uploader.pipeline]] block in this repo's deploy.toml.
DEFAULT_SOURCE_ID = "grape-datasets:/var/lib/timestd/upload"
DEFAULT_TABLE = "grape.dataset"


@dataclass
class TrimResult:
    """What a trim pass did, and -- more importantly -- what it refused to do."""

    removed: List[Path] = field(default_factory=list)
    kept_unshipped: List[Path] = field(default_factory=list)
    kept_recent: int = 0
    bytes_freed: int = 0
    cursor_ns: Optional[int] = None

    @property
    def cursor_known(self) -> bool:
        return self.cursor_ns is not None

    def summary_lines(self) -> List[str]:
        lines: List[str] = []
        if self.removed:
            mb = self.bytes_freed / (1024 * 1024)
            lines.append(
                f"   Trimmed {len(self.removed)} shipped package(s), "
                f"{mb:.0f} MB freed"
            )
        if self.kept_unshipped:
            names = ", ".join(sorted(p.name for p in self.kept_unshipped))
            lines.append(
                f"   ⚠️  {len(self.kept_unshipped)} package(s) past the "
                f"retention window but NOT confirmed shipped -- kept: {names}"
            )
        if not self.cursor_known:
            lines.append(
                "   ⚠️  upload cursor unreadable — trim skipped "
                "(nothing deleted without proof of delivery)"
            )
        return lines


def shipped_cursor_ns(
    db_path: Path | str,
    *,
    source_id: str = DEFAULT_SOURCE_ID,
    dest_id: Optional[str] = None,
    table: str = DEFAULT_TABLE,
) -> Optional[int]:
    """Newest OBS mtime (ns) the uploader has acked, or ``None`` if unknown.

    ``dest_id`` pins the transport (``psws-grape-sftp:<host>:<station>``).
    Left as ``None`` it matches whatever single transport this source+table
    has shipped to, which is what a normal single-station host has.  If more
    than one row matches, the *lowest* cursor wins -- trimming only what
    every destination has taken.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        logger.warning("grape trim: watermark db %s not found", db_path)
        return None

    sql = ("SELECT cursor FROM watermarks "
           "WHERE source_id = ? AND table_name = ?")
    params: list = [source_id, table]
    if dest_id:
        sql += " AND dest_id = ?"
        params.append(dest_id)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        logger.warning("grape trim: cannot open %s read-only: %s", db_path, exc)
        return None
    try:
        rows = list(conn.execute(sql, params))
    except sqlite3.Error as exc:
        logger.warning("grape trim: cursor query failed: %s", exc)
        return None
    finally:
        conn.close()

    cursors: List[int] = []
    for (raw,) in rows:
        val = _decode_cursor(raw)
        if val is not None:
            cursors.append(val)
    if not cursors:
        logger.warning(
            "grape trim: no cursor row for source=%s table=%s -- nothing has "
            "been confirmed shipped yet", source_id, table)
        return None
    return min(cursors)


def _decode_cursor(raw) -> Optional[int]:
    """hs_uploader writes the KEEP cursor as ascii integer nanoseconds.

    A legacy float-seconds cursor is accepted the same way the source's own
    decoder accepts it, so an old spool does not read as 'never shipped'.
    """
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:  # legacy float seconds
        return int(float(text) * 1_000_000_000)
    except ValueError:
        logger.warning("grape trim: uninterpretable cursor %r", text)
        return None


def _datasets_in(package: Path) -> List[Path]:
    """The OBS* dataset directories inside one ``<date>`` package."""
    return sorted(p for p in package.rglob("OBS*") if p.is_dir())


def pending_datasets(
    upload_root: Path | str,
    cursor_ns: Optional[int],
) -> List[Path]:
    """OBS datasets the uploader has not acked yet, oldest first.

    Mirrors FileTreeSource's KEEP-mode selection (``st_mtime_ns > cursor``)
    without importing hs_uploader or touching its watermark store, so
    ``grape status`` can answer "what is still
    waiting?" as pure read-only observers.  A ``None`` cursor means nothing
    is known to be shipped, so everything present is pending.
    """
    upload_root = Path(upload_root)
    if not upload_root.is_dir():
        return []
    found: List[tuple] = []
    for obs in upload_root.rglob("OBS*"):
        if not obs.is_dir():
            continue
        try:
            mtime = obs.stat().st_mtime_ns
        except OSError:
            continue
        if cursor_ns is None or mtime > cursor_ns:
            found.append((mtime, obs))
    return [obs for _, obs in sorted(found)]


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def trim_spool(
    upload_root: Path | str,
    *,
    cursor_ns: Optional[int],
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now_ns: Optional[int] = None,
    dry_run: bool = False,
) -> TrimResult:
    """Delete ``<date>`` packages that are BOTH aged out AND confirmed shipped.

    ``cursor_ns`` of ``None`` (unknown delivery state) removes nothing.
    """
    upload_root = Path(upload_root)
    result = TrimResult(cursor_ns=cursor_ns)
    if not upload_root.is_dir():
        return result

    if now_ns is None:
        import time
        now_ns = time.time_ns()
    cutoff_ns = now_ns - max_age_days * _NS_PER_DAY

    for package in sorted(p for p in upload_root.iterdir() if p.is_dir()):
        datasets = _datasets_in(package)
        if not datasets:
            # Not a packaged day (or a half-built one) -- never guess.
            continue
        try:
            newest_ns = max(d.stat().st_mtime_ns for d in datasets)
        except OSError:
            continue

        # Strictly older than the window.  A package sitting exactly ON the
        # boundary is not yet "older than 14 days" and is kept.
        if newest_ns >= cutoff_ns:
            result.kept_recent += 1
            continue

        # Aged out.  Delivered?
        if cursor_ns is None or newest_ns > cursor_ns:
            result.kept_unshipped.append(package)
            logger.warning(
                "grape trim: %s is past the %d-day window but the uploader "
                "has not acked it (mtime %d > cursor %s) -- KEEPING it",
                package.name, max_age_days, newest_ns, cursor_ns)
            continue

        size = _dir_size(package)
        if dry_run:
            result.removed.append(package)
            result.bytes_freed += size
            continue
        try:
            shutil.rmtree(package)
        except OSError as exc:
            logger.warning("grape trim: could not remove %s: %s", package, exc)
            continue
        result.removed.append(package)
        result.bytes_freed += size
        logger.info("grape trim: removed shipped package %s (%d bytes)",
                    package.name, size)

    return result


PACKAGED_MARKER = ".upload_complete"


def mark_day_packaged(day_dir: Path) -> Path:
    """Record that ``day_dir`` holds a finished GRAPE package.

    grape-daily's catch-up sweep skips any day whose ``upload/<day>/`` carries
    this marker.  Nothing wrote it after hf-timestd af45a6a retired SFTPUpload,
    so the sweep re-packaged days 2-7 back every night, and every re-package
    moved the OBS mtime past the send record, which re-sent the day.
    """
    day_dir.mkdir(parents=True, exist_ok=True)
    marker = day_dir / PACKAGED_MARKER
    marker.touch(exist_ok=True)
    return marker


def day_needs_retry(data_root: Path, day: str) -> bool:
    """True when the catch-up sweep should re-run ``day``.

    A day counts as packaged when ``upload/<day>/`` holds the marker anywhere
    below it, or already holds an OBS* dataset, the test Gate 3 applies.  The
    second test covers days packaged before the marker existed, so the first
    night after this change re-sends nothing.  An unpackaged day re-runs only
    while its source (raw_buffer or decimated) remains on disk."""
    day_dir = data_root / "upload" / day
    if day_dir.exists() and (any(day_dir.rglob(PACKAGED_MARKER))
                             or any(day_dir.rglob("OBS*"))):
        return False
    return (any((data_root / "raw_buffer").glob(f"*/{day}"))
            or any((data_root / "products").glob(f"*/decimated/{day}.bin")))
