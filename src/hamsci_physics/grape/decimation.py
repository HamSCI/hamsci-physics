#!/usr/bin/env python3
"""
Multi-Stage Decimation for GRAPE Recorder - Precision Doppler Measurements

================================================================================
PURPOSE
================================================================================
Decimate 24 kHz IQ samples to 10 Hz (factor 2400) while preserving:
    1. Phase continuity (for Doppler measurements)
    2. Flat passband response (for accurate amplitude)
    3. Clean stopband (no aliasing artifacts)

This is Phase 3 of the GRAPE pipeline: prepare data for upload to the
GRAPE/HamSCI/PSWS repository at 10 Hz sample rate.

================================================================================
THEORY: WHY MULTI-STAGE DECIMATION?
================================================================================
Direct decimation by factor R=2400 would require an anti-aliasing filter
with transition bandwidth of (10/2)/24000 = 0.00021 of Nyquist, which would
need ~100,000 taps. This is computationally prohibitive.

Multi-stage decimation splits the problem:
    24 kHz → 400 Hz → 10 Hz
    (R=60)    (R=40)

Each stage has relaxed filter requirements:
    Stage 1: 24 kHz → 400 Hz, transition at 200 Hz (0.8% of Nyquist) → ~100 taps
    Stage 2: 400 Hz → 10 Hz, transition at 5 Hz (2.5% of Nyquist) → 2,427 taps

TOTAL COMPUTATION: about 280 multiplies per 24 kHz input sample, against
~100,000 for a single stage.  As implemented here the CIC runs as four
60-tap boxcars (240), and the final FIR computes all 2,427 taps at 400 Hz
before keeping 1 in 40 (about 40 per input sample).

REFERENCE: Crochiere, R.E. & Rabiner, L.R. (1983). "Multirate Digital Signal
           Processing." Prentice-Hall. Chapter 3.

================================================================================
THEORY: CIC FILTERS (Stage 1)
================================================================================
The CIC (Cascaded Integrator-Comb) filter is ideal for large decimation
factors because it requires NO multipliers - only additions and subtractions.

STRUCTURE:
    ┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
    │ Integrator│ → │ Integrator│ → │   ↓R      │ → │   Comb    │ → ...
    │   1/(1-z⁻¹)│   │   1/(1-z⁻¹)│   │ Decimate │   │  (1-z⁻¹)  │
    └───────────┘   └───────────┘   └───────────┘   └───────────┘
       (N stages)                                      (N stages)

TRANSFER FUNCTION:
    H(z) = [(1 - z^(-RM)) / (1 - z^(-1))]^N

Where:
    R = decimation factor (60 for 24 kHz → 400 Hz)
    M = differential delay (typically 1)
    N = filter order (4 in our implementation)

FREQUENCY RESPONSE:
    |H(f)| = |sin(πfRM/fs) / sin(πf/fs)|^N

This creates:
    - Passband with sinc-like droop (requires compensation)
    - Nulls at multiples of fs/R (perfect alias rejection at those frequencies)

CIC DROOP at edge of 5 Hz passband (f=5 Hz, R=60, fs=24000, N=4):
    |H(5)| = |sin(π×5×60/24000) / sin(π×5/24000)|^4 ≈ 0.987 (−0.11 dB)

REFERENCE: Hogenauer, E.B. (1981). "An economical class of digital filters
           for decimation and interpolation." IEEE Trans. ASSP-29(2), 155-162.

================================================================================
THEORY: COMPENSATION FILTER (Stage 2a)
================================================================================
The CIC's sinc response causes passband droop. We correct this with an
inverse-sinc FIR filter operating at the intermediate rate (400 Hz).

COMPENSATION RESPONSE:
    H_comp(f) = 1 / |H_CIC(f)|   for f ∈ [0, passband_width]
    H_comp(f) = 1                 for f > passband_width

This flattens the passband to within 0.1 dB of unity gain.

WHY AT INTERMEDIATE RATE?
- At 400 Hz, the compensation filter is applied AFTER CIC decimation
- Filter operates on fewer samples (60× fewer than at 24 kHz)
- Passband droop correction is independent of final decimation

REFERENCE: Altera Application Note 455: "Understanding CIC Compensation Filters"

================================================================================
THEORY: FINAL ANTI-ALIASING FILTER (Stage 2b)
================================================================================
Before the final decimation (400 Hz → 10 Hz), we need a sharp lowpass filter:

REQUIREMENTS:
    - Passband: 0 - 5 Hz (Nyquist for 10 Hz output)
    - Stopband: > 6 Hz
    - Stopband attenuation: > 90 dB (prevents aliasing artifacts)

DESIGN METHOD: Kaiser Window
    The Kaiser window provides optimal trade-off between main lobe width
    and side lobe level for a given filter length.

    β = Kaiser shape parameter (calculated from required attenuation)
    N = filter length ≈ (A - 8) / (2.285 × Δω)

    Where:
        A = stopband attenuation (dB)
        Δω = transition width (normalized frequency)

REFERENCE: Kaiser, J.F. (1974). "Nonrecursive digital filter design using
           the I₀-sinh window function." IEEE Int. Symp. Circuits and Systems.

================================================================================
SIGNAL FLOW
================================================================================
    Input: Complex IQ @ 24 kHz (1,440,000 samples/minute)
           │
           ▼
    ┌──────────────────────────────────────────────────────────────────────┐
    │ STAGE 1: CIC Filter (R=60, N=4)                                      │
    │   - Input:  24,000 Hz                                                │
    │   - Output: 400 Hz                                                   │
    │   - Alias rejection at 400, 800, 1200... Hz                          │
    │   - Passband droop: ~0.1 dB at 5 Hz                                  │
    └──────────────────────────────────────────────────────────────────────┘
           │
           ▼ (24,000 samples/minute)
    ┌──────────────────────────────────────────────────────────────────────┐
    │ STAGE 2a: Compensation FIR (R=1, 63 taps)                            │
    │   - Input:  400 Hz                                                   │
    │   - Output: 400 Hz (no decimation)                                   │
    │   - Inverse sinc correction in passband (0-5 Hz)                     │
    │   - Flattens response to < 0.1 dB ripple                             │
    └──────────────────────────────────────────────────────────────────────┘
           │
           ▼
    ┌──────────────────────────────────────────────────────────────────────┐
    │ STAGE 2b: Final FIR + Decimate (R=40, 2,427 taps)                    │
    │   - Input:  400 Hz                                                   │
    │   - Output: 10 Hz                                                    │
    │   - Sharp cutoff at 5 Hz (Nyquist)                                   │
    │   - Kaiser FIR, 2,427 taps at 24 kHz input, ≥ 90 dB from 6 Hz        │
    └──────────────────────────────────────────────────────────────────────┘
           │
           ▼
    Output: Complex IQ @ 10 Hz (600 samples/minute), each sample on the
    instant its label names: the decimator removes its own group delay
    (74,758 input samples, 3.115 s, at 24 kHz) and records that it did.

================================================================================
DESIGN GOALS
================================================================================
1. DOPPLER PRECISION
   - Flat passband 0-5 Hz (< 0.1 dB ripple)
   - Phase continuity (linear phase filters throughout)
   - Resolution: 0.1 Hz Doppler at 10 MHz carrier = 3 m/s velocity

2. CLEAN OUTPUT
   - No aliasing artifacts (> 90 dB stopband)
   - No filter ringing (smooth transitions)
   - Consistent amplitude (flat passband)

3. COMPUTATIONAL EFFICIENCY
   - CIC requires no multiplications
   - Multi-stage reduces total operations by ~100×
   - Real-time capable on modest hardware

================================================================================
USAGE
================================================================================
    from hamsci_physics.grape.decimation import StatefulDecimator
    
    # 60 seconds of 24 kHz IQ data
    iq_24k = np.random.randn(1440000) + 1j*np.random.randn(1440000)
    
    # Decimate to 10 Hz; the decimator keeps filter state between calls
    decimator = StatefulDecimator(input_rate=24000, output_rate=10)
    iq_10hz = decimator.process(iq_24k)
    print(len(iq_10hz))  # 569: the first call holds back latency_outputs (31)

    tail = decimator.flush()  # the 31 held-back samples; then it resets
    print(len(iq_10hz) + len(tail))  # 600 samples (60 seconds × 10 Hz)

================================================================================
REVISION HISTORY
================================================================================
2026-11-25: Group delay removed (DECIMATOR_VERSION 2); prime, flush, describe
2026-11-25: Kaiser normalisation fixed, tap cap removed (was 22 dB at 6 Hz)
2025-12-07: Added comprehensive theoretical documentation
2025-11-01: Added support for 16 kHz input (legacy compatibility)
2025-10-15: Initial implementation with 3-stage pipeline
"""

