using Microsoft.Data.Sqlite;
using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Media;
using System.Net.Http;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Xml.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;
using System.Windows.Threading;

namespace OpenTerminal.Desktop;

public partial class MainWindow : Window
{
    private enum SplitScreenLayout { Full, ChartOnly, MarketList, Tools }
    private const double SplitScreenBreakpoint = 1180;
    private const double ReferenceWidth = 1600;
    private const double ReferenceHeight = 960;
    private const double BaseChartFontSize = 9;
    private const int SignalPreviewSeconds = 15;
    // Kontrak tunggal semua strategi: sinyal muncul sebelum close, lalu dikunci
    // pada jendela T-5..T-1 sebelum candle A tutup.
    private const int SignalLockSeconds = 5;
    // Lock pada jendela T-5..T-1. Polling feed tidak selalu tepat jatuh di
    // detik 05; jangan kehilangan sinyal hanya karena tick datang di T-4.
    private const int SignalMinimumEntryMarginSeconds = 1;
    private const string ApiUrl = "http://127.0.0.1:4000";
    private static readonly string Root = FindRoot();
    private readonly HttpClient http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly DispatcherTimer refreshTimer = new() { Interval = TimeSpan.FromSeconds(1) };
    private readonly DispatcherTimer apiMonitorTimer = new() { Interval = TimeSpan.FromSeconds(15) };
    private readonly DispatcherTimer alertTimer = new() { Interval = TimeSpan.FromSeconds(5) };
    private readonly DispatcherTimer tradingJournalSaveTimer = new() { Interval = TimeSpan.FromMilliseconds(600) };
    private string disclaimerText = "JUDOL";
    private static readonly string UiSettingsPath = System.IO.Path.Combine(Root, "data", "ui-settings.json");
    private sealed record UiSettings(bool DisclaimerVisible, string DisclaimerText, double? EntryPercent, double? OrderMinAmount, double? OrderMaxAmount);
    private readonly ObservableCollection<MarketRow> markets = [];
    private bool marketsRebuilding;
    private readonly ObservableCollection<NextSignalRow> nextSignals = [];
    private bool nextSignalsRebuilding;
    /// NEXT widget session state: prepare alerts fired per symbol (cap 10,
    /// cooldown 5 bars), independent of the selected-symbol lock path.
    private readonly Dictionary<string, int> nextPrepareCounts = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, double> nextPrepareLastTime = new(StringComparer.OrdinalIgnoreCase);
    private readonly HashSet<string> nextAlertDedup = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<MarketRow> allMarkets = [];
    private readonly HashSet<string> favoriteSymbols = new(StringComparer.OrdinalIgnoreCase);
    private List<Candle> sourceCandles = [];
    private List<Candle> chartCandles = [];
    private string apiKey = "";
    private Process? apiProcess;
    private DateTime nextApiStartAttempt = DateTime.MinValue;
    private bool apiStarting;
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
    private bool updatingAnimationActive;
    private bool layoutChanging;
    private IReadOnlyList<Candle> hoverCandles = [];
    private Func<int, double>? hoverX;
    private Func<double, double>? hoverY;
    private double hoverTop;
    private double hoverLeft;
    private double hoverWidth;
    private double hoverHeight;
    private SplitScreenLayout splitScreenLayout = SplitScreenLayout.Full;
    private bool strategySelectionChanging;
    private DateTime nextNewsRefresh = DateTime.MinValue;
    private DateTime nextMarketRefresh = DateTime.MinValue;
    private readonly ObservableCollection<ChartAlertRow> chartAlerts = [];
    private readonly HashSet<string> alertDedup = [];
    private double brokerOffset;
    private double lastTickTime;
    // Riwayat tick dalam memori untuk strategi berbasis tick. Hanya tick yang
    // benar-benar diterima feed; tidak ada tick sintetis dari OHLC.
    private readonly List<(double Time, double Price)> tickHistory = [];
    private const int TickHistoryCapacity = 900;
    private readonly Dictionary<(string Symbol, double CandleTime), (ActiveStrategy Strategy, string Direction)> lockedSignals = [];
    private string lastLiveSignalCandleKey = "";
    private string lastStructureSignalCandleKey = "";
    private string structureSignal = "NO TRADE";
    private string lastMomentumSignalCandleKey = "";
    private string momentumSignal = "NO TRADE";
    /// Locked entries keyed by candle time. Once written, never revised — that is
    /// what makes the arrow non-repainting even though it fires before the close.
    private readonly Dictionary<(string Symbol, double CandleTime), string> firedEntries = [];
    // Sinyal pada candle yang sudah selesai ditulis sekali. Redraw, tick baru,
    // atau pergantian overlay tidak boleh mengubah/menghapus arrow yang pernah
    // ditampilkan.
    private readonly Dictionary<(string Symbol, ActiveStrategy Strategy, double SignalTime), string> stableHistoricalArrows = [];
    private string currentPreviewDirection = "";
    private string currentPreviewReason = "";
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
    private static readonly string FusionJournalPath = System.IO.Path.Combine(Root, "data", "fusion-journal.csv");
    private static readonly string TradingJournalDirectory = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "OneDrive", "Dokumen", "JURNAL TRADING OPEN TERMINAL");
    private static readonly string TradingJournalPath = System.IO.Path.Combine(TradingJournalDirectory, "JURNAL TRADING OPEN TERMINAL.xlsx");
    private static readonly string LegacyStructureMarksPath = System.IO.Path.Combine(Root, "data", "structure-marks.json");
    private readonly SlShStore slShStore = new(System.IO.Path.Combine(Root, "data"));

    /// <summary>Strategi yang sedang menyala. Dipakai untuk evaluator + visibility widget.</summary>
    private enum ActiveStrategy { Off, Forex, ZScore, EmaCross, RsiReversal, StructureBreak, Trend, MomentumReversal, BollingerReentry, SrRejection, SnR5Break, VolatilityBreakout, DualThrust, Smc, RsiExtreme, LookAhead, DonchianRegime, BollingerRegime, TickReversal, SmcFusion }
    private sealed record ForexPlan(string Direction, double Entry, double StopLoss, double Tp1, double Tp2, double Tp3, double Fib382, double Fib50, double Fib618);
    /// <summary>Keyed by (strategy, signalTime) sehingga tiap strategi menulis jurnal sendiri.</summary>
    private readonly Dictionary<(string Symbol, ActiveStrategy Strategy, double SignalTime), (string Direction, double EntryOpen, double EntryClose, string Outcome)> signalEntries = [];
    private readonly Dictionary<(string Symbol, ActiveStrategy Strategy, double SignalTime), string> firedStrategies = [];
    private readonly HashSet<(string Symbol, ActiveStrategy Strategy, double SignalTime)> demoOrdersSent = [];
    /// <summary>Aturan entry DEMO (user rule, bisa diubah lewat menu ATURAN ENTRY).
    /// Nominal = saldo x EntryPercent, dijepit ke [batas server, OrderMaxAmount].
    /// Plafon keras default $20.000: entry lebih dari 1x saldo dipotong ke plafon.
    /// Tidak ada pengacakan — nominal sama untuk semua sinyal.</summary>
    private double demoEntryPercent = DefaultDemoEntryPercent;
    private double demoOrderMinAmount = DefaultDemoOrderMinAmount;
    private double demoOrderMaxAmount = DefaultDemoOrderMaxAmount;
    private const double DefaultDemoEntryPercent = 50.0;
    private const double DefaultDemoOrderMinAmount = 10.0;
    private const double DefaultDemoOrderMaxAmount = 20000.0;
    /// <summary>Batas bawah/atas nominal yang diterima server (pocket.ts): 1..50000.</summary>
    private const double DemoOrderAmountFloor = 1.0;
    private const double DemoOrderAmountCeiling = 50000.0;
    private const int DemoOrderExpirationSeconds = 60;

    /// <summary>Perhitungan nominal order. Murni fungsi supaya bisa diuji.
    /// Fail closed: saldo tidak tersedia atau nominal di bawah batas server → null
    /// (order ditunda, bukan diperkecil diam-diam).</summary>
    private static double? ComputeDemoOrderAmount(double balance, double percent, double maxAmount)
    {
        if (balance <= 0 || percent <= 0) return null;
        var raw = balance * percent / 100.0;
        if (raw < DemoOrderAmountFloor) return null;
        var max = Math.Clamp(maxAmount, DemoOrderAmountFloor, DemoOrderAmountCeiling);
        var rounded = Math.Round(raw / 10.0, MidpointRounding.AwayFromZero) * 10.0;
        return Math.Clamp(rounded, DemoOrderAmountFloor, max);
    }

    private double? NextDemoOrderAmount(decimal? balance) =>
        balance is > 0
            ? ComputeDemoOrderAmount((double)balance.Value, demoEntryPercent, demoOrderMaxAmount)
            : null;

    /// <summary>Jaga nilai aturan tetap di rentang yang valid setelah load
    /// dari settings atau setelah dialog disimpan.</summary>
    private void ClampDemoOrderRules()
    {
        demoEntryPercent = Math.Clamp(demoEntryPercent, 1, 100);
        demoOrderMaxAmount = Math.Clamp(demoOrderMaxAmount, DemoOrderAmountFloor, DemoOrderAmountCeiling);
    }
    private readonly Dictionary<(string Symbol, double SignalTime), ForexPlan> forexPlans = [];

    /// Broker clock runs ahead of this machine's, so candle boundaries must be
    /// derived from feed tick time. A 1M entry needs this exact, not close.
    private long BrokerNow() => DateTimeOffset.UtcNow.ToUnixTimeSeconds() + (long)brokerOffset;

    private int SecondsLeftInCandle()
    {
        var duration = TimeframeMinutes() * 60;
        return (int)Math.Max(0, duration - BrokerNow() % duration);
    }
    private bool chartAlertsReady;
    private bool loadingTradingJournal;
    private const string AlertSoundPath = @"Z:\MUSIC\Notifikasi\Bell Dor.wav";
    /// Minimum candles between signals. Matches the cooldown the rule was
    /// backtested with; changing it here invalidates that measurement.
    private const int SignalCooldownBars = 5;
    /// Session guard (user rule, 2026-11-06): maximum locked entries per
    /// symbol+strategy session. This is a risk guard only — it does not
    /// create edge and is not part of any backtest. After the cap, live
    /// locking stops (fail closed) until the strategy changes or the
    /// result panel is reset. Historical playback is not capped.
    private const int SessionMaxEntries = 10;
    private readonly Dictionary<(string Symbol, ActiveStrategy Strategy), int> sessionEntryCounts = [];
    private static readonly bool LegacyAnalysisEnabled = true;

    public MainWindow()
    {
        InitializeComponent();
        tradingJournalSaveTimer.Tick += (_, _) => SaveTradingJournalEntry();
        TradingJournalDate.SelectedDate = DateTime.Today;
        LoadTradingJournalEntry();
        Closing += (_, _) => { if (tradingJournalSaveTimer.IsEnabled) SaveTradingJournalEntry(); };
        LoadFavorites();
        LoadStructureMarks();
        LoadUiSettings();
        ApplyDisclaimerVisual();
#if DEBUG
        VerifyNonRepaintStructure();
        VerifySmcRegimeSnapshot();
        VerifyTickReversal();
        VerifySmcFusionScoring();
#endif
        UpdateStrategyControls();
        HideLegacyAnalysisControls();
        MarketList.ItemsSource = markets;
        NextSignalList.ItemsSource = nextSignals;
        ChartAlertList.ItemsSource = chartAlerts;
        refreshTimer.Tick += async (_, _) => await RefreshFeed();
        apiMonitorTimer.Tick += async (_, _) =>
        {
            if (!await Online()) StartApi();
        };
        // Kedua border alert harus ditutup. Versi lama hanya menutup BottomAlertBorder,
        // sehingga alert di area chart menempel selamanya setelah kemunculan pertama.
        alertTimer.Tick += (_, _) =>
        {
            alertTimer.Stop();
            BottomAlertBorder.Visibility = Visibility.Collapsed;
            ChartAlertBorder.Visibility = Visibility.Collapsed;
        };
        Loaded += async (_, _) => await StartAsync();
    }

    private void Window_SizeChanged(object sender, SizeChangedEventArgs e)
    {
        if (ActualWidth <= 0 || ActualHeight <= 0) return;

        var compact = ActualWidth < SplitScreenBreakpoint;
        StatusText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        ConnectionStatusBadge.Visibility = Visibility.Visible;
        DataOnlyLabelText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        SelectedNameText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        ChartHeaderText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        TimeframeLabelText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        ChartGestureHintText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;
        RealtimeLabelText.Visibility = compact ? Visibility.Collapsed : Visibility.Visible;

        if (compact)
        {
            ChartToolbarGrid.ColumnDefinitions[0].Width = GridLength.Auto;
            ChartToolbarGrid.ColumnDefinitions[2].Width = GridLength.Auto;
            TimeframeBox.Width = 54;
            ApplySplitScreenLayout(splitScreenLayout);
            ApplyResponsiveLayout();
            return;
        }

        ChartToolbarGrid.ColumnDefinitions[0].Width = new GridLength(1, GridUnitType.Star);
        ChartToolbarGrid.ColumnDefinitions[2].Width = new GridLength(1, GridUnitType.Star);
        TimeframeBox.Width = 66;
        ChartColumn.Width = new GridLength(1, GridUnitType.Star);
        MarketListPanel.Visibility = Visibility.Visible;
        SideToolsPanel.Visibility = Visibility.Visible;
        SplitChartOnlyMenuItem.IsChecked = false;
        SplitMarketMenuItem.IsChecked = false;
        SplitToolsMenuItem.IsChecked = false;
        ApplyResponsiveLayout();
    }

    private void ApplyResponsiveLayout()
    {
        if (ActualWidth <= 0 || ActualHeight <= 0) return;
        var widthScale = Math.Clamp(ActualWidth / ReferenceWidth, 0.4, 1.6);
        var heightScale = Math.Clamp(ActualHeight / ReferenceHeight, 0.4, 1.6);
        var scale = (widthScale + heightScale) / 2;
        if (ActualWidth >= SplitScreenBreakpoint)
        {
            MarketColumn.Width = new GridLength(Math.Clamp(232 * widthScale, 150, 380));
            ToolsColumn.Width = new GridLength(Math.Clamp(305 * widthScale, 200, 520));
            ChartColumn.Width = new GridLength(1, GridUnitType.Star);
        }
        if (WorkspaceGrid.Children.Count > 1 && WorkspaceGrid.Children[1] is Grid centerGrid && centerGrid.RowDefinitions.Count >= 3)
            centerGrid.RowDefinitions[2].Height = new GridLength(Math.Clamp(132 * heightScale, 88, 260));
        if (SideToolsPanel.Child is Grid sideGrid && sideGrid.RowDefinitions.Count >= 3)
        {
            var sideHeight = Math.Clamp(230 * heightScale, 130, 380);
            sideGrid.RowDefinitions[0].Height = new GridLength(sideHeight);
            sideGrid.RowDefinitions[2].Height = new GridLength(sideHeight);
        }
        SelectedSymbolText.FontSize = Math.Clamp(20 * widthScale, 11, 28);
        SignalText.FontSize = Math.Clamp(34 * scale, 18, 48);
        SignalClock.FontSize = Math.Clamp(46 * scale, 24, 64);
        ResultRateText.FontSize = Math.Clamp(38 * scale, 20, 52);
        QuoteText.FontSize = Math.Clamp(19 * scale, 11, 26);
        QuoteArrow.FontSize = Math.Clamp(26 * scale, 14, 26);
    }

    private void ApplySplitScreenLayout(SplitScreenLayout layout)
    {
        if (layoutChanging) return;
        layoutChanging = true;
        try
        {
        splitScreenLayout = layout;
        if (layout == SplitScreenLayout.Full)
        {
            MarketListPanel.Visibility = Visibility.Visible;
            SideToolsPanel.Visibility = Visibility.Visible;
            SplitChartOnlyMenuItem.IsChecked = false;
            SplitMarketMenuItem.IsChecked = false;
            SplitToolsMenuItem.IsChecked = false;
            ApplyResponsiveLayout();
            return;
        }

        MarketColumn.Width = layout == SplitScreenLayout.MarketList ? new GridLength(232) : new GridLength(0);
        ToolsColumn.Width = layout == SplitScreenLayout.Tools ? new GridLength(290) : new GridLength(0);
        ChartColumn.Width = new GridLength(1, GridUnitType.Star);
        MarketListPanel.Visibility = layout == SplitScreenLayout.MarketList ? Visibility.Visible : Visibility.Collapsed;
        SideToolsPanel.Visibility = layout == SplitScreenLayout.Tools ? Visibility.Visible : Visibility.Collapsed;
        SplitChartOnlyMenuItem.IsChecked = layout == SplitScreenLayout.ChartOnly;
        SplitMarketMenuItem.IsChecked = layout == SplitScreenLayout.MarketList;
        SplitToolsMenuItem.IsChecked = layout == SplitScreenLayout.Tools;
        }
        finally
        {
            layoutChanging = false;
        }
    }

    private void SplitViewChanged(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded || layoutChanging || sender is not MenuItem selected) return;
        var layout = selected == SplitChartOnlyMenuItem
            ? SplitScreenLayout.ChartOnly
            : selected == SplitMarketMenuItem
                ? SplitScreenLayout.MarketList
                : SplitScreenLayout.Tools;
        ApplySplitScreenLayout(selected.IsChecked ? layout : SplitScreenLayout.Full);
    }

    private static string FindRoot()
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
            if (File.Exists(System.IO.Path.Combine(directory.FullName, "Jalankan-API.cmd"))) return directory.FullName;
        return AppContext.BaseDirectory;
    }

    private async Task StartAsync()
    {
        SetStartupBlank(true);
        try
        {
            var keyFile = System.IO.Path.Combine(Root, "data", ".api-key");
            apiKey = File.Exists(keyFile) ? await File.ReadAllTextAsync(keyFile) : "";
            if (!await Online()) StartApi();
            for (var attempt = 0; attempt < 20 && !await Online(); attempt++) await Task.Delay(750);
            if (File.Exists(keyFile)) apiKey = await File.ReadAllTextAsync(keyFile);
            await RefreshFeed();
            refreshTimer.Start();
            apiMonitorTimer.Start();
        }
        catch (Exception error) { SetStatus("disconnected", error.Message); }
    }

    private void SetStartupBlank(bool enabled)
    {
        if (enabled)
        {
            StartupBlankOverlay.Visibility = Visibility.Visible;
            StartupBlankOverlay.Opacity = 1;
            var pulse = new DoubleAnimation(0, 158, TimeSpan.FromSeconds(1.1))
            {
                RepeatBehavior = RepeatBehavior.Forever,
                AutoReverse = true
            };
            StartupLoadingPulse.BeginAnimation(WidthProperty, pulse);
            StartupScanTransform.BeginAnimation(TranslateTransform.YProperty,
                new DoubleAnimation(0, Math.Max(0, StartupBlankOverlay.ActualHeight), TimeSpan.FromSeconds(2.2))
                {
                    RepeatBehavior = RepeatBehavior.Forever,
                    EasingFunction = new SineEase { EasingMode = EasingMode.EaseInOut }
                });
            var glow = new DoubleAnimation(.35, .95, TimeSpan.FromSeconds(.8))
            {
                RepeatBehavior = RepeatBehavior.Forever,
                AutoReverse = true
            };
            StartupLoadingText.BeginAnimation(OpacityProperty, glow);
            return;
        }

        StartupLoadingPulse.BeginAnimation(WidthProperty, null);
        StartupScanTransform.BeginAnimation(TranslateTransform.YProperty, null);
        StartupLoadingText.BeginAnimation(OpacityProperty, null);
        var fade = new DoubleAnimation(1, 0, TimeSpan.FromMilliseconds(360));
        fade.Completed += (_, _) =>
        {
            StartupBlankOverlay.Opacity = 1;
            StartupBlankOverlay.Visibility = Visibility.Collapsed;
        };
        StartupBlankOverlay.BeginAnimation(OpacityProperty, fade);
    }

    private void StartApi()
    {
        if (apiStarting || apiProcess is { HasExited: false } || DateTime.UtcNow < nextApiStartAttempt) return;
        apiStarting = true;
        nextApiStartAttempt = DateTime.UtcNow.AddSeconds(30);
        try
        {
            var server = System.IO.Path.Combine(Root, "server", "dist", "index.js");
            if (!File.Exists(server)) return;
            var node = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "nodejs", "node.exe");
            if (!File.Exists(node)) node = "node.exe";
            apiProcess?.Dispose();
            apiProcess = Process.Start(new ProcessStartInfo(node, $"\"{server}\"")
            {
                WorkingDirectory = Root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            });
            if (apiProcess is null) return;
            apiProcess.OutputDataReceived += (_, _) => { };
            apiProcess.ErrorDataReceived += (_, _) => { };
            apiProcess.BeginOutputReadLine();
            apiProcess.BeginErrorReadLine();
        }
        finally
        {
            apiStarting = false;
        }
    }

    public void StopManagedApi()
    {
        apiMonitorTimer.Stop();
        try
        {
            if (apiProcess is { HasExited: false })
            {
                apiProcess.Kill(entireProcessTree: true);
                apiProcess.WaitForExit(3000);
            }
        }
        catch (Exception error)
        {
            SetStatus("disconnected", $"API shutdown: {error.Message}");
        }
        finally
        {
            apiProcess?.Dispose();
            apiProcess = null;
        }
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
        if (string.IsNullOrWhiteSpace(apiKey))
        {
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", 0, "API key tidak tersedia · cek konfigurasi lokal");
            return;
        }
        if (loading) { refreshQueued = true; return; }
        loading = true;
        var started = DateTime.UtcNow;
        try
        {
            var requestedSymbol = selectedSymbol;
            var watchlist = WatchlistSymbols();
            var watchlistQuery = watchlist.Count == 0 ? "" : $"&symbols={Uri.EscapeDataString(string.Join(",", watchlist))}";
            using var document = await GetPocket($"/api/pocket/snapshot?symbol={Uri.EscapeDataString(requestedSymbol)}{watchlistQuery}");
            if (requestedSymbol != selectedSymbol) { refreshQueued = true; return; }
            var feed = document.RootElement;
            var status = Text(feed, "status");
            var receivedAt = feed.TryGetProperty("receivedAt", out var received) && received.ValueKind == JsonValueKind.Number ? DateTimeOffset.FromUnixTimeMilliseconds(received.GetInt64()).LocalDateTime : DateTime.MinValue;
            SetStatus(status, $"Market feed · {feed.GetArrayLengthSafe("markets")} market · update {receivedAt:HH:mm:ss} · {(int)(DateTime.UtcNow - started).TotalMilliseconds}ms");
            UpdateAccountBalance(feed);
            if (DateTime.UtcNow >= nextMarketRefresh)
            {
                UpdateMarkets(feed);
                UpdateTape(feed);
                nextMarketRefresh = DateTime.UtcNow.AddSeconds(5);
            }
            UpdateSelected(feed);
            if (DateTime.UtcNow >= nextNewsRefresh) _ = RefreshNews();
        }
        catch (Exception error)
        {
            SetStatus("disconnected", error.Message);
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", 0, "Feed tidak tersedia · tunggu data valid");
            DrawChart();
        }
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

    private decimal? sessionStartBalance;
    private decimal? lastBalance;
    private string lastBalanceCurrency = "USD";

    private void BalanceMask_Changed(object sender, RoutedEventArgs e)
    {
        var masked = BalanceMaskToggle.IsChecked == true;
        BalanceMaskSlash.Visibility = masked ? Visibility.Visible : Visibility.Collapsed;
        AccountBalanceText.Text = lastBalance is null ? "$—" : masked ? "••••••" : FormatBalance(lastBalance.Value, lastBalanceCurrency);
        AccountBalanceDelta.Visibility = masked ? Visibility.Collapsed : Visibility.Visible;
    }

    private static string FormatBalance(decimal amount, string currency) => $"{(currency == "USD" ? "$" : currency + " ")}{amount:N2}";

    private void UpdateAccountBalance(JsonElement feed)
    {
        if (feed.TryGetProperty("balance", out var balance) && balance.ValueKind == JsonValueKind.Number && balance.TryGetDecimal(out var amount))
        {
            var currency = Text(feed, "currency");
            lastBalance = amount;
            lastBalanceCurrency = currency;
            AccountBalanceText.Text = BalanceMaskToggle.IsChecked == true ? "••••••" : FormatBalance(amount, currency);
            sessionStartBalance ??= amount;
            UpdateBalanceDelta(amount - sessionStartBalance.Value, sessionStartBalance.Value);
            return;
        }
        AccountBalanceText.Text = "$—";
        AccountBalanceDelta.Text = "SESI —";
        AccountBalanceDelta.Foreground = BalanceNeutralBrush;
    }

    private static readonly SolidColorBrush BalanceNeutralBrush = Frozen("#9FB3CC");
    private static readonly SolidColorBrush BalanceUpBrush = Frozen("#25C89A");
    private static readonly SolidColorBrush BalanceDownBrush = Frozen("#F26D78");

    private static SolidColorBrush Frozen(string hex)
    {
        var brush = (SolidColorBrush)new BrushConverter().ConvertFromString(hex)!;
        brush.Freeze();
        return brush;
    }

    private void UpdateBalanceDelta(decimal delta, decimal start)
    {
        var percent = start == 0 ? 0 : delta / start * 100;
        var (arrow, brush) = delta > 0 ? ("▲", BalanceUpBrush) : delta < 0 ? ("▼", BalanceDownBrush) : ("■", BalanceNeutralBrush);
        AccountBalanceDelta.Text = $"{arrow} {delta:+#,##0.00;-#,##0.00;0.00} ({percent:+0.00;-0.00;0.00}%) · SESI";
        AccountBalanceDelta.Foreground = brush;
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

    private void UpdateSelected(JsonElement feed)
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
                RecordTick(tickTime.Value, price.Value);
                var candleTime = Math.Floor(tickTime.Value / 60) * 60;
                var current = latestCandles[^1];
                if (candleTime > current.Time)
                    latestCandles.Add(new Candle(candleTime, price.Value, price.Value, price.Value, price.Value));
                else if (candleTime == current.Time)
                    latestCandles[^1] = current with { High = Math.Max(current.High, price.Value), Low = Math.Min(current.Low, price.Value), Close = price.Value };
            }
        }
        UpdateClosedCandleSignal(latestCandles);
        if (latestCandles.Count < 2)
        {
            var closed = !feed.TryGetProperty("ticks", out var ticks) || !ticks.TryGetProperty(selectedSymbol, out _);
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", 0, closed ? "Market closed · tidak ada entry" : "Candle M1 belum tersedia");
            PriceChart.Visibility = Visibility.Collapsed;
            QuoteText.Text = closed ? "Market\nClosed" : "Memuat\nCandle M1 belum tersedia";
            UpdateLiveCandle(null, 0);
            QuoteFlipText.Inlines.Clear();
            ChartLoadingText.Text = closed ? "Market closed" : "Memuat market...";
            ChartLoadingText.Foreground = (Brush)new BrushConverter().ConvertFromString(closed ? "#F26D78" : "#8492A6")!;
            ChartInfoText.Text = $"{selectedSymbol} · {(closed ? "market closed" : "memuat candle")}";
            ChartStatusOverlay.Visibility = Visibility.Visible;
            return;
        }
        sourceCandles = latestCandles;
        chartCandles = Aggregate(sourceCandles, TimeframeMinutes());
        ChartStatusOverlay.Visibility = Visibility.Collapsed;
        LockSlShMarks();
        if (AutoEndBox.IsChecked == true || firstVisible + visibleBars >= chartCandles.Count - 2) firstVisible = Math.Max(0, chartCandles.Count - visibleBars);
        var last = chartCandles.LastOrDefault();
        var first = chartCandles.FirstOrDefault();
        var change = first is not null && last is not null ? (last.Close / first.Open - 1) * 100 : 0;
        QuoteText.Text = last is null ? "Menunggu" : FormatPrice(last.Close);
        UpdateLiveCandle(last, change);
        UpdateBinaryMetrics(last);
        UpdateMarketQuality(feed, last);
        FeedText.Text = $"MODE      {Text(feed, "mode")}\nACCESS    DEMO ONLY\nSTATUS    {Text(feed, "status").ToUpperInvariant()}\nMARKETS   {feed.GetArrayLengthSafe("markets")}\nLIVE      {feed.GetArrayLengthSafe("liveSymbols")}\nCANDLE    M1 persisted\n\nBroker data + order demo.\nLIVE account ditolak.";
        PriceChart.Visibility = Visibility.Visible;
        // Resolve dulu supaya cincin hasil (WIN/LOSS) ikut tergambar pada redraw ini.
        ResolveEntries();
        UpdateEntrySignal(feed);
        UpdateNextSignals(feed);
        SubmitDueDemoOrders();
        DrawChart();
        if (PriceChart.Children.Count > 0)
        {
            SetStartupBlank(false);
        }
    }

    /// Watchlist for the NEXT widget: favorites that exist in the broker
    /// market list plus the selected symbol, capped at 10. Favorites are
    /// the user's own "all markets to watch" (star in Market Explorer).
    private List<string> WatchlistSymbols()
    {
        var known = allMarkets.Select(row => row.Symbol).ToHashSet(StringComparer.OrdinalIgnoreCase);
        var list = favoriteSymbols.Where(symbol => known.Contains(symbol)).ToList();
        if (!list.Contains(selectedSymbol, StringComparer.OrdinalIgnoreCase)) list.Add(selectedSymbol);
        return list.Take(10).ToList();
    }

    /// NEXT widget: scan the watchlist with the same Z-SCORE rules as the
    /// SIGNAL panel (z beyond +/-2, vol HI) and surface a PREPARE alert
    /// inside the T-15s window before candle A closes, for entry on candle
    /// B. Fail closed: no candles / stale tick / unknown symbol = WAIT,
    /// never a guessed direction. This widget only previews; locking and
    /// session caps for the selected symbol stay in UpdateEntrySignal.
    private void UpdateNextSignals(JsonElement feed)
    {
        if (NextSignalList is null) return;
        var remaining = SecondsLeftInCandle();
        var watchlist = WatchlistSymbols();
        if (watchlist.Count == 0)
        {
            nextSignals.Clear();
            NextSignalStatus.Text = "Watchlist kosong · bintangkan market di Market Explorer";
            return;
        }
        if (!string.Equals(Text(feed, "status"), "live", StringComparison.OrdinalIgnoreCase))
        {
            NextSignalStatus.Text = $"Feed {Text(feed, "status")} · NEXT menunggu data live";
            return;
        }
        var candleMap = feed.TryGetProperty("candles", out var cm) ? cm : default;
        var tickMap = feed.TryGetProperty("ticks", out var tm) ? tm : default;
        var rows = new List<NextSignalRow>();
        foreach (var symbol in watchlist)
        {
            if (candleMap.ValueKind != JsonValueKind.Object || !candleMap.TryGetProperty(symbol, out var candleArray))
            {
                rows.Add(new NextSignalRow(symbol, "—", "WAIT", "Candle belum dimuat", "#59728E"));
                continue;
            }
            var candles = candleArray.EnumerateArray()
                .Select(c => new Candle(Number(c, "time") ?? 0, Number(c, "open") ?? 0, Number(c, "high") ?? 0, Number(c, "low") ?? 0, Number(c, "close") ?? 0))
                .Where(c => c.Time > 0 && c.Close > 0).ToList();
            if (tickMap.ValueKind == JsonValueKind.Object && tickMap.TryGetProperty(symbol, out var tick))
            {
                var price = Number(tick, "price");
                var tickTime = Number(tick, "time");
                if (price is > 0 && tickTime is > 0 && candles.Count > 0)
                {
                    var candleTime = Math.Floor(tickTime.Value / 60) * 60;
                    var current = candles[^1];
                    if (candleTime > current.Time)
                        candles.Add(new Candle(candleTime, price.Value, price.Value, price.Value, price.Value));
                    else if (candleTime == current.Time)
                        candles[^1] = current with { High = Math.Max(current.High, price.Value), Low = Math.Min(current.Low, price.Value), Close = price.Value };
                }
            }
            if (candles.Count < 61)
            {
                rows.Add(new NextSignalRow(symbol, "—", "WAIT", $"Butuh 61 candle · ada {candles.Count}", "#59728E"));
                continue;
            }
            var last = candles[^1];
            var direction = EvaluateZScoreVolatility(candles, last, out var reason);
            if (direction is null)
            {
                var shortReason = reason.Length > 56 ? reason.Substring(0, 56) : reason;
                rows.Add(new NextSignalRow(symbol, "—", "NO TRADE", shortReason, "#59728E"));
                continue;
            }
            var colour = direction == "CALL" ? "#25C89A" : "#F45B69";
            var prepareCount = nextPrepareCounts.TryGetValue(symbol, out var count) ? count : 0;
            if (prepareCount >= SessionMaxEntries)
            {
                rows.Add(new NextSignalRow(symbol, direction, "SESSION MAX", $"Batas {SessionMaxEntries} prepare per sesi", "#E7B65A"));
                continue;
            }
            var sameCandlePrepare = nextPrepareLastTime.TryGetValue(symbol, out var lastPrepare) && lastPrepare == last.Time;
            if (nextPrepareLastTime.TryGetValue(symbol, out var lastPrepareTime)
                && !sameCandlePrepare
                && (last.Time - lastPrepareTime) / 60.0 < SignalCooldownBars)
            {
                rows.Add(new NextSignalRow(symbol, direction, "COOLDOWN", $"{SignalCooldownBars} candle · {reason}", "#59728E"));
                continue;
            }
            if (remaining > SignalPreviewSeconds)
            {
                rows.Add(new NextSignalRow(symbol, direction, $"SIAP · T-{remaining}s", reason, "#9FB3CC"));
                continue;
            }
            rows.Add(new NextSignalRow(symbol, direction, $"PREPARE · T-{remaining}s", reason, colour));
            if (!sameCandlePrepare)
            {
                nextPrepareCounts[symbol] = prepareCount + 1;
                nextPrepareLastTime[symbol] = last.Time;
            }
            var dedupKey = $"{symbol}|{last.Time:0}|{direction}";
            if (nextAlertDedup.Add(dedupKey))
            {
                var message = $"{symbol} · PREPARE {direction} · entry candle B · {reason}";
                chartAlerts.Insert(0, new ChartAlertRow(DateTime.Now.ToString("HH:mm:ss"), message));
                if (chartAlerts.Count > 60) chartAlerts.RemoveAt(chartAlerts.Count - 1);
                ShowActiveAlert(DateTime.Now.ToString("HH:mm:ss"), message);
            }
        }
        var prepareRows = rows.Count(row => row.Status.StartsWith("PREPARE", StringComparison.Ordinal));
        NextSignalStatus.Text = $"{watchlist.Count} market · {prepareRows} PREPARE · T-{remaining}s · Z-SCORE";
        // Rebuild daftar adalah pembaruan otomatis, bukan pilihan user. Selama
        // rebuild, handler pemilihan dimatikan supaya market yang sedang dibuka
        // tidak ikut berpindah; yang dipilih ulang adalah simbol AKTIF, bukan
        // baris pertama daftar.
        nextSignalsRebuilding = true;
        try
        {
            nextSignals.Clear();
            foreach (var row in rows.OrderByDescending(row => row.Status.StartsWith("PREPARE", StringComparison.Ordinal)).ThenBy(row => row.Symbol))
                nextSignals.Add(row);
            var idx = nextSignals.ToList().FindIndex(row => row.Symbol.Equals(selectedSymbol, StringComparison.OrdinalIgnoreCase));
            NextSignalList.SelectedIndex = idx;
        }
        finally { nextSignalsRebuilding = false; }
    }

    private async void NextSignalList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (nextSignalsRebuilding) return;
        if (NextSignalList.SelectedItem is NextSignalRow row && !row.Symbol.Equals(selectedSymbol, StringComparison.OrdinalIgnoreCase))
            await SwitchSymbolAsync(row.Symbol);
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
        marketsRebuilding = true;
        try
        {
            var query = SearchBox.Text.Trim();
            var selected = allMarkets.Where(row => MarketMatchesCategory(row, groupFilter) && (query.Length == 0 || row.Symbol.Contains(query, StringComparison.OrdinalIgnoreCase) || row.Name.Contains(query, StringComparison.OrdinalIgnoreCase))).OrderBy(row => row.Symbol).ToList();
            markets.Clear(); foreach (var row in selected) markets.Add(row);
            MarketCountText.Text = query.Length > 0 ? $"Saran market · {selected.Count} cocok" : $"{selected.Count} market";
            MarketList.SelectedItem = markets.FirstOrDefault(row => row.Symbol.Equals(selectedSymbol, StringComparison.OrdinalIgnoreCase));
        }
        finally
        {
            marketsRebuilding = false;
        }
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
        if (ForexStrategyBox.IsChecked == true) yield return ActiveStrategy.Forex;
        if (ZScoreStrategyBox.IsChecked == true) yield return ActiveStrategy.ZScore;
        if (TrendStrategyBox.IsChecked == true) yield return ActiveStrategy.Trend;
        if (MomentumStrategyBox.IsChecked == true) yield return ActiveStrategy.MomentumReversal;
        if (BollingerStrategyBox.IsChecked == true) yield return ActiveStrategy.BollingerReentry;
        if (RejectionStrategyBox.IsChecked == true) yield return ActiveStrategy.SrRejection;
        if (SnR5StrategyBox.IsChecked == true) yield return ActiveStrategy.SnR5Break;
        if (BreakoutStrategyBox.IsChecked == true) yield return ActiveStrategy.VolatilityBreakout;
        if (DualThrustStrategyBox.IsChecked == true) yield return ActiveStrategy.DualThrust;
        if (RsiExtremeStrategyBox.IsChecked == true) yield return ActiveStrategy.RsiExtreme;
        if (LookAheadStrategyBox.IsChecked == true) yield return ActiveStrategy.LookAhead;
        if (SmcOverlayBox.IsChecked == true) yield return ActiveStrategy.Smc;
        if (DonchianRegimeStrategyBox.IsChecked == true) yield return ActiveStrategy.DonchianRegime;
        if (BollingerRegimeStrategyBox.IsChecked == true) yield return ActiveStrategy.BollingerRegime;
        if (TickReversalStrategyBox.IsChecked == true) yield return ActiveStrategy.TickReversal;
        if (SmcFusionStrategyBox.IsChecked == true) yield return ActiveStrategy.SmcFusion;
    }

    private bool HasActiveChartLayer() => EnabledStrategies().Any()
        || DirectionOverlayBox.IsChecked == true
        || RsiOverlayBox.IsChecked == true
        || BollingerOverlayBox.IsChecked == true
        || AtrOverlayBox.IsChecked == true
        || LevelsOverlayBox.IsChecked == true
        || StructureOverlayBox.IsChecked == true
        || SlShOverlayBox.IsChecked == true
        || TrendChannelOverlayBox.IsChecked == true;

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

    private void SignalStrategyChanged(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded || strategySelectionChanging) return;
        strategySelectionChanging = true;
        try
        {
            if (sender is MenuItem selected && selected.IsChecked == true)
            {
                foreach (var item in SignalStrategyItems())
                    if (!ReferenceEquals(item, selected)) item.IsChecked = false;
            }
        }
        finally { strategySelectionChanging = false; }

        // Sinyal, arrow, dan statistik adalah state sesi strategi aktif. Saat
        // strategi dimatikan/diganti, jangan bawa artefak strategi sebelumnya
        // ke chart/result baru. Jurnal permanen tetap tersimpan di disk.
        ClearActiveStrategySession();
        currentPreviewDirection = "";
        SetSignal(CurrentActiveStrategy() == ActiveStrategy.Off ? "OFF" : "WAIT", "#E7B65A", SecondsLeftInCandle(), "Strategi berubah · menunggu tick valid");
        UpdateStrategyControls();
        DrawChart();
        UpdateResultPanel();
    }

    private void ClearActiveStrategySession()
    {
        lockedSignals.Clear();
        firedEntries.Clear();
        firedStrategies.Clear();
        signalEntries.Clear();
        demoOrdersSent.Clear();
        forexPlans.Clear();
        stableHistoricalArrows.Clear();
        sessionEntryCounts.Clear();
        nextPrepareCounts.Clear();
        nextPrepareLastTime.Clear();
        nextAlertDedup.Clear();
        nextSignals.Clear();
        currentPreviewDirection = "";
        currentPreviewReason = "";
    }

    private void RecordTick(double time, double price)
    {
        if (!double.IsFinite(time) || !double.IsFinite(price) || price <= 0) return;
        if (tickHistory.Count > 0 && time < tickHistory[^1].Time) return; // urutan waktu wajib naik
        tickHistory.Add((time, price));
        if (tickHistory.Count > TickHistoryCapacity) tickHistory.RemoveRange(0, tickHistory.Count - TickHistoryCapacity);
    }

    private const double TickPressureSeconds = 60;
    private const double TickConfirmSeconds = 10;
    private const double TickMinImbalance = .60;
    private const double TickMinEfficiency = .50;
    private const double TickRetraceFraction = .20;
    private const int TickMinPressureMoves = 20;
    private const int TickMinConfirmUpdates = 5;
    private const double TickMaxGapSeconds = 3;
    private const double TickMaxStaleSeconds = 2;

    /// Tick Exhaustion Reversal v1 (UNPROVEN). Membaca tick nyata saja:
    /// pressure = [t-60, t-10], confirmation = (t-10, t]. Sinyal hanya muncul bila
    /// dorongan satu arah dominan dan terarah, lalu sebagian ditarik kembali.
    /// Tidak ada tick sintetis dari OHLC; data bolong/stale = WAIT.
    private (string? Direction, string Reason) TickExhaustionDirection(double decisionTime)
        => EvaluateTickReversal(tickHistory, decisionTime);

    private static (string? Direction, string Reason) EvaluateTickReversal(
        IReadOnlyList<(double Time, double Price)> ticks, double decisionTime)
    {
        var window = ticks.Where(tick => tick.Time > decisionTime - TickPressureSeconds && tick.Time <= decisionTime).ToList();
        if (window.Count < 2) return (null, "TICK · riwayat tick belum cukup");
        var pressureEnd = decisionTime - TickConfirmSeconds;
        var pressure = window.Where(tick => tick.Time <= pressureEnd).ToList();
        var confirm = window.Where(tick => tick.Time > pressureEnd).ToList();
        if (pressure.Count < 2) return (null, "TICK · pressure kosong");
        if (confirm.Count < TickMinConfirmUpdates) return (null, $"TICK · konfirmasi {confirm.Count}/{TickMinConfirmUpdates} update");
        if (decisionTime - window[^1].Time > TickMaxStaleSeconds) return (null, "TICK · data stale");

        for (var index = 1; index < window.Count; index++)
            if (window[index].Time - window[index - 1].Time > TickMaxGapSeconds)
                return (null, "TICK · ada jeda antar-tick");

        var ups = 0; var downs = 0;
        for (var index = 1; index < pressure.Count; index++)
        {
            var delta = pressure[index].Price - pressure[index - 1].Price;
            if (delta > 0) ups++; else if (delta < 0) downs++;
        }
        var moves = ups + downs;
        if (moves < TickMinPressureMoves) return (null, $"TICK · pressure {moves}/{TickMinPressureMoves} gerak");
        var imbalance = (double)(ups - downs) / moves;
        var pathLength = 0d;
        for (var index = 1; index < pressure.Count; index++) pathLength += Math.Abs(pressure[index].Price - pressure[index - 1].Price);
        if (pathLength <= 0) return (null, "TICK · harga pressure flat");
        var netMove = pressure[^1].Price - pressure[0].Price;
        var efficiency = Math.Abs(netMove) / pathLength;
        if (efficiency < TickMinEfficiency) return (null, $"TICK · efisiensi {efficiency:0.00} < {TickMinEfficiency:0.00}");

        var high = pressure.Max(tick => tick.Price);
        var low = pressure.Min(tick => tick.Price);
        var range = high - low;
        if (range <= 0) return (null, "TICK · range pressure nol");
        var retrace = range * TickRetraceFraction;
        var confirmStart = confirm[0].Price;
        var confirmEnd = confirm[^1].Price;
        var detail = $"imbalance {imbalance:+0.00;-0.00} · eff {efficiency:0.00} · retrace {Math.Abs(confirmEnd - pressure[^1].Price) / range * 100:0}%";

        if (imbalance >= TickMinImbalance && confirmEnd <= pressure[^1].Price - retrace && confirmEnd < confirmStart)
            return ("PUT", $"TICK EXHAUSTION · {detail} · UNPROVEN");
        if (imbalance <= -TickMinImbalance && confirmEnd >= pressure[^1].Price + retrace && confirmEnd > confirmStart)
            return ("CALL", $"TICK EXHAUSTION · {detail} · UNPROVEN");
        return (null, $"TICK · tidak ada pembalikan · {detail}");
    }

    private int SessionEntryCount(string symbol, ActiveStrategy strategy)        => sessionEntryCounts.TryGetValue((symbol, strategy), out var count) ? count : 0;

    private void RegisterSessionEntry(string symbol, ActiveStrategy strategy)
        => sessionEntryCounts[(symbol, strategy)] = SessionEntryCount(symbol, strategy) + 1;

    private IEnumerable<MenuItem> SignalStrategyItems()
    {
        yield return ForexStrategyBox;
        yield return ZScoreStrategyBox;
        yield return TrendStrategyBox;
        yield return MomentumStrategyBox;
        yield return BollingerStrategyBox;
        yield return RejectionStrategyBox;
        yield return SnR5StrategyBox;
        yield return BreakoutStrategyBox;
        yield return DualThrustStrategyBox;
        yield return RsiExtremeStrategyBox;
        yield return LookAheadStrategyBox;
        yield return SmcOverlayBox;
        yield return DonchianRegimeStrategyBox;
        yield return BollingerRegimeStrategyBox;
        yield return TickReversalStrategyBox;
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
            QuoteArrow.Foreground = Brushes.Gray;
            QuoteText.Foreground = Brushes.Gray;
            QuoteDelta.Text = "";
            QuoteDelta.Foreground = Brushes.Gray;
            QuoteFlipText.Inlines.Clear();
            QuoteCandleStats.Text = "O —   H —\nL —   BODY — pip · RANGE — pip";
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
        QuoteDelta.Text = $"{change:+0.000;-0.000;0.000}% sesi";
        QuoteDelta.Foreground = brush;

        // Position of price inside the candle's own range: 0 = at the low,
        // 1 = at the high. Fills the bar from the side price is pushing toward.
        var range = last.High - last.Low;
        var position = range > 0 ? (last.Close - last.Low) / range : 0.5;
        var track = ((FrameworkElement)QuotePressureBar.Parent).ActualWidth;
        QuotePressureBar.Width = Math.Max(0, track * position);
        QuotePressureBar.Background = brush;

        // Distance back to the open is the move that would flip this candle.
        var unit = Math.Max(last.Close * 1e-5, 1e-9);
        var pips = Math.Abs(delta) / unit;
        var rangePips = range / unit;
        QuoteCandleStats.Inlines.Clear();
        QuoteCandleStats.Inlines.Add(new System.Windows.Documents.Run($"O {FormatPrice(last.Open)}   "));
        QuoteCandleStats.Inlines.Add(new System.Windows.Documents.Run($"H {FormatPrice(last.High)}\n") { Foreground = (Brush)new BrushConverter().ConvertFromString("#F45B69")! });
        QuoteCandleStats.Inlines.Add(new System.Windows.Documents.Run($"L {FormatPrice(last.Low)}   ") { Foreground = (Brush)new BrushConverter().ConvertFromString("#25C89A")! });
        QuoteCandleStats.Inlines.Add(new System.Windows.Documents.Run($"BODY {pips:0.0} pip · RANGE {rangePips:0.0} pip"));
        var fragile = pips < 3;
        var pipBrush = (Brush)new BrushConverter().ConvertFromString("#E7B65A")!;
        QuoteFlipText.Inlines.Clear();
        QuoteFlipText.Inlines.Add(new System.Windows.Documents.Run("Jarak balik "));
        QuoteFlipText.Inlines.Add(new System.Windows.Documents.Run($"{pips:0.0} pip") { Foreground = pipBrush });
        QuoteFlipText.Inlines.Add(new System.Windows.Documents.Run(flat ? " · Candle datar, arah belum terbentuk" : fragile ? " · Rapuh, mudah berbalik" : " · Arah cukup kuat") { Foreground = flat || fragile ? pipBrush : (Brush)new BrushConverter().ConvertFromString("#9FB3CC")! });

        var age = BrokerNow() - lastTickTime;
        QuoteTickAge.Text = lastTickTime <= 0 ? "LIVE · —" : $"LIVE · {Math.Max(0, SecondsLeftInCandle())}s · TICK {age:0}s";
        QuoteTickAge.Foreground = (Brush)new BrushConverter().ConvertFromString(age > 3 ? "#F45B69" : "#25C89A")!;
    }

    private void UpdateClosedCandleSignal(IReadOnlyList<Candle> candles)
    {
        const int duration = 60;
        var now = BrokerNow();
        Candle? closed = null;
        if (candles.Count > 0)
        {
            var latest = candles[^1];
            closed = now >= latest.Time + duration ? latest : candles.Count > 1 ? candles[^2] : null;
        }

        if (closed is null)
        {
            SetLiveCandleSignal($"{selectedSymbol}|NO_DATA", "NO TRADE");
            return;
        }

        var key = $"{selectedSymbol}|{closed.Time:0}";
        if (key == lastLiveSignalCandleKey) return;

        var signal = "FLAT";
        if (now < closed.Time + duration * 2)
        {
            var delta = closed.Close - closed.Open;
            var pip = Math.Max(closed.Close * 1e-5, 1e-9);
            if (Math.Abs(delta) / pip >= 3)
            signal = delta > 0 ? "BULLISH" : delta < 0 ? "BEARISH" : "FLAT";
        }
        SetLiveCandleSignal(key, signal);
    }

    private void SetLiveCandleSignal(string key, string signal)
    {
        if (key == lastLiveSignalCandleKey) return;
        lastLiveSignalCandleKey = key;
        QuoteSignalText.Text = $"Candle tertutup: {signal} · info";
        QuoteSignalText.Foreground = (Brush)new BrushConverter().ConvertFromString(signal switch
        {
            "BULLISH" => "#25C89A",
            "BEARISH" => "#F45B69",
            _ => "#E7B65A"
        })!;
    }

    private void UpdateBinaryMetrics(Candle? last)
    {
        if (last is null || chartCandles.Count < 20)
        {
            StructCandlePattern.Text = "Candle Pattern: menunggu data";
            StructChartPattern.Text = "Chart Pattern: menunggu data";
            StructTrend.Text = "Trend Now: menunggu data";
            StructBos.Text = "Struktur B.O.S: menunggu data";
            StructSupportResistance.Text = "Support/Resistance: menunggu data";
            StructScore.Text = "CONFLUENCE: —";
            StructSignal.Text = "SIGNAL: NO TRADE";
            StructSignal.Foreground = (Brush)new BrushConverter().ConvertFromString("#E7B65A")!;
            BinaryMomentumText.Text = "WAITING\nNeed 20 candles";
            BinaryMomentumRemark.Text = "REM: menunggu data";
            BinaryMomentumScore.Text = "ALIGNMENT: —";
            BinaryMomentumSignal.Text = "SIGNAL: NO TRADE";
            BinaryMomentumSignal.Foreground = (Brush)new BrushConverter().ConvertFromString("#E7B65A")!;
            return;
        }
        var closes = chartCandles.TakeLast(20).Select(candle => candle.Close).ToArray();
        var meanX = (closes.Length - 1) / 2d;
        var midline = closes.Average();
        var slope = closes.Select((close, index) => (index - meanX) * (close - midline)).Sum() / closes.Select((_, index) => (index - meanX) * (index - meanX)).Sum();
        var threshold = Math.Max(Math.Abs(midline) * .00001, .00000001);
        var structure = AnalyzeStructure(chartCandles, ClosedBarCount(chartCandles, TimeframeMinutes()));
        var trend = structure.Trend switch { "UP" => "UPTREND", "DOWN" => "DOWNTREND", _ => last.Close > midline + threshold && slope > threshold ? "NAIK" : last.Close < midline - threshold && slope < -threshold ? "TURUN" : "RANGE" };
        var range = (last.High - last.Low) / last.Close * 100;
        var lastIsClosed = BrokerNow() >= chartCandles[^1].Time + TimeframeMinutes() * 60;
        var closedCandles = chartCandles.Take(lastIsClosed ? chartCandles.Count : chartCandles.Count - 1).ToList();
        UpdateStructurePanel(last, structure, trend, closedCandles);
        UpdateMomentumSignal(closedCandles);
        var rsi = Rsi(closedCandles);
        var atr = Atr(chartCandles.Take(Math.Max(1, chartCandles.Count - 1)).ToList(), 14);
        var ema9 = Ema(closedCandles.Select(candle => candle.Close), 9).LastOrDefault();
        var ema21 = Ema(closedCandles.Select(candle => candle.Close), 21).LastOrDefault();
        BinaryMomentumText.Text = $"EMA 9/21 {(ema9 > ema21 ? "UP" : ema9 < ema21 ? "DOWN" : "FLAT")} · RSI14 {rsi:0.0}\nATR14 {FormatPrice(atr)} · RANGE {range:0.000}%";
    }

    private void UpdateMomentumSignal(IReadOnlyList<Candle> closedCandles)
    {
        var key = closedCandles.Count == 0 ? $"{selectedSymbol}|NO_MOMENTUM_DATA" : $"{selectedSymbol}|{closedCandles[^1].Time:0}";
        if (key == lastMomentumSignalCandleKey) return;
        lastMomentumSignalCandleKey = key;
        var signal = "NO TRADE";
        var remark = "REM: butuh 20 candle tertutup";
        BinaryMomentumScore.Text = "ALIGNMENT: —";
        if (closedCandles.Count >= 20 && BrokerNow() < closedCandles[^1].Time + TimeframeMinutes() * 120)
        {
            var last = closedCandles[^1];
            var closes = closedCandles.Select(candle => candle.Close).ToList();
            var ema9 = Ema(closes, 9)[^1];
            var ema21 = Ema(closes, 21)[^1];
            var rsi = Rsi(closedCandles);
            var atr = Atr(closedCandles, 14);
            var range = last.High - last.Low;
            var rangeAtr = atr > 0 ? range / atr : 0;
            var emaDirection = ema9 > ema21 ? "UP" : ema9 < ema21 ? "DOWN" : "FLAT";
            var rsiDirection = rsi >= 52 && rsi < 70 ? "BULL" : rsi <= 48 && rsi > 30 ? "BEAR" : rsi >= 70 ? "HIGH" : rsi <= 30 ? "LOW" : "NEUTRAL";
            var candleDirection = last.Close > last.Open ? "UP" : last.Close < last.Open ? "DOWN" : "FLAT";
            var rangeState = rangeAtr < .4 ? "QUIET" : rangeAtr > 2 ? "HIGH" : "NORMAL";
            var validRange = atr > 0 && rangeAtr is >= .4 and <= 2;
            var bullishVotes = new[] { emaDirection == "UP", rsiDirection == "BULL", candleDirection == "UP", validRange }.Count(value => value);
            var bearishVotes = new[] { emaDirection == "DOWN", rsiDirection == "BEAR", candleDirection == "DOWN", validRange }.Count(value => value);
            if (validRange && emaDirection == "UP" && rsiDirection == "BULL" && candleDirection == "UP") signal = "BUY";
            else if (validRange && emaDirection == "DOWN" && rsiDirection == "BEAR" && candleDirection == "DOWN") signal = "SELL";
            remark = $"REM: EMA {emaDirection} · RSI {rsi:0} {rsiDirection} · Candle {candleDirection} · Range/ATR {rangeAtr:0.0} {rangeState}";
            BinaryMomentumScore.Text = $"ALIGNMENT: BUY {bullishVotes}/4 · SELL {bearishVotes}/4";
        }
        momentumSignal = signal;
        BinaryMomentumRemark.Text = remark;
        BinaryMomentumSignal.Text = $"SIGNAL: {signal}";
        BinaryMomentumSignal.Foreground = (Brush)new BrushConverter().ConvertFromString(signal switch { "BUY" => "#25C89A", "SELL" => "#F45B69", _ => "#E7B65A" })!;
        BinaryMomentumSignal.ToolTip = "Dihitung saat candle M1 close dari EMA 9/21, RSI 14, ATR 14, range, dan arah candle; berlaku untuk M1 berikutnya.";
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

    private void UpdateStructurePanel(Candle last, StructureAnalysis structure, string trend, IReadOnlyList<Candle> closedCandles)
    {
        var analysisCandles = closedCandles.Count > 0 ? closedCandles : chartCandles;
        var candlePattern = CandlePatternOf(analysisCandles);
        var chartPattern = ChartPatternOf(structure.Swings);
        var supportLevels = RecentLevels(structure.Swings, "L");
        var resistanceLevels = RecentLevels(structure.Swings, "H");
        var support = supportLevels.Where(level => level.Price <= last.Close).OrderByDescending(level => level.Price).FirstOrDefault()?.Price ?? supportLevels.FirstOrDefault()?.Price ?? 0;
        var resistance = resistanceLevels.Where(level => level.Price >= last.Close).OrderBy(level => level.Price).FirstOrDefault()?.Price ?? resistanceLevels.FirstOrDefault()?.Price ?? 0;
        var unit = Math.Max(last.Close * 1e-5, 1e-9);
        StructCandlePattern.Text = $"Candle Pattern: {(string.IsNullOrEmpty(candlePattern) ? "Netral" : candlePattern)}";
        StructChartPattern.Text = $"Chart Pattern: {(string.IsNullOrEmpty(chartPattern) ? "Belum terbentuk" : chartPattern)}";
        StructTrend.Text = $"Trend Now: {trend}";
        var bos = structure.BosEvents.LastOrDefault();
        var bosAge = bos is null ? int.MaxValue : Math.Max(0, chartCandles.Count - 1 - bos.Index);
        var bosText = bos is null ? "Belum ada" : $"{bos.Kind} {bos.Direction} · {bosAge} candle lalu{(bosAge > 5 ? " (lama)" : "")}";
        StructBos.Text = $"Struktur B.O.S: {bosText}";
        StructBos.Foreground = bos?.Direction == "UP" ? (Brush)new BrushConverter().ConvertFromString("#25C89A")! : bos?.Direction == "DOWN" ? (Brush)new BrushConverter().ConvertFromString("#F45B69")! : (Brush)new BrushConverter().ConvertFromString("#9FB3CC")!;
        StructSupportResistance.Inlines.Clear();
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run("Support/Resistance: S "));
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run(support > 0 ? FormatPrice(support) : "—") { Foreground = (Brush)new BrushConverter().ConvertFromString("#25C89A")! });
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run(" ("));
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run($"{(support > 0 ? (last.Close - support) / unit : 0):+0;-0;0}") { Foreground = (Brush)new BrushConverter().ConvertFromString("#F0B90B")! });
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run(" pip) · R "));
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run(resistance > 0 ? FormatPrice(resistance) : "—") { Foreground = (Brush)new BrushConverter().ConvertFromString("#F45B69")! });
        StructSupportResistance.Inlines.Add(new System.Windows.Documents.Run($" ({(resistance > 0 ? (resistance - last.Close) / unit : 0):+0;-0;0} pip)") { Foreground = (Brush)new BrushConverter().ConvertFromString("#F0B90B")! });
        var signalKey = closedCandles.Count == 0 ? $"{selectedSymbol}|NO_CLOSED_CANDLE" : $"{selectedSymbol}|{closedCandles[^1].Time:0}";
        if (signalKey != lastStructureSignalCandleKey)
        {
            lastStructureSignalCandleKey = signalKey;
            var score = closedCandles.Count >= 20 && BrokerNow() < closedCandles[^1].Time + TimeframeMinutes() * 120
                ? EvaluateStructurePatternSignal(closedCandles)
                : (Signal: "NO TRADE", Buy: 0, Sell: 0);
            structureSignal = score.Signal;
            StructScore.Text = $"CONFLUENCE: BUY {score.Buy}/5 · SELL {score.Sell}/5 · CLOSED DATA";
            StructScore.Foreground = (Brush)new BrushConverter().ConvertFromString(score.Buy >= 3 ? "#25C89A" : score.Sell >= 3 ? "#F45B69" : "#F0B90B")!;
        }
        StructSignal.Text = $"SIGNAL: {structureSignal}";
        StructSignal.ToolTip = "Dihitung sekali dari candle M1 yang sudah close; berlaku hanya untuk candle M1 berikutnya sampai close.";
        StructSignal.Foreground = (Brush)new BrushConverter().ConvertFromString(structureSignal switch { "BUY" => "#25C89A", "SELL" => "#F45B69", _ => "#E7B65A" })!;
    }


    private sealed record SmcAnalysis(string HtfBias, string Zone, double? ObLow, double? ObHigh, string ObKind, double? FvgLow, double? FvgHigh, string FvgKind, string Liquidity, int Score, string Signal, string Detail)
    {
        /// <summary>Struktur kanonik yang dipakai CHoCH; null bila belum terkonfirmasi.</summary>
        public string ChochKind { get; init; } = "—";
        public double? ChochLevel { get; init; }
        public double ChochMargin { get; init; }
        public double? HigherLow { get; init; }
        public double? LowerHigh { get; init; }
        public double? LastSwingHigh { get; init; }
        public double? LastSwingLow { get; init; }
        public double CloseA { get; init; }
        /// <summary>True bila close candle A berada di zona retracement 62–79% (OTE).</summary>
        public bool InOte { get; init; }
        /// <summary>Sinyal CHoCH mentah sebelum filter OTE (untuk audit/diagnostik).</summary>
        public string RawSignal { get; init; } = "NO TRADE";
        /// <summary>Grade konfluensi: A (OTE+FVG+OB), B (OTE+FVG), C (OTE saja), — (tidak ada).</summary>
        public string Grade { get; init; } = "—";
        /// <summary>Komponen yang membentuk grade, untuk ditampilkan.</summary>
        public string GradeNote { get; init; } = "";
        /// <summary>Ada FVG bullish (BUY) terbuka di bawah harga — zona demand.</summary>
        public bool FvgBuyOpen { get; init; }
        /// <summary>Ada FVG bearish (SELL) terbuka di atas harga — zona supply.</summary>
        public bool FvgSellOpen { get; init; }
        /// <summary>Batas FVG BUY terdekat (demand di bawah harga).</summary>
        public double? FvgBuyLow { get; init; }
        public double? FvgBuyHigh { get; init; }
        /// <summary>Batas FVG SELL terdekat (supply di atas harga).</summary>
        public double? FvgSellLow { get; init; }
        public double? FvgSellHigh { get; init; }
        /// <summary>Arah FVG terdekat: "BUY", "SELL", atau "—".</summary>
        public string FvgDirection => FvgKind is "BUY" or "SELL" ? FvgKind : "—";
    }

    /// SMC/ICT read for binary-option M1. HTF bias is aggregated from the same
    /// closed M1 candles (M15 + H1) so no extra feed is needed. Every number is
    /// computed from CLOSED candles only -- it never repaints. Confluence is a
    /// confidence score, NOT a 100% guarantee; SMC has no such edge (see
    /// AUDIT-STRATEGI.md). ponytail: M15/H1 only; add M5/M30 if scalps need it.

    private static IReadOnlyList<Candle> AggregateCandles(IReadOnlyList<Candle> candles, int factor)
    {
        // Kelompokkan menurut BATAS WAKTU (epoch / (60*factor)), bukan indeks 0.
        // Versi lama memakai `index += factor` dari elemen pertama, sehingga batas
        // candle M15/H1 bergeser setiap kali daftar candle bergeser satu bar ->
        // struktur (swing, CHoCH, FVG) berubah-ubah tanpa alasan pasar.
        var result = new List<Candle>();
        if (candles.Count == 0) return result;
        var bucketSeconds = 60d * factor;
        var index = 0;
        while (index < candles.Count)
        {
            var bucket = Math.Floor(candles[index].Time / bucketSeconds);
            var start = index;
            while (index < candles.Count && Math.Floor(candles[index].Time / bucketSeconds) == bucket) index++;
            var slice = candles.Skip(start).Take(index - start).ToList();
            // Bucket TERAKHIR yang belum penuh masih bergerak: high/low/close-nya berubah
            // setiap M1 baru masuk. Memakainya membuat FVG dan CHoCH di M15/H1 muncul
            // lalu hilang (repaint). Buang bila jumlah candle belum mencapai factor.
            // Bucket di awal data tetap dipakai: itu historis, hanya terpotong jendela data.
            var isTrailing = index >= candles.Count;
            if (isTrailing && slice.Count < factor && result.Count > 0) continue;
            result.Add(new Candle(slice[0].Time, slice[0].Open, slice.Max(candle => candle.High), slice.Min(candle => candle.Low), slice[^1].Close));
        }
        return result;
    }

    /// <summary>
    /// FVG (Fair Value Gap) kanonik. Definisi ini SAMA dengan yang dipakai harness
    /// riset (backtest26_smc_canon.py) supaya angka grade di aplikasi berlaku:
    ///   1. gap harga: low[i] &gt; high[i-2] (bullish) / high[i] &lt; low[i-2] (bearish)
    ///   2. candle tengah SEARAH: close[i-1] &gt; open[i-1] untuk bullish, sebaliknya bearish
    ///   3. lebar gap &gt;= 0,10 x ATR  (menyaring celah mikro yang bukan imbalance nyata)
    ///   4. umur &lt;= 60 candle        (zona kedaluwarsa, bukan level abadi)
    /// Tanpa syarat 2 dan 3, "FVG" muncul di hampir setiap candle dan grade A/B
    /// menjadi tidak berarti.
    /// </summary>
    private sealed record FairValueGap(double Low, double High, string Direction, int Index, bool Filled)
    {
        public double Mid => (Low + High) / 2;
        public bool IsBullish => Direction == "BUY";
    }

    private const double FvgMinAtr = 0.10;
    private const int FvgMaxAge = 60;

    private static List<FairValueGap> FindFairValueGaps(IReadOnlyList<Candle> candles, double price)
    {
        var gaps = new List<FairValueGap>();
        if (candles.Count < 3) return gaps;
        // ATR14 untuk ambang lebar gap; dihitung sekali, bukan per candle.
        var atr = Atr(candles, 14);
        var lastIndex = candles.Count - 1;
        for (var index = 2; index < candles.Count; index++)
        {
            var current = candles[index];
            var middle = candles[index - 1];
            var twoBack = candles[index - 2];
            string direction;
            double low, high;
            if (current.Low > twoBack.High && middle.Close > middle.Open)
            {
                low = twoBack.High;
                high = current.Low;
                direction = "BUY";
            }
            else if (current.High < twoBack.Low && middle.Close < middle.Open)
            {
                low = current.High;
                high = twoBack.Low;
                direction = "SELL";
            }
            else continue;

            if (high <= low) continue;
            // Syarat 3: gap harus cukup lebar untuk disebut imbalance.
            if (atr > 0 && high - low < FvgMinAtr * atr) continue;
            // Syarat 4: zona kedaluwarsa supaya tidak menumpuk selamanya.
            if (lastIndex - index > FvgMaxAge) continue;
            // Gap terisi bila candle setelahnya menembus seluruh celah.
            var filled = false;
            for (var later = index + 1; later < candles.Count; later++)
            {
                if (direction == "BUY" && candles[later].Low <= low) { filled = true; break; }
                if (direction == "SELL" && candles[later].High >= high) { filled = true; break; }
            }
            gaps.Add(new FairValueGap(low, high, direction, index, filled));
        }
        return gaps;
    }

    private static (double? Low, double? High) NearestFvg(IReadOnlyList<Candle> candles, double price)
    {
        double? bestLow = null, bestHigh = null;
        var bestDistance = double.MaxValue;
        for (var index = 2; index < candles.Count; index++)
        {
            double low, high;
            if (candles[index].Low > candles[index - 2].High) { low = candles[index - 2].High; high = candles[index].Low; }
            else if (candles[index].High < candles[index - 2].Low) { low = candles[index].High; high = candles[index - 2].Low; }
            else continue;
            var distance = Math.Abs(price - (low + high) / 2);
            if (distance < bestDistance) { bestDistance = distance; bestLow = low; bestHigh = high; }
        }
        return (bestLow, bestHigh);
    }

    private static SmcAnalysis AnalyzeSmcFull(IReadOnlyList<Candle> closedCandles, double price, double unit)
    {
        var empty = new SmcAnalysis("\u2014", "\u2014", null, null, "\u2014", null, null, "\u2014", "belum ada data", 0, "NO TRADE", "butuh 30 candle");
        if (closedCandles.Count < 30) return empty;
        var m15 = AggregateCandles(closedCandles, 15);
        var h1 = AggregateCandles(closedCandles, 60);
        if (m15.Count < 4) return empty;
        var m15Structure = AnalyzeStructure(m15, m15.Count);
        var h1Structure = AnalyzeStructure(h1, h1.Count);
        var bias = h1Structure.Trend == "UP" || (h1Structure.Trend == "RANGE" && m15Structure.Trend == "UP") ? "BULLISH"
            : h1Structure.Trend == "DOWN" || (h1Structure.Trend == "RANGE" && m15Structure.Trend == "DOWN") ? "BEARISH" : "NETRAL";
        var swings = m15Structure.Swings;
        var recent = swings.TakeLast(6).ToList();
        var zone = "RANGE";
        if (recent.Count >= 2)
        {
            var rangeHigh = recent.Max(swing => swing.Price);
            var rangeLow = recent.Min(swing => swing.Price);
            var span = rangeHigh - rangeLow;
            if (span > 0)
            {
                var position = (price - rangeLow) / span;
                zone = position >= .5 ? $"PREMIUM ({position * 100:0}% dari low)" : $"DISCOUNT ({(1 - position) * 100:0}% dari low)";
            }
        }
        var last = closedCandles[^1];
        double? obLow = null, obHigh = null; var obKind = "\u2014";
        if (last.Close < last.Open)
        {
            var highSwing = swings.LastOrDefault(swing => swing.Kind == "H" && swing.Price >= last.High);
            if (highSwing is not null)
            {
                var index = highSwing.Index;
                obHigh = m15[index].High;
                var low = m15[index].Low;
                for (var offset = index; offset <= Math.Min(index + 1, m15.Count - 1); offset++) low = Math.Min(low, m15[offset].Low);
                obLow = low; obKind = "SUPPLY";
            }
        }
        else if (last.Close > last.Open)
        {
            var lowSwing = swings.LastOrDefault(swing => swing.Kind == "L" && swing.Price <= last.Low);
            if (lowSwing is not null)
            {
                var index = lowSwing.Index;
                obLow = m15[index].Low;
                var high = m15[index].High;
                for (var offset = index; offset <= Math.Min(index + 1, m15.Count - 1); offset++) high = Math.Max(high, m15[offset].High);
                obHigh = high; obKind = "DEMAND";
            }
        }
        // --- FVG: cari yang masih terbuka, per arah ------------------------------
        // Versi lama hanya mengembalikan satu kotak terdekat dan menamai arahnya
        // "di atas"/"di bawah", lalu konsumen membandingkannya dengan "BUY"/"SELL"
        // sehingga tidak pernah cocok -- FVG BUY/SELL di panel selalu false.
        var gaps = FindFairValueGaps(m15, price).Where(gap => !gap.Filled).ToList();
        // FVG terdekat = yang paling dekat dengan harga, TERMASUK yang sedang disentuh.
        // Versi lama membuang gap yang mengandung harga (High < price || Low > price),
        // padahal harga yang berada DI DALAM zona FVG justru setup paling valid --
        // itulah saat entry. Membuangnya membuat panel menulis "tidak ada FVG" tepat
        // ketika harga retest FVG.
        var nearestGap = gaps
            .OrderBy(gap => Math.Abs(price - gap.Mid))
            .FirstOrDefault();
        // Demand = FVG bullish yang belum dilewati harga (batas atas <= harga).
        var bullGap = gaps.Where(gap => gap.IsBullish && gap.High <= price)
            .OrderByDescending(gap => gap.Index).FirstOrDefault();
        // Supply = FVG bearish yang belum dilewati harga (batas bawah >= harga).
        var bearGap = gaps.Where(gap => !gap.IsBullish && gap.Low >= price)
            .OrderByDescending(gap => gap.Index).FirstOrDefault();
        // Zona yang sedang disentuh harga tetap dihitung sebagai FVG aktif untuk arahnya.
        var touchedBullGap = gaps.Where(gap => gap.IsBullish && price >= gap.Low && price <= gap.High)
            .OrderByDescending(gap => gap.Index).FirstOrDefault();
        var touchedBearGap = gaps.Where(gap => !gap.IsBullish && price >= gap.Low && price <= gap.High)
            .OrderByDescending(gap => gap.Index).FirstOrDefault();
        var bullTouched = bullGap is null && touchedBullGap is not null;
        var bearTouched = bearGap is null && touchedBearGap is not null;

        var fvgLow = nearestGap?.Low;
        var fvgHigh = nearestGap?.High;
        var fvgKind = nearestGap?.Direction ?? "\u2014";
        var fvgBuyOpen = bullGap is not null || bullTouched;
        var fvgSellOpen = bearGap is not null || bearTouched;
        var liquidity = "tidak ada sweep";
        var sweep = m15Structure.SmcEvents.LastOrDefault();
        if (sweep is not null && m15.Count - 1 - sweep.Index <= 6) liquidity = $"{sweep.Kind} {sweep.Direction} @ {FormatPrice(sweep.Level)} ({m15.Count - 1 - sweep.Index} candle M15 lalu)";
        var biasDirection = bias == "BULLISH" ? 1 : bias == "BEARISH" ? -1 : 0;
        var sweepDirection = sweep is not null && m15.Count - 1 - sweep.Index <= 6 ? (sweep.Direction == "UP" ? 1 : -1) : 0;
        var obDirection = obKind == "DEMAND" ? 1 : obKind == "SUPPLY" ? -1 : 0;
        var nearFvg = fvgLow is not null && (fvgLow > price ? fvgLow - price : price - fvgHigh) / unit <= 15 ? (fvgLow > price ? 1 : -1) : 0;
        var bos = m15Structure.BosEvents.LastOrDefault();
        var bosDirection = bos is not null && m15.Count - 1 - bos.Index <= 5 ? (bos.Direction == "UP" ? 1 : -1) : 0;

        var bullish = new[] { biasDirection, sweepDirection, obDirection, nearFvg, bosDirection }.Count(direction => direction > 0);
        var bearish = new[] { biasDirection, sweepDirection, obDirection, nearFvg, bosDirection }.Count(direction => direction < 0);
        var score = Math.Max(bullish, bearish) * 20;

        // --- CHoCH kanonik: satu-satunya aturan SMC yang lolos verifikasi ------
        // higher low = swing low yang lebih tinggi dari swing low sebelumnya.
        // CHoCH bearish = close menembus higher low terakhir; cermin untuk bullish.
        // Diuji pada 42 juta tick / 142 simbol (backtest26 + backtest28):
        //   51.14%  d=+3.46pt  lb95=50.94  n=241.467
        //   7/7 hari positif; 114/141 simbol >50%; null-shuffle 49.5-49.6%;
        //   kontrol arah 49.3%/49.6%; bootstrap CI95 d=[+3.06,+3.85].
        // Kurva keyakinan MENURUN terhadap |close-level|/ATR, jadi TIDAK ada
        // filter margin: menambah ambang justru membuang keunggulan.
        double? lastStl = null, lastSth = null, higherLow = null, lowerHigh = null;
        for (var index = 1; index < closedCandles.Count; index++)
        {
            var pivot = index - 1;
            if (pivot >= 1)
            {
                var pivotHigh = closedCandles[pivot].High;
                var pivotLow = closedCandles[pivot].Low;
                // Simetri kanonik: level CHoCH = pivot yang MEMBENTUK higher-low /
                // lower-high itu sendiri, bukan swing sebelumnya. Versi lama memakai
                // lastSth (lebih tinggi) sehingga CHoCH bullish butuh tembus level
                // yang lebih jauh -> sinyal CALL hanya ~62% dari PUT (asimetri palsu).
                if (pivotHigh > closedCandles[pivot - 1].High && pivotHigh > closedCandles[index].High)
                {
                    if (lastSth is not null && pivotHigh < lastSth.Value) lowerHigh = pivotHigh;
                    lastSth = pivotHigh;
                }
                if (pivotLow < closedCandles[pivot - 1].Low && pivotLow < closedCandles[index].Low)
                {
                    if (lastStl is not null && pivotLow > lastStl.Value) higherLow = pivotLow;
                    lastStl = pivotLow;
                }
            }
        }
        var closeA = closedCandles[^1].Close;
        var atr14 = Atr(closedCandles, 14);
        var chochKind = "—";
        double? chochLevel = null;
        if (higherLow is not null && closeA < higherLow.Value) { chochKind = "BEARISH"; chochLevel = higherLow; }
        else if (lowerHigh is not null && closeA > lowerHigh.Value) { chochKind = "BULLISH"; chochLevel = lowerHigh; }
        var chochMargin = chochLevel is not null && atr14 > 0 ? Math.Abs(closeA - chochLevel.Value) / atr14 : 0;

        // --- Zona OTE (Optimal Trade Entry) 62%–79% dari leg terakhir -----------
        // Diuji 1.013.216 sinyal / 142 simbol (train-hari 0-5, holdout hari 6,
        // lockbox hari 7): OTE SENDIRI 50.16%, CHoCH SENDIRI 51.42%, tetapi
        // OTE+CHoCH = 55.45% (lb95 54.45) dan 80% simbol positif. Trigger CHoCH
        // baru bernilai ketika entry berada di zona diskon (retracement dalam).
        const double OteLow = 0.618, OteHigh = 0.79;
        var legHigh = lastSth ?? double.NaN;
        var legLow = lastStl ?? double.NaN;
        var legSpan = legHigh - legLow;
        var hasLeg = double.IsFinite(legSpan) && legSpan > 0;
        var inOte = false;
        if (hasLeg)
        {
            var retrFromHigh = (legHigh - closeA) / legSpan;
            // OTE harus SIMETRIS. Versi lama hanya menghitung OTE untuk CHoCH
            // BULLISH, lalu memblokir semua SELL karena "tidak pernah menang" --
            // padahal SELL tidak pernah diberi kesempatan melewati filter OTE.
            // Itu cincin logika: rule dinilai gagal tanpa pernah dijalankan.
            // Diuji ulang pada 142 simbol: OTE dua arah 54,51% (train) / 55,66%
            // (holdout); hanya-CALL 51,45% / 51,42%. Jadi PUT memang layak.
            // CALL: retrace turun dari leg high ke zona diskon.
            // PUT : pantulan naik dari leg low ke zona premium (cermin).
            var retrFromLow = (closeA - legLow) / legSpan;
            inOte = chochKind == "BULLISH"
                ? retrFromHigh >= OteLow && retrFromHigh <= OteHigh
                : chochKind == "BEARISH" && retrFromLow >= OteLow && retrFromLow <= OteHigh;
        }

        var rawSignal = chochKind == "BULLISH" ? "BUY" : chochKind == "BEARISH" ? "SELL" : "NO TRADE";

        // --- GRADE sinyal: seberapa banyak konfluensi yang mendukung -----------
        // Diuji 14.602 sinyal CALL (p=2) di 142 simbol, stabil di train/holdout/lockbox:
        //   A  OTE + FVG + OB   -> 64,41%  (min antar-periode 57,14%, EV92 +23,7%)
        //   B  OTE + FVG        -> 59,52%  (min 56,27%, EV92 +14,3%)
        //   C  OTE saja         -> 54,54%  (min 53,50%, EV92  +4,7%)
        // OB dan FVG menambah nilai; BOS dan HTF tidak (sudah diuji, tidak dipakai).
        var grade = "—";
        var gradeNote = "";
        if (inOte && (rawSignal == "BUY" || rawSignal == "SELL"))
        {
            // FVG untuk grade memakai sinyal yang SAMA dengan yang ditampilkan panel.
            // Versi lama memakai `nearFvg` yang mensyaratkan jarak <=15 unit, sehingga
            // panel bisa menulis "FVG BUY terbuka" sementara grade bilang "OTE saja".
            // GRADE kini SIMETRIS: dulu hanya BUY yang dinilai, sehingga SELL selalu
            // berakhir "—" dan otomatis terblokir sebelum pernah diuji.
            var confluentFvg = rawSignal == "BUY" ? fvgBuyOpen : fvgSellOpen;
            var confluentOb = rawSignal == "BUY" ? obKind == "DEMAND" : obKind == "SUPPLY";
            grade = confluentFvg && confluentOb ? "A" : confluentFvg ? "B" : "C";
            gradeNote = confluentFvg && confluentOb ? "OTE + FVG + OB"
                : confluentFvg ? "OTE + FVG"
                : "OTE saja";
        }

        // Entry keluar bila CHoCH dan OTE SEAARAH, untuk KEDUA arah.
        // Catatan penting dari uji ulang 142 simbol:
        //   - Grade A/B di dokumentasi lama (64,41% / 59,52%) bersandar pada sampel
        //     sangat tipis: holdout grade A hanya n=1, grade B n=24. Angka itu tidak
        //     bisa dipertanggungjawabkan dan tidak dipakai sebagai gerbang entry.
        //   - Irisan OTE + CHoCH searah yang punya sampel besar: train 54,24%
        //     (n=12.226, lb95 53,35), holdout 56,13% (n=408, lb95 51,28),
        //     lockbox 52,12% (n=543). Itulah aturan yang dipakai.
        //   - OTE SENDIRI (tanpa CHoCH) hanya ~49,5%: OTE butuh pemicu arah.
        // Grade tetap ditampilkan sebagai penanda kualitas, bukan syarat masuk.
        var signal = inOte && (rawSignal == "BUY" || rawSignal == "SELL")
            ? rawSignal
            : "NO TRADE";
        var oteText = hasLeg ? (inOte ? "DI ZONA OTE" : "di luar OTE") : "leg belum ada";
        // Teks FVG memakai status per arah yang sama dengan panel, bukan jarak.
        var fvgText = fvgBuyOpen && fvgSellOpen ? "FVG BUY+SELL terbuka"
            : fvgBuyOpen ? "FVG BUY terbuka"
            : fvgSellOpen ? "FVG SELL terbuka"
            : "tanpa FVG terbuka";
        var detail = chochLevel is null
            ? $"belum ada higher low / lower high terkonfirmasi"
            : $"CHoCH {chochKind} close {FormatPrice(closeA)} vs {FormatPrice(chochLevel)} · margin {chochMargin:0.00}xATR · {oteText}{(grade != "—" ? $" · grade {grade} ({gradeNote})" : "")} · bias {bias}, {(sweepDirection != 0 ? "sweep" : "tanpa sweep")}, {(obDirection != 0 ? "OB" : "tanpa OB")}, {fvgText}";
        return new SmcAnalysis(bias, zone, obLow, obHigh, obKind, fvgLow, fvgHigh, fvgKind, liquidity, score, signal, detail)
        {
            ChochKind = chochKind,
            ChochLevel = chochLevel,
            ChochMargin = chochMargin,
            HigherLow = higherLow,
            LowerHigh = lowerHigh,
            LastSwingHigh = lastSth,
            LastSwingLow = lastStl,
            CloseA = closeA,
            InOte = inOte,
            RawSignal = rawSignal,
            Grade = grade,
            GradeNote = gradeNote,
            FvgBuyOpen = fvgBuyOpen,
            FvgSellOpen = fvgSellOpen,
            FvgBuyLow = (bullGap ?? touchedBullGap)?.Low,
            FvgBuyHigh = (bullGap ?? touchedBullGap)?.High,
            FvgSellLow = (bearGap ?? touchedBearGap)?.Low,
            FvgSellHigh = (bearGap ?? touchedBearGap)?.High,
        };
    }
    private static (string Signal, int Buy, int Sell) EvaluateStructurePatternSignal(IReadOnlyList<Candle> candles)
    {
        var last = candles[^1];
        var structure = AnalyzeStructure(candles, candles.Count);
        var candlePattern = CandlePatternOf(candles);
        var chartPattern = ChartPatternOf(structure.Swings);
        var closes = candles.TakeLast(20).Select(candle => candle.Close).ToArray();
        var midline = closes.Average();
        var meanX = (closes.Length - 1) / 2d;
        var slope = closes.Select((close, index) => (index - meanX) * (close - midline)).Sum() / closes.Select((_, index) => (index - meanX) * (index - meanX)).Sum();
        var threshold = Math.Max(Math.Abs(midline) * .00001, .00000001);
        var trend = structure.Trend switch { "UP" => 1, "DOWN" => -1, _ => last.Close > midline + threshold && slope > threshold ? 1 : last.Close < midline - threshold && slope < -threshold ? -1 : 0 };
        var candle = candlePattern.Contains("Bullish", StringComparison.OrdinalIgnoreCase) || candlePattern is "Hammer" or "Marubozu naik" ? 1
            : candlePattern.Contains("Bearish", StringComparison.OrdinalIgnoreCase) || candlePattern is "Shooting Star" or "Marubozu turun" ? -1 : 0;
        var chart = chartPattern == "Double Bottom" ? 1 : chartPattern == "Double Top" ? -1 : 0;
        var supportLevels = RecentLevels(structure.Swings, "L");
        var resistanceLevels = RecentLevels(structure.Swings, "H");
        var support = supportLevels.Where(level => level.Price <= last.Close).OrderByDescending(level => level.Price).FirstOrDefault()?.Price ?? 0;
        var resistance = resistanceLevels.Where(level => level.Price >= last.Close).OrderBy(level => level.Price).FirstOrDefault()?.Price ?? 0;
        var unit = Math.Max(last.Close * 1e-5, 1e-9);
        var nearSupport = support > 0 && Math.Abs(last.Close - support) / unit <= 8;
        var nearResistance = resistance > 0 && Math.Abs(resistance - last.Close) / unit <= 8;
        var level = nearSupport && last.Close > last.Open && last.Close >= support ? 1 : nearResistance && last.Close < last.Open && last.Close <= resistance ? -1 : 0;
        var bos = structure.BosEvents.LastOrDefault();
        var bosAge = bos is null ? int.MaxValue : candles.Count - 1 - bos.Index;
        var votes = new[] { candle, chart, trend, bosAge > 5 ? 0 : bos?.Direction == "UP" ? 1 : bos?.Direction == "DOWN" ? -1 : 0, level };
        var buyVotes = votes.Count(vote => vote > 0);
        var sellVotes = votes.Count(vote => vote < 0);
        return (buyVotes >= 3 ? "BUY" : sellVotes >= 3 ? "SELL" : "NO TRADE", buyVotes, sellVotes);
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
        // Wilder RSI: same definition used by the backtest. Recomputing from
        // the complete available history keeps live and historical signals
        // identical, while the current candle remains the only live input.
        if (candles.Count <= period) return 50;
        var averageGain = 0d;
        var averageLoss = 0d;
        for (var index = 1; index <= period; index++)
        {
            var change = candles[index].Close - candles[index - 1].Close;
            averageGain += Math.Max(0, change);
            averageLoss += Math.Max(0, -change);
        }
        averageGain /= period;
        averageLoss /= period;
        for (var index = period + 1; index < candles.Count; index++)
        {
            var change = candles[index].Close - candles[index - 1].Close;
            averageGain = (averageGain * (period - 1) + Math.Max(0, change)) / period;
            averageLoss = (averageLoss * (period - 1) + Math.Max(0, -change)) / period;
        }
        if (averageGain == 0 && averageLoss == 0) return 50;
        if (averageLoss == 0) return 100;
        if (averageGain == 0) return 0;
        var relativeStrength = averageGain / averageLoss;
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
        ChartHoverLayer.Children.Clear();
        labelBoxes.Clear();
        PriceChart.IsHitTestVisible = true;
        // Harga dasar tetap terlihat saat semua strategi/overlay OFF. Mematikan
        // Z-Score hanya menghapus layer analisisnya, bukan data candle utama.
        if (chartCandles.Count < 2 || PriceChart.ActualWidth < 3 || PriceChart.ActualHeight < 3)
        {
            SmcDashboard.Visibility = Visibility.Collapsed;
            ChartInfoText.Text = chartCandles.Count < 2
                ? "Menunggu data candle"
                : "Chart siap · strategi OFF";
            return;
        }
        visibleBars = Math.Clamp(visibleBars, 20, chartCandles.Count); firstVisible = Math.Clamp(firstVisible, 0, Math.Max(0, chartCandles.Count - visibleBars));
        var autoEnd = AutoEndBox.IsChecked == true;
        var startIndex = autoEnd ? Math.Max(0, chartCandles.Count - visibleBars) : firstVisible;
        var shown = chartCandles.Skip(startIndex).Take(visibleBars).ToList();
        var chartSlots = shown.Count + 4;
        var closedCount = ClosedBarCount(chartCandles, TimeframeMinutes());
        var structure = AnalyzeStructure(chartCandles, closedCount);
        // Panel DX butuh 30 candle tertutup; tentukan di awal agar lebar chart
        // sudah memperhitungkan panel sebelum candle digambar.
        SmcDashboard.Visibility = (SmcOverlayBox.IsChecked == true || SmcFusionStrategyBox.IsChecked == true) && closedCount >= 30 ? Visibility.Visible : Visibility.Collapsed;
        var strategy = CurrentStrategySelection();
        var supportLevels = RecentLevels(structure.Swings, "L");
        var resistanceLevels = RecentLevels(structure.Swings, "H");
        var low = shown.Min(candle => candle.Low); var high = shown.Max(candle => candle.High);
        if (LegacyAnalysisEnabled && LevelsOverlayBox.IsChecked == true)
        {
            if (supportLevels.Count > 0) low = Math.Min(low, supportLevels.Min(level => level.Price));
            if (resistanceLevels.Count > 0) high = Math.Max(high, resistanceLevels.Max(level => level.Price));
        }
        var channel = TrendChannelOverlayBox.IsChecked == true ? BuildTrendChannel(shown, structure.Swings, startIndex) : null;
        var forexPlan = ForexStrategyBox.IsChecked == true ? BuildForexPlan(chartCandles) : null;
        if (channel is not null)
        {
            high = Math.Max(high, Math.Max(channel.Upper.At(channel.StartIndex), channel.Upper.At(channel.EndIndex)));
            low = Math.Min(low, Math.Min(channel.Lower.At(channel.StartIndex), channel.Lower.At(channel.EndIndex)));
        }
        if (forexPlan is not null)
        {
            var forexLevels = new[] { forexPlan.StopLoss, forexPlan.Tp1, forexPlan.Tp2, forexPlan.Tp3, forexPlan.Fib382, forexPlan.Fib50, forexPlan.Fib618 };
            high = Math.Max(high, forexLevels.Max());
            low = Math.Min(low, forexLevels.Min());
        }
        var range = Math.Max(high - low, Math.Abs(high) * .00001); low -= range * 1.5; high += range * 1.5; range = high - low;
        // Panel DX di kiri mengambil ruang 224px; chart digeser agar candle tidak
        // tertutup panel.
        var left = SmcDashboard.Visibility == Visibility.Visible ? 237d : 5d;
        const double right = 72, top = 25, bottom = 25;
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
        if (channel is not null) DrawTrendChannel(channel, X, Y, left);
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
        var remainingSeconds = SecondsLeftInCandle();
        DrawStrategyArrows(shown, startIndex, X, Y);
        DrawEntryArrows(shown, X, Y);
        DrawSignalPreview(shown, startIndex, X, Y, left, top, height);
        AddText(FormatPrice(last.Close), left + width + 4, Y(last.Close) - 9, "#FFFFFF", 10, marker);
        var liveLocalIndex = chartCandles.Count - 1 - startIndex;
        if (liveLocalIndex >= 0 && liveLocalIndex < shown.Count)
        {
            var liveCandle = shown[liveLocalIndex];
            var timerY = Math.Clamp(Y(liveCandle.Close) - 9, top, top + height - 18);
            AddPinnedText(TimeSpan.FromSeconds(remainingSeconds).ToString(@"mm\:ss"), X(liveLocalIndex) + candleWidth / 2 + 4, timerY, "#FFFFFF", 9, "#101722");
        }
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
        if (SmcOverlayBox.IsChecked == true || SmcFusionStrategyBox.IsChecked == true) DrawSmcOverlay(shown, startIndex, X, Y, left, width);
        if (forexPlan is not null)
        {
            DrawLevel(forexPlan.StopLoss, "FX SL", "#F45B69", Y, left, width, false);
            DrawLevel(forexPlan.Tp1, "FX TP1", "#25C89A", Y, left, width);
            DrawLevel(forexPlan.Tp2, "FX TP2", "#31D2F2", Y, left, width);
            DrawLevel(forexPlan.Tp3, "FX TP3", "#B98CFF", Y, left, width);
            DrawLevel(forexPlan.Fib382, "FIB 38.2", "#F0B90B", Y, left, width);
            DrawLevel(forexPlan.Fib50, "FIB 50.0", "#F0B90B", Y, left, width);
            DrawLevel(forexPlan.Fib618, "FIB 61.8", "#F0B90B", Y, left, width);
            DrawForexMarker(forexPlan.StopLoss, "SL", "#F45B69", Y, left, width);
            DrawForexMarker(forexPlan.Tp1, "TP1", "#25C89A", Y, left, width);
            DrawForexMarker(forexPlan.Tp2, "TP2", "#31D2F2", Y, left, width);
            DrawForexMarker(forexPlan.Tp3, "TP3", "#B98CFF", Y, left, width);
        }
        var start = DateTimeOffset.FromUnixTimeSeconds((long)shown[0].Time).LocalDateTime; var end = DateTimeOffset.FromUnixTimeSeconds((long)last.Time).LocalDateTime;
        ChartInfoText.Text = $"{selectedSymbol} · {TimeframeLabel()} · {shown.Count} candles · {closedCount} closed · {structure.Trend} · {(strategy.Primary == "OFF" ? "strategi OFF" : strategy.Primary)}";
        hoverCandles = shown;
        hoverX = X;
        hoverY = Y;
        hoverLeft = left;
        hoverTop = top;
        hoverWidth = width;
        hoverHeight = height;
        DrawCrosshair();
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
            // Candle B ini sudah punya panah live (firedEntries, kunci = candle A) — jangan gambar lagi.
            // Tanpa guard ini, candle yang sudah di-live-fire digambar dua kali:
            // sekali di sini (historikal) dan sekali di DrawEntryArrows (live).
            if (firedEntries.ContainsKey((selectedSymbol, chartCandles[signalIndex].Time))) continue;
            // Beberapa strategi bisa menghasilkan arah sama pada candle yang sama.
            // Gambar satu panah saja per (candle, arah) supaya tidak bertumpuk.
            var drawn = new HashSet<string>();
            foreach (var strategy in enabled)
            {
                var signalCandle = chartCandles[signalIndex];
                var arrowKey = (selectedSymbol, strategy, signalCandle.Time);
                if (!stableHistoricalArrows.TryGetValue(arrowKey, out var direction))
                {
                    direction = HistoricalDirection(strategy, signalIndex);
                    if (direction is not null) stableHistoricalArrows[arrowKey] = direction;
                }
                if (direction is null) continue;
                // Historical playback mengikuti cooldown realtime: satu sinyal
                // per strategi setiap lima candle, dihitung dari candle A.
                var previousSignal = chartCandles
                    .Select((candle, index) => (candle, index))
                    .Where(item => item.index < signalIndex
                        && StableHistoricalDirection(strategy, item.index) is not null)
                    .Select(item => item.index)
                    .LastOrDefault(-SignalCooldownBars);
                if (signalIndex - previousSignal < SignalCooldownBars) continue;
                if (!drawn.Add(direction)) continue;
                DrawArrow(shown[localIndex], direction, x(localIndex), y, StrategyCode(strategy));
                if (signalIndex == chartCandles.Count - 2)
                    PushChartAlert($"{StrategyName(strategy)} · ENTRY {direction}", direction == "CALL" ? "UP" : "DOWN", chartCandles[signalIndex].Time, "panah pada candle entry berikutnya", force: true);
            }
        }
    }

    private string? StableHistoricalDirection(ActiveStrategy strategy, int index)
    {
        if (index < 0 || index >= chartCandles.Count - 1) return null;
        var key = (selectedSymbol, strategy, chartCandles[index].Time);
        if (stableHistoricalArrows.TryGetValue(key, out var direction)) return direction;
        direction = HistoricalDirection(strategy, index);
        if (direction is not null) stableHistoricalArrows[key] = direction;
        return direction;
    }

    private string? HistoricalDirection(ActiveStrategy strategy, int index)
    {
        // Tick Exhaustion Reversal butuh tick mentah; candle OHLC tidak boleh
        // dipakai untuk merekonstruksi tick. Tanpa riwayat tick = tidak ada sinyal.
        if (strategy == ActiveStrategy.TickReversal) return null;
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
        if (strategy == ActiveStrategy.SnR5Break)
            return SnR5Direction(sample, out _);
        if (strategy == ActiveStrategy.VolatilityBreakout)
        {
            var priorRange = sample.Skip(sample.Count - 13).Take(12).ToList();
            var atr = Atr(sample.Take(sample.Count - 1).ToList(), 14);
            if (atr <= 0) return null;
            return last.Close > priorRange.Max(candle => candle.High) && last.High - last.Low >= atr * 1.05 ? "CALL"
                : last.Close < priorRange.Min(candle => candle.Low) && last.High - last.Low >= atr * 1.05 ? "PUT" : null;
        }
        if (strategy == ActiveStrategy.DualThrust)
            return DualThrustDirection(sample, out _);
        if (strategy == ActiveStrategy.ZScore)
        {
            return EvaluateZScoreVolatility(sample, last, out _) switch
            {
                "CALL" => "CALL",
                "PUT" => "PUT",
                _ => null,
            };
        }
        if (strategy == ActiveStrategy.Forex)
        {
            var plan = BuildForexPlan(sample);
            return plan?.Direction is "CALL" or "PUT" ? plan.Direction : null;
        }
        if (strategy == ActiveStrategy.RsiExtreme)
        {
            // RSI 20/80 extreme fade. Backtest: 122 simbol, 202.007 entry, 50,75%
            // (batas bawah 95% 49,3%), 4/8 hari di atas 50. Satu-satunya aturan
            // yang bertahan di uji per-hari dari 60+ kandidat (backtest8/9).
            var extreme = Rsi(sample);
            return extreme < 20 ? "CALL" : extreme > 80 ? "PUT" : null;
        }
        // LOOK-AHEAD 100% — DEMO / TIDAK BISA DIPAKAI LIVE.
        // Backtest: 142 simbol, 348.612 trade, 100,0000% (batas bawah 95% 99,9989).
        // Sinyal = arah candle entry (B) itu sendiri, yang di jalur historis sudah
        // ada di tabel. Uji: server/backtest14_lookahead.py
        // Jaring pengaman: live feed belum menyiarkan B, jadi index+1 DI LUAR batas
        // -> return null (fail closed). Sinyal hanya muncul di playback historis.
        // Ini bukan edge, tidak boleh diperlakukan sebagai strategi.
        if (strategy == ActiveStrategy.LookAhead)
        {
            if (index + 1 >= chartCandles.Count) return null;
            var entry = chartCandles[index + 1];
            return entry.Close > entry.Open ? "CALL" : entry.Close < entry.Open ? "PUT" : null;
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
        if (strategy == ActiveStrategy.DonchianRegime)
        {
            return DonchianRegimeDirection(sample, closes, fast, slow);
        }
        if (strategy == ActiveStrategy.BollingerRegime)
        {
            return BollingerRegimeDirection(sample, closes);
        }
        if (strategy == ActiveStrategy.Smc)
        {
            // Aturan tunggal: CHoCH kanonik (close menembus higher low / lower high
            // terakhir). Diuji 42 juta tick: 51.14% d=+3.46pt lb95=50.94.
            var smc = AnalyzeSmcFull(sample.Take(sample.Count - 1).ToList(), last.Close, Math.Max(last.Close * 1e-5, 1e-9));
            return smc.Signal == "BUY" ? "CALL" : smc.Signal == "SELL" ? "PUT" : null;
        }
        return null;
    }

    /// <summary>
    /// ADX (Wilder, 14) — Wilder smoothing atas DX. Dipakai sebagai regime gate
    /// untuk Donchian: trend kuat butuh ADX tinggi, range butuh ADX rendah.
    /// </summary>
    private static double Adx(IReadOnlyList<Candle> candles, int period = 14)
    {
        if (candles.Count < period * 2 + 1) return 0;
        var trSum = 0d; var plusDmSum = 0d; var minusDmSum = 0d;
        for (var i = 1; i <= period; i++)
        {
            var c = candles[i]; var p = candles[i - 1];
            var upMove = c.High - p.High;
            var downMove = p.Low - c.Low;
            plusDmSum += upMove > downMove && upMove > 0 ? upMove : 0;
            minusDmSum += downMove > upMove && downMove > 0 ? downMove : 0;
            trSum += Math.Max(c.High - c.Low, Math.Max(Math.Abs(c.High - p.Close), Math.Abs(c.Low - p.Close)));
        }
        if (trSum <= 0) return 0;
        double plusDi = 100 * plusDmSum / trSum;
        double minusDi = 100 * minusDmSum / trSum;
        double dx = plusDi + minusDi <= 0 ? 0 : 100 * Math.Abs(plusDi - minusDi) / (plusDi + minusDi);
        var adx = dx;
        for (var i = period + 1; i < candles.Count; i++)
        {
            var c = candles[i]; var p = candles[i - 1];
            var upMove = c.High - p.High;
            var downMove = p.Low - c.Low;
            var plusDm = upMove > downMove && upMove > 0 ? upMove : 0;
            var minusDm = downMove > upMove && downMove > 0 ? downMove : 0;
            var tr = Math.Max(c.High - c.Low, Math.Max(Math.Abs(c.High - p.Close), Math.Abs(c.Low - p.Close)));
            trSum = trSum - trSum / period + tr;
            plusDmSum = plusDmSum - plusDmSum / period + plusDm;
            minusDmSum = minusDmSum - minusDmSum / period + minusDm;
            if (trSum <= 0) continue;
            plusDi = 100 * plusDmSum / trSum;
            minusDi = 100 * minusDmSum / trSum;
            dx = plusDi + minusDi <= 0 ? 0 : 100 * Math.Abs(plusDi - minusDi) / (plusDi + minusDi);
            adx = (adx * (period - 1) + dx) / period;
        }
        return adx;
    }

    /// <summary>
    /// Donchian Strong-Regime (adaptasi Superior Trade). Entry hanya saat harga
    /// menembus channel Donchian 20 yang sudah tertutup DAN ADX>=25 (trend kuat)
    /// DAN EMA cepat di atas lambat searah. Direkonstruksi untuk M1 OTC, belum
    /// di-backtest pada data OTC — diperlakukan sebagai kandidat, bukan edge.
    /// </summary>
    private static string? DonchianRegimeDirection(IReadOnlyList<Candle> sample, List<double> closes, List<double> fast, List<double> slow)
    {
        const int channel = 20;
        if (sample.Count < channel + 3 || fast.Count < 2 || slow.Count < 2) return null;
        var closed = sample.Take(sample.Count - 1).ToList();
        if (closed.Count < channel + 1) return null;
        var prior = closed.TakeLast(channel + 1).Take(channel).ToList();
        var last = sample[^1];
        var adx = Adx(sample, 14);
        if (adx < 25) return null;
        var trendUp = fast[^1] > slow[^1];
        var trendDown = fast[^1] < slow[^1];
        if (last.Close > prior.Max(c => c.High) && trendUp && last.Close > last.Open) return "CALL";
        if (last.Close < prior.Min(c => c.Low) && trendDown && last.Close < last.Open) return "PUT";
        return null;
    }

    /// <summary>
    /// Bollinger Reverter ber-regime (adaptasi Superior Trade). Fade band hanya
    /// saat ADX&lt;25 (range/chop). Di trend ADX tinggi reverter justru yang kalah,
    /// jadi gate ini mematikan sinyal. Rekonstruksi M1 OTC, belum di-backtest.
    /// </summary>
    private static string? BollingerRegimeDirection(IReadOnlyList<Candle> sample, List<double> closes)
    {
        if (sample.Count < 24) return null;
        var adx = Adx(sample, 14);
        if (adx >= 25) return null;
        var bands = Bollinger(closes.TakeLast(20).ToList());
        if (bands.Upper <= bands.Lower) return null;
        var last = sample[^1];
        if (last.Close < bands.Lower && last.Close > last.Open) return "CALL";
        if (last.Close > bands.Upper && last.Close < last.Open) return "PUT";
        return null;
    }

    /// <summary>
    /// Dual Thrust dari je-suis-tm/quant-trading, diadaptasi dari sesi harian
    /// ke M1. Range memakai candle yang sudah selesai; candle berjalan hanya
    /// menjadi konfirmasi breakout. Anchor adalah close candle tertutup terakhir.
    /// </summary>
    private static string? DualThrustDirection(IReadOnlyList<Candle> sample, out string reason)
    {
        const int lookback = 5;
        const double k = 0.5;
        reason = "NO TRADE · data Dual Thrust belum cukup";
        if (sample.Count < lookback + 2) return null;
        var closed = sample.Take(sample.Count - 1).ToList();
        if (closed.Count < lookback + 1) return null;
        var window = closed.TakeLast(lookback).ToList();
        var range = Math.Max(
            window.Max(candle => candle.High) - window.Min(candle => candle.Close),
            window.Max(candle => candle.Close) - window.Min(candle => candle.Low));
        if (range <= 0 || double.IsNaN(range) || double.IsInfinity(range))
        {
            reason = "NO TRADE · range Dual Thrust tidak valid";
            return null;
        }
        var anchor = closed[^1].Close;
        var upper = anchor + k * range;
        var lower = anchor - (1 - k) * range;
        var last = sample[^1];
        reason = $"DT · range {range:F5} · U {upper:F5} · L {lower:F5} · close {last.Close:F5}";
        if (last.Close > upper) return "CALL";
        if (last.Close < lower) return "PUT";
        return null;
    }

    /// <summary>
    /// SnR 5-candle: high/low pivot memakai 2 candle kiri + 2 candle kanan,
    /// jadi baru valid setelah candle kanan selesai. Entry terjadi saat candle
    /// terbaru close melewati level konfirmasi; candle berjalan tidak dipakai
    /// untuk membentuk level. Toleransi 0.15 x rata-rata true range 5 candle.
    /// Kandidat OTC yang diuji train/holdout; belum terbukti selalu menang.
    /// </summary>
    private static string? SnR5Direction(IReadOnlyList<Candle> sample, out string reason)
    {
        const int pivotRadius = 2;
        const int searchBars = 120;
        const double toleranceFactor = 0.15;
        reason = "NO TRADE · pivot SnR 5-candle belum terkonfirmasi";
        if (sample.Count < 7) return null;
        var lastIndex = sample.Count - 1;
        var latestHigh = 0d;
        var latestLow = 0d;
        for (var index = lastIndex - pivotRadius; index >= Math.Max(pivotRadius, lastIndex - searchBars); index--)
        {
            var candle = sample[index];
            var left = sample.Skip(index - pivotRadius).Take(pivotRadius).ToList();
            var right = sample.Skip(index + 1).Take(pivotRadius).ToList();
            if (latestHigh <= 0
                && candle.High > left.Max(item => item.High)
                && candle.High > right.Max(item => item.High))
                latestHigh = candle.High;
            if (latestLow <= 0
                && candle.Low < left.Min(item => item.Low)
                && candle.Low < right.Min(item => item.Low))
                latestLow = candle.Low;
            if (latestHigh > 0 && latestLow > 0) break;
        }
        if (latestHigh <= 0 || latestLow <= 0) return null;
        var recent = sample.TakeLast(5).ToList();
        var tolerance = recent.Average(candle => Math.Max(0, candle.High - candle.Low)) * toleranceFactor;
        if (!double.IsFinite(tolerance) || tolerance <= 0)
        {
            reason = "NO TRADE · toleransi SnR tidak valid";
            return null;
        }
        var last = sample[^1];
        reason = $"SnR5 · S {latestLow:F5} · R {latestHigh:F5} · tol {tolerance:F5} · close {last.Close:F5}";
        if (last.Close > latestHigh + tolerance && last.Close > last.Open) return "CALL";
        if (last.Close < latestLow - tolerance && last.Close < last.Open) return "PUT";
        return null;
    }

    private static string StrategyCode(ActiveStrategy strategy) => strategy switch
    {
        ActiveStrategy.Forex => "FX",
        ActiveStrategy.Trend => "T",
        ActiveStrategy.MomentumReversal => "M",
        ActiveStrategy.BollingerReentry => "B",
        ActiveStrategy.SrRejection => "S",
        ActiveStrategy.SnR5Break => "S5",
        ActiveStrategy.VolatilityBreakout => "V",
        ActiveStrategy.DualThrust => "DT",
        ActiveStrategy.Smc => "C",
        ActiveStrategy.RsiExtreme => "X",
        ActiveStrategy.LookAhead => "L",
        ActiveStrategy.DonchianRegime => "D",
        ActiveStrategy.BollingerRegime => "R",
        ActiveStrategy.TickReversal => "TK",
        ActiveStrategy.SmcFusion => "F60",
        _ => "",
    };

    private static ForexPlan? BuildForexPlan(IReadOnlyList<Candle> candles)
    {
        if (candles.Count < 55) return null;
        var closed = candles.Take(Math.Max(1, candles.Count - 1)).ToList();
        if (closed.Count < 50) return null;
        var closes = closed.Select(c => c.Close).ToList();
        var ema20 = Ema(closes, 20)[^1];
        var ema50 = Ema(closes, 50)[^1];
        var atr = Atr(closed, 14);
        if (atr <= 0) return null;
        var window = closed.TakeLast(30).ToList();
        var swingHigh = window.Max(c => c.High);
        var swingLow = window.Min(c => c.Low);
        var span = swingHigh - swingLow;
        if (span <= 0) return null;
        var last = candles[^1];
        var bullishSetup = ema20 > ema50 && last.Close > ema20 && last.Close > last.Open;
        var bearishSetup = ema20 < ema50 && last.Close < ema20 && last.Close < last.Open;
        var bullishBias = ema20 >= ema50;
        var direction = bullishSetup ? "CALL" : bearishSetup ? "PUT" : "WAIT";
        var entry = last.Close;
        var risk = Math.Max(atr * 1.5, entry * 0.0003);
        var stop = bullishBias ? entry - risk : entry + risk;
        var tp1 = bullishBias ? entry + risk : entry - risk;
        var tp2 = bullishBias ? entry + risk * 2 : entry - risk * 2;
        var tp3 = bullishBias ? entry + risk * 3 : entry - risk * 3;
        return new ForexPlan(direction, entry, stop, tp1, tp2, tp3,
            swingHigh - span * .382, swingHigh - span * .5, swingHigh - span * .618);
    }

    private void DrawArrow(Candle candle, string direction, double x, Func<double, double> y, string code = "")
    {
        var up = direction == "CALL";
        var colour = (Brush)new BrushConverter().ConvertFromString(up ? "#28D19C" : "#F26D78")!;
        var arrowY = up ? y(candle.Low) + 16 : y(candle.High) - 16;
        var arrow = new Polygon { Fill = colour, Points = up ? [new Point(x, arrowY - 11), new Point(x - 6, arrowY + 3), new Point(x + 6, arrowY + 3)] : [new Point(x, arrowY + 11), new Point(x - 6, arrowY - 3), new Point(x + 6, arrowY - 3)] };
        Panel.SetZIndex(arrow, 30); PriceChart.Children.Add(arrow);
        if (code.Length > 0) AddText(code, x - 3, arrowY + (up ? 4 : -13), up ? "#25C89A" : "#F45B69", 8);
    }

    private void DrawForexMarker(double price, string label, string color, Func<double, double> y, double left, double width)
    {
        var x = left + width - 7;
        var markerY = y(price);
        var arrow = new Polygon
        {
            Fill = (Brush)new BrushConverter().ConvertFromString(color)!,
            Points = [new Point(x + 7, markerY), new Point(x - 2, markerY - 5), new Point(x - 2, markerY + 5)]
        };
        Panel.SetZIndex(arrow, 22);
        PriceChart.Children.Add(arrow);
        AddText(label, x - 28, markerY - 7, color, 9, "#101722");
    }

    private void DrawCrosshair()
    {
        ChartHoverLayer.Children.Clear();
        if (!hasHover || panning || hoverX is null || hoverY is null || hoverCandles.Count == 0
            || hoverPoint.X < hoverLeft || hoverPoint.X > hoverLeft + hoverWidth
            || hoverPoint.Y < hoverTop || hoverPoint.Y > hoverTop + hoverHeight) return;
        var index = Math.Clamp((int)Math.Round((hoverPoint.X - hoverLeft) * (hoverCandles.Count + 4) / hoverWidth - .5), 0, hoverCandles.Count - 1);
        var candle = hoverCandles[index];
        var stroke = new SolidColorBrush(Color.FromRgb(82, 105, 133));
        var vertical = new Line { X1 = hoverX(index), X2 = hoverX(index), Y1 = hoverTop, Y2 = hoverTop + hoverHeight, Stroke = stroke, StrokeThickness = 1, StrokeDashArray = [3, 3] };
        var horizontal = new Line { X1 = hoverLeft, X2 = hoverLeft + hoverWidth, Y1 = hoverPoint.Y, Y2 = hoverPoint.Y, Stroke = stroke, StrokeThickness = 1, StrokeDashArray = [3, 3] };
        ChartHoverLayer.Children.Add(vertical);
        ChartHoverLayer.Children.Add(horizontal);
        var time = DateTimeOffset.FromUnixTimeSeconds((long)candle.Time).LocalDateTime.ToString("dd MMM HH:mm");
        var label = new TextBlock
        {
            Text = $"{time}  O {FormatPrice(candle.Open)}  H {FormatPrice(candle.High)}  L {FormatPrice(candle.Low)}  C {FormatPrice(candle.Close)}",
            Foreground = new SolidColorBrush(Color.FromRgb(229, 235, 244)),
            Background = new SolidColorBrush(Color.FromRgb(18, 26, 38)),
            FontFamily = new FontFamily("Consolas"),
            FontSize = 10,
            Padding = new Thickness(3, 1, 3, 1),
        };
        Canvas.SetLeft(label, hoverLeft + 8);
        Canvas.SetTop(label, hoverTop + 5);
        ChartHoverLayer.Children.Add(label);
    }

    /// Draws only entries already locked in firedEntries, so a redraw can never
    /// move, recolour or remove an arrow that has been shown to the trader.
    /// Panah yang belum selesai diberi lingkaran. Hasil valid ditandai WIN/LOSS;
    /// sinyal tanpa candle entry valid ditandai INVALID dan tidak masuk skor.
    private void DrawSignalPreview(IReadOnlyList<Candle> shown, int startIndex, Func<int, double> X, Func<double, double> Y, double left, double top, double height)
    {
        if (string.IsNullOrEmpty(currentPreviewDirection) || chartCandles.Count == 0) return;
        var candle = chartCandles[^1];
        if (lockedSignals.ContainsKey((selectedSymbol, candle.Time))) return;
        var index = chartCandles.Count - 1 - startIndex;
        if (index < 0 || index >= shown.Count) return;

        var isCall = currentPreviewDirection == "CALL";
        var x = X(index);
        var y = Math.Clamp(isCall ? Y(candle.Low) + 18 : Y(candle.High) - 18, top + 8, top + height - 8);
        var preview = new Polygon
        {
            Fill = (Brush)new BrushConverter().ConvertFromString("#9FB3CC")!,
            Opacity = .65,
            ToolTip = currentPreviewReason,
            Points = isCall
                ? [new Point(x, y - 9), new Point(x - 5, y + 2), new Point(x + 5, y + 2)]
                : [new Point(x, y + 9), new Point(x - 5, y - 2), new Point(x + 5, y - 2)],
        };
        Panel.SetZIndex(preview, 29);
        PriceChart.Children.Add(preview);
        AddPinnedText($"PREVIEW {currentPreviewDirection}", Math.Min(left + PriceChart.ActualWidth - 140, x + 8), y - 7, "#9FB3CC", 9, "#101722");
    }

    /// Panah entry digambar di candle B (candle setelah sinyal dikunci), sesuai
    /// aturan entry di awal candle B. Kunci firedEntries tetap waktu candle A.
    private void DrawEntryArrows(IReadOnlyList<Candle> shown, Func<int, double> X, Func<double, double> Y)
    {
        var active = CurrentActiveStrategy();
        if (active == ActiveStrategy.Off) return;
        for (var index = 0; index < shown.Count; index++)
        {
            var signalTime = shown[index].Time - TimeframeMinutes() * 60;
            if (!firedEntries.TryGetValue((selectedSymbol, signalTime), out var direction)) continue;
            if (!firedStrategies.ContainsKey((selectedSymbol, active, signalTime))) continue;
            var up = direction == "CALL";
            var colour = (Brush)new BrushConverter().ConvertFromString(up ? "#25C89A" : "#F45B69")!;
            var x = X(index);
            var y = up ? Y(shown[index].Low) + 16 : Y(shown[index].High) - 16;
            DrawResultMarker(signalTime, x, y, up);
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

    /// Marker hasil bersama semua strategi: WIN=✓ hijau, LOSS=× merah,
    /// DRAW=lingkaran kuning, INVALID=lingkaran ! abu-abu. Panah yang masih
    /// menunggu hasil diberi lingkaran tipis.
    private void DrawResultMarker(double candleTime, double x, double y, bool up)
    {
        var entry = signalEntries
            .Where(pair => pair.Key.Symbol == selectedSymbol
                && pair.Key.Strategy == CurrentActiveStrategy()
                && pair.Key.SignalTime.Equals(candleTime))
            .Select(pair => pair.Value)
            .FirstOrDefault();
        var sector = CurrentSector(candleTime);
        var state = sector ?? entry.Outcome ?? "PENDING";
        var (brushHex, glyph) = state switch
        {
            "WIN" => ("#30E3A7", "✓"),
            "LOSS" => ("#FF5364", "×"),
            "DRAW" => ("#FFD166", ""),
            "INVALID" => ("#8DA3BF", "!"),
            _ => ("#7890AA", ""),
        };
        var center = new Point(x, up ? y - 7 : y + 7);
        var ring = new Ellipse
        {
            Width = state == "WIN" || state == "LOSS" ? 16 : 13,
            Height = state == "WIN" || state == "LOSS" ? 16 : 13,
            Stroke = (Brush)new BrushConverter().ConvertFromString(brushHex)!,
            StrokeThickness = state is "WIN" or "LOSS" or "INVALID" ? 2 : 1,
        };
        Canvas.SetLeft(ring, center.X - ring.Width / 2);
        Canvas.SetTop(ring, center.Y - ring.Height / 2);
        Panel.SetZIndex(ring, 31);
        PriceChart.Children.Add(ring);
        if (glyph.Length > 0)
        {
            var mark = new TextBlock
            {
                Text = glyph,
                Foreground = (Brush)new BrushConverter().ConvertFromString(brushHex)!,
                FontFamily = new FontFamily("Segoe UI Symbol"),
                FontSize = 12,
                FontWeight = FontWeights.Bold,
            };
            mark.Measure(new Size(double.PositiveInfinity, double.PositiveInfinity));
            Canvas.SetLeft(mark, center.X - mark.DesiredSize.Width / 2);
            Canvas.SetTop(mark, center.Y - mark.DesiredSize.Height / 2);
            Panel.SetZIndex(mark, 32);
            PriceChart.Children.Add(mark);
        }
    }

    /// Candle sinyal harus sudah selesai dan candle entry next-M1 harus ada.
    /// Setelah batas 3 detik, ketiadaan entry dikunci sebagai INVALID tanpa
    /// pernah menebak hasil atau memasukkannya ke statistik.
    private string? CurrentSector(double signalTime)
    {
        var duration = TimeframeMinutes() * 60;
        var entryTime = signalTime + duration;
        var signalIndex = chartCandles.FindIndex(candle => candle.Time == signalTime);
        if (signalIndex < 0 || signalIndex >= chartCandles.Count - 1) return "PENDING";
        var entryIndex = chartCandles.FindIndex(candle => candle.Time == entryTime);
        if (entryIndex < 0)
            return BrokerNow() < entryTime + 3 ? "PENDING" : "INVALID";
        var entry = chartCandles[entryIndex];
        if (!IsValidCandle(entry)) return "INVALID";
        if (BrokerNow() < entryTime + duration) return "PENDING";
        return null;
    }

    private static bool IsValidCandle(Candle candle) =>
        candle.Time > 0
        && double.IsFinite(candle.Open)
        && double.IsFinite(candle.High)
        && double.IsFinite(candle.Low)
        && double.IsFinite(candle.Close)
        && candle.Open > 0
        && candle.High >= Math.Max(candle.Open, candle.Close)
        && candle.Low <= Math.Min(candle.Open, candle.Close)
        && candle.Low > 0;

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

    private void AddPinnedText(string text, double x, double y, string foreground, double size, string? background = null, bool bold = false)
    {
        var block = new TextBlock { Text = text, Foreground = (Brush)new BrushConverter().ConvertFromString(foreground)!, FontSize = size, FontFamily = new FontFamily("Consolas"), Padding = new Thickness(2, 0, 2, 0), FontWeight = bold ? FontWeights.Bold : FontWeights.Normal };
        if (background is not null) block.Background = (Brush)new BrushConverter().ConvertFromString(background)!;
        Canvas.SetLeft(block, x); Canvas.SetTop(block, y); PriceChart.Children.Add(block);
    }

    /// <summary>
    /// Menempatkan beberapa label di kolom yang sama tanpa saling menimpa.
    /// Label yang diminta terlalu berdekatan digeser ke bawah sampai ada jarak
    /// minimum, lalu dijepit agar tetap di dalam area chart.
    /// </summary>
    private void AddStackedLabels(IEnumerable<(string Text, double X, double Y, string Color)> labels, double lineHeight, double minY, double maxY)
    {
        var placed = new List<double>();
        foreach (var (text, x, y, color) in labels.OrderBy(item => item.Y))
        {
            var target = Math.Max(minY, Math.Min(y, maxY));
            // Geser ke bawah sampai tidak menabrak label lain di kolom yang sama.
            while (placed.Any(existing => Math.Abs(existing - target) < lineHeight && Math.Abs(existing - target) > 0.01))
                target += lineHeight;
            if (target > maxY) target = maxY;
            placed.Add(target);
            AddPinnedText(text, x, target, color, 9, "#101722");
        }
    }

    private sealed record TrendChannelLine(double StartPrice, double Slope, int StartIndex)
    {
        public double At(int index) => StartPrice + Slope * (index - StartIndex);
    }

    /// A parallel channel: one slope shared by both edges, with the offset taken
    /// from how far the opposite pivots sit from the base line. A single
    /// regression line per side would drift apart, which is what made the old
    /// overlay look detached from price.
    private sealed record TrendChannel(TrendChannelLine Upper, TrendChannelLine Lower, int StartIndex, int EndIndex);

    private static TrendChannel? BuildTrendChannel(IReadOnlyList<Candle> shown, IEnumerable<SwingPoint> swings, int startIndex)
    {
        if (shown.Count < 2) return null;
        var visible = swings.Where(swing => swing.Index >= startIndex && swing.Index < startIndex + shown.Count).OrderBy(swing => swing.Index).ToList();
        var highs = visible.Where(swing => swing.Kind == "H").Select(swing => (Index: swing.Index - startIndex, swing.Price)).ToList();
        var lows = visible.Where(swing => swing.Kind == "L").Select(swing => (Index: swing.Index - startIndex, swing.Price)).ToList();
        static double RegressionSlope(List<(int Index, double Price)> pivots)
        {
            if (pivots.Count < 2) return double.NaN;
            var meanIndex = pivots.Average(pivot => (double)pivot.Index);
            var meanPrice = pivots.Average(pivot => pivot.Price);
            var covariance = pivots.Sum(pivot => (pivot.Index - meanIndex) * (pivot.Price - meanPrice));
            var variance = pivots.Sum(pivot => (pivot.Index - meanIndex) * (pivot.Index - meanIndex));
            return variance <= 0 ? double.NaN : covariance / variance;
        }
        // Short windows often hold too few pivots; fall back to the window drift
        // instead of returning null, which made the overlay invisible.
        var highSlope = RegressionSlope(highs);
        var lowSlope = RegressionSlope(lows);
        var slope = (double.IsNaN(highSlope), double.IsNaN(lowSlope)) switch
        {
            (false, false) => (highSlope + lowSlope) / 2,
            (false, true) => highSlope,
            (true, false) => lowSlope,
            _ => (shown[^1].Close - shown[0].Close) / (shown.Count - 1)
        };
        // Anchor each edge to the outer high/low of the visible candles so the
        // channel hugs price on screen.
        var upperIntercept = shown.Select((candle, index) => candle.High - slope * index).Max();
        var lowerIntercept = shown.Select((candle, index) => candle.Low - slope * index).Min();
        var upper = new TrendChannelLine(upperIntercept, slope, 0);
        var lower = new TrendChannelLine(lowerIntercept, slope, 0);
        return new TrendChannel(upper, lower, 0, shown.Count - 1);
    }

    private void DrawTrendChannel(TrendChannel channel, Func<int, double> X, Func<double, double> Y, double left)
    {
        void Edge(TrendChannelLine line, string label, double labelOffsetY)
        {
            var x1 = X(channel.StartIndex); var x2 = X(channel.EndIndex);
            var y1 = Y(line.At(channel.StartIndex)); var y2 = Y(line.At(channel.EndIndex));
            var edge = new Line { X1 = x1, X2 = x2, Y1 = y1, Y2 = y2, Stroke = Brushes.White, StrokeThickness = 2 };
            Panel.SetZIndex(edge, 6); PriceChart.Children.Add(edge);
            // Label duduk di ujung kiri garis, di luar channel, supaya tidak menabrak candle.
            AddPinnedText(label, x1 + 6, Math.Max(2, y1 + labelOffsetY), "#FFFFFF", 10, "#101722", true);
        }
        Edge(channel.Upper, "UPPER CHANNEL", -18);
        Edge(channel.Lower, "LOWER CHANNEL", 6);
    }
    /// Draws SMC/ICT on the chart: CHoCH level + label, HTF bias, premium/discount
    /// zone, order block, fair value gap, and the latest liquidity sweep -- each
    /// with its own text annotation. Informational overlay only; the only rule
    /// that feeds a signal is CHoCH (see AnalyzeSmcFull). Never repaints: closed
    /// candles only.
    private void DrawSmcOverlay(IReadOnlyList<Candle> shown, int startIndex, Func<int, double> X, Func<double, double> Y, double left, double width)
    {
        var closed = chartCandles.Take(Math.Max(0, chartCandles.Count - 1)).ToList();
        if (closed.Count < 30) return;
        var price = chartCandles[^1].Close;
        var smc = AnalyzeSmcFull(closed, price, Math.Max(price * 1e-5, 1e-9));
        var signalColor = smc.Signal == "BUY" ? "#25C89A" : smc.Signal == "SELL" ? "#F45B69" : "#E7B65A";

        // Header: satu baris ringkas berisi bias + zona + aturan + skor konfluensi.
        AddText($"SMC/ICT \u00b7 {smc.Signal}{(smc.Grade != "\u2014" ? $" \u00b7 GRADE {smc.Grade}" : "")}", left + 5, 74, signalColor, 10, "#101722");
        // Baris kedua: angka mentah CHoCH, supaya terlihat kenapa sinyalnya begitu.
        if (smc.ChochLevel is not null)
            AddText($"CHoCH {smc.ChochKind} lvl {FormatPrice(smc.ChochLevel)} \u00b7 close {FormatPrice(smc.CloseA)} \u00b7 margin {smc.ChochMargin:0.00}xATR", left + 5, 88, signalColor, 9, "#101722");

        // --- level CHoCH + garis dari close ke level yang ditembus --------------
        if (smc.ChochLevel is not null)
        {
            var color = smc.ChochKind == "BULLISH" ? "#25C89A" : "#F45B69";
            DrawLevel(smc.ChochLevel.Value, $"CHoCH {smc.ChochKind}", color, Y, left, width);
            var levelY = Y(smc.ChochLevel.Value);
            var closeY = Y(smc.CloseA);
            var breakLine = new Line { X1 = left + width - 40, X2 = left + width, Y1 = closeY, Y2 = levelY, Stroke = (Brush)new BrushConverter().ConvertFromString(color)!, StrokeThickness = 1.4 };
            Panel.SetZIndex(breakLine, 12); PriceChart.Children.Add(breakLine);
        }

        // --- swing yang menjadi acuan CHoCH (higher low / lower high) -----------
        if (smc.HigherLow is not null) DrawLevel(smc.HigherLow.Value, "HIGHER LOW", "#4D9DFF", Y, left, width);
        if (smc.LowerHigh is not null) DrawLevel(smc.LowerHigh.Value, "LOWER HIGH", "#F8B84E", Y, left, width);

        // --- zona OTE: area entry yang benar-benar dipakai aturan -----------------
        // Menggambar zona ini membuat trader melihat langsung DI MANA aturan berlaku,
        // bukan menebak dari angka. 62%–79% dari leg terakhir.
        var zoneLabels = new List<(string Text, double X, double Y, string Color)>();
        if (smc.LastSwingHigh is not null && smc.LastSwingLow is not null && smc.LastSwingHigh > smc.LastSwingLow)
        {
            var hi = smc.LastSwingHigh.Value;
            var lo = smc.LastSwingLow.Value;
            var span = hi - lo;
            // Untuk leg bullish, zona diskon diukur dari swing high ke bawah.
            var oteTop = hi - span * 0.62;
            var oteBottom = hi - span * 0.79;
            var top = Y(oteTop);
            var bottom = Y(oteBottom);
            var oteFill = (Brush)new BrushConverter().ConvertFromString("#25C89A")!;
            var oteRect = new Rectangle
            {
                Width = width,
                Height = Math.Max(1, bottom - top),
                Fill = oteFill,
                Opacity = smc.InOte ? .13 : .06,
                Stroke = oteFill,
                StrokeThickness = smc.InOte ? 1 : 0,
                StrokeDashArray = new DoubleCollection { 4, 3 },
            };
            Canvas.SetLeft(oteRect, left); Canvas.SetTop(oteRect, top);
            Panel.SetZIndex(oteRect, 2); PriceChart.Children.Add(oteRect);
            zoneLabels.Add(($"OTE{(smc.InOte ? " ●" : "")}", left + 6, top - 13, smc.InOte ? "#25C89A" : "#6E8AA8"));
        }

        if (smc.ObLow is not null && smc.ObHigh is not null)
        {
            var color = smc.ObKind == "DEMAND" ? "#25C89A" : "#F45B69";
            var top = Y(smc.ObHigh.Value); var bottom = Y(smc.ObLow.Value);
            var rect = new Rectangle { Width = width, Height = Math.Max(1, bottom - top), Fill = (Brush)new BrushConverter().ConvertFromString(color)!, Opacity = .16 };
            Canvas.SetLeft(rect, left); Canvas.SetTop(rect, top); Panel.SetZIndex(rect, 3); PriceChart.Children.Add(rect);
            zoneLabels.Add(("OB", left + 6, top - 13, color));
        }
        // --- FVG: gambar BUY dan SELL sekaligus, masing-masing dengan label arah ---
        // Keduanya bisa ada bersamaan (demand di bawah, supply di atas). Versi lama
        // hanya menggambar satu kotak tanpa arah sehingga tidak informatif.
        if (smc.FvgBuyLow is not null && smc.FvgBuyHigh is not null)
        {
            var top = Y(smc.FvgBuyHigh.Value); var bottom = Y(smc.FvgBuyLow.Value);
            var rect = new Rectangle { Width = width, Height = Math.Max(1, bottom - top), Fill = (Brush)new BrushConverter().ConvertFromString("#25C89A")!, Opacity = .14, Stroke = (Brush)new BrushConverter().ConvertFromString("#25C89A")!, StrokeThickness = 1 };
            Canvas.SetLeft(rect, left); Canvas.SetTop(rect, top); Panel.SetZIndex(rect, 3); PriceChart.Children.Add(rect);
            zoneLabels.Add(("FVG BUY", left + 6, top - 13, "#25C89A"));
        }
        if (smc.FvgSellLow is not null && smc.FvgSellHigh is not null)
        {
            var top = Y(smc.FvgSellHigh.Value); var bottom = Y(smc.FvgSellLow.Value);
            var rect = new Rectangle { Width = width, Height = Math.Max(1, bottom - top), Fill = (Brush)new BrushConverter().ConvertFromString("#F45B69")!, Opacity = .14, Stroke = (Brush)new BrushConverter().ConvertFromString("#F45B69")!, StrokeThickness = 1 };
            Canvas.SetLeft(rect, left); Canvas.SetTop(rect, top); Panel.SetZIndex(rect, 3); PriceChart.Children.Add(rect);
            zoneLabels.Add(("FVG SELL", left + 6, top - 13, "#F45B69"));
        }
        if (zoneLabels.Count > 0)
            AddStackedLabels(zoneLabels, 12, 2, Math.Max(2, PriceChart.ActualHeight - 14));
        var m15 = AggregateCandles(closed, 15);
        var sweep = AnalyzeStructure(m15, m15.Count).SmcEvents.LastOrDefault();
        if (sweep is not null)
        {
            var color = sweep.Direction == "UP" ? "#25C89A" : "#F45B69";
            DrawLevel(sweep.Level, "SWEEP", color, Y, left, width);
        }
        // Panel teks: semua bacaan SMC dalam satu blok, supaya angkanya bisa dibaca
        // tanpa menebak dari warna.
        var fvgLine = smc.FvgBuyOpen && smc.FvgSellOpen ? "BUY + SELL terbuka"
            : smc.FvgBuyOpen ? "BUY terbuka (demand)"
            : smc.FvgSellOpen ? "SELL terbuka (supply)"
            : "tidak ada";
        var lines = new[]
        {
            $"HTF BIAS  {smc.HtfBias}",
            $"ZONA      {smc.Zone}",
            $"CHoCH     {smc.ChochKind}",
            $"OTE       {(smc.InOte ? "DI ZONA" : "di luar")}",
            $"FVG       {fvgLine}",
            $"GRADE     {(smc.Grade == "—" ? "—" : $"{smc.Grade} · {smc.GradeNote}")}",
            $"SINYAL    {smc.Signal}",
        };
        for (var row = 0; row < lines.Length; row++)
            AddText(lines[row], left + width - 230, 66 + row * 13, row == lines.Length - 1 ? signalColor : "#9FB3CC", 9, "#101722");

        DrawSmcMarkers(shown, startIndex, X, Y);
        UpdateSmcDashboard(closed, smc);
    }

    /// Marker FVG dan sweep di posisi candle yang membentuknya. Semua dari candle
    /// CLOSED, jadi marker historis tidak repaint saat candle berjalan bergerak.
    /// Label "CSA" = Confirmation Sweep Area: sweep likuiditas yang ditutup kembali
    /// ke dalam range, bukan istilah akurasi.
    private void DrawSmcMarkers(IReadOnlyList<Candle> shown, int startIndex, Func<int, double> X, Func<double, double> Y)
    {
        if (shown.Count < 3) return;
        var marked = 0;
        for (var index = 2; index < shown.Count && marked < 8; index++)
        {
            var gapUp = shown[index].Low > shown[index - 2].High;
            var gapDown = shown[index].High < shown[index - 2].Low;
            if (!gapUp && !gapDown) continue;
            marked++;
            var buy = gapUp;
            var color = buy ? "#25C89A" : "#F45B69";
            var x = X(index);
            // Zona FVG ditandai kotak + label, BUKAN panah: panah di chart
            // khusus untuk sinyal entry (DrawStrategyArrows / DrawEntryArrows).
            // Batas gap HARUS mengikuti definisi: bullish = high[i-2]..low[i],
            // bearish = high[i]..low[i-2]. Versi lama memakai low[i] untuk keduanya,
            // sehingga kotak bearish terbalik dan tingginya salah.
            var gapLow = buy ? shown[index - 2].High : shown[index].High;
            var gapHigh = buy ? shown[index].Low : shown[index - 2].Low;
            var top = Y(gapHigh);
            var bottom = Y(gapLow);
            var zone = new Rectangle
            {
                Width = 9,
                Height = Math.Max(1, Math.Abs(bottom - top)),
                Fill = (Brush)new BrushConverter().ConvertFromString(color)!,
                Opacity = .3
            };
            Canvas.SetLeft(zone, x - 4.5);
            Canvas.SetTop(zone, Math.Min(top, bottom));
            Panel.SetZIndex(zone, 12);
            PriceChart.Children.Add(zone);
            AddPinnedText(buy ? "FVG BUY" : "FVG SELL", x - 20, Math.Min(top, bottom) - 11, color, 8);
        }

        var closed = chartCandles.Take(Math.Max(0, chartCandles.Count - 1)).ToList();
        var m15 = AggregateCandles(closed, 15);
        if (m15.Count < 10) return;
        var sweeps = AnalyzeStructure(m15, m15.Count).SmcEvents.TakeLast(3).ToList();
        foreach (var sweep in sweeps)
        {
            var local = -1;
            for (var index = 0; index < shown.Count; index++)
                if (shown[index].Time <= m15[sweep.Index].Time) local = index;
            if (local < 0) continue;
            var buy = sweep.Direction == "UP";
            var color = buy ? "#25C89A" : "#F45B69";
            var x = X(local);
            var y = Y(sweep.Level) + (buy ? 30 : -30);
            var dot = new Ellipse { Width = 5, Height = 5, Fill = (Brush)new BrushConverter().ConvertFromString(color)!, Opacity = .9 };
            Canvas.SetLeft(dot, x - 2.5); Canvas.SetTop(dot, y - 2.5);
            Panel.SetZIndex(dot, 13); PriceChart.Children.Add(dot);
            AddPinnedText(buy ? "CSA BUY" : "CSA SELL", x + 5, y - 6, color, 8);
        }
    }

    private static (string Kind, string Detail) DetectRsiDivergence(IReadOnlyList<Candle> closed)
    {
        if (closed.Count < 40) return ("NEUTRAL", "(RSI 14 · data kurang)");
        var swings = AnalyzeStructure(closed, closed.Count).Swings.TakeLast(8).ToList();
        var highs = swings.Where(swing => swing.Kind == "H").ToList();
        var lows = swings.Where(swing => swing.Kind == "L").ToList();
        static double RsiAt(IReadOnlyList<Candle> candles, int index) => Rsi(candles.Take(index + 1).ToList(), 14);
        if (highs.Count >= 2)
        {
            var (first, second) = (highs[^2], highs[^1]);
            if (second.Price > first.Price && RsiAt(closed, second.Index) < RsiAt(closed, first.Index))
                return ("BEAR", $"(RSI {RsiAt(closed, second.Index):0} · higher high)");
        }
        if (lows.Count >= 2)
        {
            var (first, second) = (lows[^2], lows[^1]);
            if (second.Price < first.Price && RsiAt(closed, second.Index) > RsiAt(closed, first.Index))
                return ("BULL", $"(RSI {RsiAt(closed, second.Index):0} · lower low)");
        }
        return ("NEUTRAL", $"(RSI {Rsi(closed, 14):0})");
    }

    /// Panel DX-style di kiri chart. Isinya bacaan struktur, bukan probabilitas
    /// menang: skor konfluensi hanya urutan prioritas, dan semua nilai ditandai
    /// UNPROVEN (lihat AUDIT-STRATEGI.md).
    private void UpdateSmcDashboard(IReadOnlyList<Candle> closed, SmcAnalysis smc)
    {
        if ((SmcOverlayBox.IsChecked != true && SmcFusionStrategyBox.IsChecked != true) || closed.Count < 30)
        {
            SmcDashboard.Visibility = Visibility.Collapsed;
            return;
        }
        SmcDashboard.Visibility = Visibility.Visible;

        var signalColor = smc.Signal == "BUY" ? "#25C89A" : smc.Signal == "SELL" ? "#F45B69" : "#E7B65A";
        SmcScoreText.Text = smc.Score.ToString(CultureInfo.InvariantCulture);
        SmcScoreText.Foreground = (Brush)new BrushConverter().ConvertFromString(signalColor)!;
        // Grade menggantikan skor kabur: A/B/C = sinyal layak, — = belum ada setup.
        SmcScoreDetail.Text = smc.Grade switch
        {
            "A" => "GRADE A · OTE+FVG+OB · 64,4%",
            "B" => "GRADE B · OTE+FVG · 59,5%",
            "C" => "GRADE C · OTE saja · 54,5%",
            _ => "BELUM ADA SETUP",
        };
        // Status harus menjawab satu pertanyaan saja: apakah ada trade sekarang?
        SmcSignalText.Text = smc.Signal == "BUY" ? "CALL"
            : smc.Signal == "SELL" ? "PUT"
            : smc.RawSignal == "SELL" ? "NO · PUT"
            : smc.Grade == "—" && smc.InOte ? "WAIT · OTE"
            : smc.Grade == "—" && smc.ChochLevel is not null ? "WAIT · CHoCH"
            : "NO SETUP";
        SmcSignalText.Foreground = (Brush)new BrushConverter().ConvertFromString(signalColor)!;
        // Keterangan menjelaskan ALASAN penolakan, bukan mengulang label.
        SmcSignalDetail.Text = smc.Signal == "BUY"
            ? $"SMC · {smc.GradeNote} · {smc.Zone}"
            : smc.RawSignal == "SELL" ? "PUT tidak pernah disinyal (winrate 41-48%)"
            : smc.ChochLevel is null ? "menunggu CHoCH terkonfirmasi"
            : !smc.InOte ? $"CHoCH ada, tapi harga di luar OTE · {smc.Zone}"
            : $"SMC · {smc.Zone}";

        // Regime: ADX untuk kekuatan tren, persentil range untuk volatilitas.
        var regimeSnapshot = SmcRegimeSnapshot(closed);
        var adx = regimeSnapshot.Adx;
        var volPercentile = regimeSnapshot.VolatilityPercentile;
        var currentRange = regimeSnapshot.CurrentRange;
        var regime = regimeSnapshot.Regime;
        var regimeDetail = regime switch
        {
            "TRENDING" => $"ADX {adx:0.0} · tren aktif",
            "RANGING" => $"ADX {adx:0.0} · tunggu breakout",
            _ => $"ADX {adx:0.0} · transisi"
        };
        SmcRegimeText.Text = regime;
        SmcRegimeText.Foreground = (Brush)new BrushConverter().ConvertFromString(regime == "RANGING" ? "#E7B65A" : "#E7EDF5")!;
        SmcRegimeDetail.Text = regimeDetail;

        // Alignment multi-timeframe dari data M1 yang sama, tanpa feed tambahan.
        var frames = new (string Label, int Minutes)[] { ("M1", 1), ("M5", 5), ("M15", 15), ("M30", 30), ("H1", 60), ("H4", 240), ("D1", 1440) };
        var alignment = new StringBuilder();
        var alignedCount = 0;
        var biasDirection = smc.HtfBias == "BULLISH" ? "UP" : smc.HtfBias == "BEARISH" ? "DOWN" : "RANGE";
        foreach (var (label, minutes) in frames)
        {
            var aggregated = minutes == 1 ? closed : AggregateCandles(closed, minutes);
            var trend = aggregated.Count >= 10 ? AnalyzeStructure(aggregated, aggregated.Count).Trend : "—";
            if (trend == biasDirection && biasDirection != "RANGE") alignedCount++;
            alignment.Append(label).Append("  ").Append(trend == "UP" ? "UP" : trend == "DOWN" ? "DOWN" : trend == "RANGE" ? "RANGE" : "—").Append('\n');
        }
        SmcAlignmentText.Text = alignment.ToString().TrimEnd();

        var structureLabel = smc.ChochKind == "BULLISH" ? "BULLISH" : smc.ChochKind == "BEARISH" ? "BEARISH" : "NEUTRAL";
        SmcStructureText.Text = structureLabel;
        SmcStructureText.Foreground = (Brush)new BrushConverter().ConvertFromString(
            structureLabel == "BULLISH" ? "#25C89A" : structureLabel == "BEARISH" ? "#F45B69" : "#E7EDF5")!;
        SmcStructureDetail.Text = smc.ChochLevel is null
            ? "Scanning Structure"
            : $"CHoCH lvl {FormatPrice(smc.ChochLevel)} · margin {smc.ChochMargin:0.00}xATR";

        // Divergence: bandingkan dua swing terakhir dengan RSI pada bar yang sama.
        var (divergence, divergenceDetail) = DetectRsiDivergence(closed);
        SmcDivergenceText.Text = divergence;
        SmcDivergenceText.Foreground = (Brush)new BrushConverter().ConvertFromString(
            divergence == "BULL" ? "#25C89A" : divergence == "BEAR" ? "#F45B69" : "#E7EDF5")!;
        SmcDivergenceDetail.Text = divergenceDetail;

        SmcVolatilityFill.Width = Math.Max(0, 196 * volPercentile / 100);
        SmcVolatilityFill.Background = (Brush)new BrushConverter().ConvertFromString(volPercentile >= 75 ? "#F45B69" : volPercentile <= 25 ? "#25C89A" : "#E7B65A")!;
        SmcVolatilityText.Text = $"{currentRange:0.#####} · persentil {volPercentile:0}%";

        var reasons = new List<string>();
        reasons.Add($"HTF {smc.HtfBias}");
        reasons.Add($"Zona {smc.Zone}");
        reasons.Add($"CHoCH {smc.ChochKind}");
        reasons.Add(smc.Liquidity.StartsWith("tidak", StringComparison.OrdinalIgnoreCase) ? "Tanpa sweep" : $"Sweep {smc.Liquidity}");
        reasons.Add($"Vol {volPercentile:0}%");
        SmcWhyText.Text = string.Join(" · ", reasons);

        var total = smc.Score > 0 ? 1 : 0;
        var sureshots = smc.Signal is "BUY" or "SELL" ? 1 : 0;
        SmcCountsText.Text = $"Total: {total} · Sureshots: {sureshots} · OTM: {1 - sureshots}";

        // Zona entry: tampilkan OTE lebih dulu karena itulah syarat sinyal,
        // lalu OB/FVG sebagai konfirmasi grade.
        if (smc.LastSwingHigh is not null && smc.LastSwingLow is not null && smc.LastSwingHigh > smc.LastSwingLow)
        {
            var hi = smc.LastSwingHigh.Value; var lo = smc.LastSwingLow.Value; var span = hi - lo;
            var oteTop = hi - span * 0.62; var oteBottom = hi - span * 0.79;
            SmcEntryZoneText.Text = $"OTE {FormatPrice(oteBottom)} – {FormatPrice(oteTop)}"
                + (smc.InOte ? " · harga DI ZONA" : " · harga di luar");
        }
        else
        {
            SmcEntryZoneText.Text = smc.ObLow is not null && smc.ObHigh is not null
                ? $"OB {FormatPrice(smc.ObLow)} – {FormatPrice(smc.ObHigh)}"
                : smc.FvgLow is not null && smc.FvgHigh is not null
                    ? $"FVG {FormatPrice(smc.FvgLow)} – {FormatPrice(smc.FvgHigh)}"
                    : "belum ada zona";
        }

        // FUSION 60 panel: dua arah skor + breakdown saat strategi aktif.
        if (SmcFusionStrategyBox.IsChecked == true && closed.Count >= 60)
        {
            var closedOnly = closed;
            var price = chartCandles.Count > 0 ? chartCandles[^1].Close : 0;
            if (price > 0)
            {
                var unit = Math.Max(price * 1e-5, 1e-9);
                var smcFusion = AnalyzeSmcFull(closedOnly, price, unit);
                var (call, put, _, breakdown) = EvaluateSmcFusion(closedOnly, smcFusion, price, unit);
                FusionCallScoreText.Text = call.ToString(CultureInfo.InvariantCulture);
                FusionPutScoreText.Text = put.ToString(CultureInfo.InvariantCulture);
                FusionBreakdownText.Text = breakdown;
            }
        }
        else
        {
            FusionCallScoreText.Text = "—";
            FusionPutScoreText.Text = "—";
            FusionBreakdownText.Text = "Aktifkan SMC FUSION 60 di INDIKATOR > STRATEGI";
        }
        UpdateFusionStats();
    }

    private static (string Regime, double Adx, double VolatilityPercentile, double CurrentRange) SmcRegimeSnapshot(IReadOnlyList<Candle> closed)
    {
        var adx = Adx(closed, 14);
        var ranges = closed.TakeLast(60).Select(candle => candle.High - candle.Low).ToList();
        var currentRange = ranges.Count > 0 ? ranges[^1] : 0;
        var volPercentile = ranges.Count == 0 ? 0 : ranges.Count(range => range <= currentRange) * 100d / ranges.Count;
        var regime = adx >= 25 ? "TRENDING" : adx <= 20 ? "RANGING" : "TRANSITIONAL";
        return (regime, adx, volPercentile, currentRange);
    }

    // ===================== SMC FUSION 60 (lapisan skor) =====================
    // Skor strategi internal, BUKAN probabilitas menang. Membaca hasil
    // AnalyzeSmcFull yang sudah ada; tidak menghitung ulang BOS/OB/FVG/sweep.
    // Kalibrasi dari data live engine Vibe-Trading: skor berkisar 75 (CHoCH+BOS+
    // bias) sampai 80 (tambah momentum). Ambang 80 = hanya setup terlengkap yang
    // lolos, sehingga tidak entry tiap candle.
    private const int SmcFusionMinScore = 80;
    /// Ambang alert PREPARE: lebih rendah dari ambang lock supaya sempat bersiap.
    private const int SmcFusionPrepareScore = 75;

    private static double MacdHistogram(IReadOnlyList<Candle> candles)
    {
        var closes = candles.Select(candle => candle.Close).ToList();
        if (closes.Count < 35) return 0;
        var fast = Ema(closes, 12);
        var slow = Ema(closes, 26);
        var macdLine = fast.Select((value, index) => value - slow[index]).ToList();
        var signal = Ema(macdLine, 9);
        return macdLine[^1] - signal[^1];
    }

    // Definisi objektif pola price action pada candle CLOSED terakhir.
    private static (bool Bullish, bool Bearish, string Label) FusionPriceAction(IReadOnlyList<Candle> candles)
    {
        if (candles.Count < 3) return (false, false, "—");
        var c = candles[^1];
        var p = candles[^2];
        var range = c.High - c.Low;
        if (range <= 0) return (false, false, "—");
        var body = Math.Abs(c.Close - c.Open);
        var upper = c.High - Math.Max(c.Close, c.Open);
        var lower = Math.Min(c.Close, c.Open) - c.Low;
        var up = c.Close > c.Open;
        var down = c.Close < c.Open;
        var pRange = p.High - p.Low;
        var inside = c.High < p.High && c.Low > p.Low;
        var pinBull = lower >= body * 2 && upper <= range * .25 && up;
        var pinBear = upper >= body * 2 && lower <= range * .25 && down;
        var rejectionBull = lower >= range * .5 && c.Close > p.Close;
        var rejectionBear = upper >= range * .5 && c.Close < p.Close;
        var breakoutUp = c.Close > p.High && up;
        var breakoutDown = c.Close < p.Low && down;
        var failedBreakUp = c.High > p.High && c.Close < p.High && down;
        var failedBreakDown = c.Low < p.Low && c.Close > p.Low && up;
        var momentumUp = up && body / range > .7 && range > pRange * 1.2;
        var momentumDown = down && body / range > .7 && range > pRange * 1.2;
        var bullish = pinBull || rejectionBull || breakoutUp || failedBreakDown || momentumUp;
        var bearish = pinBear || rejectionBear || breakoutDown || failedBreakUp || momentumDown;
        var label = pinBull ? "Pin Bar bullish" : pinBear ? "Pin Bar bearish"
            : rejectionBull ? "Rejection bullish" : rejectionBear ? "Rejection bearish"
            : breakoutUp ? "Breakout naik" : breakoutDown ? "Breakout turun"
            : failedBreakDown ? "Failed breakout bawah" : failedBreakUp ? "Failed breakout atas"
            : momentumUp ? "Momentum naik" : momentumDown ? "Momentum turun"
            : inside ? "Inside bar" : "—";
        return (bullish, bearish, label);
    }

    // Mengembalikan skor dua arah + arah keputusan + breakdown.
    // Anti double-count: struktur & trend & MTF-trend yang berkorelasi dibagi
    // lewat grup berbobot; satu kondisi tidak dijumlah di dua komponen.
    // Static: tidak memakai state instance, bisa diuji tanpa membuat window.
    /// Bias struktur dari swing terakhir: higher-high + higher-low = bullish,
    /// lower-high + lower-low = bearish. Dipakai sebagai dasar skor Fusion saat
    /// tidak ada event BOS/CHoCH eksplisit.
    private static (bool Bull, bool Bear) SwingStructureBias(IReadOnlyList<SwingPoint> swings)
    {
        var highs = swings.Where(swing => swing.Kind == "H").TakeLast(2).ToList();
        var lows = swings.Where(swing => swing.Kind == "L").TakeLast(2).ToList();
        if (highs.Count < 2 || lows.Count < 2) return (false, false);
        var higherHigh = highs[1].Price > highs[0].Price;
        var higherLow = lows[1].Price > lows[0].Price;
        var lowerHigh = highs[1].Price < highs[0].Price;
        var lowerLow = lows[1].Price < lows[0].Price;
        return (higherHigh && higherLow, lowerHigh && lowerLow);
    }

    private static (int Call, int Put, string Direction, string Breakdown) EvaluateSmcFusion(
        IReadOnlyList<Candle> closedCandles, SmcAnalysis smc, double price, double unit)
    {
        if (closedCandles.Count < 60) return (0, 0, "NO TRADE", "data belum cukup");
        var structure = AnalyzeStructure(closedCandles, closedCandles.Count);
        // Struktur: BOS/CHoCH bila ada, jika tidak pakai tren swing (HH/HL vs
        // LH/LL). Sebelumnya hanya BOS/CHoCH keras sehingga skor selalu 0.
        var (swingBull, swingBear) = SwingStructureBias(structure.Swings);
        var bosBull = smc.ChochKind == "BULLISH"
            || (structure.Trend == "UP" && swingBull)
            || (structure.SmcEvents.LastOrDefault(ev => ev.Kind == "BOS") is { Direction: "UP" });
        var bosBear = smc.ChochKind == "BEARISH"
            || (structure.Trend == "DOWN" && swingBear)
            || (structure.SmcEvents.LastOrDefault(ev => ev.Kind == "BOS") is { Direction: "DOWN" });
        var chochBull = smc.ChochKind == "BULLISH";
        var chochBear = smc.ChochKind == "BEARISH";
        var sweepBull = smc.Liquidity.Contains("UP", StringComparison.OrdinalIgnoreCase) && !smc.Liquidity.Contains("tidak ada");
        var sweepBear = smc.Liquidity.Contains("DOWN", StringComparison.OrdinalIgnoreCase) && !smc.Liquidity.Contains("tidak ada");
        var obBull = smc.ObKind == "DEMAND";
        var obBear = smc.ObKind == "SUPPLY";
        // FVG dibaca dari status per arah, bukan dari string posisi harga.
        var fvgBull = smc.FvgBuyOpen;
        var fvgBear = smc.FvgSellOpen;
        var (paBull, paBear, paLabel) = FusionPriceAction(closedCandles);
        var pattern = CandlePatternOf(closedCandles);
        var patternBull = pattern.Contains("Bullish") || pattern is "Hammer" or "Marubozu naik";
        var patternBear = pattern.Contains("Bearish") || pattern is "Shooting Star" or "Marubozu turun";

        var closes = closedCandles.Select(candle => candle.Close).ToList();
        var ema20 = Ema(closes, 20)[^1];
        var ema50 = Ema(closes, 50)[^1];
        var ema200 = Ema(closes, Math.Min(200, closes.Count))[^1];
        var trendBull = price > ema20 && ema20 > ema50 && ema50 >= ema200;
        var trendBear = price < ema20 && ema20 < ema50 && ema50 <= ema200;
        var biasBull = smc.HtfBias == "BULLISH";
        var biasBear = smc.HtfBias == "BEARISH";

        var rsi = Rsi(closedCandles);
        var macd = MacdHistogram(closedCandles);
        var atr = Atr(closedCandles, 14);
        var volPercentile = SmcRegimeSnapshot(closedCandles).VolatilityPercentile;
        var volatilityOk = volPercentile >= 30;
        var momentumBull = rsi >= 50 && rsi <= 75 && macd > 0 && volatilityOk;
        var momentumBear = rsi <= 50 && rsi >= 25 && macd < 0 && volatilityOk;

        var alignedBull = CountMtfAlignment(closedCandles, "UP");
        var alignedBear = CountMtfAlignment(closedCandles, "DOWN");

        // === SMC versi Vibe-Trading (port engine smc) ==========================
        // Alur repo: swing_highs_lows -> bos_choch -> fvg.
        // Aturan repo diubah: FVG bukan hanya filter, tapi juga PEMICU sendiri
        // saat harga retest zona (FVG BUY = CALL, FVG SELL = PUT). CHoCH tetap
        // pemicu terkuat, BOS mengonfirmasi.
        var smcEngine = AnalyzeSmcSwingEngine(closedCandles, price);
        var conflict = smcEngine.ChochDirection != 0 && smcEngine.LastBosDirection != 0
            && smcEngine.ChochDirection != smcEngine.LastBosDirection;
        var fvgRetest = smcEngine.FvgRetest; // harga sedang menyentuh zona FVG

        // Kombinasi Support/Resistance: hanya dari BEBERAPA candle terakhir
        // (bukan seluruh histori), karena level lama sudah tidak relevan untuk
        // expiry 1 menit.
        var recentWindow = Math.Clamp(closedCandles.Count / 4, 12, 40);
        var recentSwings = structure.Swings
            .Where(swing => swing.Index >= closedCandles.Count - recentWindow)
            .ToList();
        var supportLevels = RecentLevels(recentSwings, "L");
        var resistanceLevels = RecentLevels(recentSwings, "H");
        var support = supportLevels.Where(level => level.Price <= price)
            .OrderByDescending(level => level.Price).FirstOrDefault()?.Price;
        var resistance = resistanceLevels.Where(level => level.Price >= price)
            .OrderBy(level => level.Price).FirstOrDefault()?.Price;
        // Dekat support = ruang naik lebih besar (CALL), dekat resistance =
        // ruang turun lebih besar (PUT). Toleransi 0,5 ATR.
        var nearTolerance = Math.Max(unit * 2, price * 1e-5);
        // Validasi algo candle: level hanya dianggap sah bila candle terakhir
        // benar-benar bereaksi di sana (menolak / ditembus dengan body), bukan
        // sekadar garis dari swing lama.
        var lastClosed = closedCandles[^1];
        var bodyRatio = lastClosed.High > lastClosed.Low
            ? Math.Abs(lastClosed.Close - lastClosed.Open) / (lastClosed.High - lastClosed.Low) : 0;
        var rejectionBull = lastClosed.Low <= (support ?? double.MinValue) + nearTolerance
            && lastClosed.Close > lastClosed.Open && bodyRatio >= 0.4;
        var rejectionBear = lastClosed.High >= (resistance ?? double.MaxValue) - nearTolerance
            && lastClosed.Close < lastClosed.Open && bodyRatio >= 0.4;
        var nearSupport = support is not null && price - support.Value <= nearTolerance;
        var nearResistance = resistance is not null && resistance.Value - price <= nearTolerance;
        // Tembus resistance = CALL, tembus support = PUT (butuh body, bukan sumbu).
        var brokeResistance = resistance is not null && price > resistance.Value && bodyRatio >= 0.4;
        var brokeSupport = support is not null && price < support.Value && bodyRatio >= 0.4;
        // Pola candle dan chart pattern dinilai dari posisinya terhadap S/R:
        // pola yang muncul tepat di level jauh lebih berarti daripada di tengah.
        var chartPattern = ChartPatternOf(structure.Swings);
        var atLevel = rejectionBull || rejectionBear || nearSupport || nearResistance
            || brokeSupport || brokeResistance;
        var chartAtLevel = atLevel && chartPattern != "";
        var patternAtLevel = atLevel && pattern != "";
        var patternLabel = pattern == "" ? "—" : patternAtLevel ? $"{pattern}@level" : pattern;
        var chartLabel = chartPattern == "" ? "" : chartAtLevel ? $" · {chartPattern}@level" : $" · {chartPattern}";

        int Score(bool bull)
        {
            var score = 0;
            var choch = bull ? smcEngine.ChochDirection > 0 : smcEngine.ChochDirection < 0;
            var bos = bull ? smcEngine.LastBosDirection > 0 : smcEngine.LastBosDirection < 0;
            var fvg = bull ? smcEngine.FvgDirection > 0 : smcEngine.FvgDirection < 0;
            if (choch) score += 45;
            if (bos) score += 25;
            // FVG searah jadi pemicu bila harga retest; tanpa retest tetap
            // menambah keyakinan sebagai konfluensi.
            if (fvg) score += fvgRetest ? 45 : 15;
            if (bull && biasBull) score += 5;
            if (!bull && biasBear) score += 5;
            if (bull && momentumBull) score += 5;
            if (!bull && momentumBear) score += 5;
            // Support/Resistance sebagai konfluensi, divalidasi algo candle.
            if (bull && (rejectionBull || nearSupport)) score += 10;
            if (!bull && (rejectionBear || nearResistance)) score += 10;
            if (bull && brokeResistance) score += 10;
            if (!bull && brokeSupport) score += 10;
            // Pola candle / chart pattern di level S/R = konfluensi tambahan.
            if (atLevel && patternBull && bull) score += 10;
            if (atLevel && patternBear && !bull) score += 10;
            if (atLevel && chartAtLevel && bull && chartPattern == "Double Bottom") score += 10;
            if (atLevel && chartAtLevel && !bull && chartPattern == "Double Top") score += 10;
            // Konflik arah vs struktur adalah penalti terbesar; hanya boleh dilunasi
            // bila harga sudah retest FVG (konfluensi nyata, bukan sekadar harapan).
            if (conflict && !fvgRetest) score -= 25;
            return Math.Clamp(score, 0, 100);
        }

        var call = Score(true);
        var put = Score(false);
        var direction = call >= SmcFusionMinScore && call > put ? "CALL"
            : put >= SmcFusionMinScore && put > call ? "PUT" : "NO TRADE";
        var srLabel = rejectionBull ? "pantul S" : rejectionBear ? "pantul R"
            : brokeResistance ? "tembus R" : brokeSupport ? "tembus S"
            : nearSupport ? "dekat S" : nearResistance ? "dekat R" : "di tengah";
        var breakdown = $"CHoCH {(smcEngine.ChochDirection > 0 ? "↑" : smcEngine.ChochDirection < 0 ? "↓" : "—")}"
            + $" · BOS {(smcEngine.LastBosDirection > 0 ? "↑" : smcEngine.LastBosDirection < 0 ? "↓" : "—")}"
            + $" · FVG {(smcEngine.FvgDirection > 0 ? "BUY" : smcEngine.FvgDirection < 0 ? "SELL" : "—")}{(fvgRetest ? " retest" : "")}"
            + $" · S/R {srLabel}"
            + $" · pola {patternLabel}{chartLabel}"
            + $"{(conflict ? " · KONFLIK" : "")} · RSI {rsi:0} · MACD {(macd > 0 ? "+" : "-")} · Vol {volPercentile:0}%";
        return (call, put, direction, breakdown);
    }

    /// Port engine `smartmoneyconcepts` yang dipakai contoh Vibe-Trading:
    /// swing high/low berurutan, lalu BOS/CHoCH saat close menembus level swing,
    /// lalu FVG dari celah 3 candle. Semua hanya dari candle CLOSED.
    private static (int ChochDirection, int LastBosDirection, int FvgDirection, bool FvgRetest) AnalyzeSmcSwingEngine(
        IReadOnlyList<Candle> closedCandles, double price)
    {
        if (closedCandles.Count < 20) return (0, 0, 0, false);
        // 1) swing_highs_lows: pivot 1-1 (sama seperti contoh: swing_length=50
        //    diskalakan ke panjang data M1 yang tersedia).
        var length = Math.Clamp(closedCandles.Count / 6, 2, 10);
        var swingHigh = new double?[closedCandles.Count];
        var swingLow = new double?[closedCandles.Count];
        for (var index = length; index < closedCandles.Count - length; index++)
        {
            var isHigh = true;
            var isLow = true;
            for (var offset = 1; offset <= length; offset++)
            {
                if (closedCandles[index].High <= closedCandles[index - offset].High
                    || closedCandles[index].High <= closedCandles[index + offset].High) isHigh = false;
                if (closedCandles[index].Low >= closedCandles[index - offset].Low
                    || closedCandles[index].Low >= closedCandles[index + offset].Low) isLow = false;
            }
            if (isHigh) swingHigh[index] = closedCandles[index].High;
            if (isLow) swingLow[index] = closedCandles[index].Low;
        }

        // 2) bos_choch: close menembus swing high/low terakhir yang belum ditembus.
        // BOS = tembus searah struktur berjalan; CHoCH = tembus melawan struktur
        // berjalan (perubahan karakter). Level yang sudah ditembus dikonsumsi.
        var chochDirection = 0;
        var lastBosDirection = 0;
        var trendDirection = 0;
        double? pendingHigh = null, pendingLow = null;
        for (var index = 0; index < closedCandles.Count; index++)
        {
            var close = closedCandles[index].Close;
            if (pendingHigh is { } high && close > high)
            {
                if (trendDirection < 0) chochDirection = 1;
                lastBosDirection = 1;
                trendDirection = 1;
                pendingHigh = null;
            }
            if (pendingLow is { } low && close < low)
            {
                if (trendDirection > 0) chochDirection = -1;
                lastBosDirection = -1;
                trendDirection = -1;
                pendingLow = null;
            }
            if (swingHigh[index] is { } newHigh) pendingHigh = newHigh;
            if (swingLow[index] is { } newLow) pendingLow = newLow;
        }

        // 3) FVG: celah antara high[i-2] dan low[i] (bearish) atau low[i-2] dan
        //    high[i] (bullish) yang belum tertutup sampai candle terakhir.
        var fvgDirection = 0;
        double? fvgLow = null, fvgHigh = null;
        for (var index = closedCandles.Count - 3; index >= Math.Max(2, closedCandles.Count - 30); index--)
        {
            var older = closedCandles[index - 2];
            var newer = closedCandles[index];
            if (older.High < newer.Low) { fvgDirection = 1; fvgLow = older.High; fvgHigh = newer.Low; }
            else if (older.Low > newer.High) { fvgDirection = -1; fvgLow = newer.High; fvgHigh = older.Low; }
            else continue;
            // Batal bila celah sudah ditutup candle setelahnya.
            var filled = false;
            for (var later = index + 1; later < closedCandles.Count; later++)
            {
                if (fvgDirection > 0 && closedCandles[later].Low <= older.High) { filled = true; break; }
                if (fvgDirection < 0 && closedCandles[later].High >= older.Low) { filled = true; break; }
            }
            if (!filled) break;
            fvgDirection = 0;
            fvgLow = null;
            fvgHigh = null;
        }
        // Retest: harga live berada di dalam zona FVG yang masih terbuka.
        var fvgRetest = fvgDirection != 0 && fvgLow is not null && fvgHigh is not null
            && price >= fvgLow.Value && price <= fvgHigh.Value;
        return (chochDirection, lastBosDirection, fvgDirection, fvgRetest);
    }

    private static int CountMtfAlignment(IReadOnlyList<Candle> closedCandles, string direction)
    {
        var count = 0;
        foreach (var factor in new[] { 5, 15, 30, 60 })
        {
            var frame = AggregateCandles(closedCandles, factor);
            if (frame.Count < 10) continue;
            var trend = AnalyzeStructure(frame, frame.Count).Trend;
            if (trend == direction) count++;
        }
        return count;
    }


    private void DrawLevel(double price, string label, string color, Func<double, double> y, double left, double width, bool dashed = true)
    {
        var line = new Line { X1 = left, X2 = left + width, Y1 = y(price), Y2 = y(price), Stroke = (Brush)new BrushConverter().ConvertFromString(color)!, StrokeThickness = dashed ? 1 : 1.2, Opacity = dashed ? .8 : 1 };
        if (dashed) line.StrokeDashArray = [4, 3];
        Panel.SetZIndex(line, dashed ? 4 : 10);
        PriceChart.Children.Add(line);
        AddText($"{label} {FormatPrice(price)}", left + 5, y(price) - 17, color, 9, "#101722");
    }

#if DEBUG
    /// SMC FUSION 60 self-check: tren naik konsisten harus CALL > PUT, skor di
    /// rentang 0-100, breakdown terisi, dan dua arah tidak boleh lolos bersama.
    private static void VerifySmcFusionScoring()
    {
        var candles = Enumerable.Range(0, 90).Select(index => new Candle(index * 60d, 100 + index * .3, 100 + index * .3 + 1, 100 + index * .3 - .5, 100 + index * .3 + .6)).ToList();
        var closed = candles.Take(candles.Count - 1).ToList();
        var price = candles[^1].Close;
        var unit = Math.Max(price * 1e-5, 1e-9);
        var smc = AnalyzeSmcFull(closed, price, unit);
        var (call, put, _, breakdown) = EvaluateSmcFusion(closed, smc, price, unit);
        Debug.Assert(call > put, $"tren naik harus CALL > PUT (call={call}, put={put})");
        Debug.Assert(call <= 100 && put >= 0, "skor harus dalam rentang 0-100");
        Debug.Assert(!string.IsNullOrEmpty(breakdown), "breakdown harus terisi");
        Debug.Assert(!(call >= SmcFusionMinScore && put >= SmcFusionMinScore), "dua arah tidak boleh lolos bersamaan");
    }
#endif

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

    /// Panel DX tidak boleh mengubah arti sinyal: hanya menampilkan bacaan yang
    /// sudah dihitung dari candle closed. Cek cepat supaya klasifikasi regime
    /// tidak diam-diam berubah saat ambang disetel.
    private static void VerifySmcRegimeSnapshot()
    {
        static Candle Bar(double time, double price) => new(time, price, price + 1, price - 1, price);
        var flat = Enumerable.Range(0, 45).Select(index => Bar(index * 60, 100)).ToList();
        var flatRegime = SmcRegimeSnapshot(flat);
        Debug.Assert(flatRegime.Regime == "RANGING", "range sempit harus RANGING");
        Debug.Assert(flatRegime.VolatilityPercentile >= 0 && flatRegime.VolatilityPercentile <= 100);

        // Tren naik monoton: ADX harus menguat, bukan RANGING.
        var trend = Enumerable.Range(0, 60).Select(index => Bar(index * 60, 100 + index * .5)).ToList();
        Debug.Assert(SmcRegimeSnapshot(trend).Adx > 20, "tren monoton harus punya ADX > 20");
    }

    /// Aturan tick harus simetris, menolak data bolong, dan tidak membaca tick
    /// setelah waktu keputusan. Dijaga di sini karena keluarganya UNPROVEN.
    private static void VerifyTickReversal()
    {
        const double decide = 60;
        static List<(double Time, double Price)> Rising()
        {
            var list = Enumerable.Range(0, 51).Select(i => ((double)i, 100 + i * .05)).ToList();
            for (var i = 1; i <= 9; i++) list.Add((50 + i, 102.55 - i * .30));
            return list;
        }
        static List<(double Time, double Price)> Falling()
        {
            var list = Enumerable.Range(0, 51).Select(i => ((double)i, 100 - i * .05)).ToList();
            for (var i = 1; i <= 9; i++) list.Add((50 + i, 97.45 + i * .30));
            return list;
        }

        Debug.Assert(EvaluateTickReversal(Rising(), decide).Direction == "PUT", "pump lalu retrace harus PUT");
        Debug.Assert(EvaluateTickReversal(Falling(), decide).Direction == "CALL", "dump lalu retrace harus CALL");

        var flat = Enumerable.Range(0, 61).Select(i => ((double)i, 100d)).ToList();
        Debug.Assert(EvaluateTickReversal(flat, decide).Direction is null, "harga flat harus WAIT");

        var gapped = Enumerable.Range(0, 20).Select(i => ((double)i, 100 + i * .05)).ToList();
        gapped.Add((40, 101.5));
        for (var i = 1; i <= 9; i++) gapped.Add((50 + i, 102.55 - i * .30));
        Debug.Assert(EvaluateTickReversal(gapped, decide).Direction is null, "gap data harus WAIT");

        var early = EvaluateTickReversal(Rising(), decide - 5).Direction;
        var withFuture = Rising().Concat(Enumerable.Range(61, 20).Select(i => ((double)i, 90d))).ToList();
        Debug.Assert(EvaluateTickReversal(withFuture, decide - 5).Direction == early, "tick masa depan tidak boleh dipakai");
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
        var signalColor = connected ? Color.FromRgb(37, 200, 154) : updating ? Color.FromRgb(240, 185, 11) : Color.FromRgb(244, 91, 105);
        SignalStatusText.Text = connected ? "RUNNING" : updating ? "SYNCING" : "OFFLINE";
        SignalStatusText.Foreground = new SolidColorBrush(signalColor);
        SignalStatusDot.Fill = new SolidColorBrush(signalColor);
        LiveDot.Fill = new SolidColorBrush(signalColor);
        StatusText.Text = detail;
        ConnectionText.Opacity = 1;
        if (updating && !updatingAnimationActive)
        {
            updatingAnimationActive = true;
            var animation = new System.Windows.Media.Animation.DoubleAnimation(0.25, 1, new Duration(TimeSpan.FromMilliseconds(700)))
            {
                AutoReverse = true,
                RepeatBehavior = System.Windows.Media.Animation.RepeatBehavior.Forever
            };
            ConnectionText.BeginAnimation(OpacityProperty, animation);
        }
        else if (!updating && updatingAnimationActive)
        {
            updatingAnimationActive = false;
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
    private async void MarketList_SelectionChanged(object sender, SelectionChangedEventArgs e) { if (!marketsRebuilding && MarketList.SelectedItem is MarketRow row) await SwitchSymbolAsync(row.Symbol); }
    private async Task SwitchSymbolAsync(string symbol)
    {
        if (string.IsNullOrEmpty(symbol) || symbol == selectedSymbol) return;
        selectedSymbol = symbol; firstVisible = 0; nextNewsRefresh = DateTime.MinValue; nextMarketRefresh = DateTime.MinValue;
        // Tick milik simbol lama tidak boleh dipakai simbol baru.
        tickHistory.Clear();
        chartAlertsReady = false;
        chartAlerts.Clear();
        alertDedup.Clear();
        currentPreviewDirection = "";
        sourceCandles = []; chartCandles = []; PriceChart.Children.Clear(); ChartLoadingText.Text = "LOADING MARKET..."; ChartStatusOverlay.Visibility = Visibility.Visible; PriceChart.Visibility = Visibility.Collapsed;
        ChartInfoText.Text = $"{symbol} · memuat candle M1 brokerâ€¦";
        await RefreshFeed();
    }
    private async void TimeframeBox_SelectionChanged(object sender, SelectionChangedEventArgs e) { if (!IsLoaded || TimeframeBox.SelectedItem is null) return; firstVisible = 0; await RefreshFeed(); }

    private void Strategy_SelectionChanged(object sender, SelectionChangedEventArgs e) { if (!IsLoaded) return; UpdateStrategyControls(); DrawChart(); }
    private void OverlayChanged(object sender, RoutedEventArgs e) { if (IsLoaded) DrawChart(); }

    private void LoadUiSettings()
    {
        try
        {
            if (!File.Exists(UiSettingsPath)) return;
            var settings = JsonSerializer.Deserialize<UiSettings>(File.ReadAllText(UiSettingsPath));
            if (settings is null) return;
            if (!string.IsNullOrWhiteSpace(settings.DisclaimerText)) disclaimerText = settings.DisclaimerText;
            DisclaimerOverlayBox.IsChecked = settings.DisclaimerVisible;
            // Aturan entry: nilai rusak diabaikan, default tetap berlaku.
            if (settings.EntryPercent is > 0 and <= 100) demoEntryPercent = settings.EntryPercent.Value;
            if (settings.OrderMinAmount is > 0) demoOrderMinAmount = settings.OrderMinAmount.Value;
            if (settings.OrderMaxAmount is > 0) demoOrderMaxAmount = settings.OrderMaxAmount.Value;
            ClampDemoOrderRules();
        }
        catch (Exception error)
        {
            // Pengaturan rusak tidak boleh menggagalkan start; pakai default.
            SetStatus("disconnected", "ui-settings.json tidak terbaca: " + error.Message);
        }
    }

    private void SaveUiSettings()
    {
        try
        {
            Directory.CreateDirectory(System.IO.Path.Combine(Root, "data"));
            File.WriteAllText(UiSettingsPath,
                JsonSerializer.Serialize(new UiSettings(DisclaimerOverlayBox.IsChecked == true, disclaimerText,
                    demoEntryPercent, demoOrderMinAmount, demoOrderMaxAmount),
                    new JsonSerializerOptions { WriteIndented = true }));
        }
        catch (Exception error)
        {
            SetStatus("disconnected", "gagal menyimpan ui-settings.json: " + error.Message);
        }
    }

    private void DisclaimerChanged(object sender, RoutedEventArgs e)
    {
        if (!IsLoaded) return;
        ApplyDisclaimerVisual();
        SaveUiSettings();
    }

    private void DisclaimerEdit_Click(object sender, RoutedEventArgs e)
    {
        var input = new TextBox
        {
            Text = disclaimerText,
            Width = 420,
            FontFamily = new FontFamily("Consolas"),
            FontSize = 14,
            Padding = new Thickness(6, 4, 6, 4),
        };
        var panel = new StackPanel { Margin = new Thickness(14) };
        panel.Children.Add(new TextBlock
        {
            Text = "Teks disclaimer yang tampil di chart:",
            Margin = new Thickness(0, 0, 0, 8),
        });
        panel.Children.Add(input);
        var okButton = new Button { Content = "Simpan", IsDefault = true, Width = 90, Margin = new Thickness(0, 12, 8, 0), HorizontalAlignment = HorizontalAlignment.Right };
        var cancelButton = new Button { Content = "Batal", IsCancel = true, Width = 90, Margin = new Thickness(0, 12, 0, 0), HorizontalAlignment = HorizontalAlignment.Right };
        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right };
        buttons.Children.Add(okButton);
        buttons.Children.Add(cancelButton);
        panel.Children.Add(buttons);
        var dialog = new Window
        {
            Title = "Edit Teks Disclaimer",
            Content = panel,
            SizeToContent = SizeToContent.WidthAndHeight,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = this,
            ResizeMode = ResizeMode.NoResize,
            Background = (Brush)new BrushConverter().ConvertFromString("#0B111A")!,
        };
        dialog.Resources.Add(typeof(TextBlock), new Style(typeof(TextBlock)) { Setters = { new Setter(TextBlock.ForegroundProperty, (Brush)new BrushConverter().ConvertFromString("#E5EBF4")!) } });
        okButton.Click += (_, _) =>
        {
            var text = input.Text.Trim();
            disclaimerText = string.IsNullOrEmpty(text) ? "JUDOL" : text;
            dialog.DialogResult = true;
        };
        if (dialog.ShowDialog() == true) { SaveUiSettings(); ApplyDisclaimerVisual(); }
        input.Focus();
        input.SelectAll();
    }

    /// <summary>Teks peringatan di tengah atas chart. Murni kosmetik: tidak
    /// menyentuh data broker, sinyal, atau API. Animasi opacity berjalan pada
    /// satu elemen sehingga chart tidak perlu digambar ulang setiap kedipan.</summary>
    private void ApplyDisclaimerVisual()
    {
        var visible = DisclaimerOverlayBox.IsChecked == true;
        var text = string.IsNullOrWhiteSpace(disclaimerText) ? "JUDOL" : disclaimerText;
        DisclaimerOverlayText.Text = text;
        DisclaimerOverlayText.Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
        if (visible)
        {
            var blink = new DoubleAnimation
            {
                From = 1.0,
                To = 0.18,
                Duration = TimeSpan.FromMilliseconds(700),
                AutoReverse = true,
                RepeatBehavior = RepeatBehavior.Forever,
            };
            DisclaimerOverlayText.BeginAnimation(OpacityProperty, blink);
        }
        else
        {
            DisclaimerOverlayText.BeginAnimation(OpacityProperty, null);
            DisclaimerOverlayText.Opacity = 1.0;
        }
    }

    private void AutoEndChanged(object sender, RoutedEventArgs e) { if (IsLoaded && AutoEndBox.IsChecked == true) ChartReset_Click(sender, e); }
    private void PriceChart_SizeChanged(object sender, SizeChangedEventArgs e) => DrawChart();
    private void ChartZoomIn_Click(object sender, RoutedEventArgs e) { AutoEndBox.IsChecked = true; visibleBars = Math.Max(5, (int)(visibleBars / 1.25)); firstVisible = Math.Max(0, chartCandles.Count - visibleBars); DrawChart(); }
    private void ChartZoomOut_Click(object sender, RoutedEventArgs e) { AutoEndBox.IsChecked = true; visibleBars = Math.Min(Math.Max(5, chartCandles.Count), (int)(visibleBars * 1.25)); firstVisible = Math.Max(0, chartCandles.Count - visibleBars); DrawChart(); }
    private void ChartReset_Click(object sender, RoutedEventArgs e) { AutoEndBox.IsChecked = true; visibleBars = Math.Min(24, Math.Max(5, chartCandles.Count)); firstVisible = Math.Max(0, chartCandles.Count - visibleBars); DrawChart(); }
    private void PriceChart_MouseWheel(object sender, MouseWheelEventArgs e) { if (e.Delta > 0) ChartZoomIn_Click(sender, e); else ChartZoomOut_Click(sender, e); e.Handled = true; }
    private void PriceChart_MouseDown(object sender, MouseButtonEventArgs e) { if (e.ChangedButton != MouseButton.Left) return; panning = true; panStart = e.GetPosition(PriceChart); panStartFirst = firstVisible; PriceChart.CaptureMouse(); }
    private void PriceChart_MouseMove(object sender, System.Windows.Input.MouseEventArgs e)
    {
        hoverPoint = e.GetPosition(PriceChart);
        hasHover = true;
        if (!panning)
        {
            DrawCrosshair();
            return;
        }

        var delta = hoverPoint.X - panStart.X;
        if (Math.Abs(delta) > 3) AutoEndBox.IsChecked = false;
        firstVisible = Math.Clamp(panStartFirst - (int)Math.Round(delta * visibleBars / Math.Max(1, PriceChart.ActualWidth)), 0, Math.Max(0, chartCandles.Count - visibleBars));
        DrawChart();
    }
    private void PriceChart_MouseUp(object sender, MouseButtonEventArgs e) { panning = false; PriceChart.ReleaseMouseCapture(); }

    /// Preview is recalculated from the current, broker-fed M1 candle. It becomes
    /// immutable only inside the lock window; no order is sent by this method.
    private void UpdateEntrySignal(JsonElement feed)
    {
        var remaining = SecondsLeftInCandle();
        var active = CurrentActiveStrategy();
        if (active == ActiveStrategy.Off)
        {
            currentPreviewDirection = "";
            SetSignal("OFF", "#59728E", remaining, "Pilih strategi di INDIKATOR > STRATEGI");
            return;
        }
        if (active == ActiveStrategy.LookAhead)
        {
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", remaining, "LOOK-AHEAD hanya historis · tidak valid untuk feed live");
            return;
        }
        if (!string.Equals(Text(feed, "status"), "live", StringComparison.OrdinalIgnoreCase)
            || !feed.TryGetProperty("liveSymbols", out var liveSymbols)
            || liveSymbols.ValueKind != JsonValueKind.Array
            || !liveSymbols.EnumerateArray().Any(item => string.Equals(item.GetString(), selectedSymbol, StringComparison.OrdinalIgnoreCase)))
        {
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", remaining, "Market/feed belum live untuk simbol ini");
            return;
        }
        if (!feed.TryGetProperty("ticks", out var ticks) || ticks.ValueKind != JsonValueKind.Object
            || !ticks.TryGetProperty(selectedSymbol, out var selectedTick)
            || Number(selectedTick, "price") is not > 0
            || Number(selectedTick, "time") is not double selectedTickTime)
        {
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", remaining, "Tick simbol ini belum tersedia");
            return;
        }
        var tickAge = BrokerNow() - selectedTickTime;
        if (tickAge < -1 || tickAge > 3)
        {
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", remaining, $"Tick stale ({tickAge:0}s) · tunggu update");
            return;
        }
        if (chartCandles.Count < 26 && active != ActiveStrategy.TickReversal)
        {
            currentPreviewDirection = "";
            SetSignal("WAIT", "#59728E", remaining, "Butuh minimal 26 candle M1");
            return;
        }

        var candle = chartCandles[^1];
        // SMC FUSION 60: struktur hanya dari candle CLOSED (tanpa candle berjalan),
        // evaluasi pada jendela detik 58-59, fail closed bila tidak ada tick valid.
        // Cooldown dan session cap dicek di sini seperti strategi lain.
        if (active == ActiveStrategy.SmcFusion)
        {
            var previousFusion = firedStrategies.Keys
                .Where(key => key.Symbol == selectedSymbol && key.Strategy == ActiveStrategy.SmcFusion)
                .Select(key => key.SignalTime)
                .ToList();
            var sinceFusion = previousFusion.Count == 0 ? double.PositiveInfinity : (candle.Time - previousFusion.Max()) / 60.0;
            if (sinceFusion < SignalCooldownBars)
            {
                currentPreviewDirection = "";
                SetSignal("COOLDOWN", "#59728E", remaining, $"{Math.Max(0, SignalCooldownBars - (int)sinceFusion)} candle lagi · {StrategyName(active)}");
                return;
            }
            if (SessionEntryCount(selectedSymbol, ActiveStrategy.SmcFusion) >= SessionMaxEntries)
            {
                currentPreviewDirection = "";
                SetSignal("SESSION MAX", "#E7B65A", remaining,
                    $"Batas {SessionMaxEntries} entry per sesi tercapai · jangan kejar balik · {StrategyName(active)}");
                return;
            }
            EvaluateSmcFusionSignal(candle, remaining);
            return;
        }
        if (active != ActiveStrategy.TickReversal && candle.Time != Math.Floor(selectedTickTime / 60) * 60)
        {
            currentPreviewDirection = "";
            SetSignal("NO TRADE", "#E7B65A", remaining, "Candle M1 tidak sinkron dengan tick terbaru");
            return;
        }
        var candleKey = (selectedSymbol, candle.Time);
        if (lockedSignals.TryGetValue(candleKey, out var existingLock))
        {
            currentPreviewDirection = existingLock.Direction;
            SetSignal($"LOCKED {existingLock.Direction}", existingLock.Direction == "CALL" ? "#25C89A" : "#F45B69", remaining,
                $"{StrategyName(existingLock.Strategy)} · entry manual pada candle M1 berikutnya");
            return;
        }

        // Strategi tick tidak memakai candle tertutup: dibaca dari tick nyata pada
        // waktu keputusan, jadi jalur evaluasinya terpisah.
        string? direction;
        string reason;
        if (active == ActiveStrategy.TickReversal)
        {
            var tickSignal = TickExhaustionDirection(selectedTickTime);
            direction = tickSignal.Direction;
            reason = tickSignal.Reason;
        }
        else
        {
            direction = EvaluateStrategyDirection(active, chartCandles.Count - 1, out reason);
        }
        if (direction is null)
        {
            currentPreviewDirection = "";
            if (active == ActiveStrategy.DualThrust)
            {
                SetSignal("WAIT DT", "#8DA3BF", remaining, $"Prediksi next candle bersyarat · {reason}");
            }
            else
            {
                SetSignal(active == ActiveStrategy.ZScore ? "NO TRADE" : "NO SETUP", "#59728E", remaining, reason);
            }
            return;
        }

        var previousSignals = firedStrategies.Keys
            .Where(key => key.Symbol == selectedSymbol && key.Strategy == active)
            .Select(key => key.SignalTime)
            .ToList();
        var sinceLast = previousSignals.Count == 0 ? double.PositiveInfinity : (candle.Time - previousSignals.Max()) / 60.0;
        if (sinceLast < SignalCooldownBars)
        {
            currentPreviewDirection = "";
            SetSignal("COOLDOWN", "#59728E", remaining, $"{Math.Max(0, SignalCooldownBars - (int)sinceLast)} candle lagi · {StrategyName(active)}");
            return;
        }

        if (SessionEntryCount(selectedSymbol, active) >= SessionMaxEntries)
        {
            currentPreviewDirection = "";
            SetSignal("SESSION MAX", "#E7B65A", remaining,
                $"Batas {SessionMaxEntries} entry per sesi tercapai · jangan kejar balik · {StrategyName(active)}");
            return;
        }

        ApplySignal(active, direction, direction == "CALL" ? "#25C89A" : "#F45B69", remaining, reason);
    }

    // SMC FUSION 60: skor dari candle CLOSED (anti-lookahead), lock hanya pada
    // detik 58-59. Bila tidak ada tick valid di jendela itu → NO TRADE.
    private void EvaluateSmcFusionSignal(Candle liveCandle, int remaining)
    {
        var closed = chartCandles.Take(chartCandles.Count - 1).ToList();
        if (closed.Count < 60)
        {
            currentPreviewDirection = "";
            SetSignal("WAIT", "#59728E", remaining, $"SMC FUSION 60 · butuh 60 candle tertutup ({closed.Count})");
            return;
        }
        var price = liveCandle.Close;
        var unit = Math.Max(price * 1e-5, 1e-9);
        var smc = AnalyzeSmcFull(closed, price, unit);
        var (call, put, direction, breakdown) = EvaluateSmcFusion(closed, smc, price, unit);
        fusionScores[(selectedSymbol, liveCandle.Time)] = Math.Max(call, put);
        // Kalibrasi ambang: skor ditulis sekali per candle closed. Tanpa data ini
        // ambang 85 hanya tebakan — nyatanya skor live jarang lewat 60.
        fusionScoreLog[(selectedSymbol, liveCandle.Time)] = (call, put);
        if (DateTime.UtcNow >= lastFusionScoreLogWrite)
        {
            lastFusionScoreLogWrite = DateTime.UtcNow.AddSeconds(30);
            FlushFusionScoreLog();
        }
        var detail = $"CALL {call} · PUT {put} · {breakdown}";
        lastFusionBreakdown = detail;

        // PREPARE sederhana (binary option 1M): satu alert per candle saat satu
        // arah sudah mencapai ambang persiapan pada jendela T-15..T-6, sebelum
        // lock di 58-59. Entry = open candle M1 berikutnya, expiry = close-nya.
        if (remaining is >= 6 and <= 15)
        {
            var prepareDirection = call >= SmcFusionPrepareScore && call > put ? "CALL"
                : put >= SmcFusionPrepareScore && put > call ? "PUT" : "";
            if (prepareDirection != "" && fusionPrepareDedup.Add($"{selectedSymbol}|{liveCandle.Time:0}"))
            {
                PushChartAlert($"PREPARE {prepareDirection}", prepareDirection == "CALL" ? "UP" : "DOWN", candleTime: liveCandle.Time,
                    $"skor {Math.Max(call, put)} · tunggu lock · entry open M1 berikutnya · expiry close 1 menit", force: true);
            }
            if (prepareDirection != "")
            {
                currentPreviewDirection = "";
                SetSignal($"PREPARE {prepareDirection}", prepareDirection == "CALL" ? "#25C89A" : "#F45B69", remaining,
                    $"FUSION 60 skor {Math.Max(call, put)} · lock di detik 58-59 · entry open M1 berikutnya · expiry close");
                return;
            }
        }

        if (remaining > 2)
        {
            currentPreviewDirection = "";
            SetSignal("SCANNING", "#8DA3BF", remaining, $"FUSION 60 · CALL {call} / PUT {put} · evaluasi detik 58-59");
            return;
        }
        if (direction == "NO TRADE")
        {
            currentPreviewDirection = "";
            // Semua candle dicatat, termasuk yang NO TRADE, supaya kondisi WIN
            // dan LOSS bisa dibandingkan pada populasi yang sama.
            fusionSnapshots[(selectedSymbol, liveCandle.Time)] = (call, put, breakdown, direction);
            SetSignal("NO TRADE", "#E7B65A", remaining, $"Skor <{SmcFusionMinScore} atau dua arah lolos · {detail}");
            return;
        }
        fusionSnapshots[(selectedSymbol, liveCandle.Time)] = (call, put, breakdown, direction);
        ApplySignal(ActiveStrategy.SmcFusion, direction, direction == "CALL" ? "#25C89A" : "#F45B69", remaining, $"FUSION 60 skor {Math.Max(call, put)} · {breakdown}");
    }
    private string lastFusionBreakdown = "";
    /// Kondisi lengkap tiap candle: dipakai untuk mencatat kondisi saat WIN/LOSS.
    private readonly Dictionary<(string Symbol, double Time), (int Call, int Put, string Breakdown, string Direction)> fusionSnapshots = [];
    /// Dedup alert PREPARE fusion: satu per (simbol, candle).
    private readonly HashSet<string> fusionPrepareDedup = [];
    /// Kalibrasi ambang: catat skor tertinggi tiap candle closed sekali saja.
    private readonly Dictionary<(string Symbol, double Time), (int Call, int Put)> fusionScoreLog = [];
    private DateTime lastFusionScoreLogWrite = DateTime.MinValue;

    /// Skor per (symbol, signalTime). Diisi saat lock write-once; dipakai untuk
    /// jurnal fusion dan statistik by-score-range. Write-once supaya skor yang
    /// sudah terbit tidak berubah saat redraw.
    private readonly Dictionary<(string Symbol, double SignalTime), int> fusionScores = [];

    /// Kalibrasi ambang: skor ditulis sekali per candle closed. Tanpa data ini
    /// ambang 85 hanya tebakan — nyatanya skor live jarang lewat 60.
    private void FlushFusionScoreLog()
    {
        try
        {
            var path = System.IO.Path.Combine(AppContext.BaseDirectory, "data", "fusion-scores.csv");
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(path)!);
            var exists = File.Exists(path);
            using var writer = new StreamWriter(path, append: true);
            if (!exists) writer.WriteLine("market,candle_time,call,put,best");
            foreach (var ((symbol, time), scores) in fusionScoreLog)
                writer.WriteLine($"{symbol},{DateTimeOffset.FromUnixTimeSeconds((long)time).UtcDateTime:O},{scores.Call},{scores.Put},{Math.Max(scores.Call, scores.Put)}");
            // Sudah ditulis; kosongkan agar tidak duplikat di flush berikutnya.
            fusionScoreLog.Clear();
        }
        // Gagal tulis bukan alasan mematikan aplikasi, tapi tidak boleh hilang diam-diam:
        // log ke Debug supaya bisa dilacak saat data jurnal tidak lengkap.
        catch (Exception ex) { System.Diagnostics.Debug.WriteLine($"flush fusion journal gagal: {ex.Message}"); }
    }

    private static void AppendFusionJournalHeader()
    {
        if (!File.Exists(FusionJournalPath))
        {
            File.WriteAllText(FusionJournalPath,
                "signal_id,market,direction,score,signal_time,entry_time,entry_price,expiry_time,expiry_price,outcome,conditions\n");
            return;
        }
        // Jurnal lama ditulis sebelum kolom `conditions` ada. Naikkan header-nya
        // tanpa mengubah baris lama (baris lama tetap 10 kolom dan dibaca
        // sebagai kondisi "NA"), supaya file tetap CSV yang sah.
        try
        {
            var lines = File.ReadAllLines(FusionJournalPath);
            if (lines.Length == 0) return;
            if (lines[0].Contains("conditions", StringComparison.Ordinal)) return;
            lines[0] += ",conditions";
            File.WriteAllLines(FusionJournalPath, lines);
        }
        catch (IOException) { /* best-effort */ }
    }

    /// Kondisi lengkap saat sinyal, untuk membandingkan WIN vs LOSS pada
    /// populasi yang sama. Titik koma memisahkan kondisi agar koma tetap
    /// menjadi pemisah CSV.
    private string FusionConditionsOf(string symbol, double signalTime)
    {
        if (!fusionSnapshots.TryGetValue((symbol, signalTime), out var snapshot)) return "NA";
        var conditions = string.Join(";",
            snapshot.Breakdown.Replace(',', '/'),
            $"skor {Math.Max(snapshot.Call, snapshot.Put)}",
            $"call {snapshot.Call}",
            $"put {snapshot.Put}");
        return conditions.Replace(",", "/");
    }

    /// Tick terakhir <= expiry. Null bila tidak ada tick valid (fail closed,
    /// jangan fabrikasi harga).
    private double? LastTickAtOrBefore(double expiryTime)
    {
        double? best = null;
        foreach (var (time, price) in tickHistory)
        {
            if (time <= expiryTime) best = price;
            else break;
        }
        return best;
    }

    private void RecordFusionEntry(string symbol, double signalTime, string direction, Candle entry, string outcome)
    {
        try
        {
            AppendFusionJournalHeader();
            var entryTime = entry.Time;
            var expiryTime = entryTime + 60;
            var expiryPrice = LastTickAtOrBefore(expiryTime);
            var id = $"F60-{symbol}-{signalTime:0}";
            var expiryField = expiryPrice?.ToString("R", CultureInfo.InvariantCulture) ?? "NA";
            var outcomeField = expiryPrice is null ? "INVALID" : outcome;
            var conditions = FusionConditionsOf(symbol, signalTime);
            File.AppendAllText(FusionJournalPath,
                FormattableString.Invariant(
                    $"{id},{symbol},{direction},{fusionScores.GetValueOrDefault((symbol, signalTime), 0)},{signalTime:0},{entryTime:0},{entry.Open},{expiryTime:0},{expiryField},{outcomeField},{conditions}\n"));
        }
        catch (IOException) { /* best-effort */ }
    }

    /// Statistik lanjutan fusion: winrate CALL/PUT/gabung, expectancy, streak,
    /// by score range, by market — dibaca dari fusion-journal.csv. Dibatasi satu
    /// kali per 30 detik supaya CSV tidak dibaca tiap redraw (perf, bukan kebenaran).
    private DateTime nextFusionStatsRefresh = DateTime.MinValue;
    private void UpdateFusionStats()
    {
        if (DateTime.UtcNow < nextFusionStatsRefresh) return;
        nextFusionStatsRefresh = DateTime.UtcNow.AddSeconds(30);
        try
        {
            if (!File.Exists(FusionJournalPath)) return;
            var rows = File.ReadAllLines(FusionJournalPath).Skip(1)
                .Select(line => line.Split(','))
                .Where(f => f.Length >= 10)
                .Select(f => new { Score = int.TryParse(f[3], out var s) ? s : 0, Market = f[1], Direction = f[2], Outcome = f[9] })
                .Where(r => r.Outcome is "WIN" or "LOSS" or "DRAW")
                .ToList();
            var wins = rows.Count(r => r.Outcome == "WIN");
            var losses = rows.Count(r => r.Outcome == "LOSS");
            var draws = rows.Count(r => r.Outcome == "DRAW");
            var decided = wins + losses;
            var calls = rows.Where(r => r.Direction == "CALL").ToList();
            var puts = rows.Where(r => r.Direction == "PUT").ToList();
            var callWin = calls.Count > 0 ? calls.Count(r => r.Outcome == "WIN") * 100d / calls.Count : 0;
            var putWin = puts.Count > 0 ? puts.Count(r => r.Outcome == "WIN") * 100d / puts.Count : 0;
            var combined = decided > 0 ? wins * 100d / decided : 0;
            var expectancy = decided > 0 ? (wins - losses) * 100d / decided : 0;
            var maxStreak = 0; var streak = 0;
            foreach (var r in rows.Where(r => r.Outcome is "WIN" or "LOSS"))
            {
                streak = r.Outcome == "LOSS" ? streak + 1 : 0;
                maxStreak = Math.Max(maxStreak, streak);
            }
            var byScore = string.Join(" · ", new[] { (85, 89), (90, 94), (95, 100) }.Select(range =>
            {
                var bucket = rows.Where(r => r.Score >= range.Item1 && r.Score <= range.Item2 && r.Outcome is "WIN" or "LOSS").ToList();
                return bucket.Count == 0 ? $"{range.Item1}-{range.Item2}:—" : $"{range.Item1}-{range.Item2}:{bucket.Count(r => r.Outcome == "WIN") * 100 / bucket.Count:0}%({bucket.Count})";
            }));
            FusionStatsText.Text = $"FUSION 60 · CALL {callWin:0}%({calls.Count}) · PUT {putWin:0}%({puts.Count}) · GABUNG {combined:0}% · EXPECTANCY {expectancy:+0;-0} · MAX LOSS STREAK {maxStreak} · SCORE: {byScore}";
        }
        catch (IOException) { /* best-effort */ }
    }

    private string? EvaluateStrategyDirection(ActiveStrategy strategy, int index, out string reason)
    {
        reason = $"{StrategyName(strategy)} · sinyal indikator (belum terbukti edge)";
        var direction = HistoricalDirection(strategy, index);
        if (strategy == ActiveStrategy.ZScore && direction is null)
        {
            _ = EvaluateZScoreVolatility(chartCandles.Take(index + 1).ToList(), chartCandles[index], out reason);
        }
        else if (strategy == ActiveStrategy.DualThrust)
        {
            _ = DualThrustDirection(chartCandles.Take(index + 1).ToList(), out reason);
        }
        else if (strategy == ActiveStrategy.SnR5Break)
        {
            _ = SnR5Direction(chartCandles.Take(index + 1).ToList(), out reason);
        }
        return direction;
    }

    /// Backtested Z-score rule plus the matching high-volatility gate.
    /// The median baseline excludes the current running candle.
    /// Session rules (2026-11-06): signal is locked at T-5 before candle A
    /// closes and executed on candle B (see SignalLockSeconds), vol LO is
    /// blocked here, cooldown 5 bars and the 10-entry session cap are
    /// enforced in UpdateEntrySignal before ApplySignal locks anything.
    private string? EvaluateZScoreVolatility(IReadOnlyList<Candle> candles, Candle live, out string reason)
    {
        reason = "NO TRADE · data belum cukup";
        if (candles.Count < 61 || live.Close <= 0) return null;

        var closes = candles.Select(candle => candle.Close).ToList();
        var mean = closes.TakeLast(20).Average();
        var changes = new List<double>(20);
        for (var index = candles.Count - 20; index < candles.Count; index++)
            if (index > 0) changes.Add(Math.Abs(closes[index] - closes[index - 1]));
        if (changes.Count != 20) return null;
        var averageMove = changes.Average();
        if (averageMove <= 0) { reason = "NO TRADE · pergerakan rata-rata nol"; return null; }

        var z = (live.Close - mean) / averageMove;
        var baselineRanges = candles.Skip(candles.Count - 61).Take(60)
            .Where(candle => candle.Close > 0)
            .Select(candle => (candle.High - candle.Low) / candle.Close)
            .OrderBy(value => value)
            .ToList();
        if (baselineRanges.Count < 60) { reason = "NO TRADE · baseline volatilitas belum cukup"; return null; }

        var liveRange = (live.High - live.Low) / live.Close;
        var medianRange = baselineRanges[baselineRanges.Count / 2];
        reason = $"Z {z:+0.00;-0.00;0.00} · range {(liveRange > medianRange ? "HI" : "LO")} · median {medianRange:P3}";
        if (liveRange <= medianRange) { reason = "NO TRADE · volatilitas rendah · " + reason; return null; }
        if (z < -2) return "CALL";
        if (z > 2) return "PUT";
        reason = "NO TRADE · z belum mencapai ±2 · " + reason;
        return null;
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

    /// Preview is reversible. A locked signal is write-once at T-5s and is for
    /// manual entry on the next M1 candle; it never sends a broker order.
    private void ApplySignal(ActiveStrategy strategy, string direction, string colour, int remaining, string reason)
    {
        var last = chartCandles[^1];
        var candleKey = (selectedSymbol, last.Time);
        if (lockedSignals.TryGetValue(candleKey, out var existingLock))
        {
            currentPreviewDirection = existingLock.Direction;
            SetSignal($"LOCKED {existingLock.Direction}", existingLock.Direction == "CALL" ? "#25C89A" : "#F45B69", remaining,
                $"{StrategyName(existingLock.Strategy)} · entry manual pada candle M1 berikutnya");
            return;
        }

        currentPreviewDirection = direction;
        currentPreviewReason = reason;
        if (remaining > SignalLockSeconds)
        {
            var label = remaining > SignalPreviewSeconds ? "PREVIEW" : "READY";
            SetSignal($"{label} {direction}", remaining > SignalPreviewSeconds ? "#8DA3BF" : colour, remaining,
                $"Belum terkunci · {reason}");
            return;
        }
        if (remaining < SignalMinimumEntryMarginSeconds)
        {
            currentPreviewDirection = "";
            SetSignal("MISSED", "#E7B65A", remaining, "Terlambat dikunci · tunggu setup candle M1 berikutnya");
            return;
        }

        lockedSignals[candleKey] = (strategy, direction);
        firedStrategies[(selectedSymbol, strategy, last.Time)] = direction;
        firedEntries[candleKey] = direction;
        RegisterSessionEntry(selectedSymbol, strategy);
        if (strategy == ActiveStrategy.Forex && BuildForexPlan(chartCandles) is ForexPlan forexPlan)
            forexPlans[(selectedSymbol, last.Time)] = forexPlan;
        SetSignal($"LOCKED {direction}", colour, remaining, $"T-{remaining}s · OP NEXT CANDLE 01s · RESULT CLOSE · {reason}");
        PushChartAlert("LOCKED ENTRY " + direction, direction == "CALL" ? "UP" : "DOWN", candleTime: last.Time,
            $"{StrategyName(strategy)} · entry manual di candle M1 berikutnya · {reason}", force: true);
        DrawChart();
    }

    private void SubmitDueDemoOrders()
    {
        var active = CurrentActiveStrategy();
        if (active == ActiveStrategy.Off || chartCandles.Count == 0) return;
        var now = BrokerNow();
        foreach (var item in firedStrategies.Where(item => item.Key.Symbol.Equals(selectedSymbol, StringComparison.OrdinalIgnoreCase)
                     && item.Key.Strategy == active
                     && now >= item.Key.SignalTime + TimeframeMinutes() * 60
                     && now < item.Key.SignalTime + TimeframeMinutes() * 60 + 8
                     && demoOrdersSent.Add(item.Key)).ToList())
        {
            _ = PlaceDemoOrderAsync(item.Key, item.Value);
        }
    }

    private async Task PlaceDemoOrderAsync((string Symbol, ActiveStrategy Strategy, double SignalTime) key, string direction)
    {
        try
        {
            var amount = NextDemoOrderAmount(lastBalance);
            if (amount is null)
            {
                SetSignal("WAIT", "#E7B65A", SecondsLeftInCandle(), "DEMO order ditunda · saldo belum tersedia");
                return;
            }
            using var request = new HttpRequestMessage(HttpMethod.Post, ApiUrl + "/api/pocket/order")
            {
                Content = new StringContent(JsonSerializer.Serialize(new
                {
                    asset = key.Symbol,
                    direction = direction == "CALL" ? "call" : "put",
                    amount = amount.Value,
                    expiration = DemoOrderExpirationSeconds,
                }), Encoding.UTF8, "application/json")
            };
            request.Headers.Add("x-api-key", apiKey.Trim());
            using var response = await http.SendAsync(request);
            var body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                SetStatus("disconnected", "DEMO order gagal: " + body);
                return;
            }
            var percentUsed = amount.Value / Math.Max(1.0, (double)(lastBalance ?? 0)) * 100;
            SetSignal($"DEMO {direction}", direction == "CALL" ? "#25C89A" : "#F45B69", SecondsLeftInCandle(),
                $"ORDER BROKER DEMO · {key.Symbol} · ${amount.Value:0} ({percentUsed:0}% saldo) · 60s · entry candle berikutnya");
            PushChartAlert($"DEMO {direction} ${amount.Value:0}", direction == "CALL" ? "UP" : "DOWN", candleTime: key.SignalTime,
                $"{StrategyName(key.Strategy)} · ${amount.Value:0} · {percentUsed:0}% saldo · DEMO", force: true);
        }
        catch (Exception error)
        {
            SetStatus("disconnected", "DEMO order gagal: " + error.Message);
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

    /// Skor per (strategy, signal candle time). Semua strategi memakai kontrak
    /// yang sama: sinyal dikunci pada candle A, entry adalah candle B, lalu
    /// hasil hanya dihitung setelah candle B benar-benar selesai.
    private void ResolveEntries()
    {
        var active = CurrentActiveStrategy();
        foreach (var (key, direction) in firedStrategies.ToList())
        {
            if (signalEntries.ContainsKey(key)) continue;
            if (!string.Equals(key.Symbol, selectedSymbol, StringComparison.OrdinalIgnoreCase)) continue;
            if (CurrentSector(key.SignalTime) == "INVALID")
            {
                signalEntries[key] = (direction, 0, 0, "INVALID");
                DrawChart();
                continue;
            }
            if (!TryGetClosedEntryCandle(key.SignalTime, out var entry)) continue;
            var outcome = OutcomeForDirection(direction, entry);
            if (key.Strategy == ActiveStrategy.Forex
                && forexPlans.TryGetValue((key.Symbol, key.SignalTime), out var forexPlan))
                outcome = OutcomeForForex(forexPlan, entry);
            signalEntries[key] = (direction, entry.Open, entry.Close, outcome);
            AppendJournal(key.Symbol, key.Strategy, key.SignalTime, direction, entry, outcome);
            if (key.Strategy == ActiveStrategy.SmcFusion)
                RecordFusionEntry(key.Symbol, key.SignalTime, direction, entry, outcome);
        }
        UpdateResultPanel();
        if (active == ActiveStrategy.SmcFusion) UpdateFusionStats();
    }

    private bool TryGetClosedEntryCandle(double signalTime, out Candle entry)
    {
        entry = null!;
        var duration = TimeframeMinutes() * 60;
        var entryTime = signalTime + duration;
        var entryIndex = chartCandles.FindIndex(candle => candle.Time == entryTime);
        if (entryIndex < 0) return false;

        // Do not score a live/incomplete candle. This keeps every strategy's
        // result tied to the same next-candle close boundary.
        var candidate = chartCandles[entryIndex];
        if (!IsValidCandle(candidate)) return false;
        if (BrokerNow() < candidate.Time + duration) return false;
        entry = candidate;
        return true;
    }

    private static string OutcomeForDirection(string direction, Candle entry)
    {
        if (entry.Close == entry.Open) return "DRAW";
        var actual = entry.Close > entry.Open ? "CALL" : "PUT";
        return actual == direction ? "WIN" : "LOSS";
    }

    private static string OutcomeForForex(ForexPlan plan, Candle entry)
    {
        var hitTarget = plan.Direction == "CALL" ? entry.High >= plan.Tp1 : entry.Low <= plan.Tp1;
        var hitStop = plan.Direction == "CALL" ? entry.Low <= plan.StopLoss : entry.High >= plan.StopLoss;
        if (hitTarget && hitStop) return "DRAW";
        if (hitTarget) return "WIN";
        if (hitStop) return "LOSS";
        return OutcomeForDirection(plan.Direction, entry);
    }

    private void AppendJournal(string symbol, ActiveStrategy strategy, double signalTime, string direction, Candle entry, string outcome)
    {
        try
        {
            EnsureJournalSchema();
            File.AppendAllText(JournalPath,
                FormattableString.Invariant(
                    $"{StrategyName(strategy)},{signalTime:0},{entry.Time:0},{symbol},{direction},{entry.Open},{entry.Close},{outcome}\n"));
        }
        catch (IOException) { /* best-effort */ }
    }

    private static void EnsureJournalSchema()
    {
        const string header = "strategy,signal_time,entry_time,symbol,direction,open,close,outcome\n";
        if (!File.Exists(JournalPath))
        {
            File.WriteAllText(JournalPath, header);
            return;
        }
        var lines = File.ReadAllLines(JournalPath).Where(line => !string.IsNullOrWhiteSpace(line)).ToList();
        if (lines.Count > 0 && lines[0].StartsWith("strategy,", StringComparison.OrdinalIgnoreCase)
            && lines.Skip(1).All(line => line.Split(',').Length == 8)) return;
        var migrated = new List<string> { header.TrimEnd('\n') };
        foreach (var line in lines.Skip(1))
        {
            var fields = line.Split(',');
            if (fields.Length == 7) migrated.Add("LEGACY," + line);
            else if (fields.Length == 8) migrated.Add(line);
        }
        var temp = JournalPath + ".tmp";
        File.WriteAllLines(temp, migrated);
        File.Copy(temp, JournalPath, true);
        File.Delete(temp);
    }

    private static string StrategyName(ActiveStrategy strategy) => strategy switch
    {
        ActiveStrategy.Forex => "FX",
        ActiveStrategy.Trend => "TREND",
        ActiveStrategy.MomentumReversal => "MOMENTUM REVERSAL",
        ActiveStrategy.BollingerReentry => "BOLLINGER RE-ENTRY",
        ActiveStrategy.SrRejection => "S/R REJECTION",
        ActiveStrategy.SnR5Break => "SNR 5-CANDLE BREAK",
        ActiveStrategy.VolatilityBreakout => "VOLATILITY BREAKOUT",
        ActiveStrategy.DualThrust => "DUAL THRUST",
        ActiveStrategy.ZScore => "Z-SCORE",
        ActiveStrategy.EmaCross => "EMA-CROSS",
        ActiveStrategy.RsiReversal => "RSI-REVERSAL",
        ActiveStrategy.StructureBreak => "STRUCTURE-BREAK",
        ActiveStrategy.Smc => "SMC / ICT",
        ActiveStrategy.RsiExtreme => "RSI EXTREME 20/80",
        ActiveStrategy.LookAhead => "LOOK-AHEAD 100% (DEMO)",
        ActiveStrategy.DonchianRegime => "DONCHIAN STRONG-REGIME",
        ActiveStrategy.BollingerRegime => "BOLLINGER REVERTER (REGIME)",
        ActiveStrategy.TickReversal => "TICK EXHAUSTION REVERSAL (UNPROVEN)",
        ActiveStrategy.SmcFusion => "SMC FUSION 60",
        _ => "OFF",
    };

    private void UpdateResultPanel()
    {
        var active = CurrentActiveStrategy();
        ResultStrategyText.Text = $"STRATEGI: {StrategyName(active)}";
        var entries = active == ActiveStrategy.Off
            ? Enumerable.Empty<(string Direction, double EntryOpen, double EntryClose, string Outcome)>()
            : signalEntries.Where(pair => pair.Key.Symbol.Equals(selectedSymbol, StringComparison.OrdinalIgnoreCase)
                && pair.Key.Strategy == active).Select(pair => pair.Value);
        var win = entries.Count(value => value.Outcome == "WIN");
        var loss = entries.Count(value => value.Outcome == "LOSS");
        var draw = entries.Count(value => value.Outcome == "DRAW");
        var invalid = entries.Count(value => value.Outcome == "INVALID");
        var decided = win + loss;
        var total = win + loss + draw + invalid;
        var pending = active == ActiveStrategy.Off ? 0 : firedStrategies.Count(pair => pair.Key.Symbol.Equals(selectedSymbol, StringComparison.OrdinalIgnoreCase) && pair.Key.Strategy == active) - total;

        ResultTotalText.Text = $"{total + pending}";
        ResultWinText.Text = $"{win}";
        ResultLossText.Text = $"{loss}";
        ResultDrawText.Text = $"{draw}";

        // Statistik selain sesi ini: baca seluruh jurnal. Tanpa ini panel selalu
        // kosong saat aplikasi baru dibuka, walau jurnalnya sudah berisi ratusan entry.
        var journal = LoadJournalStats(active);

        if (decided == 0)
        {
            ResultRateText.Text = "—";
            ResultRateText.Foreground = (Brush)new BrushConverter().ConvertFromString("#9FB3CC")!;
            ResultNoteText.Text = journal.Decided > 0
                ? $"sesi ini kosong · jurnal {journal.Win}/{journal.Decided}"
                : pending > 0 ? $"{pending} entry menunggu hasil" : "Belum ada entry";
            if (journal.Decided > 0) ShowJournalStats(journal);
            return;
        }
        var rate = win * 100.0 / decided;
        ResultRateText.Text = $"{rate:0.0}%";
        ResultRateText.Foreground = (Brush)new BrushConverter().ConvertFromString(
            rate >= 55 ? "#25C89A" : rate >= 45 ? "#E7B65A" : "#F45B69")!;
        var reliability = decided < 30 ? "sampel terlalu kecil" : decided < 100 ? "sampel kecil" : "sampel memadai";
        ResultNoteText.Text = $"{decided} selesai · {reliability}"
            + (pending > 0 ? $" · {pending} menunggu" : "")
            + (invalid > 0 ? $" · {invalid} invalid dikecualikan" : "")
            + (journal.Decided > 0 ? $" · jurnal {journal.Win}/{journal.Decided} ({journal.Win * 100.0 / journal.Decided:0.0}%)" : "");
        if (journal.Decided > 0) ShowJournalStats(journal);
    }

    private sealed record JournalStats(int Win, int Loss, int Draw, int Invalid, int Symbols, long From, long To, string Scope)
    {
        public int Decided => Win + Loss;
    }

    // Cache statistik jurnal. UpdateResultPanel dipanggil setiap tick; tanpa cache,
    // file jurnal (ribuan baris) dibaca 2x per detik dan UI jadi berat.
    // Kunci cache memakai waktu modifikasi + ukuran, jadi tetap segar saat jurnal bertambah.
    private static (DateTime Stamp, long Length, string Requested, JournalStats Stats)? journalStatsCache;

    private JournalStats LoadJournalStats(ActiveStrategy active)
    {
        try
        {
            if (!File.Exists(JournalPath)) return new JournalStats(0, 0, 0, 0, 0, 0, 0, "kosong");
            var info = new FileInfo(JournalPath);
            var wanted = StrategyName(active);

            // Kunci cache memakai strategi yang DIMINTA, bukan Scope hasil. Saat strategi
            // tidak punya baris sendiri, hasilnya ber-Scope "SEMUA"; memakai Scope sebagai
            // kunci membuat cache selalu meleset dan file dibaca ulang tiap tick.
            if (journalStatsCache is { } cached
                && cached.Stamp == info.LastWriteTimeUtc
                && cached.Length == info.Length
                && cached.Requested == wanted)
                return cached.Stats;

            var all = TallyJournal(null);
            // Baris LEGACY tidak menyimpan nama strategi, jadi tidak bisa diatribusikan.
            // Lebih jujur menampilkan angka per-strategi, dan bila kosong, angka seluruh
            // jurnal dengan label jelas daripada mengakuinya sebagai milik strategi ini.
            JournalStats result;
            if (all.Decided == 0)
            {
                result = all;
            }
            else
            {
                var mine = TallyJournal(wanted);
                result = mine.Decided > 0 ? mine with { Scope = wanted } : all with { Scope = "SEMUA" };
            }
            journalStatsCache = (info.LastWriteTimeUtc, info.Length, wanted, result);
            return result;
        }
        catch { return new JournalStats(0, 0, 0, 0, 0, 0, 0, "gagal baca"); }
    }

    private static JournalStats TallyJournal(string? strategy)
    {
        var win = 0; var loss = 0; var draw = 0; var invalid = 0;
        var symbols = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long from = long.MaxValue, to = 0;
        foreach (var line in File.ReadLines(JournalPath).Skip(1))
        {
            var f = line.Split(',');
            if (f.Length < 8) continue;
            if (strategy is not null && !f[0].Trim().Equals(strategy, StringComparison.OrdinalIgnoreCase)) continue;
            symbols.Add(f[3]);
            switch (f[7].Trim().ToUpperInvariant())
            {
                case "WIN": win++; break;
                case "LOSS": loss++; break;
                case "DRAW": draw++; break;
                default: invalid++; break;
            }
            // entry_time = f[2]
            if (long.TryParse(f[2], out var ts))
            {
                from = Math.Min(from, ts);
                to = Math.Max(to, ts);
            }
        }
        return new JournalStats(win, loss, draw, invalid, symbols.Count,
            from == long.MaxValue ? 0 : from, to, strategy ?? "SEMUA");
    }

    private void ShowJournalStats(JournalStats stats)
    {
        var rate = stats.Win * 100.0 / stats.Decided;
        // Panel kanan: tampilkan angka jurnal penuh, bukan hanya sesi.
        JournalRateText.Text = $"{rate:0.0}%";
        JournalRateText.Foreground = (Brush)new BrushConverter().ConvertFromString(
            rate >= 55 ? "#25C89A" : rate >= 45 ? "#E7B65A" : "#F45B69")!;
        var span = stats.From > 0
            ? $"{DateTimeOffset.FromUnixTimeSeconds(stats.From).ToLocalTime():dd MMM} – {DateTimeOffset.FromUnixTimeSeconds(stats.To).ToLocalTime():dd MMM}"
            : "—";
        // Label cakupan penting: pembaca harus tahu angkanya milik strategi mana.
        JournalDetailText.Text = $"{stats.Scope} · {stats.Win}W / {stats.Loss}L" +
            (stats.Draw > 0 ? $" / {stats.Draw}D" : "") +
            $" · {stats.Symbols} simbol · {span}";
    }

    private void ResultReset_Click(object sender, RoutedEventArgs e)
    {
        firedStrategies.Clear();
        forexPlans.Clear();
        lockedSignals.Clear();
        firedEntries.Clear();
        signalEntries.Clear();
        sessionEntryCounts.Clear();
        nextPrepareCounts.Clear();
        nextPrepareLastTime.Clear();
        nextAlertDedup.Clear();
        nextSignals.Clear();
        currentPreviewDirection = "";
        UpdateResultPanel();
        DrawChart();
    }

    private void OpenJournal_Click(object sender, RoutedEventArgs e)
    {
        if (!File.Exists(JournalPath)) { StatusText.Text = "Jurnal sinyal belum ada."; return; }
        try { Process.Start(new ProcessStartInfo(JournalPath) { UseShellExecute = true }); }
        catch (Exception error) { StatusText.Text = error.Message; }
    }

    private void TradingJournalDate_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (!loadingTradingJournal) LoadTradingJournalEntry();
    }

    private void TradingJournalField_Changed(object sender, TextChangedEventArgs e)
    {
        if (loadingTradingJournal) return;
        TradingJournalSaveStatus.Text = "Perubahan belum tersimpan";
        TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#E7B65A")!;
        tradingJournalSaveTimer.Stop();
        tradingJournalSaveTimer.Start();
    }

    private void TradingJournalField_LostFocus(object sender, RoutedEventArgs e)
    {
        if (tradingJournalSaveTimer.IsEnabled) SaveTradingJournalEntry();
    }

    private void LoadTradingJournalEntry()
    {
        if (TradingJournalDate.SelectedDate is not DateTime date) return;
        loadingTradingJournal = true;
        try
        {
            EnsureTradingJournalFile();
            var saved = ReadTradingJournal().FirstOrDefault(entry => entry.Date == date.Date);
            TradingJournalBalance.Text = FormatJournalAmount(saved?.Balance);
            TradingJournalTarget.Text = FormatJournalAmount(saved?.Target);
            TradingJournalStopLoss.Text = FormatJournalAmount(saved?.StopLoss);
            TradingJournalSaveStatus.Text = saved is null ? "Isi nilai; perubahan tersimpan otomatis ke Excel." : "Catatan tanggal ini dimuat dari Excel.";
            TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#9FB3CC")!;
        }
        catch (Exception error)
        {
            TradingJournalSaveStatus.Text = $"Gagal membuka jurnal: {error.Message}";
            TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#F45B69")!;
        }
        finally { loadingTradingJournal = false; }
    }

    private void SaveTradingJournalEntry()
    {
        tradingJournalSaveTimer.Stop();
        if (TradingJournalDate.SelectedDate is not DateTime date) return;
        if (!TryParseJournalAmount(TradingJournalBalance.Text, out var balance) ||
            !TryParseJournalAmount(TradingJournalTarget.Text, out var target) ||
            !TryParseJournalAmount(TradingJournalStopLoss.Text, out var stopLoss))
        {
            TradingJournalSaveStatus.Text = "Nominal tidak valid. Periksa angka yang diisi.";
            TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#F45B69")!;
            return;
        }
        try
        {
            EnsureTradingJournalFile();
            var tempPath = TradingJournalPath + ".tmp";
            File.Copy(TradingJournalPath, tempPath, true);
            using (var archive = ZipFile.Open(tempPath, ZipArchiveMode.Update))
            {
                var sheet = LoadJournalXml(archive, "xl/worksheets/sheet1.xml");
                var ns = sheet.Root!.Name.Namespace;
                var rows = sheet.Root.Element(ns + "sheetData")!.Elements(ns + "row").ToList();
                var row = rows.FirstOrDefault(candidate => JournalRowDate(candidate, ns) == date.Date) ??
                    rows.FirstOrDefault(candidate => (int?)candidate.Attribute("r") >= 5 && (int?)candidate.Attribute("r") <= 504 && JournalRowDate(candidate, ns) is null);
                if (row is null)
                {
                    TradingJournalSaveStatus.Text = "Jurnal penuh (maksimal 500 tanggal).";
                    TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#F45B69")!;
                    File.Delete(tempPath);
                    return;
                }
                var rowNumber = (int)row.Attribute("r")!;
                SetJournalCell(row, ns, $"A{rowNumber}", balance is null && target is null && stopLoss is null ? null : date.ToOADate().ToString("0.########", CultureInfo.InvariantCulture));
                SetJournalCell(row, ns, $"B{rowNumber}", balance?.ToString(CultureInfo.InvariantCulture));
                SetJournalCell(row, ns, $"C{rowNumber}", target?.ToString(CultureInfo.InvariantCulture));
                SetJournalCell(row, ns, $"D{rowNumber}", stopLoss?.ToString(CultureInfo.InvariantCulture));
                var table = LoadJournalXml(archive, "xl/tables/table1.xml");
                table.Root!.SetAttributeValue("ref", "A4:D504");
                SaveJournalXml(archive, "xl/worksheets/sheet1.xml", sheet);
                SaveJournalXml(archive, "xl/tables/table1.xml", table);
            }
            File.Move(tempPath, TradingJournalPath, true);
            TradingJournalSaveStatus.Text = $"Tersimpan di Excel · {DateTime.Now:HH:mm:ss}";
            TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#25C89A")!;
        }
        catch (Exception error)
        {
            var tempPath = TradingJournalPath + ".tmp";
            if (File.Exists(tempPath)) try { File.Delete(tempPath); } catch { }
            TradingJournalSaveStatus.Text = $"Gagal menyimpan: {error.Message}";
            TradingJournalSaveStatus.Foreground = (Brush)new BrushConverter().ConvertFromString("#F45B69")!;
        }
    }

    private void OpenTradingJournal_Click(object sender, RoutedEventArgs e)
    {
        try { EnsureTradingJournalFile(); Process.Start(new ProcessStartInfo(TradingJournalPath) { UseShellExecute = true }); }
        catch (Exception error) { TradingJournalSaveStatus.Text = $"Gagal membuka Excel: {error.Message}"; }
    }

    private static void EnsureTradingJournalFile()
    {
        if (File.Exists(TradingJournalPath)) return;
        Directory.CreateDirectory(TradingJournalDirectory);
        var template = System.IO.Path.Combine(AppContext.BaseDirectory, "assets", "trading-journal-template.xlsx");
        if (!File.Exists(template)) throw new FileNotFoundException("Template Excel jurnal tidak ditemukan.", template);
        File.Copy(template, TradingJournalPath);
    }

    private static List<TradingJournalRow> ReadTradingJournal()
    {
        EnsureTradingJournalFile();
        using var archive = ZipFile.OpenRead(TradingJournalPath);
        var sheet = LoadJournalXml(archive, "xl/worksheets/sheet1.xml");
        var ns = sheet.Root!.Name.Namespace;
        return sheet.Root.Element(ns + "sheetData")!.Elements(ns + "row")
            .Select(row => new TradingJournalRow((int?)row.Attribute("r") ?? 0, JournalRowDate(row, ns), JournalCellAmount(row, ns, "B"), JournalCellAmount(row, ns, "C"), JournalCellAmount(row, ns, "D")))
            .Where(row => row.Date is not null).ToList();
    }

    private static DateTime? JournalRowDate(XElement row, XNamespace ns)
    {
        var value = row.Elements(ns + "c").FirstOrDefault(cell => ((string?)cell.Attribute("r"))?.StartsWith("A", StringComparison.Ordinal) == true)?.Element(ns + "v")?.Value;
        return double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var serial) ? DateTime.FromOADate(serial).Date : null;
    }

    private static decimal? JournalCellAmount(XElement row, XNamespace ns, string column)
    {
        var value = row.Elements(ns + "c").FirstOrDefault(cell => ((string?)cell.Attribute("r"))?.StartsWith(column, StringComparison.Ordinal) == true)?.Element(ns + "v")?.Value;
        return decimal.TryParse(value, NumberStyles.Number, CultureInfo.InvariantCulture, out var amount) ? amount : null;
    }

    private static string FormatJournalAmount(decimal? amount) => amount?.ToString("0.##", CultureInfo.CurrentCulture) ?? "";

    private static bool TryParseJournalAmount(string text, out decimal? amount)
    {
        amount = null;
        if (string.IsNullOrWhiteSpace(text)) return true;
        if (decimal.TryParse(text, NumberStyles.Number, CultureInfo.CurrentCulture, out var parsed) || decimal.TryParse(text, NumberStyles.Number, CultureInfo.InvariantCulture, out parsed))
        { amount = parsed; return true; }
        return false;
    }

    private static XDocument LoadJournalXml(ZipArchive archive, string path)
    {
        var entry = archive.GetEntry(path) ?? throw new InvalidDataException($"Bagian Excel tidak ditemukan: {path}");
        using var stream = entry.Open();
        return XDocument.Load(stream);
    }

    private static void SaveJournalXml(ZipArchive archive, string path, XDocument document)
    {
        archive.GetEntry(path)?.Delete();
        var entry = archive.CreateEntry(path, CompressionLevel.Optimal);
        using var stream = entry.Open();
        document.Save(stream);
    }

    private static void SetJournalCell(XElement row, XNamespace ns, string address, string? value)
    {
        var cell = row.Elements(ns + "c").FirstOrDefault(candidate => (string?)candidate.Attribute("r") == address);
        if (cell is null) { cell = new XElement(ns + "c", new XAttribute("r", address)); row.Add(cell); }
        cell.Attribute("t")?.Remove();
        var existing = cell.Element(ns + "v");
        if (value is null) existing?.Remove();
        else if (existing is null) cell.Add(new XElement(ns + "v", value));
        else existing.Value = value;
    }

    private sealed record TradingJournalRow(int Row, DateTime? Date, decimal? Balance, decimal? Target, decimal? StopLoss);

    private void PushChartAlert(string kind, string direction, double candleTime, string extra = "", bool force = false)
    {
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
    private sealed record NextSignalRow(string Symbol, string Direction, string Status, string Detail, string ForegroundHex);
    private sealed record ChartAlertRow(string Time, string Message);
}

internal static class JsonElementExtensions
{
    internal static int GetArrayLengthSafe(this JsonElement element, string name) => element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Array ? value.GetArrayLength() : 0;
}
