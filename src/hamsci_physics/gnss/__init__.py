"""GNSS receiver input and VTEC estimation.

These modules came from hf-timestd (``hf_timestd.core``) in v3.96, so the
timing core runs without the science half:

* ``ubx_parser`` — u-blox UBX-RXM-RAWX/SFRBX frames
* ``rtcm3_adapter`` — RTCM 3 MSM receivers (the Quectel LG290P)
* ``broadcast_ephemeris`` — GPS broadcast orbits and geodetic helpers
* ``brdc_fetcher`` — daily RINEX navigation files for the RTCM path
* ``gnss_source`` — serial and TCP receiver connections
* ``gnss_tec`` — the dual-frequency slant-to-vertical TEC analyzer
"""
