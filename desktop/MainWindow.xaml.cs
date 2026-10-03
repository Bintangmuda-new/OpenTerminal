using Microsoft.Data.Sqlite;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Media;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace OpenTerminal.Desktop;

public partial class MainWindow : Window
{
    private const string ApiUrl = "http://127.0.0.1:4000";
    private static readonly string Root = FindRoot();
    private readonly HttpClient http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly DispatcherTimer refreshTimer = new() { Interval = TimeSpan.FromMilliseconds(500) };
    private readonly DispatcherTimer alertTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly ObservableCollection<MarketRow> markets = [];
    private readonly List<MarketRow> allMarkets = [];
    private readonly HashSet<string> favoriteSymbols = new(StringComparer.OrdinalIgnoreCase);
    private List<Candle> sourceCandles = [];
    private List<Candle> chartCandles = [];
    private string apiKey = "";
    private string selectedSymbol = "#AAPL_otc";
    private string groupFilter = "ALL";
    private int visibleBars = 24, firstVisible;
    private bool panning;
    private System.Windows.Point panStart;
    private System.Windows.Point hoverPoint;
    private bool hasHover;
    private int panStartFirst;
    private bool loading;
    private bool refreshQueued;
    private DateTime nextNewsRefresh = DateTime.MinValue;
    private DateTime nextMarketRefresh = DateTime.MinValue;
    private readonly ObservableCollection<ChartAlertRow> chartAlerts = [];
    private readonly HashSet<string> alertDedup = [];
    private double brokerOffset;
    private double lastTickTime;
    /// Locked entries keyed by candle time. Once written, never revised — that is
    /// what makes the arrow non-repainting even though it fires before the close.
    private readonly Dictionary<double, string> firedEntries = []; // legacy alias: first direction per candle time (arrow drawing)
    /// Cache SL/SH + S/R yang sudah final per candle time. Write-once: sekali
    /// dihitung saat close candle A, tidak pernah disentuh lagi. Inilah yang
    /// membuat strategi SL/SH fix benar-benar non-repaint.
    private readonly Dictionary<double, Strategy2Snapshot> strategy2Snapshot = [];
    private readonly Dictionary<(string Symbol, int Minutes, double Time), StructureMark> lockedStructureMarks = [];
    private readonly struct Strategy2Snapshot
    {
        public readonly double CloseA, SL, SH, Support, Resistance;
        public readonly string Pattern;
        public Strategy2Snapshot(double closeA, double sl, double sh, double support, double resistance, string pattern)
        { CloseA = closeA; SL = sl; SH = sh; Support = support; Resistance = resistance; Pattern = pattern; }
    }
    private static readonly string JournalPath = System.IO.Path.Combine(Root, "data", "signal-journal.csv");
    private static readonly string LegacyStructureMarksPath = System.IO.Path.Combine(Root, "data", "structure-marks.json");
    private readonly SlShStore slShStore = new(System.IO.Path.Combine(Root, "data"));

    /// <summary>Strategi yang sedang menyala. Dipakai untuk evaluator + visibility widget.</summary>
    private enum ActiveStrategy { Off, ZScore, EmaCross, RsiReversal, StructureBreak, Trend, MomentumReversal, BollingerReentry, SrRejection, VolatilityBreakout }
    /// <summary>Keyed by (strategy, signalTime) sehingga tiap strategi menulis jurnal sendiri.</summary>
    private readonly Dictionary<(ActiveStrategy Strategy, double SignalTime), (string Direction, double EntryOpen, double EntryClose, string Outcome)> signalEntries = [];
    private readonly Dictionary<(ActiveStrategy Strategy, double SignalTime), string> firedStrategies = [];

    /// Broker clock runs ahead of this machine's, so candle boundaries must be
    /// derived from feed tick time. A 1M entry needs this exact, not close.
    private long BrokerNow() => DateTimeOffset.UtcNow.ToUnixTimeSeconds() + (long)brokerOffset;

    private int SecondsLeftInCandle()
    {
        var duration = TimeframeMinutes() * 60;
        return (int)Math.Max(0, duration - BrokerNow() % duration);
    }
    private bool chartAlertsReady;
    private const string AlertSoundPath = @"Z:\MUSIC\Notifikasi\Bell Dor.wav";
    /// Minimum candles between signals. Matches the cooldown the rule was
    /// backtested with; changing it here invalidates that measurement.
    private const int SignalCooldownBars = 5;
    private static readonly bool LegacyAnalysisEnabled = true;

    public MainWindow()
    {
        InitializeComponent();
        LoadFavorites();
        LoadStructureMarks();
#if DEBUG
        VerifyNonRepaintStructure();
#endif
        UpdateStrategyControls();
        HideLegacyAnalysisControls();
        MarketList.ItemsSource = markets;
        ChartAlertList.ItemsSource = chartAlerts;
        refreshTimer.Tick += async (_, _) => await RefreshFeed();
        alertTimer.Tick += (_, _) => { alertTimer.Stop(); BottomAlertBorder.Visibility = Visibility.Collapsed; };
        Loaded += async (_, _) => await StartAsync();
    }