import numpy as np
from scipy import signal
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Supported input sample rates and their decimation parameters
# All paths converge to 400 Hz intermediate rate, then final FIR decimates to 10 Hz
# To add a new rate: ensure input_rate / cic_decimation = 400
SUPPORTED_INPUT_RATES: Dict[int, Dict] = {
    20000: {
        'cic_decimation': 50,      # 20000 / 50 = 400 Hz
        'total_factor': 2000,      # 20000 / 10 = 2000
        'description': '20 kHz (default)',
    },
    16000: {
        'cic_decimation': 40,      # 16000 / 40 = 400 Hz  
        'total_factor': 1600,      # 16000 / 10 = 1600
        'description': '16 kHz (legacy)',
    },
    24000: {
        'cic_decimation': 60,      # 24000 / 60 = 400 Hz
        'total_factor': 2400,      # 24000 / 10 = 2400
        'description': '24 kHz (standard)',
    },
}

OUTPUT_RATE = 10  # Fixed 10 Hz output for GRAPE
INTERMEDIATE_RATE = 400  # 400 Hz intermediate (after CIC)
FINAL_FIR_DECIMATION = 40  # 400 / 40 = 10 Hz
FINAL_FIR_PASSBAND_HZ = 5.0
FINAL_FIR_STOPBAND_HZ = 6.0
FINAL_FIR_DESIGN_DB = 95.0
ALIAS_REJECTION_FLOOR_DB = 90.0  # required from 6 Hz to 200 Hz
DECIMATOR_VERSION = 2  # 2: removes its own group delay (v3.96)


