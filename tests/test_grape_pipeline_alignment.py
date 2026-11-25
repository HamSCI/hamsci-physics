import json
import numpy as np
from hamsci_physics.grape.decimated_buffer import DayMetadata, DecimatedBuffer, SAMPLES_PER_MINUTE

DAY = "20260904"; T0 = 1_788_480_000.0      # 2026-09-04T00:00:00Z


def test_day_summary_carries_the_decimator_record(tmp_path):
    buf = DecimatedBuffer(tmp_path, "SHARED 5000")
    buf.write_minute(minute_utc=T0, decimated_iq=np.zeros(SAMPLES_PER_MINUTE, np.complex64), filter_edge=True)
    buf.set_decimator(DAY, {"name": "grape-cic-fir", "version": 2})
    buf.flush_metadata()
    meta = json.loads(next(tmp_path.rglob("*_meta.json")).read_text())
    assert meta["summary"]["decimator"]["version"] == 2
    assert meta["minutes"]["0"]["filter_edge"] is True
    assert DayMetadata.from_dict(meta).decimator["version"] == 2


def test_old_day_files_read_as_having_no_decimator_record():
    assert DayMetadata.from_dict({"channel": "x", "date": "2026-09-04"}).decimator is None


def test_discard_day_removes_both_files(tmp_path):
    buf = DecimatedBuffer(tmp_path, "SHARED 5000")
    buf.write_minute(minute_utc=T0, decimated_iq=np.zeros(SAMPLES_PER_MINUTE, np.complex64))
    buf.flush_metadata()
    buf.discard_day(DAY)
    assert not list(tmp_path.rglob(f"{DAY}*"))
