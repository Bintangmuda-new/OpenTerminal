# PROJECT AUDIT — OpenTerminal BMF / SMC Engine

> Audit kode sumber saja. Tidak ada perubahan kode pada fase ini.
> Semua baris merujuk `desktop/MainWindow.xaml.cs` kecuali disebut lain.

---

## 1. Peta modul yang sudah ada (REUSE, jangan tulis ulang)

| Fitur | Fungsi / record | Lokasi | Status |
|---|---|---|---|
| BOS / CHoCH struktur | `AnalyzeStructure(candles, closedCount)` | ~2890 | Ada, pivot 3-candle |
| CHoCH kanonik (aturan SMC aktif) | `AnalyzeSmcFull` (blok CHoCH) | 1486–1517 | Ada, satu-satunya aturan yang lolos backtest |
| Swing higher low / lower high | loop pivot di `AnalyzeSmcFull` | 1486–1505 | Ada |
| HTF Bias (M15 + H1 dari M1) | `AggregateCandles` + bias di `AnalyzeSmcFull` | 1383–1420 | Ada, agregasi M1→M15/H1 |
| Premium / Discount zone | blok `zone` di `AnalyzeSmcFull` | 1421–1433 | Ada, dari 6 swing M15 terakhir |
| Order Block (SUPPLY/DEMAND) | blok `obLow/obHigh` di `AnalyzeSmcFull` | 1435–1460 | Ada, berbasis swing M15 |
| Fair Value Gap (3-candle) | `NearestFvg` | 1394–1408 | Ada, gap index vs index-2 |
| Liquidity Sweep | `SmcEvents` dari `AnalyzeStructure` (dipakai di 1463–1465, 2548) | ~2890+ | Ada |
| Multi-timeframe alignment | `UpdateSmcDashboard` (M1/M5/M15/M30/H1/H4/D1) | 2686–2698 | Ada |
| DX / Structure Confidence Score | `score` di `AnalyzeSmcFull` + `SmcScoreText` | 1466–1475, 2663 | Ada, skor 0–100 (max(bull,bear)×20) |
| Market Regime (ADX + persentil range) | `SmcRegimeSnapshot` | 2738 | Ada |
| ADX (Wilder 14) | `Adx` | 1998 | Ada |
| EMA | `Ema` | dipakai banyak | Ada |
| RSI (Wilder 14) | `Rsi` | dipakai banyak | Ada |
| ATR | `Atr` | 1507 dst | Ada |
| Candlestick: Engulfing/Doji/Marubozu/Hammer/ShootingStar | `CandlePatternOf` | 1274–1297 | Ada, sebagian |
| Chart pattern (double top/bottom, triangle) | `ChartPatternOf` | 1301 | Ada |
| Momentum (EMA9/21 + RSI + ATR) | `UpdateMomentumSignal` | 1233 | Ada |
| PREPARE CALL/PUT alerts | `UpdateNextSignals` + `nextAlertDedup` | 655–762 | Ada, T-15s Z-Score |
| Signal lock (write-once) | `ApplySignal` + `lockedSignals` | 3464–3504 | Ada, T-5s |
| Entry = candle B, result close | `TryGetClosedEntryCandle`, `OutcomeForDirection` | 3626–3648 | Ada |
| Expiry 60s (DEMO order) | `SubmitDueDemoOrders` + `PlaceDemoOrderAsync` | 3506–3558 | Ada, `expiration=60` |
| Signal Journal (CSV) | `AppendJournal` + `EnsureJournalSchema` | 3660–3694 | Ada, 8 kolom |
| Win Rate panel | `UpdateResultPanel` | 3719 | Ada |
| Market Scanner / watchlist | `UpdateMarkets`, `WatchlistSymbols` | 554–647 | Ada |
| Chart overlay SMC | `DrawSmcOverlay`, `DrawSmcMarkers` | 2492–2609 | Ada |
| SMC dashboard (DX panel) | `UpdateSmcDashboard` | 2653–2736 | Ada |
| RSI divergence | `DetectRsiDivergence` | 2628 | Ada |
| Tick history (no synthetic) | `RecordTick` / `tickHistory` | 95–96 | Ada |

---

## 2. Detail perhitungan (sesuai TASK 1)

### 2.1 BOS & CHoCH — `AnalyzeStructure` (~2890)
- Pivot high: `candles[pivot].High > kedua tetangga`; pivot low sebaliknya (3-candle, butuh 1 candle kanan → tidak repaint).
- `lastHigh`/`lastLow` disimpan saat pivot terkonfirmasi.
- BOS: close menembus `lastHigh` searah tren; CHoCH: close menembus lawan tren. Event disimpan di `StrategyEvent`.

### 2.2 CHoCH kanonik SMC — `AnalyzeSmcFull` (1486–1517)
- Higher low = swing low > swing low sebelumnya; lower high = swing high < sebelumnya.
- BEARISH bila `closeA < higherLow`; BULLISH bila `closeA > lowerHigh`.
- Ini satu-satunya aturan yang dipakai `HistoricalDirection(ActiveStrategy.Smc)` (1984–1989).
- Catatan backtest di kode: 42 jt tick / 142 simbol, 51.14% (lihat AUDIT-STRATEGI.md).

