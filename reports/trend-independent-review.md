Audit selesai. Tidak menjalankan skrip, tidak akses kredensial/jaringan broker.

Temuan:

1. **Data masa depan — `backtest37_cheat.py:103`**  
   `np.convolve(..., mode="same")` menghitung rata-rata body memakai candle sebelum dan sesudah candle keputusan. `strong_mom()` memakai nilai masa depan untuk sinyal candle saat ini.

2. **Data masa depan — `backtest41_ml_knn.py:12`**  
   Volatilitas `sd` memakai `np.convolve(..., "same")`; normalisasi fitur candle awal memakai return masa depan. Model menerima fitur yang belum tersedia saat entry.

3. **Data masa depan lintas split — `backtest43_anchor.py:20`**  
   `dev.std()` dihitung dari seluruh rangkaian, termasuk holdout dan lockbox. Ambang z-score memakai informasi evaluasi.

4. **Timing entry salah — `backtest45_regime.py:33-40`**  
   Label `y55` memakai harga tick detik 55, tetapi arah `d` dihitung dari `c`, ADX, EMA, `h`, dan `l` candle penuh. Sinyal baru diketahui setelah candle selesai, sehingga tidak bisa dieksekusi pada T-5.

5. **Label entry tidak konsisten — `backtest45_regime.py:32-34`**  
   `ynext` mengukur `close[next] - open[next]`, sedangkan varian T-5 mengukur `close[next] - p55[current]`. Dua label menguji entry price berbeda, tetapi hasil dibandingkan dalam tabel strategi yang sama.

6. **Penyebut menang tercemar tie — `backtest37_cheat.py:31`, `55-59`**  
   `y == 0` dihitung sebagai kalah karena tetap masuk denominator. Untuk metrik per trade, tie harus dikeluarkan atau diberi aturan payout eksplisit.

7. **Penyebut menang tercemar tie — `backtest39_tick_reversal.py:23-26`**  
   `win = y == d`, tetapi `y == 0` tetap masuk `m.sum()`. `draw` hanya dilaporkan, tidak dikeluarkan dari win-rate.

8. **Parameter memakai masa depan — `backtest39_tick_reversal.py:13-20`**  
   `med` dihitung dari semua tick, termasuk periode holdout. Threshold `k*med` bukan parameter yang tersedia secara kausal pada awal holdout.

Kriteria runnable untuk eksperimen TREND baru tanpa batas T-5:

1. **Kausalitas entry**  
   Untuk setiap trade pada candle `i`, semua fitur hanya boleh memakai data `<= close[i]`; entry harus eksplisit `open[i+1]` atau tick pertama setelah keputusan. Tambahkan assert bahwa tidak ada indeks fitur `> i`.

2. **Split dan kalibrasi temporal**  
   Train hari 0–5, holdout hari 6, lockbox hari 7+. Semua threshold, normalisasi, kuantil, dan parameter hanya dihitung dari train. Eksperimen lulus hanya bila holdout dan lockbox masing-masing memiliki minimal 200 trade.

3. **Target 80% per trade**  
   Hitung `wins / (wins + losses)` setelah tie didefinisikan konsisten dan tidak diam-diam masuk denominator. Lulus hanya jika win-rate holdout dan lockbox ≥80%, dengan minimal 200 trade per split; laporkan juga interval Wilson 95%, bukan win-rate sesi gabungan.