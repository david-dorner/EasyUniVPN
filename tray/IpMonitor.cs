namespace EasyUniVPN;

/// <summary>
/// Event-driven IP-change notifications via <c>NotifyUnicastIpAddressChange</c>,
/// the callback-based replacement for a blocking <c>NotifyAddrChange</c> wait.
///
/// No thread is parked: registration happens once, and the OS invokes the
/// callback on a system worker thread whenever a unicast address is added or
/// removed anywhere (VPN tunnel up/down, DHCP renew, adapter enable/disable).
///
/// Adapter changes produce a burst of notifications (one per address per
/// family), so the callback coalesces them: the subscriber's action runs once,
/// shortly after the first notification of a burst, on a thread-pool thread -
/// never on the OS callback thread, which must return quickly.
/// </summary>
internal sealed class IpMonitor : IDisposable
{
    private const ushort AF_UNSPEC = 0;         // both IPv4 and IPv6
    private const int    COALESCE_DELAY_MS = 400;

    private readonly Action _onChanged;
    private readonly NativeMethods.IpAddressChangeCallback _callback; // GC anchor - see below
    private IntPtr _handle;
    private int _pending; // 0=idle, 1=a coalesced change-check is scheduled

    internal IpMonitor(Action onChanged)
    {
        _onChanged = onChanged;
        // The delegate must stay referenced for the registration's lifetime -
        // the native side only holds a function pointer, and a collected
        // delegate means an unrecoverable crash on the next notification.
        _callback = OnNativeNotification;

        uint err = NativeMethods.NotifyUnicastIpAddressChange(
            AF_UNSPEC, _callback, IntPtr.Zero,
            initialNotification: false, ref _handle);
        if (err != 0)
        {
            _handle = IntPtr.Zero;
            TrayLog.Warn($"NotifyUnicastIpAddressChange failed (error {err}) - " +
                         "VPN state will only refresh on user actions");
        }
        else
        {
            TrayLog.Info("IP change notifications registered");
        }
    }

    internal bool IsRegistered => _handle != IntPtr.Zero;

    private void OnNativeNotification(IntPtr callerContext, IntPtr row, int notificationType)
    {
        // First notification of a burst schedules the check; the rest are
        // dropped. The flag is released before invoking the action, so a
        // change arriving while the action runs schedules a fresh check
        // rather than being lost.
        if (Interlocked.CompareExchange(ref _pending, 1, 0) != 0)
            return;
        _ = Task.Run(async () =>
        {
            try
            {
                await Task.Delay(COALESCE_DELAY_MS);
                Interlocked.Exchange(ref _pending, 0);
                _onChanged();
            }
            catch (Exception ex)
            {
                Interlocked.Exchange(ref _pending, 0);
                TrayLog.Warn($"IP-change handler: {ex.Message}");
            }
        });
    }

    public void Dispose()
    {
        if (_handle != IntPtr.Zero)
        {
            NativeMethods.CancelMibChangeNotify2(_handle);
            _handle = IntPtr.Zero;
        }
    }
}
