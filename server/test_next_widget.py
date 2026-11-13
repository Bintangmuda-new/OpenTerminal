"""Guards for the NEXT widget watchlist contract (2026-11-06).

Mirrors the bounded watchlist logic added to pocket_feed.snapshot():
extra symbols are asset-validated, de-duplicated, capped at 10, and the
candles map carries exactly the requested set. Without the parameter the
old single-symbol shape is preserved.
"""
import asyncio
import unittest

from pocket_feed import Feed


class NextWidgetContractTest(unittest.TestCase):
    def make_feed(self):
        feed = Feed('{"session":"test","isDemo":1}')
        ns = __import__("types").SimpleNamespace
        feed.assets = {name: ns(symbol=name, name=name, type="", payout=80, is_active=True)
                       for name in ("EURUSD_otc", "XAUUSD_otc", "BTCUSD_otc")}
        feed.client = __import__("types").SimpleNamespace(
            connected=True, assets=lambda: [], balance=None)
        feed.candles = {
            "EURUSD_otc": [{"time": 60, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "volume": 0}],
            "XAUUSD_otc": [{"time": 60, "open": 2.1, "high": 2.2, "low": 2.0, "close": 2.1, "volume": 0}],
        }
        feed.tick_times = {"EURUSD_otc": 9999999999, "XAUUSD_otc": 9999999999}
        return feed

    def test_snapshot_without_symbols_keeps_single_key_shape(self):
        feed = self.make_feed()
        snap = asyncio.run(feed.snapshot("EURUSD_otc"))
        self.assertEqual(list(snap["candles"].keys()), ["EURUSD_otc"])

    def test_snapshot_returns_watchlist_candles(self):
        feed = self.make_feed()
        snap = asyncio.run(feed.snapshot("EURUSD_otc", ["XAUUSD_otc", "EURUSD_otc"]))
        self.assertEqual(sorted(snap["candles"].keys()), ["EURUSD_otc", "XAUUSD_otc"])
        self.assertEqual(len(snap["candles"]["XAUUSD_otc"]), 1)

    def test_snapshot_drops_unknown_and_caps_at_ten(self):
        feed = self.make_feed()
        ns = __import__("types").SimpleNamespace
        feed.assets.update({f"SYM{i}_otc": ns(symbol=f"SYM{i}_otc", name=f"SYM{i}_otc", type="", payout=80, is_active=True)
                            for i in range(20)})
        extra = [f"SYM{i}_otc" for i in range(20)] + ["NOPE_otc"]
        snap = asyncio.run(feed.snapshot("EURUSD_otc", extra))
        self.assertLessEqual(len(snap["candles"]), 11)  # requested + max 10 extra
        self.assertNotIn("NOPE_otc", snap["candles"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