    private static string FindRoot()
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
            if (File.Exists(System.IO.Path.Combine(directory.FullName, "Jalankan-API.cmd"))) return directory.FullName;
        return AppContext.BaseDirectory;
    }

    private async Task StartAsync()
    {
        try
        {
            var keyFile = System.IO.Path.Combine(Root, "data", ".api-key");
            apiKey = File.Exists(keyFile) ? await File.ReadAllTextAsync(keyFile) : "";
            if (!await Online()) StartApi();
            for (var attempt = 0; attempt < 20 && !await Online(); attempt++) await Task.Delay(750);
            if (File.Exists(keyFile)) apiKey = await File.ReadAllTextAsync(keyFile);
            await RefreshFeed();
            refreshTimer.Start();
        }
        catch (Exception error) { SetStatus("disconnected", error.Message); }
    }

    private void StartApi()
    {
        var server = System.IO.Path.Combine(Root, "server", "dist", "index.js");
        if (!File.Exists(server)) return;
        var node = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "nodejs", "node.exe");
        if (!File.Exists(node)) node = "node.exe";
        var process = Process.Start(new ProcessStartInfo(node, $"\"{server}\"")
        {
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        });
        if (process is null) return;
        process.OutputDataReceived += (_, _) => { };
        process.ErrorDataReceived += (_, _) => { };
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    private async Task<bool> Online()
    {
        try { return (await http.GetAsync($"{ApiUrl}/api/status")).IsSuccessStatusCode; }
        catch { return false; }
    }

    private async Task<JsonDocument> GetPocket(string path)
    {
        using var request = new HttpRequestMessage(HttpMethod.Get, ApiUrl + path);
        request.Headers.Add("x-api-key", apiKey.Trim());
        using var response = await http.SendAsync(request);
        var body = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode) throw new InvalidOperationException($"{response.StatusCode}: {body}");
        return JsonDocument.Parse(body);
    }

    private async Task RefreshFeed()
    {
        if (string.IsNullOrWhiteSpace(apiKey)) return;
        if (loading) { refreshQueued = true; return; }
        loading = true;
        var started = DateTime.UtcNow;
        try
        {
            var requestedSymbol = selectedSymbol;
            using var document = await GetPocket($"/api/pocket/snapshot?symbol={Uri.EscapeDataString(requestedSymbol)}");
            if (requestedSymbol != selectedSymbol) { refreshQueued = true; return; }
            var feed = document.RootElement;
            var status = Text(feed, "status");
            var receivedAt = feed.TryGetProperty("receivedAt", out var received) && received.ValueKind == JsonValueKind.Number ? DateTimeOffset.FromUnixTimeMilliseconds(received.GetInt64()).LocalDateTime : DateTime.MinValue;
            SetStatus(status, $"Market feed · {feed.GetArrayLengthSafe("markets")} market · update {receivedAt:HH:mm:ss} · {(int)(DateTime.UtcNow - started).TotalMilliseconds}ms");
            if (DateTime.UtcNow >= nextMarketRefresh)
            {
                UpdateMarkets(feed);
                UpdateTape(feed);
                nextMarketRefresh = DateTime.UtcNow.AddSeconds(5);
            }
            await UpdateSelected(feed);
            if (DateTime.UtcNow >= nextNewsRefresh) _ = RefreshNews();
        }
        catch (Exception error) { SetStatus("disconnected", error.Message); }
        finally
        {
            loading = false;
            if (refreshQueued)
            {
                refreshQueued = false;
                await RefreshFeed();
            }
        }
    }

    private void UpdateMarkets(JsonElement feed)
    {
        var ticks = feed.TryGetProperty("ticks", out var t) ? t : default;
        var live = feed.TryGetProperty("liveSymbols", out var l) ? l.EnumerateArray().Select(value => value.GetString() ?? "").ToHashSet() : [];
        allMarkets.Clear();
        if (feed.TryGetProperty("markets", out var marketList))
        {
            foreach (var market in marketList.EnumerateArray())
            {
                var symbol = Text(market, "symbol");
                var price = ticks.ValueKind == JsonValueKind.Object && ticks.TryGetProperty(symbol, out var tick) ? Number(tick, "price") : null;
                var trend = feed.TryGetProperty("trends", out var trendMap) && trendMap.TryGetProperty(symbol, out var trendValue) ? Text(trendValue, "direction") : "NOTREND";
                allMarkets.Add(new MarketRow(symbol, Text(market, "name"), Text(market, "group"), FormatPrice(price), live.Contains(symbol) ? "CONNECTED" : "WAITING", trend, favoriteSymbols.Contains(symbol)));
            }
        }
        ApplyMarketFilter();
    }

    private async Task UpdateSelected(JsonElement feed)
    {
        var selected = allMarkets.FirstOrDefault(row => row.Symbol == selectedSymbol);
        SelectedSymbolText.Text = selected?.Symbol ?? selectedSymbol;
        SelectedNameText.Text = selected?.Name ?? "Broker market";
        var latestCandles = new List<Candle>();
        if (feed.TryGetProperty("candles", out var candleMap) && candleMap.TryGetProperty(selectedSymbol, out var candles))
            latestCandles = candles.EnumerateArray().Select(candle => new Candle(Number(candle, "time") ?? 0, Number(candle, "open") ?? 0, Number(candle, "high") ?? 0, Number(candle, "low") ?? 0, Number(candle, "close") ?? 0)).Where(candle => candle.Time > 0 && candle.Close > 0).ToList();
        if (latestCandles.Count > 0 && feed.TryGetProperty("ticks", out var tickMap) && tickMap.TryGetProperty(selectedSymbol, out var tick))
        {
            var price = Number(tick, "price");
            var tickTime = Number(tick, "time");
            if (price is > 0 && tickTime is > 0)
            {
                brokerOffset = tickTime.Value - DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                lastTickTime = tickTime.Value;
                var candleTime = Math.Floor(tickTime.Value / 60) * 60;
                var current = latestCandles[^1];
                if (candleTime > current.Time)
                    latestCandles.Add(new Candle(candleTime, price.Value, price.Value, price.Value, price.Value));
                else if (candleTime == current.Time)
                    latestCandles[^1] = current with { High = Math.Max(current.High, price.Value), Low = Math.Min(current.Low, price.Value), Close = price.Value };
            }
        }
        if (latestCandles.Count < 2)
        {
var closed = !feed.TryGetProperty("ticks", out var ticks) || !ticks.TryGetProperty(selectedSymbol, out _);
            PriceChart.Visibility = Visibility.Collapsed;
            QuoteText.Text = closed ? "MARKET\nCLOSED" : "MEMUAT\nCandle M1 belum tersedia";
            ChartLoadingText.Text = closed ? "MARKET CLOSED" : "LOADING MARKET...";
            ChartLoadingText.Foreground = (Brush)new BrushConverter().ConvertFromString(closed ? "#F26D78" : "#8492A6")!;
            ChartInfoText.Text = $"{selectedSymbol} · {(closed ? "market closed" : "memuat candle")}";
            ChartStatusOverlay.Visibility = Visibility.Visible;
            return;
        }
        sourceCandles = latestCandles;
        chartCandles = Aggregate(sourceCandles, TimeframeMinutes());
        LockSlShMarks();
        if (AutoEndBox.IsChecked == true || firstVisible + visibleBars >= chartCandles.Count - 2) firstVisible = Math.Max(0, chartCandles.Count - visibleBars);
        var last = chartCandles.LastOrDefault();
        var first = chartCandles.FirstOrDefault();
        var change = first is not null && last is not null ? (last.Close / first.Open - 1) * 100 : 0;
        QuoteText.Text = last is null ? "WAITING" : FormatPrice(last.Close);
        UpdateLiveCandle(last, change);
        UpdateBinaryMetrics(last);
        UpdateMarketQuality(feed, last);
        FeedText.Text = $"MODE      {Text(feed, "mode")}\nACCESS    DATA ONLY\nSTATUS    {Text(feed, "status").ToUpperInvariant()}\nMARKETS   {feed.GetArrayLengthSafe("markets")}\nLIVE      {feed.GetArrayLengthSafe("liveSymbols")}\nCANDLE    M1 persisted\n\nBroker data is read-only.\nOrders are disabled.";
        PriceChart.Visibility = Visibility.Visible;
        DrawChart();
        if (PriceChart.Children.Count > 0)
        {
            ChartStatusOverlay.Visibility = Visibility.Collapsed;
        }
    }

    private void UpdateTape(JsonElement feed)
    {
        var ticks = feed.TryGetProperty("ticks", out var tickMap) ? tickMap : default;
        var tape = allMarkets.Where(row => ticks.ValueKind == JsonValueKind.Object && ticks.TryGetProperty(row.Symbol, out _)).Select(row => new { row.Symbol, row.Price, row.Trend, row.State }).OrderBy(row => row.Symbol).ToList();
        TapeGrid.ItemsSource = tape;
    }

    private async Task RefreshNews()
    {
        nextNewsRefresh = DateTime.UtcNow.AddMinutes(1);
        var searchSymbol = selectedSymbol.TrimStart('#').Replace("_otc", "", StringComparison.OrdinalIgnoreCase);
        try
        {
            using var response = await http.GetAsync($"{ApiUrl}/api/news?symbol={Uri.EscapeDataString(searchSymbol)}");
            var body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode) throw new InvalidOperationException($"HTTP {(int)response.StatusCode}");
            using var document = JsonDocument.Parse(body);
            var rows = document.RootElement.EnumerateArray().Take(12).Select(item => new { headline = Text(item, "title"), source = Text(item, "source"), time = Text(item, "time") }).ToList();
            NewsGrid.ItemsSource = rows;
            NewsStatusText.Text = $"{rows.Count} items · {searchSymbol} · refresh {DateTime.Now:HH:mm:ss}";
        }
        catch (Exception error)
        {
            NewsStatusText.Text = $"News unavailable: {error.Message}";
        }
    }

    private void ApplyMarketFilter()
    {
        var query = SearchBox.Text.Trim();
        var selected = allMarkets.Where(row => MarketMatchesCategory(row, groupFilter) && (query.Length == 0 || row.Symbol.Contains(query, StringComparison.OrdinalIgnoreCase) || row.Name.Contains(query, StringComparison.OrdinalIgnoreCase))).OrderBy(row => row.Symbol).ToList();
        markets.Clear(); foreach (var row in selected) markets.Add(row);
        MarketCountText.Text = query.Length > 0 ? $"Saran market · {selected.Count} cocok" : $"{selected.Count} market";
    }

    private static bool MarketMatchesCategory(MarketRow row, string category)
    {
        if (category == "ALL") return true;
        if (category == "FAVORITES") return row.IsFavorite;
        var group = row.Group.ToUpperInvariant();
        var symbol = row.Symbol.ToUpperInvariant();
        var name = row.Name.ToUpperInvariant();
        return category switch
        {
            "OPTION" => symbol.Contains("_OTC") || group.Contains("OTC"),
            "CURRENCIES" => group.Contains("FOREX") || group.Contains("CURRENCY") || (symbol.Contains('/') && !group.Contains("CRYPTO")),
            "CRYPTOCURRENCIES" => group.Contains("CRYPTO") || name.Contains("BITCOIN") || name.Contains("ETHEREUM"),
            "COMMODITIES" => group.Contains("COMMODIT") || new[] { "GOLD", "SILVER", "OIL", "BRENT", "NATURAL GAS", "COPPER" }.Any(name.Contains),
            "STOCKS" => group.Contains("STOCK") || group.Contains("EQUITY"),
            "INDICES" => group.Contains("INDEX") || group.Contains("INDICE"),
            _ => false,
        };
    }

    private string FavoritesPath => System.IO.Path.Combine(Root, "data", "market-favorites.txt");
    private void LoadFavorites()
    {
        try { if (File.Exists(FavoritesPath)) favoriteSymbols.UnionWith(File.ReadAllLines(FavoritesPath).Where(line => !string.IsNullOrWhiteSpace(line))); }
        catch (IOException) { }
    }
    private void SaveFavorites()
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(FavoritesPath)!);
        File.WriteAllLines(FavoritesPath, favoriteSymbols.OrderBy(symbol => symbol));
    }
    private void Favorite_Click(object sender, RoutedEventArgs e)
    {
        e.Handled = true;
        if ((sender as FrameworkElement)?.Tag is not string symbol) return;
        if (!favoriteSymbols.Add(symbol)) favoriteSymbols.Remove(symbol);
        SaveFavorites();
        for (var index = 0; index < allMarkets.Count; index++)
            if (allMarkets[index].Symbol.Equals(symbol, StringComparison.OrdinalIgnoreCase))
                allMarkets[index] = allMarkets[index] with { IsFavorite = favoriteSymbols.Contains(symbol) };
        ApplyMarketFilter();
    }

    private static List<Candle> Aggregate(List<Candle> candles, int minutes)
    {
        if (minutes == 1) return candles;
        return candles.GroupBy(candle => candle.Time / (minutes * 60) * (minutes * 60)).Select(group => new Candle(group.Key, group.First().Open, group.Max(candle => candle.High), group.Min(candle => candle.Low), group.Last().Close)).ToList();
    }

    private int TimeframeMinutes() => 1;

    private StrategySelection CurrentStrategySelection()
    {
        var enabled = EnabledStrategies().Select(StrategyName).ToList();
        return new StrategySelection(enabled.Count == 0 ? "OFF" : string.Join(", ", enabled), "NONE");
    }

    private IEnumerable<ActiveStrategy> EnabledStrategies()
    {
        if (TrendStrategyBox.IsChecked == true) yield return ActiveStrategy.Trend;
        if (MomentumStrategyBox.IsChecked == true) yield return ActiveStrategy.MomentumReversal;
        if (BollingerStrategyBox.IsChecked == true) yield return ActiveStrategy.BollingerReentry;
        if (RejectionStrategyBox.IsChecked == true) yield return ActiveStrategy.SrRejection;
        if (BreakoutStrategyBox.IsChecked == true) yield return ActiveStrategy.VolatilityBreakout;
    }

    private ActiveStrategy CurrentActiveStrategy()
    {
        return EnabledStrategies().FirstOrDefault();
    }

    private static bool StrategyOn(ActiveStrategy active, ActiveStrategy target) => active switch
    {
        ActiveStrategy.Off => false,
        ActiveStrategy.ZScore or ActiveStrategy.EmaCross or ActiveStrategy.RsiReversal or ActiveStrategy.StructureBreak => active == target,
        _ => false,
    };

    private void StrategyBox_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!IsLoaded) return;
        var active = CurrentActiveStrategy();
        var bosOn = StrategyOn(active, ActiveStrategy.ZScore);
        var any = active != ActiveStrategy.Off;
        // Overlay indikator mengikuti strategi yang menyala.
        DirectionOverlayBox.IsChecked = bosOn;
        LevelsOverlayBox.IsChecked = active == ActiveStrategy.StructureBreak;
        StructureOverlayBox.IsChecked = active == ActiveStrategy.StructureBreak;
        // Widget structure + result + AI hanya untuk mode yang ada analisa.
        var structVisible = any && active == ActiveStrategy.StructureBreak;
        var resultVisible = any;
        var aiVisible = bosOn;
        // Cari parent Grid kolom2/3 secara langsung lewat FindName? Sudah x:Name.
        // Border di kolom structure: cari lewat LogicalTree. Untuk amannya pakai IndexOf.
        if (FindName("ResultRootBorder") is Border resultBorder) resultBorder.Visibility = resultVisible ? Visibility.Visible : Visibility.Collapsed;
        if (FindName("StructureRootBorder") is Border structBorder) structBorder.Visibility = structVisible ? Visibility.Visible : Visibility.Collapsed;
        UpdateStrategyControls();
        DrawChart();
        UpdateResultPanel();
    }

    private void Indicators_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.ContextMenu is null) return;
        button.ContextMenu.PlacementTarget = button;
        button.ContextMenu.IsOpen = true;
    }

    private void UpdateStrategyControls()
    {
        // Legacy ComboBox sudah dihapus. StrategyBox adalah sumber kebenaran.
    }

    private void HideLegacyAnalysisControls()
    {
    }

    /// Shows where the running candle stands right now: direction from its own
    /// open, how far price sits from flipping that direction, and how stale the
    /// feed is. For a 1M entry the distance-to-flip is the number that decides
    /// whether a signal at 8s is solid or about to reverse.
    private void UpdateLiveCandle(Candle? last, double change)
    {
        if (last is null)
        {
            QuoteArrow.Text = "·";
            QuoteDelta.Text = "";
            QuoteFlipText.Text = "";
            QuotePressureBar.Width = 0;
            QuoteTickAge.Text = "—";
            return;
        }

        var delta = last.Close - last.Open;
        var rising = delta > 0;
        var flat = Math.Abs(delta) < double.Epsilon;
        var colour = flat ? "#E7B65A" : rising ? "#25C89A" : "#F45B69";
        var brush = (Brush)new BrushConverter().ConvertFromString(colour)!;

        QuoteArrow.Text = flat ? "=" : rising ? "▲" : "▼";
        QuoteArrow.Foreground = brush;
        QuoteText.Foreground = brush;
        QuoteDelta.Text = $"open {FormatPrice(last.Open)} · {change:+0.000;-0.000;0.000}% sesi";

        // Position of price inside the candle's own range: 0 = at the low,
        // 1 = at the high. Fills the bar from the side price is pushing toward.
        var range = last.High - last.Low;
        var position = range > 0 ? (last.Close - last.Low) / range : 0.5;
        var track = ((FrameworkElement)QuotePressureBar.Parent).ActualWidth;
        QuotePressureBar.Width = Math.Max(0, track * position);
        QuotePressureBar.Background = brush;

        // Distance back to the open is the move that would flip this candle.
        var toFlip = Math.Abs(delta);
        var unit = Math.Max(last.Close * 1e-5, 1e-9);
        var pips = toFlip / unit;
        var fragile = pips < 3;
        QuoteFlipText.Text = flat
            ? "FLAT · arah belum terbentuk"
            : $"jarak balik {pips:0.0} pip · {(fragile ? "RAPUH, mudah berbalik" : "arah cukup kuat")}"
              + $" · H/L {FormatPrice(last.High)}/{FormatPrice(last.Low)}";
        QuoteFlipText.Foreground = (Brush)new BrushConverter().ConvertFromString(fragile && !flat ? "#E7B65A" : "#9FB3CC")!;

        var age = BrokerNow() - lastTickTime;
        QuoteTickAge.Text = lastTickTime <= 0 ? "—" : $"tick {age:0}s";
        QuoteTickAge.Foreground = (Brush)new BrushConverter().ConvertFromString(age > 3 ? "#F45B69" : "#9FB3CC")!;
    }

    private void UpdateBinaryMetrics(Candle? last)
    {
        if (last is null || chartCandles.Count < 20)
        {
            StructLevels.Text = "menunggu 20 candle";
            StructReason.Text = "";
            BinaryMomentumText.Text = "WAITING\nNeed 15 candles";
            BinaryTimingText.Text = "WAITING\nNo valid candle";
            return;
        }
        var closes = chartCandles.TakeLast(20).Select(candle => candle.Close).ToArray();
        var meanX = (closes.Length - 1) / 2d;
        var midline = closes.Average();
        var slope = closes.Select((close, index) => (index - meanX) * (close - midline)).Sum() / closes.Select((_, index) => (index - meanX) * (index - meanX)).Sum();
        var threshold = Math.Max(Math.Abs(midline) * .00001, .00000001);
        var structure = AnalyzeStructure(chartCandles, ClosedBarCount(chartCandles, TimeframeMinutes()));
        var trend = structure.Trend switch { "UP" => "UPTREND", "DOWN" => "DOWNTREND", _ => last.Close > midline + threshold && slope > threshold ? "NAIK" : last.Close < midline - threshold && slope < -threshold ? "TURUN" : "RANGE" };
        var rsi = Rsi(chartCandles);
        var range = (last.High - last.Low) / last.Close * 100;
        var duration = TimeframeMinutes() * 60;
        var remaining = SecondsLeftInCandle();
        UpdateStructurePanel(last, structure, trend, slope, threshold, rsi);
        var atr = Atr(chartCandles.Take(Math.Max(1, chartCandles.Count - 1)).ToList(), 14);
        var ema9 = Ema(chartCandles.Select(candle => candle.Close), 9).LastOrDefault();
        var ema21 = Ema(chartCandles.Select(candle => candle.Close), 21).LastOrDefault();
        BinaryMomentumText.Text = $"OHLC  {FormatPrice(last.Open)} / {FormatPrice(last.High)} / {FormatPrice(last.Low)} / {FormatPrice(last.Close)}\nRSI14  {rsi:0.0} · ATR14 {FormatPrice(atr)}\nEMA 9/21 {(ema9 > ema21 ? "UP" : ema9 < ema21 ? "DOWN" : "FLAT")} · RANGE {range:0.000}%";
        BinaryTimingText.Text = $"{TimeframeLabel()} CLOSE\n{TimeSpan.FromSeconds(remaining):mm\\:ss} remaining\nBroker time basis";
    }

    /// Reads the candle's own shape. Realtime on purpose: the running candle is
    /// included, so a pattern can appear then vanish before close.
    private static string CandlePatternOf(IReadOnlyList<Candle> candles)
    {
        var c = candles[^1];
        var range = c.High - c.Low;
        if (range <= 0) return "";
        var body = Math.Abs(c.Close - c.Open);
        var upper = c.High - Math.Max(c.Close, c.Open);
        var lower = Math.Min(c.Close, c.Open) - c.Low;
        var up = c.Close > c.Open;
        if (candles.Count >= 2)
        {
            var p = candles[^2];
            var pBody = Math.Abs(p.Close - p.Open);
            if (body > pBody && pBody > 0 && up != p.Close > p.Open
                && Math.Max(c.Open, c.Close) >= Math.Max(p.Open, p.Close)
                && Math.Min(c.Open, c.Close) <= Math.Min(p.Open, p.Close))
                return up ? "Bullish Engulfing" : "Bearish Engulfing";
        }
        if (body / range < .1) return "Doji";
        if (body / range > .8) return up ? "Marubozu naik" : "Marubozu turun";
        if (lower > body * 2 && upper < body) return "Hammer";
        if (upper > body * 2 && lower < body) return "Shooting Star";
        return "";
    }

    /// Chart pattern from the last four swing points. Double top/bottom needs two
    /// same-kind swings within 1/3 of the leg; otherwise converging legs = triangle.
    private static string ChartPatternOf(List<SwingPoint> swings)
    {
        if (swings.Count < 4) return "";
        var last4 = swings.TakeLast(4).ToList();
        var highs = last4.Where(s => s.Kind == "H").Select(s => s.Price).ToList();
        var lows = last4.Where(s => s.Kind == "L").Select(s => s.Price).ToList();
        var span = last4.Max(s => s.Price) - last4.Min(s => s.Price);
        if (span <= 0) return "";
        if (highs.Count >= 2 && Math.Abs(highs[^1] - highs[^2]) / span < .33) return "Double Top";
        if (lows.Count >= 2 && Math.Abs(lows[^1] - lows[^2]) / span < .33) return "Double Bottom";
        if (highs.Count >= 2 && lows.Count >= 2)
        {
            var hFall = highs[^1] < highs[^2];
            var lRise = lows[^1] > lows[^2];
            if (hFall && lRise) return "Triangle (squeeze)";
            if (!hFall && !lRise) return "Broadening";
        }
        return "";
    }

    /// Confluence score, NOT a win probability. Each factor votes ±1; the result
    /// is normalised so BUY+SELL+NO TRADE = 100. Backtests on this feed found no
    /// rule that beats chance, so the panel is labelled as agreement, not odds.
    internal static (int Buy, int Sell, int Flat, string Reason) BiasScore(IReadOnlyList<(int Vote, string Label)> votes)
    {
        var buy = votes.Count(v => v.Vote > 0);
        var sell = votes.Count(v => v.Vote < 0);
        var total = Math.Max(votes.Count, 1);
        var flat = total - buy - sell;
        var reason = string.Join(" · ", votes.Where(v => v.Vote != 0).Select(v => v.Label));
        return (buy * 100 / total, sell * 100 / total, 100 - buy * 100 / total - sell * 100 / total,
            reason.Length == 0 ? "tidak ada faktor yang sejalan" : reason);
    }

    private void UpdateStructurePanel(Candle last, StructureAnalysis structure, string trend, double slope, double threshold, double rsi)
    {
        var candlePattern = CandlePatternOf(chartCandles);
        var chartPattern = ChartPatternOf(structure.Swings);
        var support = RecentLevels(structure.Swings, "L").LastOrDefault()?.Price ?? 0;
        var resistance = RecentLevels(structure.Swings, "H").LastOrDefault()?.Price ?? 0;
        var unit = Math.Max(last.Close * 1e-5, 1e-9);
        var bos = structure.BosEvents.LastOrDefault();

        var votes = new List<(int, string)>
        {
            (structure.Trend == "UP" ? 1 : structure.Trend == "DOWN" ? -1 : 0, $"struktur {structure.Trend}"),
            (slope > threshold ? 1 : slope < -threshold ? -1 : 0, slope > threshold ? "slope naik" : "slope turun"),
            (rsi < 30 ? 1 : rsi > 70 ? -1 : 0, $"RSI {rsi:0}"),
            (bos is null ? 0 : bos.Direction == "UP" ? 1 : -1, bos is null ? "" : $"{bos.Kind} {bos.Direction}"),
            (candlePattern.Contains("Bullish") || candlePattern == "Hammer" ? 1 : candlePattern.Contains("Bearish") || candlePattern == "Shooting Star" ? -1 : 0, candlePattern),
            (chartPattern == "Double Bottom" ? 1 : chartPattern == "Double Top" ? -1 : 0, chartPattern),
        };
        var (buy, sell, flat, reason) = BiasScore(votes);

        var lead = buy > sell && buy > flat ? "BUY" : sell > buy && sell > flat ? "SELL" : "NO TRADE";
        var pct = lead == "BUY" ? buy : lead == "SELL" ? sell : flat;
        var colour = lead == "BUY" ? "#25C89A" : lead == "SELL" ? "#F45B69" : "#59728E";
        var brush = (Brush)new BrushConverter().ConvertFromString(colour)!;
        StructBiasText.Text = lead;
        StructBiasText.Foreground = brush;
        StructBiasPct.Text = $"{pct}%";
        StructBiasPct.Foreground = brush;
        StructBiasBar.Background = brush;
        var track = ((FrameworkElement)StructBiasBar.Parent).ActualWidth;
        StructBiasBar.Width = Math.Max(0, track * pct / 100.0);

        StructBosAge.Text = bos is null ? "no BOS" : $"BOS {structure.Swings.Count} sw";
        StructLevels.Text = $"R {(resistance > 0 ? $"{(resistance - last.Close) / unit:+0;-0}p" : "—")}   S {(support > 0 ? $"{(last.Close - support) / unit:+0;-0}p" : "—")}   {trend}";
        var patterns = string.Join(" + ", new[] { candlePattern, chartPattern }.Where(p => p.Length > 0));
        StructReason.Text = $"{(patterns.Length > 0 ? patterns + " · " : "")}{reason}\nskor konfluensi, bukan peluang menang";
    }

    private void UpdateMarketQuality(JsonElement feed, Candle? last)
    {
        var reasons = new List<string>();
        var isOtc = selectedSymbol.EndsWith("_otc", StringComparison.OrdinalIgnoreCase);
        if (!feed.TryGetProperty("liveSymbols", out var liveSymbols) || !liveSymbols.EnumerateArray().Any(item => item.GetString() == selectedSymbol)) reasons.Add("Tick terlambat");
        if (sourceCandles.Count < 50) reasons.Add("Riwayat belum cukup");
        if (last is null || last.Close <= 0) reasons.Add("Candle tidak valid");
        if (last is not null && chartCandles.Count >= 21)
        {
            var currentRange = (last.High - last.Low) / last.Close;
            var medianRange = chartCandles.TakeLast(21).Take(20).Select(candle => (candle.High - candle.Low) / candle.Close).OrderBy(value => value).ElementAt(10);
            if (medianRange > 0 && (currentRange > medianRange * 4 || currentRange < medianRange * .08)) reasons.Add("Pergerakan harga tidak normal");
        }
        var normal = reasons.Count == 0;
        var color = normal ? "#25C89A" : "#F45B69";
        MarketQualityBorder.Background = (Brush)new BrushConverter().ConvertFromString(normal ? "#173F38" : "#421C28")!;
        MarketQualityBorder.BorderBrush = (Brush)new BrushConverter().ConvertFromString(color)!;
        MarketQualityText.Foreground = (Brush)new BrushConverter().ConvertFromString(color)!;
        MarketQualityText.Text = normal
            ? $"INTEGRITAS {(isOtc ? "OTC" : "DATA")}\n✓ Data normal\nTick live · Candle valid"
            : $"INTEGRITAS {(isOtc ? "OTC" : "DATA")}\n! Periksa data\n{string.Join(" · ", reasons)}";
    }

    private static double Rsi(IReadOnlyList<Candle> candles, int period = 14)
    {
        if (candles.Count <= period) return 0;
        var gains = 0d; var losses = 0d;
        for (var index = candles.Count - period; index < candles.Count; index++)
        {
            var change = candles[index].Close - candles[index - 1].Close;
            gains += Math.Max(0, change); losses += Math.Max(0, -change);
        }
        if (losses == 0) return 100;
        var relativeStrength = gains / losses;
        return 100 - 100 / (1 + relativeStrength);
    }

    private static (double Lower, double Middle, double Upper) Bollinger(IReadOnlyList<double> values, int period = 20, double deviations = 2)
    {
        if (values.Count == 0) return (0, 0, 0);
        var window = values.TakeLast(period).ToArray();
        var mean = window.Average();
        var deviation = Math.Sqrt(window.Average(value => (value - mean) * (value - mean)));
        return (mean - deviations * deviation, mean, mean + deviations * deviation);
    }

    private string TimeframeLabel() => ((ComboBoxItem?)TimeframeBox.SelectedItem)?.Content?.ToString() ?? "1m";

    private void DrawChart()
    {
        PriceChart.Children.Clear();
        labelBoxes.Clear();
        PriceChart.IsHitTestVisible = true;
        if (chartCandles.Count < 2 || PriceChart.ActualWidth < 3 || PriceChart.ActualHeight < 3) return;
        visibleBars = Math.Clamp(visibleBars, 20, chartCandles.Count); firstVisible = Math.Clamp(firstVisible, 0, Math.Max(0, chartCandles.Count - visibleBars));
        var autoEnd = AutoEndBox.IsChecked == true;
        var startIndex = autoEnd ? Math.Max(0, chartCandles.Count - visibleBars) : firstVisible;
        var shown = chartCandles.Skip(startIndex).Take(visibleBars).ToList();
        var chartSlots = shown.Count + 4;
        var closedCount = ClosedBarCount(chartCandles, TimeframeMinutes());
        var structure = AnalyzeStructure(chartCandles, closedCount);
        var strategy = CurrentStrategySelection();
        var supportLevels = RecentLevels(structure.Swings, "L");
        var resistanceLevels = RecentLevels(structure.Swings, "H");
        var low = shown.Min(candle => candle.Low); var high = shown.Max(candle => candle.High);
        if (LegacyAnalysisEnabled && LevelsOverlayBox.IsChecked == true)
        {
            if (supportLevels.Count > 0) low = Math.Min(low, supportLevels.Min(level => level.Price));
            if (resistanceLevels.Count > 0) high = Math.Max(high, resistanceLevels.Max(level => level.Price));
        }
        var range = Math.Max(high - low, Math.Abs(high) * .00001); low -= range * 1.5; high += range * 1.5; range = high - low;
        const double left = 5, right = 72, top = 25, bottom = 25;
        var width = Math.Max(1, PriceChart.ActualWidth - left - right); var height = Math.Max(1, PriceChart.ActualHeight - top - bottom);
        double X(int index) => left + (index + .5) * width / chartSlots; double Y(double value) => top + (high - value) * height / range;
        for (var row = 0; row <= 5; row++)
        {
            var y = top + row * height / 5; var price = high - row * range / 5;
            PriceChart.Children.Add(new Line { X1 = left, X2 = left + width, Y1 = y, Y2 = y, Stroke = new SolidColorBrush(Color.FromRgb(31, 46, 65)), StrokeThickness = 1 });
            AddText(price.ToString("N5"), left + width + 5, y - 8, "#7C91AB", 10);
        }
        var timeAxisY = top + height;
        var timeLabelStep = Math.Max(1, (int)Math.Ceiling(shown.Count / Math.Max(2, width / 90)));
        for (var index = 0; index < shown.Count; index += timeLabelStep)
        {
            var axisX = X(index);
            PriceChart.Children.Add(new Line { X1 = axisX, X2 = axisX, Y1 = top, Y2 = timeAxisY, Stroke = new SolidColorBrush(Color.FromRgb(24, 35, 49)), StrokeThickness = 1 });
            PriceChart.Children.Add(new Line { X1 = axisX, X2 = axisX, Y1 = timeAxisY - 3, Y2 = timeAxisY + 2, Stroke = new SolidColorBrush(Color.FromRgb(90, 108, 130)), StrokeThickness = 1 });
            var brokerTime = DateTimeOffset.FromUnixTimeSeconds((long)shown[index].Time + (long)brokerOffset).ToLocalTime();
            AddText(brokerTime.ToString("HH:mm"), axisX - 17, timeAxisY + 4, "#7C91AB", 9);
        }
        if (LegacyAnalysisEnabled && DirectionOverlayBox.IsChecked == true && shown.Count >= 21)
        {
            DrawEma(9, "#31D2F2");
            DrawEma(21, "#D77CFF");
        }
        if (BollingerOverlayBox.IsChecked == true && shown.Count >= 20)
        {
            var upper = new Polyline { Stroke = new SolidColorBrush(Color.FromRgb(240, 185, 11)), StrokeThickness = 1, Opacity = .7 };
            var lower = new Polyline { Stroke = new SolidColorBrush(Color.FromRgb(240, 185, 11)), StrokeThickness = 1, Opacity = .7 };
            for (var index = 19; index < shown.Count; index++)
            {
                var bands = Bollinger(shown.Skip(index - 19).Take(20).Select(candle => candle.Close).ToList());
                upper.Points.Add(new Point(X(index), Y(bands.Upper)));
                lower.Points.Add(new Point(X(index), Y(bands.Lower)));
            }
            PriceChart.Children.Add(upper); PriceChart.Children.Add(lower);
            AddText("BOLL 20,2", left + 5, top + 32, "#F0B90B", 9, "#101722");
        }
        if (RsiOverlayBox.IsChecked == true && shown.Count >= 15)
            AddText($"RSI 14 {Rsi(shown):0.0}", left + 5, top + 46, "#D77CFF", 9, "#101722");
        if (AtrOverlayBox.IsChecked == true && shown.Count >= 15)
            AddText($"ATR 14 {FormatPrice(Atr(shown, 14))}", left + 5, top + 60, "#52B7D8", 9, "#101722");
        void DrawEma(int period, string color)
        {
            var values = Ema(shown.Select(candle => candle.Close), period);
            var line = new Polyline { Stroke = (Brush)new BrushConverter().ConvertFromString(color)!, StrokeThickness = 1.35 };
            for (var index = period - 1; index < shown.Count; index++) line.Points.Add(new Point(X(index), Y(values[index])));
            PriceChart.Children.Add(line);
            AddText($"EMA{period} {FormatPrice(values[^1])}", left + 5, top + (period == 9 ? 2 : 17), color, 9, "#101722");
        }
        var candleWidth = Math.Max(1, width / chartSlots * .52);
        for (var index = 0; index < shown.Count; index++)
        {
            var candle = shown[index]; var color = candle.Close >= candle.Open ? Color.FromRgb(38, 202, 155) : Color.FromRgb(244, 91, 105); var brush = new SolidColorBrush(color); var x = X(index);
            var wick = new Line { X1 = x, X2 = x, Y1 = Y(candle.High), Y2 = Y(candle.Low), Stroke = brush, StrokeThickness = 1.4, SnapsToDevicePixels = true };
            PriceChart.Children.Add(wick);
            var body = new Rectangle { Width = candleWidth, Height = Math.Max(1, Math.Abs(Y(candle.Open) - Y(candle.Close))), Fill = brush, SnapsToDevicePixels = true, UseLayoutRounding = true, StrokeThickness = 0 };
            Canvas.SetLeft(body, x - candleWidth / 2); Canvas.SetTop(body, Math.Min(Y(candle.Open), Y(candle.Close))); PriceChart.Children.Add(body);
        }
        if (LegacyAnalysisEnabled && LevelsOverlayBox.IsChecked == true)
        {
            foreach (var level in supportLevels) DrawLevel(level.Price, "SUPPORT", "#25C89A", Y, left, width, false);
            foreach (var level in resistanceLevels) DrawLevel(level.Price, "RESISTANCE", "#F45B69", Y, left, width, false);
        }
        var last = shown[^1]; var marker = last.Close >= last.Open ? "#26CA9B" : "#F45B69";
        var liveLine = new Line { X1 = left, X2 = left + width, Y1 = Y(last.Close), Y2 = Y(last.Close), Stroke = (Brush)new BrushConverter().ConvertFromString(marker)!, StrokeThickness = 1.2, StrokeDashArray = [3, 2], Opacity = .95 };
        Panel.SetZIndex(liveLine, 11); PriceChart.Children.Add(liveLine);
        var candleSeconds = TimeframeMinutes() * 60;
        var remainingSeconds = SecondsLeftInCandle();
        DrawStrategyArrows(shown, startIndex, X, Y);
        AddText($"{FormatPrice(last.Close)}  {TimeSpan.FromSeconds(remainingSeconds):mm\\:ss}", left + width + 4, Y(last.Close) - 9, "#FFFFFF", 10, marker);
        if (SlShOverlayBox.IsChecked == true) foreach (var pivot in LockedStructureMarks().Where(pivot => pivot.Kind is "SL" or "SH").Where(pivot => pivot.Index >= startIndex && pivot.Index < startIndex + shown.Count))
        {
            var color = pivot.Kind == "SH" ? "#F8B84E" : "#4D9DFF";
            var localIndex = pivot.Index - startIndex;
            var direction = pivot.Kind == "SH" ? "DOWN" : "UP";
            AddPinnedText(pivot.Kind, X(localIndex) - 7, Y(pivot.Price) + (pivot.Kind == "SH" ? -16 : 4), color, 9, "#101722");
            PushChartAlert(pivot.Kind, direction, shown[localIndex].Time);
        }
        if (LegacyAnalysisEnabled && StructureOverlayBox.IsChecked == true)
        {
            var primaryEvents = strategy.Primary == "SMC" ? structure.SmcEvents : structure.BosEvents;
            IReadOnlyList<StrategyEvent> filterEvents = strategy.Filter == "BOS" ? structure.BosEvents : strategy.Filter == "SMC" ? structure.SmcEvents : Array.Empty<StrategyEvent>();
            foreach (var primary in primaryEvents.Where(eventItem => eventItem.Index >= startIndex && eventItem.Index < startIndex + shown.Count))
            {
                var localIndex = primary.Index - startIndex;
                if (primary.Kind == "CHoCH") PushChartAlert("CHoCH", primary.Direction, shown[localIndex].Time);
                if (primary.Kind != "BOS" && primary.Kind != "SMC SWEEP") continue;
                if (strategy.Filter != "NONE" && !filterEvents.Any(filter => filter.Direction == primary.Direction && filter.Index < primary.Index && primary.Index - filter.Index <= 6)) continue;
                var candle = shown[localIndex];
                var rangeSize = candle.High - candle.Low;
                var atrLocal = shown.Skip(Math.Max(0, localIndex - 19)).Take(Math.Min(20, localIndex + 1)).Select(candleItem => candleItem.High - candleItem.Low).DefaultIfEmpty(Math.Max(rangeSize, Math.Abs(candle.Close) * .00001)).Average();
                if (rangeSize < atrLocal * 1.05) continue;
                // Structure is drawn as information only. The confluence-scored
                // "NEXT ENTRY" arrow that used to sit here was removed after it
                // backtested at 49.96% over 2,364 holdout trades — see
                // AUDIT-STRATEGI.md. Directional calls come from the SIGNAL panel.
                var color = primary.Direction == "UP" ? "#25C89A" : "#F45B69";
                DrawLevel(primary.Level, $"{primary.Kind} LEVEL", color, Y, left, width);
                var breakLine = new Line { X1 = X(localIndex), X2 = X(localIndex), Y1 = Y(candle.Close), Y2 = Y(primary.Level), Stroke = (Brush)new BrushConverter().ConvertFromString(color)!, StrokeThickness = 1, StrokeDashArray = [2, 2] };
                Panel.SetZIndex(breakLine, 12); PriceChart.Children.Add(breakLine);
            }
        }
        var start = DateTimeOffset.FromUnixTimeSeconds((long)shown[0].Time).LocalDateTime; var end = DateTimeOffset.FromUnixTimeSeconds((long)last.Time).LocalDateTime;
        ChartInfoText.Text = $"{selectedSymbol} · {TimeframeLabel()} · {shown.Count} candles · {closedCount} closed · {structure.Trend} · {(strategy.Primary == "OFF" ? "strategi OFF" : strategy.Primary)}";
        DrawCrosshair(shown, X, Y, left, top, width, height);
        chartAlertsReady = true;
    }

    private void DrawStrategyArrows(IReadOnlyList<Candle> shown, int startIndex, Func<int, double> x, Func<double, double> y)
    {
        var enabled = EnabledStrategies().ToList();
        if (enabled.Count == 0) return;
        for (var localIndex = 1; localIndex < shown.Count; localIndex++)
        {
            var signalIndex = startIndex + localIndex - 1;
            if (signalIndex < 25 || signalIndex >= chartCandles.Count - 1) continue;
            foreach (var strategy in enabled)
            {
                var direction = HistoricalDirection(strategy, signalIndex);
                if (direction is null) continue;
                DrawArrow(shown[localIndex], direction, x(localIndex), y, StrategyCode(strategy));
                if (signalIndex == chartCandles.Count - 2)
                    PushChartAlert($"{StrategyName(strategy)} · ENTRY {direction}", direction == "CALL" ? "UP" : "DOWN", chartCandles[signalIndex].Time, "panah pada candle entry berikutnya", force: true);
            }
        }
    }

    private string? HistoricalDirection(ActiveStrategy strategy, int index)
    {
        var sample = chartCandles.Take(index + 1).ToList();
        if (sample.Count < 26) return null;
        var last = sample[^1];
        var previous = sample[^2];
        var closes = sample.Select(candle => candle.Close).ToList();
        var fast = Ema(closes, 9);
        var slow = Ema(closes, 21);
        var pattern = CandlePatternOf(sample);
        var bullishCandle = last.Close > last.Open && (pattern.Contains("Bullish") || pattern == "Hammer" || last.Close > previous.High);
        var bearishCandle = last.Close < last.Open && (pattern.Contains("Bearish") || pattern == "Shooting Star" || last.Close < previous.Low);
        if (strategy == ActiveStrategy.Trend)
        {
            var structure = AnalyzeStructure(sample, sample.Count);
            var bos = structure.BosEvents.LastOrDefault(item => item.Index == sample.Count - 1);
            var crossUp = fast[^2] <= slow[^2] && fast[^1] > slow[^1];
            var crossDown = fast[^2] >= slow[^2] && fast[^1] < slow[^1];
            return (bos?.Direction == "UP" || crossUp) && fast[^1] > slow[^1] && structure.Trend == "UP" && bullishCandle ? "CALL"
                : (bos?.Direction == "DOWN" || crossDown) && fast[^1] < slow[^1] && structure.Trend == "DOWN" && bearishCandle ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.MomentumReversal)
        {
            var before = Rsi(sample.Take(sample.Count - 1).ToList());
            var now = Rsi(sample);
            return before < 30 && now >= 30 && bullishCandle ? "CALL"
                : before > 70 && now <= 70 && bearishCandle ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.BollingerReentry)
        {
            var bandsBefore = Bollinger(sample.Take(sample.Count - 1).Select(candle => candle.Close).TakeLast(20).ToList());
            var bandsNow = Bollinger(closes.TakeLast(20).ToList());
            return previous.Close < bandsBefore.Lower && last.Close >= bandsNow.Lower && last.Close < bandsNow.Middle ? "CALL"
                : previous.Close > bandsBefore.Upper && last.Close <= bandsNow.Upper && last.Close > bandsNow.Middle ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.SrRejection)
        {
            var levels = AnalyzeStructure(sample.Take(sample.Count - 1).ToList(), sample.Count - 1).Swings;
            var support = RecentLevels(levels, "L").LastOrDefault()?.Price;
            var resistance = RecentLevels(levels, "H").LastOrDefault()?.Price;
            var tolerance = Atr(sample.Take(sample.Count - 1).ToList(), 14) * .2;
            if (support is > 0 && tolerance > 0 && last.Low <= support + tolerance && last.Close > support && last.Close > last.Open) return "CALL";
            if (resistance is > 0 && tolerance > 0 && last.High >= resistance - tolerance && last.Close < resistance && last.Close < last.Open) return "PUT";
            return null;
        }
        if (strategy == ActiveStrategy.VolatilityBreakout)
        {
            var priorRange = sample.Skip(sample.Count - 13).Take(12).ToList();
            var atr = Atr(sample.Take(sample.Count - 1).ToList(), 14);
            if (atr <= 0) return null;
            return last.Close > priorRange.Max(candle => candle.High) && last.High - last.Low >= atr * 1.05 ? "CALL"
                : last.Close < priorRange.Min(candle => candle.Low) && last.High - last.Low >= atr * 1.05 ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.ZScore)
        {
            var average = closes.TakeLast(20).Average();
            var move = closes.TakeLast(20).Zip(closes.TakeLast(21), (current, previous) => Math.Abs(current - previous)).Average();
            var z = move > 0 ? (last.Close - average) / move : 0;
            return z < -2 ? "CALL" : z > 2 ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.EmaCross)
        {
            return fast.Count < 2 || slow.Count < 2 ? null : fast[^2] <= slow[^2] && fast[^1] > slow[^1] ? "CALL" : fast[^2] >= slow[^2] && fast[^1] < slow[^1] ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.RsiReversal)
        {
            var rsi = Rsi(sample);
            return rsi < 30 ? "CALL" : rsi > 70 ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.StructureBreak)
        {
            var prior = sample.Skip(Math.Max(0, sample.Count - 6)).Take(5).ToList();
            return last.Close > prior.Max(candle => candle.High) ? "CALL" : last.Close < prior.Min(candle => candle.Low) ? "PUT" : null;
        }
        return null;
    }

    private static string StrategyCode(ActiveStrategy strategy) => strategy switch
    {
        ActiveStrategy.Trend => "T",
        ActiveStrategy.MomentumReversal => "M",
        ActiveStrategy.BollingerReentry => "B",
        ActiveStrategy.SrRejection => "S",
        ActiveStrategy.VolatilityBreakout => "V",
        _ => "",
    };

    private void DrawArrow(Candle candle, string direction, double x, Func<double, double> y, string code = "")
    {
        var up = direction == "CALL";
        var colour = (Brush)new BrushConverter().ConvertFromString(up ? "#28D19C" : "#F26D78")!;
        var arrowY = up ? y(candle.Low) + 16 : y(candle.High) - 16;
        var arrow = new Polygon { Fill = colour, Points = up ? [new Point(x, arrowY - 11), new Point(x - 6, arrowY + 3), new Point(x + 6, arrowY + 3)] : [new Point(x, arrowY + 11), new Point(x - 6, arrowY - 3), new Point(x + 6, arrowY - 3)] };
        Panel.SetZIndex(arrow, 30); PriceChart.Children.Add(arrow);
        if (code.Length > 0) AddText(code, x - 3, arrowY + (up ? 4 : -13), up ? "#25C89A" : "#F45B69", 8);
    }

    private void DrawCrosshair(IReadOnlyList<Candle> shown, Func<int, double> x, Func<double, double> y, double left, double top, double width, double height)
    {
        if (!hasHover || panning || hoverPoint.X < left || hoverPoint.X > left + width || hoverPoint.Y < top || hoverPoint.Y > top + height) return;
        var index = Math.Clamp((int)Math.Round((hoverPoint.X - left) * (shown.Count + 4) / width - .5), 0, shown.Count - 1);
        var candle = shown[index];
        var stroke = new SolidColorBrush(Color.FromRgb(82, 105, 133));
        var vertical = new Line { X1 = x(index), X2 = x(index), Y1 = top, Y2 = top + height, Stroke = stroke, StrokeThickness = 1, StrokeDashArray = [3, 3] };
        var horizontal = new Line { X1 = left, X2 = left + width, Y1 = hoverPoint.Y, Y2 = hoverPoint.Y, Stroke = stroke, StrokeThickness = 1, StrokeDashArray = [3, 3] };
        Panel.SetZIndex(vertical, 40); Panel.SetZIndex(horizontal, 40); PriceChart.Children.Add(vertical); PriceChart.Children.Add(horizontal);
        var time = DateTimeOffset.FromUnixTimeSeconds((long)candle.Time).LocalDateTime.ToString("dd MMM HH:mm");
        AddText($"{time}  O {FormatPrice(candle.Open)}  H {FormatPrice(candle.High)}  L {FormatPrice(candle.Low)}  C {FormatPrice(candle.Close)}", left + 8, top + 5, "#E5EBF4", 10, "#121A26");
    }

    /// Draws only entries already locked in firedEntries, so a redraw can never
    /// move, recolour or remove an arrow that has been shown to the trader.
    private void DrawEntryArrows(IReadOnlyList<Candle> shown, Func<int, double> X, Func<double, double> Y)
    {
        for (var index = 0; index < shown.Count; index++)
        {
            if (!firedEntries.TryGetValue(shown[index].Time, out var direction)) continue;
            var up = direction == "CALL";
            var colour = (Brush)new BrushConverter().ConvertFromString(up ? "#25C89A" : "#F45B69")!;
            var x = X(index);
            var y = up ? Y(shown[index].Low) + 16 : Y(shown[index].High) - 16;
            var arrow = new Polygon
            {
                Fill = colour,
                Points = up
                    ? [new Point(x, y - 11), new Point(x - 6, y + 3), new Point(x + 6, y + 3)]
                    : [new Point(x, y + 11), new Point(x - 6, y - 3), new Point(x + 6, y - 3)],
            };
            Panel.SetZIndex(arrow, 30);
            PriceChart.Children.Add(arrow);
            AddText(direction, x - 13, up ? y + 4 : y - 18, up ? "#25C89A" : "#F45B69", 9, "#101722");
        }
    }

    private readonly List<Rect> labelBoxes = [];

    /// Nudges a label down until it clears every label already placed this redraw.
    /// ponytail: linear scan over labels on screen; fine at this count, index by
    /// row if a chart ever draws hundreds.
    private double FreeRow(double x, double y, double width, double height)
    {
        var box = new Rect(x, y, width, height);
        for (var attempt = 0; attempt < 12; attempt++)
        {
            if (!labelBoxes.Any(placed => placed.IntersectsWith(box))) break;
            box.Y += height + 2;
        }
        labelBoxes.Add(box);
        return box.Y;
    }

    private void AddText(string text, double x, double y, string foreground, double size, string? background = null)
    {
        var block = new TextBlock { Text = text, Foreground = (Brush)new BrushConverter().ConvertFromString(foreground)!, FontSize = size, FontFamily = new FontFamily("Consolas"), Padding = new Thickness(2, 0, 2, 0) };
        y = FreeRow(x, y, text.Length * size * .62 + 4, size + 3);
        if (background is not null) block.Background = (Brush)new BrushConverter().ConvertFromString(background)!;
        Canvas.SetLeft(block, x); Canvas.SetTop(block, y); PriceChart.Children.Add(block);
    }

    private void AddPinnedText(string text, double x, double y, string foreground, double size, string? background = null)
    {
        var block = new TextBlock { Text = text, Foreground = (Brush)new BrushConverter().ConvertFromString(foreground)!, FontSize = size, FontFamily = new FontFamily("Consolas"), Padding = new Thickness(2, 0, 2, 0) };
        if (background is not null) block.Background = (Brush)new BrushConverter().ConvertFromString(background)!;
        Canvas.SetLeft(block, x); Canvas.SetTop(block, y); PriceChart.Children.Add(block);
    }

    private void DrawTrendChannel(IEnumerable<SwingPoint> swings, int startIndex, int shownCount, Func<int, double> X, Func<double, double> Y, double left, double width)
    {
        var highs = swings.Where(swing => swing.Kind == "H").OrderBy(swing => swing.Index).TakeLast(2).Select(swing => (swing.Index - startIndex, swing.Price)).ToList();
        var lows = swings.Where(swing => swing.Kind == "L").OrderBy(swing => swing.Index).TakeLast(2).Select(swing => (swing.Index - startIndex, swing.Price)).ToList();
        if (highs.Count == 2) DrawChannelEdge(highs[0], highs[1], shownCount - 1, X, Y, left, width, "#31D2F2", "UPPER");
        if (lows.Count == 2) DrawChannelEdge(lows[0], lows[1], shownCount - 1, X, Y, left, width, "#D77CFF", "LOWER");
    }

    private void DrawChannelEdge((int index, double price) a, (int index, double price) b, int lastIndex, Func<int, double> X, Func<double, double> Y, double left, double width, string color, string label)
    {
        if (b.index <= a.index) return;
        var slope = (b.price - a.price) / (b.index - a.index);
        var startIndex = Math.Max(a.index, 0);
        var y1 = Y(a.price + slope * (startIndex - a.index)); var y2 = Y(a.price + slope * (lastIndex - a.index));
        var line = new Line { X1 = X(startIndex), X2 = X(lastIndex), Y1 = y1, Y2 = y2, Stroke = (Brush)new BrushConverter().ConvertFromString(color)!, StrokeThickness = 1.1, StrokeDashArray = [5, 3] };
        Panel.SetZIndex(line, 6); PriceChart.Children.Add(line);
        AddPinnedText($"{label} CHANNEL", Math.Max(left + 5, X(lastIndex) - 95), y2 - 7, color, 9, "#101722");
    }
    private void DrawLevel(double price, string label, string color, Func<double, double> y, double left, double width, bool dashed = true)
    {
        var line = new Line { X1 = left, X2 = left + width, Y1 = y(price), Y2 = y(price), Stroke = (Brush)new BrushConverter().ConvertFromString(color)!, StrokeThickness = dashed ? 1 : 1.2, Opacity = dashed ? .8 : 1 };
        if (dashed) line.StrokeDashArray = [4, 3];
        Panel.SetZIndex(line, dashed ? 4 : 10);
        PriceChart.Children.Add(line);
        AddText($"{label} {FormatPrice(price)}", left + 5, y(price) - 17, color, 9, "#101722");
    }

    private static List<SwingPoint> RecentLevels(IEnumerable<SwingPoint> swings, string kind)
    {
        var levels = new List<SwingPoint>();
        foreach (var swing in swings.Where(swing => swing.Kind == kind).OrderByDescending(swing => swing.Index))
        {
            var tolerance = Math.Max(Math.Abs(swing.Price) * .0002, .00000001);
            if (levels.All(level => Math.Abs(level.Price - swing.Price) > tolerance)) levels.Add(swing);
            if (levels.Count == 2) break;
        }
        return levels;
    }

    private static int ClosedBarCount(IReadOnlyList<Candle> candles, int minutes)
    {
        return Math.Max(0, candles.Count - 1);
    }

    private void LockStructureMarks(StructureAnalysis structure)
    {
        var minutes = TimeframeMinutes();
        var changed = false;
        foreach (var pivot in structure.Swings)
        {
            var time = chartCandles[pivot.Index].Time;
            changed |= lockedStructureMarks.TryAdd((selectedSymbol, minutes, time), new StructureMark(pivot.Price, pivot.Kind));
        }
        if (changed) { }
    }

    private void LockSlShMarks()
    {
        var minutes = TimeframeMinutes();
        var closedCount = ClosedBarCount(chartCandles, minutes);
        var changed = false;
        for (var index = 4; index < closedCount; index++)
        {
            var candle = chartCandles[index];
            var prior = chartCandles.Skip(index - 4).Take(4).ToList();
            var kind = candle.High >= prior.Max(item => item.High) ? "SH" : candle.Low <= prior.Min(item => item.Low) ? "SL" : "";
            if (kind.Length == 0) continue;
            var price = kind == "SH" ? candle.High : candle.Low;
            if (!lockedStructureMarks.TryAdd((selectedSymbol, minutes, candle.Time), new StructureMark(price, kind))) continue;
            slShStore.InsertIfAbsent(new SlShRow(selectedSymbol, minutes, candle.Time, price, kind));
            changed = true;
        }
        if (changed) { }
    }

    private IEnumerable<SwingPoint> LockedStructureMarks()
    {
        var minutes = TimeframeMinutes();
        return chartCandles.Select((candle, index) => new { candle, index })
            .Where(item => lockedStructureMarks.TryGetValue((selectedSymbol, minutes, item.candle.Time), out _))
            .Select(item => new SwingPoint(item.index, lockedStructureMarks[(selectedSymbol, minutes, item.candle.Time)].Price, lockedStructureMarks[(selectedSymbol, minutes, item.candle.Time)].Kind));
    }

    private void LoadStructureMarks()
    {
        foreach (var mark in slShStore.Load())
            lockedStructureMarks.TryAdd((mark.Symbol, mark.Minutes, mark.Time), new StructureMark(mark.Price, mark.Kind));
        if (!File.Exists(LegacyStructureMarksPath)) return;
        try
        {
            foreach (var mark in JsonSerializer.Deserialize<List<StructureMarkRow>>(File.ReadAllText(LegacyStructureMarksPath)) ?? [])
            {
                if (mark.Kind is not ("SL" or "SH")) continue;
                lockedStructureMarks.TryAdd((mark.Symbol, mark.Minutes, mark.Time), new StructureMark(mark.Price, mark.Kind));
                slShStore.InsertIfAbsent(new SlShRow(mark.Symbol, mark.Minutes, mark.Time, mark.Price, mark.Kind));
            }
        }
        catch { }
    }

