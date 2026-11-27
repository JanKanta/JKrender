// A stand-in for Nuke in JKrender's tests: speaks the jkrender_worker protocol and writes PNG frames, so a render runs
// through JKrender as with Nuke, in seconds and without a licence. run_tests.py builds it with the .NET Framework compiler.
//
// It reads what the worker gets: -m threads, -c cache, JKRENDER_ARGS (script, original, Write names), JKRENDER_TEST_OUT.
// Environment, for the tests:
//   FAKE_OUT          output pattern with %04d (else <script folder>/render/<script name>.%04d.png)
//   FAKE_SECS         seconds a frame takes (default 0.3)
//   FAKE_LOAD_SECS    seconds opening the script takes (default 0.5)
//   FAKE_CRASH_FRAME  the process exits without a word when it starts this frame, the first time only (a crash mid-chunk)
//   FAKE_FAIL_FRAMES  frames (1005,1007) that throw every time: JKR FAIL, as the worker after its three tries
//   FAKE_FLAKY_FRAMES frames that throw the first time only
//   FAKE_BEFORE_SECS  beforeRender hooks taking that long before the process's first frame: JKR BEFORE 1 <secs>
//   FAKE_FRAME_HOOKS  1: the Write's per-frame hooks, JKR FRAMEHOOKS <frame> beforeFrameRender=0.010 afterFrameRender=0.020
//   FAKE_HOOKS        afterRender hooks the script holds (JKR HOOKS; 0 in Test this script, as the worker). AFTER adds a
//                     line "after <pid> <frames on disk>" to .fake_after.log next to the frames, then says AFTERDONE
//   FAKE_AFTER_SECS   seconds AFTER takes (default 0.2)
//   FAKE_AFTER_FAIL   that many of the hooks fail
//   FAKE_AFTER_CRASH  1: the process exits in the middle of AFTER
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Threading;

class FakeNuke
{
    static void Say(string s) { Console.Out.Write("JKR " + s + "\n"); Console.Out.Flush(); }
    static volatile bool burn;
    static byte[] ram;
    static Random rnd = new Random(Process.GetCurrentProcess().Id);

