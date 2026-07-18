using System.Diagnostics;
using System.Drawing;
using System.Runtime.InteropServices;

namespace EasyUniVPN;

// ── state machine ────────────────────────────────────────────────────────────

/// <summary>
/// Mirrors the four states in <c>tray/app.py</c>.
///
/// DISCONNECTED → CONNECTING → CONNECTED → DISCONNECTING → DISCONNECTED
/// </summary>
internal enum TrayState
{
    Disconnected,
    Connecting,
    Connected,
    Disconnecting,
}

// ── tray application ─────────────────────────────────────────────────────────

/// <summary>
/// The system tray icon and menu - mirrors <c>tray/app.py::TrayApp</c>.
///
/// Two event sources drive state transitions:
/// <list type="bullet">
///   <item><c>IpMonitor</c> - OS-invoked callback on IP address changes; handles tunnel up/down.</item>
///   <item><c>WatchProcessAsync</c> - awaits process exit; catches auth failures.</item>
/// </list>
///
/// All UI operations (icon, menu, balloon) are marshalled back to the Windows
/// message-loop thread via <c>_menuStrip.BeginInvoke</c>.
/// </summary>
internal sealed class TrayApp : IDisposable
{
    private TrayState _state;
    private Icon? _currentIcon;
    private AppConfig _config;                // swapped on live reload (UI thread)
    private HotkeyWindow _hotkey;             // recreated when shortcuts change
    private IpMonitor? _ipMonitor;            // null when no VPN is configured
    private long _configStamp;                // last seen config.json write time
    private readonly NotifyIcon _trayIcon;
    private readonly ContextMenuStrip _menuStrip;
    private readonly VpnController _vpn;
    private readonly System.Windows.Forms.Timer _configTimer;
    private readonly CancellationTokenSource _cts = new();
    private readonly bool _verbose;
    private readonly Microsoft.Win32.UserPreferenceChangedEventHandler _preferenceChanged;
    private readonly EventHandler _displayChanged;
    private int _otpBusy; // 0=idle, 1=in-progress; Interlocked to drop rapid re-triggers

    /// <summary>
    /// Set when a live config reload enables the VPN while this process is
    /// unelevated - elevation cannot be gained in-process, so Program.Main
    /// relaunches via the launcher (which handles UAC) after exit.
    /// </summary>
    internal bool RestartRequested { get; private set; }

    // Menu item labels - mirrors _TOGGLE_LABEL in app.py
    private static string ToggleLabel(TrayState s) => s switch
    {
        TrayState.Disconnected  => "Connect",
        TrayState.Connecting    => "Connecting...",
        TrayState.Connected     => "Disconnect",
        TrayState.Disconnecting => "Disconnecting...",
        _                       => "Connect",
    };

    // ── construction ─────────────────────────────────────────────────────

