"""
core/signal_math.py — the calculations behind the chart views (CMP13). No Qt.

Chart timestamps say when a line was processed, not when the device took the sample,
and lines arrive in batches. So the spectrum doesn't trust individual timestamps: it
takes the average sample rate over the whole stretch and treats samples as evenly
spaced, which is right for a device sending at a steady rate.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: The newest samples used for a spectrum; more only adds cost.
MAX_FFT_SAMPLES = 65536
MIN_FFT_SAMPLES = 8
MIN_BINS, MAX_BINS = 10, 100


@dataclass
class Spectrum:
    freq: np.ndarray
    amplitude: np.ndarray
    sample_rate: float
    peak_freq: float
    peak_amplitude: float


def spectrum(t: np.ndarray, v: np.ndarray) -> Spectrum | None:
    """Amplitude spectrum of the newest samples; ``None`` if there are too few or no
    time has passed. A sine of amplitude A reads about A at its frequency."""
    n = min(len(v), MAX_FFT_SAMPLES)
    if n < MIN_FFT_SAMPLES:
        return None
    t, v = t[-n:], v[-n:]
    span = float(t[-1] - t[0])
    if span <= 0:
        return None
    rate = (n - 1) / span
    window = np.hanning(n)
    amplitude = 2 * np.abs(np.fft.rfft((v - v.mean()) * window)) / window.sum()
    freq = np.fft.rfftfreq(n, 1 / rate)
    peak = int(np.argmax(amplitude[1:])) + 1
    return Spectrum(freq, amplitude, rate, float(freq[peak]), float(amplitude[peak]))


@dataclass
class Histogram:
    edges: np.ndarray
    counts: np.ndarray
    mean: float
    std: float
    low: float
    high: float


def histogram(v: np.ndarray) -> Histogram | None:
    """``None`` if there are no values. Bin count follows the data, within limits."""
    if len(v) == 0:
        return None
    bins = int(np.clip(len(np.histogram_bin_edges(v, bins="fd")) - 1, MIN_BINS, MAX_BINS))
    counts, edges = np.histogram(v, bins=bins)
    return Histogram(edges, counts, float(v.mean()), float(v.std()), float(v.min()), float(v.max()))


def align_xy(tx: np.ndarray, vx: np.ndarray, ty: np.ndarray, vy: np.ndarray
             ) -> tuple[np.ndarray, np.ndarray]:
    """Pair two channels by time: each Y sample with X at that moment, for as long as
    both have data. Channels from one line share timestamps, so they pair exactly."""
    if len(tx) == 0 or len(ty) == 0:
        return np.array([]), np.array([])
    keep = (ty >= tx[0]) & (ty <= tx[-1])
    ty = ty[keep]
    return np.interp(ty, tx, vx), vy[keep]


def normalise(v: np.ndarray, visible: np.ndarray) -> tuple[np.ndarray, float, float]:
    """*v* scaled so *visible* spans 0 to 1, with that range. A flat signal sits at 0.5."""
    if len(visible) == 0:
        return np.full(len(v), 0.5), 0.0, 0.0
    low, high = float(visible.min()), float(visible.max())
    if high == low:
        return np.full(len(v), 0.5), low, high
    return (v - low) / (high - low), low, high
