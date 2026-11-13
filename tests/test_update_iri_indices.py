"""update-iri-indices.sh refreshes every IRI data directory it is given, from the first source that
serves a plausible file, and leaves a directory alone when every source fails.  Sources are local
file:// directories, so no network is touched."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "update-iri-indices.sh"


def _run(tmp_path, sources, dirs):
    env = dict(os.environ, IRI_SOURCES=" ".join(f"file://{s}" for s in sources),
               IRI_DAT_DIRS=" ".join(str(d) for d in dirs))
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _source(path, last_line):
    path.mkdir()
    for name in ("apf107.dat", "ig_rz.dat"):
        (path / name).write_text("x" * 2000 + "\n" + last_line + "\n")
    return path


def _stale(path):
    path.mkdir()
    for name in ("apf107.dat", "ig_rz.dat"):
        (path / name).write_text("23  6 29 stale\n")
    return path


def test_every_directory_gets_the_fresh_files(tmp_path):
    src = _source(tmp_path / "src", "26 11 12 fresh")
    pharlap, package = _stale(tmp_path / "pharlap"), _stale(tmp_path / "pkg")
    r = _run(tmp_path, [src], [pharlap, package])
    assert r.returncode == 0, r.stdout + r.stderr
    for d in (pharlap, package):
        for name in ("apf107.dat", "ig_rz.dat"):
            assert (d / name).read_text().splitlines()[-1] == "26 11 12 fresh"
            assert oct((d / name).stat().st_mode & 0o777) == "0o644"
        assert not list(d.glob(".*.new"))


def test_a_short_answer_falls_through_to_the_next_source(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    for name in ("apf107.dat", "ig_rz.dat"):
        (bad / name).write_text("404\n")
    good = _source(tmp_path / "good", "26 11 12 fallback")
    d = _stale(tmp_path / "pkg")
    r = _run(tmp_path, [bad, good], [d])
    assert r.returncode == 0, r.stdout + r.stderr
    assert (d / "apf107.dat").read_text().splitlines()[-1] == "26 11 12 fallback"


def test_when_every_source_fails_the_old_files_stay(tmp_path):
    d = _stale(tmp_path / "pkg")
    r = _run(tmp_path, [tmp_path / "missing"], [d])
    assert r.returncode != 0
    assert (d / "apf107.dat").read_text() == "23  6 29 stale\n"


def test_no_iri_directory_is_not_an_error(tmp_path):
    r = subprocess.run(["bash", "-c", f"IRI_DAT_DIRS='' bash {SCRIPT}"], capture_output=True,
                       text=True, env=dict(os.environ, IRI_SOURCES="file:///nonexistent"))
    # On the devbox no /opt/pharlap and no /opt/git/sigmond venvs exist, so discovery finds none.
    if not Path("/opt/git/sigmond").exists() and not Path("/opt/pharlap_4.7.4").exists():
        assert r.returncode == 0, r.stdout + r.stderr
        assert "nothing to update" in r.stdout
