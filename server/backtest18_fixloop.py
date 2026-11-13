"""backtest18_fixloop.py — loop "sinyal salah -> audit -> perbaiki" yang jujur.

Permintaan pemilik: setiap sinyal salah diaudit dan strategi diperbaiki sampai menang.
Loop ini melakukannya secara mekanis, dengan pengaman agar tidak menghafal data:

  1. Mulai dari strategi dasar (z>2 revert + vol HI, strategi aktif app) pada T-5.
  2. Kumpulkan sinyal TRAIN yang LOSS vs WIN.
  3. Untuk tiap fitur kandidat x ambang, cari filter "buang sinyal bila fitur di
     sisi ini" yang paling menaikkan winrate TRAIN dengan sisa n >= MIN_N_TRAIN.
  4. Pasang filter, ukur HOLDOUT. Lanjut hanya bila HOLDOUT ikut naik.
  5. Berhenti saat HOLDOUT tidak naik 2 putaran berturut (tanda mulai menghafal).
  6. Lockbox (hari 6+) dibuka sekali di akhir untuk vonis.
"""
from __future__ import annotations

import sys

import numpy as np

import research_harness as H
from backtest16_reverse import feats, roll_mean, shift

MIN_N_TRAIN = 3000
MAX_ROUNDS = 8


def _streak(d: np.ndarray) -> np.ndarray:
    """Panjang streak arah candle tertutup sebelum i (bertanda)."""
    out = np.zeros(len(d))
    run = 0.0
    for i in range(1, len(d)):
        x = d[i - 1]
        run = run + x if x != 0 and np.sign(run) == x else x
        out[i] = run
    return out


def extra_feats(T: dict) -> dict:
    f = dict(feats(T))
    if "_x" not in T:
        rng_prev = shift(T["h"] - T["l"])
        atr = roll_mean(rng_prev, 14)
        T["_x"] = {
            "atr_rel": atr / np.maximum(roll_mean(rng_prev, 60), 1e-12),
            "n5_rel": T["n5"] / np.maximum(shift(roll_mean(T["n"].astype(float), 20)), 1),
            "streak": _streak(np.sign(T["c"] - T["o"])),
            "wick_up": shift((T["h"] - np.maximum(T["o"], T["c"])) / np.maximum(T["h"] - T["l"], 1e-12)),
            "wick_dn": shift((np.minimum(T["o"], T["c"]) - T["l"]) / np.maximum(T["h"] - T["l"], 1e-12)),
            "r5_atr": (T["h5"] - T["l5"]) / np.maximum(atr, 1e-12),
            "abs_z": np.abs(f["z"]),
        }
    f.update(T["_x"])
    return f


def base_rule(T: dict) -> np.ndarray:
    f = feats(T)
    d = np.where(f["z"] < -2, 1, np.where(f["z"] > 2, -1, 0))
    return np.where(f["volhi"], d, 0)


def make_rule(filters):
    def rule(T):
        d = base_rule(T).astype(float)
        f = extra_feats(T)
        for k, signed, side, q in filters:
            x = np.asarray(f[k], dtype=float) * (d if signed else 1)
            keep = np.isfinite(x) & ((x < q) if side == "lt" else (x > q))
            d = np.where(keep, d, 0)
        return d
    return rule


def train_signals(tabs: dict, rule):
    d0 = H._day_index(tabs)
    rows = []
    for T in tabs.values():
        d = np.where(T["ok"], np.asarray(rule(T), dtype=float), 0)
        day = T["t"] // H.DAY - d0
        idx = np.flatnonzero((d != 0) & (day < H.HOLDOUT_FROM_DAY))
        if not len(idx):
            continue
        f = extra_feats(T)
        rows.append(({k: np.asarray(v, dtype=float)[idx] for k, v in f.items() if k != "hour"},
                     d[idx], T["y"][idx] == d[idx]))
    F = {k: np.concatenate([r[0][k] for r in rows]) for k in rows[0][0]}
    return F, np.concatenate([r[1] for r in rows]), np.concatenate([r[2] for r in rows])


def best_filter(F: dict, dirs: np.ndarray, win: np.ndarray):
    best = None
    for k, v in F.items():
        for signed in (False, True):
            x = v * dirs if signed else v
            ok = np.isfinite(x)
            if ok.sum() < MIN_N_TRAIN:
                continue
            for q in np.unique(np.quantile(x[ok], np.linspace(0.05, 0.95, 19))):
                for side in ("lt", "gt"):
                    keep = ok & ((x < q) if side == "lt" else (x > q))
                    n = int(keep.sum())
                    if n < MIN_N_TRAIN:
                        continue
                    wr = win[keep].mean()
                    if best is None or wr > best[0]:
                        best = (wr, k, signed, side, float(q), n)
    return best if best and best[0] > win.mean() else None


def main() -> None:
    tabs = H.load_tables()
    filters = []
    r = H.evaluate("R0 dasar z>2 volHI @T-5", make_rule(filters), tabs)
    best_hold, stall, history = r["holdout"]["wr"], 0, [([], r)]
    for rnd in range(1, MAX_ROUNDS + 1):
        F, dirs, win = train_signals(tabs, make_rule(filters))
        print(f"  audit R{rnd - 1}: TRAIN {len(win)} sinyal, {int((~win).sum())} salah", flush=True)
        bf = best_filter(F, dirs, win)
        if bf is None:
            print("  tidak ada filter yang menaikkan TRAIN -> stop")
            break
        _, k, signed, side, q, _ = bf
        filters.append((k, signed, side, q))
        desc = f"{k}{'*dir' if signed else ''} {'<' if side == 'lt' else '>'} {q:.3g}"
        r = H.evaluate(f"R{rnd} +[{desc}]", make_rule(filters), tabs)
        history.append((list(filters), r))
        if r["holdout"]["wr"] > best_hold + 0.05:
            best_hold, stall = r["holdout"]["wr"], 0
        else:
            stall += 1
            if stall >= 2:
                print("  HOLDOUT tidak naik 2 putaran -> berhenti (mulai menghafal)")
                break
    chosen = max(history, key=lambda h: h[1]["holdout"]["lb"])
    print("\nDIPILIH (lb95 holdout terbaik):", chosen[0])
    if "--lockbox" in sys.argv:
        H.OPEN_LOCKBOX = True
        H.evaluate("VONIS dipilih", make_rule(chosen[0]), tabs)
        H.evaluate("VONIS dasar R0", make_rule([]), tabs)


if __name__ == "__main__":
    main()
