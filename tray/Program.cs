using System.Security.Principal;
using EasyUniVPN;

// ── single-instance guard ────────────────────────────────────────────────────

using var mutex = new Mutex(initiallyOwned: true, "EasyUniVPN-Tray-SingleInstance", out bool isFirst);
if (!isFirst)
{
    // Another instance is already running - exit silently.
    return;
}

// ── admin guard ──────────────────────────────────────────────────────────────
// Normally the Rust launcher handles elevation before spawning us.  This is a
// safety net for the case where someone starts EasyUniVPN.exe directly.

if (!IsAdmin())
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

// ── logging ──────────────────────────────────────────────────────────────────

TrayLog.Init();
TrayLog.Info($"Starting (PID {System.Diagnostics.Process.GetCurrentProcess().Id}, elevated)");

// ── WinForms bootstrap ───────────────────────────────────────────────────────

Application.EnableVisualStyles();
Application.SetCompatibleTextRenderingDefault(false);

bool verbose = args.Any(a => a is "--verbose" or "-v");

using var app = new TrayApp(verbose);
Application.Run();   // runs until Application.Exit() is called from TrayApp.OnQuit()
TrayLog.Info("Tray exited cleanly");

// ── helpers ──────────────────────────────────────────────────────────────────

static bool IsAdmin()
{
    using var id = WindowsIdentity.GetCurrent();
    return new WindowsPrincipal(id).IsInRole(WindowsBuiltInRole.Administrator);
}
