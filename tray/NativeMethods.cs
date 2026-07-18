using System.Runtime.InteropServices;
using System.Text;

namespace EasyUniVPN;

internal static class NativeMethods
{
    // ── routing ───────────────────────────────────────────────────────────

    /// <summary>Index of the interface that would route traffic to destAddr
    /// (IPv4 in network byte order). Returns 0 (NO_ERROR) on success.</summary>
    [DllImport("iphlpapi.dll")]
    internal static extern int GetBestInterface(uint destAddr, out uint bestIfIndex);

    // ── IP change notification (callback-based, no blocked thread) ───────
    // Callback signature per netioapi.h: PUNICAST_IPADDRESS_CHANGE_CALLBACK.
    // Row and NotificationType are unused by EasyUniVPN - any notification
    // just triggers a fresh VPN-state check - so they stay opaque IntPtr/int.

    internal delegate void IpAddressChangeCallback(IntPtr callerContext, IntPtr row, int notificationType);

    [DllImport("iphlpapi.dll")]
    internal static extern uint NotifyUnicastIpAddressChange(
        ushort family,
        IpAddressChangeCallback callback,
        IntPtr callerContext,
        [MarshalAs(UnmanagedType.U1)] bool initialNotification,
        // In/out per the API contract: must be IntPtr.Zero going in.
        ref IntPtr notificationHandle);

    [DllImport("iphlpapi.dll")]
    internal static extern uint CancelMibChangeNotify2(IntPtr notificationHandle);

    // ── icon management ───────────────────────────────────────────────────

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool DestroyIcon(IntPtr hIcon);

    // ── window activation ─────────────────────────────────────────────────
    // Needed before manually showing the tray context menu: without
    // foregrounding its window first, the menu does not dismiss when the
    // user clicks elsewhere.

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool SetForegroundWindow(IntPtr hWnd);

    // ── DPI awareness ─────────────────────────────────────────────────────

    private static readonly IntPtr DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (IntPtr)(-4);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr value);

    [DllImport("user32.dll")]
    private static extern bool SetProcessDPIAware();

    /// <summary>
    /// Opts the process in to Per-Monitor-V2 DPI awareness (Windows 10
    /// 1703+), falling back to system DPI awareness. Must run before any
    /// window is created. Without this the process is DPI-unaware and
    /// Windows bitmap-stretches everything it draws - including the tray
    /// icon, which is exactly what makes it blurry on scaled displays.
    /// </summary>
    internal static void EnablePerMonitorDpiAwareness()
    {
        try
        {
            if (!SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
                SetProcessDPIAware();
        }
        catch (EntryPointNotFoundException)
        {
            try { SetProcessDPIAware(); } catch { }
        }
    }

    // ── process creation ──────────────────────────────────────────────────

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct STARTUPINFO
    {
        public uint   cb;
        public string? lpReserved;
        public string? lpDesktop;
        public string? lpTitle;
        public uint   dwX, dwY, dwXSize, dwYSize;
        public uint   dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public ushort wShowWindow, cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public uint   dwProcessId, dwThreadId;
    }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CreateProcess(
        string?           lpApplicationName,
        StringBuilder     lpCommandLine,
        IntPtr            lpProcessAttributes,
        IntPtr            lpThreadAttributes,
        bool              bInheritHandles,
        uint              dwCreationFlags,
        IntPtr            lpEnvironment,
        string?           lpCurrentDirectory,
        ref STARTUPINFO   lpStartupInfo,
        out PROCESS_INFORMATION lpProcessInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CloseHandle(IntPtr hObject);

    // ── Windows Credential Manager ────────────────────────────────────────

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct CREDENTIAL
    {
        public uint   Flags;
        public uint   Type;
        public IntPtr TargetName;
        public IntPtr Comment;
        public long   LastWritten;     // FILETIME = two DWORDs = int64
        public uint   CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint   Persist;
        public uint   AttributeCount;
        public IntPtr Attributes;
        public IntPtr TargetAlias;
        public IntPtr UserName;
    }

    internal const uint CRED_TYPE_GENERIC = 1;

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    internal static extern bool CredRead(string target, uint type, int reserved, out IntPtr credential);

    [DllImport("advapi32.dll")]
    internal static extern void CredFree(IntPtr cred);

    // ── synthetic keyboard input ──────────────────────────────────────────
    // Layout is x64-specific (PlatformTarget=x64 in .csproj):
    //   offset 0:  type  (4 bytes)
    //   offset 4:  union alignment padding (4 bytes)
    //   offset 8:  wVk, wScan (2+2 bytes)
    //   offset 12: dwFlags, time (4+4 bytes)
    //   offset 20: alignment padding (4 bytes)
    //   offset 24: dwExtraInfo (8 bytes, IntPtr on x64)
    //   total: 40 bytes (Size = max(KEYBDINPUT=24+pad, MOUSEINPUT=32) + 8 header)

    [StructLayout(LayoutKind.Explicit, Size = 40)]
    internal struct INPUT
    {
        [FieldOffset(0)]  public uint   type;
        [FieldOffset(8)]  public ushort wVk;
        [FieldOffset(10)] public ushort wScan;
        [FieldOffset(12)] public uint   dwFlags;
        [FieldOffset(16)] public uint   time;
        [FieldOffset(24)] public IntPtr dwExtraInfo;
    }

    internal const uint INPUT_KEYBOARD    = 1;
    internal const uint KEYEVENTF_KEYUP   = 0x0002;

    [DllImport("user32.dll", SetLastError = true)]
    internal static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
}
