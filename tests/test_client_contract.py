"""CLIENT-CONTRACT conformance (sigmond/docs/CLIENT-CONTRACT.md).

Sigmond's entire runtime view of a client is four things: the systemd
unit state, ``inventory --json``, ``validate --json``, and the §13
control surface.  It shells the two subcommands out as subprocesses and
never imports client code, so the JSON shape *is* the interface.

hamsci-physics is a **meta-client** in §16.3.1 terms: its data plane is
files another sigmond-managed client (hf-timestd) already spooled to
disk.  That fixes several answers — `data_path.kind` is `"file"` with
`details.upstream_client`, and the radiod-side fields MUST be absent
because the radiod relationship lives on the upstream's inventory, not
this one's.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def run(*argv):
    return subprocess.run(
        [sys.executable, "-m", "hamsci_physics.cli", *argv],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC},
    )


@pytest.fixture(scope="module")
def inventory():
    proc = run("inventory", "--json")
    assert proc.returncode == 0, proc.stderr[-500:]
    return json.loads(proc.stdout)


# ── §3 stdout cleanliness (hard requirement) ────────────────────────────

@pytest.mark.parametrize("sub", ["inventory", "validate", "version"])
def test_stdout_is_only_json(sub):
    """"A malformed first byte on stdout makes the whole inventory
    unparseable."  Banners, logging lines and progress dots belong on
    stderr; a client that configures a logger at import needs an explicit
    guard so "Logging configured" never lands in the JSON pipe."""
    proc = run(sub, "--json")
    json.loads(proc.stdout)                      # must parse whole
    assert proc.stdout.lstrip()[0] in "{["


def test_inventory_exits_zero_even_with_no_config(tmp_path):
    """§ diag_drop_in: "inventory MUST exit 0 even on degraded paths"."""
    proc = run("inventory", "-c", str(tmp_path / "absent.toml"))
    assert proc.returncode == 0, proc.stderr[-400:]
    doc = json.loads(proc.stdout)
    assert doc["issues"], "a missing config should be reported, not hidden"


# ── §3 top-level shape ──────────────────────────────────────────────────

def test_top_level_keys(inventory):
    assert inventory["client"] == "hamsci-physics"
    assert inventory["version"]
    assert inventory["contract_version"]
    assert "config_path" in inventory
    assert isinstance(inventory["instances"], list) and inventory["instances"]
    assert isinstance(inventory["issues"], list)


def test_contract_version_matches_the_deploy_manifest(inventory):
    """Two declarations of the same fact must not drift."""
    import tomllib
    with open(ROOT / "deploy.toml", "rb") as fh:
        deploy = tomllib.load(fh)
    assert inventory["contract_version"] == deploy["package"]["contract_version"]


def test_git_block_is_present_and_shaped(inventory):
    """Optional per the spec, but it is what lets `smd admin diag` answer
    "what's running?" without shelling into the client."""
    git = inventory.get("git")
    assert git is not None, "git block recommended by §3"
    assert set(git) >= {"sha", "short", "ref", "dirty"}
    assert isinstance(git["dirty"], bool)


# ── §16.3.1 meta-client data_path ───────────────────────────────────────

def test_data_path_declares_a_meta_client(inventory):
    inst = inventory["instances"][0]
    dp = inst["data_path"]
    assert dp["kind"] == "file", "data is spooled by a sibling client (§16.3.1)"
    details = dp["details"]
    assert details["upstream_client"] == "hf-timestd"
    assert details.get("spool"), "operator needs to find the upstream's data"


def test_radiod_fields_are_absent(inventory):
    """§16.5: a non-radiod client MUST omit these — they would mislead.
    The radiod facts live on hf-timestd's inventory."""
    inst = inventory["instances"][0]
    for forbidden in ("radiod_id", "data_destination", "chain_delay_ns_applied"):
        assert forbidden not in inst, f"{forbidden} must not appear (§16.5)"


def test_timing_calibration_flags_are_honest(inventory):
    inst = inventory["instances"][0]
    assert inst["provides_timing_calibration"] is False
    assert inst["uses_timing_calibration"] is False


# ── §17 data_sinks ──────────────────────────────────────────────────────

def test_data_sinks_describe_what_this_client_writes(inventory):
    sinks = inventory["instances"][0]["data_sinks"]
    assert sinks, "§17: a v0.6 client SHOULD report its output sinks"
    kinds = {s["kind"] for s in sinks}
    assert kinds <= {"file", "service"}
    for s in sinks:
        assert set(s) >= {"kind", "target", "schema_ref",
                          "retention_days", "mb_per_day"}
        if s["kind"] == "service":
            assert s["schema_ref"], "service sinks carry <db>:<schema_version>"
        else:
            assert s["schema_ref"] is None

    targets = {s["target"] for s in sinks}
    assert any("dtec" in t for t in targets), "the live L3 product is missing"


def test_deploy_toml_path_points_at_a_real_manifest(inventory):
    inst = inventory["instances"][0]
    assert inst["deploy_toml_path"].endswith("hamsci-physics/deploy.toml")


# ── §3 validate ─────────────────────────────────────────────────────────

def test_validate_shape(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   f'\n[paths]\ndata_root = "{tmp_path}"\n')
    proc = run("validate", "-c", str(cfg))
    doc = json.loads(proc.stdout)
    assert isinstance(doc["ok"], bool)
    for issue in doc["issues"]:
        assert set(issue) >= {"severity", "instance", "message"}
        assert issue["severity"] in {"fail", "warn", "info"}


# ── §17 the GNSS VTEC sink appears only where the service runs ──────────

def test_inventory_declares_gnss_vtec_only_where_it_runs(tmp_path, monkeypatch):
    from hamsci_physics import contract
    gnss = tmp_path / "gnss-vtec.toml"
    monkeypatch.setattr(contract, "GNSS_VTEC_TOML", gnss)

    def vtec_sinks():
        inv = contract.build_inventory({}, tmp_path / "config.toml")
        return [s for s in inv["instances"][0]["data_sinks"]
                if s["target"] == "timestd.L3_gnss_vtec"]

    assert vtec_sinks() == []
    gnss.write_text("[gnss_vtec]\nenabled = true\n")
    [sink] = vtec_sinks()
    assert sink["kind"] == "service" and sink["retention_days"] == 30
    assert sink["schema_ref"] == "timestd:1"
