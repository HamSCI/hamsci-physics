"""F16 (IEEE binary16) decode for 24 kHz complex I/Q raw chunks.

Copy of the decode half of hf-timestd's ``hf_timestd/core/iq_codec.py``,
which stays the source of truth.  hamsci-physics does not depend on
hf-timestd, so it carries its own copy; ``tests/test_raw_reader_f16.py``
pins the two together with hard-coded vectors.

A chunk stored as F16 holds each real and imaginary component as
``binary16(component * F16_SCALE)``.  The sidecar names the dtype and the
scale.
"""

from __future__ import annotations

import numpy as np

F16_SCALE: float = 4096.0  # 2**12, fixed in code
DTYPE_F32 = "complex64"
DTYPE_F16 = "complex-f16"


def decode_iq(raw: bytes, dtype: str, scale: float = 1.0) -> np.ndarray:
    """Decode raw archive bytes to complex64, dividing out ``scale``."""
    if dtype == DTYPE_F32:
        components = np.frombuffer(raw, dtype=np.float32)
    elif dtype == DTYPE_F16:
        components = np.frombuffer(raw, dtype="<f2")
    else:
        raise ValueError(f"unknown I/Q dtype: {dtype!r}")
    values = components.astype(np.float32) / np.float32(scale)
    return values.view(np.complex64)


def bytes_per_sample(dtype: str) -> int:
    """Bytes per complex sample for an I/Q dtype string."""
    if dtype == DTYPE_F32:
        return 8
    if dtype == DTYPE_F16:
        return 4
    raise ValueError(f"unknown I/Q dtype: {dtype!r}")
