"""Timing guard for the 1M entry signal.

The chart clock must follow broker time, not this machine's, and the alert must
fire with at least 5 seconds left so the order lands on the NEXT candle.
Mirrors BrokerNow/SecondsLeftInCandle and the arm/fire gates in MainWindow.xaml.cs.
"""
import unittest

ARM_AT = 15   # tentative -> "bersiap"
FIRE_AT = 8   # locked alert; leaves 5s margin even if one 1s poll is missed
REQUIRED_MARGIN = 5


def seconds_left(broker_now: float, duration: int = 60) -> int:
    return int(max(0, duration - broker_now % duration))


def broker_now(local_epoch: float, tick_time: float) -> float:
    """Offset is derived from the feed tick, so a broker clock hours ahead still
    yields the correct position inside the candle."""
    return local_epoch + (tick_time - local_epoch)


class SignalTiming(unittest.TestCase):
    def test_broker_offset_beats_local_clock(self):
        local = 1_790_374_900.0
        # A whole-hour skew (7200s) is invisible here because 7200 % 60 == 0;
        # only a sub-minute skew moves the candle boundary, and that is the
        # case the broker clock must win.
        tick = local + 7200 + 23
        self.assertEqual(seconds_left(broker_now(local, tick)), seconds_left(tick))
        self.assertNotEqual(seconds_left(local), seconds_left(tick))

    def test_fires_with_enough_margin_to_enter_next_candle(self):
        self.assertGreaterEqual(FIRE_AT, REQUIRED_MARGIN)
        # A skipped 1s poll must still land above the margin.
        self.assertGreaterEqual(FIRE_AT - 1, REQUIRED_MARGIN)

    def test_arm_precedes_fire(self):
        self.assertGreater(ARM_AT, FIRE_AT)

    def test_candle_boundary_wraps_to_full_minute(self):
        start = 1_790_374_860.0  # exact minute boundary
        self.assertEqual(seconds_left(start), 60)
        self.assertEqual(seconds_left(start + 52), 8)
        self.assertEqual(seconds_left(start + 59), 1)

    def test_fire_window_is_reachable_by_one_second_polling(self):
        """Every candle must contain at least one poll inside (0, FIRE_AT]."""
        start = 1_790_374_860.0
        hits = [s for s in range(60) if 0 < seconds_left(start + s) <= FIRE_AT]
        self.assertTrue(hits, "no poll lands in the fire window")

    def test_locked_entry_is_never_revised(self):
        """firedEntries is write-once per candle: a later poll in the same candle
        must not overwrite a direction already shown to the trader."""
        fired = {}

        def lock(candle_time, direction):
            if candle_time in fired:
                return False
            fired[candle_time] = direction
            return True

        self.assertTrue(lock(1_790_374_860, "CALL"))
        # Price flips inside the final seconds; the arrow must not follow.
        self.assertFalse(lock(1_790_374_860, "PUT"))
        self.assertEqual(fired[1_790_374_860], "CALL")
        # A new candle is free to take its own direction.
        self.assertTrue(lock(1_790_374_920, "PUT"))

    def test_half_second_polling_never_skips_the_window(self):
        start = 1_790_374_860.0
        hits = [s / 2 for s in range(120) if 0 < seconds_left(start + s / 2) <= FIRE_AT]
        self.assertGreaterEqual(len(hits), FIRE_AT, "500ms timer must sample the window repeatedly")


if __name__ == "__main__":
    unittest.main()
