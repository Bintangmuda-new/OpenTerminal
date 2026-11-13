using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows;
using Forms = System.Windows.Forms;

namespace OpenTerminal.Desktop;

public partial class App : System.Windows.Application
{
    private const string AppName = "OPEN TERMINAL BMF";
    private Mutex? instanceMutex;
    private Forms.NotifyIcon? trayIcon;
    private bool exiting;

    public App()
    {
        // Driver Intel HD memicu UCEERR_RENDERTHREADFAILURE (crash fatal) pada render GPU; software render menghindarinya.
        System.Windows.Media.RenderOptions.ProcessRenderMode = System.Windows.Interop.RenderMode.SoftwareOnly;
        DispatcherUnhandledException += (_, eventArgs) =>
        {
            Log(eventArgs.Exception);
            eventArgs.Handled = true;
            // Error render/automation bersifat transien; jangan spam dialog untuknya.
            if (eventArgs.Exception is System.Runtime.InteropServices.COMException or System.Collections.Generic.KeyNotFoundException) return;
            try { MessageBox.Show(eventArgs.Exception.Message, $"{AppName} error", MessageBoxButton.OK, MessageBoxImage.Error); } catch { }
        };
        AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) => Log(eventArgs.ExceptionObject);
        System.Threading.Tasks.TaskScheduler.UnobservedTaskException += (_, eventArgs) => { Log(eventArgs.Exception); eventArgs.SetObserved(); };
    }

    private static void Log(object error)
    {
        try { File.AppendAllText(Path.Combine(AppContext.BaseDirectory, "OpenTerminal-crash.log"), $"{DateTimeOffset.Now:O} {error}\n"); } catch { }
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        instanceMutex = new Mutex(true, @"Local\OpenTerminalBmf", out var isFirstInstance);
        if (!isFirstInstance)
        {
            instanceMutex.Dispose();
            instanceMutex = null;
            Shutdown();
            return;
        }

        var startHidden = e.Args.Any(arg => string.Equals(arg, "--tray", StringComparison.OrdinalIgnoreCase));

        var window = new MainWindow();
        window.Closing += (_, eventArgs) =>
        {
            if (exiting) return;
            eventArgs.Cancel = true;
            window.Hide();
            trayIcon?.ShowBalloonTip(1500, AppName, "Masih berjalan di system tray.", Forms.ToolTipIcon.Info);
        };
        trayIcon = new Forms.NotifyIcon { Icon = System.Drawing.Icon.ExtractAssociatedIcon(Environment.ProcessPath ?? string.Empty) ?? System.Drawing.SystemIcons.Application, Text = AppName, Visible = true, ContextMenuStrip = new Forms.ContextMenuStrip() };
        trayIcon.ContextMenuStrip.Items.Add($"Buka {AppName}", null, (_, _) => Restore(window));
        trayIcon.ContextMenuStrip.Items.Add("Keluar", null, (_, _) => ExitApplication(window));
        trayIcon.DoubleClick += (_, _) => Restore(window);
        if (startHidden)
        {
            // Mode startup: jendela wajib di-Show sekali agar Loaded/StartAsync jalan, lalu langsung disembunyikan ke tray.
            window.ShowInTaskbar = false;
            window.WindowState = WindowState.Minimized;
            window.Loaded += (_, _) => window.Hide();
        }
        window.Show();
    }

    private static void Restore(Window window)
    {
        window.ShowInTaskbar = true;
        window.Show();
        window.WindowState = WindowState.Normal;
        window.Activate();
    }

    private void ExitApplication(MainWindow window)
    {
        exiting = true;
        window.StopManagedApi();
        trayIcon?.Dispose();
        window.Close();
        Shutdown();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        instanceMutex?.ReleaseMutex();
        instanceMutex?.Dispose();
        instanceMutex = null;
        base.OnExit(e);
    }
}
