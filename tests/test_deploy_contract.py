"""The deploy manifest is a cross-repo contract; pin what must not drift.

Two things here are load-bearing beyond this repo:

* the GRAPE upload pipeline's ``source_id`` and transport ``name`` are the
  hs-uploader watermark keys for a KEEP-retention spool.  If they change,
  the daemon re-scans /var/lib/timestd/upload and re-ships every OBS*
  dataset already delivered to PSWS.
* ``grape-daily`` keeps its unit name across the split (operator runbooks),
  while the service units take the hamsci-physics- prefix.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def deploy():
    with open(ROOT / "deploy.toml", "rb") as fh:
        return tomllib.load(fh)


def test_package_identity(deploy):
    assert deploy["package"]["name"] == "hamsci-physics"


def test_grape_upload_watermark_keys_are_pinned(deploy):
    pipes = deploy["hs_uploader"]["pipeline"]
    grape = next(p for p in pipes if p["name"] == "grape-psws")
    # Byte-identical to hf-timestd's pre-split block.
    assert grape["source"]["source_id"] == "grape-datasets:/var/lib/timestd/upload"
    assert grape["transport"]["name"] == \
        "psws-grape-sftp:pswsnetwork.eng.ua.edu:{station_id}"
    assert grape["source"]["root"] == "/var/lib/timestd/upload"
    assert grape["source"]["retention"] == "keep"      # datasets stay local
    assert grape["source"]["match_dirs"] is True       # DRF dataset is a tree
    assert grape["transport"]["table"] == "grape.dataset"


def test_grape_daily_keeps_its_operator_facing_name(deploy):
    units = deploy["systemd"]["units"]
    assert "grape-daily.timer" in units
    assert not any(u.startswith("hamsci-physics-grape") for u in units)


def test_science_units_are_namespaced_and_shipped(deploy):
    shipped = {p.name for p in (ROOT / "systemd").glob("*")}
    for unit in deploy["systemd"]["units"]:
        assert unit in shipped, f"{unit} declared but not shipped"


def test_the_fusion_service_is_enabled():
    """The L3 fusion stage must be in the units list.

    hf-timestd's deploy.toml carried a comment calling this stage
    "DISABLED 2026-08-10 ... produces nothing trustworthy", and the first
    cut of this manifest believed it.  B4 says otherwise: the service runs
    and writes ~14 carrier-phase dTEC records a minute plus differential
    dTEC for WWV/WWVH/BPM, all through the SQLite store.  Omitting it here
    would have made the split's cutover silently stop live science.
    """
    with open(ROOT / "deploy.toml", "rb") as fh:
        deploy = tomllib.load(fh)
    assert "hamsci-physics-fusion.service" in deploy["systemd"]["units"]


def test_no_unit_still_points_at_the_hf_timestd_venv():
    offenders = []
    for unit in (ROOT / "systemd").glob("*"):
        text = unit.read_text()
        if "/opt/git/sigmond/hf-timestd" in text or "hf_timestd" in text:
            offenders.append(unit.name)
    assert not offenders, offenders


def test_config_template_matches_the_frozen_data_root():
    with open(ROOT / "config" / "hamsci-physics-config.toml.template", "rb") as fh:
        cfg = tomllib.load(fh)
    assert cfg["paths"]["data_root"] == "/var/lib/timestd"
    assert set(cfg["station"]) >= {
        "callsign", "grid_square", "psws_station_id", "instrument_id"}


def test_watchdog_units_have_the_binding_that_feeds_them():
    """A unit with WatchdogSec needs sd_notify, which needs systemd-python.

    Shipping the unit without the dependency means systemd kills the
    service every WatchdogSec — seen on the first AC0G-B4 deployment,
    where the fusion service was killed every 120 s and never committed a
    minute's dTEC.
    """
    import tomllib

    watchdog_units = [
        u.name for u in (ROOT / "systemd").glob("*.service")
        if "WatchdogSec=" in u.read_text()
    ]
    if not watchdog_units:
        pytest.skip("no unit uses the systemd watchdog")

    with open(ROOT / "pyproject.toml", "rb") as fh:
        deps = tomllib.load(fh)["project"]["dependencies"]
    assert any(d.startswith("systemd-python") for d in deps), (
        f"{watchdog_units} use WatchdogSec but systemd-python is not a "
        f"dependency — systemd will kill them on schedule")


def test_no_second_uploader_ships():
    """hs-uploader owns GRAPE delivery.  The retired retry units and the
    per-station key script (one key per uploading machine, mjh 2026-10-01)
    are gone, not merely disabled."""
    for gone in ("systemd/grape-upload-retry.service",
                 "systemd/grape-upload-retry.timer",
                 "scripts/setup-psws-keys.sh"):
        assert not (ROOT / gone).exists(), gone