    static double Env(string name, double fallback)
    {
        string v = Environment.GetEnvironmentVariable(name);
        double d;
        return v != null && double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out d) ? d : fallback;
    }

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
        int w = 640, h = 360;
        using (var bmp = new Bitmap(w, h))
        using (var g = Graphics.FromImage(bmp))
        {
            double t = (frame % 100) / 100.0;
            using (var sky = new LinearGradientBrush(new Rectangle(0, 0, w, h), Color.FromArgb(20, 30, 60),
                                                     Color.FromArgb(200, (int)(80 + t * 150), 60), 90f))
                g.FillRectangle(sky, 0, 0, w, h);
            using (var f = new Font("Segoe UI", 28, FontStyle.Bold))
            using (var b = new SolidBrush(Color.White))
                g.DrawString("frame " + frame, f, b, 30, 30);
            for (int i = 0; i < 3000; i++)  // grain: no two frames alike, sizes like a real sequence
                bmp.SetPixel(rnd.Next(w), rnd.Next(h), Color.FromArgb(rnd.Next(80), rnd.Next(80), rnd.Next(80)));
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            string tmp = path + ".tmp";  // as Nuke: a temp file, renamed when whole
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
        string pattern = Environment.GetEnvironmentVariable("FAKE_OUT") ?? "";
        if (pattern.Length == 0)
        {
            var jargs = Environment.GetEnvironmentVariable("JKRENDER_ARGS") ?? "";
            var mm = System.Text.RegularExpressions.Regex.Match(jargs, @"\[\s*""([^""]+)""");
            if (mm.Success)
            {
                string script = System.Text.RegularExpressions.Regex.Unescape(mm.Groups[1].Value).Replace("\\", "/");
                pattern = Path.GetDirectoryName(script).Replace("\\", "/") + "/render/" + Path.GetFileNameWithoutExtension(script) + ".%04d.png";
            }
        }
        string testOut = Environment.GetEnvironmentVariable("JKRENDER_TEST_OUT");
        if (!string.IsNullOrEmpty(testOut) && pattern.Length > 0) pattern = testOut + "/" + Path.GetFileName(pattern);
        double secs = Env("FAKE_SECS", 0.3), load = Env("FAKE_LOAD_SECS", 0.5);
        int crashAt = (int)Env("FAKE_CRASH_FRAME", -1);
        var failAt = new HashSet<int>();
        foreach (var x in (Environment.GetEnvironmentVariable("FAKE_FAIL_FRAMES") ?? "").Split(','))
            if (x.Trim().Length > 0) failAt.Add(int.Parse(x.Trim()));
        var flaky = new HashSet<int>();
        foreach (var x in (Environment.GetEnvironmentVariable("FAKE_FLAKY_FRAMES") ?? "").Split(','))
            if (x.Trim().Length > 0) flaky.Add(int.Parse(x.Trim()));
        int burners = Math.Max(1, Math.Min(threads / 3, 4));
        for (int i = 0; i < burners; i++) { var th = new Thread(Burner); th.IsBackground = true; th.Start(); }
        Console.Out.Write("Nuke 16.0v9 (stand-in for tests)\n");
        Say("BOOT");
        Thread.Sleep((int)(load * 1000));
        ram = new byte[(200 + rnd.Next(100)) * 1024L * 1024L];
        for (long i = 0; i < ram.Length; i += 4096) ram[i] = 1;
        Say("READY " + load.ToString("0.00", CultureInfo.InvariantCulture));
        int hooks = string.IsNullOrEmpty(testOut) ? (int)Env("FAKE_HOOKS", 0) : 0;
        Say("HOOKS " + hooks);
        double beforeSecs = Env("FAKE_BEFORE_SECS", 0);
        bool beforeDone = false, frameHooks = Env("FAKE_FRAME_HOOKS", 0) > 0;
        string line;
        while ((line = Console.In.ReadLine()) != null)
        {
            var c = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            if (c.Length == 0) continue;
            // a movie: one execute over the whole range, frame by frame like a chunk here (it never rendered before 1.0.11)
            if (c[0] == "RANGE" && c.Length >= 4) c = new[] { "RENDER", c[1], c[2] + "-" + c[3] };
            if (c[0] == "QUIT") break;
            if (c[0] == "AFTER")
            {
                string dir = pattern.Length > 0 ? Path.GetDirectoryName(pattern) : ".";
                int onDisk = Directory.Exists(dir) ? Directory.GetFiles(dir, "*.png").Length : 0;
                File.AppendAllText(Path.Combine(dir, ".fake_after.log"), "after " + Process.GetCurrentProcess().Id + " " + onDisk + "\n");
                Thread.Sleep((int)(Env("FAKE_AFTER_SECS", 0.2) * 1000));
                if (Env("FAKE_AFTER_CRASH", 0) > 0)
                {
                    Console.Out.Write("Segmentation fault (stand-in)\n");
                    Console.Out.Flush();
                    Environment.Exit(3);
                }
                int failed = Math.Min(hooks, (int)Env("FAKE_AFTER_FAIL", 0));
                if (failed > 0) Console.Out.Write("Traceback (most recent call last):\nRuntimeError: stand-in hook failed\n");
                Say("AFTERDONE " + (hooks - failed) + " " + failed + (failed > 0 ? " publish: stand-in hook failed" : " "));
                continue;
            }
            if (c[0] == "RENDER")
            {
                bool failed = false;
                if (beforeSecs > 0 && !beforeDone)
                {
                    Thread.Sleep((int)(beforeSecs * 1000));
                    Say("BEFORE 1 " + beforeSecs.ToString("0.00", CultureInfo.InvariantCulture));
                    beforeDone = true;
                }
                foreach (int f in Frames(c[2]))
                {
                    Say("START " + f);
                    bool failNow = failAt.Contains(f);
                    if (flaky.Contains(f) && pattern.Length > 0)
                    {
                        string fmark = Path.Combine(Path.GetDirectoryName(pattern), ".fake_flaky_" + f);
                        failNow = !File.Exists(fmark);
                        if (failNow) File.WriteAllText(fmark, "");
                    }
                    if (failNow)
                    {
                        Say("FAIL " + c[1] + " " + f + " stand-in error on frame " + f);
                        failed = true;
                        break;
                    }
                    if (f == crashAt && pattern.Length > 0)
                    {
                        string mark = Path.Combine(Path.GetDirectoryName(pattern), ".fake_crashed_" + f);
                        if (!File.Exists(mark))
                        {
                            File.WriteAllText(mark, "");
                            Console.Out.Write("Segmentation fault (stand-in)\n");
                            Console.Out.Flush();
                            Environment.Exit(3);
                        }
                    }
                    var sw = Stopwatch.StartNew();
                    burn = true;
                    double want = secs * (0.8 + rnd.NextDouble() * 0.4);
                    if (pattern.Length > 0) Draw(pattern.Replace("%04d", f.ToString("0000")), f);
                    while (sw.Elapsed.TotalSeconds < want) Thread.Sleep(20);
                    burn = false;
                    Say("FRAME " + f + " " + sw.Elapsed.TotalSeconds.ToString("0.000", CultureInfo.InvariantCulture));
                    if (frameHooks) Say("FRAMEHOOKS " + f + " beforeFrameRender=0.010 afterFrameRender=0.020");
                    long used = (long)(cache * 1024L * 1024L * 1024L * (0.3 + rnd.NextDouble() * 0.4));
                    Say("MEM " + used + " " + cache * 1024L * 1024L * 1024L);
                }
                if (!failed) Say("CHUNK " + c[1]);
            }
        }
        Say("BYE");
        return 0;
    }
}