def _design_cic_filter(decimation_factor: int, order: int = 4) -> dict:
    """
    Design CIC (Cascaded Integrator-Comb) filter parameters.
    
    THEORY:
    -------
    The CIC filter is a multiplier-free implementation of a moving average
    filter cascaded N times. It's ideal for large decimation factors.
    
    Transfer function:
        H(z) = [(1 - z^(-R)) / (1 - z^(-1))]^N
    
    Frequency response:
        |H(f)| = |sin(πfR/fs) / sin(πf/fs)|^N ≈ |sinc(fR/fs)|^N
    
    Properties:
        - Passband droop: ~(π²/6) × (f/fs × R)² × N at small f
        - Nulls at: f = k × fs/R for k = 1, 2, 3, ...
        - These nulls provide alias rejection at folded frequencies
    
    ORDER SELECTION:
    ----------------
    Higher order (N) provides:
        + Better stopband attenuation (~6N dB/octave rolloff)
        + Deeper nulls at alias frequencies
        - More passband droop (must be compensated)
        - Larger output word growth (N × log₂(R) bits)
    
    N=4 is a good compromise: ~24 dB/octave rolloff with manageable droop.
    
    Args:
        decimation_factor: Decimation ratio R (50 for 20 kHz → 400 Hz)
        order: Filter order N (4 = good balance of performance/complexity)
        
    Returns:
        dict with 'order' and 'decimation_factor' for _apply_cic_filter()
        
    Reference:
        Hogenauer (1981), IEEE Trans. ASSP-29(2)
    """
    return {
        'order': order,
        'decimation_factor': decimation_factor
    }


