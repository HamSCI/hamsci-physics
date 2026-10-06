#!/usr/bin/env python3
"""Command line interface for hamsci-physics.

Contract surface (sigmond CLIENT-CONTRACT §16 non-radiod client):
    version    — machine-readable version info (--json)
    inventory  — what this client is and writes (--json)
    validate   — self-check of config + data paths (--json)

Science products:
    grape      — GRAPE/PSWS daily pipeline (decimate → spectrogram →
                 package → upload), moved here from hf-timestd in the
                 2026-08-24 split.

The data root stays ``/var/lib/timestd`` — a frozen contract of the
split: hamsci-physics reads the timing core's data products in place
rather than copying them.  Only the *config* moved, to
``/etc/hamsci-physics/config.toml``.
"""

import sys
import json
import logging
import argparse
from pathlib import Path

DEFAULT_CONFIG = '/etc/hamsci-physics/config.toml'
DEFAULT_DATA_ROOT = '/var/lib/timestd'      # frozen split contract


def _load_config(path):
    """Read the TOML config, or return {} when it is absent."""
    import toml as _toml
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, 'r') as fh:
        return _toml.load(fh)


def _version_string():
    try:
        from importlib.metadata import version as pkg_version
        return pkg_version('hamsci-physics')
    except Exception:
        from . import __version__
        return __version__


def _handle_version(args):
    info = {
        'name': 'hamsci-physics',
        'version': _version_string(),
        'python': sys.version.split()[0],
    }
    if getattr(args, 'json', False):
        print(json.dumps(info, indent=2))
    else:
        print(f"hamsci-physics {info['version']} (python {info['python']})")
    return 0


def _handle_inventory(args):
    """`hamsci-physics inventory --json` — CLIENT-CONTRACT §3/§16/§17.

    MUST exit 0 even on degraded paths (sigmond's drop-in check calls
    this out explicitly): a client that cannot describe itself is still
    expected to say so in `issues` rather than fail the subprocess.
    """
    from hamsci_physics.contract import build_inventory

    config_path = Path(getattr(args, 'config', None) or DEFAULT_CONFIG)
    payload = build_inventory(_load_config(config_path), config_path)
    print(json.dumps(payload, indent=2))
    return 0


def _handle_validate(args):
    """`hamsci-physics validate --json` — CLIENT-CONTRACT §3/§12.3."""
    from hamsci_physics.contract import build_validate

    config_path = Path(getattr(args, 'config', None) or DEFAULT_CONFIG)
    payload = build_validate(_load_config(config_path), config_path)
    print(json.dumps(payload, indent=2))
    return 0 if payload["ok"] else 1



def _grape_sweep_retry(day: str, data_root, config_path) -> None:
    """Re-invoke this CLI for one backfill day.  Never fatal.

    Spawns ``sys.executable -m hamsci_physics.cli`` rather than ``sys.argv[0]``:
    grape-daily.service runs the CLI as ``python3 -m hamsci_physics.cli``, so
    ``sys.argv[0]`` is the module FILE, which is not executable and whose
    ``#!/usr/bin/env python3`` shebang would resolve to system Python (no
    hamsci_physics).  Spawning it raised PermissionError, and ``check=False``
    does not suppress a failure to spawn -- only a non-zero exit status --
    so the sweep killed an otherwise successful run and never once ran.
    See hf-timestd#26.
    """
    import os
    import subprocess
    cmd = [sys.executable, '-m', 'hamsci_physics.cli', 'grape', 'daily',
           '--date', day,
           '--data-root', str(data_root),
           '--config', str(config_path)]
    try:
        subprocess.run(cmd, env=dict(os.environ, GRAPE_SWEEP='1'),
                       check=False)
    except OSError as exc:
        # Honour the documented contract: per-day failures are non-fatal.
        print(f"   \u26a0\ufe0f  sweep for {day} could not start: {exc!r} "
              f"(continuing)")


