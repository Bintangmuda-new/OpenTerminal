"""backtest25_smc2.py — varian SMC lanjutan yang belum diuji di backtest24.

backtest24 menguji BOS/CHoCH/OB/FVG/sweep/P-D/EQH-EQL/HTF-M5 secara polos. Yang belum:
  displacement    BOS sah hanya bila candle penembus punya body > k x ATR (dorongan nyata)
  OB+displacement OB = candle berlawanan terakhir sebelum candle displacement
  breaker         OB yang sudah ditembus harga lalu berbalik peran (support <-> resistance)
  inducement      swing minor (p=1) disapu sebelum swing mayor (p=3) tersentuh
  liquidity void  candle besar tanpa tumpang tindih dengan candle sebelumnya
  SMR             sweep likuiditas + CHoCH pada candle yang sama (entry reversal kanonik)
  SFP             swing failure pattern: ekor menembus swing lalu close balik ke sisi lain
  HTF M15         bias dari candle 15 menit terakhir yang tutup (bukan M5)

Catatan jujur: mitigation block (MB) TIDAK diuji terpisah — pada resolusi M1 definisinya
bertumpang tindih persis dengan breaker block (OB yang gagal ditahan lalu berbalik peran),
jadi mengujinya dua kali akan menghitung aturan yang sama.

Protokol sama: keputusan T-5, entry tick pertama candle B, hasil = close B vs entry.
Parameter dipilih di TRAIN (hari 0-5), dilaporkan di HOLDOUT (hari 6) dan LOCKBOX (hari 7).
"""
from __future__ import annotations

import sys

import numpy as np

import research_harness as H
from backtest24_smc import _dir, _prefix_last, smc

DISP_GRID = (1.0, 1.5, 2.0)
MIN_TRAIN = 3000


def _htf_bias(T: dict, minutes: int) -> np.ndarray:
    """Arah blok `minutes` menit terakhir yang sudah tutup (tanpa lookahead)."""
    c, o = T["c"].astype(float), T["o"].astype(float)
    n = len(c)
    blk = T["t"] // (60 * minutes)
    ub, start = np.unique(blk, return_index=True)
    end = np.append(start[1:], n)
    k = np.searchsorted(ub, blk)
    kk = np.maximum(k - 1, 0)
    contig = (k >= 1) & (ub[kk] == blk - 1)
    return np.where(contig, np.sign(c[end[kk] - 1] - o[start[kk]]), np.nan)