    internal TrayApp(bool verbose)
    {
        _verbose = verbose;
        _config = AppConfig.Load();
        _vpn = new VpnController(verbose);

        // Determine initial state synchronously before starting monitors.
        // Without the VPN configured (one-time-codes-only setups) the state
        // machine stays parked at Disconnected and no netsh check ever runs.
        _state = _config.VpnEnabled && VpnController.IsConnected()
            ? TrayState.Connected
            : TrayState.Disconnected;
        TrayLog.Info($"Initial VPN state: {_state} (mode kfu={_config.KfuMode}, tu={_config.TuEnabled})");
        if (_config.VpnEnabled)
        {
            if (_state == TrayState.Connected) VpnController.RecordSessionStarted();
            else                               VpnController.RecordSessionEnded();
        }

        // Build menu strip and force handle creation on the UI thread so that
        // BeginInvoke from background threads is safe from the moment monitors start.
        _menuStrip = new ContextMenuStrip();
        _ = _menuStrip.Handle;  // forces HWND creation

        _currentIcon = CreateTrayIcon(_state == TrayState.Connected);
        _trayIcon = new NotifyIcon
        {
            Icon    = _currentIcon,
            Text    = "EasyUniVPN",
            Visible = true,
        };
        // The menu is shown manually (not via ContextMenuStrip assignment) so
        // its open direction can be forced away from the taskbar - WinForms'
        // automatic placement sometimes drops it below the screen edge on
        // DPI-scaled displays.
        _trayIcon.MouseUp += (_, e) =>
        {
            if (e.Button == MouseButtons.Right)
                ShowContextMenu();
        };

        // Re-render the icon when the theme, DPI, or display layout changes -
        // both the correct pixel size and the glyph tone depend on them.
        // Handlers are kept in fields so Dispose can unsubscribe; SystemEvents
        // holds static references and would otherwise keep this object alive.
        _preferenceChanged = (_, _) => OnSystemSettingsChanged();
        _displayChanged    = (_, _) => OnSystemSettingsChanged();
        Microsoft.Win32.SystemEvents.UserPreferenceChanged += _preferenceChanged;
        Microsoft.Win32.SystemEvents.DisplaySettingsChanged += _displayChanged;

        RebuildMenu();

        // Register the configured quick-paste shortcuts (one per university).
        _hotkey = CreateHotkeyWindow(out var shortcutSummary);
        if (shortcutSummary.Count > 0 && !_hotkey.IsRegistered)
            Notify("EasyUniVPN - shortcuts unavailable",
                   "The quick-paste shortcuts could not be registered. Another app may block keyboard hooks.");
        else if (shortcutSummary.Count > 0)
            Notify("EasyUniVPN", $"Quick paste ready - {string.Join(", ", shortcutSummary)}.");

        // Register for IP-change notifications (callback-based - no thread is
        // parked waiting; the OS calls in when an address changes). Only the
        // VPN state machine needs them.
        _ipMonitor = _config.VpnEnabled ? new IpMonitor(OnIpChanged) : null;

        // Watch config.json so setup-console changes (shortcuts, added or
        // removed universities, reset) apply to this running instance without
        // a restart. Polling rather than FileSystemWatcher: reset deletes the
        // whole config directory, which kills a watcher rooted in it.
        _configStamp = ConfigStamp();
        _configTimer = new System.Windows.Forms.Timer { Interval = 2000 };
        _configTimer.Tick += (_, _) => ReloadConfigIfChanged();
        _configTimer.Start();

        // Measure the login server's clock offset in the background so the
        // first paste already generates a server-time TOTP code.
        ServerClock.WarmUp();
    }

    private HotkeyWindow CreateHotkeyWindow(out List<string> shortcutSummary)
    {
        var bindings = new List<HotkeyWindow.Binding>();
        shortcutSummary = new List<string>();
        AddHotkeyBinding(bindings, shortcutSummary, "kfu", _config.KfuConfigured, _config.KfuHotkey);
        AddHotkeyBinding(bindings, shortcutSummary, "tu", _config.TuEnabled, _config.TuHotkey);
        var hotkey = new HotkeyWindow(bindings);
        TrayLog.Info($"[HOTKEY] IsRegistered={hotkey.IsRegistered} ({string.Join(", ", shortcutSummary)})");
        return hotkey;
    }

    private void AddHotkeyBinding(
        List<HotkeyWindow.Binding> bindings, List<string> summary,
        string university, bool configured, string spec)
    {
        if (!configured || string.IsNullOrEmpty(spec))
            return;
        var binding = HotkeyWindow.TryParse(spec);
        if (binding is null)
        {
            TrayLog.Warn($"[HOTKEY] could not parse configured shortcut '{spec}' for {university}");
            return;
        }
        binding.Callback = () => OnOtpHotkey(university, binding);
        bindings.Add(binding);
        summary.Add($"{FormatHotkey(spec)} pastes the {UniversityLabel(university)} code");
    }

    private static string UniversityLabel(string university)
        => university == "tu" ? "TU Graz" : "University of Graz";

    // ── live config reload ───────────────────────────────────────────────

    private static long ConfigStamp()
    {
        try
        {
            var info = new FileInfo(AppPaths.ConfigPath);
            return info.Exists ? info.LastWriteTimeUtc.Ticks : 0L;
        }
        catch { return 0L; }
    }

