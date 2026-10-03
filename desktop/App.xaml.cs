using System;
using System.IO;
using System.Windows;
using Forms = System.Windows.Forms;

namespace OpenTerminal.Desktop;

public partial class App : System.Windows.Application
{
    private const string AppName = "OPEN TERMINAL BMF";
    private Forms.NotifyIcon? trayIcon;
    private bool exiting;

    public App()
    {
        DispatcherUnhandledException += (_, eventArgs) =>
        {
            File.AppendAllText(Path.Combine(AppContext.BaseDirectory, "OpenTerminal-crash.log"), $"{DateTimeOffset.Now:O} {eventArgs.Exception}\n");
            MessageBox.Show(eventArgs.Exception.Message, $"{AppName} startup error", MessageBoxButton.OK, MessageBoxImage.Error);
            eventArgs.Handled = true;
        };
    }

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
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
        window.Show();
    }

    private static void Restore(Window window)
    {
        window.Show();
        window.WindowState = WindowState.Normal;
        window.Activate();
    }

    private void ExitApplication(Window window)
    {
        exiting = true;
        trayIcon?.Dispose();
        window.Close();
        Shutdown();
    }
}
