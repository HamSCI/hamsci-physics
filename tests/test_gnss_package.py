import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_gnss_modules_import_from_hamsci_physics():
    from hamsci_physics.gnss.rtcm3_adapter import RTCM3Adapter, plausible_ecef
    from hamsci_physics.gnss.gnss_source import make_source
    from hamsci_physics.gnss.gnss_tec import GNSSTECAnalyzer
    from hamsci_physics.gnss.broadcast_ephemeris import ecef_to_geodetic
    assert RTCM3Adapter.__module__ == "hamsci_physics.gnss.rtcm3_adapter"
    ok, _ = GNSSTECAnalyzer.self_test()
    assert ok

def test_pyproject_declares_the_receiver_libraries():
    deps = " ".join(tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"])
    for name in ("pyserial", "pyubx2", "pyrtcm"):
        assert name in deps, name