    /// <summary>
    /// Applies setup-console changes to this running instance: shortcut
    /// changes re-register the hook, adding/removing a university updates the
    /// menu, and enabling/disabling the VPN starts/stops the monitors. Runs
    /// on the UI thread (timer tick), so menu and hook work is safe inline.
    /// </summary>
    private void ReloadConfigIfChanged()
    {
        if (_cts.IsCancellationRequested)
            return;
        long stamp = ConfigStamp();
        if (stamp == _configStamp)
            return;
        _configStamp = stamp;

        var oldConfig = _config;
        _config = AppConfig.Load();
        TrayLog.Info($"Config changed on disk - applying (kfu={_config.KfuMode}, tu={_config.TuEnabled}, " +
                     $"shortcuts='{_config.KfuHotkey}'/'{_config.TuHotkey}')");

        // Re-register shortcuts when they (or the set of universities) changed.
        if (oldConfig.KfuHotkey != _config.KfuHotkey
            || oldConfig.TuHotkey != _config.TuHotkey
            || oldConfig.KfuConfigured != _config.KfuConfigured
            || oldConfig.TuEnabled != _config.TuEnabled)
        {
            _hotkey.Dispose();
            _hotkey = CreateHotkeyWindow(out _);
        }

        if (_config.VpnEnabled && !oldConfig.VpnEnabled)
        {
            // VPN newly enabled. Elevation cannot be gained in-process, so an
            // unelevated (codes-only) instance restarts via the launcher,
            // which shows the usual UAC prompt and brings the tray back.
            if (!Program.IsAdmin())
            {
                TrayLog.Info("VPN enabled but process is unelevated - restarting via the launcher");
                RestartRequested = true;
                _trayIcon.Visible = false;
                Application.Exit();
                return;
            }
            _ipMonitor ??= new IpMonitor(OnIpChanged);
            _state = VpnController.IsConnected() ? TrayState.Connected : TrayState.Disconnected;
            if (_state == TrayState.Connected)
                VpnController.RecordSessionStarted();
        }
        else if (!_config.VpnEnabled && oldConfig.VpnEnabled)
        {
            _ipMonitor?.Dispose();
            _ipMonitor = null;
            _state = TrayState.Disconnected;
        }

        RebuildMenu();
        UpdateIcon(_state is TrayState.Connected or TrayState.Disconnecting);
    }

    // ── menu display ─────────────────────────────────────────────────────

    /// <summary>
    /// Shows the context menu at the cursor, opening away from the nearest
    /// screen edges so it can never extend under the taskbar.
    /// </summary>
    private void ShowContextMenu()
    {
        // Without foregrounding the menu's window first, a manually shown
        // ContextMenuStrip does not dismiss when the user clicks elsewhere.
        NativeMethods.SetForegroundWindow(_menuStrip.Handle);

        var position = Cursor.Position;
        var area = Screen.FromPoint(position).WorkingArea;
        bool openUp   = position.Y > area.Top + area.Height / 2;
        bool openLeft = position.X > area.Left + area.Width / 2;
        var direction = openUp
            ? (openLeft ? ToolStripDropDownDirection.AboveLeft : ToolStripDropDownDirection.AboveRight)
            : (openLeft ? ToolStripDropDownDirection.BelowLeft : ToolStripDropDownDirection.BelowRight);
        _menuStrip.Show(position, direction);
    }

    private static string FormatHotkey(string spec)
        => string.Join("+", spec.Split('+').Select(p =>
               p.Length == 1 ? p.ToUpperInvariant() : char.ToUpperInvariant(p[0]) + p.Substring(1)));

    // ── monitors ─────────────────────────────────────────────────────────

    /// <summary>
    /// Invoked by <see cref="IpMonitor"/> (coalesced, on a thread-pool thread)
    /// after any IP address change. Re-checks the actual VPN state and flips
    /// steady-state. Mirrors <c>tray/app.py::monitor()</c>:
    /// <list type="bullet">
    ///   <item>CONNECTING + VPN appeared → CONNECTED</item>
    ///   <item>CONNECTED/DISCONNECTING + VPN gone → DISCONNECTED</item>
    ///   <item>CONNECTING + VPN gone → ignored (WatchProcess handles that)</item>
    /// </list>
    /// </summary>
    private void OnIpChanged()
    {
        if (_cts.IsCancellationRequested)
            return;

        bool connected = VpnController.IsConnected();
        if (connected && _state is TrayState.Disconnected or TrayState.Connecting)
            SetState(TrayState.Connected);
        else if (!connected && _state is TrayState.Connected or TrayState.Disconnecting)
            SetState(TrayState.Disconnected);
    }

