"""Adopt a station's GNSS VTEC settings from hf-timestd (v3.96, decision 10).

Until v3.96 the VTEC service ran as hf-timestd's ``timestd-vtec.service`` and
read ``[gnss_vtec]`` from ``/etc/hf-timestd/timestd-config.toml``.  It now
runs as ``hamsci-physics-vtec.service`` and reads its own file,
``/etc/hamsci-physics/gnss-vtec.toml``.  sigmond runs ``hamsci-physics vtec
adopt`` (the move's ``migrate`` command in ``deploy.toml``) before it stops
the old unit, so a station keeps its receiver without anyone retyping it.

The adoption copies the ``[gnss_vtec]`` table as text, so the operator's
comments survive.  It never prints or logs a station coordinate: the
position goes from one root-owned file to another and nowhere else.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

GNSS_VTEC_TOML = Path("/etc/hamsci-physics/gnss-vtec.toml")


def extract_table(text: str, table: str) -> Optional[str]:
    """The raw lines of ``[table]``: from its header up to, not including,
    the next line that opens a ``[`` header.  Trailing blank lines are
    dropped.  ``None`` when the table is absent."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.split("#", 1)[0].strip() == f"[{table}]":
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].lstrip().startswith("["):
            end = j
            break
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) + "\n"


def _real_host(host) -> bool:
    return isinstance(host, str) and bool(host.strip()) and "<" not in host


def _real_device(device) -> bool:
    return isinstance(device, str) and bool(device.strip())


def _nonzero(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0


def _write_atomic(dst: Path, data: bytes, mode: int) -> Path:
    """Write ``data`` to a temporary file beside ``dst`` with ``mode`` and
    return its path; the caller renames it into place."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fchmod(fh.fileno(), mode)
        return Path(tmp)
    except BaseException:
        os.unlink(tmp)
        raise


def adopt_vtec(src_config: Path, dst: Path, now: datetime) -> str:
    """Write ``dst`` from ``src_config``'s ``[gnss_vtec]`` table.

    Returns ``"kept"`` when ``dst`` already exists (an operator's file is
    never overwritten), ``"none"`` when there is nothing to adopt (no
    source, no table, ``enabled`` not ``true``, or neither a real host nor
    a device), and ``"written"`` otherwise.
    """
    src_config, dst = Path(src_config), Path(dst)
    if dst.exists():
        return "kept"
    if not src_config.exists():
        return "none"
    text = src_config.read_text()
    config = tomllib.loads(text)
    section = config.get("gnss_vtec") or {}
    if section.get("enabled") is not True:
        return "none"
    if not (_real_host(section.get("host")) or _real_device(section.get("device"))):
        return "none"
    table = extract_table(text, "gnss_vtec")
    if table is None:          # tomllib saw it as a dotted key or inline table
        return "none"

    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    out = [
        f"# Adopted from {src_config} by hamsci-physics vtec adopt, {stamp}.",
        "# Edit here; hf-timestd no longer reads [gnss_vtec].",
        "",
        table.rstrip("\n"),
    ]
    # An RTCM3 receiver that sends a zero 1005 falls back to this position.
    station = config.get("station") or {}
    lat, lon = station.get("latitude"), station.get("longitude")
    if _nonzero(lat) and _nonzero(lon):
        out += ["", "[station]", f"latitude = {lat!r}", f"longitude = {lon!r}"]
        alt = station.get("altitude_m")
        if isinstance(alt, (int, float)) and not isinstance(alt, bool):
            out.append(f"altitude_m = {alt!r}")
    tmp = _write_atomic(dst, ("\n".join(out) + "\n").encode(), 0o644)
    try:
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return "written"


def adopt_netrc(src: Path, dst: Path) -> str:
    """Copy the Earthdata netrc: ``"kept"`` when ``dst`` exists, ``"absent"``
    when ``src`` does not, else ``"copied"`` (mode 0600, ``src``'s owner and
    group)."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        return "kept"
    if not src.exists():
        return "absent"
    st = src.stat()
    tmp = _write_atomic(dst, src.read_bytes(), 0o600)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return "copied"