def _grape_daily_summary(upload_attempted: bool, upload_ok: bool,
                         pipeline_status: dict) -> list:
    """Trailing upload lines for `grape daily`.

    Extracted because the inline version referenced a `status` dict that no
    scope had held since the hs_uploader switch, so it raised NameError on
    every successful night. The damage was out of all proportion to a
    summary line: the pipeline had already decimated, packaged, uploaded,
    cleaned up and saved state, then died formatting its own report. systemd
    called that a failure -- 30 days with zero successful exits -- and,
    worse, the catch-up sweep that recovers missed days sits AFTER this
    point and had therefore never run once.

    Returning lines instead of printing them is what makes it testable, and
    it is why the caller can afford to treat reporting as non-fatal.
    """
    if not upload_attempted:
        return []
    if not upload_ok:
        return ["   \u26a0\ufe0f  Upload pending \u2014 queued for retry"]
    how = (pipeline_status or {}).get("upload_status", "completed")
    if how == "external":
        return ["   upload: shipped by hs-uploader.service (external daemon)"]
    return [f"   upload: drained via hs_uploader ({how})"]



def run_timing_chain(data_root, date: str, chain_path, out_dir) -> int:
    """`grape timing-chain`: write the day's timing-chain sidecar
    (TIMING_PROVENANCE_MODEL §3.2).  Exit 1 only when nothing was written."""
    from .grape.timing_chain_sidecar import write_day_sidecar
    out = write_day_sidecar(Path(data_root), date, chain_path=Path(chain_path), out_dir=out_dir)
    if out is None:
        print("FAILED: timing chain sidecar not written", file=sys.stderr)
        return 1
    print(str(out))
    return 0


