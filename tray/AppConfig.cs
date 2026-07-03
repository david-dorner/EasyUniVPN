using Microsoft.Win32;
using System.Runtime.InteropServices;
using System.Text;

namespace EasyUniVPN;

/// <summary>
/// Mirrors <c>common/paths.py</c> and <c>common/app_config.py</c>.
///
/// All path calculations are centralised here so the rest of the tray code
/// never builds a path by hand, exactly as in the Python version.
/// </summary>
internal static class AppPaths
{
    // ── constants ────────────────────────────────────────────────────────
    internal const string AppName        = "EasyUniVPN";
    internal const string VpnServer      = "univpn.uni-graz.at";
    internal const string ProfileName    = "UniVPN";
    internal const string OcConfigEnv    = "OPENCONNECT_SAML_CONFIG";   // mirrors OPENCONNECT_CONFIG_ENV

    // ── install directory ────────────────────────────────────────────────

    /// <summary>
    /// The folder containing this exe - equivalent to <c>app_root()</c> when frozen.
    /// </summary>
    internal static string InstallDir { get; } =
        Path.GetDirectoryName(
            System.Diagnostics.Process.GetCurrentProcess().MainModule?.FileName
            ?? AppDomain.CurrentDomain.BaseDirectory)
        ?? AppDomain.CurrentDomain.BaseDirectory;

    // ── runtime paths (mirrors paths.py) ────────────────────────────────

    internal static string RuntimeRoot     => Path.Combine(InstallDir, "runtime");
    internal static string PythonDir       => Path.Combine(RuntimeRoot, "python");
    internal static string PythonExe       => Path.Combine(PythonDir, "python.exe");
    internal static string ScriptsDir      => Path.Combine(PythonDir, "Scripts");
    internal static string OcSamlExe       => Path.Combine(ScriptsDir, "openconnect-saml.exe");
    internal static string OpenConnectDir  => Path.Combine(RuntimeRoot, "openconnect");

    internal static string LauncherExe     => Path.Combine(InstallDir, "EasyUniVPNLauncher.exe");
    internal static string ConsoleExe      => Path.Combine(InstallDir, "EasyUniVPNCli.exe");
    internal static string AssetsDir       => Path.Combine(InstallDir, "assets");

    // ── app-data paths (mirrors paths.py) ───────────────────────────────

    internal static string AppDataDir =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            AppName);

    internal static string ConfigPath        => Path.Combine(AppDataDir, "config.json");
    internal static string SessionStatePath  => Path.Combine(AppDataDir, "session_state.json");
    internal static string OcConfigPath      => Path.Combine(AppDataDir, "openconnect-saml", "config.toml");
    internal static string LogDir            => Path.Combine(AppDataDir, "logs");

    // ── icon paths ───────────────────────────────────────────────────────

    internal static string? TrayIconPath(bool connected)
    {
        bool dark = IsDarkMode();
        string state = connected ? "on" : "off";
        string tone  = dark ? "white" : "black";
        string path  = Path.Combine(AssetsDir, $"vpn-{state}-{tone}.png");
        return File.Exists(path) ? path : null;
    }

    internal static bool IsDarkMode()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            return key?.GetValue("AppsUseLightTheme") is int v && v == 0;
        }
        catch { return false; }
    }

    // ── TOTP secret retrieval ─────────────────────────────────────────────

    /// <summary>
    /// Reads the TOTP secret from Windows Credential Manager.
    /// Tries the dedicated <c>EasyUniVPN/totp_secret</c> entry written by
    /// <c>save_profile()</c>, then falls back to the openconnect-saml entry.
    /// Returns <c>null</c> if setup has not been completed yet.
    /// </summary>
    internal static string? ReadTotpSecret()
    {
        // Modern keyring encodes TargetName as "service/username"
        string? secret = ReadCred("EasyUniVPN/totp_secret");
        if (secret != null) { TrayLog.Info("[CRED] found via EasyUniVPN/totp_secret"); return secret; }

        // Older keyring versions store TargetName = service only; UserName = username.
        // The Windows Credential Manager shows this as address="EasyUniVPN", user="totp_secret".
        secret = ReadCred("EasyUniVPN");
        if (secret != null) { TrayLog.Info("[CRED] found via EasyUniVPN (service-only TargetName)"); return secret; }

        // Last fallback: openconnect-saml's own entry
        string email = AppConfig.Load().Email;
        if (!string.IsNullOrEmpty(email))
        {
            secret = ReadCred($"openconnect-saml/totp/{email}");
            if (secret != null) { TrayLog.Info($"[CRED] found via openconnect-saml/totp/{email}"); return secret; }
        }

        TrayLog.Warn("[CRED] no TOTP secret found in CredMan (tried EasyUniVPN/totp_secret, EasyUniVPN, openconnect-saml/totp/*)");
        return null;
    }

    private static string? ReadCred(string target)
    {
        if (!NativeMethods.CredRead(target, NativeMethods.CRED_TYPE_GENERIC, 0, out IntPtr ptr))
            return null;
        try
        {
            var cred = Marshal.PtrToStructure<NativeMethods.CREDENTIAL>(ptr);
            if (cred.CredentialBlobSize == 0 || cred.CredentialBlob == IntPtr.Zero)
                return null;
            var bytes = new byte[cred.CredentialBlobSize];
            Marshal.Copy(cred.CredentialBlob, bytes, 0, bytes.Length);
            return Encoding.Unicode.GetString(bytes);
        }
        finally { NativeMethods.CredFree(ptr); }
    }

    // ── environment variables for openconnect-saml subprocess ────────────

    /// <summary>
    /// Returns the environment dictionary to pass to the openconnect-saml
    /// subprocess. Mirrors <c>common/openconnect_config.py::configure_openconnect_env()</c>.
    /// </summary>
    internal static Dictionary<string, string> OpenConnectEnv()
    {
        var env = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        // Copy current process env
        foreach (System.Collections.DictionaryEntry kv in Environment.GetEnvironmentVariables())
            env[(string)kv.Key] = (string?)kv.Value ?? "";

        // OPENCONNECT_SAML_CONFIG - tells openconnect-saml where its config.toml lives
        Directory.CreateDirectory(Path.GetDirectoryName(OcConfigPath)!);
        env[OcConfigEnv] = OcConfigPath;

        // Prepend bundled openconnect binary dir to PATH (openconnect.exe + DLLs)
        if (Directory.Exists(OpenConnectDir))
        {
            string current = env.TryGetValue("PATH", out var p) ? p : "";
            if (!current.Split(Path.PathSeparator).Contains(OpenConnectDir,
                    StringComparer.OrdinalIgnoreCase))
                env["PATH"] = OpenConnectDir + Path.PathSeparator + current;
        }

        return env;
    }
}

