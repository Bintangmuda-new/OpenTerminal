"""my_backtest.py — backtest independen (ditulis ulang dari nol).

Tujuan: menjawab "bisa tidak winrate 100%?" dengan bukti, bukan klaim.

Yang diuji:
  1. Baseline feed (arah candle) — pembanding netral.
  2. 8 keluarga aturan, split train 60% / holdout 40% per simbol.
     Peringkat dipilih dari TRAIN, angka dilaporkan dari HOLDOUT.
  3. Uji null: acak hasil, jalankan sweep yang sama -> apakah "sel bagus"
     pada data nyata hanya artefak seleksi?
  4. Uji look-ahead: satu-satunya cara mencapai 100%.
  5. Pencarian artefak data: candle tanpa gerak, duplikat waktu, celah,
     simbol yang "terlalu mudah diprediksi".
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np

DB = Path(__file__).resolve().parent.parent / "data" / "otc_research.sqlite3"
RNG = np.random.default_rng(20261014)


def wilson_low(w, n, z=1.96):
    if n == 0:
        return 0.0
    p = w / n
    d = 1 + z * z / n
    return ((p + z * z / (2 * n)) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d * 100


def sma(v, p):
    out = np.full_like(v, np.nan, dtype=float)
    if len(v) >= p:
        c = np.cumsum(np.insert(v, 0, 0.0))
        out[p - 1:] = (c[p:] - c[:-p]) / p
    return out


def rsi_series(v, p=14):
    d = np.diff(v, prepend=v[0])
    up = np.clip(d, 0, None)
    dn = np.clip(-d, 0, None)
    ag = np.zeros_like(v)
    al = np.zeros_like(v)
    if len(v) > p:
        ag[p] = up[1:p + 1].mean()
        al[p] = dn[1:p + 1].mean()
        for i in range(p + 1, len(v)):
            ag[i] = (ag[i - 1] * (p - 1) + up[i]) / p
            al[i] = (al[i - 1] * (p - 1) + dn[i]) / p
    return np.where(al > 0, 100 - 100 / (1 + ag / np.maximum(al, 1e-12)), 50.0)


def load():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    frames = []
    for (sym,) in con.execute("select distinct symbol from candles_m1 order by symbol"):
        rows = con.execute(
            "select time,open,high,low,close from candles_m1 where symbol=? order by time", (sym,)
        ).fetchall()
        if len(rows) < 400:
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        frames.append({"symbol": sym, "t": t, "o": o, "h": h, "l": l, "c": c})
    con.close()
    return frames


def features(f):
    o, h, l, c = f["o"], f["h"], f["l"], f["c"]
    dev = np.maximum(sma(np.abs(np.diff(c, prepend=c[0])), 20), 1e-12)
    s20 = sma(c, 20)
    sd20 = np.full_like(c, np.nan)
    if len(c) >= 20:
        for i in range(19, len(c)):
            sd20[i] = c[i - 19:i + 1].std()
    rng = h - l
    med_rng = np.full_like(c, np.nan)
    if len(c) >= 60:
        for i in range(59, len(c)):
            med_rng[i] = np.median(rng[i - 59:i + 1])
    # arah candle berikutnya (target): +1 naik, -1 turun, 0 datar
    nxt = np.concatenate([np.sign(c - o)[1:], [0.0]])
    return {
        "c": c,
        "z": (c - s20) / dev,
        "rsi": rsi_series(c, 14),
        "sd20": sd20,
        "s20": s20,
        "rng": rng,
        "med_rng": med_rng,
        "nxt": nxt,
        "up": np.sign(c - o),
    }


def rules(feat):
    """Kembalikan dict nama-aturan -> (mask_valid, arah_prediksi +1/-1)."""
    z, rsi, c = feat["z"], feat["rsi"], feat["c"]
    s20, sd20, rng, med_rng = feat["s20"], feat["sd20"], feat["rng"], feat["med_rng"]
    up = feat["up"]
    out = {}

    def add(name, mask, direction):
        m = mask & np.isfinite(z) & (feat["nxt"] != 0)
        out[name] = (m, direction)

    # 1. z-score revert (aturan yang dipakai aplikasi)
    add("z>2 revert", np.abs(z) > 2, np.where(z > 0, -1, 1))
    add("z>3 revert", np.abs(z) > 3, np.where(z > 0, -1, 1))
    # 2. RSI ekstrem (fade)
    add("rsi<30/>70 fade", (rsi < 30) | (rsi > 70), np.where(rsi > 50, -1, 1))
    add("rsi<20/>80 fade", (rsi < 20) | (rsi > 80), np.where(rsi > 50, -1, 1))
    # 3. Bollinger band fade
    bb = np.where(sd20 > 0, (c - s20) / np.maximum(sd20, 1e-12), 0)
    add("bb>2 fade", np.abs(bb) > 2, np.where(bb > 0, -1, 1))
    # 4. Streak fade (3+ searah -> lawan)
    streak = np.zeros_like(c)
    for i in range(1, len(c)):
        streak[i] = streak[i - 1] + up[i] if up[i] == up[i - 1] and up[i] != 0 else up[i]
    add("streak>=3 fade", np.abs(streak) >= 3, np.where(streak > 0, -1, 1))
    # 5. Momentum (ikuti arah candle terakhir)
    add("momentum follow", np.ones_like(c, dtype=bool), up)
    # 6. EMA cross (9/21)
    e9 = np.full_like(c, np.nan)
    e21 = np.full_like(c, np.nan)
    if len(c) > 21:
        e9[8] = c[:9].mean()
        e21[20] = c[:21].mean()
        for i in range(9, len(c)):
            e9[i] = e9[i - 1] + (c[i] - e9[i - 1]) * 2 / 10
        for i in range(21, len(c)):
            e21[i] = e21[i - 1] + (c[i] - e21[i - 1]) * 2 / 22
    add("ema9>21 follow", np.isfinite(e9) & np.isfinite(e21) & (e9 != e21),
        np.where(e9 > e21, 1, -1))
    # 7. z>2 revert + filter volatilitas (aturan final aplikasi)
    vol_hi = rng > med_rng
    add("z>2 + VOL HI", (np.abs(z) > 2) & vol_hi, np.where(z > 0, -1, 1))
    # 8. Kontrol: selalu CALL
    add("control always CALL", np.ones_like(c, dtype=bool), np.ones_like(c))
    return out


def evaluate(frames, split=0.6):
    """Peringkat dari train, angka dari holdout."""
    train_cells = {}
    hold_cells = {}
    for f in frames:
        feat = features(f)
        n = len(f["c"])
        cut = int(n * split)
        for name, (mask, direction) in rules(feat).items():
            win = (direction == feat["nxt"]) & mask
            for tag, sl in (("train", slice(0, cut)), ("hold", slice(cut, n))):
                m = mask[sl]
                w = int(win[sl].sum())
                tot = int(m.sum())
                if tot == 0:
                    continue
                d = train_cells if tag == "train" else hold_cells
                a = d.setdefault(name, [0, 0])
                a[0] += w
                a[1] += tot
    return train_cells, hold_cells


def main():
    frames = load()
    total_bars = sum(len(f["c"]) for f in frames)
    print(f"symbols={len(frames)}  bars={total_bars}\n")

    # --- 1. baseline
    all_up = all_dn = all_flat = 0
    for f in frames:
        s = np.sign(f["c"] - f["o"])
        all_up += int((s > 0).sum())
        all_dn += int((s < 0).sum())
        all_flat += int((s == 0).sum())
    tot = all_up + all_dn
    print("=== 1. BASELINE FEED")
    print(f"  naik {all_up}  turun {all_dn}  datar {all_flat}")
    print(f"  P(naik) = {all_up / tot * 100:.2f}%   (netral = 50%)\n")

    # --- 2. aturan, train vs holdout
    tr, ho = evaluate(frames)
    print("=== 2. ATURAN (peringkat dari TRAIN, angka dari HOLDOUT)")
    print(f"  {'aturan':22} {'train%':>8} {'hold%':>8} {'n_hold':>8} {'lb95':>7}")
    ranked = sorted(tr.items(), key=lambda kv: -(kv[1][0] / kv[1][1]))
    for name, (w, n) in ranked:
        hw, hn = ho.get(name, (0, 0))
        if hn == 0:
            continue
        print(f"  {name:22} {w / n * 100:8.2f} {hw / hn * 100:8.2f} {hn:8d} {wilson_low(hw, hn):7.2f}")
    print()

    # --- 3. uji null
    print("=== 3. UJI NULL (acak TARGET, sweep sama, x10)")
    real_best = max((hw / hn * 100) for hw, hn in ho.values() if hn >= 100)
    null_best = []
    for it in range(10):
        tr2, ho2 = {}, {}
        for f in frames:
            feat = features(f)
            n = len(f["c"])
            cut = int(n * 0.6)
            # acak target: hancurkan hubungan aturan<->hasil, jumlah tetap
            perm = RNG.permutation(n)
            feat["nxt"] = feat["nxt"][perm]
            for name, (mask, direction) in rules(feat).items():
                win = (direction == feat["nxt"]) & mask
                for tag, sl in (("train", slice(0, cut)), ("hold", slice(cut, n))):
                    m = mask[sl]
                    if m.sum() == 0:
                        continue
                    w = int(win[sl].sum())
                    d = tr2 if tag == "train" else ho2
                    a = d.setdefault(name, [0, 0])
                    a[0] += w
                    a[1] += int(m.sum())
        nb = max((hw / hn * 100) for hw, hn in ho2.values() if hn >= 100)
        null_best.append(nb)
        print(f"  null {it + 1:2d}: best holdout {nb:.2f}%")
    nb = np.array(null_best)
    print(f"  null: mean={nb.mean():.2f}%  p95={np.percentile(nb, 95):.2f}%  max={nb.max():.2f}%")
    print(f"  real best {real_best:.2f}% vs null p95 {np.percentile(nb, 95):.2f}%  ->  "
          f"{'ARTEFAK SELEKSI' if real_best <= np.percentile(nb, 95) else 'MENONJOL'}\n")

    # --- 4. look-ahead
    print("=== 4. LOOK-AHEAD (satu-satunya jalan ke 100%)")
    lw = ln = 0
    for f in frames:
        o, c = f["o"], f["c"]
        i = np.arange(25, len(c) - 1)
        b = np.sign(c[i + 1] - o[i + 1])
        v = b != 0
        lw += int(v.sum())
        ln += int(v.sum())
    print(f"  sinyal = arah candle entry B (dibaca dari masa depan)")
    print(f"  winrate {lw / ln * 100:.4f}%   n={ln}   lb95={wilson_low(lw, ln):.4f}")
    print("  -> 100% hanya tercapai dengan membaca candle yang belum ada di feed live.\n")

    # --- 5. artefak data
    print("=== 5. PENCARIAN ARTEFAK DATA")
    flat = dup = gaps = 0
    for f in frames:
        flat += int((f["h"] == f["l"]).sum())
        dt = np.diff(f["t"])
        dup += int((dt == 0).sum())
        gaps += int((dt > 60).sum())
    print(f"  candle tanpa gerak (high==low): {flat}")
    print(f"  duplikat waktu: {dup}")
    print(f"  celah > 60s: {gaps}")

    # simbol paling "mudah diprediksi" (always CALL)
    per = []
    for f in frames:
        s = np.sign(f["c"] - f["o"])
        v = s != 0
        if v.sum() >= 200:
            per.append((f["symbol"], (s[v] > 0).mean() * 100, int(v.sum())))
    per.sort(key=lambda x: -x[1])
    print("  simbol paling bias naik (always CALL):")
    for sym, wr, n in per[:5]:
        print(f"    {sym:16} {wr:6.2f}%  n={n}")
    print("  simbol paling bias turun (always PUT):")
    for sym, wr, n in per[-5:]:
        print(f"    {sym:16} {wr:6.2f}%  n={n}")
    print("\n  Catatan: bias per-simbol = tren periode data, bukan edge yang bertahan.")

    print("\n=== VERDICT")
    print(f"  Ceiling holdout tertinggi: {real_best:.2f}%  (baseline 50%)")
    print("  100% hanya via look-ahead. Tidak ada aturan jujur yang melewatinya.")


if __name__ == "__main__":
    sys.exit(main())
