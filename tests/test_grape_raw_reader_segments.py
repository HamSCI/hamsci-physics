"""RawBinaryReader joins a chunk's later segments, ``{chunk_ts}.seg<N>``.

A chunk reopened at its boundary (a recorder restart mid-chunk, a pair step
over 0.25 s) is written as a later segment, never over the first file.  The
reader used to skip any stem that was not all digits, so every minute after
the restart read as a gap.  On B4, 2026-11-23, the timing recorder restarted
eight times in one morning; the first file of each chunk sat on disk and its
segment in /dev/shm.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = str(REPO_ROOT / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from hamsci_physics.grape.chunk_segments import join_segments, parse_chunk_name  # noqa: E402
from hamsci_physics.grape.raw_reader import RawBinaryReader  # noqa: E402

RATE = 100          # small chunks; the reader takes the rate from the sidecar
CHUNK = 1745798400  # 2025-04-28 00:00Z, on a 10-minute boundary
DAY = "20250428"


def _write(day_dir: Path, stem: str, samples: np.ndarray, **side) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{stem}.bin").write_bytes(samples.astype(np.complex64).tobytes())
    sidecar = {"sample_rate": RATE, "file_duration_sec": 600,
               "samples_written": len(samples)}
    sidecar.update(side)
    (day_dir / f"{stem}.json").write_text(json.dumps(sidecar))


def _restart_at(n_first: int, n_gap: int):
    """A chunk whose recorder stopped after n_first samples and came back
    n_gap samples later: the first file, then a full-length segment 2."""
    total = 600 * RATE
    first = np.full(n_first, 1, np.complex64)
    seg2 = np.zeros(total, np.complex64)
    seg2[n_first + n_gap:] = 2
    return first, seg2


def _channel(root: Path) -> Path:
    return root / "raw_buffer" / "WWV_10000"


def test_minutes_after_a_restart_read_from_the_segment(tmp_path):
    first, seg2 = _restart_at(n_first=4 * 60 * RATE, n_gap=30 * RATE)
    day = _channel(tmp_path) / DAY
    _write(day, str(CHUNK), first, covered_runs=[[0, len(first)]])
    _write(day, f"{CHUNK}.seg2", seg2,
           covered_runs=[[int(np.flatnonzero(seg2)[0]), len(seg2)]])
    r = RawBinaryReader(tmp_path, "WWV 10000", hot_buffer_root=tmp_path / "no-hot")
    assert r.get_available_minutes(DAY) == [CHUNK + 60 * i for i in range(10)]
    early, _ = r.read_minute(CHUNK)
    assert np.all(early == 1)
    straddle, _ = r.read_minute(CHUNK + 240)       # restart gap, then segment 2
    assert np.all(straddle[:30 * RATE] == 0) and np.all(straddle[30 * RATE:] == 2)
    late, meta = r.read_minute(CHUNK + 540)
    assert late is not None and np.all(late == 2)
    assert [s["segment"] for s in meta["segments"]] == [1, 2]


def test_segments_in_two_tiers_are_joined(tmp_path):
    """Storage tiering moved the first file to disk; segment 2 is still in RAM."""
    first, seg2 = _restart_at(n_first=60 * RATE, n_gap=0)
    hot = tmp_path / "shm"
    _write(_channel(tmp_path) / DAY, str(CHUNK), first)
    _write(hot / "raw_buffer" / "WWV_10000" / DAY, f"{CHUNK}.seg2", seg2)
    r = RawBinaryReader(tmp_path, "WWV 10000", hot_buffer_root=hot)
    x, _ = r.read_minute(CHUNK)
    assert np.all(x == 1)
    x, _ = r.read_minute(CHUNK + 300)
    assert np.all(x == 2)


def test_a_lone_segment_is_read(tmp_path):
    _, seg2 = _restart_at(n_first=60 * RATE, n_gap=0)
    _write(_channel(tmp_path) / DAY, f"{CHUNK}.seg2", seg2)
    r = RawBinaryReader(tmp_path, "WWV 10000", hot_buffer_root=tmp_path / "no-hot")
    x, meta = r.read_minute(CHUNK + 300)
    assert np.all(x == 2)
    assert r.read_minute(CHUNK)[0] is not None    # the leading hole reads as zeros
    assert "segments" not in meta


def test_copy_matches_hf_timestd():
    """This module copies hf-timestd's chunk_segments; pin the behavior both carry."""
    assert parse_chunk_name("1795417200.seg2.bin.zst") == (1795417200, 2, ".bin.zst")
    assert parse_chunk_name("1795417200.json") == (1795417200, 1, ".json")
    a = np.full(6, 1, np.complex64)
    b = np.full(8, 2, np.complex64)
    x, meta = join_segments([
        (1, a, {"covered_runs": [[0, 6]], "start_rtp_timestamp": 10}),
        (2, b, {"covered_runs": [[4, 8]], "start_rtp_timestamp": 4}),
    ])
    np.testing.assert_array_equal(x, [1, 1, 1, 1, 1, 1, 2, 2])
    assert meta["segments"][1]["samples_refused"] == 2
    assert meta["segments"][1]["start_rtp_offset_samples"] == -6
