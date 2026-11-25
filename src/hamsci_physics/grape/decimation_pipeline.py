"""
Decimation Pipeline - Orchestrate reading, decimation, and storage
"""

import logging
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional

from .raw_reader import RawBinaryReader
from .decimated_buffer import DecimatedBuffer, SAMPLES_PER_MINUTE
from .decimation import StatefulDecimator

logger = logging.getLogger(__name__)


# Grade ladder and the unknown sentinel live in timing_state since 2026-09-04;
# re-exported so existing importers keep working.
from .timing_state import GRADE_LADDER as _GRADE_LADDER  # noqa: F401
from .timing_state import UNKNOWN as _UNKNOWN_STATE
from .timing_state import timing_state_from_sidecar
UNKNOWN_TIMING = _UNKNOWN_STATE.legacy_tuple


def timing_from_sidecar(meta):
    """(d_clock_ms, uncertainty_ms, quality_grade) for one raw chunk.

    The sidecar records the registration under ``timing`` — since 2026-09-04
    the schema v2 ``state`` record (TIMING_PROVENANCE_MODEL §3.1), before
    that the Offset Judge verdict (``offset_ns``, ``offset_sigma_ns``,
    ``judge_tier``) — written by ``binary_archive_writer``.  This pipeline
    used to look for flat ``uncertainty_ms`` / ``quality_grade`` /
    ``d_clock_ms`` keys that no producer has ever written, so every minute
    of every product shipped the unknown sentinels regardless of tier
    (AC0G-B4: all 1440 minutes of 20260814).

    Flat keys still win where a producer does supply them.  Absent both,
    the sentinels are returned unchanged: a chunk recorded without a
    verdict must keep saying so.

    Delegates to ``timing_state.timing_state_from_sidecar`` since 2026-09-04;
    that module reads ``u_epoch_ns`` and never ``judge_tier``.
    Forward-only — already-written products are not revisited.
    """
    return timing_state_from_sidecar(meta).legacy_tuple


