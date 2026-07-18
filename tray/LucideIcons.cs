using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;

namespace EasyUniVPN;

/// <summary>
/// Renders Lucide icons as crisp bitmaps at any pixel size.
///
/// Icon path data is from Lucide (https://lucide.dev), lucide-static v1.25.0,
/// Copyright (c) Lucide Icons and Contributors, ISC License. The SVG sources
/// and full license text live in <c>assets/lucide/</c>; see also
/// THIRD-PARTY-NOTICES.md. The parser/renderer below is EasyUniVPN code.
///
/// Lucide icons are pure stroke drawings on a 24x24 grid (stroke width 2,
/// round caps and joins, no fills). Rendering the vector data directly at the
/// exact target size - instead of scaling a fixed-size PNG - is what keeps
/// the tray icon sharp on every DPI. The parser supports the SVG path
/// commands the bundled icons use (M/m, L/l including implicit repeats,
/// H/h, V/v, C/c, A/a, Z/z) and throws on anything else, so swapping in a
/// future icon that needs more is caught immediately at render time.
/// </summary>
internal static class LucideIcons
{
    // lucide "shield-check"
    internal static readonly string[] ShieldCheck =
    {
        "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",
        "m9 12 2 2 4-4",
    };

    // lucide "shield-off"
    internal static readonly string[] ShieldOff =
    {
        "m2 2 20 20",
        "M5 5a1 1 0 0 0-1 1v7c0 5 3.5 7.5 7.67 8.94a1 1 0 0 0 .67.01c2.35-.82 4.48-1.97 5.9-3.71",
        "M9.309 3.652A12.252 12.252 0 0 0 11.24 2.28a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1v7a9.784 9.784 0 0 1-.08 1.264",
    };

    private const float ViewBox = 24f;
    private const float StrokeWidth = 2f;

    /// <summary>
    /// Renders the given icon (a set of SVG path strings) into a new
    /// <paramref name="sizePx"/> x <paramref name="sizePx"/> ARGB bitmap,
    /// stroked in <paramref name="color"/>. The caller owns the bitmap.
    /// </summary>
    internal static Bitmap Render(string[] pathData, int sizePx, Color color)
    {
        var bmp = new Bitmap(sizePx, sizePx, PixelFormat.Format32bppArgb);
        using var g = Graphics.FromImage(bmp);
        g.SmoothingMode   = SmoothingMode.AntiAlias;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        g.Clear(Color.Transparent);

        // World transform maps the 24x24 icon grid onto the target bitmap;
        // the pen width is transformed along with it, so strokes stay
        // proportional at every size.
        float scale = sizePx / ViewBox;
        g.ScaleTransform(scale, scale);

        using var pen = new Pen(color, StrokeWidth)
        {
            StartCap = LineCap.Round,
            EndCap   = LineCap.Round,
            LineJoin = LineJoin.Round,
        };
        foreach (string d in pathData)
        {
            using var path = ParsePath(d);
            g.DrawPath(pen, path);
        }
        return bmp;
    }

    // ── SVG path parsing ─────────────────────────────────────────────────

    private static GraphicsPath ParsePath(string d)
    {
        var path = new GraphicsPath();
        int i = 0;
        char cmd = '\0';
        PointF cur = PointF.Empty, figureStart = PointF.Empty;

        while (true)
        {
            SkipSeparators(d, ref i);
            if (i >= d.Length)
                break;

            if (char.IsLetter(d[i]))
            {
                cmd = d[i];
                i++;
            }
            else
            {
                // Numbers with no preceding letter repeat the previous
                // command; a repeated moveto becomes an implicit lineto.
                if (cmd == 'M') cmd = 'L';
                else if (cmd == 'm') cmd = 'l';
            }

            bool rel = char.IsLower(cmd);
            switch (char.ToUpperInvariant(cmd))
            {
                case 'M':
                {
                    PointF p = ReadPoint(d, ref i, rel, cur);
                    path.StartFigure();
                    cur = p;
                    figureStart = p;
                    break;
                }
                case 'L':
                {
                    PointF p = ReadPoint(d, ref i, rel, cur);
                    path.AddLine(cur, p);
                    cur = p;
                    break;
                }
                case 'H':
                {
                    float x = ReadNumber(d, ref i) + (rel ? cur.X : 0f);
                    var p = new PointF(x, cur.Y);
                    path.AddLine(cur, p);
                    cur = p;
                    break;
                }
                case 'V':
                {
                    float y = ReadNumber(d, ref i) + (rel ? cur.Y : 0f);
                    var p = new PointF(cur.X, y);
                    path.AddLine(cur, p);
                    cur = p;
                    break;
                }
                case 'C':
                {
                    PointF c1 = ReadPoint(d, ref i, rel, cur);
                    PointF c2 = ReadPoint(d, ref i, rel, cur);
                    PointF p  = ReadPoint(d, ref i, rel, cur);
                    path.AddBezier(cur, c1, c2, p);
                    cur = p;
                    break;
                }
                case 'A':
                {
                    float rx    = ReadNumber(d, ref i);
                    float ry    = ReadNumber(d, ref i);
                    float rot   = ReadNumber(d, ref i);
                    bool  large = ReadNumber(d, ref i) != 0f;
                    bool  sweep = ReadNumber(d, ref i) != 0f;
                    PointF p    = ReadPoint(d, ref i, rel, cur);
                    AddArc(path, cur, p, rx, ry, rot, large, sweep);
                    cur = p;
                    break;
                }
                case 'Z':
                {
                    path.CloseFigure();
                    cur = figureStart;
                    break;
                }
                default:
                    throw new FormatException($"Unsupported SVG path command '{cmd}' in Lucide icon data.");
            }
        }
        return path;
    }

