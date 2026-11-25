"""Build the synthetic RTCM3 capture the GNSS tests read.

No real receiver produced this file, so it carries no station's position,
measured or stated.  It holds 20 one-second GPS epochs.  The first epoch's
GPS time of week is 74418 s in week 2439, which is 2026-10-04 20:40:00 UTC.
Each epoch has two messages:

* a 1005 giving an antenna at the centre of grid square EN16
  (46.5 N, 97.0 W, 300 m);
* a 1077 (GPS MSM7) with L1 C/A ("1C") and L2C ("2L") pseudorange and
  carrier phase for every GPS satellite more than 10 degrees above that
  antenna.

The file has no 1033 receiver descriptor.

The observations come from the committed public broadcast ephemeris,
BRDC00WRD_S_20262770000_GPS_16-23h_excerpt.rnx, read through
``hamsci_physics.gnss.broadcast_ephemeris``.  For each satellite the
pseudorange on frequency f is:

    P_f = rho + c*dt_rx - c*dt_sat + I_f + noise
    rho     geometric range, solved for light time, with the satellite
            rotated by the Earth's turn during that light time (Sagnac)
    dt_rx   receiver clock: 2 us plus 1 ns/s
    dt_sat  broadcast satellite clock, af0 + af1*dt + af2*dt^2
    I_f     ionospheric group delay, 40.308 * STEC / f^2.  STEC comes from
            a uniform VTEC of 20 TECU and the single-layer mapping with a
            350 km shell.
    noise   Gaussian, 0.20 m on code and 2 mm on phase

Carrier phase (metres) is rho + c*dt_rx - c*dt_sat - I_f, plus a whole
number of cycles that stays fixed for each satellite and frequency, plus
noise.  There is no troposphere and there are no hardware biases, so a fit
that removes the ionosphere is left with the noise alone.

The output depends only on the fixed seed and the BRDC excerpt.  To rebuild
it, run from the hamsci-physics checkout:

    .venv/bin/python tests/fixtures/gnss/make_synthetic_rtcm3.py \\
        tests/fixtures/gnss/synthetic_en16_20261004T2040Z_20epochs.rtcm3
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

from hamsci_physics.gnss.broadcast_ephemeris import (
    C, F_L1, F_L2, OMEGA_E, BroadcastEphemeris, elevation_azimuth, geodetic_to_ecef,
)
from hamsci_physics.gnss.rtcm3_adapter import LIGHT_MS, _crc24q

HERE = Path(__file__).resolve().parent
BRDC = HERE / 'BRDC00WRD_S_20262770000_GPS_16-23h_excerpt.rnx'

LAT, LON, HEIGHT = 46.5, -97.0, 300.0          # EN16's centre
WEEK, TOW0, EPOCHS = 2439, 74418, 20           # 2026-10-04 20:40:00 UTC
VTEC_TECU = 20.0
SHELL_M, RE_M = 350e3, 6371e3
K_IONO = 40.308
RX_CLOCK_S, RX_DRIFT = 2.0e-6, 1.0e-9
CODE_SIGMA_M, PHASE_SIGMA_M = 0.20, 0.002
ELEV_MASK_DEG = 10.0
SEED = 20261004

# MSM signal ids (RTCM 10403.3 table 3.5-91): 2 = L1 C/A, 16 = L2C (L).
SIGNALS = ((2, F_L1), (16, F_L2))


class _Bits:
    def __init__(self):
        self.value = self.n = 0

    def put(self, width: int, value: int) -> None:
        if not -(1 << (width - 1)) <= value < (1 << width):
            raise ValueError(f'{value} does not fit {width} bits')
        self.value = (self.value << width) | (value & ((1 << width) - 1))
        self.n += width

    def payload(self) -> bytes:
        pad = (-self.n) % 8
        return (self.value << pad).to_bytes((self.n + pad) // 8, 'big')


def _frame(payload: bytes) -> bytes:
    head = bytes([0xD3, (len(payload) >> 8) & 0x03, len(payload) & 0xFF]) + payload
    return head + _crc24q(head).to_bytes(3, 'big')


def msg_1005(ecef) -> bytes:
    b = _Bits()
    b.put(12, 1005); b.put(12, 0); b.put(6, 0)          # DF002, DF003, DF021
    b.put(1, 1); b.put(1, 0); b.put(1, 0); b.put(1, 0)  # DF022-024 (GPS only), DF141
    b.put(38, round(ecef[0] * 1e4))                     # DF025, 0.1 mm
    b.put(1, 0); b.put(1, 0)                            # DF142, reserved
    b.put(38, round(ecef[1] * 1e4))                     # DF026
    b.put(2, 0)                                         # DF364
    b.put(38, round(ecef[2] * 1e4))                     # DF027
    return _frame(b.payload())


def msg_1077(tow_ms: int, sats) -> bytes:
    """``sats``: sorted [(prn, range_rate_m_s, [(code_m, phase_m, cn0), ...per SIGNALS])]."""
    rough = []
    for prn, rate, cells in sats:
        ms = cells[0][0] / LIGHT_MS
        whole = math.floor(ms)
        frac = round((ms - whole) * 1024)
        if frac == 1024:
            whole, frac = whole + 1, 0
        rough.append((whole, frac, whole + frac / 1024))
    b = _Bits()
    b.put(12, 1077); b.put(12, 0); b.put(30, tow_ms)    # DF002, DF003, DF004
    b.put(1, 0); b.put(3, 0); b.put(7, 0)               # DF393, DF409, reserved
    b.put(2, 0); b.put(2, 0); b.put(1, 0); b.put(3, 0)  # DF411, DF412, DF417, DF418
    b.put(64, sum(1 << (64 - prn) for prn, _, _ in sats))           # DF394
    b.put(32, sum(1 << (32 - sid) for sid, _ in SIGNALS))           # DF395
    b.put(len(sats) * len(SIGNALS), (1 << (len(sats) * len(SIGNALS))) - 1)  # DF396
    for whole, _, _ in rough:
        b.put(8, whole)                                 # DF397, ms
    for _ in sats:
        b.put(4, 0)                                     # extended satellite info
    for _, frac, _ in rough:
        b.put(10, frac)                                 # DF398, 2^-10 ms
    for _, rate, _ in sats:
        b.put(14, round(rate))                          # DF399, m/s
    cells = [(r[2], cell, rate) for r, (_, rate, cs) in zip(rough, sats) for cell in cs]
    for r, (code, _, _), _ in cells:
        b.put(20, round((code / LIGHT_MS - r) * 2 ** 29))   # DF405
    for r, (_, phase, _), _ in cells:
        b.put(24, round((phase / LIGHT_MS - r) * 2 ** 31))  # DF406
    for _ in cells:
        b.put(10, 600)                                  # DF407, lock time
    for _ in cells:
        b.put(1, 0)                                     # DF420, half-cycle
    for _, (_, _, cn0), _ in cells:
        b.put(10, round(cn0 / 0.0625))                  # DF408, dB-Hz
    for _, _, rate in cells:
        b.put(15, round((rate - round(rate)) * 1e4))    # DF404, 0.0001 m/s
    return _frame(b.payload())


def _slm(elev_deg: float) -> float:
    c = RE_M * math.cos(math.radians(elev_deg)) / (RE_M + SHELL_M)
    return 1.0 / math.sqrt(1.0 - c * c)


def _range(eph, station, t_true: float):
    """(rho, transmit time) for a signal received at true GPS time ``t_true``."""
    tau = 0.075
    for _ in range(8):
        t_tx = t_true - tau
        x, y, z = eph.position(t_tx)
        th = OMEGA_E * tau
        sat = (x * math.cos(th) + y * math.sin(th), -x * math.sin(th) + y * math.cos(th), z)
        rho = math.dist(sat, station)
        tau = rho / C
    return rho, t_true - tau


def build() -> bytes:
    ephem = BroadcastEphemeris()
    ephem.load_file(BRDC)
    station = geodetic_to_ecef(LAT, LON, HEIGHT)
    rng = random.Random(SEED)
    t0 = WEEK * 604800 + TOW0
    visible = []
    for prn in range(1, 33):
        eph = ephem.select(prn, t0)
        if eph is not None and elevation_azimuth(station, eph.position(t0))[0] > ELEV_MASK_DEG:
            visible.append(prn)
    cycles = {(prn, sid): rng.randint(-20, 20) for prn in visible for sid, _ in SIGNALS}
    out = bytearray()
    for k in range(EPOCHS):
        t_rcv = t0 + k
        dt_rx = RX_CLOCK_S + RX_DRIFT * k
        t_true = t_rcv - dt_rx
        sats = []
        for prn in visible:
            eph = ephem.select(prn, t_true)
            rho, t_tx = _range(eph, station, t_true)
            rate = (_range(eph, station, t_true + 0.5)[0] - _range(eph, station, t_true - 0.5)[0])
            elev = elevation_azimuth(station, eph.position(t_tx))[0]
            stec = VTEC_TECU * 1e16 * _slm(elev)
            clocks = C * dt_rx - C * eph.clock_bias(t_tx)
            cells = []
            for sid, f in SIGNALS:
                iono = K_IONO * stec / f ** 2
                code = rho + clocks + iono + rng.gauss(0.0, CODE_SIGMA_M)
                phase = (rho + clocks - iono + cycles[(prn, sid)] * C / f
                         + rng.gauss(0.0, PHASE_SIGMA_M))
                cells.append((code, phase, 30.0 + 20.0 * math.sin(math.radians(elev))))
            sats.append((prn, rate, cells))
        out += msg_1005(station)
        out += msg_1077(round((TOW0 + k) * 1000), sats)
    return bytes(out)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data = build()
    Path(sys.argv[1]).write_bytes(data)
    print(f'{len(data)} bytes written to {sys.argv[1]}')
