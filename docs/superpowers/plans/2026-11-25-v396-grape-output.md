# v3.96 GRAPE output: samples on their labels, spectrograms off — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The 10 Hz GRAPE product stands where its labels say, rejects aliases by at least 90 dB from 6 Hz, says so in its local metadata, and the nightly job draws no spectrogram unless an operator asks for one.

**Architecture:** `StatefulDecimator` keeps its three filters (four boxcars, the droop compensator, the final FIR) but picks its two decimation phases so that every output sample sits on the 10 Hz grid, and it drops its own latency, so output *i* of a stream belongs to the instant *i* × 0.1 s after the stream's first input sample. The final FIR gets the Kaiser design its docstring describes. The pipeline treats a gap or a counter-epoch change as the end of one stream and the start of another, reads one minute of the neighbouring day at each day edge, and flags the minutes where a stream starts or ends without neighbours. Spectrograms move behind a config switch that defaults off, and run after packaging when on.

**Tech Stack:** Python 3.11, numpy, scipy.signal, digital_rf 2.6, pytest. Repo: `/home/mjh/hamsci/repos/hamsci-physics` (all paths below start there unless they name another repo).

**Spec:** `/home/mjh/hamsci/tasks/hf-timestd-audit/ASSESSMENT.md` ("Michael's decisions, 2026-11-23", items 1 and 2) and `/home/mjh/hamsci/tasks/hf-timestd-audit/part-D-grape-physics.md` (items D3, D4, D5, D6). Builds on hamsci-physics `00301b5` (decode each chunk once; each day in a child process).

---

## Size, scope and estimate

Both decisions fit v3.96.  Eight tasks: six change code (all in hamsci-physics), one changes docs, one measures B4.  No task touches a running service on a station except the last one, and that one only reads.  The code changes alter what `grape-daily.service` (a oneshot under a timer) does on its next run; they touch no long-running service.  Rolling v3.96 onto a station via `smd align` restarts whatever the hamsci-physics component update restarts (for example `hamsci-physics-fusion.service`), as every release does.

What stays for later, and why:

- PNG retention.  Decision 7 gives retention to sigmond's storage ledger; that plan owns it.  With drawing off by default, no new PNGs pile up meanwhile.
- The per-minute timing record (uncertainty, origin, epoch) inside the PSWS-bound files.  It waits on the PSWS acceptance question the audit left open (part D, Q2).  This plan changes no PSWS-bound key.
- Running-sum boxcars, dropping the per-minute `gc.collect()`, keeping the decimated day past Stage 5, and the sweep's definition of done (D2, D3 cost, D10, D11).  The audit filed these under cost, not under decisions 1 and 2.

## What the code showed, checked 2026-11-25 against `00301b5`

I measured the shipped design in the repo's own venv.  The audit's figures hold:

- Final FIR (401 taps, capped from 2,287): −6.5 dB at 5 Hz, −22.2 dB at 6 Hz, −37.8 dB at 10 Hz; a 7 Hz tone comes through the whole decimator at −28.3 dB.  The cause sits in `grape/decimation.py:483-494`: the tap cap, and `kaiser_atten` given a width normalised to the sample rate where it wants Nyquist, so `kaiser_beta` returns 0 (a rectangular window).
- Delay: 13,978 input samples at 24 kHz = 582.4 ms (boxcars 118 samples, compensator 31 × 60, final FIR 200 × 60).  An impulse at output label 50 comes out at index 56.  `decimation_pipeline.py:242-251` writes each block under its input minute.
- One audit citation drifted: part D, item D7, cites `timing_chain_sidecar.py:398-456`; that file has 131 lines (`write_day_sidecar` starts at line 73).  Nothing in this plan depends on it.

A prototype of the design below, run on the devbox: a Kaiser FIR designed for 95 dB (2,427 taps) gives at worst −94.7 dB from 6 Hz to 200 Hz; through the whole decimator a 6 Hz tone comes out at −96 dB and a 7 Hz tone at −114 dB; tones from 0.5 to 5.0 Hz stay within 0.01 dB; a 1 Hz tone's phase matches its label to 1e-12 rad; an impulse at label 500 peaks at index 500.  The removed delay grows to 74,758 input samples (3.115 s), which the decimator now cancels exactly.  The final FIR costs 0.014 s of CPU per channel-minute on the devbox, about 2 CPU-minutes a night for six channels.

## Open questions for Michael

**Answered 2026-11-25: all four as recommended.**

1. **Tell PSWS and GRAPE users?**  The Digital RF that ships cannot say that the samples moved: PSWS rejects unexpected DRF metadata, and an extra file in the payload gets stored but never ingested (`docs/TIMING_STATE.md`, "Where the chain sidecar lands").  So a PSWS user will see each station's data jump 0.582 s earlier on the day that station adopts v3.96, with no flag in the data.  The passband also flattens: the old filter ran +0.5 dB near 4 Hz.  *Recommendation:* you send the PSWS/GRAPE team a dated note naming the change and each station's adoption date.
2. **The decimator record in `gap_summary.json`.**  That file already ships inside every OBS directory, and PSWS stores it without ingesting it.  This plan adds a `decimator` block to each channel's entry there (Task 5).  *Recommendation:* keep it; it costs nothing, and anyone who opens the file learns which filter made the day.  If you say no, Task 5 drops one assertion and Task 3 keeps the block out of the summary.
3. **Where the alias floor starts.**  The documentation promises a flat passband to 5 Hz and 90 dB from 6 Hz, and this plan builds exactly that.  Content between 5 and 6 Hz therefore folds into 4 to 5 Hz, attenuated by 0 to 6 dB.  The alternative (passband to 4 Hz, 90 dB from 5 Hz) folds nothing, but narrows the Doppler window.  *Recommendation:* keep the documented spec; WWV Doppler rarely leaves ±2 Hz.
4. **Re-ship old days?**  The catch-up sweep re-decimates any of the last seven days it judges unpackaged, so a few pre-v3.96 days may go out again with the new alignment.  This plan does not re-ship days on purpose.  *Recommendation:* no backfill; the `decimator` block tells the two apart on the station.

