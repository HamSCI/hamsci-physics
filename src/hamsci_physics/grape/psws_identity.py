"""Resolve the PSWS upload identity from a parsed hamsci-physics config.

Two config shapes are in the wild and both have to work:

* ``/etc/hamsci-physics/config.toml`` -- this repo's own file, created by
  the 2026-08-24 split.  It deliberately mirrors *mag-recorder's* field
  names: ``[station].psws_station_id``.
* ``/etc/hf-timestd/timestd-config.toml`` -- what GRAPE read before the
  split, and what an un-migrated host still has.  It uses the older
  ``[station].id``.

Reading only the *second* shape is what silently killed GRAPE uploads on
2026-08-25: the installed config had ``psws_station_id`` set, contract.py
validated that name and reported the client green, while the uploader read
``id``, found ``""``, and raised "[station].id (PSWS station id) is empty"
every night.  One config, two names, two readers that disagreed.

So: accept both spellings, prefer the new one, and keep this module free of
third-party imports so the resolution can be unit-tested without numpy,
h5py or hs_uploader present.

No key is resolved here.  hs-uploader ships GRAPE with the machine's one PSWS
key (/etc/hs-uploader/keys/id_ed25519_host, mjh 2026-10-01); the in-process
upload path that read ``[uploader].ssh_key_file`` and a timestd-owned default
is gone, and `smd psws verify` is the login probe.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULT_HOST = "pswsnetwork.eng.ua.edu"


def _section(config: Dict, *path: str) -> Dict:
    """Walk a dotted TOML path, returning {} for any missing/!dict level."""
    cur: Any = config
    for key in path:
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(key) or {}
    return cur if isinstance(cur, dict) else {}


def _first(*values: Any) -> str:
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def station_id(config: Dict) -> str:
    """PSWS station id, e.g. ``S000170``.  New name wins; ``""`` if unset."""
    station = _section(config, "station")
    return _first(station.get("psws_station_id"), station.get("id"))


def instrument_id(config: Dict) -> str:
    """PSWS instrument id, e.g. ``171``.  Spelled the same in both shapes."""
    return _first(_section(config, "station").get("instrument_id"))


def sftp_host(config: Dict) -> str:
    """PSWS SFTP host; both shapes may override it, else the default."""
    return _first(
        _section(config, "uploader").get("host"),
        _section(config, "uploader", "sftp").get("host"),
        DEFAULT_HOST,
    )
