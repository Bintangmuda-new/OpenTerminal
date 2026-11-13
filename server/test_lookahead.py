"""test_lookahead.py — guard for the LOOK-AHEAD 100% demo rule.

The rule reads the entry candle (B). It is 100% by construction and it is
DISHONEST by construction. This test pins BOTH facts so nobody ever mistakes
it for a real edge:

  1. when B is available the rule is right on every single trade
  2. when B is NOT available (the live case) the rule must return None,
     i.e. the app fails closed instead of crashing or guessing
"""
import unittest


def lookahead_signal(candles, index):
    """Mirror of MainWindow.xaml.cs -> HistoricalDirection(ActiveStrategy.LookAhead)."""
    if index + 1 >= len(candles):
        return None
    entry = candles[index + 1]
    if entry[1] > entry[0]:
        return "CALL"
    if entry[1] < entry[0]:
        return "PUT"
    return None


class LookAheadGuard(unittest.TestCase):
    def test_perfect_when_entry_known(self):
        candles = [
            (10.0, 10.0 + (-1) ** i * 0.5)
            for i in range(60)
        ]
        wins = total = 0
        for i in range(25, len(candles) - 1):
            signal = lookahead_signal(candles, i)
            if signal is None:
                continue
            entry_open, entry_close = candles[i + 1]
            outcome = "CALL" if entry_close > entry_open else "PUT"
            total += 1
            wins += signal == outcome
        self.assertGreater(total, 30)
        self.assertEqual(wins, total)

    def test_fails_closed_on_live_tail(self):
        candles = [(10.0, 10.5)] * 40
        # last index has no next candle -> must be None, never a guess
        self.assertIsNone(lookahead_signal(candles, len(candles) - 1))

    def test_doji_returns_none(self):
        candles = [(10.0, 10.0)] * 5
        self.assertIsNone(lookahead_signal(candles, 0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
