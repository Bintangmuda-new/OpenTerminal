import asyncio
import unittest
from types import SimpleNamespace

from pocket_feed import Feed, candle_json


class FeedTest(unittest.TestCase):
    def test_tick_updates_active_candle(self):
        feed = Feed('{"session":"test","isDemo":1}')
        feed.candles["EURUSD_otc"] = [{"time": 60, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "volume": 0}]

        asyncio.run(feed.on_tick("EURUSD_otc", 1.3, 61))

        self.assertEqual(feed.candles["EURUSD_otc"][-1], {"time": 60, "open": 1.1, "high": 1.3, "low": 1.0, "close": 1.3, "volume": 0})

    def test_tick_starts_the_next_minute_candle(self):
        feed = Feed('{"session":"test","isDemo":1}')
        feed.candles["EURUSD_otc"] = [{"time": 60, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.1, "volume": 0}]

        asyncio.run(feed.on_tick("EURUSD_otc", 1.3, 120))

        self.assertEqual(feed.candles["EURUSD_otc"][-1], {"time": 120, "open": 1.3, "high": 1.3, "low": 1.3, "close": 1.3, "volume": 0})

    def test_candle_normalizes_ohlc_bounds(self):
        candle = candle_json(SimpleNamespace(time=60, open=1.1, high=1.2, low=1.15, close=1.12, volume=0))

        self.assertEqual(candle, {"time": 60, "open": 1.1, "high": 1.2, "low": 1.1, "close": 1.12, "volume": 0})


if __name__ == "__main__":
    unittest.main()
