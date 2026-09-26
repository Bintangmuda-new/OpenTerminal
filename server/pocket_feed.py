from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
from pathlib import Path

from pocket_client import AsyncPocketOption


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def group_for(asset) -> str:
    symbol = asset.symbol.lower()
    kind = (asset.type or "").lower()
    if symbol.endswith("_otc"):
        return "OTC"
    if "crypto" in kind or any(part in symbol for part in ("btc", "eth", "xrp")):
        return "CRYPTO"
    return "FOREX"


def candle_json(candle) -> dict:
    open_price = float(candle.open)
    close_price = float(candle.close)
    high_price = float(candle.high)
    low_price = float(candle.low)
    if not all(math.isfinite(value) and value > 0 for value in (open_price, high_price, low_price, close_price)):
        raise ValueError("invalid candle price")
    return {"time": candle.time, "open": open_price, "high": max(high_price, open_price, close_price), "low": min(low_price, open_price, close_price), "close": close_price, "volume": candle.volume}


class Feed:
    def __init__(self, ssid: str) -> None:
        self.client = AsyncPocketOption(ssid, demo=True, allow_real=False)
        self.assets = {}
        self.ticks = {}
        self.tick_times = {}
        self.candles = {}
        self.candle_times = {}
        self.resub_times = {}
        self.lock = asyncio.Lock()

    async def connect(self) -> None:
        await self.client.connect(ready_timeout=12)
        if not self.client.is_demo:
            raise RuntimeError("Pocket session is not DEMO")
        self.assets = {asset.symbol: asset for asset in self.client.assets() if asset.symbol}
        self.client.on_tick(self.on_tick)
        await asyncio.gather(*(self.client.subscribe(symbol) for symbol in self.assets), return_exceptions=True)

    async def on_tick(self, symbol, price, timestamp) -> None:
        if not symbol or not isinstance(price, (int, float)) or price <= 0:
            return
        tick_time = float(timestamp or time.time())
        if tick_time > 10_000_000_000:
            tick_time /= 1000
        self.ticks[symbol] = {"price": float(price), "time": tick_time}
        self.tick_times[symbol] = time.time()
        rows = self.candles.get(symbol)
        if rows:
            candle = rows[-1]
            candle_time = int(tick_time // 60) * 60
            if candle_time > candle["time"]:
                rows.append({"time": candle_time, "open": float(price), "high": float(price), "low": float(price), "close": float(price), "volume": 0})
                del rows[:-500]
            elif candle_time == candle["time"]:
                candle["close"] = float(price)
                candle["high"] = max(candle["high"], float(price))
                candle["low"] = min(candle["low"], float(price))

    async def load_candles(self, symbol: str) -> None:
        now = time.time()
        if symbol in self.candles and now - self.candle_times.get(symbol, 0) < 10:
            return
        try:
            rows = await self.client.candles(symbol, 60, count=500, timeout=15)
            if rows:
                candles = []
                for row in rows:
                    try:
                        candles.append(candle_json(row))
                    except (TypeError, ValueError):
                        continue
                if candles:
                    self.candles[symbol] = candles
                    self.candle_times[symbol] = now
        except Exception:
            return

    async def resubscribe(self, symbol: str) -> None:
        # ponytail: fixed 30s retry gap; back off per symbol if the broker starts rate-limiting.
        now = time.time()
        if now - self.resub_times.get(symbol, 0) < 30:
            return
        self.resub_times[symbol] = now
        try:
            await self.client.subscribe(symbol)
        except Exception:
            return

    def status(self) -> str:
        if not self.client.connected or not self.assets:
            return "connecting"
        if not self.ticks:
            return "stale"
        return "live" if any(time.time() - value < 10 for value in self.tick_times.values()) else "stale"

    def trends(self) -> dict:
        result = {}
        for symbol, rows in self.candles.items():
            closes = [row["close"] for row in rows[-20:] if row["close"] > 0]
            if len(closes) >= 5:
                result[symbol] = {"direction": "UP" if closes[-1] >= sum(closes) / len(closes) else "DOWN"}
        return result

    async def snapshot(self, symbol: str) -> dict:
        requested_symbol = symbol.strip()
        async with self.lock:
            # Ticks for a symbol can stop without the socket dropping, which froze the chart on
            # stale candles forever. Re-subscribe and refetch while it is the one being watched.
            if requested_symbol in self.assets:
                stale = time.time() - self.tick_times.get(requested_symbol, 0) > 15
                if not self.candles.get(requested_symbol) or stale:
                    asyncio.create_task(self.load_candles(requested_symbol))
                if stale:
                    asyncio.create_task(self.resubscribe(requested_symbol))
        markets = [{"symbol": asset.symbol, "name": asset.name or asset.symbol, "group": group_for(asset)} for asset in self.assets.values()]
        live_symbols = [name for name, seen in self.tick_times.items() if time.time() - seen < 10]
        return {"mode": "DEMO", "status": self.status(), "receivedAt": int(time.time() * 1000), "markets": markets, "liveSymbols": live_symbols, "ticks": self.ticks, "candles": {requested_symbol: self.candles.get(requested_symbol, [])}, "trends": self.trends()}

    async def close(self) -> None:
        await self.client.close()


async def main() -> None:
    ssid_file = Path(os.environ["POCKET_SSID_FILE"])
    ssid = ssid_file.read_text(encoding="utf-8").strip()
    if '"isDemo":1' not in ssid.replace(" ", ""):
        raise RuntimeError("SSID is not DEMO")
    feed = Feed(ssid)
    await feed.connect()
    emit({"type": "ready", "status": feed.status(), "markets": len(feed.assets)})
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            request = json.loads(line)
            if request.get("type") == "snapshot":
                emit({"id": request.get("id"), "ok": True, "snapshot": await feed.snapshot(request.get("symbol", ""))})
    finally:
        await feed.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        emit({"type": "error", "error": str(error)[:240]})
        raise
