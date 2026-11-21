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
| [NASA_EARTHDATA_SETUP.md](NASA_EARTHDATA_SETUP.md) | operator | a NASA Earthdata account for the CDDIS downloads |
| [DRF_UPLOAD_SYSTEM.md](DRF_UPLOAD_SYSTEM.md) | contributor | Digital RF upload, per its text handled by a separate grape-recorder project (to re-check) |
| [superpowers/](superpowers/) | — | design records: plans |