def _apply_cic_filter(samples: np.ndarray, cic_params: dict) -> np.ndarray:
    """
    Apply CIC filter to input samples and decimate.
    
    IMPLEMENTATION:
    ---------------
    This uses a boxcar (moving average) filter approximation rather than
    true CIC integrator/comb stages. This is mathematically equivalent:
    
        True CIC: H(z) = [(1 - z^(-R)) / (1 - z^(-1))]^N
                       = [Σ(k=0 to R-1) z^(-k)]^N
        
        Boxcar:   H(z) = (1/R) × Σ(k=0 to R-1) z^(-k)
        
        N boxcars = (1/R^N) × [Σ z^(-k)]^N
    
    The 1/R normalization is applied to prevent overflow and maintain
    unity DC gain.
    
    FILTER STAGES:
    --------------
    Stage 1: Boxcar(R) → output = (x[n] + x[n-1] + ... + x[n-R+1]) / R
    Stage 2: Boxcar(R) on Stage 1 output
    ...
    Stage N: Boxcar(R) on Stage N-1 output
    Then: Decimate by R (keep every R-th sample)
    
    WHY BOXCAR APPROXIMATION?
    - Simpler to implement with scipy.signal.lfilter
    - Numerically stable (no accumulator overflow)
    - Equivalent frequency response to true CIC
    - True CIC only advantageous in fixed-point hardware
    
    Args:
        samples: Complex input samples at high rate
        cic_params: Dict with 'order' (N) and 'decimation_factor' (R)
        
    Returns:
        Decimated complex samples at output_rate = input_rate / R
        
    Note:
        Output length = floor(input_length / R)
    """
    R = cic_params['decimation_factor']
    N = cic_params['order']
    
    # Boxcar filter coefficients: [1/R, 1/R, ..., 1/R] (R terms)
    # This is equivalent to an R-point moving average
    b = np.ones(R) / R
    a = [1.0]  # FIR filter (no feedback)
    
    # Apply N cascaded stages (equivalent to N-stage CIC)
    # Each stage convolves with the boxcar, effectively raising
    # the sinc response to the Nth power
    filtered = samples
    for _ in range(N):
        filtered = signal.lfilter(b, a, filtered)
    
    # Decimate: keep every R-th sample
    # Safe to do after anti-alias filtering
    decimated = filtered[::R]
    
    logger.debug(f"CIC filter: {len(samples)} → {len(decimated)} samples (R={R}, N={N})")
    return decimated