def _per_minute_gap(meta: Optional[dict]) -> int:
    """Estimate per-minute raw-sample gap from a chunk's sidecar metadata.

    The recorder writes one ``gap_samples`` value covering the entire
    chunk file (``file_duration_sec`` seconds — typically 600 = 10 min).
    ``RawBinaryReader.read_minute`` returns one minute's slice plus the
    chunk's full metadata, so naively assigning ``meta['gap_samples']``
    to each minute inflates ``total_gap_samples`` by ``chunk_minutes×``
    when summed across the chunk (a 30-second gap shows up as 5 minutes
    of gap in the daily summary).

    Spread the chunk-wide value evenly across the chunk's minutes so
    aggregates are exact: ``N × (chunk_gap // N) ≈ chunk_gap`` (modulo
    integer-division rounding ≤ ``N - 1`` raw samples per chunk).
    Per-minute precision is approximate, but it was already an illusion
    — every minute reported the same chunk-wide value before this fix.
    """
    if not meta:
        return 0
    chunk_gap = int(meta.get('gap_samples', 0) or 0)
    if chunk_gap <= 0:
        return 0
    chunk_dur_sec = int(meta.get('file_duration_sec', 60) or 60)
    chunk_minutes = max(1, chunk_dur_sec // 60)
    return chunk_gap // chunk_minutes


def _neighbour_minute(reader, minute_ts: int, epoch: Optional[str],
                      expected: int) -> Optional[np.ndarray]:
    """One raw minute borrowed from a neighbouring day, or None.

    Returns the minute's samples only when the reader holds exactly
    ``expected`` samples for it and its sidecar's ``counter_epoch_id``
    equals ``epoch`` (two ``None``s count as equal).  No filter history
    may span a re-based counter (MEASUREMENT_MODEL §3), and a short or
    padded minute would put zeros, not signal, into the filters.
    The borrow is optional: any fault in reading the minute or its
    sidecar returns None, and the caller runs that edge unprimed.
    """
    try:
        samples, meta = reader.read_minute(minute_ts)
        if samples is None or len(samples) != expected:
            return None
        if timing_state_from_sidecar(meta).counter_epoch_id != epoch:
            return None
        return np.asarray(samples, dtype=np.complex64)
    except Exception as e:
        logger.warning(f"neighbour minute {minute_ts} unusable, edge runs unprimed: {e}")
        return None

class DecimationPipeline:
    """
    Pipeline to process raw high-rate station data into 10 Hz products.
    
    Flow:
    1. Read RawBinaryReader (24 kHz, minute chunks)
    2. Decimate via StatefulDecimator (24 kHz -> 10 Hz)
    3. Write to DecimatedBuffer (10 Hz, daily files)
    """
    
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        
    def process_day(self, date_str: str, channel: Optional[str] = None):
        """
        Process a full day of data.
        
        Args:
            date_str: Date to process (YYYYMMDD or YYYY-MM-DD)
            channel: Specific channel to process (None for all)
        """
        # Normalize date
        if '-' in date_str:
            date_str = date_str.replace('-', '')
            
        # Discover channels if not specified
        channels_to_process = []
        if channel:
            channels_to_process = [channel]
        else:
            # Look in raw_archive/raw_buffer for directories
            # We check both locations to be safe
            for subdir in ['raw_archive', 'raw_buffer']:
                p = self.data_root / subdir
                if p.exists():
                    # hf-timestd uses underscores for directory names
                    # We convert back to spaces for "channel names" if needed, 
                    # but RawBinaryReader and DecimatedBuffer handle the mapping.
                    # Best to stick to what the directories actually are.
                    for d in p.iterdir():
                        if d.is_dir():
                            # Convert directory name to channel name format
                            # e.g., SHARED_10000 -> SHARED 10000
                            name = d.name.replace('_', ' ')
                            if name not in channels_to_process:
                                channels_to_process.append(name)
        
        # Deduplicate
        channels_to_process = sorted(list(set(channels_to_process)))
        
        if not channels_to_process:
            logger.warning("No channels found to process")
            return

        logger.info(f"Processing {len(channels_to_process)} channels for {date_str}")
        
        for ch in channels_to_process:
            try:
                self._process_channel_day(date_str, ch)
            except Exception as e:
                logger.error(f"Failed to process {ch}: {e}", exc_info=True)

    def _process_channel_day(self, date_str: str, channel_name: str):
        """
        Process one channel for one day.

        Enumerates all 1440 expected minutes (minute 0 through 1439) so that
        gap accounting is explicit and complete, including gaps at the start
        or end of the day.

        Since v3.96 (decision 2, 2026-11-23) each 10 Hz sample lands under
        its own label.  The decimator removes its own filter delay: it holds
        back latency_outputs samples and returns them from flush().  So the
        pipeline runs the day as streams of contiguous readable minutes.  A
        gap, a minute the decimator fails on, or a change of counter epoch
        (MEASUREMENT_MODEL §3: a radiod restart renumbers the samples) ends
        the stream; the pipeline flushes it, and the next readable minute
        starts a fresh decimator.  Stream outputs go into a day array at the
        stream's first minute times 600, so a lost minute never shifts a
        later label.  Gaps no longer run zeros through the filters.

        At the day's two edges the pipeline borrows one raw minute from the
        neighbouring day, when that minute exists in the same counter
        epoch: the minute before midnight primes the first stream, and the
        minute after the day feeds the last flush.  A minute whose filters
        ran into zeros (beside a gap, a failure, an epoch change, or an
        unborrowed day edge) carries ``filter_edge = True``.  So does a
        short minute the pipeline pads with zeros, and the minute after it,
        which the filters' 3.1 s reach still touches.

        The pipeline builds the day aside, in the buffer's staging
        directory, and replaces the day's .bin and _meta.json only after
        the build and its metadata flush succeed.  The replacement holds
        only the new run's minutes, so no minute from an older run survives
        beside them.  A fault during the build leaves the old product as it
        was.  The pipeline also keeps the old product, and logs an error,
        when the raw data yields no minute or fewer valid minutes than the
        old product holds: the sweep re-runs days whose raw data has been
        evicted, wholly or in part, and a partial rebuild must not replace
        a fuller product.
        """
        logger.info(f"Starting {channel_name} for {date_str}")

        reader = RawBinaryReader(self.data_root, channel_name)
        day_buffer = DecimatedBuffer(self.data_root, channel_name)
        output_buffer = day_buffer.open_staging(date_str)
        try:
            self._build_channel_day(date_str, channel_name, reader, output_buffer,
                                    day_buffer)
        finally:
            day_buffer.discard_staging(date_str)

    def _build_channel_day(self, date_str, channel_name, reader, output_buffer,
                           day_buffer):
        """Build the day into output_buffer (staged); commit it if it earns it."""
        input_rate = reader.get_sample_rate(date_str)
        logger.info(f"  Input rate: {input_rate} Hz")

        expected_raw_samples = input_rate * 60  # e.g., 1440000 for 24kHz
        decimation_ratio = input_rate // 10

        available_minutes = set(reader.get_available_minutes(date_str))

        from datetime import datetime as _dt, timezone as _tz
        day_start_dt = _dt.strptime(date_str, '%Y%m%d').replace(tzinfo=_tz.utc)
        day_start_ts = int(day_start_dt.timestamp())

        out = np.zeros(1440 * SAMPLES_PER_MINUTE, dtype=np.complex64)
        rows = {}               # minute index -> row for write_minute
        record = None           # the decimator's describe(), for the day

        gap_minutes_total = 0
        stream = None           # the decimator of the current stream
        epoch = None            # the counter epoch in force in the stream
        cursor = 0              # next output's index in `out`
        last_good = None        # last minute the stream processed cleanly
        edge_start = False
        padded_last = False     # the stream's previous minute was zero-padded

        def put(y, end):
            """Write y at the cursor, never past sample `end`."""
            nonlocal cursor
            if y is None or len(y) == 0:
                return
            n = max(0, min(len(y), end - cursor))
            out[cursor:cursor + n] = y[:n]
            cursor += n

        def end_stream(lookahead=None):
            """Flush the stream; flag its last minute unless lookahead fed it."""
            nonlocal stream
            if stream is None:
                return
            if last_good is not None:
                tail = stream.flush(lookahead=lookahead)
                put(tail, (last_good + 1) * SAMPLES_PER_MINUTE)
                if lookahead is None:
                    rows[last_good]['filter_edge'] = True
            else:
                stream.flush()
            stream = None

        import gc

        for minute_index in range(1440):
            minute_ts = day_start_ts + minute_index * 60
            samples, meta = None, None
            gap_info = 0
            padded_now = 0          # zeros the pipeline appended to this minute

            if minute_ts in available_minutes:
                samples, meta = reader.read_minute(minute_ts)
                if samples is not None and len(samples) == 0:
                    samples = None
                if samples is not None:
                    if len(samples) < expected_raw_samples:
                        gap_info = expected_raw_samples - len(samples)
                        padded = np.zeros(expected_raw_samples, dtype=np.complex64)
                        padded[:len(samples)] = samples
                        samples = padded
                        padded_now = gap_info
                    elif len(samples) > expected_raw_samples:
                        samples = samples[:expected_raw_samples]

            state = timing_state_from_sidecar(meta) if samples is not None else None
            e = state.counter_epoch_id if state is not None else None

            # A gap, or a new counter epoch, ends the stream.  "New epoch"
            # means both ids present and unequal (MEASUREMENT_MODEL §3).
            new_epoch = stream is not None and bool(e) and bool(epoch) and e != epoch
            if samples is None or new_epoch:
                if new_epoch:
                    logger.info(f"  {channel_name}: counter epoch {epoch} -> {e} at minute "
                                f"{minute_index}; ending the decimator stream (MEASUREMENT_MODEL §3)")
                end_stream()
                if samples is None:
                    gap_minutes_total += 1
                    continue

            if stream is None:
                stream = StatefulDecimator(input_rate=input_rate, output_rate=10)
                record = stream.describe()
                cursor = minute_index * SAMPLES_PER_MINUTE
                epoch = e
                last_good = None
                padded_last = False
                history = (_neighbour_minute(reader, minute_ts - 60, e, expected_raw_samples)
                           if minute_index == 0 else None)
                if history is not None:
                    try:
                        stream.prime(history)
                    except Exception as exc:
                        logger.warning(f"  {channel_name}: priming from the previous day failed "
                                       f"({exc}); minute 0 starts unprimed and carries filter_edge")
                        stream = StatefulDecimator(input_rate=input_rate, output_rate=10)
                        history = None
                edge_start = history is None
            if e:
                epoch = e

            y = stream.process(samples)
            del samples
            if y is None:
                # The decimator failed on this minute: end the stream before
                # it, count the minute as a gap, start afresh after it.
                logger.warning(f"  {channel_name}: decimation failed at minute {minute_index}; "
                               f"treating it as a gap")
                end_stream()
                gap_minutes_total += 1
                continue
            put(y, (minute_index + 1) * SAMPLES_PER_MINUTE)

            # The sidecar's gap_samples is chunk-wide; spread it across the
            # chunk's minutes (see _per_minute_gap), then convert to 10 Hz.
            gap_info = max(gap_info, _per_minute_gap(meta))
            if decimation_ratio > 0:
                gap_info = gap_info // decimation_ratio

            # Padding zeros reach the padded minute and, through the
            # filters' 3.1 s span, the start of the minute after it.  When
            # the minute holds less real signal than that span, they reach
            # back into the end of the minute before it too.
            if (padded_now and last_good == minute_index - 1
                    and expected_raw_samples - padded_now < stream.group_delay_input_samples):
                rows[last_good]['filter_edge'] = True
            rows[minute_index] = dict(minute_ts=minute_ts, state=state, gap_info=gap_info,
                                      filter_edge=bool(edge_start or padded_now or padded_last))
            edge_start = False
            padded_last = bool(padded_now)
            last_good = minute_index
            gc.collect()

        if stream is not None:
            ahead = (_neighbour_minute(reader, day_start_ts + 1440 * 60, epoch, expected_raw_samples)
                     if last_good == 1439 else None)
            end_stream(lookahead=ahead)

        minutes_processed = 0
        for minute_index, row in sorted(rows.items()):
            state = row['state']
            lo = minute_index * SAMPLES_PER_MINUTE
            if output_buffer.write_minute(
                minute_utc=float(row['minute_ts']),
                decimated_iq=out[lo:lo + SAMPLES_PER_MINUTE],
                d_clock_ms=state.d_clock_ms,
                uncertainty_ms=state.uncertainty_ms,
                quality_grade=state.quality_grade,
                gap_samples=row['gap_info'],
                counter_epoch_id=state.counter_epoch_id,
                origin=state.origin,
                filter_edge=row['filter_edge'],
            ):
                minutes_processed += 1
        if rows:
            output_buffer.set_decimator(date_str, record)
        output_buffer.flush_metadata()
        del stream, out
        gc.collect()

        if day_buffer.has_day(date_str):
            old_valid = day_buffer.valid_minutes(date_str)
            if minutes_processed == 0:
                logger.error(
                    f"  {channel_name} {date_str}: no raw minute readable; keeping the "
                    f"existing product ({old_valid} valid minutes) untouched")
                return
            if minutes_processed < old_valid:
                logger.error(
                    f"  {channel_name} {date_str}: raw data yields {minutes_processed} valid "
                    f"minutes but the existing product holds {old_valid}; keeping the "
                    f"existing product rather than replace it with a partial one")
                return
        if minutes_processed == 0:
            logger.info(f"  {channel_name} {date_str}: no raw minute readable; nothing written")
            return
        day_buffer.commit_staging(date_str)

        logger.info(
            f"  Completed {channel_name}: {minutes_processed} valid, "
            f"{gap_minutes_total} gaps, {minutes_processed * SAMPLES_PER_MINUTE} samples"
        )
