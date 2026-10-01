"""Every moved module must import cleanly in the new namespace.

The split rewrote hf_timestd.* imports to hamsci_dsp.* / hamsci_physics.*
mechanically; this is the check that the rewrite produced a package that
actually loads, rather than one that only looks right in a diff.
"""
from __future__ import annotations

import importlib

import pytest

MODULES = [
    "hamsci_physics",
    "hamsci_physics.constants",
    "hamsci_physics.solar_zenith",
    "hamsci_physics.tid_detector",
    "hamsci_physics.propagation_stats",
    "hamsci_physics.physics_fusion_service",
    "hamsci_physics.ionospheric_reanalysis",
    "hamsci_physics.grape.decimated_buffer",
    "hamsci_physics.grape.decimation",
    "hamsci_physics.grape.decimation_pipeline",
    "hamsci_physics.grape.raw_reader",
    "hamsci_physics.grape.packager",
    "hamsci_physics.grape.spectrogram",
    "hamsci_physics.cddis",
    "hamsci_physics.cddis_auth",
    "hamsci_physics.cli",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)


def test_constants_track_the_shared_catalog():
    from hamsci_dsp.stations import BUILTIN_CATALOG
    from hamsci_physics import constants as c

    assert c.WWV_FREQUENCIES == list(BUILTIN_CATALOG.get("WWV").frequencies_mhz)
    assert (c.WWV_LAT, c.WWV_LON) == (
        BUILTIN_CATALOG.get("WWV").lat, BUILTIN_CATALOG.get("WWV").lon)
    # Carried over byte-identical from hf-timestd — a change here silently
    # moves every reanalysis result, so it must be deliberate.
    assert c.EARTH_RADIUS_KM == 6371.0
    assert c.SPEED_OF_LIGHT_KM_S == 299792.458
    assert (c.E_LAYER_HEIGHT_KM, c.F_LAYER_HEIGHT_KM) == (110.0, 300.0)


def test_ionex_script_reexports_the_canonical_parser():
    """The standalone ionex_* scripts moved here with the CDDIS/IONEX stack.

    They must keep getting IONEXParser from the shared library rather than
    redefining it — the original defect (P-H18) was a second copy loaded by
    importlib on every cache miss.
    """
    import sys
    from pathlib import Path

    from hamsci_dsp.ionosphere.ionex import IONEXParser

    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import ionex_integration
    assert ionex_integration.IONEXParser is IONEXParser


def test_the_in_process_upload_modules_are_gone():
    import importlib
    for mod in ("hamsci_physics.grape.uploader", "hamsci_physics.grape.hs_upload"):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"{mod} still exists: hs-uploader is the only uploader")
