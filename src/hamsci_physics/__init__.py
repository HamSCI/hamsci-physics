"""hamsci-physics — ionospheric science products from HF time-standard data.

The science half of the 2026-08-24 hf-timestd split.  hf-timestd keeps the
real-time timing core (RTP→UTC labelling, fusion, the chrony feed);
everything that turns those labelled observations into *ionospheric*
products lives here:

* ``physics_fusion_service`` — L3 TEC / dTEC fusion across broadcasts
* ``ionospheric_reanalysis`` — offline re-solve of a day's arrivals
* ``tid_detector`` — travelling-ionospheric-disturbance detection
* ``propagation_stats`` — per-path propagation statistics
* ``grape`` — the GRAPE/PSWS daily pipeline (decimate → spectrogram →
  package → upload)
* ``gnss`` — GNSS receiver input and VTEC estimation, moved from
  hf-timestd in v3.96

Both repos consume the shared engines from ``hamsci_dsp``; this package
never imports ``hf_timestd`` (pinned by ``tests/test_import_lint.py``).  It
reads the timing core's data products through the frozen contracts: the
``/var/lib/timestd`` data root and its SQLite data-product store.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