    /// <summary>
    /// Awaits the openconnect-saml process exit and resets state if it exits
    /// unexpectedly. Mirrors <c>tray/app.py::_watch_process()</c>.
    ///
    /// <c>Process.WaitForExitAsync</c> is .NET 5+ only; on net48 we use the
    /// Exited event pattern which is equivalent and allocation-free.
    /// </summary>
    private Task WatchProcessAsync(Process proc)
    {
        var tcs = new TaskCompletionSource<bool>();
        proc.EnableRaisingEvents = true;
        proc.Exited += (_, _) => tcs.TrySetResult(true);
        _cts.Token.Register(() => tcs.TrySetResult(false));
        if (proc.HasExited) tcs.TrySetResult(true);

        return tcs.Task.ContinueWith(_ =>
        {
            if (_state is TrayState.Connecting or TrayState.Connected
                    && !_cts.IsCancellationRequested)
                SetState(TrayState.Disconnected);
        });
    }

    // ── state machine ─────────────────────────────────────────────────────

    /// <summary>
    /// Atomically update state, rebuild menu, update icon, and send notification.
    /// Mirrors <c>tray/app.py::_set_state()</c>.
    ///
    /// Called from background threads - all UI work is marshalled to the message loop.
    /// </summary>
    private void SetState(TrayState newState)
    {
        if (_state == newState) return;
        var old = _state;
        _state = newState;
        TrayLog.Info($"State: {old} → {newState}");

        if (newState == TrayState.Connected)    VpnController.RecordSessionStarted();
        else if (newState == TrayState.Disconnected) VpnController.RecordSessionEnded();

        if (_cts.IsCancellationRequested) return;

        // All UI operations must happen on the message-loop thread.
        _menuStrip.BeginInvoke(new Action(() =>
        {
            // Always rebuild the menu - WinForms caches menu item text, so we must
            // recreate items on every state change to show the correct label/enabled state.
            // (Same lesson learned from pystray's Win32 HMENU caching in the Python version.)
            RebuildMenu();

            // Icon only changes at steady-state boundaries.
            if (newState is TrayState.Connected or TrayState.Disconnected)
                UpdateIcon(newState == TrayState.Connected);

            // Balloon notifications for steady-state transitions.
            if (TraySettings.NotificationsEnabled)
            {
                if (newState == TrayState.Connected)
                    Notify("VPN Connected", $"Connected to {AppPaths.VpnServer}");
                // Check both Connected and Disconnecting as old state: the normal
                // disconnect path goes Connected→Disconnecting→Disconnected, so
                // old==Connected never fires on the final transition.
                else if (newState == TrayState.Disconnected
                         && old is TrayState.Connected or TrayState.Disconnecting)
                    Notify("VPN Disconnected", $"Disconnected from {AppPaths.VpnServer}");
            }
        }));
    }

    // ── menu ─────────────────────────────────────────────────────────────

