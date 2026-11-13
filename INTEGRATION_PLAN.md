# INTEGRATION PLAN — SMC FUSION 60

> Rencana integrasi. BELUM ada implementasi. Menunggu persetujuan (PHASE 6).
> Prinsip: tambah lapisan di ATAS `AnalyzeSmcFull` yang sudah ada — jangan rebuild SMC, jangan duplikasi modul.

---

## PHASE 1–2 (selesai)
Audit + peta reuse → lihat `PROJECT_AUDIT.md`.

## PHASE 3 — Lapisan skor SMC Fusion (baru, memakai hasil SMC)

Tambah satu fungsi penilaian baru, mis. `EvaluateSmcFusion(...)`, yang **membaca** `SmcAnalysis` + hasil indikator yang sudah ada, lalu mengeluarkan dua skor (CALL & PUT) + breakdown. Tidak menghitung ulang BOS/OB/FVG/sweep.

Bobot awal (total 100):
| Komponen | Bobot | Sumber data (reuse) |
|---|---|---|
| Market Structure | 15 | `AnalyzeStructure` BOS/CHoCH + `ChochKind` |
| Liquidity Sweep | 15 | `SmcEvents` (sweep) |
| Order Block | 10 | `ObKind`, `ObLow/High` |
| FVG | 10 | `FvgLow/High`, `FvgKind` |
| Price Action | 15 | pola lanjutan (TASK 3) |
| Candlestick Pattern | 10 | `CandlePatternOf` + tambahan |
| Trend Confirmation | 10 | EMA 20/50/200 + HTF bias |
| Momentum | 5 | RSI/MACD/ATR |
| Multi-factor Confluence | 10 | `alignedCount` MTF + jumlah faktor searah |

Aturan:
- Skor dihitung dua arah; **anti double-count**: struktur & CHoCH & MTF-trend yang berkorelasi dijumlahkan lewat grup berbobot maksimum, bukan dijumlah dua kali.
- `minScore = 85`. Hanya satu arah boleh ≥85; bila keduanya ≥85 → NO TRADE.
- Skor = skor strategi internal, BUKAN probabilitas menang (label UI wajib menyatakan ini).

## PHASE 4 — Timing & Expiry

- **Timing**: evaluasi pada detik 58–59 (`SecondsLeftInCandle()` ∈ {1,2} atau offset tick). Karena tidak selalu ada tick tepat di detik 58, sinyal di-lock pada tick pertama yang valid di dalam jendela; bila tidak ada tick valid sampai close → NO TRADE (fail closed, tidak mengarang sinyal).
- **No repaint**: keputusan hanya memakai candle CLOSED + tick real-time hingga saat evaluasi. Sinyal yang sudah published terkunci (write-once) lewat `lockedSignals`.
- **Expiry**: `expiry = entryTimestamp + 60s`. Harga expiry diambil dari tick/intrabar nyata pada/ sesudah expiry; bila tidak ada tick tepat, pakai tick terakhir ≤ expiry yang valid, jika tidak ada → hasil INVALID, jangan fabrikasi. `EntryOpen` dari harga entry aktual, bukan asumsi.
- **Dedup**: kunci (symbol, signalTime, strategy) — sudah ada polanya di `firedStrategies`/`demoOrdersSent`.
- **Record**: Signal ID, market, arah, skor, signal timestamp, entry timestamp, entry price, expiry timestamp, expiry price, outcome.

## PHASE 5 — Integrasi UI (pertahankan antarmuka)
- Panel **DX Confidence** → tampilkan CALL Score, PUT Score, total, dan breakdown komponen (reuse `UpdateSmcDashboard`, `SmcScoreText`).
- Panel **SIGNAL** → CALL/PUT/NO TRADE + countdown + entry price + expiry + skor (reuse `SetSignal`).
- **WHY THIS SIGNAL** → alasan nyata dari komponen yang menyala (reuse `SmcWhyText`).
- Tidak mengubah scanner, chart, balance, notifikasi, jurnal, SL/SH lock.

## PHASE 6 — PERSETUJUAN (titi k henti)
Menunggu persetujuan sebelum menulis kode.

## PHASE 7 — Implementasi bertahap (setelah disetujui)
1. Tambah `EvaluateSmcFusion` + breakdown, tanpa mengubah `AnalyzeSmcFull`.
2. Tambah pola price action yang hilang dengan definisi objektif.
3. Tambah konfirmasi EMA20/50/200 + MACD (configurable).
4. Tambah timing 58–59 + expiry tick-accurate + Signal ID.
5. Upgrade DX panel + SIGNAL panel + WHY.
6. Migrasi jurnal (kolom baru) mengikuti pola `EnsureJournalSchema`, tanpa merusak data lama.

## PHASE 8 — Validasi
- Backtest pada tick tersimpan; pisahkan development / validation / out-of-sample.
- Metrik: total sinyal, CALL winrate, PUT winrate, gabungan, expectancy, max losing streak, by score-range, by market, OTC vs LIVE, by session, by regime.
- Uji non-repaint & no-lookahead (pola `VerifyNonRepaintStructure`).
- Tidak melaporkan profit sebelum terverifikasi.

## Keputusan yang perlu dikonfirmasi sebelum PHASE 7
1. Apakah `minScore=85` final, atau boleh dikonfigurasi di UI?
2. Expiry harga: pakai tick terakhir ≤ expiry (direkomendasikan) atau tick pertama ≥ expiry?
3. Apakah DEMO order otomatis tetap menyala untuk sinyal Fusion, atau DATA ONLY (tanpa order) sesuai "Preserve existing DATA ONLY operation"?
