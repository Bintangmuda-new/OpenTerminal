"""backtest47_extreme_sweep.py - sapuan ekstrem untuk target 70%, protokol kausal penuh.

Tujuan: cari SATU sel dengan winrate >= 70%, n >= 200, pada data yang belum dipakai
memilih parameter. Kalau tidak ada, laporkan batas atas empiris yang sebenarnya.

Protokol (sama dengan backtest46, tidak ada lookahead):
  * Fitur hanya dari data <= waktu_keputusan (menit i detik off).
  * Entry = tick pertama dengan t >= waktu_keputusan + DELAY.
  * Exit  = tick terakhir dengan t <= entry + H.
  * Tie (exit == entry) dikeluarkan dari penyebut winrate.
  * Ambang dipilih dari TRAIN saja, dilaporkan apa adanya di HOLDOUT dan LOCKBOX.
"""
from __future__ import annotations
import numpy as np
from pathlib import Path
import sys, json

CACHE = Path(r"E:\otc_cache")
DAY = 86400
WARMUP = 60
DELAY = 0.5
OFFSETS = (10, 25, 40, 55)
DURATIONS = (15, 60, 300)
MIN_TRADES = 200
TARGET = 0.70


def load_symbol(folder: Path, sym: str):
    f = folder / ("%s.npz" % sym)
    if not f.exists():
        return None
    try:
        z = np.load(f)
        # t disimpan sebagai float epoch; jangan cast ke int64 sebelum split hari.
        t = np.asarray(z["t"], dtype=float)
        p = np.asarray(z["p"], dtype=float)
    except Exception:
        return None
    if len(t) < 1000:
        return None
    o = np.argsort(t, kind="stable")
    t, p = t[o], p[o]
    keep = np.append(True, np.diff(t) > 0)
    t, p = t[keep], p[keep]
    ok = np.isfinite(p) & (p > 0)
    t, p = t[ok], p[ok]
    if len(t) < 1000:
        return None
    return t, p


