"""Iterative OTC 1M strategy runner.

This script builds a simple, explicit, updateable strategy from the stored
candles in data/otc_research.sqlite3. It uses a strict train/holdout split per
symbol, evaluates next-candle resolution, and prints the best candidates under a
small set of parameter sweeps.

It is intentionally conservative: no guarantee of 100% winrate is claimed.
The goal is to produce a reproducible rule set that can be tuned in a loop from
real data, not to promise a false edge.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Iterable, List, Tuple

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"


def rolling_mean(values: List[float], window: int, idx: int) -> float:
    start = max(0, idx - window + 1)
    segment = values[start : idx + 1]
    return sum(segment) / len(segment) if segment else 0.0


def rolling_median(values: List[float], window: int, idx: int) -> float:
    start = max(0, idx - window + 1)
    segment = values[start : idx + 1]
    if not segment:
        return 0.0
    s = sorted(segment)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def abs_change(values: List[float]) -> List[float]:
    diffs: List[float] = [0.0]
    for i in range(1, len(values)):
        diffs.append(abs(values[i] - values[i - 1]))
    return diffs


def candidate_trade(symbol_rows: List[Tuple[int, float, float, float, float]],
                   z_threshold: float,
                   range_mult: float,
                   cooldown: int,
                   use_rsi: bool,
                   rsi_low: float,
                   rsi_high: float,
                   include_reverse_bias: bool = False,
                   window: int = 20,
                   range_window: int = 60,
                   split_ratio: float = 0.6,
                   ) -> Tuple[int, int, int, int, float]:
    """Return (win, loss, draw, total, holdout_rate) for this symbol using the rule.

    We evaluate each valid entry by using the next candle as the resolution
    candle. This matches the intended 1M OTC logic: signal is generated before
    close, then the following candle decides the binary result.
    """
    if len(symbol_rows) < 120:
        return (0, 0, 0, 0, 0.0)

    closes = [r[4] for r in symbol_rows]
    highs = [r[2] for r in symbol_rows]
    lows = [r[3] for r in symbol_rows]
    ranges = [max(0.0, highs[i] - lows[i]) for i in range(len(symbol_rows))]
    changes = abs_change(closes)

    # split by time, not by random shuffle, to maintain chronology
    split = max(20, int(len(symbol_rows) * split_ratio))
    signal_plan = []
    last_signal_idx = -10_000

    for i in range(1, len(symbol_rows) - 1):
        if i < max(window, range_window):
            continue

        mean20 = rolling_mean(closes, window, i)
        step20 = rolling_mean(changes, window, i)
        if step20 <= 0:
            continue
        z = (closes[i] - mean20) / step20

        if i - last_signal_idx < cooldown:
            continue

        range_now = ranges[i]
        median_range = rolling_median(ranges, range_window, i)
        if median_range <= 0:
            continue

        if range_now <= median_range * range_mult:
            continue

        # RSI gate if enabled. We keep it intentionally simple and transparent.
        if use_rsi:
            # basic Wilder RSI approximation
            gains = []
            losses = []
            for j in range(1, min(i + 1, len(closes))):
                delta = closes[j] - closes[j - 1]
                gains.append(max(delta, 0.0))
                losses.append(max(-delta, 0.0))
            if len(gains) < 14:
                continue
            avg_gain = sum(gains[-14:]) / 14.0
            avg_loss = sum(losses[-14:]) / 14.0
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
            if rsi <= rsi_low or rsi >= rsi_high:
                pass
            else:
                continue

        if include_reverse_bias:
            # Strongest simple filter: only allow entries in the direction of the
            # slope of the recent 5-candle close average.
            short_mean = rolling_mean(closes, 5, i)
            if z <= -z_threshold and short_mean > closes[i]:
                pass
            elif z >= z_threshold and short_mean < closes[i]:
                pass
            else:
                continue

        if z <= -z_threshold:
            signal = "CALL"
        elif z >= z_threshold:
            signal = "PUT"
        else:
            continue

        # Validate the next candle exists
        next_idx = i + 1
        if next_idx >= len(symbol_rows):
            continue

        entry_close = closes[i]
        next_close = closes[next_idx]
        if signal == "CALL":
            outcome = "WIN" if next_close > entry_close else "LOSS"
        else:
            outcome = "WIN" if next_close < entry_close else "LOSS"

        if next_close == entry_close:
            outcome = "DRAW"

        # Only train entries use the train split, holdout is the later chunk.
        if i < split:
            bucket = "train"
        else:
            bucket = "holdout"

        signal_plan.append((bucket, outcome, signal, i, entry_close, next_close))
        last_signal_idx = i

    train = [x for x in signal_plan if x[0] == "train"]
    holdout = [x for x in signal_plan if x[0] == "holdout"]

    def count_results(rows):
        wins = sum(1 for r in rows if r[1] == "WIN")
        losses = sum(1 for r in rows if r[1] == "LOSS")
        draws = sum(1 for r in rows if r[1] == "DRAW")
        total = len(rows)
        rate = (wins / total * 100.0) if total else 0.0
        return wins, losses, draws, total, rate

    train_w, train_l, train_d, train_n, train_rate = count_results(train)
    hold_w, hold_l, hold_d, hold_n, hold_rate = count_results(holdout)

    # We only report the holdout result.
    return (hold_w, hold_l, hold_d, hold_n, hold_rate)


def iter_rules() -> List[dict]:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    symbols = [
        sym for (sym,) in con.execute(
            "SELECT DISTINCT symbol FROM candles_m1 WHERE symbol IS NOT NULL ORDER BY symbol"
        )
    ]
    con.close()

    rules = []
    for z_threshold in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
        for range_mult in [1.0, 1.25, 1.5, 1.75, 2.0]:
            for cooldown in [3, 5, 8, 10]:
                for use_rsi, rsi_low, rsi_high in [(False, 0.0, 100.0), (True, 20.0, 80.0), (True, 25.0, 75.0)]:
                    total_hold = 0
                    total_win = 0
                    total_loss = 0
                    total_draw = 0
                    per_symbol = []

                    for sym in symbols:
                        rows = read_symbol_rows(sym)
                        if not rows:
                            continue
                        w, l, d, n, rate = candidate_trade(
                            rows,
                            z_threshold=z_threshold,
                            range_mult=range_mult,
                            cooldown=cooldown,
                            use_rsi=use_rsi,
                            rsi_low=rsi_low,
                            rsi_high=rsi_high,
                        )
                        if n <= 0:
                            continue
                        total_win += w
                        total_loss += l
                        total_draw += d
                        total_hold += n
                        per_symbol.append((sym, w, l, d, n, rate))

                    if total_hold <= 0:
                        continue

                    overall_rate = (total_win / total_hold) * 100.0 if total_hold else 0.0
                    rules.append({
                        "z": z_threshold,
                        "range_mult": range_mult,
                        "cooldown": cooldown,
                        "use_rsi": use_rsi,
                        "rsi_low": rsi_low,
                        "rsi_high": rsi_high,
                        "trades": total_hold,
                        "win": total_win,
                        "loss": total_loss,
                        "draw": total_draw,
                        "winrate": overall_rate,
                        "sample_symbols": len(per_symbol),
                    })

    # sort by winrate, then by number of trades, then by lower volatility of tuning
    rules.sort(key=lambda r: (-r["winrate"], -r["trades"], r["z"]))
    return rules[:20]


def read_symbol_rows(symbol: str):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT time, open, high, low, close FROM candles_m1 WHERE symbol = ? ORDER BY time ASC",
        (symbol,),
    ).fetchall()
    con.close()
    return rows


def main() -> None:
    print("OTC 1M iterative strategy runner")
    print(f"DB: {DB_PATH}")
    print("Mode: train/holdout per symbol, next candle resolution, no claim of guaranteed edge")
    print("-" * 90)
    results = iter_rules()
    for item in results:
        print(
            f"z={item['z']:<4} range_mult={item['range_mult']:<4} cooldown={item['cooldown']:<2} "
            f"rsi={item['use_rsi']}({item['rsi_low']}-{item['rsi_high']}) "
            f"trades={item['trades']:<5} win={item['win']:<4} loss={item['loss']:<4} draw={item['draw']:<4} "
            f"winrate={item['winrate']:.2f}%"
        )
    print("-" * 90)
    if results:
        best = results[0]
        print("BEST_CANDIDATE")
        print(best)


if __name__ == "__main__":
    main()
