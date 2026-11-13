// Verifikasi FVG kanonik: arah, syarat candle tengah, ambang ATR, umur, anti-repaint.
var ok = true;
void Check(string name, bool pass, string detail = "")
{
    ok &= pass;
    Console.WriteLine($"  {(pass ? "OK  " : "GAGAL")} {name}{(detail.Length > 0 ? " — " + detail : "")}");
}

static double Atr(IReadOnlyList<Candle> c, int p)
{
    if (c.Count < 2) return 0;
    var sum = 0.0; var n = 0;
    for (var i = 1; i < c.Count; i++)
    {
        var tr = Math.Max(c[i].High - c[i].Low,
            Math.Max(Math.Abs(c[i].High - c[i - 1].Close), Math.Abs(c[i].Low - c[i - 1].Close)));
        sum += tr; n++;
    }
    return n == 0 ? 0 : sum / n;
}

static List<Gap> FindGaps(IReadOnlyList<Candle> c)
{
    var gaps = new List<Gap>();
    if (c.Count < 3) return gaps;
    var atr = Atr(c, 14); var last = c.Count - 1;
    for (var i = 2; i < c.Count; i++)
    {
        var cur = c[i]; var mid = c[i - 1]; var back = c[i - 2];
        string dir; double lo, hi;
        if (cur.Low > back.High && mid.Close > mid.Open) { lo = back.High; hi = cur.Low; dir = "BUY"; }
        else if (cur.High < back.Low && mid.Close < mid.Open) { lo = cur.High; hi = back.Low; dir = "SELL"; }
        else continue;
        if (hi <= lo) continue;
        if (atr > 0 && hi - lo < 0.10 * atr) continue;
        if (last - i > 60) continue;
        var filled = false;
        for (var j = i + 1; j < c.Count; j++)
        {
            if (dir == "BUY" && c[j].Low <= lo) { filled = true; break; }
            if (dir == "SELL" && c[j].High >= hi) { filled = true; break; }
        }
        gaps.Add(new Gap(lo, hi, dir, i, filled));
    }
    return gaps;
}

static Candle C(double o, double h, double l, double cl, double t = 0) => new(t, o, h, l, cl);

/// <summary>Salinan aturan AggregateCandles aplikasi: bucket terakhir yang belum
/// penuh dibuang supaya struktur M15/H1 tidak repaint.</summary>
static List<Candle> Aggregate(IReadOnlyList<Candle> c, int factor)
{
    var result = new List<Candle>();
    if (c.Count == 0) return result;
    var bucketSeconds = 60d * factor;
    var i = 0;
    while (i < c.Count)
    {
        var bucket = Math.Floor(c[i].Time / bucketSeconds);
        var start = i;
        while (i < c.Count && Math.Floor(c[i].Time / bucketSeconds) == bucket) i++;
        var slice = c.Skip(start).Take(i - start).ToList();
        var isTrailing = i >= c.Count;
        if (isTrailing && slice.Count < factor && result.Count > 0) continue;
        result.Add(new Candle(slice[0].Time, slice[0].Open,
            slice.Max(x => x.High), slice.Min(x => x.Low), slice[^1].Close));
    }
    return result;
}

Console.WriteLine("== ARAH FVG ==");
// Gap naik + candle tengah bullish
var bull = new List<Candle> { C(100,105,99,104), C(104,108,103,107), C(108,112,110,111) };
var gb = FindGaps(bull);
Check("celah naik + tengah bullish -> BUY", gb.Count == 1 && gb[0].Direction == "BUY", gb.Count > 0 ? $"{gb[0].Low}-{gb[0].High} {gb[0].Direction}" : "tidak ada");
Check("batas gap 105-110", gb.Count == 1 && Math.Abs(gb[0].Low - 105) < 1e-9 && Math.Abs(gb[0].High - 110) < 1e-9);

var bear = new List<Candle> { C(105,106,100,101), C(101,102,98,99), C(99,95,93,94) };
var gs = FindGaps(bear);
Check("celah turun + tengah bearish -> SELL", gs.Count == 1 && gs[0].Direction == "SELL", gs.Count > 0 ? $"{gs[0].Low}-{gs[0].High} {gs[0].Direction}" : "tidak ada");
Check("batas gap bearish 95-100", gs.Count == 1 && Math.Abs(gs[0].Low - 95) < 1e-9 && Math.Abs(gs[0].High - 100) < 1e-9);

Console.WriteLine("\n== SYARAT CANDLE TENGAH ==");
// Gap naik tapi candle tengah BEARISH -> bukan FVG valid
var wrongMid = new List<Candle> { C(100,105,99,104), C(108,108,103,104), C(108,112,110,111) };
Check("celah naik tapi tengah bearish -> ditolak", FindGaps(wrongMid).Count == 0, $"{FindGaps(wrongMid).Count} gap");

Console.WriteLine("\n== AMBANG ATR ==");
// Gap sangat tipis (0.1) dibanding ATR besar -> ditolak
var thin = new List<Candle> { C(100,105,90,104), C(104,120,103,119), C(119,121,105.1,120) };
var gt = FindGaps(thin);
Check("gap mikro < 0,10 ATR -> ditolak", gt.Count == 0, $"{gt.Count} gap");

