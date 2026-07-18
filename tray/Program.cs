using System.Security.Principal;

namespace EasyUniVPN;

internal static class Program
{
    /// <summary>
    /// <c>[STAThread]</c> is load-bearing: the UI thread must be a
    /// single-threaded apartment for OLE clipboard calls - Copy OTP from the
    /// tray menu runs directly on this thread. Top-level statements cannot
    /// carry the attribute, hence the classic Main.
    /// </summary>
    [STAThread]
    private static void Main(string[] args)
    {
        // ── DPI awareness ────────────────────────────────────────────────
        // Must be the very first thing, before any window or icon exists, so
        // the tray icon renders at real pixel size instead of being
        // bitmap-stretched.
        NativeMethods.EnablePerMonitorDpiAwareness();

        bool restartViaLauncher = false;

        // ── single-instance guard ────────────────────────────────────────
        using (var mutex = new Mutex(initiallyOwned: true, "EasyUniVPN-Tray-SingleInstance", out bool isFirst))
        {
            if (!isFirst)
            {
                // Another instance is already running - exit silently.
                return;
            }

            // ── admin guard ──────────────────────────────────────────────
            // Only the VPN needs admin rights (creating the network adapter);
            // one-time-codes-only setups run unelevated. Normally the Rust
            // launcher handles elevation before spawning us - this is a
            // safety net for the case where someone starts EasyUniVPN.exe
            // directly.
            bool needsAdmin = AppConfig.Load().VpnEnabled;
            if (needsAdmin && !IsAdmin())
            {
                try
                {
                    // Environment.ProcessPath is .NET 6+ - use MainModule on net48.
                    var exe = System.Diagnostics.Process.GetCurrentProcess().MainModule!.FileName;
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(exe)
                    {
                        Verb            = "runas",
                        UseShellExecute = true,
                    });
                }
                catch { /* user cancelled UAC */ }
                return;
            }

            // ── logging ──────────────────────────────────────────────────
            TrayLog.Init();
            TrayLog.Info($"Starting (PID {System.Diagnostics.Process.GetCurrentProcess().Id}, elevated={IsAdmin()})");

            // ── WinForms bootstrap ───────────────────────────────────────
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool verbose = args.Any(a => a is "--verbose" or "-v");

            using var app = new TrayApp(verbose);
            Application.Run();   // runs until Application.Exit() is called from TrayApp
            restartViaLauncher = app.RestartRequested;
            TrayLog.Info("Tray exited cleanly");
        }

        // A live config reload enabled the VPN while this process was
        // unelevated - hand off to the launcher, which elevates (UAC) and
        // starts a fresh tray. Runs after the using block so the
        // single-instance mutex is already released for the new instance.
        if (restartViaLauncher)
        {
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(AppPaths.LauncherExe)
                {
                    UseShellExecute = true,
                });
            }
            catch { /* user cancelled UAC - relaunch manually */ }
        }
    }

    internal static bool IsAdmin()
    {
        using var id = WindowsIdentity.GetCurrent();
        return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
    }
}
