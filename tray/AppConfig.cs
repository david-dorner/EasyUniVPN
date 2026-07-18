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

    // ── app-data paths (mirrors paths.py) ───────────────────────────────

    internal static string AppDataDir =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            AppName);

    internal static string ConfigPath        => Path.Combine(AppDataDir, "config.json");
    internal static string SessionStatePath  => Path.Combine(AppDataDir, "session_state.json");
    internal static string OcConfigPath      => Path.Combine(AppDataDir, "openconnect-saml", "config.toml");
    internal static string LogDir            => Path.Combine(AppDataDir, "logs");

    // ── theme ────────────────────────────────────────────────────────────

    /// <summary>
    /// Whether the taskbar (where the tray icon lives) uses the dark theme.
    /// The taskbar follows <c>SystemUsesLightTheme</c> - not
    /// <c>AppsUseLightTheme</c>, which governs application windows and can
    /// differ (the Windows default is dark taskbar + light apps).
    /// </summary>
    internal static bool IsTaskbarDarkTheme()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            object? value = key?.GetValue("SystemUsesLightTheme")
                         ?? key?.GetValue("AppsUseLightTheme");
            return value is not int v || v == 0;
        }
        catch { return true; }
    }

    // ── TOTP secret retrieval ─────────────────────────────────────────────

    /// <summary>
    /// Reads the University of Graz TOTP secret from Windows Credential
    /// Manager (written by setup as service "EasyUniVPN", account
    /// "totp_secret"), falling back to the openconnect-saml entry the VPN
    /// profile writes. Returns <c>null</c> if setup has not been completed.
    /// </summary>
    internal static string? ReadTotpSecret()
    {
        string? secret = ReadKeyringSecret("EasyUniVPN", "totp_secret");
        if (secret != null) { TrayLog.Info("[CRED] KFU secret found (EasyUniVPN / totp_secret)"); return secret; }

        // Fallback: openconnect-saml's own entry (written for the VPN profile).
        string email = AppConfig.Load().Email;
        if (!string.IsNullOrEmpty(email))
        {
            secret = ReadKeyringSecret("openconnect-saml", $"totp/{email}");
            if (secret != null) { TrayLog.Info("[CRED] KFU secret found via openconnect-saml entry"); return secret; }
        }

        TrayLog.Warn("[CRED] no University of Graz TOTP secret found in CredMan");
        return null;
    }

    /// <summary>
    /// Reads the TU Graz TOTP secret (written by setup as service
    /// "EasyUniVPN", account "totp_secret_tugraz"). Returns <c>null</c> when
    /// TU Graz has not been set up.
    /// </summary>
    internal static string? ReadTuTotpSecret()
    {
        string? secret = ReadKeyringSecret("EasyUniVPN", "totp_secret_tugraz");
        if (secret == null)
            TrayLog.Warn("[CRED] no TU Graz TOTP secret found in CredMan (EasyUniVPN / totp_secret_tugraz)");
        return secret;
    }

    /// <summary>
    /// Reads a secret stored by Python's keyring (WinVault backend). keyring
    /// stores the FIRST account of a service under TargetName = service with
    /// UserName = account; every FURTHER account of the same service goes to
    /// TargetName = "account@service". Which layout a given secret lands in
    /// therefore depends on setup order, so both are tried - plus the
    /// "service/account" form for manually created entries. The service-only
    /// read verifies the UserName so one university's secret can never be
    /// mistaken for the other's.
    /// </summary>
    private static string? ReadKeyringSecret(string service, string account)
    {
        return ReadCred(service, requiredUsername: account)
            ?? ReadCred($"{account}@{service}")
            ?? ReadCred($"{service}/{account}");
    }

    private static string? ReadCred(string target, string? requiredUsername = null)
    {
        if (!NativeMethods.CredRead(target, NativeMethods.CRED_TYPE_GENERIC, 0, out IntPtr ptr))
            return null;
        try
        {
            var cred = Marshal.PtrToStructure<NativeMethods.CREDENTIAL>(ptr);
            if (requiredUsername != null)
            {
                string? user = cred.UserName == IntPtr.Zero
                    ? null
                    : Marshal.PtrToStringUni(cred.UserName);
                if (!string.Equals(user, requiredUsername, StringComparison.Ordinal))
                    return null;
            }
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
/// Mirrors <c>common/app_config.py::AppConfig</c> (config version 2).
///
/// KfuMode is "vpn", "totp", or "none"; TU Graz is always codes-only.
/// Hotkeys are canonical specs like "ctrl+alt+v", "" = disabled. The TOTP
/// parameters default to what each university issues (KFU: SHA-1/30 s,
/// TU: SHA-256/60 s).
/// </summary>
internal record AppConfig(
    string Email, bool SetupComplete, bool StartWithWindows,
    string KfuMode, bool TuEnabled,
    string KfuHotkey, string TuHotkey,
    string KfuTotpAlgorithm, int KfuTotpPeriod, int KfuTotpDigits,
    string TuTotpAlgorithm, int TuTotpPeriod, int TuTotpDigits)
{
    internal bool KfuConfigured => KfuMode is "vpn" or "totp";
    internal bool VpnEnabled    => KfuMode == "vpn";

    internal static AppConfig Load()
    {
        try
        {
            string json = File.ReadAllText(AppPaths.ConfigPath);
            bool setup = ContainsBool(json, "setup_complete", true);

            // A config without kfu_mode was written before multi-university
            // support (v1), where a completed setup was always the full
            // University of Graz VPN with the fixed Ctrl+Alt+V shortcut.
            bool hasV2 = json.IndexOf("\"kfu_mode\"", StringComparison.Ordinal) >= 0;
            string kfuMode   = hasV2 ? ExtractString(json, "kfu_mode")   : (setup ? "vpn" : "none");
            string kfuHotkey = hasV2 ? ExtractString(json, "kfu_hotkey") : (setup ? "ctrl+alt+v" : "");

            return new AppConfig(
                Email:            ExtractString(json, "email"),
                SetupComplete:    setup,
                StartWithWindows: ContainsBool(json, "start_with_windows", true),
                KfuMode:          string.IsNullOrEmpty(kfuMode) ? "none" : kfuMode,
                TuEnabled:        ContainsBool(json, "tu_enabled", true),
                KfuHotkey:        kfuHotkey,
                TuHotkey:         ExtractString(json, "tu_hotkey"),
                KfuTotpAlgorithm: ExtractStringOr(json, "kfu_totp_algorithm", "sha1"),
                KfuTotpPeriod:    ExtractInt(json, "kfu_totp_period", 30),
                KfuTotpDigits:    ExtractInt(json, "kfu_totp_digits", 6),
                TuTotpAlgorithm:  ExtractStringOr(json, "tu_totp_algorithm", "sha256"),
                TuTotpPeriod:     ExtractInt(json, "tu_totp_period", 60),
                TuTotpDigits:     ExtractInt(json, "tu_totp_digits", 6));
        }
        catch
        {
            return new AppConfig("", false, false, "none", false, "", "",
                                 "sha1", 30, 6, "sha256", 60, 6);
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

    private static string ExtractStringOr(string json, string key, string fallback)
    {
        string value = ExtractString(json, key);
        return string.IsNullOrEmpty(value) ? fallback : value;
    }

    private static int ExtractInt(string json, string key, int fallback)
    {
        var marker = $"\"{key}\"";
        int i = json.IndexOf(marker, StringComparison.Ordinal);
        if (i < 0) return fallback;
        i += marker.Length;
        while (i < json.Length && (json[i] == ':' || json[i] == ' ')) i++;
        int start = i;
        while (i < json.Length && char.IsDigit(json[i])) i++;
        return i > start && int.TryParse(json.Substring(start, i - start), out int value)
            ? value
            : fallback;
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
