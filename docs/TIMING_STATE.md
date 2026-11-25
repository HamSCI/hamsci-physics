# Timing state — what GRAPE reads, and what it never reads

The chunk sidecars that `RawBinaryReader` opens carry a `timing` block.
Since 2026-09-04 that block is the schema v2 `state` record of
`hf-timestd/docs/design/TIMING_PROVENANCE_MODEL.md` §3.1: the registration
in force for the chunk and its uncertainty. This page says what the GRAPE
consumer does with it. The reasoning lives in the spec and in
`hf-timestd/docs/design/MEASUREMENT_MODEL.md` §3, §8, §9; the code in
`grape/timing_state.py`, `grape/timing_chain_sidecar.py`,
`grape/overclaim_gate.py`.

## What the consumer reads

`u_epoch_ns`, with its `k` and `p`, decides the minute's uncertainty and
grade. `stability_ns` describes the ruler over `tau_s`. `counter_epoch_id`
names whose numbering the registration belongs to. `origin` names the
registration method, `native_anchor` or `sysclock`, or is null with a
`reason`. The Offset Judge offset, mirrored at top level for one release
and otherwise under `engineering`, supplies `d_clock_ms`.

## What it never reads

`judge_tier`. The tier is engineering shorthand for how the registration
was obtained; it lives under `engineering` and may be logged, never
branched on. A minute whose tier says T6 and whose `u_epoch_ns` says
12 seconds is a 12-second minute. `tests/test_grape_timing_state.py`
pins this: a lying tier changes nothing.

## The grade ladder's scope

A/B/C/D at 2/4/8 ms stays for the fusion chain, unchanged. It has no
resolution for a microsecond-class chain: every payload-anchored chunk
grades A. Choosing its replacement belongs to Phase 3 (spec §3.0). X
still means "no verdict", which is a different statement from a bad one.

## The counter epoch rule

A radiod restart renumbers the samples, so a registration carried across
it errs by seconds (MEASUREMENT_MODEL §3). Each minute's metadata records
`counter_epoch_id` and `origin`; the day summary counts `counter_epochs`,
lists `epoch_boundaries`, and counts `origin_switches`. At an epoch
boundary the decimation pipeline builds a fresh `StatefulDecimator`, so no
filter history spans a re-based counter. Legacy sidecars carry no epoch
and decode exactly as before.

## Where the 10 Hz samples stand

Each 10 Hz output sample belongs to the instant its index names. The
decimator removes its own group delay (about 3.1 s with the version 2 filter;
the day's `decimator` record gives the exact value for its input rate),
so no reader needs to shift the samples.

Each day's `_meta.json` summary carries a `decimator` record: name
`grape-cic-fir`, version 2, and the filter's design values. The shipped
`gap_summary.json` repeats that record for each channel. A product without
the record came from version 1. Its samples sit 0.582 s after their labels,
and its filter rejected 6 Hz by only 22 dB.

The Digital RF itself carries no such record. PSWS rejects unexpected DRF
metadata, so the record travels in the sidecar files only.

## Where the chain sidecar lands, and why not in the payload

`hamsci-physics grape timing-chain` writes
`<data_root>/timing/timing-chain-YYYYMMDD.jsonl`: the station's `chain`
records once, then a `state` line per counter space whenever its stable
identity (`chain`, `origin`, `counter_epoch_id`, `a_level`,
`a_level_provenance`) changes, plus a heartbeat every 600 s. That is
mag-recorder's change-plus-heartbeat policy. The spec (§3.0.1) asks for
the file inside the OBS zip. It does not go there yet: PSWS ingestion keys
on payload convention, mag-recorder found on 2026-08-21 that an extra file
in the payload is stored and never ingested, and GRAPE's PSWS server
rejects unexpected DRF metadata. The licensing condition for moving it is
a PSWS acceptance test in which an OBS containing
`timing-chain-<date>.jsonl` is ingested and the file survives. Until that
test passes, the sidecar stays beside the OBS output and is not uploaded.

## The overclaim gate

`hamsci-physics grape timing-gate` compares, for every state record's
interval, the published claim `k × u_epoch_ns` against the scatter an
independent registration observed over the same interval, read from
`/var/lib/timestd/authority_history.db` (MEASUREMENT_MODEL §9 invariant 5;
spec §8). The witness follows the origin so the comparison stays inside
one frame: a `native_anchor` record is judged by the RMS of T5's offsets,
a `sysclock` record by the largest host-clock T2 disagreement. No witness
rows in the interval means unwitnessed, never a failure; an absent
registration never overclaims. The CLI exits 1 when any interval
overclaims. The daily pipeline runs both commands after packaging and
reports the result without failing the product.
