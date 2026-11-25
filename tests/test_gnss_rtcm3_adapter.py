"""RTCM3 input for GNSS VTEC: the path AC0G-ND's Quectel LG290P uses.

The LG290P streams RTCM 3.3 (MSM7 1077/1087/1097/1127, 1005 and 1033) and
no broadcast ephemeris, so elevations come from BKG's BRDC file plus the
1005 antenna position.  The BRDC fixture is real: the GPS records of
BRDC00WRD_S for the afternoon of 2026-10-04.  The RTCM fixture is
synthetic.  make_synthetic_rtcm3.py computed it from that BRDC file for an
antenna at the centre of grid square EN16 (46.5 N, 97.0 W, 300 m): 20 GPS
epochs from 20:40Z, each a 1005 and a 1077 carrying L1 C/A and L2C.  No
real receiver's observations or position enter this repo.

The strongest test here is the pseudorange check.  It solves for the
antenna and one receiver clock per epoch from the ionosphere-free
pseudoranges.  Every satellite's range must then fit to within a few
metres, and the solved antenna must land within metres of the 1005
position.  A wrong orbit, a wrong GPS week, a swapped RINEX field or a
missing Earth-rotation correction breaks one or the other.
"""
import gzip
import io
import math
import random
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from hamsci_physics.gnss.broadcast_ephemeris import (
    C, OMEGA_E, BroadcastEphemeris, ecef_to_geodetic, elevation_azimuth,
    parse_rinex3_gps_nav,
)
from hamsci_physics.gnss.brdc_fetcher import BrdcFetcher, brdc_url
from hamsci_physics.gnss.gnss_tec import GNSSTECAnalyzer
from hamsci_physics.gnss.rtcm3_adapter import (
    LIGHT_MS, RTCM3Adapter, _crc24q, looks_like_rtcm3,
)

