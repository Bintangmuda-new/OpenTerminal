"""Learned OTC 1M rule discovery with strict walk-forward validation.

Scoring matches the terminal exactly:

    signal is produced from candle A (only candles up to and including A),
    entry happens at the open of candle B = A + 1 minute,
    result = close(B) vs open(B)  ->  WIN / LOSS / DRAW.

Split per symbol is chronological: 50% learn / 25% validate / 25% test.
Filters are added greedily and accepted only when the validate block improves
too. The headline number is always the TEST block.

Output: data/otc-learned-rule.json

Usage:
    python server/otc_learn.py [--limit N] [--top K]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "otc_research.sqlite3"
OUT_PATH = ROOT / "data" / "otc-learned-rule.json"

WARMUP = 60
LEARN_FRACTION = 0.50
VALIDATE_FRACTION = 0.25
MIN_LEARN_TRADES = 250
MIN_VALIDATE_TRADES = 100
MIN_TEST_TRADES = 60
MIN_FILTER_GAIN = 0.25
MAX_FILTERS = 5
MIN_FINAL_RATE = 51.0
MIN_BLOCK_RATE = 50.5

FEATURES = [
    "z", "rsi", "bb_pos", "atr_pct", "range_pct", "range_mean_pct",
    "vol_ratio", "body_ratio", "upper_wick", "lower_wick", "ema_gap",
    "trend5", "trend20", "run", "hour", "dow",
]
(Z, RSI, BB, ATRP, RNGP, RNGM, VOLR, BODY, UWICK, LWICK,
 EMAG, TR5, TR20, RUN, HOUR, DOW) = range(16)


def load_candles(limit):
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    symbols = [
        row[0] for row in con.execute(
            "SELECT symbol, COUNT(*) AS n FROM candles_m1 WHERE symbol IS NOT NULL "
            "GROUP BY symbol HAVING n >= 500 ORDER BY symbol"
        )
    ]
    if limit:
        symbols = symbols[:limit]
    data = {}
    for symbol in symbols:
        data[symbol] = con.execute(
            "SELECT time, open, high, low, close FROM candles_m1 "
            "WHERE symbol = ? ORDER BY time ASC",
            (symbol,),
        ).fetchall()
    con.close()
    return data


def ema_series(values, period):
    out = [0.0] * len(values)
    if not values:
        return out
    k = 2.0 / (period + 1.0)
    acc = values[0]
    out[0] = acc
    for i in range(1, len(values)):
        acc = values[i] * k + acc * (1 - k)
        out[i] = acc
    return out


def rsi_series(closes, period=14):
    out = [50.0] * len(closes)
    gains = 0.0
    losses = 0.0
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if i <= period:
            gains += max(delta, 0.0)
            losses += max(-delta, 0.0)
            if i == period:
                ag = gains / period
                al = losses / period
                out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
            continue
        gains = gains - gains / period + max(delta, 0.0)
        losses = losses - losses / period + max(-delta, 0.0)
        ag = gains / period
        al = losses / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out
