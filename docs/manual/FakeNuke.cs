// A stand-in for Nuke for the manual's screenshots: speaks the jkrender_worker protocol, draws PNG frames.
// Output pattern from env FAKE_OUT (with %04d), seconds per frame from FAKE_SECS.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Threading;

class FakeNuke
{
    static void Say(string s) { Console.Out.Write("JKR " + s + "\n"); Console.Out.Flush(); }
    static volatile bool burn;
    static byte[] ram;
    static Random rnd = new Random(Process.GetCurrentProcess().Id);

    static void Burner()
    {
        double x = 0;
        while (true)
        {
            if (burn) { for (int i = 0; i < 200000; i++) x += Math.Sqrt(i); }
            else Thread.Sleep(20);
        }
    }

    static List<int> Frames(string spec)
    {
        var r = new List<int>();
        foreach (var p in spec.Split(','))
        {
            if (p.Length == 0) continue;
            int i = p.IndexOf('-', 1);
            if (i > 0) { int a = int.Parse(p.Substring(0, i)), b = int.Parse(p.Substring(i + 1)); for (int f = a; f <= b; f++) r.Add(f); }
            else r.Add(int.Parse(p));
        }
        return r;
    }

    static void Draw(string path, int frame)
    {
        int w = 1920, h = 1080;
        using (var bmp = new Bitmap(w, h))
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            double t = (frame % 1000) / 100.0;
            using (var sky = new LinearGradientBrush(new Rectangle(0, 0, w, h), Color.FromArgb(18, 24, 48), Color.FromArgb(222, 120, 60), 90f))
                g.FillRectangle(sky, 0, 0, w, h);
            int sx = (int)(300 + t * 1300), sy = (int)(760 - Math.Sin(t / 1.2 * Math.PI / 2) * 420);
            for (int r = 260; r > 0; r -= 20)
                using (var glow = new SolidBrush(Color.FromArgb(Math.Min(255, 14 + (260 - r) / 3), 255, 214, 140)))
                    g.FillEllipse(glow, sx - r, sy - r, 2 * r, 2 * r);
            using (var sun = new SolidBrush(Color.FromArgb(255, 250, 230, 170))) g.FillEllipse(sun, sx - 70, sy - 70, 140, 140);
            var hills = new[] { Color.FromArgb(48, 30, 52), Color.FromArgb(30, 20, 36), Color.FromArgb(16, 12, 22) };
            for (int k = 0; k < 3; k++)
            {
                var pts = new List<PointF> { new PointF(0, h) };
                for (int x = 0; x <= w; x += 40)
                    pts.Add(new PointF(x, (float)(700 + k * 110 + Math.Sin(x / (300.0 - k * 60) + k * 2 + t * (k + 1) * 0.3) * (70 - k * 15))));
                pts.Add(new PointF(w, h));
                using (var b = new SolidBrush(hills[k])) g.FillPolygon(b, pts.ToArray());
            }
            using (var f = new Font("Segoe UI", 34, FontStyle.Bold))
            using (var b = new SolidBrush(Color.FromArgb(200, 255, 255, 255)))
                g.DrawString("sh010  comp  " + frame, f, b, 60, 50);
            for (int i = 0; i < 4000; i++)  // grain, so the PNG sizes look like real frames
                bmp.SetPixel(rnd.Next(w), rnd.Next(h), Color.FromArgb(rnd.Next(60), rnd.Next(60), rnd.Next(80)));
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string tmp = path + ".tmp";
            bmp.Save(tmp, ImageFormat.Png);
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }
    }

    static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        int threads = 4; long cache = 8;
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "-m") int.TryParse(args[i + 1], out threads);
            if (args[i] == "-c") long.TryParse(args[i + 1].TrimEnd('G', 'M'), out cache);
        }
        string pattern = "";
        var jargs = Environment.GetEnvironmentVariable("JKRENDER_ARGS") ?? "";
        var mm = System.Text.RegularExpressions.Regex.Match(jargs, @"\[\s*""([^""]+)""");
        if (mm.Success)
        {
            string script = mm.Groups[1].Value.Replace("\\\\", "/");
            pattern = Path.GetDirectoryName(script).Replace("\\", "/") + "/render/" + Path.GetFileNameWithoutExtension(script) + ".%04d.png";
        }
        string testOut = Environment.GetEnvironmentVariable("JKRENDER_TEST_OUT");
        if (!string.IsNullOrEmpty(testOut)) pattern = testOut + "/" + Path.GetFileName(pattern);
        double secs = double.Parse(Environment.GetEnvironmentVariable("FAKE_SECS") ?? "4", System.Globalization.CultureInfo.InvariantCulture);
        int burners = Math.Max(1, Math.Min(threads / 3, 6));
        for (int i = 0; i < burners; i++) { var th = new Thread(Burner); th.IsBackground = true; th.Start(); }
        Console.Out.Write("Nuke 16.0v9 (stand-in)\n");
        Thread.Sleep(1500 + rnd.Next(1500));
        Say("BOOT");
        Thread.Sleep(1200 + rnd.Next(1800));
        ram = new byte[(1100 + rnd.Next(500)) * 1024L * 1024L];
        for (long i = 0; i < ram.Length; i += 4096) ram[i] = 1;
        Say("READY 1.84");
        if (Environment.GetEnvironmentVariable("JKRENDER_ANALYZE") != null) { Say("INFO " + Info()); return 0; }
        string line;
        while ((line = Console.In.ReadLine()) != null)
        {
            var c = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            if (c.Length == 0) continue;
            if (c[0] == "QUIT") break;
            if (c[0] == "ANALYZE") Say("INFO " + Info());
            if (c[0] == "PROFILE") Say("INFO " + Profile());
            if (c[0] == "RENDER")
            {
                foreach (int f in Frames(c[2]))
                {
                    Say("START " + f);
                    var sw = Stopwatch.StartNew();
                    burn = true;
                    double want = secs * (0.8 + rnd.NextDouble() * 0.4);
                    if (pattern.Length > 0) Draw(pattern.Replace("%04d", f.ToString("0000")), f);
                    while (sw.Elapsed.TotalSeconds < want) Thread.Sleep(50);
                    burn = false;
                    Say("FRAME " + f + " " + sw.Elapsed.TotalSeconds.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture));
                    long used = (long)(cache * 1024L * 1024L * 1024L * (0.3 + rnd.NextDouble() * 0.4));
                    Say("MEM " + used + " " + cache * 1024L * 1024L * 1024L);
                }
                Say("CHUNK " + c[1]);
            }
        }
        Say("BYE");
        return 0;
    }

    static string Info()
    {
        return "{\"all\": 214, \"above\": 96, \"reads\": 6, \"largest_read\": 8847360, \"big_blurs\": 1, \"gpu\": {\"ZDefocus2\": 1}, " +
               "\"nodes\": [], \"temporal\": {\"TimeOffset\": 2}, \"top\": [[\"Merge2\", 22], [\"Grade\", 14], [\"Transform\", 9], [\"Blur\", 7], " +
               "[\"Roto\", 6], [\"Read\", 6], [\"ColorCorrect\", 5], [\"ZDefocus2\", 1]]}";
    }

    static string Profile()
    {
        return "{\"nodes\": [[\"ZDefocus1\", \"ZDefocus2\", 2210000, 120, true], [\"Blur_glow\", \"Blur\", 940000, 480, false], " +
               "[\"Merge_bg\", \"Merge2\", 410000, 960, false], [\"Roto_mask\", \"Roto\", 300000, 240, false], [\"Grade_key\", \"Grade\", 120000, 960, false]]}";
    }
}