    /// <summary>
    /// Rebuild the context menu from current state.
    /// Must be called on the UI thread.
    /// </summary>
    private void RebuildMenu()
    {
        _menuStrip.Items.Clear();
        var items = new List<ToolStripItem>();

        // Connect/Disconnect only exists when the full VPN is configured.
        if (_config.VpnEnabled)
        {
            bool enabled = _state is TrayState.Disconnected or TrayState.Connected;
            var toggle = new ToolStripMenuItem(ToggleLabel(_state)) { Enabled = enabled };
            toggle.Click += (_, _) => OnToggle();
            items.Add(toggle);
        }

        // Copy OTP submenu: both universities are always listed; the ones that
        // are not set up are visible but grayed out.
        var copyOtp = new ToolStripMenuItem("Copy OTP");
        var copyKfu = new ToolStripMenuItem("Copy KFU OTP") { Enabled = _config.KfuConfigured };
        copyKfu.Click += (_, _) => CopyOtp("kfu");
        var copyTu = new ToolStripMenuItem("Copy TU OTP") { Enabled = _config.TuEnabled };
        copyTu.Click += (_, _) => CopyOtp("tu");
        copyOtp.DropDownItems.AddRange(new ToolStripItem[] { copyKfu, copyTu });
        items.Add(copyOtp);

        var setup = new ToolStripMenuItem("Setup");
        setup.Click += (_, _) => OnSetup();
        items.Add(setup);

        var notif = new ToolStripMenuItem("Notifications") { Checked = TraySettings.NotificationsEnabled };
        notif.Click += (_, _) =>
        {
            TraySettings.NotificationsEnabled = !TraySettings.NotificationsEnabled;
            _menuStrip.BeginInvoke(new Action(RebuildMenu));
        };
        items.Add(notif);

        var quit = new ToolStripMenuItem("Quit");
        quit.Click += (_, _) => OnQuit();
        items.Add(quit);

        _menuStrip.Items.AddRange(items.ToArray());
    }

    // ── OTP copy ─────────────────────────────────────────────────────────

    /// <summary>
    /// Copies the current one-time code for the given university to the
    /// clipboard. Runs on the UI thread (menu click), which is STA, so the
    /// clipboard can be used directly.
    /// </summary>
    private void CopyOtp(string university)
    {
        string label = UniversityLabel(university);
        try
        {
            string? code = ComputeCode(university, out int secondsLeft);
            if (code is null)
            {
                Notify("OTP Error", $"Could not compute the {label} code - check the saved TOTP secret.");
                return;
            }
            Clipboard.SetDataObject(new DataObject(DataFormats.UnicodeText, code), copy: true);
            TrayLog.Info($"[OTP] {label} code copied to clipboard ({secondsLeft}s remaining)");
            if (TraySettings.NotificationsEnabled)
                Notify($"{label} code copied", $"Valid for another {secondsLeft} seconds.");
        }
        catch (Exception ex)
        {
            TrayLog.Error($"[OTP] copy failed: {ex.Message}");
            Notify("OTP Error", $"Could not copy the {label} code.");
        }
    }

    /// <summary>
    /// Computes the current code for a university using its stored secret and
    /// configured otpauth parameters. Returns null when unavailable.
    /// </summary>
    private string? ComputeCode(string university, out int secondsLeft)
    {
        string? secret;
        string algorithm;
        int period, digits;
        if (university == "tu")
        {
            secret = AppPaths.ReadTuTotpSecret();
            algorithm = _config.TuTotpAlgorithm;
            period = _config.TuTotpPeriod;
            digits = _config.TuTotpDigits;
        }
        else
        {
            secret = AppPaths.ReadTotpSecret();
            algorithm = _config.KfuTotpAlgorithm;
            period = _config.KfuTotpPeriod;
            digits = _config.KfuTotpDigits;
        }
        secondsLeft = Totp.SecondsRemaining(period);
        return secret is null ? null : Totp.Compute(secret, algorithm, period, digits);
    }

    // ── toggle ────────────────────────────────────────────────────────────

    /// <summary>Mirrors <c>tray/app.py::toggle()</c>.</summary>
    private void OnToggle()
    {
        if (_state == TrayState.Connected)
        {
            TrayLog.Info("User requested disconnect");
            SetState(TrayState.Disconnecting);
            Task.Run(DoDisconnect);
        }
        else if (_state == TrayState.Disconnected)
        {
            TrayLog.Info("User requested connect");
            SetState(TrayState.Connecting);
            Task.Run(DoConnect);
        }
        // CONNECTING / DISCONNECTING: button is disabled; this click shouldn't fire.
    }

