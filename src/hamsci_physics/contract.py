"""Sigmond client-contract surface (CLIENT-CONTRACT.md v0.8).

Modelled on meteor-scatter's and psk-recorder's ``contract.py`` — the
normal-client reference implementations — rather than hf-timestd's,
which is the atypical case: hf-timestd is the timing-authority
*producer* and assembles its inventory inline.

hamsci-physics is a **meta-client** (§16.3.1): its data plane is files
another sigmond-managed client has already spooled to disk.  hf-timestd
records the IQ and writes the L1/L2 products; this client reads them and
produces ionospheric science.  Three consequences the contract spells
out:

* ``data_path.kind`` is ``"file"`` with ``details.upstream_client``,
  not ``"other"`` — the presence of ``upstream_client`` is exactly how
  sigmond distinguishes a meta-client from replay/archive data.
* the radiod-side fields (``radiod_id``, ``data_destination``,
  ``chain_delay_ns_applied``) MUST be omitted (§16.5).  They are not
  unknown, they are *somewhere else*: on hf-timestd's inventory, where
  the radiod relationship actually exists.  Reporting them here would
  duplicate a fact that can then drift.
* §2, §6, §7 and §8 do not apply.
"""

from __future__ import annotations

import logging
import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from hamsci_physics.version import GIT_INFO

CONTRACT_VERSION = "0.8"

#: The sibling client that records what this one consumes.
UPSTREAM_CLIENT = "hf-timestd"

#: Frozen by the 2026-08-24 split: hamsci-physics reads and writes inside
#: hf-timestd's data root rather than owning one.
DEFAULT_DATA_ROOT = "/var/lib/timestd"

DEPLOY_TOML_PATH = "/opt/git/sigmond/hamsci-physics/deploy.toml"

#: The GNSS VTEC service's own file.  hamsci-physics-vtec.service runs only
#: where it exists, so the VTEC sink is declared only there.
GNSS_VTEC_TOML = Path("/etc/hamsci-physics/gnss-vtec.toml")


def _version() -> str:
    try:
        return pkg_version("hamsci-physics")
    except PackageNotFoundError:
        from hamsci_physics import __version__
        return __version__


def _data_root(config: dict) -> Path:
    return Path((config.get("paths", {}) or {}).get(
        "data_root", DEFAULT_DATA_ROOT))


def _gnss_vtec_enabled() -> bool:
    try:
        with open(GNSS_VTEC_TOML, "rb") as fh:
            section = tomllib.load(fh).get("gnss_vtec") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return section.get("enabled") is True


def _data_sinks(config: dict, data_root: Path) -> list[dict[str, Any]]:
    """§17 output sinks.

    The L3 products are rows in the shared SQLite data-product store, so
    they are ``service`` sinks addressed ``<database>.<table>``; the
    GRAPE datasets are a file spool.  Volumes are best-effort figures
    for ``[disk_budget]``, measured on AC0G-B4.
    """
    db = "timestd"
    sinks: list[dict[str, Any]] = [
        {"kind": "service", "target": f"{db}.L3_dtec",
         "schema_ref": f"{db}:1", "retention_days": 365, "mb_per_day": 40},
        {"kind": "service", "target": f"{db}.L3_dtec_diff",
         "schema_ref": f"{db}:1", "retention_days": 365, "mb_per_day": 10},
        {"kind": "service", "target": f"{db}.L3_dtec_timeseries",
         "schema_ref": f"{db}:1", "retention_days": 365, "mb_per_day": 10},
        {"kind": "service", "target": f"{db}.L3_tec",
         "schema_ref": f"{db}:1", "retention_days": 365, "mb_per_day": 5},
        {"kind": "service", "target": f"{db}.L3_tid",
         "schema_ref": f"{db}:1", "retention_days": 365, "mb_per_day": 1},
        {"kind": "service", "target": f"{db}.L3C_propagation_stats",
         "schema_ref": f"{db}:1", "retention_days": 365, "mb_per_day": 1},
    ]
    if _gnss_vtec_enabled():
        # hamsci-physics-vtec.service: one row per VTEC epoch.
        sinks.append({"kind": "service", "target": f"{db}.L3_gnss_vtec",
                      "schema_ref": f"{db}:1", "retention_days": 30,
                      "mb_per_day": 5})
    if (config.get("grape", {}) or {}).get("enabled", True):
        sinks.append({
            "kind": "file",
            "target": str(data_root / "upload"),
            "schema_ref": None,
            # KEEP retention: the GRAPE spool is the hs-uploader source
            # and datasets stay after shipping.
            "retention_days": 30,
            "mb_per_day": 350,
        })
    return sinks