def run_timing_gate(data_root, date: str, history_db, *, as_json: bool = False,
                    raw_glob: str = None) -> int:
    """`grape timing-gate`: the overclaim gate (TIMING_PROVENANCE_MODEL §8).
    Exit 1 when any interval's published uncertainty falls below the scatter
    an independent registration observed."""
    from datetime import datetime, timedelta, timezone
    from .grape.overclaim_gate import assess_day, load_history_rows
    from .grape.timing_chain_sidecar import load_day_states
    date = date.replace('-', '')
    day = datetime.strptime(date, '%Y%m%d').replace(tzinfo=timezone.utc)
    start = day.isoformat().replace('+00:00', 'Z')
    end = (day + timedelta(days=1)).isoformat().replace('+00:00', 'Z')
    states = load_day_states(Path(data_root), date, raw_glob)
    rows = load_history_rows(Path(history_db), start, end) if Path(history_db).exists() else []
    rep = assess_day(states, rows)
    if as_json:
        print(json.dumps(rep.to_dict(), indent=1))
    else:
        print(f"{rep.date or date}: {len(rep.intervals)} intervals, {rep.overclaims} overclaim(s), "
              f"{rep.unwitnessed} unwitnessed")
        for v in rep.intervals:
            if v.overclaim:
                print(f"  {v.counter_space} {v.t_start}: {v.reason}")
    return 1 if rep.overclaims else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='hamsci-physics',
        description='Ionospheric science products from HF time-standard data',
    )
    subparsers = parser.add_subparsers(dest='command', help='Command')

    version_parser = subparsers.add_parser('version', help='Version information')
    version_parser.add_argument('--json', action='store_true', help='JSON output')

    inventory_parser = subparsers.add_parser(
        'inventory', help='Client-contract inventory (JSON)')
    inventory_parser.add_argument('--config', '-c', default=DEFAULT_CONFIG,
                                  help='Config file')
    inventory_parser.add_argument('--json', action='store_true',
                                  help='JSON output (default)')

    validate_parser = subparsers.add_parser(
        'validate', help='Validate configuration (JSON)')
    validate_parser.add_argument('--config', '-c', default=DEFAULT_CONFIG,
                                 help='Config file')
    validate_parser.add_argument('--json', action='store_true',
                                 help='JSON output (default)')

    grape_parser = subparsers.add_parser('grape', help='GRAPE data products (decimation, spectrograms, packaging)')
    grape_subparsers = grape_parser.add_subparsers(dest='grape_command', help='GRAPE command')
    
    # GRAPE daily (full orchestrated pipeline)
    grape_daily_parser = grape_subparsers.add_parser('daily', help='Run full daily pipeline: decimate → spectrogram → package (hs-uploader ships it)')
    grape_daily_parser.add_argument('--data-root', default='/var/lib/timestd', help='Data root directory')
    grape_daily_parser.add_argument('--config', '-c', default=DEFAULT_CONFIG, help='Config file')
    grape_daily_parser.add_argument('--date', help='Date (YYYY-MM-DD or YYYYMMDD, default: yesterday)')
    grape_daily_parser.add_argument('--no-upload', action='store_true', help='Skip upload stage (decimate, spectrogram, package only)')
    grape_daily_parser.add_argument('--debug', '-d', action='store_true', help='Enable DEBUG logging')

    # GRAPE decimate
    grape_decimate_parser = grape_subparsers.add_parser('decimate', help='Decimate 24/20 kHz IQ to 10 Hz')
    grape_decimate_parser.add_argument('--data-root', default='/var/lib/timestd', help='Data root directory')
    grape_decimate_parser.add_argument('--channel', help='Channel name (e.g., "WWV 10 MHz")')
    grape_decimate_parser.add_argument('--date', help='Date (YYYY-MM-DD or YYYYMMDD)')
    grape_decimate_parser.add_argument('--all-channels', action='store_true', help='Process all channels')
    grape_decimate_parser.add_argument('--debug', '-d', action='store_true', help='Enable DEBUG logging')
    
    # GRAPE spectrogram
    grape_spec_parser = grape_subparsers.add_parser('spectrogram', help='Generate carrier spectrograms')
    grape_spec_parser.add_argument('--data-root', default='/var/lib/timestd', help='Data root directory')
    grape_spec_parser.add_argument('--channel', required=True, help='Channel name')
    grape_spec_parser.add_argument('--date', help='Date (YYYY-MM-DD or YYYYMMDD)')
    grape_spec_parser.add_argument('--rolling', type=int, choices=[6, 12, 24], help='Rolling spectrogram (hours)')
    grape_spec_parser.add_argument('--grid', help='Receiver grid square for solar zenith overlay')
    grape_spec_parser.add_argument('--debug', '-d', action='store_true', help='Enable DEBUG logging')
    
    # GRAPE package
    grape_package_parser = grape_subparsers.add_parser('package', help='Package as Digital RF for upload')
    grape_package_parser.add_argument('--data-root', default='/var/lib/timestd', help='Data root directory')
    grape_package_parser.add_argument('--date', help='Date to package (default: yesterday)')
    grape_package_parser.add_argument('--callsign', required=True, help='Station callsign')
    grape_package_parser.add_argument('--grid', required=True, help='Grid square')
    grape_package_parser.add_argument('--debug', '-d', action='store_true', help='Enable DEBUG logging')
    
    # GRAPE status
    grape_status_parser = grape_subparsers.add_parser('status', help='Show upload status and history')
    grape_status_parser.add_argument('--data-root', default='/var/lib/timestd', help='Data root directory')
    grape_status_parser.add_argument('--days', type=int, default=7, help='Days of history to show')
    grape_status_parser.add_argument('--debug', '-d', action='store_true', help='Enable DEBUG logging')

    # Timing provenance (TIMING_PROVENANCE_MODEL §3.2, §8)
    tc = grape_subparsers.add_parser('timing-chain', help="Write the day's timing-chain sidecar (TIMING_PROVENANCE_MODEL §3.2)")
    tc.add_argument('--data-root', type=Path, required=True)
    tc.add_argument('--date', type=str, required=True, help='YYYYMMDD')
    tc.add_argument('--chain-path', type=Path, default=Path('/run/hf-timestd/timing_chain.json'))
    tc.add_argument('--out-dir', type=Path, default=None)

    tg = grape_subparsers.add_parser('timing-gate', help='Overclaim gate: published u_epoch vs observed scatter (spec §8)')
    tg.add_argument('--data-root', type=Path, required=True)
    tg.add_argument('--date', type=str, required=True, help='YYYYMMDD')
    tg.add_argument('--history-db', type=str, default='/var/lib/timestd/authority_history.db')
    tg.add_argument('--json', action='store_true')

    parser._grape_parser = grape_parser
    return parser


