import unittest


def outcome(direction, entry_open, entry_high, entry_low, entry_close, stop, tp1):
    target = entry_high >= tp1 if direction == "CALL" else entry_low <= tp1
    stopped = entry_low <= stop if direction == "CALL" else entry_high >= stop
    if target and stopped:
        return "DRAW"
    if target:
        return "WIN"
    if stopped:
        return "LOSS"
    if entry_close == entry_open:
        return "DRAW"
    actual = "CALL" if entry_close > entry_open else "PUT"
    return "WIN" if actual == direction else "LOSS"


class ForexResultTests(unittest.TestCase):
    def test_tp_and_sl_are_resolved_after_entry_close(self):
        self.assertEqual("WIN", outcome("CALL", 100, 103, 99, 101, 98, 102))
        self.assertEqual("LOSS", outcome("CALL", 100, 101, 97, 99, 98, 102))
        self.assertEqual("DRAW", outcome("CALL", 100, 103, 97, 101, 98, 102))


if __name__ == "__main__":
    unittest.main()
