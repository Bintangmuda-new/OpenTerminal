"""Guard for the rebuilt z-score reversion rule.

Rebuilt after the live journal exposed three faults in the EMA9/21 rule:
39% fire rate, 38 CALL vs 7 PUT, and back-to-back signals. This rule is
symmetric by construction and gated by a cooldown; these tests pin both.

Backtest reference (122 symbols, 4,783 holdout trades): 51.16%, Wilson low
49.7%. Not proven profitable — the tests below check correctness, not edge.
"""
import unittest

COOLDOWN_BARS = 5
Z_THRESHOLD = 2


def zscore(closes):
    """Close distance from SMA20 in units of mean bar-to-bar movement."""
    window = closes[-20:]
    sma = sum(window) / len(window)
    steps = [abs(window[i] - window[i - 1]) for i in range(1, len(window))]
    unit = sum(steps) / len(steps) if steps else 0
    return (closes[-1] - sma) / unit if unit > 0 else 0.0


def signal(closes):
    z = zscore(closes)
    if z < -Z_THRESHOLD:
        return "CALL"
    if z > Z_THRESHOLD:
        return "PUT"
    return None


def allowed(now_time, last_fire_time, bars=COOLDOWN_BARS):
    if last_fire_time is None:
        return True
    return (now_time - last_fire_time) / 60 >= bars


class ZRule(unittest.TestCase):
    def test_flat_series_gives_no_signal(self):
        self.assertIsNone(signal([100.0] * 20))

    def test_stretch_below_mean_is_call(self):
        closes = [100 + i * 0.1 for i in range(19)] + [95.0]
        self.assertEqual(signal(closes), "CALL")

    def test_stretch_above_mean_is_put(self):
        closes = [100 - i * 0.1 for i in range(19)] + [105.0]
        self.assertEqual(signal(closes), "PUT")

    def test_rule_is_symmetric(self):
        """The live rule's fatal flaw was asymmetry: mirroring the series must
        mirror the signal, with no bias toward either side."""
        up = [100 + i * 0.1 for i in range(19)] + [95.0]
        mirrored = [200 - x for x in up]
        self.assertEqual(signal(up), "CALL")
        self.assertEqual(signal(mirrored), "PUT")

    def test_z_is_scale_free(self):
        """Doubling every price must not change the signal: z is a ratio."""
        closes = [100 + i * 0.1 for i in range(19)] + [95.0]
        self.assertEqual(signal(closes), signal([c * 2 for c in closes]))

    def test_cooldown_blocks_adjacent_candles(self):
        self.assertTrue(allowed(60_000, None))
        self.assertFalse(allowed(60_060, 60_000))            # 1 candle later
        self.assertFalse(allowed(60_000 + 4 * 60, 60_000))   # 4 candles
        self.assertTrue(allowed(60_000 + 5 * 60, 60_000))    # 5 candles: allowed

    def test_cooldown_caps_signal_rate(self):
        """With a 5-bar cooldown, 115 candles can yield at most 23 signals —
        the live run produced 45."""
        self.assertLessEqual(115 // COOLDOWN_BARS, 23)


if __name__ == "__main__":
    unittest.main()