    private void DoConnect()
    {
        try
        {
            // Another VPN owning the connection makes openconnect hang until
            // its timeout with no useful error - detect it up front and tell
            // the user what to do instead.
            string? conflict = VpnController.DetectConflictingVpn();
            if (conflict != null)
            {
                TrayLog.Warn($"Connect blocked - another VPN appears active: {conflict}");
                Notify("Another VPN is active",
                       $"\"{conflict}\" appears to be connected. Disconnect it first, then click Connect again.");
                SetState(TrayState.Disconnected);
                return;
            }

            _vpn.Connect();
            if (_vpn.Process is { } proc)
                _ = Task.Run(() => WatchProcessAsync(proc));
        }
        catch (Exception ex)
        {
            TrayLog.Error($"Connect failed: {ex.Message}");
            SetState(TrayState.Disconnected);
        }
    }

    private void DoDisconnect()
    {
        try
        {
            // Mirrors tray/app.py::_do_disconnect(): kill our own process tree
            // if we started this session; otherwise fall back to disconnecting
            // whatever session is up (e.g. started by a previous tray instance).
            if (_vpn.Process is { HasExited: false })
                _vpn.Disconnect();
            else if (VpnController.IsConnected())
                VpnController.DisconnectExternalSession();
        }
        catch (Exception ex)
        {
            TrayLog.Error($"Disconnect failed: {ex.Message}");
            SetState(VpnController.IsConnected() ? TrayState.Connected : TrayState.Disconnected);
        }
    }

    // ── setup ─────────────────────────────────────────────────────────────

    private static void OnSetup()
    {
        TrayLog.Info("Opening setup console");
        try
        {
            // UseShellExecute = true launches exactly as if the user double-clicked
            // the executable - fresh process, clean token, UAC if the manifest asks
            // for it, and a new console window allocated by Windows.
            Process.Start(new ProcessStartInfo
            {
                FileName        = AppPaths.ConsoleExe,
                Arguments       = "setup",
                UseShellExecute = true,
            });
            TrayLog.Info("Setup console started");
        }
        catch (Exception ex)
        {
            TrayLog.Error($"OnSetup failed: {ex.Message}");
        }
    }

    // ── OTP paste ────────────────────────────────────────────────────────

    /// <summary>
    /// Called directly from the WH_KEYBOARD_LL hook proc on the UI thread.
    /// Must return quickly - just checks the reentrance guard and spawns a
    /// dedicated STA thread. All clipboard/OLE work happens in PasteOtpOnSta.
    /// </summary>
    private void OnOtpHotkey(string university, HotkeyWindow.Binding binding)
    {
        TrayLog.Info($"[OTP] {binding.Spec} fired ({university})");
        if (Interlocked.CompareExchange(ref _otpBusy, 1, 0) != 0)
        {
            TrayLog.Info("[OTP] dropped - paste already in flight");
            return;
        }
        var t = new Thread(() => PasteOtpOnSta(university, binding));
        t.SetApartmentState(ApartmentState.STA); // Clipboard.SetDataObject requires STA
        t.IsBackground = true;
        t.Name = "OtpPaste";
        t.Start();
    }

