namespace EasyUniVPN;

/// <summary>
/// Wraps <c>NotifyAddrChange</c> (iphlpapi.dll) as an async wait.
///
/// Mirrors <c>tray/app.py::_wait_addr_change()</c> and <c>monitor()</c>.
/// The Windows API blocks until any IP address on any adapter changes -
/// this gives us an event-driven VPN state monitor with zero polling.
/// </summary>
internal static class IpMonitor
{
    /// <summary>
    /// Awaits the next IP address change on the system.
    /// Returns <c>true</c> when a change occurred, <c>false</c> if cancelled.
    /// </summary>
    internal static Task<bool> WaitForChangeAsync(CancellationToken ct) =>
        Task.Run(() =>
        {
            if (ct.IsCancellationRequested) return false;
            // NotifyAddrChange blocks synchronously - offload to thread pool.
            uint result = NativeMethods.NotifyAddrChange(out _, IntPtr.Zero);
            // result == 0 → NO_ERROR (change happened)
            // Any non-zero value (e.g. ERROR_OPERATION_ABORTED) → cancellation/error.
            return result == 0;
        }, ct);
}