## Global Constraints

- Ship only through sigmond v3.96 and `smd align`.  No hand edit on any station.
- Timing faults get exposed, never silently corrected.  The decimator removes its *own* filter delay, a property of the code, and records that it did; it applies no clock correction.
- PSWS-bound Digital RF stays byte-compatible in shape: dtype `f4`, complex, 10 Hz, `start_global_index` = midnight × 10, 864,000 samples per channel-day, one subchannel per channel, and the Digital Metadata keys exactly `callsign, grid_square, receiver_name, lat, long, center_frequencies, uuid_str`.
- Alias rejection of the final FIR: at least 90 dB from 6 Hz to 200 Hz (`ALIAS_REJECTION_FLOOR_DB = 90.0`), designed at 95 dB.  Passband 0 to 5 Hz flat within 0.1 dB end to end.
- Residual delay after correction: zero to within 1e-6 rad of phase for a 1 Hz tone (that bounds it near 0.16 µs).
- Spectrograms: off unless `[grape] spectrograms = true` (a TOML boolean) in `/etc/hamsci-physics/config.toml`.
- Test fixtures use grid-square identities only (`N0CALL`, `EM38`), never a precise latitude or longitude.
- Tests: `cd /home/mjh/hamsci/repos/hamsci-physics && .venv/bin/python -m pytest --basetemp=/var/tmp/mjh-pt/<name> -o addopts="" <tests> -v`.  Each new test must fail when the code it checks goes away; each task names its mutation check.
- Commits: stage named files only; each message ends with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z`.
- Work on `main` in the local checkout; push only when Michael says so.

## Review Focus

1. **A day whose neighbours lack raw data.**  The first and last 3.1 s of the day then come from a filter fed zeros.  Expect `filter_edge: true` on minute 0 or 1439, never a silent ramp.  Test: Task 4, `test_minutes_beside_a_gap_carry_filter_edge`.
2. **A counter-epoch change mid-day.**  No filter history may cross it, yet the minute before it still needs its last 3.1 s.  Expect a flush with zeros, `filter_edge` on both sides, and 600 samples in every written minute.  Test: Task 4, `test_pipeline_resets_the_decimator_at_an_epoch_boundary` (updated).
3. **A re-run over a day decimated by the old code.**  Old minutes must not survive beside new ones under a new `decimator` record.  Test: Task 4, `test_rerun_starts_the_day_afresh`.
4. **Input at 20 kHz or 16 kHz.**  The phase rule must hold for every rate in `SUPPORTED_INPUT_RATES`.  Test: Task 2, `test_impulse_lands_on_its_label` (parametrised).
5. **A station whose config sets `spectrograms = "true"` (a string).**  Expect off, and a run that still packages.  Test: Task 6, `test_spectrograms_switch_reads_only_a_true_boolean`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/hamsci_physics/grape/decimation.py` | modify | Filter design; `StatefulDecimator` with zero delay, `prime`, `flush`, `describe`; dead helpers removed |
| `src/hamsci_physics/grape/__init__.py` | modify | Stop re-exporting `decimate_for_upload` |
| `src/hamsci_physics/grape/decimated_buffer.py` | modify | `filter_edge` per minute; `decimator` per day; `set_decimator`, `discard_day` |
| `src/hamsci_physics/grape/decimation_pipeline.py` | modify | Streams, day edges, aligned writes |
| `src/hamsci_physics/cli.py` | modify | Spectrogram switch and stage order; Gate 2 removed |
| `config/hamsci-physics-config.toml.template` | modify | `[grape] spectrograms = false` |
| `systemd/grape-daily.service` | modify | Comment on gates |
| `docs/GRAPE_DAILY_PROCESSING.md`, `docs/TIMING_STATE.md`, `README.md` | modify | Describe the new behaviour |
| `tests/test_grape_decimator_alignment.py` | create | Filter and alignment tests |
| `tests/test_grape_pipeline_alignment.py` | create | Pipeline streams, edges, metadata |
| `tests/test_grape_psws_format.py` | create | Pins the PSWS-bound shape |
| `tests/test_grape_spectrogram_switch.py` | create | Switch, order, no Gate 2 |
| `tests/test_raw_reader_f16.py`, `tests/test_grape_counter_epoch.py` | modify | Follow the new decimator interface |

---

### Task 1: Final FIR meets 90 dB; dead decimation helpers go

**Files:**
- Modify: `src/hamsci_physics/grape/decimation.py:202-218` (constants), `:409-499` (`_design_final_fir`), `:502-655` and `:796-840` (dead code), `:1-185` (module docstring)
- Modify: `src/hamsci_physics/grape/__init__.py:11-20`
- Test: `tests/test_grape_decimator_alignment.py` (create)