def build_inventory(config: dict, config_path: Path) -> dict:
    """Build the ``inventory --json`` payload (contract v0.8 §3, §16, §17).

    MUST exit 0 even on degraded paths, so every lookup here tolerates a
    missing or partial config and reports the gap through ``issues``.
    """
    data_root = _data_root(config)
    station = config.get("station", {}) or {}

    instance: dict[str, Any] = {
        "instance": "default",
        "host": "localhost",
        # §16.3.1 — meta-client: the data is files a sibling spooled.
        "data_path": {
            "kind": "file",
            "details": {
                "upstream_client": UPSTREAM_CLIENT,
                "upstream_unit": "timestd-core-recorder.service",
                "spool": str(data_root / "raw_buffer"),
                "products": str(data_root / "phase2"),
                "description": (
                    "L1/L2 data products and raw IQ recorded by hf-timestd; "
                    "read in place — the /var/lib/timestd root is frozen by "
                    "the 2026-08-24 split"
                ),
            },
        },
        "data_sinks": _data_sinks(config, data_root),
        "uses_timing_calibration": False,
        "provides_timing_calibration": False,
        # §18: this client reads products that hf-timestd already
        # labelled; it does not subscribe to the authority itself.
        "timing_authority_applied": None,
        "deploy_toml_path": DEPLOY_TOML_PATH,
        "station": {
            "callsign": station.get("callsign", ""),
            "grid_square": station.get("grid_square", ""),
            "psws_station_id": station.get("psws_station_id", ""),
            "instrument_id": station.get("instrument_id", ""),
        },
    }
    # NOTE: radiod_id / data_destination / chain_delay_ns_applied are
    # deliberately absent — §16.5 MUST omit.  See the module docstring.

    payload: dict[str, Any] = {
        "client": "hamsci-physics",
        "version": _version(),
        "contract_version": CONTRACT_VERSION,
        "config_path": str(config_path),
    }
    if GIT_INFO:
        payload["git"] = GIT_INFO

    payload["log_level"] = logging.getLevelName(
        logging.getLogger().getEffectiveLevel())
    payload["instances"] = [instance]
    payload["deps"] = {
        "git": [
            {"name": "hamsci-dsp",
             "note": "shared engines + io/schemas data layer"},
            {"name": "hs-uploader",
             "note": "GRAPE→PSWS transport"},
            {"name": UPSTREAM_CLIENT,
             "note": "records the products this client consumes"},
        ],
        "pypi": [
            {"name": "hamsci-dsp", "version": ">=0.5.0"},
            {"name": "digital_rf", "version": ">=2.6.0"},
        ],
    }
    payload["issues"] = collect_issues(config, config_path)
    return payload


def build_validate(config: dict, config_path: Path | None = None) -> dict:
    """Build the ``validate --json`` payload (§3, §12.3)."""
    issues = collect_issues(config, config_path)
    payload: dict[str, Any] = {
        "ok": not any(i["severity"] == "fail" for i in issues),
    }
    if config_path is not None:
        payload["config_path"] = str(config_path)   # §12.3
    payload["issues"] = issues
    return payload


def collect_issues(config: dict, config_path: Path | None = None) -> list[dict]:
    """Validation checks shared by inventory and validate."""
    issues: list[dict] = []

    if not config:
        issues.append({
            "severity": "fail",
            "instance": "all",
            "message": (
                f"no configuration loaded from {config_path} — run "
                f"`smd config init hamsci-physics`"
            ),
        })
        return issues

    station = config.get("station", {}) or {}
    for key in ("callsign", "grid_square"):
        if not station.get(key):
            issues.append({
                "severity": "fail",
                "instance": "default",
                "message": f"station.{key} is empty",
            })
    if not station.get("psws_station_id"):
        issues.append({
            "severity": "warn",
            "instance": "default",
            "message": ("station.psws_station_id is empty — the science "
                        "still runs locally, GRAPE upload is skipped"),
        })

    data_root = _data_root(config)
    if not data_root.exists():
        issues.append({
            "severity": "fail",
            "instance": "default",
            "message": (
                f"data_root {data_root} does not exist — this client reads "
                f"{UPSTREAM_CLIENT}'s products in place; is it installed on "
                f"this host?"
            ),
        })
    elif not (data_root / "phase2").exists():
        issues.append({
            "severity": "warn",
            "instance": "default",
            "message": (
                f"{data_root / 'phase2'} not present — no L2 products to "
                f"read yet; expected once {UPSTREAM_CLIENT} has run"
            ),
        })

    return issues
