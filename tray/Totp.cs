using System.Security.Cryptography;

namespace EasyUniVPN;

/// <summary>
/// RFC 6238 Time-based One-Time Password (TOTP) implementation.
///
/// Parameterized by algorithm, period, and digit count because the supported
/// universities differ: University of Graz issues SHA-1 secrets with a
/// 30-second period, TU Graz (privacyIDEA) issues SHA-256 secrets with a
/// 60-second period. The parameters come from config.json per university.
/// </summary>
internal static class Totp
{
    /// <summary>
    /// Computes the current TOTP code for the given Base32-encoded secret.
    /// Returns <c>null</c> if the secret is invalid or computation fails.
    /// </summary>
    internal static string? Compute(string base32Secret, string algorithm, int periodSeconds, int digits)
    {
        try
        {
            byte[] key     = Base32Decode(base32Secret);
            // ServerClock corrects for a wrong local system clock, so the code
            // matches the window the university's server is actually in.
            long   counter = ServerClock.UtcNow.ToUnixTimeSeconds() / periodSeconds;

            // Counter as 8-byte big-endian (RFC 4226 §5.2)
            var cb = BitConverter.GetBytes(counter);
            if (BitConverter.IsLittleEndian) Array.Reverse(cb);

            using HMAC hmac = CreateHmac(algorithm, key);
            byte[] h = hmac.ComputeHash(cb);

            // Dynamic truncation (RFC 4226 §5.3); the offset comes from the
            // last hash byte, whatever the hash length is.
            int off  = h[h.Length - 1] & 0x0F;
            int code = ((h[off]     & 0x7F) << 24)
                     | ((h[off + 1] & 0xFF) << 16)
                     | ((h[off + 2] & 0xFF) <<  8)
                     |  (h[off + 3] & 0xFF);

            int modulus = 1;
            for (int i = 0; i < digits; i++) modulus *= 10;
            return (code % modulus).ToString("D" + digits);
        }
        catch { return null; }
    }

    /// <summary>Returns remaining seconds in the current TOTP window.</summary>
    internal static int SecondsRemaining(int periodSeconds)
        => periodSeconds - (int)(ServerClock.UtcNow.ToUnixTimeSeconds() % periodSeconds);

    private static HMAC CreateHmac(string algorithm, byte[] key) => algorithm switch
    {
        "sha256" => new HMACSHA256(key),
        "sha512" => new HMACSHA512(key),
        _        => new HMACSHA1(key),
    };

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
