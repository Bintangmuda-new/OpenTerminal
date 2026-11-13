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
        self.demo_deals = {}
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

    async def snapshot(self, symbol: str, symbols=None) -> dict:
        requested_symbol = symbol.strip()
        # NEXT widget watchlist (2026-11-06): optional extra symbols whose M1
        # history should also be loaded. Bounded to 10, DEMO assets only.
        # Without this parameter the response is exactly the old single-symbol
        # shape, so existing clients are unaffected.
        extra_symbols = []
        if isinstance(symbols, list):
            for item in symbols:
                name = str(item).strip()
                if name and name != requested_symbol and name in self.assets and name not in extra_symbols:
                    extra_symbols.append(name)
                if len(extra_symbols) >= 10:
                    break
        requested_symbols = [requested_symbol] + extra_symbols if requested_symbol else extra_symbols
        async with self.lock:
            # Ticks for a symbol can stop without the socket dropping, which froze the chart on
            # stale candles forever. Re-subscribe and refetch while it is the one being watched.
            for name in requested_symbols:
                if name in self.assets:
                    stale = time.time() - self.tick_times.get(name, 0) > 15
                    if not self.candles.get(name) or stale:
                        asyncio.create_task(self.load_candles(name))
                    if stale:
                        asyncio.create_task(self.resubscribe(name))
        # Payout/active come from the client's live updateAssets map; self.assets is only the connect-time list.
        live_assets = {asset.symbol: asset for asset in self.client.assets()}
        markets = []
        for asset in self.assets.values():
            current = live_assets.get(asset.symbol, asset)
            markets.append({"symbol": asset.symbol, "name": asset.name or asset.symbol, "group": group_for(asset), "payout": int(current.payout or 0), "active": bool(current.is_active)})
        live_symbols = [name for name, seen in self.tick_times.items() if time.time() - seen < 10]
        balance = self.client.balance
        return {"mode": "DEMO", "status": self.status(), "receivedAt": int(time.time() * 1000), "balance": float(balance.balance) if balance and math.isfinite(balance.balance) else None, "currency": "USD", "markets": markets, "liveSymbols": live_symbols, "ticks": self.ticks, "candles": {name: self.candles.get(name, []) for name in requested_symbols}, "trends": self.trends()}

    async def close(self) -> None:
        await self.client.close()

    async def place_demo_order(self, asset: str, direction: str, amount: float, expiration: int) -> dict:
        if not self.client.is_demo:
            raise RuntimeError("DEMO order rejected: session is not DEMO")
        deal = await self.client.buy(asset, direction, amount, expiration)
        self.demo_deals[deal.request_id] = deal
        return {"request_id": deal.request_id, "id": deal.id, "asset": deal.asset,
                "direction": deal.direction.value, "amount": deal.amount,
                "open_price": deal.open_price, "expiration": expiration}

    async def wait_demo_result(self, request_id: int) -> dict:
        deal = self.demo_deals.get(request_id)
        if deal is None:
            raise RuntimeError("Unknown DEMO order request_id")
        result = await self.client.result(deal)
        self.demo_deals.pop(request_id, None)
        return {"request_id": request_id, "asset": result.asset,
                "won": result.won, "profit": result.profit,
                "open_price": result.open_price, "close_price": result.close_price}


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
                emit({"id": request.get("id"), "ok": True, "snapshot": await feed.snapshot(request.get("symbol", ""), request.get("symbols"))})
            elif request.get("type") == "buy":
                try:
                    result = await feed.place_demo_order(str(request["asset"]), str(request["direction"]), float(request["amount"]), int(request["expiration"]))
                    emit({"id": request.get("id"), "ok": True, "result": result})
                except Exception as error:
                    emit({"id": request.get("id"), "ok": False, "error": str(error)[:240]})
            elif request.get("type") == "result":
                try:
                    result = await feed.wait_demo_result(int(request["request_id"]))
                    emit({"id": request.get("id"), "ok": True, "result": result})
                except Exception as error:
                    emit({"id": request.get("id"), "ok": False, "error": str(error)[:240]})
    finally:
        await feed.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        emit({"type": "error", "error": str(error)[:240]})
        raise
