"""The sigmond client-contract surface: version / inventory / validate.

sigmond learns about a client by running these as a subprocess and parsing
JSON — never by importing it — so the shapes and the exit codes are the
contract, and they are what this module pins.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")


def run(*argv, expect=None):
    proc = subprocess.run(
        [sys.executable, "-m", "hamsci_physics.cli", *argv],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": SRC},
    )
    if expect is not None:
        assert proc.returncode == expect, proc.stderr[-800:]
    return proc


def _config(tmp_path, **station):
    cfg = tmp_path / "config.toml"
    body = ["[station]"]
    for k, v in station.items():
        body.append(f'{k} = "{v}"')
    body += ["", "[paths]", f'data_root = "{tmp_path}"']
    cfg.write_text("\n".join(body) + "\n")
    return cfg


def test_version_json_names_the_component():
    doc = json.loads(run("version", "--json", expect=0).stdout)
    assert doc["name"] == "hamsci-physics"
    assert doc["version"]


def test_inventory_declares_a_meta_client_reading_the_frozen_root(tmp_path):
    cfg = _config(tmp_path, callsign="AC0G", grid_square="EM38ww",
                  psws_station_id="S000170", instrument_id="171")
    doc = json.loads(run("inventory", "-c", str(cfg), expect=0).stdout)

    assert doc["client"] == "hamsci-physics"
    inst = doc["instances"][0]
    # §16.3.1: a meta-client reads what a sibling client spooled.
    assert inst["data_path"]["kind"] == "file"
    assert inst["data_path"]["details"]["upstream_client"] == "hf-timestd"
    assert inst["station"]["psws_station_id"] == "S000170"
    # It consumes the timing core's products; it never produces timing.
    assert inst["provides_timing_calibration"] is False
    assert str(tmp_path) in inst["data_path"]["details"]["products"]


def test_validate_passes_on_a_complete_config(tmp_path):
    cfg = _config(tmp_path, callsign="AC0G", grid_square="EM38ww",
                  psws_station_id="S000170")
    doc = json.loads(run("validate", "-c", str(cfg), expect=0).stdout)
    assert doc["ok"] is True
    assert not [i for i in doc["issues"] if i["severity"] == "fail"]


def test_validate_fails_without_identity(tmp_path):
    cfg = _config(tmp_path, callsign="", grid_square="")
    doc = json.loads(run("validate", "-c", str(cfg), expect=1).stdout)
    assert doc["ok"] is False
    msgs = " ".join(i["message"] for i in doc["issues"])
    assert "station.callsign" in msgs and "station.grid_square" in msgs


def test_validate_warns_but_passes_without_a_psws_id(tmp_path):
    """No PSWS id is a legitimate state — the science still runs locally,
    only the upload is skipped — so it must warn, never fail."""
    cfg = _config(tmp_path, callsign="AC0G", grid_square="EM38ww")
    doc = json.loads(run("validate", "-c", str(cfg), expect=0).stdout)
    assert doc["ok"] is True
    assert any(i["severity"] == "warn" and "psws_station_id" in i["message"]
               for i in doc["issues"])


def test_validate_fails_when_the_timing_data_root_is_absent(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[station]\ncallsign = "AC0G"\ngrid_square = "EM38ww"\n'
                   '\n[paths]\ndata_root = "/nonexistent/timestd"\n')
    doc = json.loads(run("validate", "-c", str(cfg), expect=1).stdout)
    assert any("does not exist" in i["message"] for i in doc["issues"])


def test_missing_config_is_a_validate_failure_not_a_crash(tmp_path):
    doc = json.loads(run("validate", "-c", str(tmp_path / "nope.toml"),
                         expect=1).stdout)
    assert doc["ok"] is False


@pytest.mark.parametrize("sub", ["daily", "decimate", "spectrogram",
                                 "package", "status"])
def test_grape_subcommands_are_present(sub):
    out = run("grape", "--help", expect=0).stdout
    assert sub in out


def test_no_grape_upload_commands_remain():
    """hs-uploader is the only uploader.  `grape upload` only refused (and
    listed what `grape status` lists); `grape test-upload` probed with a key
    no upload uses, where `smd psws verify` probes with the machine's key."""
    out = run("grape", "--help", expect=0).stdout
    choices = re.search(r"\{([^}]*)\}", out).group(1).split(",")
    assert "upload" not in choices and "test-upload" not in choices, choices