#if DEBUG
    private static void VerifyNonRepaintStructure()
    {
        var candles = new List<Candle>
        {
            new(0, 9, 10, 8, 9), new(60, 10, 12, 9, 10),
            new(120, 10, 11, 9.5, 10), new(180, 10, 100, 0, 10),
        };
        var first = AnalyzeStructure(candles, ClosedBarCount(candles, 1)).Swings;
        candles[^1] = candles[^1] with { High = 1_000, Low = -1_000 };
        var second = AnalyzeStructure(candles, ClosedBarCount(candles, 1)).Swings;
        Debug.Assert(first.SequenceEqual(second) && first.Any(swing => swing.Index == 1 && swing.Kind == "H"));
    }
#endif

    private static StructureAnalysis AnalyzeStructure(IReadOnlyList<Candle> candles, int closedCount)
    {
        var swings = new List<SwingPoint>();
        var events = new List<StrategyEvent>();
        SwingPoint? lastHigh = null;
        SwingPoint? lastLow = null;
        var trend = "RANGE";
        for (var index = 0; index < closedCount; index++)
        {
            var pivotIndex = index - 1;
            if (pivotIndex >= 1)
            {
                var window = candles.Skip(pivotIndex - 1).Take(3).ToList();
                if (candles[pivotIndex].High > window.Where((_, offset) => offset != 1).Max(candle => candle.High)) { lastHigh = new SwingPoint(pivotIndex, candles[pivotIndex].High, "H"); swings.Add(lastHigh); }
                if (candles[pivotIndex].Low < window.Where((_, offset) => offset != 1).Min(candle => candle.Low)) { lastLow = new SwingPoint(pivotIndex, candles[pivotIndex].Low, "L"); swings.Add(lastLow); }
            }
            if (index == 0) continue;
            var previousClose = candles[index - 1].Close;
            var close = candles[index].Close;
            if (lastHigh is not null && previousClose <= lastHigh.Price && close > lastHigh.Price) { var kind = trend == "DOWN" ? "CHoCH" : "BOS"; events.Add(new StrategyEvent(index, "UP", lastHigh.Price, kind)); trend = "UP"; lastHigh = null; }
            else if (lastLow is not null && previousClose >= lastLow.Price && close < lastLow.Price) { var kind = trend == "UP" ? "CHoCH" : "BOS"; events.Add(new StrategyEvent(index, "DOWN", lastLow.Price, kind)); trend = "DOWN"; lastLow = null; }
        }
        return new StructureAnalysis(swings, events, AnalyzeSmc(candles, closedCount, swings), trend);
    }

    private static List<StrategyEvent> AnalyzeSmc(IReadOnlyList<Candle> candles, int closedCount, IReadOnlyList<SwingPoint> swings)
    {
        var events = new List<StrategyEvent>();
        for (var index = 1; index < closedCount; index++)
        {
            var candle = candles[index];
            var low = swings.LastOrDefault(swing => swing.Kind == "L" && swing.Index < index);
            var high = swings.LastOrDefault(swing => swing.Kind == "H" && swing.Index < index);
            if (low is not null && candle.Low < low.Price && candle.Close > low.Price) events.Add(new StrategyEvent(index, "UP", low.Price, "SMC SWEEP"));
            else if (high is not null && candle.High > high.Price && candle.Close < high.Price) events.Add(new StrategyEvent(index, "DOWN", high.Price, "SMC SWEEP"));
        }
        return events;
    }

    private static List<double> Ema(IEnumerable<double> source, int period)
    {
        var values = source.ToList();
        var output = new List<double>(values.Count);
        var multiplier = 2d / (period + 1);
        var current = values[0];
        foreach (var value in values) { current = value * multiplier + current * (1 - multiplier); output.Add(current); }
        return output;
    }

    private void SetStatus(string status, string detail)
    {
        var connected = status.Equals("live", StringComparison.OrdinalIgnoreCase);
        var updating = status.Equals("updating", StringComparison.OrdinalIgnoreCase) || status.Equals("connecting", StringComparison.OrdinalIgnoreCase);
        ConnectionText.Text = connected ? "CONNECTED" : updating ? "UPDATING" : "DISCONNECTED";
        LiveDot.Fill = new SolidColorBrush(connected ? Color.FromRgb(37, 200, 154) : updating ? Color.FromRgb(240, 185, 11) : Color.FromRgb(244, 91, 105));
        StatusText.Text = detail;
        ConnectionText.Opacity = 1;
        if (updating)
        {
            var animation = new System.Windows.Media.Animation.DoubleAnimation(0.25, 1, new Duration(TimeSpan.FromMilliseconds(700)))
            {
                AutoReverse = true,
                RepeatBehavior = System.Windows.Media.Animation.RepeatBehavior.Forever
            };
            ConnectionText.BeginAnimation(OpacityProperty, animation);
        }
        else
        {
            ConnectionText.BeginAnimation(OpacityProperty, null);
        }
    }
    private static string Text(JsonElement element, string property) => element.TryGetProperty(property, out var value) ? value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : value.GetRawText() : "â€”";
    private static double? Number(JsonElement element, string property) => element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number ? value.GetDouble() : null;
    private static string FormatPrice(double? value) => value is null ? "â€”" : value.Value.ToString(value.Value >= 100 ? "N2" : "N5");

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshFeed();
    private async void BrowserLogin_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            SetStatus("updating", "Browser Pocket Option dibuka · login manual diperlukanâ€¦");
            using var request = new HttpRequestMessage(HttpMethod.Post, $"{ApiUrl}/api/pocket/browser-login");
            request.Headers.Add("x-api-key", apiKey.Trim());
            using var browserHttp = new HttpClient { Timeout = TimeSpan.FromMinutes(6) };
            using var response = await browserHttp.SendAsync(request);
            var body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode) throw new InvalidOperationException(body);
            await RefreshFeed();
        }
        catch (Exception error)
        {
            SetStatus("disconnected", $"Auto Login gagal: {error.Message}");
        }
    }

    private async void UpdateSsid_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new Window
        {
            Title = "Update Pocket Option DEMO SSID",
            Width = 620,
            Height = 210,
            Owner = this,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            ResizeMode = ResizeMode.NoResize,
            Background = new SolidColorBrush(Color.FromRgb(8, 12, 18)),
            Foreground = new SolidColorBrush(Color.FromRgb(231, 237, 245))
        };
        var input = new PasswordBox { Margin = new Thickness(0, 8, 0, 10), Padding = new Thickness(9, 7, 9, 7), Background = new SolidColorBrush(Color.FromRgb(11, 17, 26)), Foreground = new SolidColorBrush(Color.FromRgb(231, 237, 245)), BorderBrush = new SolidColorBrush(Color.FromRgb(38, 54, 74)) };
        var save = new Button { Content = "Simpan & Sambungkan", IsDefault = true, Margin = new Thickness(0, 0, 6, 0) };
        var cancel = new Button { Content = "Batal", IsCancel = true };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        save.Click += (_, _) => dialog.DialogResult = true;
        buttons.Children.Add(save);
        buttons.Children.Add(cancel);
        var panel = new StackPanel { Margin = new Thickness(18) };
        panel.Children.Add(new TextBlock { Text = "Tempel SSID Pocket Option DEMO baru. Token disimpan lokal dan tidak ditampilkan.", Foreground = new SolidColorBrush(Color.FromRgb(141, 163, 191)), TextWrapping = TextWrapping.Wrap });
        panel.Children.Add(input);
        panel.Children.Add(buttons);
        dialog.Content = panel;
        if (dialog.ShowDialog() != true || string.IsNullOrWhiteSpace(input.Password)) return;

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, $"{ApiUrl}/api/pocket/credential");
            request.Headers.Add("x-api-key", apiKey.Trim());
            request.Content = new StringContent(JsonSerializer.Serialize(new { ssid = input.Password.Trim() }), Encoding.UTF8, "application/json");
            using var response = await http.SendAsync(request);
            if (!response.IsSuccessStatusCode)
            {
                MessageBox.Show("SSID ditolak. Gunakan SSID DEMO yang masih valid.", "Update SSID", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }
            SetStatus("updating", "SSID DEMO diperbarui · menyambungkan ulangâ€¦");
            await Task.Delay(500);
            await RefreshFeed();
        }
        catch (Exception error)
        {
            MessageBox.Show($"Gagal memperbarui SSID: {error.Message}", "Update SSID", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }
    private void OpenDataFolder_Click(object sender, RoutedEventArgs e) => Process.Start(new ProcessStartInfo(System.IO.Path.Combine(Root, "data")) { UseShellExecute = true });
    private async void ExportTicks_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, $"{ApiUrl}/api/research/export/ticks.csv?symbol={Uri.EscapeDataString(selectedSymbol)}");
            request.Headers.Add("x-api-key", apiKey.Trim());
            using var response = await http.SendAsync(request);
            response.EnsureSuccessStatusCode();
            var target = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads", "pocket-option-ticks.csv");
            await File.WriteAllBytesAsync(target, await response.Content.ReadAsByteArrayAsync());
            StatusText.Text = $"CSV saved: {target}";
        }
        catch (Exception error) { StatusText.Text = error.Message; }
    }
    private void SearchBox_TextChanged(object sender, TextChangedEventArgs e) => ApplyMarketFilter();
    private void OpenMarketSearch_Click(object sender, RoutedEventArgs e)
    {
        SearchBox.Focus();
        SearchBox.SelectAll();
    }
    private void GroupFilter_Click(object sender, RoutedEventArgs e) { groupFilter = ((Button)sender).Tag.ToString() ?? "ALL"; ApplyMarketFilter(); }
    private async void MarketList_SelectionChanged(object sender, SelectionChangedEventArgs e) { if (MarketList.SelectedItem is MarketRow row) await SwitchSymbolAsync(row.Symbol); }
    private async Task SwitchSymbolAsync(string symbol)
    {
        if (string.IsNullOrEmpty(symbol) || symbol == selectedSymbol) return;
        selectedSymbol = symbol; firstVisible = 0; nextNewsRefresh = DateTime.MinValue; nextMarketRefresh = DateTime.MinValue;
        chartAlertsReady = false;
        chartAlerts.Clear();
        alertDedup.Clear();
        firedEntries.Clear();
        sourceCandles = []; chartCandles = []; PriceChart.Children.Clear(); ChartLoadingText.Text = "LOADING MARKET..."; ChartStatusOverlay.Visibility = Visibility.Visible; PriceChart.Visibility = Visibility.Collapsed;
        ChartInfoText.Text = $"{symbol} · memuat candle M1 brokerâ€¦";
        await RefreshFeed();
    }
    private async void TimeframeBox_SelectionChanged(object sender, SelectionChangedEventArgs e) { if (!IsLoaded || TimeframeBox.SelectedItem is null) return; firstVisible = 0; await RefreshFeed(); }

    private void Strategy_SelectionChanged(object sender, SelectionChangedEventArgs e) { if (!IsLoaded) return; UpdateStrategyControls(); DrawChart(); }
    private void OverlayChanged(object sender, RoutedEventArgs e) { if (IsLoaded) DrawChart(); }
    private void AutoEndChanged(object sender, RoutedEventArgs e) { if (IsLoaded && AutoEndBox.IsChecked == true) ChartReset_Click(sender, e); }
    private void PriceChart_SizeChanged(object sender, SizeChangedEventArgs e) => DrawChart();
    private void ChartZoomIn_Click(object sender, RoutedEventArgs e) { AutoEndBox.IsChecked = true; visibleBars = Math.Max(5, (int)(visibleBars / 1.25)); firstVisible = Math.Max(0, chartCandles.Count - visibleBars); DrawChart(); }
    private void ChartZoomOut_Click(object sender, RoutedEventArgs e) { AutoEndBox.IsChecked = true; visibleBars = Math.Min(Math.Max(5, chartCandles.Count), (int)(visibleBars * 1.25)); firstVisible = Math.Max(0, chartCandles.Count - visibleBars); DrawChart(); }
    private void ChartReset_Click(object sender, RoutedEventArgs e) { AutoEndBox.IsChecked = true; visibleBars = Math.Min(24, Math.Max(5, chartCandles.Count)); firstVisible = Math.Max(0, chartCandles.Count - visibleBars); DrawChart(); }
    private void PriceChart_MouseWheel(object sender, MouseWheelEventArgs e) { if (e.Delta > 0) ChartZoomIn_Click(sender, e); else ChartZoomOut_Click(sender, e); e.Handled = true; }
    private void PriceChart_MouseDown(object sender, MouseButtonEventArgs e) { if (e.ChangedButton != MouseButton.Left) return; panning = true; panStart = e.GetPosition(PriceChart); panStartFirst = firstVisible; PriceChart.CaptureMouse(); }
    private void PriceChart_MouseMove(object sender, System.Windows.Input.MouseEventArgs e) { hoverPoint = e.GetPosition(PriceChart); hasHover = true; if (!panning) { DrawChart(); return; } var delta = hoverPoint.X - panStart.X; if (Math.Abs(delta) > 3) AutoEndBox.IsChecked = false; firstVisible = Math.Clamp(panStartFirst - (int)Math.Round(delta * visibleBars / Math.Max(1, PriceChart.ActualWidth)), 0, Math.Max(0, chartCandles.Count - visibleBars)); DrawChart(); }
    private void PriceChart_MouseUp(object sender, MouseButtonEventArgs e) { panning = false; PriceChart.ReleaseMouseCapture(); }