def _design_compensation_fir(sample_rate: int, passband_width: float, 
                             cic_order: int, cic_decimation: int,
                             num_taps: int = 63) -> np.ndarray:
    """
    Design inverse-sinc FIR filter to compensate for CIC passband droop.
    
    THEORY:
    -------
    The CIC filter has a sinc^N frequency response that droops in the passband:
    
        |H_CIC(f)| = |sin(πfR/fs_orig) / sin(πf/fs_orig)|^N
        
    At the edge of our passband (f=5 Hz), this causes ~0.1-0.5 dB attenuation
    depending on the decimation factor and order.
    
    For Doppler measurements, we need flat passband response. The compensation
    filter inverts the CIC droop within the passband:
    
        |H_comp(f)| = 1 / |H_CIC(f)|   for f ≤ passband_width
        |H_comp(f)| = 1                 for f > passband_width
    
    DESIGN METHOD: Frequency Sampling
    ----------------------------------
    We specify the desired frequency response at discrete points and use
    scipy.signal.firwin2 to design an FIR filter that matches it.
    
    This is more flexible than window-based design because we can specify
    arbitrary magnitude responses.
    
    WHY FIR (not IIR)?
    ------------------
    - Linear phase (no group delay distortion)
    - Inherently stable
    - Predictable transient behavior
    - IIR would have lower latency but nonlinear phase
    
    TAP COUNT SELECTION:
    --------------------
    More taps = better frequency resolution = more accurate compensation
    - 31 taps: ~0.3 dB ripple in passband
    - 63 taps: ~0.1 dB ripple in passband (our choice)
    - 127 taps: ~0.03 dB ripple in passband
    
    Args:
        sample_rate: Current sample rate (400 Hz after CIC decimation)
        passband_width: Width of flat passband in Hz (5 Hz for ±5 Hz Doppler)
        cic_order: Order N of the preceding CIC filter (4)
        cic_decimation: Decimation factor R of the preceding CIC (50)
        num_taps: FIR filter length (63 = good accuracy/efficiency tradeoff)
        
    Returns:
        FIR filter coefficients (real-valued, symmetric for linear phase)
        
    Reference:
        Altera AN-455: "Understanding CIC Compensation Filters"
    """
    # Frequency points for compensation
    nyquist = sample_rate / 2
    freqs = np.linspace(0, nyquist, 500)
    
    # CIC frequency response (normalized)
    # |sinc(π * f * R / fs)|^N
    cic_response = np.ones_like(freqs)
    for f_idx, f in enumerate(freqs):
        if f == 0:
            cic_response[f_idx] = 1.0
        else:
            # Normalized frequency for CIC
            x = np.pi * f * cic_decimation / (sample_rate * cic_decimation)
            cic_response[f_idx] = np.abs(np.sin(x) / x) ** cic_order
    
    # Compensation: inverse of CIC droop within passband
    compensation_response = np.ones_like(freqs)
    for f_idx, f in enumerate(freqs):
        if f <= passband_width:
            # Compensate within passband
            compensation_response[f_idx] = 1.0 / max(cic_response[f_idx], 0.1)  # Avoid division by zero
        else:
            # Unity gain outside passband (let final filter handle stopband)
            compensation_response[f_idx] = 1.0
    
    # Design FIR using frequency sampling method
    # firwin2 creates filter matching arbitrary frequency response
    taps = signal.firwin2(num_taps, freqs, compensation_response, fs=sample_rate)
    
    logger.debug(f"Compensation FIR: {num_taps} taps, passband ±{passband_width} Hz")
    return taps


def _design_final_fir(sample_rate: int = 400,
                      passband_hz: float = FINAL_FIR_PASSBAND_HZ,
                      stopband_hz: float = FINAL_FIR_STOPBAND_HZ,
                      stopband_attenuation_db: float = FINAL_FIR_DESIGN_DB
                      ) -> np.ndarray:
    """
    Design the sharp anti-aliasing FIR for the final 400 Hz to 10 Hz stage.

    Before decimating by 40, everything above the new Nyquist (5 Hz) must
    fall below the noise.  The filter passes 0 to 5 Hz and attenuates
    everything from 6 Hz to Nyquist (200 Hz) by at least
    ALIAS_REJECTION_FLOOR_DB (90 dB); it is designed at 95 dB for margin.

    Method: Kaiser window.  signal.kaiserord takes the transition width
    as a fraction of Nyquist (sample_rate / 2) and returns the length and
    beta for the requested attenuation.  The length is forced odd (type I,
    linear phase, integer group delay).  No cap: at the defaults the
    design needs 2,427 taps.

    Args:
        sample_rate: Sample rate at the filter input (400 Hz)
        passband_hz: Passband edge (5 Hz)
        stopband_hz: Stopband edge (6 Hz)
        stopband_attenuation_db: Design attenuation (95 dB)

    Returns:
        FIR coefficients (real, symmetric, odd length)
    """
    numtaps, beta = signal.kaiserord(
        stopband_attenuation_db, (stopband_hz - passband_hz) / (sample_rate / 2))
    if numtaps % 2 == 0:
        numtaps += 1
    taps = signal.firwin(numtaps, (passband_hz + stopband_hz) / 2,
                         window=('kaiser', beta), fs=sample_rate, scale=True)
    logger.debug(f"Final FIR: {numtaps} taps, passband={passband_hz} Hz, "
                 f"stopband={stopband_hz} Hz, design={stopband_attenuation_db} dB")
    return taps


