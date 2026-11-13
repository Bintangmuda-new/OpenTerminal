"""Guard for the session-entry cap (user rule, 2026-11-06).

Mirrors MainWindow.xaml.cs: SessionMaxEntries = 10 locked entries per
symbol+strategy session, counted only when ApplySignal actually locks
(a preview / cooldown / missed signal does not consume the budget).
Cleared when the strategy changes or the result panel is reset.

This is a risk guard, not edge: it caps exposure per session. It does
not change the backtested winrate of any rule.
"""
import unittest

SESSION_MAX_ENTRIES = 10


class SessionGuard:
    def __init__(self):
        self.counts = {}

    def count(self, symbol, strategy):
        return self.counts.get((symbol, strategy), 0)

    def can_lock(self, symbol, strategy):
        return self.count(symbol, strategy) < SESSION_MAX_ENTRIES

    def register(self, symbol, strategy):
        self.counts[(symbol, strategy)] = self.count(symbol, strategy) + 1

    def clear(self):
        self.counts.clear()


class SessionGuardTest(unittest.TestCase):
    def test_allows_first_ten_locks(self):
        guard = SessionGuard()
        for _ in range(SESSION_MAX_ENTRIES):
            self.assertTrue(guard.can_lock("XAUUSD", "ZScore"))
            guard.register("XAUUSD", "ZScore")
        self.assertEqual(guard.count("XAUUSD", "ZScore"), SESSION_MAX_ENTRIES)

    def test_blocks_eleventh_lock(self):
        guard = SessionGuard()
        for _ in range(SESSION_MAX_ENTRIES):
            guard.register("XAUUSD", "ZScore")
        self.assertFalse(guard.can_lock("XAUUSD", "ZScore"))

    def test_cap_is_per_symbol_and_strategy(self):
        guard = SessionGuard()
        for _ in range(SESSION_MAX_ENTRIES):
            guard.register("XAUUSD", "ZScore")
        self.assertTrue(guard.can_lock("BTCUSD", "ZScore"))
        self.assertTrue(guard.can_lock("XAUUSD", "RsiExtreme"))

    def test_clear_resets_so_new_session_can_lock(self):
        guard = SessionGuard()
        for _ in range(SESSION_MAX_ENTRIES):
            guard.register("XAUUSD", "ZScore")
        guard.clear()
        self.assertTrue(guard.can_lock("XAUUSD", "ZScore"))
        self.assertEqual(guard.count("XAUUSD", "ZScore"), 0)

    def test_preview_without_lock_does_not_consume_budget(self):
        # Only register() (called on lock) may increment; a preview that
        # never reaches ApplySignal must leave the budget untouched.
        guard = SessionGuard()
        self.assertTrue(guard.can_lock("XAUUSD", "ZScore"))
        self.assertEqual(guard.count("XAUUSD", "ZScore"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