**Interfaces:**
- Produces: constants `FINAL_FIR_PASSBAND_HZ = 5.0`, `FINAL_FIR_STOPBAND_HZ = 6.0`, `FINAL_FIR_DESIGN_DB = 95.0`, `ALIAS_REJECTION_FLOOR_DB = 90.0`; `_design_final_fir(sample_rate: int = 400, passband_hz: float = FINAL_FIR_PASSBAND_HZ, stopband_hz: float = FINAL_FIR_STOPBAND_HZ, stopband_attenuation_db: float = FINAL_FIR_DESIGN_DB) -> np.ndarray`; `stopband_rejection_db(taps: np.ndarray, sample_rate: float, stopband_hz: float, n_points: int = 8192) -> float` (positive dB: the smallest attenuation from `stopband_hz` to `sample_rate / 2`).

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np
import pytest
from hamsci_physics.grape import decimation as dec
from hamsci_physics.grape.decimation import (
    ALIAS_REJECTION_FLOOR_DB, StatefulDecimator, stopband_rejection_db)


def _tone(freq_hz, seconds, rate=24000):
    n = np.arange(int(seconds * rate))
    return np.exp(2j * np.pi * freq_hz * n / rate).astype(np.complex64)


def test_final_fir_rejects_aliases_by_90_db_from_6_hz():
    taps = StatefulDecimator(24000, 10).final_taps
    assert stopband_rejection_db(taps, 400, 6.0) >= ALIAS_REJECTION_FLOOR_DB
    assert len(taps) % 2 == 1


@pytest.mark.parametrize("freq_hz", [6.0, 7.0, 10.0, 50.0])
def test_tones_above_6_hz_leave_the_decimator_90_db_down(freq_hz):
    y = StatefulDecimator(24000, 10).process(_tone(freq_hz, 90))
    rms_db = 20 * np.log10(np.sqrt(np.mean(np.abs(y[100:]) ** 2)))
    assert rms_db <= -ALIAS_REJECTION_FLOOR_DB


def test_dead_helpers_are_gone():
    for name in ("decimate_for_upload", "decimate_for_upload_simple",
                 "get_decimator", "DECIMATION_FUNCTION"):
        assert not hasattr(dec, name)
```

(The tone test reads `y[100:]` from `process` alone; Task 2 changes what `process` returns at the start, and 100 samples of margin covers both.)

- [ ] **Step 2: Run them; expect FAIL**

Run: `.venv/bin/python -m pytest --basetemp=/var/tmp/mjh-pt/t1 -o addopts="" tests/test_grape_decimator_alignment.py -v`
Expected: FAIL: `ImportError` on `ALIAS_REJECTION_FLOOR_DB` / `stopband_rejection_db` (before you add them), then 22.2 dB < 90 and −28 dB > −90.

- [ ] **Step 3: Implement**

`_design_final_fir`: `numtaps, beta = signal.kaiserord(stopband_attenuation_db, (stopband_hz - passband_hz) / (sample_rate / 2))`; make `numtaps` odd; no cap; `signal.firwin(numtaps, (passband_hz + stopband_hz) / 2, window=('kaiser', beta), fs=sample_rate, scale=True)`.  Expect 2,427 taps at the defaults.  `stopband_rejection_db`: `signal.freqz(taps, worN=np.linspace(stopband_hz, sample_rate / 2, n_points), fs=sample_rate)`, return `-20*log10(max|H|)`.  `StatefulDecimator.__init__` calls `_design_final_fir(sample_rate=400)` with the defaults.  Delete `decimate_for_upload`, `decimate_for_upload_simple`, `get_decimator`, `DECIMATION_FUNCTION`; keep `get_supported_rates` and `is_rate_supported`.  `grape/__init__.py` exports `StatefulDecimator`, `DecimationPipeline`, `DecimatedBuffer`.  Module docstring: Stage 2b reads "Kaiser FIR, 2,427 taps at 24 kHz input, ≥ 90 dB from 6 Hz"; the USAGE block shows `StatefulDecimator`; add a revision line "2026-11-25: Kaiser normalisation fixed, tap cap removed (was 22 dB at 6 Hz)".

- [ ] **Step 4: Run them; expect PASS.  Mutation check:** put back `num_taps = min(num_taps, 401)` and the old `kaiser_atten` line; both filter tests must fail.  Restore.

- [ ] **Step 5: Run the whole suite** (`tests/`): expect PASS.  (`grep -rn decimate_for_upload src tests` returns nothing.)

- [ ] **Step 6: Commit**

```bash
git add src/hamsci_physics/grape/decimation.py src/hamsci_physics/grape/__init__.py tests/test_grape_decimator_alignment.py
git commit -m "grape decimator: final FIR to its promised 90 dB from 6 Hz; drop dead helpers

The Kaiser width went in normalised to the sample rate where kaiser_atten
wants Nyquist, and a 401-tap cap cut the 2,287 taps the formula asked for,
so the window fell to rectangular: 22 dB at 6 Hz, a 7 Hz tone at -28 dB.
Designed now with kaiserord at 95 dB, no cap: >= 94 dB from 6 Hz.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 2: The decimator removes its own delay

**Files:**
- Modify: `src/hamsci_physics/grape/decimation.py` (`StatefulDecimator`, today at `:657-793`)
- Modify: `tests/test_raw_reader_f16.py:127-146` (`test_mixed_day_decimates`)
- Test: `tests/test_grape_decimator_alignment.py`