    private static void SkipSeparators(string d, ref int i)
    {
        while (i < d.Length && (d[i] == ' ' || d[i] == ',' || d[i] == '\t' || d[i] == '\n' || d[i] == '\r'))
            i++;
    }

    // Reads one float. Handles SVG's compact forms ("13c0", "1-.67-.01"):
    // a sign or a second decimal point terminates the previous number.
    private static float ReadNumber(string d, ref int i)
    {
        SkipSeparators(d, ref i);
        int start = i;
        if (i < d.Length && (d[i] == '+' || d[i] == '-'))
            i++;
        bool seenDot = false;
        while (i < d.Length && (char.IsDigit(d[i]) || (d[i] == '.' && !seenDot)))
        {
            if (d[i] == '.')
                seenDot = true;
            i++;
        }
        if (i == start)
            throw new FormatException($"Expected a number at position {i} in SVG path data.");
        return float.Parse(d.Substring(start, i - start), NumberStyles.Float, CultureInfo.InvariantCulture);
    }

    private static PointF ReadPoint(string d, ref int i, bool relative, PointF cur)
    {
        float x = ReadNumber(d, ref i);
        float y = ReadNumber(d, ref i);
        return relative ? new PointF(cur.X + x, cur.Y + y) : new PointF(x, y);
    }

    // ── elliptical arc → cubic beziers ───────────────────────────────────
    // Endpoint-to-center conversion per SVG 1.1 appendix F.6.5, then each
    // arc slice of at most 90 degrees is approximated by one cubic bezier
    // (control-point factor 4/3 * tan(delta/4)) - the standard approach,
    // accurate to well under half a pixel at tray icon sizes.

    private static void AddArc(GraphicsPath path, PointF from, PointF to,
                               float rx, float ry, float rotationDeg, bool largeArc, bool sweep)
    {
        if (rx == 0f || ry == 0f)
        {
            path.AddLine(from, to);
            return;
        }
        double phi  = rotationDeg * Math.PI / 180.0;
        double cosP = Math.Cos(phi), sinP = Math.Sin(phi);

        double dx2 = (from.X - to.X) / 2.0, dy2 = (from.Y - to.Y) / 2.0;
        double x1p =  cosP * dx2 + sinP * dy2;
        double y1p = -sinP * dx2 + cosP * dy2;

        double rxd = Math.Abs(rx), ryd = Math.Abs(ry);
        double lambda = (x1p * x1p) / (rxd * rxd) + (y1p * y1p) / (ryd * ryd);
        if (lambda > 1)
        {
            double s = Math.Sqrt(lambda);
            rxd *= s;
            ryd *= s;
        }

        double sign = (largeArc != sweep) ? 1.0 : -1.0;
        double num  = rxd * rxd * ryd * ryd - rxd * rxd * y1p * y1p - ryd * ryd * x1p * x1p;
        double den  = rxd * rxd * y1p * y1p + ryd * ryd * x1p * x1p;
        double co   = den == 0 ? 0 : sign * Math.Sqrt(Math.Max(0, num / den));
        double cxp  = co * rxd * y1p / ryd;
        double cyp  = co * -ryd * x1p / rxd;

        double cx = cosP * cxp - sinP * cyp + (from.X + to.X) / 2.0;
        double cy = sinP * cxp + cosP * cyp + (from.Y + to.Y) / 2.0;

        double theta1 = VectorAngle(1, 0, (x1p - cxp) / rxd, (y1p - cyp) / ryd);
        double delta  = VectorAngle((x1p - cxp) / rxd, (y1p - cyp) / ryd,
                                    (-x1p - cxp) / rxd, (-y1p - cyp) / ryd);
        if (!sweep && delta > 0) delta -= 2 * Math.PI;
        if (sweep && delta < 0)  delta += 2 * Math.PI;

        int segments = (int)Math.Ceiling(Math.Abs(delta) / (Math.PI / 2.0));
        double step = delta / segments;
        double t = theta1;
        PointF p0 = from;
        for (int s = 0; s < segments; s++)
        {
            double t2 = t + step;
            double alpha = 4.0 / 3.0 * Math.Tan(step / 4.0);

            PointF p3 = ArcPoint(cx, cy, rxd, ryd, cosP, sinP, t2);
            PointF d0 = ArcDerivative(rxd, ryd, cosP, sinP, t);
            PointF d3 = ArcDerivative(rxd, ryd, cosP, sinP, t2);
            var c1 = new PointF((float)(p0.X + alpha * d0.X), (float)(p0.Y + alpha * d0.Y));
            var c2 = new PointF((float)(p3.X - alpha * d3.X), (float)(p3.Y - alpha * d3.Y));

            path.AddBezier(p0, c1, c2, p3);
            p0 = p3;
            t = t2;
        }
    }

    private static PointF ArcPoint(double cx, double cy, double rx, double ry,
                                   double cosP, double sinP, double theta)
    {
        double x = rx * Math.Cos(theta), y = ry * Math.Sin(theta);
        return new PointF((float)(cx + cosP * x - sinP * y),
                          (float)(cy + sinP * x + cosP * y));
    }

    private static PointF ArcDerivative(double rx, double ry,
                                        double cosP, double sinP, double theta)
    {
        double x = -rx * Math.Sin(theta), y = ry * Math.Cos(theta);
        return new PointF((float)(cosP * x - sinP * y),
                          (float)(sinP * x + cosP * y));
    }

    private static double VectorAngle(double ux, double uy, double vx, double vy)
    {
        double dot = ux * vx + uy * vy;
        double len = Math.Sqrt((ux * ux + uy * uy) * (vx * vx + vy * vy));
        double ang = Math.Acos(Math.Max(-1, Math.Min(1, dot / len)));
        if (ux * vy - uy * vx < 0)
            ang = -ang;
        return ang;
    }
}
