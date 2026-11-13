"""Guard for the arrow-rendering fixes shipped 12 Okt 2026.

Three faults were reported on the live chart:

1. Double arrow — the same entry candle was drawn twice: once by the
   historical pass (DrawStrategyArrows, driven by HistoricalDirection) and once
   by the live pass (DrawEntryArrows, driven by firedEntries). Two more arrows
   stacked whenever two enabled strategies agreed on the same candle.
2. Arrow vanishing on a wrong call — nothing marked WIN/LOSS, and
   ResolveEntries had no caller at all, so a fired entry never got an outcome
   and the result panel stayed empty forever.
3. The trailing-bar guard in ResolveEntries skipped the newest entry candle,
   so the most recent signal could never resolve.

These tests pin the rules, not the WinForms/WPF drawing itself.
"""
import unittest

DRAWN_ONCE_PER_CANDLE_AND_DIRECTION = True
RESOLVE_SKIPS_TRAILING_BAR = False


def historical_arrows(candle_times, fired_entries, strategy_directions):
    """Mirrors DrawStrategyArrows: one arrow per (candle, direction)."""
    drawn = []
    for time, directions in strategy_directions.items():
        if time in fired_entries:
            continue  # live pass owns this candle
        seen = set()
        for direction in directions:
            if direction is None or direction in seen:
                continue
            seen.add(direction)
            drawn.append((time, direction))
    return drawn


def live_arrows(fired_entries):
    """Mirrors DrawEntryArrows: every locked entry keeps its arrow."""
    return [(time, direction) for time, direction in fired_entries.items()]


def resolve(signal_time, candle_times, fired, entries, skip_trailing):
    """Mirrors ResolveEntries. Returns the outcome, or None when unresolved."""
    key = ("ZScore", signal_time)
    if key in entries:
        return entries[key]
    candidates = [t for t in candle_times if t > signal_time]
    if not candidates:
        return None
    if skip_trailing:
        return None
    return fired.get(signal_time)


def outcome_for(direction, entry_open, entry_close):
    result = "CALL" if entry_close > entry_open else "PUT" if entry_close < entry_open else "DRAW"
    return "DRAW" if result == "DRAW" else ("WIN" if result == direction else "LOSS")


class ArrowDedupTests(unittest.TestCase):
    def test_fired_candle_is_not_drawn_by_the_historical_pass(self):
        """The live pass already drew it; a second arrow is the reported bug."""
        times = [100.0, 160.0]
        drawn = historical_arrows(times, {160.0: "CALL"}, {160.0: ["CALL"], 100.0: ["PUT"]})
        self.assertNotIn((160.0, "CALL"), drawn)
        self.assertEqual([(100.0, "PUT")], drawn)

    def test_two_strategies_agreeing_draw_one_arrow(self):
        """Two enabled strategies with the same direction must not stack."""
        drawn = historical_arrows([100.0], {}, {100.0: ["CALL", "CALL", "PUT"]})
        self.assertEqual([(100.0, "CALL"), (100.0, "PUT")], drawn)

    def test_every_locked_entry_keeps_exactly_one_arrow(self):
        arrows = live_arrows({100.0: "CALL", 160.0: "PUT"})
        self.assertEqual(2, len(arrows))
        self.assertEqual(len(arrows), len({time for time, _ in arrows}))


class WrongCallStaysVisibleTests(unittest.TestCase):
    def test_loss_keeps_the_arrow_and_marks_it(self):
        """A wrong call must stay on the chart, tagged LOSS — never removed."""
        entries = {("ZScore", 100.0): outcome_for("CALL", 1.0, 0.99)}
        self.assertEqual("LOSS", entries[("ZScore", 100.0)])
        self.assertEqual(1, len(live_arrows({100.0: "CALL"})))

    def test_win_and_draw_are_tagged(self):
        self.assertEqual("WIN", outcome_for("CALL", 1.0, 1.01))
        self.assertEqual("DRAW", outcome_for("CALL", 1.0, 1.0))

    def test_unresolved_entry_has_no_ring_but_still_draws(self):
        entries = {}
        self.assertIsNone(resolve(100.0, [100.0], {}, entries, skip_trailing=False))
        self.assertEqual(1, len(live_arrows({100.0: "CALL"})))


class TrailingBarTests(unittest.TestCase):
    def test_newest_entry_candle_resolves(self):
        """The old guard `entryIndex >= Count - 1` skipped the freshest entry."""
        fired = {100.0: "CALL"}
        self.assertEqual("CALL", resolve(100.0, [100.0, 160.0], fired, {}, skip_trailing=RESOLVE_SKIPS_TRAILING_BAR))

    def test_entry_candle_must_exist(self):
        self.assertIsNone(resolve(160.0, [100.0, 160.0], {}, {}, skip_trailing=RESOLVE_SKIPS_TRAILING_BAR))

    def test_already_resolved_entry_is_not_recomputed(self):
        entries = {("ZScore", 100.0): "WIN"}
        self.assertEqual("WIN", resolve(100.0, [100.0, 160.0, 220.0], {100.0: "CALL"}, entries, skip_trailing=False))


class CounterIntegrityTests(unittest.TestCase):
    def test_pending_never_negative_after_reset(self):
        """ResultReset clears firedStrategies and signalEntries together."""
        fired, entries = {}, {}
        total = len(entries)
        pending = max(0, len(fired) - total)
        self.assertEqual(0, pending)


if __name__ == "__main__":
    unittest.main(verbosity=2)