**Interfaces:**
- Consumes: Task 1's `_design_final_fir`, `stopband_rejection_db`, constants.
- Produces, on `StatefulDecimator(input_rate: int = 24000, output_rate: int = 10)`:
  - `group_delay_input_samples: int` (D), `latency_outputs: int` (K), `cic_phase: int` (q), `final_phase: int` (p)
  - `process(iq_samples: np.ndarray) -> Optional[np.ndarray]`: complex64.  Output *i* of a stream belongs to the instant *i* × 0.1 s after the stream's first input sample (the first sample after `prime`, when primed).  The first call returns K fewer samples than its input covers.
  - `prime(iq_samples: np.ndarray) -> None`: history that precedes the stream; outputs discarded.  Length must be a multiple of `input_rate // 10`; `ValueError` otherwise.  `RuntimeError` after any `process` call.
  - `flush(lookahead: Optional[np.ndarray] = None) -> np.ndarray`: returns exactly the outputs still owed, so the stream's total equals its input length // (`input_rate // 10`).  Uses `lookahead` (input that follows the stream) when given, zeros for whatever it lacks.  The decimator resets afterwards.
  - `describe() -> dict` with keys `name` (`"grape-cic-fir"`), `version` (`DECIMATOR_VERSION = 2`), `input_rate_hz`, `output_rate_hz`, `group_delay_s` (D / input_rate), `group_delay_removed` (True), `residual_delay_s` (0.0), `final_fir_taps`, `passband_hz`, `stopband_hz`, `stopband_rejection_db` (from `stopband_rejection_db`, rounded to 0.1).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_grape_decimator_alignment.py`)

```python
from hamsci_physics.grape.decimation import DECIMATOR_VERSION, SUPPORTED_INPUT_RATES

def _run(d, x, minute):
    out = [d.process(x[i:i + minute]) for i in range(0, len(x), minute)]
    return np.concatenate(out + [d.flush()])


@pytest.mark.parametrize("rate", sorted(SUPPORTED_INPUT_RATES))
def test_impulse_lands_on_its_label(rate):
    x = np.zeros(rate * 120, np.complex64); x[rate * 50] = 1
    y = _run(StatefulDecimator(rate, 10), x, rate * 60)
    assert len(y) == 1200
    assert int(np.argmax(np.abs(y))) == 500
    assert abs(y[499] - y[501]) < 1e-9


@pytest.mark.parametrize("freq_hz", [1.0, 4.0])
def test_tone_phase_matches_its_labels(freq_hz):
    y = _run(StatefulDecimator(24000, 10), _tone(freq_hz, 120), 24000 * 60)
    i = np.arange(len(y))
    err = np.angle(y * np.exp(-2j * np.pi * freq_hz * i / 10))[100:-100]
    assert np.max(np.abs(err)) < 1e-6


@pytest.mark.parametrize("freq_hz", [0.5, 2.0, 4.0, 4.9])
def test_passband_is_flat_to_5_hz(freq_hz):
    y = _run(StatefulDecimator(24000, 10), _tone(freq_hz, 120), 24000 * 60)[100:-100]
    assert abs(20 * np.log10(np.sqrt(np.mean(np.abs(y) ** 2)))) < 0.1


def test_every_minute_yields_600_samples_once_flushed():
    d = StatefulDecimator(24000, 10)
    first = d.process(np.zeros(1_440_000, np.complex64))
    assert len(first) == 600 - d.latency_outputs
    assert len(d.process(np.zeros(1_440_000, np.complex64))) == 600
    assert len(d.flush()) == d.latency_outputs


def test_flush_with_lookahead_equals_a_continuous_run():
    x = _tone(1.0, 180); m = 1_440_000
    whole = _run(StatefulDecimator(24000, 10), x, m)[:1200]
    d = StatefulDecimator(24000, 10)
    part = np.concatenate([d.process(x[:m]), d.process(x[m:2 * m]), d.flush(lookahead=x[2 * m:])])
    np.testing.assert_allclose(part, whole, atol=1e-6)


def test_prime_removes_the_start_transient():
    x = _tone(1.0, 120); m = 1_440_000
    ref = np.exp(2j * np.pi * 1.0 * np.arange(600) / 10)
    primed = StatefulDecimator(24000, 10); primed.prime(x[:m])
    y = np.concatenate([primed.process(x[m:]), primed.flush()])
    assert np.max(np.abs(y[:600] - ref)) < 1e-3
    cold = _run(StatefulDecimator(24000, 10), x[m:], m)
    assert np.abs(cold[0] - ref[0]) > 0.1


def test_prime_after_process_is_refused():
    d = StatefulDecimator(24000, 10); d.process(np.zeros(2400, np.complex64))
    with pytest.raises(RuntimeError):
        d.prime(np.zeros(2400, np.complex64))


def test_describe_states_the_correction():
    d = StatefulDecimator(24000, 10); r = d.describe()
    assert r["version"] == DECIMATOR_VERSION == 2
    assert r["group_delay_removed"] is True and r["residual_delay_s"] == 0.0
    assert r["group_delay_s"] == pytest.approx(d.group_delay_input_samples / 24000)
    assert r["stopband_hz"] == 6.0 and r["stopband_rejection_db"] >= 90.0
