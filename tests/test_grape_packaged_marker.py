"""GRAPE marks a day packaged, so the catch-up sweep never re-packages it.

Nothing wrote upload/<day>/.upload_complete after hf-timestd af45a6a
(2026-06-30) retired SFTPUpload.  The sweep then re-packaged days 2-7 back
every night, and each re-package moved the OBS mtime past the send record,
so hs-uploader re-sent the day (sigmond tasks/plan-sink-control.md §10.3).
"""
import ast
import inspect
from pathlib import Path

from hamsci_physics import cli
from hamsci_physics.grape.spool import (
    PACKAGED_MARKER, day_needs_retry, mark_day_packaged,
)


def _source(root: Path, day: str) -> None:
    (root / "raw_buffer" / "WWV_10" / day).mkdir(parents=True)


def test_mark_day_packaged_writes_the_marker_and_repeats_safely(tmp_path):
    day_dir = tmp_path / "upload" / "20261001"
    first = mark_day_packaged(day_dir)
    second = mark_day_packaged(day_dir)
    # sigmond sink_doors (Task 4) writes this name by literal; keep them equal.
    assert first == second == day_dir / ".upload_complete"
    assert first.is_file()


def test_an_unmarked_day_with_source_needs_a_retry(tmp_path):
    _source(tmp_path, "20261001")
    assert day_needs_retry(tmp_path, "20261001")


def test_a_marked_day_is_never_retried(tmp_path):
    _source(tmp_path, "20261001")
    mark_day_packaged(tmp_path / "upload" / "20261001")
    assert not day_needs_retry(tmp_path, "20261001")


def test_a_day_with_only_decimated_source_needs_a_retry(tmp_path):
    dec = tmp_path / "products" / "WWV_10" / "decimated"
    dec.mkdir(parents=True)
    (dec / "20261001.bin").write_bytes(b"")
    assert day_needs_retry(tmp_path, "20261001")


def test_a_day_without_source_is_not_retried(tmp_path):
    assert not day_needs_retry(tmp_path, "20261001")


def test_a_marker_deeper_in_the_day_still_counts(tmp_path):
    _source(tmp_path, "20261001")
    mark_day_packaged(tmp_path / "upload" / "20261001" / "AC0G_EM38ww")
    assert not day_needs_retry(tmp_path, "20261001")


def _obs(root: Path, day: str) -> Path:
    obs = root / "upload" / day / "AC0G_EM38ww" / "OBS2026-10-01T00-00"
    (obs / "ch0").mkdir(parents=True)
    return obs


def test_a_day_packaged_before_the_marker_existed_is_not_retried(tmp_path):
    # Existing stations hold finished OBS* datasets with no marker.  The
    # packager writes gap_summary.json last, so its presence shows the
    # dataset finished, and the first night after deploy re-sends nothing.
    _source(tmp_path, "20261001")
    obs = _obs(tmp_path, "20261001")
    (obs / "gap_summary.json").write_text('{"date": "2026-10-01"}')
    assert not day_needs_retry(tmp_path, "20261001")


def test_a_package_that_crashed_mid_write_needs_a_retry(tmp_path):
    # package_day creates OBS.../ch0 before it writes the Digital RF data.  A
    # crash mid-write leaves that directory, no gap_summary.json and no
    # marker: the crash the sweep exists to repair (B4 20260731, 20260803).
    _source(tmp_path, "20261001")
    _obs(tmp_path, "20261001")
    assert day_needs_retry(tmp_path, "20261001")


def test_a_crashed_package_with_the_marker_is_not_retried(tmp_path):
    # The marker wins: sigmond's sink doors write it on purpose.
    _source(tmp_path, "20261001")
    _obs(tmp_path, "20261001")
    mark_day_packaged(tmp_path / "upload" / "20261001")
    assert not day_needs_retry(tmp_path, "20261001")


def test_the_packager_writes_gap_summary_last():
    # day_needs_retry trusts gap_summary.json as the finished-dataset sign
    # only while package_day writes it after the data and the metadata.
    from hamsci_physics.grape import packager
    body = inspect.getsource(packager.DailyDRFPackager.package_day)
    drf = body.index("self._write_drf(")
    meta = body.index("self._write_metadata(")
    gaps = body.index("self._write_gap_summary(")
    assert drf < meta < gaps
    assert "self._write_" not in body[gaps + 1:]


def test_the_marker_never_looks_like_a_dataset(tmp_path):
    # grape-psws ships OBS* directories; the marker must never match.
    marker = mark_day_packaged(tmp_path / "upload" / "20261001")
    assert not marker.name.startswith("OBS")


def test_grape_daily_marks_the_day_after_gate_3_and_the_sweep_uses_the_test():
    src = inspect.getsource(cli)
    gate = src.index("GATE PASSED: {len(obs_dirs)} dataset(s) ready")
    window = [line.strip() for line in src[gate:gate + 600].splitlines()]
    assert "mark_day_packaged(upload_dir)" in window
    assert "if not day_needs_retry(data_root, d):" in [
        line.strip() for line in src.splitlines()]


def test_a_marker_that_cannot_be_written_never_fails_the_product():
    # An OSError writing the marker costs one sweep retry.  Unguarded, it
    # would exit grape-daily before Stage 4/5: no status save, no cleanup,
    # no sweep.
    guarded = [
        call
        for node in ast.walk(ast.parse(inspect.getsource(cli)))
        if isinstance(node, ast.Try)
        and any(isinstance(h.type, ast.Name) and h.type.id == "OSError"
                for h in node.handlers)
        for stmt in node.body
        for call in ast.walk(stmt)
        if isinstance(call, ast.Call)
        and getattr(call.func, "id", None) == "mark_day_packaged"
    ]
    assert guarded, "mark_day_packaged(upload_dir) must sit inside try/except OSError"
