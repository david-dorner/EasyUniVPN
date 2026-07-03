using System.ComponentModel;
using System.Diagnostics;

namespace EasyUniVPN;

/// <summary>
/// Owns a single openconnect-saml subprocess for one connect/disconnect cycle.
/// Mirrors <c>common/vpn.py::VpnController</c> and the disconnect logic.
/// </summary>
internal sealed class VpnController : IDisposable
{
    private Process? _process;
    private readonly bool _verbose;

    internal VpnController(bool verbose) => _verbose = verbose;

    internal Process? Process => _process;

    // ── connect ──────────────────────────────────────────────────────────

    internal void Connect()
    {
        if (_process is { HasExited: false })
            return;

        bool useScript = File.Exists(AppPaths.OcSamlExe);
        ProcessStartInfo psi = useScript
            ? new ProcessStartInfo(AppPaths.OcSamlExe, $"connect {AppPaths.ProfileName} --reconnect")
            : new ProcessStartInfo(AppPaths.PythonExe,
                $"-m openconnect_saml.cli connect {AppPaths.ProfileName} --reconnect");

        TrayLog.Info($"Connecting via {psi.FileName}");

        psi.CreateNoWindow        = true;
        psi.UseShellExecute       = false;
        psi.RedirectStandardInput = true;   // closed immediately = DEVNULL
        psi.RedirectStandardOutput = true;  // drained async - prevents 64 KB pipe-buffer fill
        psi.RedirectStandardError  = true;  // drained async + logged for diagnostics

        var env = AppPaths.OpenConnectEnv();
        foreach (var kv in env)
            psi.EnvironmentVariables[kv.Key] = kv.Value;

        _process = Process.Start(psi)
            ?? throw new InvalidOperationException("Failed to start openconnect-saml.");

        TrayLog.Info($"openconnect-saml started (PID {_process.Id})");
        try { _process.StandardInput.Close(); } catch { }

        // Drain stdout silently (mirrors subprocess.DEVNULL) and drain stderr
        // with logging so connection failures show up in tray.log without needing
        // a separate debug build.
        var proc = _process;
        _ = Task.Run(() => { try { proc.StandardOutput.ReadToEnd(); } catch { } });
        _ = Task.Run(() =>
        {
            try
            {
                string? line;
                while ((line = proc.StandardError.ReadLine()) != null)
                    TrayLog.Info($"oc: {line.TrimEnd()}");
            }
            catch { }
        });
    }

    // ── disconnect ───────────────────────────────────────────────────────

    /// <summary>
    /// Kill the entire process tree rooted at the openconnect-saml subprocess.
    /// Mirrors <c>vpn.py::VpnController.disconnect()</c>.
    ///
    /// Uses <see cref="Process.Kill(bool)"/> with <c>entireProcessTree=true</c>
    /// (.NET 5+ API) which is equivalent to <c>taskkill /F /T /PID &lt;pid&gt;</c>.
    /// This is critical: openconnect-saml (Python) spawns openconnect.exe as a
    /// child; killing only the parent orphans the child and leaves the VPN tunnel
    /// running.
    /// </summary>
    internal void Disconnect()
    {
        if (_process is null || _process.HasExited)
            return;

        TrayLog.Info($"Killing openconnect-saml (PID {_process.Id})");
        try
        {
            KillTree(_process.Id);
            _process.WaitForExit(5_000);
        }
        catch (Exception ex) when (ex is InvalidOperationException or Win32Exception)
        {
            TrayLog.Warn($"Disconnect cleanup: {ex.Message}");
        }
    }

    /// <summary>
    /// Stop a VPN session this controller does not own - e.g. one started by
    /// a previous tray instance that is still up after the tray restarted.
    /// Mirrors <c>vpn.py::disconnect_active_session()</c>: ask openconnect-saml
    /// to disconnect the profile, then kill any orphaned openconnect.exe that
    /// survived its parent.
    /// </summary>
    internal static void DisconnectExternalSession()
    {
        TrayLog.Info("Disconnecting an externally started VPN session");
        try
        {
            bool useScript = File.Exists(AppPaths.OcSamlExe);
            ProcessStartInfo psi = useScript
                ? new ProcessStartInfo(AppPaths.OcSamlExe, $"disconnect {AppPaths.ProfileName}")
                : new ProcessStartInfo(AppPaths.PythonExe,
                    $"-m openconnect_saml.cli disconnect {AppPaths.ProfileName}");
            psi.CreateNoWindow         = true;
            psi.UseShellExecute        = false;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError  = true;
            foreach (var kv in AppPaths.OpenConnectEnv())
                psi.EnvironmentVariables[kv.Key] = kv.Value;

            using var proc = Process.Start(psi);
            // Bounded wait: OnQuit budgets 15 s for the whole disconnect, and
            // the orphan cleanup below must still get its turn.
            proc?.WaitForExit(10_000);
        }
        catch (Exception ex) { TrayLog.Warn($"External disconnect: {ex.Message}"); }

        // Kill any orphaned openconnect.exe that outlived the wrapper - it is
        // what actually holds the tunnel open.
        try
        {
            using var killer = Process.Start(new ProcessStartInfo("taskkill",
                "/F /T /IM openconnect.exe")
            {
                CreateNoWindow    = true,
                UseShellExecute   = false,
                RedirectStandardOutput = true,
                RedirectStandardError  = true,
            });
            killer?.WaitForExit(5_000);
        }
        catch (Exception ex) { TrayLog.Warn($"openconnect cleanup: {ex.Message}"); }

        RecordSessionEnded();
    }

    internal static void KillTree(int pid)
    {
        try
        {
            using var killer = Process.Start(new ProcessStartInfo("taskkill",
                $"/F /T /PID {pid}")
            {
                CreateNoWindow    = true,
                UseShellExecute   = false,
                RedirectStandardOutput = true,
                RedirectStandardError  = true,
            });
            killer?.WaitForExit(5_000);
        }
        catch (Exception ex) { TrayLog.Error($"KillTree({pid}): {ex.Message}"); }
    }

    // ── VPN state ────────────────────────────────────────────────────────

    /// <summary>
    /// Returns true if the VPN interface for <see cref="AppPaths.VpnServer"/>
    /// is currently up. Mirrors <c>vpn.py::is_connected()</c>.
    /// </summary>
    internal static bool IsConnected()
    {
        try
        {
            using var proc = Process.Start(new ProcessStartInfo("netsh",
                "interface show interface")
            {
                RedirectStandardOutput = true,
                UseShellExecute        = false,
                CreateNoWindow         = true,
            })!;
            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit(10_000);
            return output.IndexOf(AppPaths.VpnServer, StringComparison.OrdinalIgnoreCase) >= 0;
        }
        catch { return false; }
    }

    // ── session state ─────────────────────────────────────────────────────

    /// <summary>
    /// Write the session-started timestamp so the Python <c>status</c> command
    /// can report connection duration. Mirrors <c>vpn.py::record_session_started()</c>.
    /// </summary>
    internal static void RecordSessionStarted()
    {
        try
        {
            Directory.CreateDirectory(AppPaths.AppDataDir);
            double ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 1000.0;
            File.WriteAllText(AppPaths.SessionStatePath,
                $"{{\"connected_since\":{ts}}}");
        }
        catch { }
    }

    /// <summary>Mirrors <c>vpn.py::record_session_ended()</c>.</summary>
    internal static void RecordSessionEnded()
    {
        try { File.Delete(AppPaths.SessionStatePath); }
        catch { }
    }

    public void Dispose()
    {
        try { _process?.Dispose(); } catch { }
    }
}
