"""Pin the Digital RF that PSWS receives; gap_summary.json names the decimator.

Fixture identity is a grid square only (N0CALL, EM38).  The packager derives
lat/long from the grid centre itself; the test supplies no position.
"""
import json

import digital_rf
import numpy as np
import pytest

from hamsci_physics.grape.decimated_buffer import DecimatedBuffer, SAMPLES_PER_MINUTE
from hamsci_physics.grape.decimation import StatefulDecimator
from hamsci_physics.grape.packager import DailyDRFPackager, StationConfig

DAY = "20260904"
T0 = 1_788_480_000.0      # 2026-09-04T00:00:00Z
CHANNELS = [("SHARED 5000", 5e6), ("SHARED 10000", 10e6)]


@pytest.fixture
def packaged(tmp_path):
    record = StatefulDecimator(24000, 10).describe()
    for name, _ in CHANNELS:
        buf = DecimatedBuffer(tmp_path, name)
        for m in range(3):
            buf.write_minute(minute_utc=T0 + 60 * m,
                             decimated_iq=np.ones(SAMPLES_PER_MINUTE, np.complex64),
                             filter_edge=False)
        buf.set_decimator(DAY, record)
        buf.flush_metadata()
    pkg = DailyDRFPackager(tmp_path, StationConfig(callsign="N0CALL", grid_square="EM38"),
                           channels=CHANNELS)
    return pkg.package_day(DAY)


def test_drf_metadata_keys_are_unchanged(packaged):
    md = digital_rf.DigitalMetadataReader(str(packaged / "ch0" / "metadata"))
    lo, hi = md.get_bounds()
    (_, rec), = md.read(lo, hi + 1).items()
    assert sorted(rec) == ["callsign", "center_frequencies", "grid_square", "lat",
                           "long", "receiver_name", "uuid_str"]


def test_drf_layout_is_unchanged(packaged):
    r = digital_rf.DigitalRFReader(str(packaged))
    lo, hi = r.get_bounds("ch0")
    assert lo == int(T0) * 10 and hi - lo + 1 == 864_000
    p = r.get_properties("ch0")
    assert p["samples_per_second"] == 10 and p["is_complex"] and p["num_subchannels"] == 2


def test_gap_summary_names_the_decimator(packaged):
    s = json.loads((packaged / "gap_summary.json").read_text())
    for ch in ("SHARED 5000", "SHARED 10000"):
        assert s["channels"][ch]["decimator"]["version"] == 2
