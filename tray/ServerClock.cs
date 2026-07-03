using System.Globalization;
using System.Net;

namespace EasyUniVPN;

/// <summary>
/// Tracks the offset between the university login server's clock and the
/// local system clock, so TOTP codes can be generated from server time.
///
/// TOTP is a function of wall-clock time: if the local clock is off by more
/// than the server's tolerance (Keycloak accepts roughly +/-30 seconds),
/// every generated code is rejected even though the secret is correct.
/// Instead of trusting the local clock, this reads the HTTP Date header
/// (1-second resolution) from a HEAD request to the login server and applies
/// the measured offset - the same approach as Google Authenticator's
/// "time correction for codes".
///
/// Syncing is always off the caller's thread and best-effort: with no
/// network or a failed probe, the offset stays at its last known value
/// (initially zero, i.e. plain local time). Callers are never blocked.
/// </summary>
internal static class ServerClock
{
    // The Keycloak host that actually validates TOTP codes - agreement with
    // this clock is what matters, not "true" time.
    private const string ProbeUrl = "https://login.uni-graz.at/";
    private static readonly TimeSpan MaxAge = TimeSpan.FromHours(6);

    private static readonly object _lock = new();
    private static TimeSpan _offset = TimeSpan.Zero;
    private static DateTimeOffset _lastSync = DateTimeOffset.MinValue;
    private static int _syncing; // 0=idle, 1=background sync in flight

    /// <summary>
    /// Current UTC time corrected by the last measured server offset.
    /// Also schedules a background re-sync when the measurement is stale.
    /// </summary>
    internal static DateTimeOffset UtcNow
    {
        get
        {
            SyncInBackgroundIfStale();
            lock (_lock) return DateTimeOffset.UtcNow + _offset;
        }
    }

    /// <summary>Kick off the first background sync (called at tray startup).</summary>
    internal static void WarmUp() => SyncInBackgroundIfStale();

    private static void SyncInBackgroundIfStale()
    {
        bool stale;
        lock (_lock) stale = DateTimeOffset.UtcNow - _lastSync > MaxAge;
        if (!stale) return;
        if (Interlocked.CompareExchange(ref _syncing, 1, 0) != 0) return;
        _ = Task.Run(() =>
        {
            try { Sync(); }
            finally { Interlocked.Exchange(ref _syncing, 0); }
        });
    }

    private static void Sync()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(ProbeUrl);
            req.Method = "HEAD";
            req.Timeout = 5_000;
            // A redirect response is fine - its Date header is just as good.
            req.AllowAutoRedirect = false;
            using var resp = (HttpWebResponse)req.GetResponse();
            ApplyDateHeader(resp.Headers["Date"]);
        }
        catch (WebException ex)
        {
            // 4xx/5xx still carry a valid Date header - use it if present.
            if (ex.Response is HttpWebResponse resp)
                using (resp) ApplyDateHeader(resp.Headers["Date"]);
            else
                TrayLog.Warn($"[CLOCK] sync failed: {ex.Message}");
        }
        catch (Exception ex)
        {
            TrayLog.Warn($"[CLOCK] sync failed: {ex.Message}");
        }
    }

    private static void ApplyDateHeader(string? dateHeader)
    {
        if (string.IsNullOrEmpty(dateHeader))
            return;
        if (!DateTimeOffset.TryParse(dateHeader, CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var serverNow))
            return;

        var offset = serverNow - DateTimeOffset.UtcNow;
        lock (_lock)
        {
            _offset = offset;
            _lastSync = DateTimeOffset.UtcNow;
        }

        if (Math.Abs(offset.TotalSeconds) > 20)
            TrayLog.Warn($"[CLOCK] system clock is {offset.TotalSeconds:F0}s off from the " +
                         "university server - compensating when generating TOTP codes");
        else
            TrayLog.Info($"[CLOCK] synced with login server (offset {offset.TotalSeconds:F1}s)");
    }
}