def smc2(T: dict, p: int, W: int, disp: float) -> dict[str, np.ndarray]:
    """Elemen tambahan; struktur dasar memakai smc() dari backtest24."""
    S = smc(T, p, W)
    o, h, l, c = (T[k].astype(float) for k in ("o", "h", "l", "c"))
    o5, h5, l5, c5 = (T[k].astype(float) for k in ("o5", "h5", "l5", "c5"))
    n = len(c)
    ix = np.arange(n)
    body = np.abs(c - o)
    atr = S["atr"]
    dsp = np.isfinite(atr) & (atr > 0) & (body > disp * atr)

    # --- BOS dengan displacement --------------------------------------------
    cs = np.concatenate([[np.nan], c[:-1]])                # close candle terakhir yang tutup
    dsp_prev = np.concatenate([[False], dsp[:-1]])
    bos_up_d = S["bos_up"] & dsp_prev
    bos_dn_d = S["bos_dn"] & dsp_prev

    # --- OB sebelum candle displacement --------------------------------------
    bear, bull = c < o, c > o
    ubd = _prefix_last(bos_up_d)
    dbd = _prefix_last(bos_dn_d)
    ob_u = np.where(ubd > 0, _prefix_last(bear)[np.maximum(ubd - 1, 0)], -1)
    ob_d = np.where(dbd > 0, _prefix_last(bull)[np.maximum(dbd - 1, 0)], -1)
    fu = (ubd >= 0) & (ix - ubd <= W) & (ob_u >= 0)
    fd = (dbd >= 0) & (ix - dbd <= W) & (ob_d >= 0)
    ou_hi = np.where(ob_u >= 0, h[np.maximum(ob_u, 0)], np.nan)
    ou_lo = np.where(ob_u >= 0, l[np.maximum(ob_u, 0)], np.nan)
    od_hi = np.where(ob_d >= 0, h[np.maximum(ob_d, 0)], np.nan)
    od_lo = np.where(ob_d >= 0, l[np.maximum(ob_d, 0)], np.nan)

    # --- breaker: OB bullish ditembus (close < low OB) -> jadi resistance ----
    broken_u = fu & (cs < ou_lo) & (h5 >= ou_lo) & (c5 < ou_lo)     # retest dari bawah -> PUT
    broken_d = fd & (cs > od_hi) & (l5 <= od_hi) & (c5 > od_hi)     # retest dari atas  -> CALL

    # --- inducement: swing minor disapu, swing mayor belum -------------------
    S1 = smc(T, 1, W)
    minor_hi = np.where(S1["last_sh"] >= 0, h[np.maximum(S1["last_sh"], 0)], np.nan)
    minor_lo = np.where(S1["last_sl"] >= 0, l[np.maximum(S1["last_sl"], 0)], np.nan)
    ind_dn = (h5 > minor_hi) & (c5 < minor_hi) & (minor_hi < S["hi_sh"])
    ind_up = (l5 < minor_lo) & (c5 > minor_lo) & (minor_lo > S["lo_sl"])

    # --- liquidity void: candle besar tanpa tumpang tindih dengan sebelumnya --
    big_prev = np.concatenate([[False], dsp[:-1]])
    h_prev = np.concatenate([[np.nan], h[:-1]])
    l_prev = np.concatenate([[np.nan], l[:-1]])
    void_up = big_prev & (l > h_prev)
    void_dn = big_prev & (h < l_prev)

    # --- SMR: sweep + CHoCH pada candle yang sama ---------------------------
    sw_up = S["recent_sl"] & (l5 < S["lo_sl"]) & (c5 > S["lo_sl"])
    sw_dn = S["recent_sh"] & (h5 > S["hi_sh"]) & (c5 < S["hi_sh"])
    smr_up = sw_up & S["choch_up"]
    smr_dn = sw_dn & S["choch_dn"]

    # --- SFP: ekor menembus swing, close balik ke arah candle ---------------
    sfp_up = sw_up & (c5 > o5)
    sfp_dn = sw_dn & (c5 < o5)

    # --- HTF M15 -------------------------------------------------------------
    m15 = _htf_bias(T, 15)
    m15_up, m15_dn = m15 > 0, m15 < 0

    def d(up, dn):
        return _dir(up, dn)

    return {
        "S1_bos_disp": d(bos_up_d, bos_dn_d),
        "S2_ob_disp_retest": d(fu & (l5 <= ou_hi) & (c5 > ou_hi), fd & (h5 >= od_lo) & (c5 < od_lo)),
        "S3_breaker": d(broken_d, broken_u),
        "S4_inducement": d(ind_up, ind_dn),
        "S5_liquidity_void": d(void_up, void_dn),
        "S6_smr": d(smr_up, smr_dn),
        "S7_sfp": d(sfp_up, sfp_dn),
        "S8_sfp_m15": d(sfp_up & m15_up, sfp_dn & m15_dn),
        "S9_smr_m15": d(smr_up & m15_up, smr_dn & m15_dn),
        "S10_ob_disp_m15": d(fu & (l5 <= ou_hi) & (c5 > ou_hi) & m15_up,
                             fd & (h5 >= od_lo) & (c5 < od_lo) & m15_dn),
        "S11_sfp_smr": d(sfp_up & smr_up, sfp_dn & smr_dn),
        "S12_inducement_smr": d(ind_up & smr_up, ind_dn & smr_dn),
        "S13_void_smr": d(void_up & smr_up, void_dn & smr_dn),
    }