```

- [ ] **Step 2: Run; expect FAIL** (`ImportError` on `DECIMATOR_VERSION`; then the impulse peaks at 531, the old latency plus the new filter's).

- [ ] **Step 3: Implement.**  The phase rule, which the tests pin but do not spell out:

```python
R = self.cic_decimation                      # 60, 50 or 40
D = (self.cic_order * (R - 1)) // 2 \
    + R * ((len(self.comp_taps) - 1) // 2) \
    + R * ((len(self.final_taps) - 1) // 2)  # 74,758 at 24 kHz
self.cic_phase   = D % R                     # keep 24 kHz samples n with n % R == q
self.final_phase = (D // R) % 40             # keep 400 Hz samples m with m % 40 == p
self.latency_outputs = D // (40 * R)         # 31: absolute output j sits at input 40R(j-K)
```

`process` keeps absolute counters (as today) but slices `filtered[(q - count) % R :: R]` and `[(p - count) % 40 :: 40]`, then drops the first `K + primed_outputs` absolute outputs, where `primed_outputs = len(prime input) // (40R)`.  `flush` feeds `lookahead` (padded with zeros, or zeros alone) in pieces of `40R × (K + 1)` samples until it has the owed count, returns exactly that many, then calls `reset()`.  `reset()` zeroes counters and the primed count.  Keep the try/except that returns `None` on a numerical failure in `process`.

Update `test_mixed_day_decimates`: append `decimator.flush()` to `outputs` before the length check; the length stays `3 * 10`.

- [ ] **Step 4: Run the new file and `tests/test_raw_reader_f16.py`; expect PASS.  Mutation checks:** set `cic_phase = 0` (the tone phase test fails at ~2.6e-4 rad); drop the K discard (impulse at 531); return `np.zeros(K)` from `flush` regardless of lookahead (the lookahead test fails).  Restore each.

- [ ] **Step 5: Commit**

```bash
git add src/hamsci_physics/grape/decimation.py tests/test_grape_decimator_alignment.py tests/test_raw_reader_f16.py
git commit -m "grape decimator: remove its own group delay; prime, flush, describe

Every 10 Hz sample used to come out 582 ms after the instant its label
named.  The decimator now picks its two decimation phases so each output
sits on the 10 Hz grid, drops its own latency, and states the correction
in describe().  An impulse at label 500 peaks at 500 at 24, 20 and 16 kHz.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01BUMH4psju42aywNgiJv98Z"
```

---

### Task 3: The day file records the decimator and the filter edges

**Files:**
- Modify: `src/hamsci_physics/grape/decimated_buffer.py:46-135` (`MinuteMetadata`, `DayMetadata`), `:147-283` (`DecimatedBuffer`)
- Test: `tests/test_grape_pipeline_alignment.py` (create; metadata tests first)

**Interfaces:**
- Produces: `MinuteMetadata.filter_edge: bool = False`; `DayMetadata.decimator: Optional[dict] = None`, written as `summary["decimator"]` by `to_dict` and read back by `from_dict`; `DecimatedBuffer.write_minute(..., filter_edge: bool = False) -> bool`; `DecimatedBuffer.set_decimator(date_str: str, record: dict) -> None` (cached, marked dirty, saved by `flush_metadata`); `DecimatedBuffer.discard_day(date_str: str) -> None` (unlinks `<date>.bin` and `<date>_meta.json` when present, drops the cache entry).

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run; expect FAIL** (`TypeError: unexpected keyword 'filter_edge'`).
- [ ] **Step 3: Implement** the fields and the two methods as the Interfaces block states.
- [ ] **Step 4: Run; expect PASS.  Mutation check:** drop `'decimator'` from the summary dict in `to_dict`; the first test fails.  Run `tests/test_grape_counter_epoch.py` and `tests/test_grape_decimated_buffer_durability.py` too; expect PASS.
- [ ] **Step 5: Commit** `decimated_buffer.py` and `tests/test_grape_pipeline_alignment.py`: "grape day file: record the decimator and per-minute filter edges" (with the two trailer lines).

---

### Task 4: The pipeline writes aligned minutes and marks its edges

**Files:**
- Modify: `src/hamsci_physics/grape/decimation_pipeline.py:137-275` (`_process_channel_day`)
- Modify: `tests/test_grape_counter_epoch.py:47-88` (fakes follow the new interface)
- Test: `tests/test_grape_pipeline_alignment.py`

**Interfaces:**
- Consumes: Task 2's `StatefulDecimator` (`process`, `prime`, `flush`, `describe`, `latency_outputs`); Task 3's `write_minute(filter_edge=)`, `set_decimator`, `discard_day`.
- Produces: `_neighbour_minute(reader, minute_ts: int, epoch: Optional[str], expected: int) -> Optional[np.ndarray]`: the minute's samples when the reader returns exactly `expected` samples whose sidecar `counter_epoch_id` equals `epoch` (two `None`s count as equal), else `None`.

- [ ] **Step 1: Write the failing tests** (append).  Use a `FakeReader` in the test module: `get_sample_rate` returns 24000; `get_available_minutes(date)` returns the listed day minutes; `read_minute(ts)` returns `(samples, {"timing": {"schema": "v2", "origin": "sysclock", "u_epoch_ns": 8_030_000, "counter_epoch_id": epoch_of[ts]}})` for any ts in a dict it holds (day minutes *and* neighbour minutes), else `(None, None)`.  Install it with `monkeypatch.setattr(decimation_pipeline, "RawBinaryReader", FakeReader)`.  Use the real `StatefulDecimator` and a real `DecimatedBuffer` under `tmp_path`.

```python
def test_an_impulse_lands_at_its_label_in_the_day_file(tmp_path):
    # minutes 10..12 present, impulse at 00:11:30
    ...
    day = np.fromfile(tmp_path / "products/SHARED_5000/decimated" / f"{DAY}.bin", np.complex64)
    assert int(np.argmax(np.abs(day))) == (11 * 60 + 30) * 10


def test_minutes_beside_a_gap_carry_filter_edge(tmp_path):
    # minutes 10..12 present, 9 and 13 missing
    ...
    assert [m[str(k)]["filter_edge"] for k in (10, 11, 12)] == [True, False, True]


def test_day_edges_borrow_a_minute_from_each_neighbouring_day(tmp_path):
    # day minutes 0, 1, 1438, 1439 present; the reader also holds T0-60 and T0+86400, same epoch
    ...
    assert m["0"]["filter_edge"] is False and m["1439"]["filter_edge"] is False


def test_a_neighbour_from_another_epoch_is_not_borrowed(tmp_path):
    # minutes 0 and 1 present; T0-60 carries a different counter_epoch_id
    ...
    assert m["0"]["filter_edge"] is True


def test_rerun_starts_the_day_afresh(tmp_path):
    # pre-write minute 5 with write_minute + flush_metadata, then run with only 10..12 present
    ...
    assert "5" not in m


def test_the_day_records_its_decimator(tmp_path):
    ...
    assert meta["summary"]["decimator"]["group_delay_removed"] is True
```

Here `m` stands for the `minutes` dict of the day's `_meta.json`.  Each `...` builds the reader, runs `DecimationPipeline(tmp_path)._process_channel_day(DAY, "SHARED 5000")`, and loads the files.

- [ ] **Step 2: Run; expect FAIL** (the impulse lands 0.582 s late and then some, and `filter_edge` is missing).

- [ ] **Step 3: Implement.**  The stream logic:

```
discard_day(date); out = zeros(864_000, complex64); rows = {}
stream = None; epoch = None; prev = None
for m in 0..1439:
    samples, meta = read minute m (padded/truncated as today) or (None, None)
    e = timing_state_from_sidecar(meta).counter_epoch_id if samples is not None
    if samples is None or (stream and e != epoch):          # a gap or a new epoch ends the stream
        if stream: put(stream.flush()); rows[prev].edge = True; stream = None
        if samples is None: gap_minutes_total += 1; continue    # gaps no longer run the filters
    if stream is None:
        stream = StatefulDecimator(rate, 10); cursor = m * 600; epoch = e
        history = _neighbour_minute(reader, ts(m) - 60, e, n) if m == 0 else None
        if history is not None: stream.prime(history)
        edge_start = history is None
    put(stream.process(samples)); rows[m] = (state, gap_info, edge_start); edge_start = False; prev = m
if stream:
    ahead = _neighbour_minute(reader, ts(1440), epoch, n) if prev == 1439 else None
    put(stream.flush(lookahead=ahead)); if ahead is None: rows[prev].edge = True
for m, row in rows: write_minute(out[m*600:(m+1)*600], ..., filter_edge=row.edge)
if rows: set_decimator(date, StatefulDecimator(rate, 10).describe())
flush_metadata()
```

`put(y)` writes `y` at `out[cursor:]` and advances `cursor`.  "Epoch differs" keeps today's test: both ids present and unequal.  Treat a minute that the reader lists but cannot read as a gap, as today.  Keep the `gap_samples` arithmetic as it stands.  The docstring says what changed and why (decision 2, 2026-11-23).

Update `tests/test_grape_counter_epoch.py`: its `FakeDecimator` gains `latency_outputs = 0`, `prime(self, s)`, `flush(self, lookahead=None)` returning `np.zeros(0, np.complex64)`, and `describe(self)` returning `{}`.  Its `FakeReader.read_minute` returns `(None, None)` for timestamps outside its three minutes.  Its `FakeBuffer` gains `set_decimator` and `discard_day` as no-ops.  The assertion stays: two decimators built.

- [ ] **Step 4: Run both test files, then the whole suite; expect PASS.  Mutation checks:** remove `discard_day` (the re-run test fails); always pass `lookahead=None` (the day-edge test fails); skip the epoch comparison in `_neighbour_minute` (the other-epoch test fails).  Restore each.

- [ ] **Step 5: Commit** `decimation_pipeline.py`, `tests/test_grape_pipeline_alignment.py`, `tests/test_grape_counter_epoch.py`: "grape pipeline: write each 10 Hz sample under its own label; mark filter edges" (with the trailers).

---

### Task 5: Pin the PSWS-bound shape

**Files:**
- Test: `tests/test_grape_psws_format.py` (create)
- Modify: none expected in `src/hamsci_physics/grape/packager.py`.  `_write_gap_summary` already copies each channel's `summary`, so Task 3's record reaches `gap_summary.json` with no packager change.

**Interfaces:**
- Consumes: `DailyDRFPackager(data_root, StationConfig(callsign="N0CALL", grid_square="EM38"), channels=[("SHARED 5000", 5e6), ("SHARED 10000", 10e6)]).package_day(DAY) -> Path` (the OBS directory); Task 3's buffer methods to build the input.

- [ ] **Step 1: Write the tests.**  A fixture writes three minutes per channel with `DecimatedBuffer.write_minute`, calls `set_decimator(DAY, StatefulDecimator(24000, 10).describe())`, `flush_metadata()`, then packages.

```python
def test_drf_metadata_keys_are_unchanged(packaged):
    md = digital_rf.DigitalMetadataReader(str(packaged / "ch0" / "metadata"))
    lo, hi = md.get_bounds()
    (_, rec), = md.read(lo, hi + 1).items()
    assert sorted(rec) == ["callsign", "center_frequencies", "grid_square", "lat",
                           "long", "receiver_name", "uuid_str"]


def test_drf_layout_is_unchanged(packaged):
    r = digital_rf.DigitalRFReader(str(packaged))
    lo, hi = r.get_bounds("ch0")
    assert lo == int(T0) * 10 and hi - lo + 1 == 864_000
    p = r.get_properties("ch0")
    assert p["samples_per_second"] == 10 and p["is_complex"] and p["num_subchannels"] == 2


def test_gap_summary_names_the_decimator(packaged):
    s = json.loads((packaged / "gap_summary.json").read_text())
    for ch in ("SHARED 5000", "SHARED 10000"):
        assert s["channels"][ch]["decimator"]["version"] == 2
```

- [ ] **Step 2: Run; expect PASS** (Tasks 3 and 4 did the work).  **Mutation checks:** add a `"decimator"` key to the `metadata` dict in `_write_metadata` (the keys test fails); remove `'decimator'` from the day summary (the gap-summary test fails).  Restore both.
- [ ] **Step 3: Commit** the test file: "grape: pin the PSWS-bound DRF shape; gap_summary names the decimator" (with the trailers).

---

### Task 6: Spectrograms off by default, after packaging, never a gate

**Files:**
- Modify: `src/hamsci_physics/cli.py:373-374` (top-level spectrogram import), `:463-501` (Stage 2 and Gate 2), `:615-616`, `:625`, and the `pipeline_status` dict at `:401-413`
- Modify: `config/hamsci-physics-config.toml.template` (`[grape]`)
- Modify: `systemd/grape-daily.service` (the "Gates enforce" comment)
- Test: `tests/test_grape_spectrogram_switch.py` (create)

**Interfaces:**
- Produces: `_spectrograms_enabled(config: dict) -> bool` (True only when `config["grape"]["spectrograms"] is True`); `_run_spectrogram_stage(config: dict, data_root: Path, channels: list, date_str: str, grid: str) -> list` (the channels drawn; `[]` and no import of `hamsci_physics.grape.spectrogram` when off; one channel's exception never escapes).

- [ ] **Step 1: Write the failing tests**

```python
import inspect, sys, toml
from pathlib import Path
from hamsci_physics import cli

def test_spectrograms_switch_reads_only_a_true_boolean():
    assert cli._spectrograms_enabled({}) is False
    assert cli._spectrograms_enabled({"grape": {"spectrograms": "true"}}) is False
    assert cli._spectrograms_enabled({"grape": {"spectrograms": True}}) is True

def test_off_means_no_drawing_and_no_matplotlib(tmp_path):
    sys.modules.pop("hamsci_physics.grape.spectrogram", None)
    assert cli._run_spectrogram_stage({}, tmp_path, ["SHARED 5000"], "20260904", "EM38") == []
    assert "hamsci_physics.grape.spectrogram" not in sys.modules

def test_a_failing_spectrogram_never_escapes(tmp_path, monkeypatch):
    from hamsci_physics.grape import spectrogram
    class Boom:
        def __init__(self, **kw): pass
        def generate_daily(self, d): raise MemoryError("matplotlib")
    monkeypatch.setattr(spectrogram, "CarrierSpectrogramGenerator", Boom)
    cfg = {"grape": {"spectrograms": True}}
    assert cli._run_spectrogram_stage(cfg, tmp_path, ["SHARED 5000"], "20260904", "EM38") == []

def test_spectrograms_run_after_packaging_and_gate_nothing():
    src = inspect.getsource(cli)
    call = "drawn = _run_spectrogram_stage(config, data_root, decimated, date_str, grid)"
    assert src.index("packager.package_day(date_str)") < src.index(call) < src.index("Stage 5: Cleanup")
    assert "spectrograms generated" not in src          # Gate 2 and its message are gone
    assert "sys.exit" not in inspect.getsource(cli._run_spectrogram_stage)

def test_template_ships_spectrograms_off():
    t = toml.load(Path(cli.__file__).parents[2] / "config/hamsci-physics-config.toml.template")
    assert t["grape"]["spectrograms"] is False
```

- [ ] **Step 2: Run; expect FAIL** (`AttributeError: _spectrograms_enabled`).

- [ ] **Step 3: Implement.**  Order inside `grape daily`: Stage 1 decimate, Gate 1, package (with the timing sidecar and overclaim gate as today), Gate 3 and `mark_day_packaged` (keep the text `GATE PASSED: {len(obs_dirs)} dataset(s) ready` and the marker call within 600 characters after it; `test_grape_packaged_marker.py` checks that), then `drawn = _run_spectrogram_stage(config, data_root, decimated, date_str, grid)` (that exact call; the order test finds it), then the handoff and cleanup.  Delete Gate 2.  `_run_spectrogram_stage` catches `Exception` per channel (a `MemoryError` subclasses `Exception`) and prints one line per channel.  Remove the `CarrierSpectrogramGenerator` import at the top of the handler.  `pipeline_status` gains `'spectrograms_enabled': bool`; `channels_spectrogram` stays `None` when off (station-web's `/grape` page then shows "-").  Replace "Spectrograms retained" and the final count line with one line: "spectrograms: off (set [grape] spectrograms = true to draw)", or "spectrograms drawn: N/M".  No line may keep the words "spectrograms generated".  Template:

```toml
# Daily spectrogram PNGs for station-web's /grape page.  Off by default
# (decision 2026-11-23): one channel-day takes about 1.6 GB and 15 s to
# draw, and nothing ships them.  When on, they draw after packaging and
# never hold the upload back.
spectrograms     = false
```

Service comment: "Gate: at least one channel decimated and one OBS dataset packaged.  Spectrograms only when [grape] spectrograms = true; never a gate."

- [ ] **Step 4: Run the new file and `tests/test_grape_packaged_marker.py`, then the suite; expect PASS.  Mutation checks:** make `_spectrograms_enabled` return `bool(value)` (the string case fails); move the stage call above packaging (the order test fails).  Restore.

- [ ] **Step 5: Commit** `cli.py`, the template, `grape-daily.service`, the test: "grape daily: spectrograms off by default, drawn after packaging, never a gate" (with the trailers).

---

### Task 7: Docs say what the product now does

**Files:**
- Modify: `docs/GRAPE_DAILY_PROCESSING.md` ("What It Does", `:47-70`)
- Modify: `docs/TIMING_STATE.md` (new section after "Counter epochs")
- Modify: `README.md:74`

- [ ] **Step 1: Edit.**  GRAPE_DAILY_PROCESSING: stages read decimate, package, spectrograms (only when `[grape] spectrograms = true`); the decimator's stopband (≥ 90 dB from 6 Hz) and that it removes its own 3.115 s delay; minutes beside a gap or an unborrowed day edge carry `filter_edge`.  TIMING_STATE gains "Where the 10 Hz samples stand": each output sample belongs to the instant its index names; the day's `summary.decimator` record (in `_meta.json`, and per channel in the shipped `gap_summary.json`) names version 2; a product without that record came from version 1, whose samples sit 0.582 s after their labels and whose filter rejected 6 Hz by only 22 dB; the Digital RF itself carries no record, because PSWS rejects unexpected DRF metadata.  README line 74: `decimate → package → (spectrogram, if enabled)`.  Write in short active sentences.
- [ ] **Step 2: Run the repo's docs check** (`.github/workflows` names it; run the same command locally).  Expected: PASS.
- [ ] **Step 3: Commit** the three files: "docs: GRAPE samples on their labels; spectrograms optional" (with the trailers).

---

### Task 8: Measure on B4, before and after (read-only, light)

The release ladder (`sigmond/release/RELEASE.md`) brings this code to B4 as part of the v3.96 candidate; this task does not deploy.  Run every command from the devbox.  `F=~/hamsci/ops/bin/fleet-ssh`.  Each command reads only: `systemctl show`, `journalctl`, `ls`, `cat`.

- [ ] **Step 1: Bus and ledger.**  Post on the claude-bus that you will take read-only GRAPE counters on B4 before and after v3.96 (per `/home/mjh/hamsci/CLAUDE.md`).  Open or update the ledger item for "v3.96 GRAPE output" and set it to `measuring`.

- [ ] **Step 2: Before — the same 24-hour unit counters the 11-23 snapshot took.**  Run twice, 24 h apart, both before v3.96 reaches B4, the second after a grape-daily run has finished:

```bash
$F b4 'date -u +%FT%TZ; cat /proc/uptime; for u in $(systemctl list-units --type=service --all --no-legend --plain | cut -d" " -f1); do echo "$u $(systemctl show $u -p NRestarts -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec -p IOReadBytes -p IOWriteBytes -p ActiveState --value | tr "\n" " ")"; done' \
  > ~/hamsci/tasks/hf-timestd-audit/b4/snapshot-$(date -u +%Y%m%dT%H%MZ).txt
```

- [ ] **Step 3: Before — the night's GRAPE run.**  After grape-daily finishes (it starts at 01:00 UTC):

```bash
$F b4 'systemctl show grape-daily.service -p Result -p ExecMainStartTimestamp -p ExecMainExitTimestamp -p CPUUsageNSec -p MemoryPeak'
$F b4 'journalctl -u grape-daily.service --since "today 00:55" --no-pager -o cat | grep -E "Stage|GATE|spectrogram|complete"'
$F b4 'journalctl -k --since "today 00:55" --no-pager | grep -ci "killed process"'
$F b4 'ls /var/lib/timestd/products/*/spectrograms/ 2>/dev/null | grep -c _spectrogram.png'
$F b4 'cat /var/lib/timestd/upload/<YYYYMMDD>/*/*/OBS*/gap_summary.json'
```

Record: CPU-seconds, MemoryPeak, Result, OOM kills, PNG count, and the absence of `decimator` in `gap_summary.json`.  (The 11-23 snapshot showed grape-daily at 17,076 CPU-s and a 2.69 GB peak.)

- [ ] **Step 4: After.**  Once B4 runs the v3.96 candidate, repeat Steps 2 and 3 over the first full night and the 24 h around it.  Also read the shipped metadata keys with the station's own venv:

```bash
$F b4 'ls -d /var/lib/timestd/upload/<YYYYMMDD>/*/*/OBS*/ch0/metadata'
$F b4 '/opt/git/sigmond/hamsci-physics/venv/bin/python -c "import digital_rf as d, glob; p = glob.glob(\"/var/lib/timestd/upload/<YYYYMMDD>/*/*/OBS*/ch0/metadata\")[0]; r = d.DigitalMetadataReader(p); b = r.get_bounds(); print(sorted(list(r.read(b[0], b[1] + 1).values())[0]))"'
$F b4 'journalctl -u hs-uploader.service --since "today 00:55" --no-pager -o cat | grep -c OBS'
```

Pass when all hold: grape-daily `Result=success`; MemoryPeak at least 1 GB below the before figure (no spectrogram drawn); no new PNG for the run date; zero OOM kills; `timestd-core-recorder.service` NRestarts unchanged across the 24 h; every channel in `gap_summary.json` shows `decimator.version` 2, `group_delay_removed` true and `stopband_rejection_db` ≥ 90; the DRF metadata keys print exactly `['callsign', 'center_frequencies', 'grid_square', 'lat', 'long', 'receiver_name', 'uuid_str']`; hs-uploader shipped the OBS.  CPU should not rise; part of any fall comes from `00301b5`, not from this plan, so name both when you report it.

- [ ] **Step 5: Report.**  Post before and after numbers on the bus, move the ledger item to `done` only when Step 4 passes on B4, and keep it open for the other canaries the release ladder names.
