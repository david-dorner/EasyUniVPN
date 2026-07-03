using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
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
    private readonly NotifyIcon _trayIcon;
    private readonly ContextMenuStrip _menuStrip;
    private readonly VpnController _vpn;
    private readonly HotkeyWindow _hotkey;
    private readonly IpMonitor _ipMonitor;
    private readonly CancellationTokenSource _cts = new();
    private readonly bool _verbose;
    private int _otpBusy; // 0=idle, 1=in-progress; Interlocked to drop rapid re-triggers

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
        _vpn = new VpnController(verbose);

        // Determine initial state synchronously before starting monitors.
        _state = VpnController.IsConnected() ? TrayState.Connected : TrayState.Disconnected;
        TrayLog.Info($"Initial VPN state: {_state}");
        if (_state == TrayState.Connected) VpnController.RecordSessionStarted();
        else                               VpnController.RecordSessionEnded();

        // Build menu strip and force handle creation on the UI thread so that
        // BeginInvoke from background threads is safe from the moment monitors start.
        _menuStrip = new ContextMenuStrip();
        _ = _menuStrip.Handle;  // forces HWND creation

        _currentIcon = LoadOrDrawIcon(_state == TrayState.Connected);
        _trayIcon = new NotifyIcon
        {
            Icon    = _currentIcon,
            Text    = "EasyUniVPN",
            Visible = true,
            ContextMenuStrip = _menuStrip,
        };

        RebuildMenu();

        // Register Ctrl+Alt+V global hotkey for OTP paste.
        _hotkey = new HotkeyWindow(OnOtpHotkey);
        TrayLog.Info($"[HOTKEY] IsRegistered={_hotkey.IsRegistered}");
        if (!_hotkey.IsRegistered)
            Notify("EasyUniVPN - hotkey unavailable",
                   "Ctrl+Alt+V could not be registered. Another app may own that shortcut.");
        else
            Notify("EasyUniVPN", "Hotkey ready - press Ctrl+Alt+V anywhere to paste your OTP.");

        // Register for IP-change notifications (callback-based - no thread is
        // parked waiting; the OS calls in when an address changes).
        _ipMonitor = new IpMonitor(OnIpChanged);

        // Measure the login server's clock offset in the background so the
        // first Ctrl+Alt+V paste already generates a server-time TOTP code.
        ServerClock.WarmUp();
    }

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

        bool enabled = _state is TrayState.Disconnected or TrayState.Connected;
        var toggle = new ToolStripMenuItem(ToggleLabel(_state)) { Enabled = enabled };
        toggle.Click += (_, _) => OnToggle();

        var setup = new ToolStripMenuItem("Setup");
        setup.Click += (_, _) => OnSetup();

        var notif = new ToolStripMenuItem("Notifications") { Checked = TraySettings.NotificationsEnabled };
        notif.Click += (_, _) =>
        {
            TraySettings.NotificationsEnabled = !TraySettings.NotificationsEnabled;
            _menuStrip.BeginInvoke(new Action(RebuildMenu));
        };

        var quit = new ToolStripMenuItem("Quit");
        quit.Click += (_, _) => OnQuit();

        _menuStrip.Items.AddRange(new ToolStripItem[] { toggle, setup, notif, quit });
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
            if (!AppConfig.Load().SetupComplete)
            {
                TrayLog.Info("Setup not complete - opening setup wizard");
                OnSetup();
                return;
            }
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
    private void OnOtpHotkey()
    {
        TrayLog.Info("[OTP] hotkey fired");
        if (Interlocked.CompareExchange(ref _otpBusy, 1, 0) != 0)
        {
            TrayLog.Info("[OTP] dropped - paste already in flight");
            return;
        }
        var t = new Thread(PasteOtpOnSta);
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
    /// 1. Compute the current 6-digit OTP.
    /// 2. Snapshot the current clipboard (best-effort).
    /// 3. Put the OTP on the clipboard (flushed so it survives thread exit).
    /// 4. Send a genuine Ctrl+V to the focused app (releasing the still-held
    ///    Ctrl+Alt modifier keys first so the app sees a clean Ctrl+V).
    /// 5. After 150 ms - long enough for any app to process the paste -
    ///    restore the original clipboard contents.
    /// </summary>
    private void PasteOtpOnSta()
    {
        try
        {
            TrayLog.Info("[OTP] reading TOTP secret from CredMan...");
            string? secret = AppPaths.ReadTotpSecret();
            if (secret is null)
            {
                TrayLog.Warn("[OTP] no secret found - setup not complete");
                Notify("OTP Error", "TOTP secret not found - run Setup first.");
                return;
            }

            TrayLog.Info($"[OTP] secret found ({secret.Length} chars), computing code...");
            string? code = Totp.Compute(secret);
            if (code is null)
            {
                TrayLog.Error("[OTP] Totp.Compute returned null");
                Notify("OTP Error", "Failed to compute OTP - check your TOTP secret.");
                return;
            }

            TrayLog.Info($"[OTP] code computed ({Totp.SecondsRemaining()}s remaining), snapshotting clipboard...");
            IDataObject? saved = null;
            try   { saved = Clipboard.GetDataObject(); }
            catch (Exception ex) { TrayLog.Warn($"[OTP] snapshot failed: {ex.Message}"); }

            TrayLog.Info("[OTP] setting clipboard...");
            // copy: true calls OleFlushClipboard - clipboard data survives thread exit.
            Clipboard.SetDataObject(new DataObject(DataFormats.UnicodeText, code), copy: true);

            TrayLog.Info("[OTP] sending Ctrl+V...");
            SendCtrlV();

            TrayLog.Info("[OTP] waiting 150 ms for target app to paste...");
            Thread.Sleep(150);

            TrayLog.Info("[OTP] restoring clipboard...");
            try
            {
                if (saved != null) Clipboard.SetDataObject(saved, copy: true);
                else               Clipboard.Clear();
            }
            catch (Exception ex) { TrayLog.Warn($"[OTP] restore failed: {ex.Message}"); }

            TrayLog.Info($"[OTP] done ({Totp.SecondsRemaining()}s remaining in TOTP window)");
        }
        catch (Exception ex)
        {
            TrayLog.Error($"[OTP] unhandled exception: {ex}");
            Notify("OTP Error", "Could not paste OTP.");
        }
        finally
        {
            Interlocked.Exchange(ref _otpBusy, 0);
        }
    }

    /// <summary>
    /// Releases the Ctrl+Alt keys still physically held from the hotkey, then
    /// injects a genuine Ctrl+V keystroke sequence via SendInput.
    ///
    /// All six events are sent in a single SendInput call so the sequence is
    /// atomic from the perspective of other processes reading the input queue.
    /// </summary>
    private static void SendCtrlV()
    {
        const ushort VK_CONTROL = 0x11;
        const ushort VK_MENU    = 0x12; // Alt
        const ushort VK_V       = 0x56;
        int sz = Marshal.SizeOf<NativeMethods.INPUT>();

        var inputs = new NativeMethods.INPUT[]
        {
            // Release the modifier keys still held from the Ctrl+Alt+V hotkey.
            new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_CONTROL, dwFlags = NativeMethods.KEYEVENTF_KEYUP },
            new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_MENU,    dwFlags = NativeMethods.KEYEVENTF_KEYUP },
            // Genuine Ctrl+V.
            new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_CONTROL },
            new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_V },
            new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_V,       dwFlags = NativeMethods.KEYEVENTF_KEYUP },
            new() { type = NativeMethods.INPUT_KEYBOARD, wVk = VK_CONTROL, dwFlags = NativeMethods.KEYEVENTF_KEYUP },
        };
        NativeMethods.SendInput((uint)inputs.Length, inputs, sz);
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
        _currentIcon = LoadOrDrawIcon(connected);
        _trayIcon.Icon = _currentIcon;
        old?.Dispose();
    }

    /// <summary>
    /// Load themed PNG from assets/, falling back to a programmatically drawn icon.
    /// Mirrors <c>tray/app.py::_make_icon()</c>.
    /// </summary>
    private static Icon LoadOrDrawIcon(bool connected)
    {
        string? path = AppPaths.TrayIconPath(connected);
        if (path is not null)
        {
            try
            {
                using var bmp = new Bitmap(path);
                return BitmapToIcon(bmp);
            }
            catch { }
        }
        return DrawIcon(connected);
    }

    /// <summary>Programmatic fallback icon - same glyph as the Python version.</summary>
    private static Icon DrawIcon(bool connected)
    {
        using var bmp = new Bitmap(64, 64, PixelFormat.Format32bppArgb);
        using var g   = Graphics.FromImage(bmp);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Color.Transparent);

        var accent = connected
            ? Color.FromArgb(52, 168, 83)
            : Color.FromArgb(190, 45, 45);

        using (var brush = new SolidBrush(accent))
            g.FillEllipse(brush, 8, 8, 48, 48);

        bool dark = AppPaths.IsDarkMode();
        var fg = dark ? Color.FromArgb(245, 245, 245) : Color.FromArgb(32, 32, 32);
        using var pen = new Pen(fg, 6f);
        g.DrawArc(pen, 20, 19, 24, 26, 205, -230);   // arc (VPN glyph)
        g.DrawLine(pen, 32, 31, 32, 48);              // vertical stem

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
        // Deregister the IP-change callback first so no notification can
        // arrive while the rest of the tray is being torn down.
        _ipMonitor.Dispose();
        _cts.Dispose();
        _hotkey.Dispose();
        _trayIcon.Visible = false;
        _trayIcon.Dispose();
        _menuStrip.Dispose();
        _currentIcon?.Dispose();
        _vpn.Dispose();
    }
}