### 2.3 Liquidity Sweep (1463–1465, 2548)
- Memakai `SmcEvents` terakhir dari `AnalyzeStructure` pada M15; dianggap "baru" bila ≤6 candle M15.
- `sweep.Direction` dipetakan ke +1/-1 untuk skor.

### 2.4 Order Block (1435–1460)
- Bearish candle terakhir + swing high M15 ≥ high → SUPPLY; bullish + swing low ≤ low → DEMAND.
- Rentang OB = low/high candle swing, diperlebar 1 candle berikutnya.

### 2.5 FVG — `NearestFvg` (1394–1408)
- Gap naik: `candles[i].Low > candles[i-2].High`; gap turun sebaliknya. Zona = [high i-2, low i].
- Dipilih yang terdekat ke harga; `nearFvg` bila jarak ≤15 unit (unit = price×1e-5).

### 2.6 Premium / Discount (1421–1433)
- Dari 6 swing M15 terakhir: rangeHigh/rangeLow. `position=(price-low)/span`.
- ≥0.5 → PREMIUM, <0.5 → DISCOUNT. Garis EQ 50% digambar di overlay (2528).

### 2.7 HTF Bias (1414–1420)
- M15 dan H1 diagregasi dari M1 yang sama (`AggregateCandles`).
- BULLISH bila H1 UP (atau H1 RANGE + M15 UP); BEARISH cermin; selain itu NETRAL.

### 2.8 MTF Alignment (2686–2698)
- Frame M1/M5/M15/M30/H1/H4/D1 dari M1; tiap frame dihitung `AnalyzeStructure.Trend`.
- `alignedCount` = jumlah frame searah bias.

### 2.9 DX / Structure Confidence Score (1466–1475)
- 5 arah: bias, sweep, OB, nearFvg, bos → masing +1/-1/0.
- `bullish`/`bearish` = hitungan; `score = max(bullish,bearish) × 20` (0–100).
- Ini skor konfluensi, BUKAN probabilitas menang. Panel: `SmcScoreText` (2663).

### 2.10 PREPARE alerts (655–762)
- Watchlist = favorites + simbol aktif (≤10). Z-Score + vol HI.
- T-15s → "PREPARE · T-{remaining}s"; dedup per (symbol,time,direction).
- `nextSignalsRebuilding` mencegah auto-switch saat rebuild.

### 2.11 Entry & Expiry (3464–3648)
- Lock write-once di `ApplySignal` saat `remaining ≤ SignalLockSeconds (5)` dan ≥1s.
- Entry = candle B (`signalTime+60`); result dari open/close candle B (`OutcomeForDirection`).
- DEMO order: `expiration=60` detik, sekali per sinyal (`demoOrdersSent`), tanpa auto-retry.
- Jurnal CSV: `strategy,signal_time,entry_time,symbol,direction,open,close,outcome`.

---

## 3. Gap terhadap target SMC FUSION 60

| Target | Status sekarang | Gap |
|---|---|---|
| Skor 9 komponen berbobot (100) | Skor 5 arah ×20 | Perlu lapisan skor baru di ATAS hasil SMC, bukan menulis ulang |
| Evaluasi detik 58–59 | Lock di T-5..T-1 (detik 55–59) | Perlu timing engine khusus 58–59 + toleransi tick hilang |
| Minimum skor 85 | Tidak ada ambang | Perlu threshold + aturan NO TRADE bila dua arah lolos |
| CALL & PUT score terpisah | Hanya satu skor max | Perlu skor dua arah |
| Pin Bar / Rejection / Inside Bar / Breakout / Failed Breakout / Momentum Candle / S-R Reaction | Sebagian (Engulfing, Hammer, ShootingStar, S/R) | Perlu definisi objektif tambahan |
| EMA 20/50/200 + MACD | EMA generik ada; MACD belum | Perlu konfirmasi trend configurable |
| Record signal vs entry timestamp terpisah | Ada (signal_time, entry_time) | Perlu tambah expiry_timestamp + harga expiry dari tick nyata |
| Signal ID | Belum ada | Perlu ID stabil |
| WIN/LOSS/DRAW dari tick expiry | Dari close candle B | Perlu harga pada expiry (entry+60) dari tick, tanpa fabrikasi |
| Performa by score/market/session/regime, OOS split | Hanya winrate sesi | Perlu modul statistik |

## 4. Batasan yang harus dijaga
- Data hanya dari feed DEMO yang ada; tidak menambah sumber data.
- Tidak mengubah skema DB/CSV tanpa rencana migrasi (`EnsureJournalSchema` sudah punya pola migrasi legacy).
- Tidak ada lookahead: semua SMC dari candle CLOSED; sinyal 58–59 memakai tick real-time yang tersedia, bukan close masa depan.
- Tidak mengklaim profit tanpa backtest/out-of-sample.