def stopband_rejection_db(taps: np.ndarray, sample_rate: float,
                          stopband_hz: float, n_points: int = 8192) -> float:
    """Smallest attenuation (positive dB) from stopband_hz to sample_rate / 2."""
    _, h = signal.freqz(taps, worN=np.linspace(stopband_hz, sample_rate / 2, n_points),
                        fs=sample_rate)
    return float(-20 * np.log10(np.max(np.abs(h))))



class StatefulDecimator:
    """
    Stateful decimator that preserves filter state across calls and
    removes its own group delay.

    Filter state carries across calls, so minute boundaries leave no
    transient.  The decimator also picks its two decimation phases so that
    every 10 Hz output stands on the instant its label names: output i of
    a stream belongs to the instant i x 0.1 s after the stream's first
    input sample.  To do that it holds back K = latency_outputs outputs
    (the filters' delay rounded down to whole outputs); the first process()
    call returns K fewer samples than its input covers, and flush() returns
    them at the end of the stream.

    This removes the decimator's own filter delay, a fixed property of the
    code.  It applies no clock correction.  describe() records the
    correction for the output's metadata.

    Usage:
        decimator = StatefulDecimator(input_rate=24000, output_rate=10)
        decimator.prime(previous_minute)        # optional history
        for minute_samples in minute_chunks:
            decimated = decimator.process(minute_samples)
        tail = decimator.flush(lookahead=next_minute)   # or flush()
        # flush() resets the decimator for the next stream
    """

    def __init__(self, input_rate: int = 24000, output_rate: int = 10):
        """
        Initialize stateful decimator.

        Args:
            input_rate: Input sample rate (Hz) - 24000, 20000 or 16000
            output_rate: Output sample rate (Hz) - must be 10
        """
        if output_rate != OUTPUT_RATE:
            raise ValueError(f"Output rate must be {OUTPUT_RATE} Hz")

        if input_rate not in SUPPORTED_INPUT_RATES:
            raise ValueError(f"Unsupported input rate: {input_rate} Hz")

        self.input_rate = input_rate
        self.output_rate = output_rate

        rate_config = SUPPORTED_INPUT_RATES[input_rate]
        self.cic_decimation = rate_config['cic_decimation']

        # Design filters once
        self.cic_params = _design_cic_filter(self.cic_decimation, order=4)

        # CIC boxcar coefficients
        R = self.cic_params['decimation_factor']
        self.cic_b = np.ones(R) / R
        self.cic_a = [1.0]
        self.cic_order = self.cic_params['order']

        self.comp_taps = _design_compensation_fir(
            sample_rate=400,
            passband_width=5.0,
            cic_order=4,
            cic_decimation=self.cic_decimation,
            num_taps=63
        )

        self.final_taps = _design_final_fir(sample_rate=400)

        # Group delay of the whole cascade, in input samples.  Every stage
        # is linear phase with an integer delay: N boxcars of R taps delay
        # N(R-1)/2 input samples (N = 4, so this is whole); each odd-length
        # FIR at 400 Hz delays (taps-1)/2 samples of R input samples each.
        self.input_per_output = input_rate // OUTPUT_RATE      # 40 R
        self.group_delay_input_samples = (
            (self.cic_order * (R - 1)) // 2
            + R * ((len(self.comp_taps) - 1) // 2)
            + R * ((len(self.final_taps) - 1) // 2))           # 74,758 at 24 kHz
        D = self.group_delay_input_samples
        # Keep CIC output n (input rate) when n % R == cic_phase, and
        # 400 Hz output m when m % 40 == final_phase.  Then absolute output
        # j sits at input sample 40 R (j - latency_outputs): exactly on the
        # 10 Hz grid, since D = 40 R K + R p + q.
        self.cic_phase = D % R
        self.final_phase = (D // R) % FINAL_FIR_DECIMATION
        self.latency_outputs = D // self.input_per_output       # 31 at 24 kHz

        # Initialize filter states
        self.reset()

        logger.info(f"StatefulDecimator initialized: {input_rate} → {output_rate} Hz, "
                    f"group delay {D} samples removed")

    def reset(self):
        """Reset all filter states and counters (call at session/channel boundaries)."""
        # CIC: N cascaded stages, each needs state
        self.cic_zi = [signal.lfilter_zi(self.cic_b, self.cic_a) * 0
                      for _ in range(self.cic_order)]

        # Compensation FIR state
        self.comp_zi = signal.lfilter_zi(self.comp_taps, [1.0]) * 0

        # Final FIR state
        self.final_zi = signal.lfilter_zi(self.final_taps, [1.0]) * 0

        # Absolute counters for decimation phase alignment
        self.cic_sample_count = 0
        self.final_sample_count = 0

        # Outputs still to drop: the latency, plus any primed outputs
        self.primed_outputs = 0
        self._discard_remaining = self.latency_outputs
        # Stream bookkeeping (excludes primed input)
        self._stream_input_samples = 0
        self._stream_outputs = 0
        self._processed = False

        logger.debug("Decimator state reset")

    def _filter(self, iq_samples: np.ndarray) -> np.ndarray:
        """Run the cascade; return every new absolute output, before any discard."""
        R = self.cic_params['decimation_factor']

        # STAGE 1: CIC filter with state, decimate on phase q
        filtered = iq_samples
        for i in range(self.cic_order):
            filtered, self.cic_zi[i] = signal.lfilter(
                self.cic_b, self.cic_a, filtered, zi=self.cic_zi[i])
        start = (self.cic_phase - self.cic_sample_count) % R
        self.cic_sample_count += len(filtered)
        iq_400hz = filtered[start::R]
        if len(iq_400hz) == 0:
            return np.array([], dtype=np.complex64)

        # STAGE 2: Compensation FIR with state
        iq_400hz_flat, self.comp_zi = signal.lfilter(
            self.comp_taps, [1.0], iq_400hz, zi=self.comp_zi)

        # STAGE 3: Final FIR with state, decimate by 40 on phase p
        iq_filtered, self.final_zi = signal.lfilter(
            self.final_taps, [1.0], iq_400hz_flat, zi=self.final_zi)
        start = (self.final_phase - self.final_sample_count) % FINAL_FIR_DECIMATION
        self.final_sample_count += len(iq_filtered)
        return iq_filtered[start::FINAL_FIR_DECIMATION].astype(np.complex64)

    def _discard(self, out: np.ndarray) -> np.ndarray:
        """Drop outputs still owed to the latency and the primed history."""
        n = min(self._discard_remaining, len(out))
        self._discard_remaining -= n
        return out[n:]

    def prime(self, iq_samples: np.ndarray) -> None:
        """
        Feed history that precedes the stream; its outputs are discarded.

        Priming fills the filters so the stream's first outputs carry no
        start transient.  The stream's instant 0 becomes the first sample
        after the primed input.

        Raises:
            ValueError: length not a multiple of input_rate // 10
            RuntimeError: called after process()
        """
        if self._processed:
            raise RuntimeError("prime() must come before any process() call")
        if len(iq_samples) % self.input_per_output != 0:
            raise ValueError(
                f"prime length {len(iq_samples)} is not a multiple of "
                f"{self.input_per_output} samples (one output period)")
        n_out = len(iq_samples) // self.input_per_output
        self.primed_outputs += n_out
        self._discard_remaining += n_out
        self._discard(self._filter(np.asarray(iq_samples)))

    def process(self, iq_samples: np.ndarray) -> Optional[np.ndarray]:
        """
        Process samples with preserved filter state.

        Output i of the stream belongs to the instant i x 0.1 s after the
        stream's first input sample.  The first call returns
        latency_outputs fewer samples than its input covers; flush()
        returns them.

        Args:
            iq_samples: Complex IQ samples at input_rate

        Returns:
            Decimated complex64 samples at output_rate, or None if the
            numerics failed
        """
        self._processed = True
        if len(iq_samples) == 0:
            return np.array([], dtype=np.complex64)

        try:
            self._stream_input_samples += len(iq_samples)
            out = self._discard(self._filter(iq_samples))
            self._stream_outputs += len(out)
            return out
        except Exception as e:
            logger.error(f"Stateful decimation failed: {e}")
            return None

    def flush(self, lookahead: Optional[np.ndarray] = None) -> np.ndarray:
        """
        Return exactly the outputs the stream is still owed, then reset.

        The stream's total output equals its input length // (input_rate
        // 10).  To produce the held-back outputs the filters need input
        that follows the stream: flush uses lookahead when given, and
        zeros for whatever it lacks.  Lookahead input yields no outputs of
        its own.

        Args:
            lookahead: Input that follows the stream (optional)

        Returns:
            complex64 array of the owed outputs
        """
        owed = self._stream_input_samples // self.input_per_output - self._stream_outputs
        tail = []
        have = 0
        if owed > 0:
            feed = (np.asarray(lookahead, dtype=np.complex64) if lookahead is not None
                    else np.array([], dtype=np.complex64))
            piece = self.input_per_output * (self.latency_outputs + 1)
            pos = 0
            while have < owed:
                chunk = feed[pos:pos + piece]
                pos += piece
                if len(chunk) < piece:
                    chunk = np.concatenate(
                        [chunk, np.zeros(piece - len(chunk), dtype=np.complex64)])
                out = self._discard(self._filter(chunk))
                tail.append(out)
                have += len(out)
        result = (np.concatenate(tail)[:max(owed, 0)] if tail
                  else np.array([], dtype=np.complex64))
        self.reset()
        return result.astype(np.complex64)

    def describe(self) -> dict:
        """State what the decimator did, for the output's metadata."""
        return {
            'name': 'grape-cic-fir',
            'version': DECIMATOR_VERSION,
            'input_rate_hz': self.input_rate,
            'output_rate_hz': self.output_rate,
            'group_delay_s': self.group_delay_input_samples / self.input_rate,
            'group_delay_removed': True,
            'residual_delay_s': 0.0,
            'final_fir_taps': len(self.final_taps),
            'passband_hz': FINAL_FIR_PASSBAND_HZ,
            'stopband_hz': FINAL_FIR_STOPBAND_HZ,
            'stopband_rejection_db': round(stopband_rejection_db(
                self.final_taps, INTERMEDIATE_RATE, FINAL_FIR_STOPBAND_HZ), 1),
        }


def get_supported_rates() -> Dict[int, Dict]:
    """
    Get dictionary of supported input sample rates.
    
    Returns:
        Dict mapping input_rate to configuration dict with:
        - cic_decimation: CIC filter decimation factor
        - total_factor: Total decimation factor to reach 10 Hz
        - description: Human-readable description
    """
    return SUPPORTED_INPUT_RATES.copy()


def is_rate_supported(input_rate: int) -> bool:
    """Check if an input sample rate is supported for decimation."""
    return input_rate in SUPPORTED_INPUT_RATES