FIX = Path(__file__).resolve().parent / 'fixtures' / 'gnss'
RTCM = FIX / 'synthetic_en16_20261004T2040Z_20epochs.rtcm3'
BRDC = FIX / 'BRDC00WRD_S_20262770000_GPS_16-23h_excerpt.rnx'
CAPTURED = datetime(2026, 10, 4, 20, 41, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture(scope='module')
def ephem():
    e = BroadcastEphemeris()
    assert e.load_file(BRDC) > 100
    return e


def _run(ephem, data=None):
    ad = RTCM3Adapter(ephem, now=lambda: CAPTURED)
    data = RTCM.read_bytes() if data is None else data
    out = []
    for i in range(0, len(data), 1000):          # socket-sized chunks
        out.extend(ad.process_data(data[i:i + 1000]))
    return ad, out


# ─── framing ─────────────────────────────────────────────────────────────

def test_the_capture_is_recognised_as_rtcm3():
    assert looks_like_rtcm3(RTCM.read_bytes()[:3000])


def test_ubx_and_noise_are_not_rtcm3():
    ubx = b'\xb5\x62\x02\x15\x10\x00' + bytes(16) + b'\x00\x00'
    assert not looks_like_rtcm3(ubx * 50)
    # stray 0xD3 bytes inside UBX must not pass the CRC
    assert not looks_like_rtcm3((b'\xb5\x62\xd3\x00\x13' + bytes(40)) * 30)


def test_a_false_preamble_does_not_swallow_real_frames(ephem):
    """Line noise holding 0xD3 + a large length must cost one byte, not 1 KB.

    pyrtcm rejects a bad frame by itself, so it is the adapter's own CRC check
    that decides how far to skip: without it the bogus 1023-byte "frame" would
    eat the real frames behind it."""
    noise = b'\xd3\x03\xff' + bytes(range(60))
    _, out = _run(ephem, noise + RTCM.read_bytes())
    assert sum(1 for c, m, _ in out if (c, m) == (2, 0x15)) == 20


def test_a_corrupted_frame_is_skipped_not_fatal(ephem):
    data = bytearray(RTCM.read_bytes())
    data[200] ^= 0xFF
    ad, out = _run(ephem, bytes(data))
    assert sum(1 for c, m, _ in out if (c, m) == (2, 0x15)) >= 19


# ─── what the adapter emits ──────────────────────────────────────────────

def test_antenna_position_comes_from_1005(ephem):
    ad, _ = _run(ephem)
    lat, lon, h = ecef_to_geodetic(*ad.station_ecef)
    # The synthetic fixture's 1005 carries EN16's centre
    assert math.degrees(lat) == pytest.approx(46.5, abs=1e-4)
    assert math.degrees(lon) == pytest.approx(-97.0, abs=1e-4)


def test_the_fixture_is_synthetic_1005_and_1077_only(ephem):
    """No 1033 receiver descriptor (it would name a real receiver)."""
    ad, _ = _run(ephem)
    assert ad.counts == {'1005': 20, '1077': 20}


def test_each_epoch_yields_elevations_then_observations(ephem):
    _, out = _run(ephem)
    kinds = [(c, m) for c, m, _ in out]
    assert kinds.count((2, 0x15)) == 20
    for k in range(len(kinds) - 1):
        if kinds[k + 1] == (2, 0x15):
            assert kinds[k] == (1, 0x35), 'elevations must precede the epoch'


def test_measurements_are_physical(ephem):
    _, out = _run(ephem)
    raw = next(p for c, m, p in out if (c, m) == (2, 0x15))
    assert raw['week'] == 2439 and raw['leapS'] == 18
    sigs = {m['sigId'] for m in raw['measurements']}
    assert {0, 3} <= sigs                      # L1 C/A and L2C (2L) both present
    for m in raw['measurements']:
        assert 1.9e7 < m['prMes'] < 2.7e7      # GPS ranges, metres
        f = 1575.42e6 if m['sigId'] == 0 else (1227.6e6 if m['sigId'] in (3, 4) else 1176.45e6)
        # phase (cycles -> m) tracks pseudorange to within the code-phase offset
        assert abs(m['cpMes'] * C / f - m['prMes']) < 1000


F1, F2 = 1575.42e6, 1227.6e6


def _sat_ranges(ephem, out):
    """Per epoch, {svId: (satellite ECEF at transmit, rotated for the Earth's
    turn during the light time; ionosphere-free pseudorange corrected for
    the satellite clock)} from the L1 C/A and L2C measurements."""
    epochs = []
    for c, m, p in out:
        if (c, m) != (2, 0x15):
            continue
        t_rx = p['week'] * 604800 + p['rcvTow']
        by_sv = {}
        for mm in p['measurements']:
            by_sv.setdefault(mm['svId'], {})[mm['sigId']] = mm['prMes']
        ep = {}
        for sv, prs in by_sv.items():
            if 0 not in prs or 3 not in prs:
                continue
            p_if = (F1 ** 2 * prs[0] - F2 ** 2 * prs[3]) / (F1 ** 2 - F2 ** 2)
            e = ephem.select(sv, t_rx)
            t_tx = t_rx - p_if / C
            t_tx -= e.clock_bias(t_tx)
            tau = t_rx - t_tx
            x, y, z = e.position(t_tx)
            th = OMEGA_E * tau
            sat = (x * math.cos(th) + y * math.sin(th), -x * math.sin(th) + y * math.cos(th), z)
            ep[sv] = (sat, p_if + C * e.clock_bias(t_tx))
        epochs.append(ep)
    return epochs


def test_pseudoranges_agree_with_computed_orbits(ephem):
    """Orbits, GPS time, RINEX field order and antenna position, all at once.

    Least squares solves for one antenna position across all 20 epochs plus
    one receiver clock per epoch, starting from the 1005 position.  The
    fixture's code noise (0.2 m per frequency) leaves a worst residual of
    1.3 m on the ionosphere-free fit, and the solution lands 0.3 m from the
    1005 position, where the generator put the antenna.  Every range must
    fit within RESIDUAL_M of its epoch's clock term, and the solution must
    land within POSITION_M of the 1005 position.  Measured failures:
    swapping the RINEX rate fields OMEGA DOT and IDOT leaves 21 m of
    residual and moves the antenna 103 m; dropping the Earth-rotation
    (Sagnac) turn in this solver moves it 25 m; a 1 s time error leaves
    820 m of residual; dropping the satellite clock leaves 156 km."""
    RESIDUAL_M, POSITION_M = 3.0, 2.0
    ad, out = _run(ephem)
    epochs = _sat_ranges(ephem, out)
    assert len(epochs) == 20 and all(len(ep) >= 6 for ep in epochs)
    start = np.array(ad.station_ecef, dtype=float)
    pos = start.copy()
    clocks = np.zeros(len(epochs))
    for _ in range(8):                               # Gauss-Newton
        rows, rhs = [], []
        for k, ep in enumerate(epochs):
            for sat, pr in ep.values():
                d = pos - np.array(sat)
                r = float(np.linalg.norm(d))
                row = np.zeros(3 + len(epochs))
                row[:3], row[3 + k] = d / r, 1.0
                rows.append(row)
                rhs.append(pr - r - clocks[k])
        step = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
        pos, clocks = pos + step[:3], clocks + step[3:]
    worst = max(abs(pr - math.dist(sat, pos) - clocks[k])
                for k, ep in enumerate(epochs) for sat, pr in ep.values())
    moved = float(np.linalg.norm(pos - start))
    assert worst < RESIDUAL_M, f'residuals up to {worst:.1f} m — orbit/time wrong'
    assert moved < POSITION_M, f'the ranges put the antenna {moved:.1f} m from the 1005 position'


def test_elevations_are_sane(ephem):
    _, out = _run(ephem)
    nav = next(p for c, m, p in out if (c, m) == (1, 0x35))
    assert len(nav['sats']) >= 6
    for s in nav['sats']:
        assert -10 < s['elev'] <= 90 and 0 <= s['azim'] < 360


def test_the_analyzer_produces_vtec(ephem):
    an = GNSSTECAnalyzer({})
    last = {}
    for c, m, p in _run(ephem)[1]:
        if (c, m) == (1, 0x35):
            an.update_satellite_positions(p, 0)
        else:
            last = an.process_rawx(p) or last
    assert len(last) >= 6
    # make_synthetic_rtcm3.py models a uniform 20 TECU; 0.2 m of code noise,
    # levelled over 20 epochs, leaves each satellite within 0.7 TECU of it.
    for sv, v in last.items():
        assert v['vtec_u'] == pytest.approx(20.0, abs=1.5), sv


def test_without_ephemeris_it_says_why_and_the_analyzer_skips(ephem):
    ad, out = _run(BroadcastEphemeris())
    nav = [p for c, m, p in out if (c, m) == (1, 0x35)]
    assert nav and all(not p['sats'] for p in nav)
    assert 'no ephemeris for G' in ad.status()
    an = GNSSTECAnalyzer({})
    assert not any(an.process_rawx(p) for c, m, p in out if (c, m) == (2, 0x15))


def test_gps_week_rolls_over_with_the_tow():
    # Just after the Sunday 00:00 GPS rollover the host is in week N+1 while a
    # late frame still carries last week's TOW (604799 s): it belongs to week N.
    rollover = datetime(2026, 10, 4, 0, 0, 0, tzinfo=timezone.utc).timestamp() - 18 + 2
    ad = RTCM3Adapter(BroadcastEphemeris(), now=lambda: rollover)
    week, _ = ad._gps_time(604799.0)
    week_now, _ = ad._gps_time(1.0)
    assert week == week_now - 1


# ─── legacy 1004 ─────────────────────────────────────────────────────────

def _bits(fields):
    v = n = 0
    for width, value in fields:
        v = (v << width) | (value & ((1 << width) - 1))
        n += width
    pad = (-n) % 8
    return (v << pad).to_bytes((n + pad) // 8, 'big')


def _frame(payload):
    head = bytes([0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF]) + payload
    return head + _crc24q(head).to_bytes(3, 'big')


def test_legacy_1004_decodes_to_l1_and_l2(ephem):
    """A synthetic 1004 (RTCM 10403.3 §3.5.2 bit layout) for one satellite."""
    pr_mod, amb = 123456.78, 75                     # metres, whole light-ms
    l1_minus_pr, l2_minus_l1pr, p2_minus_p1 = -1.2345, 3.4565, 2.34
    payload = _bits([
        (12, 1004), (12, 290), (30, 74402000), (1, 0), (5, 1), (1, 0), (3, 0),
        (6, 3), (1, 0), (24, round(pr_mod / 0.02)), (20, round(l1_minus_pr / 0.0005)),
        (7, 50), (8, amb), (8, round(45.0 / 0.25)),
        (2, 0), (14, round(p2_minus_p1 / 0.02)), (20, round(l2_minus_l1pr / 0.0005)),
        (7, 50), (8, round(40.0 / 0.25)),
    ])
    ad = RTCM3Adapter(ephem, now=lambda: CAPTURED)
    out = list(ad.process_data(_frame(payload)))
    raw = next(p for c, m, p in out if (c, m) == (2, 0x15))
    assert raw['rcvTow'] == pytest.approx(74402.0)
    by_sig = {m['sigId']: m for m in raw['measurements']}
    p1 = pr_mod + amb * LIGHT_MS
    assert by_sig[0]['svId'] == 3
    assert by_sig[0]['prMes'] == pytest.approx(p1, abs=0.02)
    assert by_sig[0]['cpMes'] * C / 1575.42e6 == pytest.approx(p1 + l1_minus_pr, abs=0.001)
    assert by_sig[3]['prMes'] == pytest.approx(p1 + p2_minus_p1, abs=0.02)
    assert by_sig[3]['cpMes'] * C / 1227.6e6 == pytest.approx(p1 + l2_minus_l1pr, abs=0.001)


# ─── RINEX parsing + fetcher ─────────────────────────────────────────────

def test_rinex_records_carry_week_and_clock():
    recs = parse_rinex3_gps_nav(BRDC.read_text())
    assert all(r.week == 2439 for r in recs)
    assert all(abs(r.af0) < 1e-3 for r in recs)   # satellite clocks within 1 ms
    assert all(5150 < r.sqrt_a < 5160 for r in recs)


def test_elevation_of_a_point_overhead_is_90():
    st = (-536027.6, -4365594.3, 4603898.0)     # EN16's centre, 300 m
    up = tuple(v * 1.5 for v in st)
    assert elevation_azimuth(st, up)[0] == pytest.approx(90.0, abs=0.3)


def test_fetcher_loads_what_it_downloads_and_survives_failure(tmp_path):
    gz = gzip.compress(BRDC.read_bytes())
    calls = []

    def ok_open(url, timeout):
        calls.append(url)
        return io.BytesIO(gz)

    e = BroadcastEphemeris()
    noon = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    BrdcFetcher(tmp_path, e, opener=ok_open).refresh_once(noon)
    assert len(e) > 100
    assert calls == [brdc_url(noon)]
    assert calls[0].endswith('/2026/277/BRDC00WRD_S_20262770000_01D_MN.rnx.gz')

    def bad_open(url, timeout):
        raise OSError('network down')

    e2 = BroadcastEphemeris()
    BrdcFetcher(tmp_path, e2, opener=bad_open).refresh_once(noon)   # must not raise
    assert len(e2) > 100, 'a failed fetch keeps the file already on disk'


def test_fetcher_takes_yesterday_too_just_after_midnight(tmp_path):
    calls = []
    BrdcFetcher(tmp_path, BroadcastEphemeris(),
                opener=lambda u, timeout: calls.append(u) or io.BytesIO(b'')
                ).refresh_once(datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc))
    assert [u.split('/')[-1][12:19] for u in calls] == ['2026277', '2026278']


def test_an_ephemeris_filled_after_the_adapter_starts_is_used():
    """live_vtec's real order: an EMPTY ephemeris goes to the adapter, and the
    fetcher thread fills that same object later.  An empty BroadcastEphemeris
    is falsy, so `ephemeris or BroadcastEphemeris()` silently replaced it."""
    e = BroadcastEphemeris()
    ad = RTCM3Adapter(e, now=lambda: CAPTURED)
    assert ad.ephem is e
    e.load_file(BRDC)                     # the fetcher's load, after the fact
    out = list(ad.process_data(RTCM.read_bytes()))
    nav = [p for c, m, p in out if (c, m) == (1, 0x35)]
    assert nav and len(nav[-1]['sats']) >= 6


# --- a stray 0xD3 preamble in non-RTCM data must not stall a probe -----------
# Rob's 2026-10-24 report: a probe fed noise with one stray \xd3\x00\x10 never
# returned.  Neither shipped reader loops (both resync at preamble+1 on a CRC
# failure), so these pin that.  A regression would hang; each call runs in a
# daemon thread with a deadline so it fails instead.

def _noise_with_one_stray_preamble():
    r = random.Random(500)
    b = bytearray(x for x in (r.randrange(256) for _ in range(4000)) if x != 0xD3)[:1500]
    b[500:503] = b'\xd3\x00\x10'
    return bytes(b)


NMEA = (b'$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n' * 30)
NMEA_STRAY = NMEA[:500] + b'\xd3\x00\x10' + NMEA[500:]


def _returns_within(fn, seconds=2.0):
    """Run ``fn`` in a daemon thread; return its value, or fail if it outlasts ``seconds``."""
    box = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f'still running after {seconds} s: the resync loops'
    return box[0]


def test_stray_preamble_in_noise_returns_promptly():
    assert _returns_within(lambda: looks_like_rtcm3(_noise_with_one_stray_preamble())) is False


def test_stray_preamble_in_nmea_returns_promptly():
    assert _returns_within(lambda: looks_like_rtcm3(NMEA_STRAY)) is False


def test_adapter_drops_a_stray_preamble_in_noise():
    ad = RTCM3Adapter(BroadcastEphemeris())
    assert _returns_within(lambda: list(ad.process_data(_noise_with_one_stray_preamble()))) == []
    assert ad.buffer == b''


def test_adapter_drops_a_stray_preamble_in_nmea():
    ad = RTCM3Adapter(BroadcastEphemeris())
    assert _returns_within(lambda: list(ad.process_data(NMEA_STRAY))) == []
    assert ad.buffer == b''
