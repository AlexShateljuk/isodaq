"""The chart views' calculations (CMP13), on signals whose answer is known."""
import pytest

np = pytest.importorskip("numpy")

from core.signal_math import MAX_BINS, MIN_BINS, align_xy, histogram, normalise, spectrum  # noqa: E402


def sine(freq, amp=1.0, rate=1000.0, seconds=2.0, offset=0.0):
    t = np.arange(int(rate * seconds)) / rate
    return t, offset + amp * np.sin(2 * np.pi * freq * t)


@pytest.mark.parametrize("freq", [5.0, 50.0, 123.0, 400.0])
def test_spectrum_finds_the_frequency_and_amplitude(freq):
    t, v = sine(freq, amp=3.0)
    s = spectrum(t, v)
    assert s.peak_freq == pytest.approx(freq, abs=1.0)
    assert s.peak_amplitude == pytest.approx(3.0, rel=0.1)
    assert s.sample_rate == pytest.approx(1000.0, rel=0.01)


def test_spectrum_ignores_a_large_offset():
    t, v = sine(50.0, offset=10000.0)
    assert spectrum(t, v).peak_freq == pytest.approx(50.0, abs=1.0)


def test_spectrum_of_two_tones_sees_both():
    t, a = sine(50.0, amp=2.0)
    _, b = sine(200.0, amp=1.0)
    s = spectrum(t, a + b)
    assert s.peak_freq == pytest.approx(50.0, abs=1.0)
    near = s.amplitude[np.abs(s.freq - 200.0) < 2]
    assert near.max() == pytest.approx(1.0, rel=0.1)


def test_spectrum_survives_batched_arrival_times():
    """Lines reach the chart in batches: many samples share one timestamp."""
    t, v = sine(50.0)
    arrival = np.floor(t / 0.033) * 0.033     # as processed every 33 ms
    assert len(np.unique(arrival)) < len(t) / 20
    # The last batch sets the span, so the estimate is a little off, not wildly off.
    assert spectrum(arrival, v).peak_freq == pytest.approx(50.0, rel=0.05)


@pytest.mark.parametrize("n", [0, 1, 7])
def test_spectrum_needs_samples(n):
    assert spectrum(np.arange(n, dtype=float), np.zeros(n)) is None


def test_spectrum_needs_time_to_have_passed():
    assert spectrum(np.zeros(100), np.arange(100.0)) is None


def test_spectrum_of_a_constant_doesnt_fail():
    assert spectrum(np.arange(100.0), np.full(100, 5.0)).peak_amplitude == pytest.approx(0.0)


def test_histogram_counts_and_stats():
    rng = np.random.default_rng(1)
    v = rng.normal(10.0, 2.0, 50000)
    h = histogram(v)
    assert h.counts.sum() == len(v)
    assert h.mean == pytest.approx(10.0, abs=0.05) and h.std == pytest.approx(2.0, abs=0.05)
    assert h.low == v.min() and h.high == v.max()
    centre = h.edges[int(np.argmax(h.counts))]
    assert centre == pytest.approx(10.0, abs=1.0)


@pytest.mark.parametrize("v", [[5.0], [5.0] * 100, list(range(3)), list(range(100000))])
def test_histogram_bin_count_stays_within_limits(v):
    h = histogram(np.array(v, dtype=float))
    assert MIN_BINS <= len(h.counts) <= MAX_BINS and h.counts.sum() == len(v)


def test_histogram_empty():
    assert histogram(np.array([])) is None


def test_align_same_timestamps_pair_exactly():
    t = np.arange(5.0)
    x, y = align_xy(t, t * 2, t, t * 3)
    assert list(x) == [0, 2, 4, 6, 8] and list(y) == [0, 3, 6, 9, 12]


def test_align_different_times_interpolates_within_the_overlap():
    tx = np.array([0.0, 10.0])
    vx = np.array([0.0, 100.0])
    ty = np.array([-5.0, 5.0, 15.0])
    x, y = align_xy(tx, vx, ty, np.array([1.0, 2.0, 3.0]))
    assert list(x) == [50.0] and list(y) == [2.0]


def test_align_empty():
    empty = np.array([])
    assert len(align_xy(empty, empty, np.arange(3.0), np.arange(3.0))[0]) == 0
    assert len(align_xy(np.arange(3.0), np.arange(3.0), empty, empty)[0]) == 0


def test_normalise():
    v = np.array([10.0, 20.0, 30.0])
    scaled, low, high = normalise(v, v)
    assert list(scaled) == [0.0, 0.5, 1.0] and (low, high) == (10.0, 30.0)


def test_normalise_flat_and_empty():
    assert list(normalise(np.array([5.0, 5.0]), np.array([5.0, 5.0]))[0]) == [0.5, 0.5]
    assert list(normalise(np.array([5.0]), np.array([]))[0]) == [0.5]


def test_normalise_scales_by_the_visible_part_only():
    v = np.array([1000.0, 10.0, 20.0])
    scaled, low, high = normalise(v, v[1:])
    assert (low, high) == (10.0, 20.0) and scaled[2] == 1.0
