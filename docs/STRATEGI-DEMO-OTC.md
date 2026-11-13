# Rancangan Strategi Binary Options — Pocket Option DEMO (Data-Only)

**Status:** rancangan riset; belum aktif sebagai rekomendasi atau eksekusi order  
**Instrumen:** candle OTC M1 yang tersedia dari feed Pocket Option DEMO  
**Tujuan:** menguji sinyal secara terpisah dan transparan, bukan menjanjikan winrate atau profit.

> Tidak ada strategi yang dapat menjamin winrate 100%. Bertindak “seperti bandar” atau mengaku mengetahui algoritma internal broker bukan metode yang dapat diuji. Rancangan ini tidak mengirim order, tidak mengubah akun, dan tidak menyarankan penggunaan akun REAL.

## Temuan audit yang menjadi dasar

Audit internal `AUDIT-STRATEGI.md` menggunakan data 13–20 September 2026: sekitar 8 hari, 357.649 candle, dan 142 simbol. Temuannya:

- Baseline arah candle naik sekitar **50,04%**.
- Z-score + filter volatilitas tinggi menghasilkan **50,61%** pada holdout (11.723 sampel); batas bawah CI 95% **49,7%**. Belum terbukti mengalahkan acak dan belum cukup untuk menyatakan profit.
- RSI ekstrem 20/80 menghasilkan **50,75%** pada fold harian; hanya **4 dari 8 hari** di atas 50%. Hasilnya belum stabil.
- 100% pada LOOK-AHEAD hanya tercapai karena membaca candle hasil sebelum candle itu tersedia. Itu bukan strategi live dan tidak boleh dipakai sebagai target.

Rentang data singkat dan sudah berumur; hasil di atas hanya titik awal riset, bukan jaminan kondisi feed sekarang.

## Strategi A — Z-Score + Volatilitas (kandidat utama untuk paper test)

### Hipotesis

Deviasi harga M1 yang ekstrem mungkin diikuti pembalikan lebih sering saat aktivitas/range tidak sedang sepi. Audit menunjukkan kandidat ini paling konsisten di antara yang diuji, tetapi belum membuktikan edge positif.

### Definisi

Gunakan candle M1 yang sudah selesai, tanpa memakai data candle entry/masa depan.

- `mean20`: rata-rata close 20 candle terakhir.
- `step20`: rata-rata nilai absolut perubahan close pada 20 langkah terakhir.
- `z = (close_A - mean20) / step20`.
- `range_A = high_A - low_A`.
- `medianRange60`: median range dari 60 candle sebelum candle A.

### Syarat sebelum sinyal

Semua harus lolos:

1. Feed berstatus `live`, simbol ada di `liveSymbols`, dan tick/candle valid serta segar.
2. Tersedia minimal 60 candle M1 untuk baseline volatilitas dan minimal 26 candle untuk kalkulasi sinyal.
3. `step20 > 0` dan `medianRange60 > 0`.
4. `range_A > medianRange60` — pasar tidak dalam rezim volatilitas rendah.
5. Cooldown minimal 5 candle M1 sejak sinyal strategi A terakhir pada simbol tersebut.

Jika salah satu tidak terpenuhi: **NO TRADE / SKIP**, bukan menebak.

### Arah dan waktu

- Jika `z <= -2`: catat kandidat **CALL**.
- Jika `z >= +2`: catat kandidat **PUT**.
- Selain itu: **NO SETUP**.
- Kunci sinyal hanya dari candle A yang sudah selesai/tersedia. Evaluasi historis tidak boleh membaca candle B.
- Untuk paper test, evaluasi candle M1 berikutnya (B) sebagai expiry 1 candle. Hasil dicatat sebagai WIN/LOSS/DRAW berdasarkan aturan hasil binary yang digunakan; payout aktual harus dicatat terpisah.

### Pembatalan / skip

Jangan keluarkan kandidat jika feed stale, market tidak live, OHLC malformed, candle datar/tidak valid, syarat volatilitas gagal, atau cooldown masih berlaku. Jangan mengganti data hilang dengan harga buatan.

## Strategi B — RSI Ekstrem 20/80 (pembanding terpisah)

- Hitung RSI pada candle M1 yang sudah selesai.
- `RSI < 20` → kandidat **CALL**.
- `RSI > 80` → kandidat **PUT**.
- Nilai di antaranya → **NO SETUP**.
- Evaluasi pada candle berikutnya, tanpa look-ahead.
- Jangan gabungkan dengan Strategi A dulu. Audit internal menunjukkan bahwa menambah persetujuan/konfluensi antar-sinyal tidak meningkatkan hasil secara konsisten.

**Catatan implementasi:** tes RSI yang ada menghitung Wilder RSI, sedangkan helper RSI C# perlu dipastikan memakai definisi yang sama. Samakan implementasi dan tes sebelum angka RSI aplikasi dipercaya.

## Kandidat lain yang tidak diprioritaskan

Trend/EMA, Bollinger re-entry, S/R rejection, breakout, dan SMC boleh dicatat sebagai fitur analisis chart, tetapi belum ada bukti holdout yang cukup untuk menyebutnya lebih akurat atau menguntungkan pada feed ini. Jangan menambah bobot atau menggabungkannya hanya agar winrate historis tampak tinggi.

## Aturan paper test dan manajemen risiko

1. Gunakan **paper trading/demo data-only**; tidak ada order yang dikirim ke broker.
2. Catat setiap sinyal, termasuk yang kalah dan DRAW, dengan waktu, simbol, strategi, z/RSI, range regime, expiry, status feed, outcome, dan payout yang tersedia.
3. Pisahkan statistik per strategi, simbol, hari, serta rezim. Jangan menghapus simbol atau jam yang hasilnya buruk setelah melihat hasil test.
4. Gunakan satuan stake simulasi tetap; **jangan Martingale**, jangan menggandakan stake setelah kalah, dan jangan mengejar kerugian.
5. Tampilkan jumlah sampel dan interval kepercayaan Wilson, bukan winrate saja.
6. Bekukan aturan terlebih dahulu. Gunakan walk-forward kronologis per hari; semua threshold dan median dihitung dari data yang sudah tersedia saat itu. Tidak boleh memilih parameter berdasarkan holdout.

### Kriteria untuk tetap berstatus riset

- Minimal 500 outcome out-of-sample dan setidaknya 30 hari pengamatan untuk kandidat sebelum penilaian awal; data proyek saat ini belum memenuhi rentang 30 hari.
- Laporkan payout dan break-even. Untuk payout bersih 80%, break-even win probability adalah sekitar `1 / (1 + 0,80) = 55,56%` (DRAW diperlakukan sesuai aturan platform).
- Jangan menyebut kandidat punya edge sampai batas bawah CI 95% out-of-sample melampaui break-even payout setelah semua biaya/aturan seri diperhitungkan, dan hasilnya tidak terkonsentrasi pada satu hari/simbol.
- Jika syarat tidak lolos, pertahankan sebagai indikator riset atau pensiunkan; jangan menaikkan stake.

## Status di aplikasi saat ini

Strategi-strategi yang ada tersedia sebagai logika/opsi indikator chart. Namun, pemeriksaan kode menemukan `UpdateEntrySignal()` belum dipanggil dari alur refresh feed; karena itu rancangan ini **belum aktif sebagai sinyal real-time**. Perubahan agar panel SIGNAL menjalankan strategi perlu menjadi langkah implementasi terpisah, dengan tes tidak-look-ahead dan data-only. Tidak ada bagian dokumen ini yang mengaktifkan order atau mengubah koneksi broker.