Console.WriteLine("\n== UMUR ZONA ==");
// Gap di awal, lalu 80 candle tanpa gap -> harus kedaluwarsa
var old = new List<Candle> { C(100,105,99,104), C(104,108,103,107), C(108,112,110,111) };
for (var i = 0; i < 80; i++) { var p = 111.0 + i * 0.01; old.Add(C(p, p + 0.2, p - 0.2, p + 0.1, i + 3)); }
Check("gap lebih tua dari 60 candle -> kedaluwarsa", FindGaps(old).Count == 0, $"{FindGaps(old).Count} gap");

Console.WriteLine("\n== STATUS TERISI ==");
var filled = new List<Candle> { C(100,105,99,104), C(104,108,103,107), C(108,112,110,111), C(111,112,104,105) };
var gf = FindGaps(filled);
Check("gap ditembus -> Filled=true", gf.Count == 1 && gf[0].Filled, gf.Count > 0 ? $"filled={gf[0].Filled}" : "tidak ada");

Console.WriteLine("\n== ANTI-REPAINT ==");
var series = new List<Candle>();
var rnd = new Random(7); double px = 100;
for (var i = 0; i < 40; i++)
{
    var o = px; var cl = o + (rnd.NextDouble() - 0.5) * 4;
    var h = Math.Max(o, cl) + rnd.NextDouble() * 2; var l = Math.Min(o, cl) - rnd.NextDouble() * 2;
    series.Add(C(o, h, l, cl, i * 60)); px = cl;
}
var at20 = FindGaps(series.Take(20).ToList());
var at40 = FindGaps(series);
var stable = at20.All(o => at40.Any(n => n.Index == o.Index && n.Direction == o.Direction
    && Math.Abs(n.Low - o.Low) < 1e-9 && Math.Abs(n.High - o.High) < 1e-9));
Check("gap lama tidak berubah saat candle bertambah", stable, $"{at20.Count} -> {at40.Count}");
var retro = at40.Where(n => n.Index < 19 && !at20.Any(o => o.Index == n.Index)).ToList();
Check("tidak ada gap retroaktif", retro.Count == 0, $"{retro.Count} retro");

Console.WriteLine("\n== KEDUA ARAH BISA ADA BERSAMAAN ==");
var both = new List<Candle>
{
    C(100,101,99,100), C(100,102,99,101), C(101,103,100.5,102),
    C(102,120,101,119), C(119,121,118,120), C(120,121,100,101),
    C(101,102,80,81), C(81,82,79,80), C(80,81,60,61),
};
var gBoth = FindGaps(both);
var hasBuy = gBoth.Any(g => g.Direction == "BUY");
var hasSell = gBoth.Any(g => g.Direction == "SELL");
Check("BUY dan SELL bisa terdeteksi", hasBuy && hasSell, $"BUY={hasBuy} SELL={hasSell} total={gBoth.Count}");

Console.WriteLine("\n== FVG DISENTUH HARGA (bug lama: zona ini dibuang) ==");
// Gap BUY 105-110, harga berhenti di 107 -- DI DALAM zona. Ini setup entry paling valid.
var touch = new List<Candle> { C(100,105,99,104), C(104,108,103,107), C(108,112,110,111) };
var gTouch = FindGaps(touch).Where(g => !g.Filled).ToList();
const double priceInGap = 107.0;
var nearestTouch = gTouch.OrderBy(g => Math.Abs(priceInGap - g.Mid)).FirstOrDefault();
Check("gap yang mengandung harga tetap ditemukan", nearestTouch is not null,
    nearestTouch is null ? "tidak ada" : $"{nearestTouch.Low}-{nearestTouch.High}");
Check("harga di dalam zona terdeteksi",
    gTouch.Any(g => priceInGap >= g.Low && priceInGap <= g.High), $"harga={priceInGap}");
// Filter lama (High < price || Low > price) akan membuangnya -- buktikan bedanya.
var oldFilter = gTouch.Where(g => g.High < priceInGap || g.Low > priceInGap).ToList();
Check("filter lama memang membuang zona ini (regresi terjaga)", oldFilter.Count == 0, $"{oldFilter.Count} lolos filter lama");

Console.WriteLine("\n== AGREGASI M15 (anti-repaint bucket berjalan) ==");
var m1 = new List<Candle>();
for (var i = 0; i < 45; i++) m1.Add(C(100 + i, 101 + i, 99 + i, 100.5 + i, i * 60));
var agg40 = Aggregate(m1.Take(40).ToList(), 15);
Check("bucket terakhir belum penuh -> dibuang", agg40.Count == 2, $"{agg40.Count} bucket (harap 2)");
var agg45 = Aggregate(m1, 15);
Check("3 bucket penuh dipakai semua", agg45.Count == 3, $"{agg45.Count} bucket (harap 3)");
var aggA = Aggregate(m1.Take(30).ToList(), 15);
var aggB = Aggregate(m1.Take(37).ToList(), 15);
var stableAgg = aggA.All(x => aggB.Any(y => Math.Abs(y.Time - x.Time) < 1e-9
    && Math.Abs(y.High - x.High) < 1e-9 && Math.Abs(y.Low - x.Low) < 1e-9));
Check("bucket lama tidak berubah saat candle bertambah", stableAgg, $"{aggA.Count} -> {aggB.Count}");

Console.WriteLine(ok ? "\nSEMUA LOLOS" : "\nADA YANG GAGAL");
return ok ? 0 : 1;

sealed record Candle(double Time, double Open, double High, double Low, double Close);
sealed record Gap(double Low, double High, string Direction, int Index, bool Filled)
{
    public double Mid => (Low + High) / 2;
    public bool IsBullish => Direction == "BUY";
}