/// Entry runs on the NEXT candle. Sinyal dihitung dengan candle A yang sudah
    /// final (close = close M1). Aturan implementasi mengikuti setiap strategi
    /// yang aktif. Lock saat remaining <= 5 detik (rule user, bukan 8).
    private void UpdateEntrySignal()
    {
        var remaining = SecondsLeftInCandle();
        var active = CurrentActiveStrategy();
        if (active == ActiveStrategy.Off)
        {
            SetSignal("OFF", "#59728E", remaining, "chart clean · tidak ada sinyal");
            return;
        }
        if (chartCandles.Count < 25) { SetSignal("WAIT", "#59728E", remaining, "Butuh 25 candle"); return; }

        // Cooldown per strategi: 5 candle (per rule backtest).
        var last = chartCandles[^1];
        var sinceLast = firedStrategies.Count == 0
            ? 999.0
            : (last.Time - firedStrategies.Keys.Max(key => key.SignalTime)) / 60.0;
        var inCooldown = sinceLast < SignalCooldownBars;
        var cooldownText = inCooldown ? $"{SignalCooldownBars - (int)sinceLast} candle lagi" : "";

        var direction = HistoricalDirection(active, chartCandles.Count - 1);
        if (direction is null) { SetSignal("NO SETUP", "#59728E", remaining, "tidak ada setup pada strategi aktif"); return; }
        if (inCooldown) { SetSignal("COOLDOWN", "#59728E", remaining, cooldownText); return; }
        ApplySignal(active, direction, direction == "CALL" ? "#28D19C" : "#F26D78", remaining, CurrentStrategySelection().Primary);
    }
    /// B.O.S = mean reversion z-score + filter range > median(60).
    private bool TryEvaluateBos(Candle last, int remaining, bool inCooldown, string cooldownText,
        out string signal, out string colour, out string reason)
    {
        signal = ""; colour = "#59728E"; reason = "";
        var closes = chartCandles.Select(candle => candle.Close).ToList();
        var sma20 = closes.TakeLast(20).Average();
        var steps = new List<double>();
        for (var index = chartCandles.Count - 20; index < chartCandles.Count; index++)
            if (index > 0) steps.Add(Math.Abs(closes[index] - closes[index - 1]));
        var unit = steps.Count > 0 ? steps.Average() : 0;
        var z = unit > 0 ? (last.Close - sma20) / unit : 0;
        var ranges = chartCandles.TakeLast(60).Select(candle => (candle.High - candle.Low) / candle.Close).Order().ToList();
        var medianRange = ranges.Count > 0 ? ranges[ranges.Count / 2] : 0;
        var liveRange = (last.High - last.Low) / last.Close;
        var quietRegime = liveRange <= medianRange;
        var up = z < -2;
        var down = z > 2;
        reason = $"z {z:+0.0;-0.0} · vol {(quietRegime ? "LO" : "HI")}";
        if (up == false && down == false) return false;
        if (quietRegime) return false;
        if (inCooldown) { signal = "COOLDOWN"; reason = cooldownText + " · " + reason; return true; }
        signal = up ? "CALL" : "PUT";
        colour = up ? "#25C89A" : "#F45B69";
        return true;
    }

    /// Strategy 2 = SL & SH fix dari close candle A (0.5 * ATR14). Capture
    /// dilakukan sekali per candle: SL/SH dihitung, snapshot ditulis ke
    /// strategy2Snapshot, dan tidak pernah disentuh lagi. Sinyal CALL/PUT
    /// hanya kalau dukun candle bullish/bearish cocok; namun SL/SH tetap
    /// di-capture walau tidak ada sinyal (sesuai permintaan: SL/SH jadi
    /// indikator chart yang non-repaint, selalu tampil di candle close).
    private bool TryEvaluateStrategy2(Candle last, int remaining, bool inCooldown, string cooldownText,
        out string signal, out string colour, out string reason)
    {
        signal = ""; colour = "#59728E"; reason = "";
        if (chartCandles.Count < 16) return false;
        var closedCandles = chartCandles.Take(chartCandles.Count - 1).ToList();
        var atr14 = Atr(closedCandles, 14);
        if (atr14 <= 0) return false;
        // Write-once: snapshot hanya diisi saat candle A sudah final (bukan
        // candle berjalan). last.Time adalah epoch menit candle A; candle
        // berjalan = candle terakhir dengan close != final. Cek candle terakhir
        // yang sudah close di chartCandles sebelum index terakhir.
        var lastClosed = closedCandles.LastOrDefault();
        if (lastClosed is null || lastClosed.Time == 0) return false;
        var sl = lastClosed.Close - 0.5 * atr14;
        var sh = lastClosed.Close + 0.5 * atr14;
        var structure = AnalyzeStructure(closedCandles, ClosedBarCount(closedCandles, TimeframeMinutes()));
        var support = RecentLevels(structure.Swings, "L").LastOrDefault()?.Price ?? 0;
        var resistance = RecentLevels(structure.Swings, "H").LastOrDefault()?.Price ?? 0;
        var pattern = CandlePatternOf(closedCandles);
        if (!strategy2Snapshot.ContainsKey(lastClosed.Time))
            strategy2Snapshot[lastClosed.Time] = new Strategy2Snapshot(lastClosed.Close, sl, sh, support, resistance, pattern);
        var snap = strategy2Snapshot[lastClosed.Time];
        var isBullish = snap.Pattern.Contains("Bullish") || snap.Pattern == "Hammer";
        var isBearish = snap.Pattern.Contains("Bearish") || snap.Pattern == "Shooting Star";
        var callOk = snap.SL >= snap.Support && snap.Support > 0 && isBullish;
        var putOk = snap.SH <= snap.Resistance && snap.Resistance > 0 && isBearish;
        reason = $"SL {snap.SL:0.#####} SH {snap.SH:0.#####} S {snap.Support:0.#####} R {snap.Resistance:0.#####} · {snap.Pattern}";
        if (!callOk && !putOk) return false;
        if (inCooldown) { signal = "COOLDOWN"; reason = cooldownText + " · " + reason; return true; }
        signal = callOk ? "CALL" : "PUT";
        colour = callOk ? "#25C89A" : "#F45B69";
        return true;
    }

    /// Strategy 3 = Hurst + z-score + dukun candle. H<0.5 (mean-reverting) +
    /// candle dukun konfirmasi. H>=0.5 -> NO SETUP.
    private bool TryEvaluateStrategy3(Candle last, int remaining, bool inCooldown, string cooldownText,
        out string signal, out string colour, out string reason)
    {
        signal = ""; colour = "#59728E"; reason = "";
        if (chartCandles.Count < 60) return false;
        var hurst = HurstExponent(chartCandles.TakeLast(60).Select(c => c.Close).ToList());
        if (hurst >= 0.5) { reason = $"H {hurst:0.00} ≥ 0.5 trending"; return false; }
        var closes = chartCandles.Select(c => c.Close).ToList();
        var sma20 = closes.TakeLast(20).Average();
        var steps = new List<double>();
        for (var index = chartCandles.Count - 20; index < chartCandles.Count; index++)
            if (index > 0) steps.Add(Math.Abs(closes[index] - closes[index - 1]));
        var unit = steps.Count > 0 ? steps.Average() : 0;
        var z = unit > 0 ? (last.Close - sma20) / unit : 0;
        var pattern = CandlePatternOf(chartCandles);
        var isBullish = pattern.Contains("Bullish") || pattern == "Hammer";
        var isBearish = pattern.Contains("Bearish") || pattern == "Shooting Star";
        reason = $"H {hurst:0.00} · z {z:+0.0;-0.0} · {pattern}";
        if (z < -1.5 && isBullish) signal = "CALL";
        else if (z > 1.5 && isBearish) signal = "PUT";
        else return false;
        if (inCooldown) { signal = "COOLDOWN"; reason = cooldownText + " · " + reason; return true; }
        colour = signal == "CALL" ? "#25C89A" : "#F45B69";
        return true;
    }

    /// Lock sinyal saat remaining <= 5 detik (rule user).
    private void ApplySignal(ActiveStrategy strategy, string direction, string colour, int remaining, string reason)
    {
        var last = chartCandles[^1];
        var isSignal = direction is "CALL" or "PUT";
        if (!isSignal) { SetSignal(direction, colour, remaining, reason); return; }
        var candleKey = last.Time;
        if (remaining > 15) { SetSignal(direction + " ?", "#8DA3BF", remaining, "tentatif · " + reason); return; }
        if (remaining > 5) { SetSignal(direction + " ?", colour, remaining, "bersiap · " + reason); return; }
        SetSignal(direction, colour, remaining, reason);
        if (!firedStrategies.ContainsKey((strategy, candleKey)))
        {
            firedStrategies[(strategy, candleKey)] = direction;
            // Mirror ke firedEntries lama supaya panah entry konsisten di chart.
            firedEntries[candleKey] = direction;
            PushChartAlert("ENTRY " + direction, direction == "CALL" ? "UP" : "DOWN", candleTime: candleKey,
                $"{CurrentStrategySelection().Primary} · {reason}", force: true);
            DrawChart();
        }
    }

    private static double Atr(IReadOnlyList<Candle> candles, int period)
    {
        if (candles.Count <= period) return 0;
        var sum = 0d;
        for (var i = candles.Count - period; i < candles.Count; i++)
        {
            var c = candles[i]; var p = candles[i - 1];
            var tr = Math.Max(c.High - c.Low, Math.Max(Math.Abs(c.High - p.Close), Math.Abs(c.Low - p.Close)));
            sum += tr;
        }
        return sum / period;
    }

    /// Hurst exponent via rescaled range (R/S). H<0.5 mean-reverting,
    /// H=0.5 random walk, H>0.5 trending. Kasar tapi deterministik & tanpa lib.
    private static double HurstExponent(IReadOnlyList<double> series)
    {
        if (series.Count < 16) return 0.5;
        var n = series.Count;
        var mean = series.Average();
        var dev = series.Select(value => value - mean).ToList();
        var cum = new double[n]; var running = 0d;
        for (var i = 0; i < n; i++) { running += dev[i]; cum[i] = running; }
        var range = cum.Max() - cum.Min();
        var stddev = Math.Sqrt(dev.Sum(value => value * value) / n);
        var rs = stddev > 0 ? range / stddev : 0;
        return rs > 0 ? Math.Log(rs) / Math.Log(n / 2.0) : 0.5;
    }

    private void SetSignal(string text, string colour, int remaining, string reason)
    {
        var brush = (Brush)new BrushConverter().ConvertFromString(colour)!;
        SignalText.Text = text;
        SignalText.Foreground = brush;
        SignalClock.Text = TimeSpan.FromSeconds(remaining).ToString(@"m\:ss");
        SignalClock.Foreground = remaining <= 10 ? brush : (Brush)new BrushConverter().ConvertFromString("#E7EDF5")!;
        SignalReason.Text = reason;
    }

    /// Skor per (strategy, signal candle time). Entry candle = candle setelah
    /// sinyal; close candle itu vs open candle itu = WIN/LOSS/DRAW.
    private void ResolveEntries()
    {
        foreach (var (key, direction) in firedStrategies.ToList())
        {
            if (signalEntries.ContainsKey(key)) continue;
            var entryIndex = chartCandles.FindIndex(candle => candle.Time > key.SignalTime);
            if (entryIndex < 0 || entryIndex >= chartCandles.Count - 1) continue;
            var entry = chartCandles[entryIndex];
            var result = entry.Close > entry.Open ? "CALL" : entry.Close < entry.Open ? "PUT" : "DRAW";
            var outcome = result == "DRAW" ? "DRAW" : result == direction ? "WIN" : "LOSS";
            signalEntries[key] = (direction, entry.Open, entry.Close, outcome);
            AppendJournal(key.Strategy, key.SignalTime, direction, entry, outcome);
        }
        UpdateResultPanel();
    }

    private void AppendJournal(ActiveStrategy strategy, double signalTime, string direction, Candle entry, string outcome)
    {
        try
        {
            if (!File.Exists(JournalPath))
                File.WriteAllText(JournalPath, "strategy,signal_time,entry_time,symbol,direction,open,close,outcome\n");
            File.AppendAllText(JournalPath,
                FormattableString.Invariant(
                    $"{StrategyName(strategy)},{signalTime:0},{entry.Time:0},{selectedSymbol},{direction},{entry.Open},{entry.Close},{outcome}\n"));
        }
        catch (IOException) { /* best-effort */ }
    }

    private static string StrategyName(ActiveStrategy strategy) => strategy switch
    {
        ActiveStrategy.Trend => "TREND",
        ActiveStrategy.MomentumReversal => "MOMENTUM REVERSAL",
        ActiveStrategy.BollingerReentry => "BOLLINGER RE-ENTRY",
        ActiveStrategy.SrRejection => "S/R REJECTION",
        ActiveStrategy.VolatilityBreakout => "VOLATILITY BREAKOUT",
        ActiveStrategy.ZScore => "Z-SCORE",
        ActiveStrategy.EmaCross => "EMA-CROSS",
        ActiveStrategy.RsiReversal => "RSI-REVERSAL",
        ActiveStrategy.StructureBreak => "STRUCTURE-BREAK",
        _ => "OFF",
    };

    private void UpdateResultPanel()
    {
        var entries = signalEntries.Values;
        var win = entries.Count(value => value.Outcome == "WIN");
        var loss = entries.Count(value => value.Outcome == "LOSS");
        var draw = entries.Count(value => value.Outcome == "DRAW");
        var decided = win + loss;
        var total = signalEntries.Count;
        var pending = firedStrategies.Count - total;

        ResultTotalText.Text = $"{total + pending}";
        ResultWinText.Text = $"{win}";
        ResultLossText.Text = $"{loss}";
        ResultDrawText.Text = $"{draw}";

        if (decided == 0)
        {
            ResultRateText.Text = "—";
            ResultRateText.Foreground = (Brush)new BrushConverter().ConvertFromString("#9FB3CC")!;
            ResultNoteText.Text = pending > 0 ? $"{pending} entry menunggu hasil" : "Belum ada entry";
            return;
        }
        var rate = win * 100.0 / decided;
        ResultRateText.Text = $"{rate:0.0}%";
        ResultRateText.Foreground = (Brush)new BrushConverter().ConvertFromString(
            rate >= 55 ? "#25C89A" : rate >= 45 ? "#E7B65A" : "#F45B69")!;
        var reliability = decided < 30 ? "sampel terlalu kecil" : decided < 100 ? "sampel kecil" : "sampel memadai";
        ResultNoteText.Text = $"{decided} selesai · {reliability}" + (pending > 0 ? $" · {pending} menunggu" : "");
    }

    private void ResultReset_Click(object sender, RoutedEventArgs e)
    {
        firedStrategies.Clear();
        signalEntries.Clear();
        UpdateResultPanel();
        DrawChart();
    }

    private void OpenJournal_Click(object sender, RoutedEventArgs e)
    {
        if (!File.Exists(JournalPath)) { StatusText.Text = "Jurnal sinyal belum ada."; return; }
        try { Process.Start(new ProcessStartInfo(JournalPath) { UseShellExecute = true }); }
        catch (Exception error) { StatusText.Text = error.Message; }
    }

    private void PushChartAlert(string kind, string direction, double candleTime, string extra = "", bool force = false)    {
        var timeLabel = DateTimeOffset.FromUnixTimeSeconds((long)candleTime).LocalDateTime.ToString("HH:mm");
        var key = $"{selectedSymbol}|{kind}|{direction}|{timeLabel}|{extra}";
        if (!alertDedup.Add(key)) return;
        var arrow = direction == "UP" ? "▲" : direction == "DOWN" ? "▼" : "·";
        var message = $"{selectedSymbol} · {arrow} {kind} · {timeLabel}{(extra.Length == 0 ? "" : " · " + extra)}";
        chartAlerts.Insert(0, new ChartAlertRow(DateTime.Now.ToString("HH:mm:ss"), message));
        if (chartAlerts.Count > 60) chartAlerts.RemoveAt(chartAlerts.Count - 1);
        if (!chartAlertsReady && !force) return;
        ShowActiveAlert(DateTime.Now.ToString("HH:mm:ss"), message);
    }

    private static void PlayAlertSound()
    {
        try
        {
            if (File.Exists(AlertSoundPath))
            {
                using var player = new SoundPlayer(AlertSoundPath);
                player.Play();
                return;
            }
        }
        catch { }

        try { SystemSounds.Asterisk.Play(); } catch { }
    }

    private void ShowActiveAlert(string time, string message)
    {
        BottomAlertTime.Text = $"ALERT · {time}";
        BottomAlertMessage.Text = message;
        BottomAlertBorder.Visibility = Visibility.Visible;
        ChartAlertTime.Text = $"ALERT · {time}";
        ChartAlertMessage.Text = message;
        ChartAlertBorder.Visibility = Visibility.Visible;
        alertTimer.Stop();
        alertTimer.Start();
        PlayAlertSound();
    }

private sealed record Candle(double Time, double Open, double High, double Low, double Close);
    private sealed record SwingPoint(int Index, double Price, string Kind);
    private sealed record StructureMark(double Price, string Kind);
    private sealed record StructureMarkRow(string Symbol, int Minutes, double Time, double Price, string Kind);
    private sealed record StrategySelection(string Primary, string Filter);
    private sealed record StrategyEvent(int Index, string Direction, double Level, string Kind);
    private sealed record StructureAnalysis(List<SwingPoint> Swings, List<StrategyEvent> BosEvents, List<StrategyEvent> SmcEvents, string Trend);
    private sealed record MarketRow(string Symbol, string Name, string Group, string Price, string State, string Trend, bool IsFavorite)
    { public string FavoriteGlyph => IsFavorite ? "★" : "☆"; }
    private sealed record ChartAlertRow(string Time, string Message);
}

internal static class JsonElementExtensions
{
    internal static int GetArrayLengthSafe(this JsonElement element, string name) => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Array ? value.GetArrayLength() : 0;
}







