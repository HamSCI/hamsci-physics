"""`grape daily` must take the station's identity from the config, or stop.

Regression cover for a hardcoded default that published one station's
location under another's name.  `cli.py` read

    callsign = station.get('callsign', 'AC0G')
    grid     = station.get('grid_square', 'EM38ww')

— a single real station (AC0G at EM38ww, Missouri) standing as the
default for every station running this code.  The GRAPE packager builds
its directory as `<callsign>_<grid_square>`, writes both into the
dataset metadata, and derives the receiver latitude/longitude from the
grid, so a config that merely omitted the keys published somebody
else's coordinates in silence.

Measured consequence of the same value arriving the other way (set, but
wrong): AC0G-ND packaged 14 days as `AC0G_EM38ww`, 1,100 km from its
antenna, 2026-10-07 .. 2026-10-27.  A MISSING key would have done it
just as quietly.  contract.py already grades both as "fail" when empty.
"""
import ast
import inspect
import pytest

from hamsci_physics import cli
from hamsci_physics.cli import _station_identity


GOOD = {'station': {'callsign': 'N0CALL', 'grid_square': 'EN16ov'}}


def test_the_configured_identity_is_returned_verbatim():
    assert _station_identity(GOOD, 'c.toml') == ('N0CALL', 'EN16ov')


def test_surrounding_whitespace_is_stripped_not_treated_as_a_value():
    cfg = {'station': {'callsign': ' N0CALL ', 'grid_square': ' EN16ov '}}
    assert _station_identity(cfg, 'c.toml') == ('N0CALL', 'EN16ov')


@pytest.mark.parametrize('station', [
    {},
    {'callsign': 'N0CALL'},                              # no grid
    {'grid_square': 'EN16ov'},                           # no callsign
    {'callsign': 'N0CALL', 'grid_square': ''},           # empty grid
    {'callsign': '', 'grid_square': 'EN16ov'},           # empty callsign
    {'callsign': 'N0CALL', 'grid_square': '   '},        # whitespace grid
    {'callsign': 'N0CALL', 'grid_square': None},         # null grid
])
def test_an_ABSENT_OR_EMPTY_identity_STOPS_the_pipeline(station):
    """Never substitute. A GRAPE day not packaged is recoverable; a day
    packaged under the wrong station's name is published and gone."""
    with pytest.raises(SystemExit) as exc:
        _station_identity({'station': station}, 'c.toml')
    assert exc.value.code != 0


def test_a_missing_station_TABLE_stops_too():
    with pytest.raises(SystemExit):
        _station_identity({}, 'c.toml')
    with pytest.raises(SystemExit):
        _station_identity({'station': None}, 'c.toml')


def test_the_refusal_NAMES_the_key_and_the_file(capsys):
    with pytest.raises(SystemExit):
        _station_identity({'station': {'callsign': 'N0CALL'}},
                          '/etc/hamsci-physics/config.toml')
    out = capsys.readouterr().out
    assert 'station.grid_square' in out
    assert '/etc/hamsci-physics/config.toml' in out
    assert 'station.callsign' not in out      # don't blame the key that was fine


def test_BOTH_missing_keys_are_named_in_ONE_message(capsys):
    with pytest.raises(SystemExit):
        _station_identity({'station': {}}, 'c.toml')
    out = capsys.readouterr().out
    assert 'station.callsign' in out and 'station.grid_square' in out


def test_NO_STATION_IDENTITY_IS_HARDCODED_AS_A_DEFAULT_ANYWHERE_IN_THE_CLI():
    """The guard itself is only as good as the absence of a second path
    around it.  Walk the AST: no `.get('callsign'|'grid_square', <non-empty>)`
    may exist in this module again, by any author, for any reason."""
    tree = ast.parse(inspect.getsource(cli))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == 'get'):
            continue
        if len(node.args) != 2:
            continue
        key, default = node.args
        if not (isinstance(key, ast.Constant)
                and key.value in ('callsign', 'grid_square')):
            continue
        if isinstance(default, ast.Constant) and default.value in ('', None):
            continue
        offenders.append((getattr(node, 'lineno', '?'), ast.dump(default)))
    assert not offenders, (
        "a station identity is being substituted at cli.py line(s) "
        + repr(offenders))


def test_the_grape_daily_path_CALLS_the_guard():
    """A guard nothing calls is a comment."""
    src = inspect.getsource(cli.main)
    assert '_station_identity(' in src
