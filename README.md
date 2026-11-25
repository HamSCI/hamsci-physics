# hamsci-physics

Ionospheric science products derived from HF time-standard observations —
the science half of the 2026-08-24 `hf-timestd` split.

`hf-timestd` keeps the real-time timing core: RTP→UTC sample labelling, the
broadcast fusion that produces `D_clock`, and the chrony feed. Everything
that turns those labelled observations into *ionospheric* products lives
here:

| Product | Module | Unit |
|---|---|---|
| L3 TEC / dTEC fusion | `physics_fusion_service` | `hamsci-physics-fusion.service` |
| Daily reanalysis of arrivals | `ionospheric_reanalysis` | `hamsci-physics-reanalysis.timer` |
| Travelling ionospheric disturbances | `tid_detector` | (in-process, fusion) |
| Per-path propagation statistics | `propagation_stats` | (in-process, reanalysis) |
| GRAPE / PSWS daily datasets | `grape/` | `grape-daily.timer` |
| GNSS VTEC from a local receiver (from hf-timestd, v3.96) | `gnss/` | `hamsci-physics-vtec.service` |
| IONEX / IRI / space-weather inputs | `cddis`, `scripts/` | `hamsci-physics-ionex-download.timer`, `…-iri-update.timer` |

`hamsci-physics-iri-update.timer` runs daily, and sigmond enables it with the
other units. It refreshes the IRI index files (`apf107.dat`, `ig_rz.dat`)
only in PHaRLAP's copy, `/opt/pharlap_4.7.4/dat/iri2020`. It leaves the
`iri2020` Python package's copy alone: that package's Fortran segfaults on
current index files.

## GNSS VTEC

`hamsci-physics-vtec.service` reads a dual-frequency GNSS receiver and writes
station-local vertical TEC to the `L3_gnss_vtec` table.  It took over from
hf-timestd's `timestd-vtec.service` in v3.96 and keeps that unit's outputs:
same table, same user (`timestd`), same working directory
(`/var/lib/timestd`), so `data/gnss_vtec.csv`, `data/dcb` and `data/brdc`
land where they always did.

The service reads its own file, `/etc/hamsci-physics/gnss-vtec.toml`, and
starts only where that file exists.  A station without a receiver has no file,
and the unit stays idle there.  Three things write the file:

- **Adoption.**  When sigmond moves the service from hf-timestd, it first runs
  `hamsci-physics vtec adopt`.  That command copies the `[gnss_vtec]` table
  from `/etc/hf-timestd/timestd-config.toml`, comments and all, but only when
  the table reads `enabled = true` and names a real host or a device.  It
  carries a non-zero `[station]` latitude and longitude along, so an RTCM3
  receiver that sends a zero position keeps a fallback.  It also copies
  `/etc/hf-timestd/earthdata-netrc` to `/etc/hamsci-physics/earthdata-netrc`
  for the DCB download.  It never overwrites a file already there, and it
  never prints a coordinate.  Then sigmond stops the old unit and starts the
  new one.
- **`sigmond-site-timing`**, when it wires a network receiver for a site.
- **You, by hand,** for a receiver on USB:

  ```bash
  sudo cp /opt/git/sigmond/hamsci-physics/config/gnss-vtec.toml.example \
      /etc/hamsci-physics/gnss-vtec.toml
  sudoedit /etc/hamsci-physics/gnss-vtec.toml   # set device = "/dev/serial/by-id/…"
  sudo systemctl restart hamsci-physics-vtec.service
  ```

  The unit runs with the `dialout` group, so it can open the serial port.

Without a `[station]` table in `gnss-vtec.toml`, the service takes the
station's position from `STATION_LAT` and `STATION_LON` in
`/etc/sigmond/coordination.env`, then from the centre of the grid square.

## Where the boundary is

Three rules define the split, and the test suite pins all three:

1. **hamsci-physics never imports `hf_timestd`** (`tests/test_import_lint.py`).
   Shared engines — TEC solvers, propagation models, the ionosphere stack,
   the io/schema data layer — come from
   [`hamsci-dsp`](https://github.com/HamSCI/hamsci-dsp), which both repos
   depend on.
2. **The data root does not move.** `/var/lib/timestd` is a frozen contract:
   this client reads the timing core's products *in place* (`raw_buffer/`,
   `phase2/`) and writes its own under `phase2/fusion`, `phase2/science` and
   `upload/`. Only the *config* moved, to `/etc/hamsci-physics/config.toml`.
3. **The GRAPE upload pipeline moved verbatim.** Its `source_id`
   (`grape-datasets:/var/lib/timestd/upload`) and transport name are
   hs-uploader watermark keys over a keep-retention spool; changing them
   would re-ship every dataset PSWS already has
   (`tests/test_deploy_contract.py`).

## Install

Through sigmond, like any other client:

```bash
smd install hamsci-physics
smd config init hamsci-physics     # fills [station] from the station identity
smd start hamsci-physics
```

`deploy.toml` is the manifest sigmond reads. hf-timestd must be installed on
the same host — it produces what this client consumes.

## Development

```bash
uv sync --extra dev
uv run pytest tests/            # ~210 tests
```

`hamsci-dsp` and `hs-uploader` are editable siblings (`[tool.uv.sources]`),
so a `git pull` of either propagates without a reinstall.

## Contract surface

```bash
hamsci-physics version --json
hamsci-physics inventory --json    # what this client is and writes
hamsci-physics validate --json     # exit 1 on any fail-severity issue
hamsci-physics grape daily         # decimate → package → (spectrogram, if enabled)
hamsci-physics vtec adopt          # copy hf-timestd's [gnss_vtec] and netrc here
```

## Status

All three products are live. The L3 fusion service runs continuously —
on AC0G/B4 it writes about 14 carrier-phase dTEC records a minute, plus
differential dTEC across WWV, WWVH and BPM, anchored to GNSS VTEC.

GRAPE reads hf-timestd's raw archive in either sample format, `complex64`
or the trial `f16`.

One known wart, inherited from the HDF5→SQLite migration: the service's
upstream-freshness check and channel discovery still read the old
file-tree layout. Neither gates the science (the freshness result is
advisory — the loop continues either way), but a permanently "stale"
health signal is a metric that lies, so both are being moved to DB reads.
