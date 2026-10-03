"""Guards the volatility-regime filter added to the signal rule 26 Sep 2026.

Mirrors UpdateEntrySignal: an entry is only allowed when the running candle's
range is ABOVE the median of the recent window. The holdout showed the low
regime at 49.34% against the high regime's 50.61%, so letting a quiet candle
through silently would undo the only filter that survived out of sample.
"""
import unittest


def median_range(candles):
    """candles: list of (high, low, close). Returns the median range ratio."""
    r = sorted((h - l) / c for h, l, c in candles if c)
    return r[len(r) // 2] if r else 0.0


def allows_entry(candles, live):
    """True when the live candle is in the high-volatility regime."""
    h, l, c = live
    return c > 0 and (h - l) / c > median_range(candles)


class VolatilityFilter(unittest.TestCase):
    def setUp(self):
        # Ranges 1..9 over a price of 100 -> median ratio is 5/100.
        self.window = [(100 + n, 100, 100) for n in range(1, 10)]

    def test_median_is_the_middle_range(self):
        self.assertAlmostEqual(median_range(self.window), 0.05)

    def test_above_median_allows_entry(self):
        self.assertTrue(allows_entry(self.window, (106, 100, 100)))

    def test_below_median_blocks_entry(self):
        self.assertFalse(allows_entry(self.window, (103, 100, 100)))

    def test_exactly_median_blocks_entry(self):
        # Strict '>' on purpose: a tie is not evidence of the high regime.
        self.assertFalse(allows_entry(self.window, (105, 100, 100)))

    def test_flat_candle_blocks_entry(self):
        self.assertFalse(allows_entry(self.window, (100, 100, 100)))

    def test_scale_free_across_price_levels(self):
        # Same shape at a 100x price must give the same verdict, or the filter
        # would behave differently on BTCUSD than on a forex pair.
        big = [(10000 + n * 100, 10000, 10000) for n in range(1, 10)]
        self.assertTrue(allows_entry(big, (10600, 10000, 10000)))
        self.assertFalse(allows_entry(big, (10300, 10000, 10000)))

    def test_zero_close_is_rejected_not_crashed(self):
        self.assertFalse(allows_entry(self.window, (10, 0, 0)))

    def test_empty_window_lets_any_moving_candle_through(self):
        # No history yet -> median 0 -> any real range qualifies. Matches the
        # app, which separately requires 25 candles before signalling at all.
        self.assertTrue(allows_entry([], (101, 100, 100)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
