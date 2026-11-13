"""Guard for the RSI-Extreme 20/80 fade rule added under INDIKATOR > STRATEGI.

Added because backtest8/9 (60+ candidate rules, train 60% / holdout 40%, then a
per-day walk-forward) found this to be the only rule whose hit rate stayed above
50 on out-of-sample day folds at a usable sample size. Every other candidate
flattened onto a coin flip.

Backtest reference (122 symbols, 202,007 holdout trades): 50.75%, Wilson low
49.3%, 4/8 day folds above 50. Below the 70% target — the tests below pin
CORRECTNESS (thresholds, symmetry, direction), not edge.

Thresholds must stay mirrored with MainWindow.xaml.cs -> HistoricalDirection.
"""
import unittest

CALL_THRESHOLD = 20
PUT_THRESHOLD = 80


def rsi(closes, period=14):
    """Wilder RSI, same smoothing as the app's Rsi() helper."""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def signal(closes):
    value = rsi(closes)
    if value < CALL_THRESHOLD:
        return "CALL"
    if value > PUT_THRESHOLD:
        return "PUT"
    return None


class RsiExtreme(unittest.TestCase):
    def test_flat_series_gives_no_signal(self):
        self.assertIsNone(signal([100.0] * 30))

    def test_oversold_gives_call(self):
        closes = [100 - i for i in range(30)]
        self.assertEqual(signal(closes), "CALL")

    def test_overbought_gives_put(self):
        closes = [100 + i for i in range(30)]
        self.assertEqual(signal(closes), "PUT")

    def test_rule_is_symmetric(self):
        """Mirroring the series must mirror the signal — no directional bias."""
        down = [100 - i * 1.5 for i in range(30)]
        mirrored = [200 - x for x in down]
        self.assertEqual(signal(down), "CALL")
        self.assertEqual(signal(mirrored), "PUT")

    def test_normal_oscillation_stays_quiet(self):
        """A mild sine wave must not trigger the extreme rule at all."""
        import math
        closes = [100 + math.sin(i / 3) * 1.2 for i in range(120)]
        fired = [signal(closes[: i + 1]) for i in range(20, len(closes))]
        self.assertEqual(set(fired), {None})

    def test_thresholds_are_mirrored(self):
        """The fade must be symmetric about 50: 20/80, not 20/75."""
        self.assertEqual(CALL_THRESHOLD - 50, -(PUT_THRESHOLD - 50))

    def test_extreme_rule_is_stricter_than_the_old_rsi_reversal(self):
        """The old RSI-14 reversal used 30/70; this rule must fire less often.
        A series whose RSI lands in the 20-30 band fires the old rule only."""
        import math
        old = lambda closes: "CALL" if rsi(closes) < 30 else "PUT" if rsi(closes) > 70 else None
        soft = [100 - i * 0.15 + math.sin(i / 2) * 1.5 for i in range(60)]
        self.assertIsNotNone(old(soft))
        self.assertIsNone(signal(soft))


if __name__ == "__main__":
    unittest.main()