/// <summary>
/// Minimal file logger for the tray process. Writes to
/// <c>%APPDATA%\EasyUniVPN\logs\tray.log</c>, truncated at each startup.
/// </summary>
internal static class TrayLog
{
    private static readonly object _lock = new();
    private static string LogPath => Path.Combine(AppPaths.LogDir, "tray.log");

    internal static void Init()
    {
        try
        {
            Directory.CreateDirectory(AppPaths.LogDir);
            File.WriteAllText(LogPath,
                $"=== EasyUniVPN tray session {DateTime.Now:yyyy-MM-dd HH:mm:ss} ==={Environment.NewLine}");
        }
        catch { }
    }

    internal static void Write(string level, string msg)
    {
        try
        {
            var line = $"{DateTime.Now:HH:mm:ss.fff} [{level}] {msg}{Environment.NewLine}";
            lock (_lock) File.AppendAllText(LogPath, line);
        }
        catch { }
    }

    internal static void Info(string msg)  => Write("INFO ", msg);
    internal static void Warn(string msg)  => Write("WARN ", msg);
    internal static void Error(string msg) => Write("ERROR", msg);
}

/// <summary>
/// Tray-side preferences that the Python bundle doesn't own, persisted in the
/// registry so they survive config.json rewrites by the Python CLI.
/// </summary>
internal static class TraySettings
{
    private const string RegKey = @"Software\EasyUniVPN";

    internal static bool NotificationsEnabled
    {
        get
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RegKey);
                return key?.GetValue("NotificationsEnabled") is not 0;
            }
            catch { return true; }
        }
        set
        {
            try
            {
                using var key = Registry.CurrentUser.CreateSubKey(RegKey);
                key?.SetValue("NotificationsEnabled", value ? 1 : 0, RegistryValueKind.DWord);
            }
            catch { }
        }
    }
}

/// <summary>
/// Parsed representation of <c>%APPDATA%\EasyUniVPN\config.json</c>.
/// Mirrors <c>common/app_config.py::AppConfig</c>.
/// </summary>
internal record AppConfig(string Email, bool SetupComplete, bool StartWithWindows)
{
    internal static AppConfig Load()
    {
        try
        {
            string json = File.ReadAllText(AppPaths.ConfigPath);
            return new AppConfig(
                Email:             ExtractString(json, "email"),
                SetupComplete:     ContainsBool(json, "setup_complete", true),
                StartWithWindows:  ContainsBool(json, "start_with_windows", true));
        }
        catch
        {
            return new AppConfig("", false, false);
        }
    }

    // Minimal JSON field extraction - avoids a System.Text.Json dependency for trimming.
    private static string ExtractString(string json, string key)
    {
        var marker = $"\"{key}\"";
        int i = json.IndexOf(marker, StringComparison.Ordinal);
        if (i < 0) return "";
        i = json.IndexOf('"', i + marker.Length + 1); // skip : and optional space
        if (i < 0) return "";
        int end = json.IndexOf('"', i + 1);
        return end < 0 ? "" : json.Substring(i + 1, end - i - 1);
    }

    private static bool ContainsBool(string json, string key, bool value)
    {
        string target = value ? "true" : "false";
        // string.Contains(string, StringComparison) is .NET Core 2.1+ only.
        // On net48, use IndexOf which has always supported StringComparison.
        return json.IndexOf($"\"{key}\":{target}", StringComparison.Ordinal) >= 0
            || json.IndexOf($"\"{key}\": {target}", StringComparison.Ordinal) >= 0;
    }
}
