# GNSS VTEC: the receiver, the stream and the service

> **Audience:** operator
> **Status:** current
> **Canonical for:** what the receiver must send, how the stream reaches the station, and how to check the VTEC service

`hamsci-physics-vtec.service` reads a dual-frequency GNSS receiver's raw
measurements and computes the vertical total electron content (VTEC) above the
station. It took over from hf-timestd's `timestd-vtec.service` in v3.96.

This guide began as the VTEC half of hf-timestd's GNSS time-server guide. The
time-server half (PPS, chrony, a stable receiver name) stays in
[hf-timestd `docs/GNSS_TIMESERVER.md`](https://github.com/HamSCI/hf-timestd/blob/main/docs/GNSS_TIMESERVER.md).
The same small box often does both jobs. Read that guide first for the box's
operating system, PPS wiring and udev name (`/dev/ttyGNSS` below).

Neither job is required. A station keeps time from the internet without the
box, and hf-timestd measures HF TEC from WWV/WWVH timing without any GPS. GNSS
VTEC gives an independent ionosphere to compare the HF measurements against.

The install, the config file and adoption from hf-timestd are in
[the README](../README.md#gnss-vtec). The keys of
`/etc/hamsci-physics/gnss-vtec.toml` are explained in
[`config/gnss-vtec.toml.example`](../config/gnss-vtec.toml.example).

---

## 1. Two receivers that run today

| | Receiver A | Receiver B |
|---|---|---|
| Model | u-blox ZED-F9P | Quectel LG290P |
| What it streams | **UBX**: `RXM-RAWX` + `NAV-SAT` | **RTCM 3.3**: MSM7 + 1005 + 1033 |
| Relay to the LAN | `str2str` (RTKLIB), TCP port 9000 | `str2str` (RTKLIB), TCP port 9000 |

The service tells the two apart by itself (`protocol = "auto"`), so a station
needs only the box's address and port.

The receivers differ in one way that matters. A u-blox receiver reports each
satellite's elevation itself (`NAV-SAT`). An RTCM base station does not. It
sends observations and its own antenna position, but no satellite orbits. For
RTCM, the service therefore downloads the day's broadcast orbits from the
internet and computes the elevations (§4).

VTEC needs two carrier frequencies on the same satellite (GPS L1 and L2). The
ZED-F9P (L1/L2) and the LG290P (L1/L2/L5/E6) both qualify. A single-frequency
receiver does not.

---

## 2. Which messages the receiver must send

| Receiver | Must send, at 1 Hz | What the service uses it for |
|---|---|---|
| u-blox (UBX) | `RXM-RAWX` | pseudorange and carrier phase, per signal |
| | `NAV-SAT` | each satellite's elevation and azimuth |
| RTCM 3.3 | `1077` (GPS MSM7), or `1074` (MSM4), or legacy `1004` | pseudorange and phase, GPS L1 + L2 |
| | `1005` or `1006`, every 1 to 10 s | the antenna's position, for elevations |
| | `1087` `1097` `1127` (GLONASS, Galileo, BeiDou) | carried, not yet used |

Today the service computes VTEC from **GPS L1 + L2 only**. GPS satellites with
no L2C signal (older Block IIR) drop out. The others pass through untouched.

**u-blox ZED-F9P.** Enable the two messages on the port the box reads (USB
here; `_UART1` for the serial port) and save them in flash. With `pyubx2`,
which hamsci-physics installs:

```python
from pyubx2 import UBXMessage, SET_LAYER_RAM, SET_LAYER_BBR, SET_LAYER_FLASH, TXN_NONE
import serial
msg = UBXMessage.config_set(
    SET_LAYER_RAM | SET_LAYER_BBR | SET_LAYER_FLASH, TXN_NONE,
    [("CFG_MSGOUT_UBX_RXM_RAWX_USB", 1),
     ("CFG_MSGOUT_UBX_NAV_SAT_USB", 1),
     ("CFG_RATE_MEAS", 1000)])
with serial.Serial("/dev/ttyGNSS", 115200, timeout=2) as s:
    s.write(msg.serialize())
```

u-center does the same through its configuration view.

**Quectel LG290P.** Configure the receiver, with Quectel's QGNSS tool or its
`$PQTM...` commands, to emit 1077, 1087, 1097, 1127 and 1005 at 1 Hz. Save the
configuration. Then check the result with the census in §6. The stream is the
authority, not the configuration tool. An LG290P base engine sends **no
broadcast ephemeris** in RTCM (no 1019/1020/1042/1046), whatever you ask of it.
§4 supplies the orbits instead.

---

## 3. Put the stream on the LAN: TCP port 9000

The service connects *to* the box, so the box must **listen**.

Both receivers use RTKLIB's `str2str` (`sudo apt install rtklib`). It passes the
receiver's bytes through unchanged, whatever their format.

**RTCM** (receiver B). `/etc/systemd/system/str2str.service`:

```ini
[Unit]
Description=RTKLIB str2str RTCM3 TCP server
After=network.target

[Service]
Type=simple
User=<user>
ExecStartPre=/bin/sh -c 'stty -F /dev/ttyGNSS 460800 raw -echo'
ExecStart=/usr/bin/str2str -in serial://ttyGNSS:460800:8:n:1 -out tcpsvr://:9000 -s 1000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Use the short name (`serial://ttyGNSS`), not a `/dev/serial/by-id/...` path.
Older RTKLIB builds prefix `/dev/` themselves. `tcpsvr://` listens. `tcpcli://`
would push to one address, and nothing at the station listens. `str2str` serves
several clients at once.

**UBX** (receiver A, `/etc/systemd/system/rawx-server.service`): the same unit,
with the u-blox's port speed and a `#ubx` format tag on the input:

```
ExecStart=/usr/bin/str2str -in serial://ttyGNSS:115200:8:n:1#ubx -out tcpsvr://:9000
```

**UBX with ser2net instead** (`sudo apt install ser2net`), if you prefer it.
`/etc/ser2net.yaml`:

```yaml
connection: &gnss
  accepter: tcp,9000
  connector: serialdev,/dev/ttyGNSS,115200n81,local
  options:
    kickolduser: true
```

`kickolduser` lets a reconnecting station take over from its own stale
connection. It also means a second client pushes the first one off.

**One program owns the port.** If gpsd runs on the same device, it fights the
relay. Disable it (`sudo systemctl disable --now gpsd gpsd.socket`), or have the
relay feed it, but not both on one port.

---

## 4. Orbits for an RTCM receiver

For an RTCM stream, the service downloads BKG's daily multi-GNSS broadcast
navigation file, `BRDC00WRD_S_<year><doy>0000_01D_MN.rnx.gz`. BKG builds it from
live IGS data streams and refreshes it every 15 minutes, so it holds orbits
valid for *now*. It needs no login. The service fetches it every 15 minutes into
`ephemeris_dir` (`data/brdc`, under `/var/lib/timestd`). It keeps yesterday's
file as well before 04:00 UTC, and keeps whatever it already has if a download
fails.

**An RTCM station therefore needs internet access for VTEC.** A u-blox station
does not.

---

## 5. The station side

Point the service at the box in `/etc/hamsci-physics/gnss-vtec.toml`:

```toml
[gnss_vtec]
enabled = true
host = "<gnss-host>"
port = 9000
protocol = "auto"            # or "ubx" / "rtcm3"; auto decides from the first bytes
ephemeris_dir = "data/brdc"  # RTCM only; relative to /var/lib/timestd
```

For a receiver plugged into this machine by USB, set `device` instead of `host`
and `port`:

```toml
[gnss_vtec]
enabled = true
device = "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_GNSS_receiver-if00"
configure_receiver = true    # default; see below
```

When `device` is set, it wins over `host` and `port`. The unit carries
`SupplementaryGroups=dialout`, so the `timestd` user can open the port. Each
time the port opens, the service sends one UBX-CFG-VALSET to the receiver's RAM
layer. It turns on UBX output on USB plus RXM-RAWX, RXM-SFRBX and NAV-SAT at
rate 1. It never writes to flash or battery-backed memory, so a power cycle
returns the receiver to its stored setup and the next open sends the message
again. The journal logs ACK, NAK or no answer. A NAK or silence is a warning,
and the reader carries on. Set `configure_receiver = false` to send nothing.
This key does not apply to `host`/`port` sources.

Then start the unit:

```bash
sudo systemctl restart hamsci-physics-vtec.service
```

The unit starts only where `gnss-vtec.toml` exists. Record the file as the
station's site delta so a reflash restores it.

---

## 6. Checking that it works

**What the box sends.** From the station, a 15-second census of the stream:

```bash
python3 - <<'PYEOF'
import socket, time, collections
s = socket.create_connection(("<gnss-host>", 9000), timeout=5); s.settimeout(5)
buf = b""; t = time.time()
while time.time() - t < 15:
    try: d = s.recv(8192)
    except socket.timeout: continue
    if not d: break
    buf += d
rtcm = collections.Counter(); i = 0
while (i := buf.find(b"\xd3", i)) >= 0 and i + 6 <= len(buf):
    n = ((buf[i+1] & 3) << 8) | buf[i+2]
    if i + 6 + n > len(buf): break
    rtcm[(buf[i+3] << 4) | (buf[i+4] >> 4)] += 1; i += 6 + n
print("bytes:", len(buf), " UBX frames:", buf.count(b"\xb5\x62"), " RTCM3 types:", dict(sorted(rtcm.items())))
PYEOF
```

An LG290P gives about 21 KB in 15 s and
`{1005: 16, 1033: 16, 1077: 15, 1087: 15, 1097: 15, 1127: 15}`. Zero bytes on an
open connection means the relay runs but nothing feeds it. "Connection refused"
means nothing listens on the port.

**What the service makes of it.** `journalctl -u hamsci-physics-vtec -f`. A
healthy RTCM start reads:

```
Receiver speaks RTCM3: observations from MSM/1004, elevations from broadcast ephemeris + RTCM 1005
BRDC: 362 healthy GPS ephemerides in memory
Rx DCB estimated: 1.869 m (6.2 ns), ...
VTEC: 14.79 TECU (Sats: 5)
```

After that, one `VTEC:` summary appears per minute, and the `L3_gnss_vtec` table
gains a row a second (`/var/lib/timestd/phase2/timestd.db`). When `save_csv` is
on, `/var/lib/timestd/data/gnss_vtec.csv` gains the same row. A u-blox start
reads `Receiver speaks UBX (RXM-RAWX + NAV-SAT)` instead, with no `BRDC` line.

---

## 7. When it does not work

| What you see | What it means | What to do |
|---|---|---|
| `str2str` (or ser2net) restarting; `stty: /dev/ttyACM0: No such file or directory` | the receiver re-enumerated under another number | give the receiver a udev name keyed on its serial number (GNSS_TIMESERVER.md §4) |
| census: connection refused | nothing listens on the port | check the relay unit; `ss -tln \| grep 9000` on the box |
| census: 0 bytes on an open connection | relay up, receiver silent | receiver unplugged or reset; check `/dev/ttyGNSS`; check that the relay owns the port (gpsd, §3) |
| census: RTCM 1004 only | legacy GPS-only output | works (GPS L1/L2), but configure MSM7 + 1005 (§2): without 1005 there are no elevations |
| log: `RTCM3 stream yields nothing usable ... antenna position NOT YET` | no 1005/1006 in the stream | enable 1005 on the receiver |
| log: `... no ephemeris for G[...]` | the BRDC download failed | the station needs internet (§4); look for `BRDC fetch failed` lines |
| the unit never starts | no `/etc/hamsci-physics/gnss-vtec.toml` | write the file (§5) and restart the unit |
| unit killed with `Watchdog timeout`, repeatedly | a silent or refusing box, on a build before the sliced wait | update hamsci-physics; the service waits in slices that pet the watchdog |
| `Failed to download DCB file ... Earthdata` at start | no NASA Earthdata login for satellite biases | harmless: VTEC runs with satellite biases taken as zero, so absolute values carry a few TECU of bias. Optional: [NASA_EARTHDATA_SETUP.md](NASA_EARTHDATA_SETUP.md) |

When you read a box's status page, the address of the connected client is the
*station's*, not the box's.

---

## 8. What the numbers are

The service levels carrier phase to code, per satellite. It maps slant TEC to
vertical with a single-layer ionosphere at 350 km. It estimates the receiver's
own inter-frequency bias once, from the first epoch with four satellites above
20 degrees, using the floor that true VTEC cannot fall below 2 TECU. Without
NASA's satellite bias file it takes each satellite's bias as zero. The result
tracks the ionosphere's changes well and its absolute level to within a few
TECU. That fits comparing against HF TEC and watching disturbances. It does not
fit calibrating other instruments.

Code: `src/hamsci_physics/gnss/` (`vtec_service.py`, `gnss_tec.py` the analyzer,
`ubx_parser.py`, `rtcm3_adapter.py`, `broadcast_ephemeris.py`,
`brdc_fetcher.py`). Tests: `tests/test_gnss_rtcm3_adapter.py`,
`tests/test_gnss_vtec_service.py`.