def minutes(t, p):
    m = (t // 60).astype(np.int64) * 60
    u, st = np.unique(m, return_index=True)
    en = np.append(st[1:], len(t))
    return dict(m=u, o=p[st], h=np.maximum.reduceat(p, st),
                l=np.minimum.reduceat(p, st), c=p[en - 1],
                consec=np.append(np.diff(u) == 60, False))


def build_features(T):
    """Fitur KAUSAL: untuk keputusan di menit i, hanya candle indeks <= i-1 yang boleh dipakai.

    PENTING: close[i]/high[i]/low[i] menit i baru diketahui setelah menit i tutup.
    Memakainya di keputusan detik 10/25/40/55 adalah lookahead (terbukti di
    proof_pos_leak.py: pos>=0.9 -> candle naik 98.4%). Semua fitur di bawah
    digeser satu candle ke belakang supaya hanya memakai candle yang sudah tutup.
    """
    c = T["c"]; h = T["h"]; l = T["l"]; o = T["o"]
    n = len(c)
    feat = {}

    # candle terakhir yang SUDAH TUTUP saat keputusan di menit i adalah indeks i-1
    def lag1(x):
        out = np.full(n, np.nan)
        out[1:] = x[:-1]
        return out

    c_l = lag1(c)
    h_l = lag1(h)
    l_l = lag1(l)
    o_l = lag1(o)

    prev = np.full(n, np.nan)   # close dua candle tertutup lalu (untuk return)
    prev[2:] = c[:-2]

    ma20 = np.full(n, np.nan)
    for i in range(20, n):
        ma20[i] = c_l[i-19:i+1].mean()
    sd20 = np.full(n, np.nan)
    for i in range(20, n):
        sd20[i] = c_l[i-19:i+1].std()

    rng = h_l - l_l
    med_rng = np.full(n, np.nan)
    for i in range(60, n):
        med_rng[i] = np.median(rng[i-60:i])

    feat["z"] = (c_l - ma20) / np.where(sd20 > 0, sd20, np.nan)
    feat["body"] = (c_l - o_l) / np.where(rng > 0, rng, np.nan)
    feat["rng_ratio"] = rng / np.where(med_rng > 0, med_rng, np.nan)
    feat["pos"] = (c_l - l_l) / np.where(rng > 0, rng, np.nan)
    # arah candle tertutup terakhir (bukan candle aktif)
    upc = np.full(n, np.nan)
    upc[1:] = np.where(np.isfinite(c_l[1:]) & np.isfinite(o_l[1:]), (c_l[1:] > o_l[1:]).astype(float), np.nan)
    s = np.zeros(n)
    for i in range(1, n):
        if np.isfinite(upc[i]) and np.isfinite(upc[i-1]) and upc[i] == upc[i-1]:
            s[i] = s[i-1] + 1.0
        else:
            s[i] = 1.0
    feat["streak"] = s * np.where(upc > 0, 1, -1)
    feat["streak"][~np.isfinite(upc)] = np.nan
    ret = np.full(n, np.nan)
    ret[1:] = (c_l[1:] - prev[1:]) / np.where(prev[1:] > 0, prev[1:], np.nan)
    return feat, ret, upc


def plan(T, t, p, off, H):
    i = np.flatnonzero(T["consec"][WARMUP:]) + WARMUP
    if len(i) == 0:
        return None
    dec = T["m"][i] + off
    j = np.searchsorted(t, dec, side="right") - 1
    ok = (j >= 0) & (t[np.maximum(j, 0)] >= T["m"][i])
    k = np.searchsorted(t, dec + DELAY, side="left")
    ok &= k < len(t) - 1
    i, j, k = i[ok], j[ok], k[ok]
    if len(i) == 0:
        return None
    exit_idx = np.searchsorted(t, t[k] + H, side="right") - 1
    keep = exit_idx > k
    i, j, k, exit_idx = i[keep], j[keep], k[keep], exit_idx[keep]
    if len(i) == 0:
        return None
    # satu posisi per simbol: tidak boleh overlap
    last = -1
    sel = []
    for idx in range(len(i)):
        if k[idx] > last:
            sel.append(idx); last = exit_idx[idx]
    sel = np.array(sel, dtype=int)
    if len(sel) == 0:
        return None
    return i[sel], k[sel], exit_idx[sel]


def run():
    folder = CACHE / "ticks"
    syms = sorted(f.stem for f in folder.glob("*.npz"))
    print("symbols: %d" % len(syms), flush=True)

    rows = []
    DAY_LO = 20709          # 8 hari absolut di cache ini: 20709..20716
    for si, sym in enumerate(syms):
        got = load_symbol(folder, sym)
        if got is None:
            continue
        t, p = got
        T = minutes(t, p)
        feat, ret, up = build_features(T)
        # mi adalah indeks MENIT, jadi hari harus dari T["m"], bukan dari array tick t.
        dm = np.floor(T["m"] / DAY).astype(np.int64)
        day = (dm - DAY_LO)
        for off in OFFSETS:
            for H in DURATIONS:
                pl = plan(T, t, p, off, H)
                if pl is None:
                    continue
                mi, ki, xi = pl
                entry = p[ki]; exitp = p[xi]
                y = np.where(exitp > entry, 1, np.where(exitp < entry, -1, 0))
                for fname in ("z", "body", "rng_ratio", "pos", "streak"):
                    fv = feat[fname][mi]
                    good = np.isfinite(fv) & (y != 0)
                    if good.sum() < 50:
                        continue
                    rows.append(dict(sym=sym, off=off, H=H, f=fname,
                                     v=fv[good], y=y[good], day=day[mi][good]))
        if (si + 1) % 20 == 0:
            print("  loaded %d/%d symbols" % (si + 1, len(syms)), flush=True)

    print("blocks: %d" % len(rows), flush=True)
    if not rows:
        return
    # Setiap baris = satu blok (simbol, offset, H, fitur).
    # v = nilai FITUR, y = HASIL trade {-1,0,1}. Jangan tertukar.
    print("blocks: %d" % len(rows), flush=True)

    def hit_rate(y, mask, direction):
        m = mask
        if m.sum() == 0:
            return None
        yy = y[m]
        if direction == 1:
            hit = yy > 0
        elif direction == -1:
            hit = yy < 0
        else:
            return None
        n = int(m.sum())
        w = int(hit.sum())
        return w, n, (w / n if n else 0.0)

    print("%-10s %4s %5s %6s | %8s %8s | %8s %8s | %8s %8s" % (
        "feat", "off", "H", "thr", "train", "n", "holdout", "n", "lockbox", "n"), flush=True)
    best = []
    for r in rows:
        fname, off, H = r["f"], r["off"], r["H"]
        v, y, day = r["v"], r["y"], r["day"]
        base = np.isfinite(v)
        # normalisasi skala: pakai z-score dalam blok agar ambang sebanding antar fitur
        finite = v[base]
        if len(finite) < 50:
            continue
        sd = finite.std()
        if not np.isfinite(sd) or sd <= 0:
            continue
        vz = np.zeros_like(v, dtype=float)
        vz[base] = v[base] / sd
        for thr in (1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
            mtr = base & (np.abs(vz) >= thr) & ((day <= 5))
            if mtr.sum() < MIN_TRADES:
                continue
            d = np.sign(np.median(vz[mtr]))
            if d == 0:
                continue
            r_tr = hit_rate(y, mtr, d)
            if r_tr is None:
                continue
            r_ho = hit_rate(y, base & (np.abs(vz) >= thr) & (day == 6), d)
            r_lk = hit_rate(y, base & (np.abs(vz) >= thr) & (day >= 7), d)
            line = "%-10s %4d %5d %6.1f | %7.2f%% %8d |" % (
                fname, off, H, thr, r_tr[2] * 100, r_tr[1])
            if r_ho:
                line += " %7.2f%% %8d |" % (r_ho[2] * 100, r_ho[1])
            else:
                line += " %8s %8s |" % ("-", "-")
            if r_lk:
                line += " %7.2f%% %8d" % (r_lk[2] * 100, r_lk[1])
            else:
                line += " %8s %8s" % ("-", "-")
            print(line, flush=True)
            if r_tr[2] >= TARGET:
                best.append((fname, off, H, thr, r_tr, r_ho, r_lk))
    print("", flush=True)
    print("cells at train >= %.0f%%: %d" % (TARGET * 100, len(best)), flush=True)
    for b in best[:20]:
        print("  BEST %s off=%d H=%d thr=%.1f train=%.2f%% n=%d holdout=%s lockbox=%s" % (
            b[0], b[1], b[2], b[3], b[4][2] * 100, b[4][1],
            ("%.2f%% n=%d" % (b[5][2] * 100, b[5][1])) if b[5] else "-",
            ("%.2f%% n=%d" % (b[6][2] * 100, b[6][1])) if b[6] else "-"), flush=True)


if __name__ == "__main__":
    run()
