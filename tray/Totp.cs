using System.Security.Cryptography;

namespace EasyUniVPN;

/// <summary>
/// RFC 6238 Time-based One-Time Password (TOTP) implementation.
/// Uses HMAC-SHA1 with a 30-second step and 6-digit output - the standard
/// algorithm used by authenticator apps (Google Authenticator, Aegis, etc.).
/// </summary>
internal static class Totp
{
    /// <summary>
    /// Computes the current 6-digit TOTP for the given Base32-encoded secret.
    /// Returns <c>null</c> if the secret is invalid or computation fails.
    /// </summary>
    internal static string? Compute(string base32Secret)
    {
        try
        {
            byte[] key     = Base32Decode(base32Secret);
            // ServerClock corrects for a wrong local system clock, so the code
            // matches the window the university's Keycloak is actually in.
            long   counter = ServerClock.UtcNow.ToUnixTimeSeconds() / 30;

            // Counter as 8-byte big-endian (RFC 4226 §5.2)
            var cb = BitConverter.GetBytes(counter);
            if (BitConverter.IsLittleEndian) Array.Reverse(cb);

            using var hmac = new HMACSHA1(key);
            byte[] h = hmac.ComputeHash(cb);

            // Dynamic truncation (RFC 4226 §5.3)
            int off  = h[19] & 0x0F;
            int code = ((h[off]     & 0x7F) << 24)
                     | ((h[off + 1] & 0xFF) << 16)
                     | ((h[off + 2] & 0xFF) <<  8)
                     |  (h[off + 3] & 0xFF);

            return (code % 1_000_000).ToString("D6");
        }
        catch { return null; }
    }

    /// <summary>Returns remaining seconds in the current 30-second TOTP window.</summary>
    internal static int SecondsRemaining()
        => 30 - (int)(ServerClock.UtcNow.ToUnixTimeSeconds() % 30);

    private static byte[] Base32Decode(string s)
    {
        const string alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
        s = s.ToUpperInvariant().TrimEnd('=').Replace(" ", "");
        var result = new byte[s.Length * 5 / 8];
        int buf = 0, bitsLeft = 0, idx = 0;
        foreach (char c in s)
        {
            int v = alpha.IndexOf(c);
            if (v < 0) throw new ArgumentException($"Invalid Base32 character '{c}'");
            buf = (buf << 5) | v;
            bitsLeft += 5;
            if (bitsLeft >= 8)
                result[idx++] = (byte)(buf >> (bitsLeft -= 8));
        }
        return result;
    }
}
