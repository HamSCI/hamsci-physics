import io
import math

import pytest

from hamsci_dsp.geometry import grid_to_latlon
from hamsci_physics.gnss.broadcast_ephemeris import ecef_to_geodetic
from hamsci_physics.gnss.station import station_ecef
from hamsci_physics.gnss import vtec_service as vs

GRID = "FN31pr"
LAT, LON = grid_to_latlon(GRID)


def _deg(ecef):
    lat, lon, _ = ecef_to_geodetic(*ecef)
    return math.degrees(lat), math.degrees(lon)


def test_config_position_wins_over_environment():
    pos = station_ecef({"station": {"latitude": LAT, "longitude": LON}},
                       {"STATION_LAT": "10.0", "STATION_LON": "10.0"})
    assert _deg(pos) == pytest.approx((LAT, LON), abs=1e-6)


def test_environment_position_when_config_has_none():
    pos = station_ecef({}, {"STATION_LAT": str(LAT), "STATION_LON": str(LON)})
    assert _deg(pos) == pytest.approx((LAT, LON), abs=1e-6)


def test_template_zeroes_fall_to_the_grid_centre():
    pos = station_ecef({"station": {"latitude": 0.0, "longitude": 0.0}}, {"STATION_GRID": GRID})
    assert _deg(pos) == pytest.approx((LAT, LON), abs=1e-6)


def test_placeholder_config_grid_does_not_hide_the_environment_grid():
    pos = station_ecef({"station": {"grid_square": "<YOUR_GRID>"}}, {"STATION_GRID": GRID})
    assert _deg(pos) == pytest.approx((LAT, LON), abs=1e-6)


@pytest.mark.parametrize("cfg,env", [({}, {}), ({"station": {"grid_square": "<YOUR_GRID>"}}, {}),
                                      ({}, {"STATION_LAT": "95", "STATION_LON": "0"})])
def test_no_usable_position_is_none(cfg, env):
    assert station_ecef(cfg, env) is None


def test_load_config_takes_each_table_from_its_file(tmp_path):
    g = tmp_path / "gnss-vtec.toml"
    m = tmp_path / "config.toml"
    g.write_text('[gnss_vtec]\nenabled = true\nhost = "192.0.2.10"\n[station]\nlatitude = 1.5\n')
    m.write_text('[station]\nlatitude = 9.0\ngrid_square = "FN31"\n[storage]\nsqlite_path = "/x.db"\n')
    c = vs.load_config(g, m)
    assert c["gnss_vtec"]["host"] == "192.0.2.10"
    assert c["station"] == {"latitude": 1.5, "grid_square": "FN31"}
    assert c["storage"]["sqlite_path"] == "/x.db"


def test_missing_files_read_empty(tmp_path):
    assert vs.load_config(tmp_path / "a", tmp_path / "b")["gnss_vtec"] == {}


def test_no_csv_means_no_stdout(capsys):
    vs.write_epoch_line("1,2,3\n", None)
    assert capsys.readouterr().out == ""


def test_csv_gets_the_line():
    f = io.StringIO()
    vs.write_epoch_line("1,2,3\n", f)
    assert f.getvalue() == "1,2,3\n"
