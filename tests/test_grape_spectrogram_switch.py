import inspect, sys, toml
from pathlib import Path
from hamsci_physics import cli

def test_spectrograms_switch_reads_only_a_true_boolean():
    assert cli._spectrograms_enabled({}) is False
    assert cli._spectrograms_enabled({"grape": {"spectrograms": "true"}}) is False
    assert cli._spectrograms_enabled({"grape": {"spectrograms": True}}) is True

def test_off_means_no_drawing_and_no_matplotlib(tmp_path):
    sys.modules.pop("hamsci_physics.grape.spectrogram", None)
    assert cli._run_spectrogram_stage({}, tmp_path, ["SHARED 5000"], "20260904", "EM38") == []
    assert "hamsci_physics.grape.spectrogram" not in sys.modules

def test_a_failing_spectrogram_never_escapes(tmp_path, monkeypatch):
    from hamsci_physics.grape import spectrogram
    class Boom:
        def __init__(self, **kw): pass
        def generate_daily(self, d): raise MemoryError("matplotlib")
    monkeypatch.setattr(spectrogram, "CarrierSpectrogramGenerator", Boom)
    cfg = {"grape": {"spectrograms": True}}
    assert cli._run_spectrogram_stage(cfg, tmp_path, ["SHARED 5000"], "20260904", "EM38") == []

def test_spectrograms_run_after_packaging_and_gate_nothing():
    src = inspect.getsource(cli)
    call = "drawn = _run_spectrogram_stage(config, data_root, decimated, date_str, grid)"
    assert src.index("packager.package_day(date_str)") < src.index(call) < src.index("Stage 5: Cleanup")
    assert "spectrograms generated" not in src          # Gate 2 and its message are gone
    assert "sys.exit" not in inspect.getsource(cli._run_spectrogram_stage)

def test_template_ships_spectrograms_off():
    t = toml.load(Path(cli.__file__).parents[2] / "config/hamsci-physics-config.toml.template")
    assert t["grape"]["spectrograms"] is False