    /// <summary>
    /// Runs on a dedicated STA thread so that OLE/Clipboard calls are guaranteed
    /// to have the correct apartment state regardless of how the hook fired.
    ///
    /// Flow:
    /// 1. Compute the current code for the university the shortcut belongs to.
    /// 2. Deep-copy the current clipboard contents (format by format - the
    ///    IDataObject from Clipboard.GetDataObject is only a live proxy that
    ///    turns stale the moment the clipboard changes, so re-setting it
    ///    would not restore anything).
    /// 3. Put the code on the clipboard (flushed so it survives thread exit).
    /// 4. Send a genuine Ctrl+V to the focused app (releasing the shortcut's
    ///    still-held modifier keys first so the app sees a clean Ctrl+V).
    /// 5. After 150 ms - long enough for any app to process the paste -
    ///    restore the copied original clipboard contents.
    /// </summary>
    private void PasteOtpOnSta(string university, HotkeyWindow.Binding binding)
    {
        string label = UniversityLabel(university);
        try
        {
            TrayLog.Info($"[OTP] computing {label} code...");
            string? code = ComputeCode(university, out int secondsLeft);
            if (code is null)
            {
                TrayLog.Warn($"[OTP] no usable {label} secret - run Setup");
                Notify("OTP Error", $"The {label} TOTP secret is missing - run Setup first.");
                return;
            }

            TrayLog.Info($"[OTP] code computed ({secondsLeft}s remaining), snapshotting clipboard...");
            DataObject? saved = SnapshotClipboard();

            TrayLog.Info("[OTP] setting clipboard...");
            // copy: true calls OleFlushClipboard - clipboard data survives thread exit.
            Clipboard.SetDataObject(new DataObject(DataFormats.UnicodeText, code), copy: true);

            TrayLog.Info("[OTP] sending Ctrl+V...");
            SendPaste(binding);

            TrayLog.Info("[OTP] waiting 150 ms for target app to paste...");
            Thread.Sleep(150);

            TrayLog.Info("[OTP] restoring clipboard...");
            try
            {
                if (saved != null) Clipboard.SetDataObject(saved, copy: true);
                else               Clipboard.Clear();
            }
            catch (Exception ex) { TrayLog.Warn($"[OTP] restore failed: {ex.Message}"); }

            TrayLog.Info("[OTP] done");
        }
        catch (Exception ex)
        {
            TrayLog.Error($"[OTP] unhandled exception: {ex}");
            Notify("OTP Error", $"Could not paste the {label} code.");
        }
        finally
        {
            Interlocked.Exchange(ref _otpBusy, 0);
        }
    }

    /// <summary>
    /// Deep-copies the current clipboard contents into a standalone
    /// DataObject that stays valid after the clipboard changes. Formats that
    /// refuse to copy (some app-private ones do) are skipped individually,
    /// so a stubborn format cannot break restoring the rest.
    /// </summary>
    private static DataObject? SnapshotClipboard()
    {
        try
        {
            var source = Clipboard.GetDataObject();
            if (source is null)
                return null;
            var copy = new DataObject();
            foreach (string format in source.GetFormats(false))
            {
                try
                {
                    object? data = source.GetData(format, false);
                    if (data != null)
                        copy.SetData(format, false, data);
                }
                catch { }
            }
            return copy;
        }
        catch (Exception ex)
        {
            TrayLog.Warn($"[OTP] clipboard snapshot failed: {ex.Message}");
            return null;
        }
    }

