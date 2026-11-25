import tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
UNIT = (ROOT / "systemd" / "hamsci-physics-vtec.service").read_text()

def test_unit_gates_on_the_config_file():
    assert "ConditionPathExists=/etc/hamsci-physics/gnss-vtec.toml" in UNIT

def test_unit_runs_the_module_as_timestd_where_the_data_lives():
    for line in ("User=timestd", "SupplementaryGroups=dialout", "WorkingDirectory=/var/lib/timestd",
                 "Environment=NETRC=/etc/hamsci-physics/earthdata-netrc",
                 "EnvironmentFile=-/etc/sigmond/coordination.env", "Type=notify", "WatchdogSec=60",
                 "-m hamsci_physics.gnss.vtec_service"):
        assert line in UNIT, line

def test_deploy_declares_the_unit_and_its_move():
    d = tomllib.loads((ROOT / "deploy.toml").read_text())["systemd"]
    assert d["units"][0] == "hamsci-physics-fusion.service"      # main service stays first
    assert "hamsci-physics-vtec.service" in d["units"]
    assert d["moves"] == [{"from": ["timestd-vtec.service"], "to": "hamsci-physics-vtec.service",
                           "migrate": ["/opt/git/sigmond/hamsci-physics/venv/bin/hamsci-physics",
                                       "vtec", "adopt"]}]


def test_hamsci_physics_ships_no_timing_inputs():
    names = {p.name for p in (ROOT / "systemd").iterdir()}
    for gone in ("hamsci-physics-ionex-download.timer", "hamsci-physics-iri-update.timer",
                 "hamsci-physics-iri-healthcheck.timer", "hamsci-physics-spaceweather-healthcheck.timer"):
        assert gone not in names, gone
    units = tomllib.loads((ROOT / "deploy.toml").read_text())["systemd"]["units"]
    assert not any("ionex" in u or "iri-" in u for u in units)