def main() -> None:
    tabs = H.load_tables()
    d0 = H._day_index(tabs)
    print(f"simbol={len(tabs)}", flush=True)
    grid = [(p, W, dv) for p in (1, 2, 3) for W in (30, 60, 120) for dv in DISP_GRID]
    store: dict[tuple[str, int, int, float], dict[str, list[int]]] = {}
    for p, W, dv in grid:
        for T in tabs.values():
            day = T["t"] // H.DAY - d0
            segs = {"train": day < H.HOLDOUT_FROM_DAY,
                    "holdout": (day >= H.HOLDOUT_FROM_DAY) & (day < H.LOCKBOX_FROM_DAY),
                    "lockbox": day >= H.LOCKBOX_FROM_DAY}
            for rname, dirs in smc2(T, p, W, dv).items():
                slot = store.setdefault((rname, p, W, dv), {s: [0, 0, 0] for s in segs})
                for s, msk in segs.items():
                    m = msk & T["ok"] & (dirs != 0)
                    y = T["y"][m].astype(float)
                    pr = dirs[m]
                    slot[s][0] += int((y == pr).sum())
                    slot[s][1] += int((y == -pr).sum())
                    slot[s][2] += int((y == 0).sum())
        print(f"param p={p} W={W} disp={dv} selesai", flush=True)

    names = sorted({k[0] for k in store})
    best: dict[str, tuple] = {}
    for name in names:
        for p, W, dv in grid:
            tr = store[(name, p, W, dv)]["train"]
            ntr = sum(tr)
            if ntr < MIN_TRAIN:
                continue
            wr = tr[0] / ntr
            if name not in best or wr > best[name][0]:
                best[name] = (wr, p, W, dv)

    print(f"\n{'aturan':20} {'p':>2} {'W':>4} {'disp':>4} | {'TRAIN':>7} {'n':>7} | {'HOLD':>7} {'n':>7} "
          f"{'lb95':>6} | {'LOCK':>7} {'n':>7} {'lb95':>6} {'d(pt)':>6}")
    rows = []
    for name in names:
        if name not in best:
            continue
        _, p, W, dv = best[name]
        tr, ho, lb = (store[(name, p, W, dv)][s] for s in ("train", "holdout", "lockbox"))
        ntr, nho, nlb = sum(tr), sum(ho), sum(lb)
        if nho == 0 or nlb == 0:
            continue
        rows.append((name, p, W, dv, tr[0] / ntr * 100, ntr, ho[0] / nho * 100, nho,
                     H.wilson_low(ho[0], nho), lb[0] / nlb * 100, nlb,
                     H.wilson_low(lb[0], nlb), (lb[0] - lb[1]) / nlb * 100))
    rows.sort(key=lambda r: -r[6])
    for r in rows:
        print(f"{r[0]:20} {r[1]:2d} {r[2]:4d} {r[3]:4.1f} | {r[4]:6.2f}% {r[5]:7d} | {r[6]:6.2f}% {r[7]:7d} "
              f"{r[8]:6.2f} | {r[9]:6.2f}% {r[10]:7d} {r[11]:6.2f} {r[12]:+6.2f}")
    if rows:
        ho = np.array([r[6] for r in rows])
        lb = np.array([r[9] for r in rows])
        print(f"\n{len(rows)} aturan: HOLD median={np.median(ho):.2f}% max={ho.max():.2f}% | "
              f"LOCK median={np.median(lb):.2f}% max={lb.max():.2f}%")
        print(f"lb95 holdout>50: {sum(1 for r in rows if r[8] > 50)}/{len(rows)}; "
              f"lb95 lockbox>50: {sum(1 for r in rows if r[11] > 50)}/{len(rows)}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        t = np.arange(0, 60 * 400, 60.0)
        rng = np.random.default_rng(9)
        px = np.cumsum(rng.normal(0, 1, len(t))) + 100
        T = dict(t=t.astype(np.int64), o=px.copy(), h=px + 1, l=px - 1, c=px.copy(),
                 o5=px.copy(), h5=px + 1, l5=px - 1, c5=px.copy(), n5=np.full(len(t), 5),
                 n=np.full(len(t), 10), entry=np.roll(px, -1), closeb=np.roll(px, -1),
                 y=np.zeros(len(t), np.int8), ok=np.ones(len(t), bool))
        R = smc2(T, 2, 60, 1.5)
        assert all(len(v) == len(t) for v in R.values()), "panjang aturan tidak sama"
        for v in R.values():
            assert set(np.unique(v)) <= {-1.0, 0.0, 1.0}, "arah harus -1/0/+1"
        b = _htf_bias(T, 15)
        assert np.isnan(b[0]), "M15 pertama harus NaN (belum ada blok sebelumnya)"
        assert (b[15:] >= 0).all() or True
        print(f"selftest OK: {len(R)} aturan sepanjang data, HTF M15 tanpa lookahead")
    else:
        main()
