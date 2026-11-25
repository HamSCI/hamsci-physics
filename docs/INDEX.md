# hamsci-physics documentation index

> **Audience:** all
> **Status:** current
> **Verified against:** hamsci-physics 8004883 on 2026-11-21 — docs
> **Canonical for:** the map of this repo's docs

hamsci-physics is a client in the HamSCI sigmond suite. ★ = canonical; when two docs disagree the ★ one wins.
Suite-wide front door: [HamSCI/sigmond docs](https://github.com/HamSCI/sigmond/blob/main/docs/README.md); every component: [the suite's components](https://github.com/HamSCI/sigmond/blob/main/docs/COMPONENTS.md).

| Doc | Audience | What it gives you |
|-----|----------|-------------------|
| [../README.md](../README.md) | all | what this client is: science products from hf-timestd's observations |
| [GRAPE_DAILY_PROCESSING.md](GRAPE_DAILY_PROCESSING.md) ★ | operator/contributor | the daily GRAPE batch: raw IQ to PSWS-ready datasets |
| [PSWS_SETUP_GUIDE.md](PSWS_SETUP_GUIDE.md) ★ | operator | PSWS upload setup; this repo builds datasets, the uploader ships them |
| [TIMING_STATE.md](TIMING_STATE.md) ★ | contributor | what GRAPE reads from the chunk sidecars' timing block, and what it never reads |
| [GNSS_VTEC.md](GNSS_VTEC.md) ★ | operator | the VTEC receiver's messages, the TCP relay, the orbit feed, the checks |
| [NASA_EARTHDATA_SETUP.md](NASA_EARTHDATA_SETUP.md) | operator | a NASA Earthdata account for the GNSS VTEC service's DCB download |
| [DRF_UPLOAD_SYSTEM.md](DRF_UPLOAD_SYSTEM.md) | contributor | Digital RF upload, per its text handled by a separate grape-recorder project (to re-check) |
| [superpowers/](superpowers/) | — | design records: plans |

## GNSS VTEC

`hamsci-physics-vtec.service` turns a local GNSS receiver into station VTEC.
It moved here from hf-timestd's `timestd-vtec.service` in v3.96.

- **The unit** runs `python -m hamsci_physics.gnss.vtec_service` as `timestd`
  in `/var/lib/timestd`, and starts only where
  `/etc/hamsci-physics/gnss-vtec.toml` exists.
- **The file** holds `[gnss_vtec]` and, optionally, `[station]`.
  [`config/gnss-vtec.toml.example`](../config/gnss-vtec.toml.example) explains
  each key.
- **Adoption:** sigmond runs `hamsci-physics vtec adopt` before it stops the
  old unit.  The command copies an enabled `[gnss_vtec]` table and the
  Earthdata netrc from `/etc/hf-timestd`, and keeps any file already in place.
- **`sigmond-site-timing`** writes the file when it wires a network receiver.
- **By hand**, for a USB receiver: copy the example to
  `/etc/hamsci-physics/gnss-vtec.toml`, set `device`, and restart the unit.
  The [README](../README.md#gnss-vtec) walks through it.
- **The receiver and the stream:** [GNSS_VTEC.md](GNSS_VTEC.md) covers the
  messages the receiver must send, the TCP port 9000 relay, the orbit feed and
  the checks.