def main():
    parser = build_parser()
    grape_parser = parser._grape_parser
    args = parser.parse_args()

    # CONTRACT §3, hard requirement: inventory/validate must emit ONLY
    # the JSON document on stdout.  Every logger goes to stderr before a
    # single subcommand runs, so no "Logging configured" line can ever
    # land in the JSON pipe.
    _root = logging.getLogger()
    _root.handlers.clear()
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setFormatter(
        logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    _root.addHandler(_stderr)
    _root.setLevel(logging.DEBUG if getattr(args, 'debug', False)
                   else logging.INFO)

    if args.command == 'version':
        sys.exit(_handle_version(args))
    elif args.command == 'inventory':
        sys.exit(_handle_inventory(args))
    elif args.command == 'validate':
        sys.exit(_handle_validate(args))
    elif args.command == 'grape':
        # GRAPE data products mode
        from datetime import datetime, timedelta, timezone
        
        if not args.grape_command:
            grape_parser.print_help()
            sys.exit(1)
        
        data_root = Path(args.data_root) if hasattr(args, 'data_root') else None
        
        def resolve_date(date_arg):
            """Resolve date argument to YYYYMMDD string."""
            if not date_arg or date_arg.lower() == 'yesterday':
                return (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime('%Y%m%d')
            return date_arg.replace('-', '')
        
        if args.grape_command == 'daily':
            from .grape.decimation_pipeline import DecimationPipeline
            from .grape.spectrogram import CarrierSpectrogramGenerator
            from .grape.packager import DailyDRFPackager, StationConfig, STANDARD_CHANNELS
            import toml
            import os
            import json as _json

            date_str = resolve_date(args.date)

            # Load config
            config_path = Path(args.config)
            if not config_path.exists():
                print(f"❌ Config not found: {config_path}")
                sys.exit(1)
            with open(config_path, 'r') as f:
                config = toml.load(f)

            station = config.get('station', {})
            callsign = station.get('callsign', 'AC0G')
            grid = station.get('grid_square', 'EM38ww')

            # Use canonical 9 GRAPE channels — not dir scanning which picks up
            # legacy aliases (BPM_10000, WWV_10000, WWVH_10000, etc.)
            all_channels = [name for name, _freq in STANDARD_CHANNELS]
            expected_count = len(all_channels)
            print(f"📡 GRAPE daily pipeline for {date_str}")
            print(f"   Channels: {expected_count} ({', '.join(all_channels)})")

            # Status file for health dashboard
            status_file = data_root / 'upload' / 'grape_status.json'
            pipeline_status = {
                'date': date_str,
                'started_at': datetime.now(tz=timezone.utc).isoformat(),
                'completed_at': None,
                'status': 'running',
                'channels_expected': expected_count,
                'channels_decimated': 0,
                'channels_spectrogram': 0,
                'upload_status': 'pending',
                'upload_completed': 0,
                'upload_failed': 0,
                'error': None,
            }

            def _save_status():
                try:
                    status_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(status_file, 'w') as sf:
                        _json.dump(pipeline_status, sf, indent=2)
                except Exception:
                    pass

            _save_status()

            # === Stage 1: Decimate all channels ===
            print(f"\n━━━ Stage 1: Decimation ({expected_count} channels) ━━━")
            pipeline = DecimationPipeline(data_root)
            decimated = []
            failed_decimate = []

            for ch in all_channels:
                try:
                    print(f"   [{len(decimated)+len(failed_decimate)+1}/{expected_count}] {ch}...")
                    pipeline.process_day(date_str, ch)
                    # Verify output exists
                    ch_dir = ch.replace(' ', '_')
                    dec_file = data_root / 'products' / ch_dir / 'decimated' / f'{date_str}.bin'
                    if dec_file.exists() and dec_file.stat().st_size > 0:
                        decimated.append(ch)
                    else:
                        failed_decimate.append(ch)
                        print(f"   ⚠️  {ch}: decimation produced no output")
                except Exception as e:
                    failed_decimate.append(ch)
                    print(f"   ❌ {ch}: {e}")

            print(f"\n   Decimation: {len(decimated)}/{expected_count} channels")
            pipeline_status['channels_decimated'] = len(decimated)

            # === Gate 1: At least one channel must be decimated ===
            if len(decimated) == 0:
                print(f"   ❌ GATE FAILED: 0/{expected_count} channels decimated")
                print(f"   Aborting — no data to package/upload")
                pipeline_status['status'] = 'failed'
                pipeline_status['error'] = f'0/{expected_count} channels decimated'
                pipeline_status['completed_at'] = datetime.now(tz=timezone.utc).isoformat()
                _save_status()
                sys.exit(1)
            if failed_decimate:
                print(f"   ⚠️  {len(failed_decimate)} channels had no data: {', '.join(failed_decimate)}")
            print(f"   ✅ GATE PASSED: {len(decimated)}/{expected_count} channels decimated")

            # === Stage 2: Generate spectrograms ===
            print(f"\n━━━ Stage 2: Spectrograms ({len(decimated)} channels) ━━━")
            spectrograms = []
            failed_spec = []

            for ch in decimated:
                try:
                    gen = CarrierSpectrogramGenerator(
                        data_root=data_root,
                        channel_name=ch,
                        receiver_grid=grid
                    )
                    result = gen.generate_daily(date_str)
                    if result and result.exists():
                        spectrograms.append(ch)
                        print(f"   ✅ {ch}: {result.name}")
                    else:
                        failed_spec.append(ch)
                        print(f"   ⚠️  {ch}: no spectrogram generated")
                except Exception as e:
                    failed_spec.append(ch)
                    print(f"   ❌ {ch}: {e}")

            print(f"\n   Spectrograms: {len(spectrograms)}/{len(decimated)} channels")
            pipeline_status['channels_spectrogram'] = len(spectrograms)
            _save_status()

            # === Gate 2: At least one spectrogram must exist ===
            if len(spectrograms) == 0:
                print(f"   ❌ GATE FAILED: 0/{len(decimated)} spectrograms generated")
                print(f"   Aborting — no spectrograms to package/upload")
                pipeline_status['status'] = 'failed'
                pipeline_status['error'] = f'0/{len(decimated)} spectrograms generated'
                pipeline_status['completed_at'] = datetime.now(tz=timezone.utc).isoformat()
                _save_status()
                sys.exit(1)
            if failed_spec:
                print(f"   ⚠️  {len(failed_spec)} spectrograms missing: {', '.join(failed_spec)}")
            print(f"   ✅ GATE PASSED: {len(spectrograms)}/{len(decimated)} spectrograms generated")

            # === Stage 3: Package into Digital RF ===
            print(f"\n━━━ Stage 3: Package ━━━")
            try:
                station_config = StationConfig(callsign=callsign, grid_square=grid)
                packager = DailyDRFPackager(data_root=data_root, station_config=station_config)
                packager.package_day(date_str)
                print(f"   ✅ Package complete")
                # Timing provenance, report-only (TIMING_PROVENANCE_MODEL §3.2, §8):
                # the day's chain sidecar beside the OBS output, and the
                # overclaim gate.  Neither may fail the product.
                try:
                    from .grape.timing_chain_sidecar import write_day_sidecar
                    write_day_sidecar(data_root, date_str)
                    rc = run_timing_gate(data_root, date_str, '/var/lib/timestd/authority_history.db')
                    print("   overclaim gate: " + (
                        "OVERCLAIM(S) FOUND — see `hamsci-physics grape timing-gate`" if rc else "clean"))
                except Exception as exc:  # noqa: BLE001 — provenance never fails the product
                    print(f"   ⚠️  timing sidecar/gate step failed (continuing): {exc}")
            except Exception as e:
                print(f"   ❌ Package failed: {e}")
                print(f"   Aborting — will not upload without valid package")
                pipeline_status['status'] = 'failed'
                pipeline_status['error'] = f'Package failed: {e}'
                pipeline_status['completed_at'] = datetime.now(tz=timezone.utc).isoformat()
                _save_status()
                sys.exit(1)

            # === Gate 3: Verify OBS directory exists ===
            upload_dir = data_root / 'upload' / date_str
            obs_dirs = list(upload_dir.rglob('OBS*')) if upload_dir.exists() else []
            if not obs_dirs:
                print(f"   ❌ GATE FAILED: no OBS directory in {upload_dir}")
                pipeline_status['status'] = 'failed'
                pipeline_status['error'] = 'No OBS directory after packaging'
                pipeline_status['completed_at'] = datetime.now(tz=timezone.utc).isoformat()
                _save_status()
                sys.exit(1)
            print(f"   ✅ GATE PASSED: {len(obs_dirs)} dataset(s) ready")
            try:
                from .grape.spool import mark_day_packaged
                mark_day_packaged(upload_dir)
            except OSError as exc:  # a missing marker costs one retry, never the product
                print(f"   ⚠️  could not mark {upload_dir} packaged (continuing): {exc}")

            # === Stage 4: Upload to PSWS ===
            upload_attempted = False
            upload_ok = False

            # hs-uploader.service is the SINGLE outbound path for every
            # HamSCI client (2026-08-26).  grape-daily produces; it no longer
            # ships.  Two uploaders draining one spool against one watermark
            # was never supported -- SqliteWatermarkStore's own docstring
            # calls cross-process writers "an operator config error" -- and
            # the in-process path is what made the 08-25 config-name break
            # look like an upload failure instead of a producer success.
            print(f"\n━━━ Stage 4: Handoff ━━━")
            upload_attempted = True
            upload_ok = True
            pipeline_status['upload_status'] = 'external'
            print(f"   spool: {upload_dir}")
            print(f"   hs-uploader.service ships it (pumps every 30 s)")
            if args.no_upload:
                # Retained for back-compat, but be honest: the daemon scans
                # the spool independently, so this flag cannot hold the data
                # back any more.
                print(f"   ⚠️  --no-upload no longer suppresses upload: the "
                      f"daemon owns the spool.")
                print(f"      To hold data back: systemctl stop hs-uploader.service")

            # === Stage 5: Cleanup ===
            if upload_ok:
                print(f"\n━━━ Stage 5: Cleanup ━━━")
                # Delete decimated .bin files.  Safe to do before the daemon
                # has shipped: they are regenerable from raw, and the packaged
                # OBS dataset (which is what actually gets sent) stays in the
                # spool for the whole retention window.
                cleaned_dec = 0
                for ch in decimated:
                    ch_dir = ch.replace(' ', '_')
                    dec_file = data_root / 'products' / ch_dir / 'decimated' / f'{date_str}.bin'
                    meta_file = data_root / 'products' / ch_dir / 'decimated' / f'{date_str}_meta.json'
                    for f in [dec_file, meta_file]:
                        try:
                            if f.exists():
                                f.unlink()
                                cleaned_dec += 1
                        except Exception as e:
                            print(f"   ⚠️  Could not delete {f.name}: {e}")
                if cleaned_dec > 0:
                    print(f"   Removed {cleaned_dec} decimated files")

                # The packaged dataset STAYS -- hs-uploader has not shipped
                # it yet and its source runs `retention = "keep"`, so deleting
                # here would destroy the day's science before it left the
                # host.  Retention is by age AND proof of delivery instead:
                from .grape.spool import (DEFAULT_MAX_AGE_DAYS, shipped_cursor_ns,
                                          trim_spool)
                try:
                    from hs_uploader.watermark.sqlite import default_path
                    _db = default_path()
                except Exception:            # hs_uploader absent (dev host)
                    _db = Path('/var/lib/hs-uploader/watermarks.db')
                try:
                    _cursor = shipped_cursor_ns(_db)
                    _trim = trim_spool(data_root / 'upload', cursor_ns=_cursor,
                                       max_age_days=DEFAULT_MAX_AGE_DAYS)
                    for line in _trim.summary_lines():
                        print(line)
                except Exception as e:
                    # Retention must never take the pipeline down with it.
                    print(f"   ⚠️  spool trim skipped: {e}")

                # Spectrograms are KEPT — they're the permanent visual record
                print(f"   Spectrograms retained")

            # Finalize status
            pipeline_status['status'] = 'completed' if upload_ok or args.no_upload else 'upload_pending'
            pipeline_status['completed_at'] = datetime.now(tz=timezone.utc).isoformat()
            _save_status()

            print(f"\n✅ GRAPE daily pipeline complete for {date_str}")
            print(f"   {len(decimated)}/{expected_count} channels decimated")
            print(f"   {len(spectrograms)} spectrograms generated")
            # Reporting must not be able to discard a completed run. The
            # NameError this replaced killed the process here, after every
            # stage had succeeded and state was saved -- which also made the
            # catch-up sweep below unreachable. Surface a formatting failure
            # loudly, then carry on; the work is already done and the sweep
            # still needs to run.
            try:
                for line in _grape_daily_summary(upload_attempted, upload_ok,
                                                 pipeline_status):
                    print(line)
            except Exception as exc:                      # pragma: no cover
                print(f"   \u26a0\ufe0f  could not format the upload summary: "
                      f"{exc!r} (pipeline itself completed)")

            # ── catch-up sweep (2026-08-05) ──────────────────────────────
            # The timer only ever processed "yesterday", so a failed or
            # skipped night became a permanent portal hole (AC0G-B4 lost
            # 20260730 entirely and 20260803 to a package crash). After
            # the normal run, retry any of the previous 7 days that
            # spool.day_needs_retry finds unpackaged with source data, by
            # re-invoking this same pipeline per day. GRAPE_SWEEP guards
            # against recursion; failures are per-day and non-fatal.
            if not os.environ.get('GRAPE_SWEEP') and not args.date:
                import subprocess
                from datetime import timedelta
                today = datetime.now(tz=timezone.utc).date()
                for back in range(2, 8):   # yesterday was handled above
                    d = (today - timedelta(days=back)).strftime('%Y%m%d')
                    from .grape.spool import day_needs_retry
                    if not day_needs_retry(data_root, d):
                        continue
                    print(f"\n🔁 sweep: retrying incomplete day {d}")
                    _grape_sweep_retry(d, data_root, config_path)

        elif args.grape_command == 'decimate':
            from .grape.decimation_pipeline import DecimationPipeline
            
            date_str = resolve_date(args.date)
            
            pipeline = DecimationPipeline(data_root)
            
            if args.all_channels:
                # Get all channels from raw_buffer (tiered storage) and raw_archive (legacy)
                channel_set = set()
                for subdir in ['raw_buffer', 'raw_archive']:
                    channels_dir = data_root / subdir
                    if channels_dir.exists():
                        for d in channels_dir.iterdir():
                            if d.is_dir():
                                channel_set.add(d.name.replace('_', ' '))
                if not channel_set:
                    print(f"❌ No raw data found in {data_root}/raw_buffer/ or {data_root}/raw_archive/")
                    sys.exit(1)
                for channel_name in sorted(channel_set):
                    print(f"Processing {channel_name}...")
                    pipeline.process_day(date_str, channel_name)
            elif args.channel:
                pipeline.process_day(date_str, args.channel)  # FIXED: date first, then channel
            else:
                print("❌ Specify --channel or --all-channels")
                sys.exit(1)

                
        elif args.grape_command == 'spectrogram':
            from .grape.spectrogram import CarrierSpectrogramGenerator
            import toml
            
            # Get grid from args or config file
            receiver_grid = args.grid
            if not receiver_grid:
                config_path = Path(DEFAULT_CONFIG)
                if config_path.exists():
                    with open(config_path, 'r') as f:
                        config = toml.load(f)
                    receiver_grid = config.get('station', {}).get('grid_square', '')
                    if receiver_grid:
                        print(f"Using grid from config: {receiver_grid}")
            
            gen = CarrierSpectrogramGenerator(
                data_root=data_root,
                channel_name=args.channel,
                receiver_grid=receiver_grid or ''
            )
            
            if args.date:
                date_str = args.date.replace('-', '')
                gen.generate_daily(date_str)
            elif args.rolling:
                gen.generate_rolling(hours=args.rolling)
            else:
                # Default to yesterday
                date_str = (datetime.now(tz=timezone.utc) - timedelta(days=1)).strftime('%Y%m%d')
                gen.generate_daily(date_str)
                
        elif args.grape_command == 'package':
            from .grape.packager import DailyDRFPackager, StationConfig
            
            date_str = resolve_date(args.date)
            station_config = StationConfig(
                callsign=args.callsign,
                grid_square=args.grid
            )
            packager = DailyDRFPackager(
                data_root=data_root,
                station_config=station_config
            )
            packager.package_day(date_str)
            
        elif args.grape_command == 'timing-chain':
            sys.exit(run_timing_chain(args.data_root, args.date, args.chain_path, args.out_dir))

        elif args.grape_command == 'timing-gate':
            sys.exit(run_timing_gate(args.data_root, args.date, args.history_db, as_json=args.json))

        elif args.grape_command == 'status':
            import sqlite3
            import toml
            from .grape import psws_identity
            from .grape.spool import pending_datasets, shipped_cursor_ns
            from hs_uploader.watermark.sqlite import default_path

            config_path = Path(DEFAULT_CONFIG)
            config = {}
            if config_path.exists():
                with open(config_path, 'r') as f:
                    config = toml.load(f)

            print(f"\n📊 GRAPE Upload Status (hs_uploader → PSWS)")
            # hs-uploader ships GRAPE with the machine's one PSWS key; prove
            # that login with `smd psws verify` (this CLI has no key of its own).
            print("   verify the PSWS login:  smd psws verify")
            upload_root = data_root / 'upload'
            # Read-only observer: building an Uploader here would construct
            # SqliteWatermarkStore, which WRITES on construct (schema init +
            # a group-write chmod).  A status command must never open the
            # daemon's cursor store for writing.
            try:
                cursor = shipped_cursor_ns(default_path())
                pending = pending_datasets(upload_root, cursor)
                station = psws_identity.station_id(config) or "(unset)"
                print(f"   dest:    psws-grape-sftp:"
                      f"{psws_identity.sftp_host(config)}:{station}")
                print(f"   cursor:  {cursor if cursor is not None else '(none — nothing shipped yet)'}")
                print(f"   pending: {len(pending)} un-shipped dataset(s)")
                for o in pending[:10]:
                    print(f"     • {o}")
            except Exception as e:
                print(f"   (could not read delivery state: {e})")

            # Recent attempt outcomes + dead-letter from the watermark DB.
            try:
                c = sqlite3.connect(f"file:{default_path()}?mode=ro", uri=True)
                rows = list(c.execute(
                    "select outcome, count(*), max(ts) from attempts "
                    "where dest_id like '%grape%' group by outcome"
                ))
                print(f"\n   attempts: " + (
                    ", ".join(f"{o}={n} (last {t})" for o, n, t in rows)
                    if rows else "none"))
                dl = list(c.execute(
                    "select count(*) from dead_letter where pipeline like '%grape%'"
                ))[0][0]
                print(f"   dead_letter: {dl}")
                c.close()
            except Exception as e:
                print(f"   (could not read watermark attempts: {e})")
        else:
            grape_parser.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
