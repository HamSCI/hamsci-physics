# GRAPE Daily Processing

## Overview

GRAPE (GRAPE Recorder and Processor Engine) runs as a daily batch job to process raw IQ data from hf-timestd into data products for PSWS upload.

## Systemd Timer

The GRAPE daily processing is managed by a systemd timer that runs at 01:00 UTC each day.

### Installation

```bash
# Copy service and timer files
sudo cp systemd/grape-daily.service /etc/systemd/system/
sudo cp systemd/grape-daily.timer /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable and start the timer
sudo systemctl enable --now grape-daily.timer
```

There is no upload timer: hs-uploader.service ships the packaged datasets and
retries on its own.

### Management

```bash
# Check timer status
systemctl status grape-daily.timer

# Check when it will run next
systemctl list-timers grape-daily.timer

# View logs from last run
journalctl -u grape-daily.service -n 100

# Manually trigger a run (for testing)
sudo systemctl start grape-daily.service

# Disable the timer
sudo systemctl disable --now grape-daily.timer
```

## What It Does

The daily job processes **yesterday's data** through the following pipeline:

1. **Decimate** - Convert 24/20 kHz raw IQ to 10 Hz decimated IQ
   - Reads from raw buffer (handles both legacy 1-minute files and 10-minute chunk files)
   - Enumerates all 1440 expected minutes per day explicitly (no gaps missed at day boundaries)
   - Single `StatefulDecimator` per channel preserves filter state across minutes
   - Multi-stage: CIC (R=60) → compensation FIR → final FIR (R=40)
   - Outputs to `/var/lib/timestd/products/{CHANNEL}/decimated/`

2. **Spectrograms** - Generate carrier spectrograms
   - Creates daily spectrograms for all configured channels
   - Edge tapering at gap boundaries (half-cosine, 5s) replaces zero interpolation
   - Full-window validity masking: any NFFT=512 window overlapping a gap is NaN-masked
   - Outputs to `/var/lib/timestd/products/{CHANNEL}/spectrograms/`

3. **Package** - Package as Digital RF
   - Creates DRF packages for PSWS
   - Outputs to `upload/{YYYYMMDD}/{CALLSIGN}_{GRID}/{RECEIVER}@{ID}/OBS.../ch0/`

The job uploads nothing.  hs-uploader's `grape-psws` pipeline picks the `OBS*`
datasets up from `upload/` and ships them to PSWS.

## Upload Behavior

hs-uploader owns delivery, so packaging never waits on PSWS.  If the machine's
key is not yet registered with PSWS, the datasets simply wait in `upload/` and
ship once the portal accepts it.  `grape daily --no-upload` still parses but
changes nothing.

```bash
hamsci-physics grape status     # cursor, pending datasets, recent outcomes
smd psws verify                 # prove the SFTP login with the machine's key
journalctl -u hs-uploader -f    # watch it ship
```

## Configuration

Edit `/etc/systemd/system/grape-daily.service` to customize:

- **Channels**: Add/remove spectrogram generation for specific channels
- **Upload**: Uncomment package/upload lines when ready
- **Resource limits**: Adjust `CPUQuota` and `MemoryMax` if needed

After editing:

```bash
sudo systemctl daemon-reload
sudo systemctl restart grape-daily.timer
```

## Monitoring

### Check Last Run

```bash
systemctl status grape-daily.service
```

### View Logs

```bash
# Last 100 lines
journalctl -u grape-daily.service -n 100

# Follow live
journalctl -u grape-daily.service -f

# Logs from specific date
journalctl -u grape-daily.service --since "2026-01-02"
```

### Check for Failures

```bash
# Show failed runs
systemctl list-timers --failed

# Check service status
systemctl is-failed grape-daily.service
```

## Manual Execution

To process a specific date manually:

```bash
# Decimate specific channel
hamsci-physics grape decimate --channel "SHARED 10000" --date 2026-01-02

# Decimate all channels
hamsci-physics grape decimate --all-channels --date 2026-01-02

# Generate spectrogram
hamsci-physics grape spectrogram --channel "SHARED 10000" --date 2026-01-02

# Package for upload (hs-uploader ships it)
hamsci-physics grape package --date 2026-01-02 --callsign AC0G --grid EM28
```

## Preflight Check

Before relying on uploads, prove the PSWS login with the machine's key:

```bash
smd psws verify
```

It tests TCP reach to `pswsnetwork.eng.ua.edu:22`, then an SFTP login as the
station id with `/etc/hs-uploader/keys/id_ed25519_host`, the one key hs-uploader
uses (create and register it with `smd psws enroll`).

## Troubleshooting

### Timer not running

```bash
# Check if timer is enabled
systemctl is-enabled grape-daily.timer

# Check timer status
systemctl status grape-daily.timer

# Enable if needed
sudo systemctl enable --now grape-daily.timer
```

### Service failing

```bash
# View detailed logs
journalctl -u grape-daily.service -xe

# Check permissions
ls -la /var/lib/timestd/grape/
ls -la /var/log/timestd/grape/

# Ensure directories exist
sudo mkdir -p /var/lib/timestd/grape /var/log/timestd/grape
sudo chown timestd:timestd /var/lib/timestd/grape /var/log/timestd/grape
```

### Missing data

```bash
# Check raw data exists
ls -la /var/lib/timestd/raw_archive/

# Check decimated output
ls -la /var/lib/timestd/products/{CHANNEL}/decimated/
```

## Performance

The daily job typically takes:

- **Decimation**: ~5-10 minutes per channel
- **Spectrograms**: ~1-2 minutes per channel
- **Total**: ~30-60 minutes for the active channels (6; the 3 CHU channels are disabled while CHU is off-air)

Resource usage:

- **CPU**: Limited to 50% (configurable)
- **Memory**: Limited to 2GB (configurable)
- **Disk I/O**: Moderate (reading raw data, writing products)

## Timing provenance (2026-09-04)

After the package step the daily pipeline writes the day's timing-chain
sidecar and runs the overclaim gate, both report-only. What the consumer
reads from each chunk's `timing` block, the counter-epoch rule, where the
sidecar lands and why it is not yet in the PSWS payload, and what the gate
compares: see `docs/TIMING_STATE.md`.