    /// <summary>
    /// Releases the shortcut's modifier keys still physically held from the
    /// hotkey press, then injects a genuine Ctrl+V keystroke sequence via
    /// SendInput.
    ///
    /// All events are sent in a single SendInput call so the sequence is
    /// atomic from the perspective of other processes reading the input queue.
    /// </summary>
    private static void SendPaste(HotkeyWindow.Binding binding)
    {
        const ushort VK_CONTROL = 0x11;
        const ushort VK_SHIFT   = 0x10;
        const ushort VK_MENU    = 0x12; // Alt
        const ushort VK_V       = 0x56;

        var inputs = new List<NativeMethods.INPUT>();
        // Release the modifier keys still held from the shortcut press.
        if (binding.Ctrl)
            inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_CONTROL, dwFlags = NativeMethods.KEYEVENTF_KEYUP });
        if (binding.Shift)
            inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_SHIFT,   dwFlags = NativeMethods.KEYEVENTF_KEYUP });
        if (binding.Alt)
            inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_MENU,    dwFlags = NativeMethods.KEYEVENTF_KEYUP });
        // Genuine Ctrl+V.
        inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_CONTROL });
        inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_V });
        inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_V,       dwFlags = NativeMethods.KEYEVENTF_KEYUP });
        inputs.Add(new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_CONTROL, dwFlags = NativeMethods.KEYEVENTF_KEYUP });

        NativeMethods.SendInput((uint)inputs.Count, inputs.ToArray(), Marshal.SizeOf<NativeMethods.INPUT>());
    }

    // ── quit ──────────────────────────────────────────────────────────────

    /// <summary>Mirrors <c>tray/app.py::quit()</c>.</summary>
    private void OnQuit()
    {
        TrayLog.Info("Quit requested");
        _cts.Cancel();

        // Disconnect synchronously (with timeout) before exiting so that
        // any child processes (openconnect.exe) are cleaned up first.
        if (_state is TrayState.Connected or TrayState.Connecting
                      or TrayState.Disconnecting)
        {
            // 15 s budget: the external-session fallback path waits up to 10 s
            // for openconnect-saml plus 5 s for the orphan openconnect cleanup.
            var disconnectTask = Task.Run(DoDisconnect);
            disconnectTask.Wait(TimeSpan.FromSeconds(15));

            // Last-resort kill if DoDisconnect timed out.
            if (_vpn.Process is { HasExited: false })
                try { VpnController.KillTree(_vpn.Process.Id); } catch { }
        }

        _trayIcon.Visible = false;
        Application.Exit();
    }

    // ── icon ──────────────────────────────────────────────────────────────

    private void UpdateIcon(bool connected)
    {
        var old = _currentIcon;
        _currentIcon = CreateTrayIcon(connected);
        _trayIcon.Icon = _currentIcon;
        old?.Dispose();
    }

    /// <summary>
    /// Re-renders the current icon after a theme/DPI/display change. The
    /// icon reflects the last steady state (Disconnecting still shows the
    /// connected glyph, matching UpdateIcon's steady-state-only behavior).
    /// </summary>
    private void OnSystemSettingsChanged()
    {
        if (_cts.IsCancellationRequested)
            return;
        try
        {
            _menuStrip.BeginInvoke(new Action(() =>
                UpdateIcon(_state is TrayState.Connected or TrayState.Disconnecting)));
        }
        catch (Exception)
        {
            // Menu handle already gone during shutdown - nothing to update.
        }
    }

    /// <summary>
    /// Renders the Lucide shield glyph for the given connection state:
    /// shield-check when connected, shield-off when disconnected. Rendered
    /// from vector data at the native small-icon size for the current DPI -
    /// never a scaled bitmap - so the icon stays sharp at every display
    /// scale. The tone follows the taskbar theme.
    /// </summary>
    private static Icon CreateTrayIcon(bool connected)
    {
        int size = Math.Max(16, SystemInformation.SmallIconSize.Width);
        var tone = AppPaths.IsTaskbarDarkTheme()
            ? Color.FromArgb(245, 245, 245)
            : Color.FromArgb(32, 32, 32);
        using var bmp = LucideIcons.Render(
            connected ? LucideIcons.ShieldCheck : LucideIcons.ShieldOff, size, tone);
        return BitmapToIcon(bmp);
    }

    /// <summary>
    /// Convert a <see cref="Bitmap"/> to a managed <see cref="Icon"/>.
    /// Calls <c>DestroyIcon</c> to release the temporary Win32 HICON immediately.
    /// </summary>
    private static Icon BitmapToIcon(Bitmap bmp)
    {
        IntPtr hIcon = bmp.GetHicon();
        try   { return (Icon)Icon.FromHandle(hIcon).Clone(); }
        finally { NativeMethods.DestroyIcon(hIcon); }
    }

    // ── notifications ─────────────────────────────────────────────────────

    private void Notify(string title, string text)
    {
        try
        {
            _trayIcon.BalloonTipTitle = title;
            _trayIcon.BalloonTipText  = text;
            _trayIcon.BalloonTipIcon  = ToolTipIcon.Info;
            _trayIcon.ShowBalloonTip(3_000);
        }
        catch { }
    }

    // ── disposal ──────────────────────────────────────────────────────────

    public void Dispose()
    {
        _cts.Cancel();
        // Deregister external callbacks first so nothing can call in while
        // the rest of the tray is being torn down.
        _configTimer.Stop();
        _configTimer.Dispose();
        _ipMonitor?.Dispose();
        Microsoft.Win32.SystemEvents.UserPreferenceChanged -= _preferenceChanged;
        Microsoft.Win32.SystemEvents.DisplaySettingsChanged -= _displayChanged;
        _cts.Dispose();
        _hotkey.Dispose();
        _trayIcon.Visible = false;
        _trayIcon.Dispose();
        _menuStrip.Dispose();
        _currentIcon?.Dispose();
        _vpn.Dispose();
    }
}
