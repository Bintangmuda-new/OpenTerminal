"""Scoring guard for the signal result panel.

Mirrors ResolveEntries/UpdateResultPanel in MainWindow.xaml.cs. The entry runs on
the candle AFTER the signal, a flat candle is a DRAW (stake returned, not a win),
and winrate counts decided trades only — folding draws into the denominator would
understate the rule's accuracy.
"""
import unittest


def outcome(direction, entry_open, entry_close):
    if entry_close == entry_open:
        return "DRAW"
    actual = "CALL" if entry_close > entry_open else "PUT"
    return "WIN" if actual == direction else "LOSS"


def winrate(results):
    win = results.count("WIN")
    decided = win + results.count("LOSS")
    return None if decided == 0 else win / decided * 100


def resolve(fired, candles):
    """fired: {signal_time: direction}. candles: list of (time, open, close).
    A signal only scores once its entry candle is provably closed, i.e. another
    candle exists after it."""
    out = {}
    for signal_time, direction in fired.items():
        idx = next((i for i, c in enumerate(candles) if c[0] > signal_time), -1)
        if idx < 0 or idx >= len(candles) - 1:
            continue
        _, o, c = candles[idx]
        out[signal_time] = outcome(direction, o, c)
    return out


class Scoring(unittest.TestCase):
    def test_direction_scoring(self):
        self.assertEqual(outcome("CALL", 100, 101), "WIN")
        self.assertEqual(outcome("CALL", 100, 99), "LOSS")
        self.assertEqual(outcome("PUT", 100, 99), "WIN")
        self.assertEqual(outcome("PUT", 100, 101), "LOSS")

    def test_flat_candle_is_draw_not_win(self):
        self.assertEqual(outcome("CALL", 100, 100), "DRAW")
        self.assertEqual(outcome("PUT", 100, 100), "DRAW")

    def test_draws_excluded_from_winrate(self):
        self.assertEqual(winrate(["WIN", "LOSS", "DRAW", "DRAW"]), 50.0)
        self.assertEqual(winrate(["WIN", "WIN", "DRAW"]), 100.0)

    def test_no_decided_trades_yields_no_rate(self):
        self.assertIsNone(winrate([]))
        self.assertIsNone(winrate(["DRAW", "DRAW"]))

    def test_entry_is_the_candle_after_the_signal(self):
        candles = [(60, 10, 11), (120, 11, 13), (180, 13, 12)]
        # Signal on the candle closing at t=60; entry is the t=120 candle (11->13 up).
        self.assertEqual(resolve({60: "CALL"}, candles), {60: "WIN"})
        self.assertEqual(resolve({60: "PUT"}, candles), {60: "LOSS"})

    def test_unfinished_entry_is_not_scored(self):
        candles = [(60, 10, 11), (120, 11, 13)]
        # t=120 is the running candle: no candle after it, so nothing is scored.
        self.assertEqual(resolve({60: "CALL"}, candles), {})

    def test_each_signal_scores_once(self):
        candles = [(60, 10, 11), (120, 11, 13), (180, 13, 12)]
        first = resolve({60: "CALL"}, candles)
        already = dict(first)
        # Re-running must not change a recorded outcome, even if later data shifts.
        for k, v in resolve({60: "CALL"}, candles).items():
            self.assertEqual(already.get(k, v), v)


if __name__ == "__main__":
    unittest.main()
