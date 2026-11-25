import os, stat
from datetime import datetime, timezone
from hamsci_physics.gnss.adopt import adopt_netrc, adopt_vtec, extract_table

NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)
SRC = '''[storage]
sqlite_path = "/var/lib/timestd/phase2/timestd.db"

[gnss_vtec]
enabled = true                # wired by sigmond-site-timing
host = "192.0.2.10"
port = 9000
protocol = "auto"
save_csv = true

[next]
x = 1
'''

def test_extract_table_stops_at_the_next_header():
    t = extract_table(SRC, "gnss_vtec")
    assert t.startswith("[gnss_vtec]") and 'host = "192.0.2.10"' in t and "[next]" not in t

def test_enabled_section_is_written_with_comments_kept(tmp_path):
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text(SRC)
    assert adopt_vtec(src, dst, NOW) == "written"
    text = dst.read_text()
    assert "# wired by sigmond-site-timing" in text and "save_csv = true" in text
    assert stat.S_IMODE(dst.stat().st_mode) == 0o644

def test_disabled_section_writes_nothing(tmp_path):
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text(SRC.replace("enabled = true ", "enabled = false"))
    assert adopt_vtec(src, dst, NOW) == "none" and not dst.exists()

def test_placeholder_host_writes_nothing(tmp_path):
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text(SRC.replace('"192.0.2.10"', '"<YOUR_GNSS_HOST>"'))
    assert adopt_vtec(src, dst, NOW) == "none"

def test_existing_file_is_kept(tmp_path):
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text(SRC); dst.write_text("[gnss_vtec]\nenabled = true\n")
    assert adopt_vtec(src, dst, NOW) == "kept"
    assert dst.read_text() == "[gnss_vtec]\nenabled = true\n"

def test_station_position_carries_without_printing(tmp_path, capsys):
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text("[station]\nlatitude = 41.6875\nlongitude = -72.7917\n" + SRC)  # FN31pr centre
    adopt_vtec(src, dst, NOW)
    assert "latitude = 41.6875" in dst.read_text()
    assert "41.68" not in capsys.readouterr().out

def test_netrc_copy_keeps_mode_and_owner(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("machine urs.earthdata.nasa.gov login u password p\n"); os.chmod(src, 0o600)
    assert adopt_netrc(src, dst) == "copied"
    assert stat.S_IMODE(dst.stat().st_mode) == 0o600
    assert (dst.stat().st_uid, dst.stat().st_gid) == (src.stat().st_uid, src.stat().st_gid)
    assert adopt_netrc(src, dst) == "kept"
    assert adopt_netrc(tmp_path / "none", tmp_path / "c") == "absent"


def test_netrc_symlinked_source_is_refused_and_nothing_written(tmp_path):
    secret = tmp_path / "root_only"
    secret.write_text("TOP SECRET\n")
    src, dst = tmp_path / "a", tmp_path / "b"
    src.symlink_to(secret)
    assert adopt_netrc(src, dst) == "refused"
    assert not dst.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".b.")]


def test_netrc_fifo_source_is_refused(tmp_path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert adopt_netrc(fifo, tmp_path / "b") == "refused"


def test_vtec_symlinked_source_is_refused_and_nothing_written(tmp_path):
    real = tmp_path / "real.toml"
    real.write_text("[station]\nlatitude = 41.6875\nlongitude = -72.7917\n" + SRC)
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.symlink_to(real)
    assert adopt_vtec(src, dst, NOW) == "refused"
    assert not dst.exists()

N8UR_DEVICE = "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00"

def test_placeholder_host_with_a_device_is_adopted(tmp_path):
    # N8UR's shape: the template's host placeholder never filled in, a USB device set.
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text(SRC.replace('host = "192.0.2.10"',
                               f'host = "<YOUR_GNSS_HOST>"\ndevice = "{N8UR_DEVICE}"'))
    assert adopt_vtec(src, dst, NOW) == "written"
    assert f'device = "{N8UR_DEVICE}"' in dst.read_text()

def test_placeholder_host_and_placeholder_device_write_nothing(tmp_path):
    src, dst = tmp_path / "t.toml", tmp_path / "gnss-vtec.toml"
    src.write_text(SRC.replace('host = "192.0.2.10"',
                               'host = "<YOUR_GNSS_HOST>"\ndevice = "<YOUR_GNSS_DEVICE>"'))
    assert adopt_vtec(src, dst, NOW) == "none" and not dst.exists()
