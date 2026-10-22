/*
 * Kernel.cs: kernel.py in C#. .NET 8, the base class library only.
 *
 *   dotnet build Kernel.csproj -c Release -o out
 *   dotnet out/Kernel.dll init    --box DIR --pile FOLDER --key FILE
 *   dotnet out/Kernel.dll bite    --box DIR "your words"
 *   dotnet out/Kernel.dll propose --box DIR --file ROWS.jsonl
 *   dotnet out/Kernel.dll seal    --box DIR proposal:<sha256>
 *   dotnet out/Kernel.dll verify  --box DIR
 *   dotnet out/Kernel.dll out     --box DIR
 *   dotnet out/Kernel.dll vectors CASES.json RESULTS.json
 *
 * The third port, made from two sources: Java's shape (../java/Kernel.java)
 * and TypeScript's scanners (../ts/kernel.ts). Held to ../cases.json by
 * ../compare.py, byte for byte and both ways.
 *
 * The kernel's four patterns are scanners here, not .NET regular
 * expressions: .NET matches UTF-16 units and can't put an astral code point
 * in a character class, which the tables need. Python's decisions come from
 * ../kernel_tables.json (gen.py reads them out of Python); it is found at
 * $KERNEL_TABLES, else two folders above the built Kernel.dll.
 *
 * On Linux and macOS it takes libc's flock on the record, the lock kernel.py
 * takes, so this port and kernel.py keep out of each other's way when they
 * write one box. (.NET's own lock emulation is switched off in Kernel.csproj:
 * it takes a shared flock on every read, which kernel.py's writer refuses.)
 *
 * Exit codes, the kernel's: 0 a human said it, 42 not enough information to
 * say a human said this was true, 2 refused.
 */

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;

public static class Kernel
{
    const int ALIVE = 0, ESCALATE = 42, REFUSED = 2;

    // ── JSON: Python's json.loads in, the kernel's canon out ───────────────
    /** A JSON object as Python's dict: insertion order, a repeated key keeps
     *  its place and takes the last value. */
    sealed class JObj
    {
        public readonly List<string> Keys = new();
        readonly Dictionary<string, object?> map = new(StringComparer.Ordinal);
        public object? this[string k]
        {
            get => map[k];
            set { if (!map.ContainsKey(k)) Keys.Add(k); map[k] = value; }
        }
        public bool Has(string k) => map.ContainsKey(k);
        public object? Get(string k) => map.TryGetValue(k, out var v) ? v : null;
        public void Remove(string k) { if (map.Remove(k)) Keys.Remove(k); }
        public int Count => Keys.Count;
        public JObj Copy() { var o = new JObj(); foreach (var k in Keys) o[k] = map[k]; return o; }
    }

    static JObj Obj(params object?[] kv)
    {
        var o = new JObj();
        for (int i = 0; i < kv.Length; i += 2) o[(string)kv[i]!] = kv[i + 1];
        return o;
    }

    sealed class JsonError : Exception { public JsonError(string m) : base(m) { } }

    sealed class Parser
    {
        readonly string s;
        int i;
        Parser(string s) { this.s = s; }

        public static object? Loads(string text)
        {
            var p = new Parser(text);
            p.Ws();
            var v = p.Value();
            p.Ws();
            if (p.i != p.s.Length) throw new JsonError("extra data");
            return v;
        }

        void Ws() { while (i < s.Length && " \t\n\r".IndexOf(s[i]) >= 0) i++; }

        bool Lit(string w) { if (string.CompareOrdinal(s, i, w, 0, w.Length) == 0) { i += w.Length; return true; } return false; }

        object? Value()
        {
            if (i >= s.Length) throw new JsonError("expecting value");
            switch (s[i])
            {
                case '{': return Object();
                case '[': return Array();
                case '"': return Str();
            }
            if (Lit("null")) return null;
            if (Lit("true")) return true;
            if (Lit("false")) return false;
            if (Lit("NaN")) return double.NaN;
            if (Lit("Infinity")) return double.PositiveInfinity;
            if (Lit("-Infinity")) return double.NegativeInfinity;
            return Number();
        }

        object Number()
        {
            int st = i;
            if (i < s.Length && s[i] == '-') i++;
            if (i < s.Length && s[i] == '0') i++;
            else if (i < s.Length && s[i] >= '1' && s[i] <= '9') while (i < s.Length && char.IsAsciiDigit(s[i])) i++;
            else throw new JsonError("expecting value");
            int intEnd = i;
            bool frac = false, exp = false;
            if (i + 1 < s.Length && s[i] == '.' && char.IsAsciiDigit(s[i + 1])) { frac = true; i++; while (i < s.Length && char.IsAsciiDigit(s[i])) i++; }
            if (i < s.Length && (s[i] == 'e' || s[i] == 'E'))
            {
                int j = i + 1;
                if (j < s.Length && (s[j] == '+' || s[j] == '-')) j++;
                if (j < s.Length && char.IsAsciiDigit(s[j])) { exp = true; i = j; while (i < s.Length && char.IsAsciiDigit(s[i])) i++; }
            }
            if (!frac && !exp) return BigInteger.Parse(s.AsSpan(st, intEnd - st), CultureInfo.InvariantCulture);
            return double.Parse(s.AsSpan(st, i - st), CultureInfo.InvariantCulture);
        }

        JObj Object()
        {
            i++;
            var o = new JObj();
            Ws();
            if (i < s.Length && s[i] == '}') { i++; return o; }
            while (true)
            {
                Ws();
                if (i >= s.Length || s[i] != '"') throw new JsonError("expecting key");
                var k = Str();
                Ws();
                if (i >= s.Length || s[i] != ':') throw new JsonError("expecting :");
                i++;
                Ws();
                o[k] = Value();
                Ws();
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == '}') { i++; return o; }
                throw new JsonError("expecting , or }");
            }
        }

        List<object?> Array()
        {
            i++;
            var o = new List<object?>();
            Ws();
            if (i < s.Length && s[i] == ']') { i++; return o; }
            while (true)
            {
                Ws();
                o.Add(Value());
                Ws();
                if (i < s.Length && s[i] == ',') { i++; continue; }
                if (i < s.Length && s[i] == ']') { i++; return o; }
                throw new JsonError("expecting , or ]");
            }
        }

        string Str()
        {
            i++;
            var b = new StringBuilder();
            while (true)
            {
                if (i >= s.Length) throw new JsonError("unterminated string");
                char c = s[i++];
                if (c == '"') return b.ToString();
                if (c < 0x20) throw new JsonError("invalid control character");
                if (c != '\\') { b.Append(c); continue; }
                if (i >= s.Length) throw new JsonError("unterminated escape");
                char e = s[i++];
                switch (e)
                {
                    case '"': b.Append('"'); break;
                    case '\\': b.Append('\\'); break;
                    case '/': b.Append('/'); break;
                    case 'b': b.Append('\b'); break;
                    case 'f': b.Append('\f'); break;
                    case 'n': b.Append('\n'); break;
                    case 'r': b.Append('\r'); break;
                    case 't': b.Append('\t'); break;
                    case 'u':
                        if (i + 4 > s.Length || !int.TryParse(s.AsSpan(i, 4), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out int u))
                            throw new JsonError("bad \\u escape");
                        b.Append((char)u);
                        i += 4;
                        break;
                    default: throw new JsonError("invalid escape");
                }
            }
        }
    }

    /** Code point order, as Python compares str: not UTF-16 unit order. */
    sealed class CodePointOrder : IComparer<string>
    {
        public static readonly CodePointOrder It = new();
        public int Compare(string? a, string? b)
        {
            int i = 0, j = 0;
            while (i < a!.Length && j < b!.Length)
            {
                int x = char.ConvertToUtf32(a, i), y = char.ConvertToUtf32(b, j);
                if (x != y) return x.CompareTo(y);
                i += x > 0xFFFF ? 2 : 1;
                j += y > 0xFFFF ? 2 : 1;
            }
            return (a.Length - i).CompareTo(b!.Length - j);
        }
    }

    static void Quote(string s, StringBuilder b, bool ascii)
    {
        b.Append('"');
        foreach (char c in s)
        {
            switch (c)
            {
                case '"': b.Append("\\\""); break;
                case '\\': b.Append("\\\\"); break;
                case '\n': b.Append("\\n"); break;
                case '\r': b.Append("\\r"); break;
                case '\t': b.Append("\\t"); break;
                case '\b': b.Append("\\b"); break;
                case '\f': b.Append("\\f"); break;
                default:
                    if (c < 0x20 || (ascii && c > 0x7e)) b.Append("\\u").Append(((int)c).ToString("x4"));
                    else b.Append(c);
                    break;
            }
        }
        b.Append('"');
    }

    /** json.dumps: keys sorted by code point when `sort`; `item`/`key`
     *  separators; ensure_ascii when `ascii`. No floats: rows never hold one. */
    static void Dump(object? o, StringBuilder b, bool sort, string item, string key, bool ascii)
    {
        switch (o)
        {
            case null: b.Append("null"); return;
            case bool x: b.Append(x ? "true" : "false"); return;
            case BigInteger n: b.Append(n.ToString(CultureInfo.InvariantCulture)); return;
            case int n: b.Append(n.ToString(CultureInfo.InvariantCulture)); return;
            case string str: Quote(str, b, ascii); return;
            case List<object?> list:
                b.Append('[');
                for (int i = 0; i < list.Count; i++) { if (i > 0) b.Append(item); Dump(list[i], b, sort, item, key, ascii); }
                b.Append(']');
                return;
            case JObj m:
                var keys = new List<string>(m.Keys);
                if (sort) keys.Sort(CodePointOrder.It);
                b.Append('{');
                for (int i = 0; i < keys.Count; i++)
                {
                    if (i > 0) b.Append(item);
                    Quote(keys[i], b, ascii);
                    b.Append(key);
                    Dump(m[keys[i]], b, sort, item, key, ascii);
                }
                b.Append('}');
                return;
        }
        throw new ArgumentException("canon refuses " + o.GetType().Name);
    }

    static string Dumps(object? o, bool sort, string item, string key, bool ascii)
    {
        var b = new StringBuilder();
        Dump(o, b, sort, item, key, ascii);
        return b.ToString();
    }

    /** Family A, `sorted_compact_utf8`. */
    static string Canon(object? o) => Dumps(o, true, ",", ":", false);
    /** json.dumps(o) with Python's defaults: kernel.json, the proposal files. */
    static string DumpsDefault(object? o, bool sort) => Dumps(o, sort, ", ", ": ", true);
    /** json.dumps(o, ensure_ascii=False): what the person reads. */
    static string DumpsShown(object? o) => Dumps(o, false, ", ", ": ", false);

    // ── hashing and the seal ───────────────────────────────────────────────
    static readonly UTF8Encoding Utf8 = new(false, true); // strict, and a BOM stays a character
    static string Hex(byte[] d) => Convert.ToHexString(d).ToLowerInvariant();
    static string Sha256(byte[] d) => Hex(SHA256.HashData(d));
    static string H256(string text) => Sha256(Utf8.GetBytes(text));
    static string Hmac(byte[] key, byte[] msg) => Hex(HMACSHA256.HashData(key, msg));
    /** onescript/sign.py hmac_parts(key, "seal", subject). */
    static string Proof(byte[] key, string subject) => Hmac(key, Utf8.GetBytes("seal\u001f" + subject));

    // ── the tables: Python's decisions, read out of Python ─────────────────
    sealed class Ranges
    {
        readonly int[] lo, hi;
        public Ranges(object? rs)
        {
            var list = (List<object?>)rs!;
            lo = new int[list.Count];
            hi = new int[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                var r = (List<object?>)list[i]!;
                lo[i] = (int)(BigInteger)r[0]!;
                hi[i] = (int)(BigInteger)r[1]!;
            }
        }
        public bool Has(int c)
        {
            int a = 0, z = lo.Length - 1;
            while (a <= z)
            {
                int m = (a + z) >>> 1;
                if (c < lo[m]) z = m - 1;
                else if (c > hi[m]) a = m + 1;
                else return true;
            }
            return false;
        }
    }

    static Ranges WORD = null!, SPACE = null!, DIGIT = null!, CASED = null!, IGNORABLE = null!;
    static Dictionary<int, string> LOWER = null!, INVALID_CHARREFS = null!;
    static HashSet<int> LINE_BREAKS = null!, INVALID_CODEPOINTS = null!;
    static Dictionary<string, string> HTML5 = null!, NEXT = null!;
    static Dictionary<char, HashSet<int>> TAG_FOLD = null!;
    static Dictionary<char, HashSet<(int, int)>> TAG_BACKREF = null!;
    static string GENESIS = "";
    static int TOP, SNIFF, MAX_FILE, MAX_CHUNK, ECHO_RUN;
    static BigInteger HOLD_NUM, HOLD_DEN;
    static HashSet<string> FUNCTION = null!, MAKE = null!;
    static List<string> ROW_KEYS = null!;
    static string VERSION = "";

    static int I(object? v) => (int)(BigInteger)v!;
    static List<object?> L(object? v) => (List<object?>)v!;

    static void LoadTables(string file)
    {
        var t = (JObj)Parser.Loads(File.ReadAllText(file, Utf8))!;
        var kc = (JObj)t["kernel"]!;
        var sigma = (JObj)t["sigma"]!;
        WORD = new Ranges(t["word"]);
        SPACE = new Ranges(t["space"]);
        DIGIT = new Ranges(t["digit"]);
        CASED = new Ranges(sigma["cased"]);
        IGNORABLE = new Ranges(sigma["case_ignorable"]);
        var lower = (JObj)t["lower"]!;
        LOWER = lower.Keys.ToDictionary(k => int.Parse(k, CultureInfo.InvariantCulture), k => (string)lower[k]!);
        LINE_BREAKS = L(t["line_breaks"]).Select(I).ToHashSet();
        var html5 = (JObj)t["html5"]!;
        HTML5 = html5.Keys.ToDictionary(k => k, k => (string)html5[k]!, StringComparer.Ordinal);
        var icr = (JObj)t["invalid_charrefs"]!;
        INVALID_CHARREFS = icr.Keys.ToDictionary(k => int.Parse(k, CultureInfo.InvariantCulture), k => (string)icr[k]!);
        INVALID_CODEPOINTS = L(t["invalid_codepoints"]).Select(I).ToHashSet();
        var fold = (JObj)t["tag_fold"]!;
        TAG_FOLD = fold.Keys.ToDictionary(k => k[0], k => L(fold[k]).Select(I).ToHashSet());
        var back = (JObj)t["tag_backref"]!;
        TAG_BACKREF = back.Keys.ToDictionary(k => k[0], k => L(back[k]).Select(p => (I(L(p)[0]), I(L(p)[1]))).ToHashSet());
        GENESIS = (string)kc["GENESIS"]!;
        TOP = I(kc["TOP"]);
        SNIFF = I(kc["SNIFF"]);
        MAX_FILE = I(kc["MAX_FILE"]);
        MAX_CHUNK = I(kc["MAX_CHUNK"]);
        ECHO_RUN = I(kc["ECHO_RUN"]);
        var hold = L(kc["HOLD"]);
        HOLD_NUM = (BigInteger)hold[0]!;
        HOLD_DEN = (BigInteger)hold[1]!;
        FUNCTION = L(kc["FUNCTION"]).Select(x => (string)x!).ToHashSet(StringComparer.Ordinal);
        MAKE = L(kc["MAKE"]).Select(x => (string)x!).ToHashSet(StringComparer.Ordinal);
        var next = (JObj)kc["NEXT"]!;
        NEXT = next.Keys.ToDictionary(k => k, k => (string)next[k]!, StringComparer.Ordinal);
        ROW_KEYS = L(kc["ROW_KEYS"]).Select(x => (string)x!).ToList();
    }

    // ── str, as Python has it ──────────────────────────────────────────────
    static int[] Cps(string s)
    {
        var o = new List<int>(s.Length);
        for (int i = 0; i < s.Length; i++)
        {
            if (char.IsHighSurrogate(s[i]) && i + 1 < s.Length && char.IsLowSurrogate(s[i + 1])) { o.Add(char.ConvertToUtf32(s[i], s[i + 1])); i++; }
            else o.Add(s[i]);
        }
        return o.ToArray();
    }

    static string FromCps(int[] a, int from, int to)
    {
        var b = new StringBuilder();
        for (int i = from; i < to; i++) Cp(b, a[i]);
        return b.ToString();
    }

    static StringBuilder Cp(StringBuilder b, int c) => c > 0xFFFF ? b.Append(char.ConvertFromUtf32(c)) : b.Append((char)c);

    static int Len(string s) => Cps(s).Length;
    /** Python's \w: a word character or "_". */
    static bool IsW(int c) => c == '_' || WORD.Has(c);

    /** str.lower: the table, and U+03A3 by CPython's final-sigma context. */
    static string Lower(string s)
    {
        var a = Cps(s);
        var b = new StringBuilder(s.Length);
        for (int i = 0; i < a.Length; i++)
        {
            if (a[i] == 0x03A3) b.Append(FinalSigma(a, i) ? 'ς' : 'σ');
            else if (LOWER.TryGetValue(a[i], out var l)) b.Append(l);
            else Cp(b, a[i]);
        }
        return b.ToString();
    }

    static bool FinalSigma(int[] a, int i)
    {
        int j = i - 1;
        while (j >= 0 && IGNORABLE.Has(a[j])) j--;
        if (j < 0 || !CASED.Has(a[j])) return false;
        j = i + 1;
        while (j < a.Length && IGNORABLE.Has(a[j])) j++;
        return j == a.Length || !CASED.Has(a[j]);
    }

    /** str.strip() == "" */
    static bool Blank(string s) => Cps(s).All(SPACE.Has);

    /** str.strip() */
    static string Strip(string s)
    {
        var a = Cps(s);
        int i = 0, j = a.Length;
        while (i < j && SPACE.Has(a[i])) i++;
        while (j > i && SPACE.Has(a[j - 1])) j--;
        return FromCps(a, i, j);
    }

    /** str.splitlines(): "\r\n" is one break; no terminators kept. */
    static List<string> Splitlines(string s)
    {
        var a = Cps(s);
        var o = new List<string>();
        int start = 0, i = 0;
        while (i < a.Length)
        {
            if (LINE_BREAKS.Contains(a[i]))
            {
                o.Add(FromCps(a, start, i));
                i += a[i] == '\r' && i + 1 < a.Length && a[i + 1] == '\n' ? 2 : 1;
                start = i;
            }
            else i++;
        }
        if (start < a.Length) o.Add(FromCps(a, start, a.Length));
        return o;
    }

    static string Take(string s, int n) { var a = Cps(s); return a.Length <= n ? s : FromCps(a, 0, n); }

    // ── words and the bite key: onescript/words.py ─────────────────────────
    static List<string> Words(string text)
    {
        var o = new List<string>();
        var cur = new StringBuilder();
        foreach (int c in Cps(Lower(text)))
        {
            if (WORD.Has(c)) Cp(cur, c);
            else if (cur.Length > 0) { o.Add(cur.ToString()); cur.Clear(); }
        }
        if (cur.Length > 0) o.Add(cur.ToString());
        return o;
    }

    static string? BiteKey(string text) { var ws = Words(text); return ws.Count == 0 ? null : H256(string.Join(" ", ws)); }

    static string SubjectOf(JObj prop) =>
        "proposal:" + H256(Canon(Obj("path", prop.Get("path"), "data", prop.Get("data"), "cites", prop.Get("cites"), "claim", prop.Get("claim"))));

    // ── the scanners: the kernel's regular expressions, by hand (from kernel.ts) ──
    /** " " and one newline for each the match held: line numbers still point home. */
    static string Kept(int[] a, int from, int to)
    {
        int n = 0;
        for (int i = from; i < to; i++) if (a[i] == '\n') n++;
        return " " + new string('\n', n);
    }

    /** `<(script|style)\b.*?</\1\s*>|<!--.*?-->` under re.S | re.I, at `i` (a "<"). */
    static int HiddenAt(int[] a, int i)
    {
        foreach (var name in new[] { "script", "style" })
        {
            int n = name.Length;
            bool ok = i + 1 + n <= a.Length;
            for (int k = 0; ok && k < n; k++) ok = TAG_FOLD[name[k]].Contains(a[i + 1 + k]);
            if (!ok) continue;
            int e = i + 1 + n;
            if (e < a.Length && IsW(a[e])) continue; // \b
            for (int k = e; k + 1 < a.Length; k++)
            {
                if (a[k] != '<' || a[k + 1] != '/') continue;
                bool same = k + 2 + n <= a.Length;
                for (int x = 0; same && x < n; x++) same = TAG_BACKREF[name[x]].Contains((a[i + 1 + x], a[k + 2 + x]));
                if (!same) continue;
                int p = k + 2 + n;
                while (p < a.Length && SPACE.Has(a[p])) p++;
                if (p < a.Length && a[p] == '>') return p + 1;
            }
        }
        if (i + 3 < a.Length && a[i + 1] == '!' && a[i + 2] == '-' && a[i + 3] == '-')
            for (int k = i + 4; k + 2 < a.Length; k++) if (a[k] == '-' && a[k + 1] == '-' && a[k + 2] == '>') return k + 3;
        return -1;
    }

    static string Hidden(string text)
    {
        var a = Cps(text);
        var b = new StringBuilder();
        int i = 0;
        while (i < a.Length)
        {
            int m = a[i] == '<' ? HiddenAt(a, i) : -1;
            if (m >= 0) { b.Append(Kept(a, i, m)); i = m; }
            else Cp(b, a[i++]);
        }
        return b.ToString();
    }

    /** `<[^>]*>` */
    static string Tags(string text)
    {
        var a = Cps(text);
        var b = new StringBuilder();
        int i = 0;
        while (i < a.Length)
        {
            if (a[i] == '<')
            {
                int q = System.Array.IndexOf(a, '>', i + 1);
                if (q >= 0) { b.Append(Kept(a, i, q + 1)); i = q + 1; continue; }
            }
            Cp(b, a[i++]);
        }
        return b.ToString();
    }

    static bool IsDec(int c) => c >= '0' && c <= '9';
    static bool IsHex(int c) => IsDec(c) || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f');
    static readonly HashSet<int> NAME_STOP = new() { '\t', '\n', '\f', ' ', '<', '&', '#', ';' };
    static int At(int[] a, int i) => i < a.Length ? a[i] : -1;

    /** html.unescape: `&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)`. */
    static string Unescape(string s)
    {
        if (s.IndexOf('&') < 0) return s;
        var a = Cps(s);
        var b = new StringBuilder();
        int i = 0;
        while (i < a.Length)
        {
            if (a[i] != '&') { Cp(b, a[i++]); continue; }
            int j = i + 1;
            if (At(a, j) == '#')
            {
                if (IsDec(At(a, j + 1))) { j += 1; while (j < a.Length && IsDec(a[j])) j++; }
                else if ((At(a, j + 1) == 'x' || At(a, j + 1) == 'X') && IsHex(At(a, j + 2))) { j += 2; while (j < a.Length && IsHex(a[j])) j++; }
                else { b.Append('&'); i++; continue; }
            }
            else
            {
                while (j < a.Length && j - (i + 1) < 32 && !NAME_STOP.Contains(a[j])) j++;
                if (j == i + 1) { b.Append('&'); i++; continue; }
            }
            if (At(a, j) == ';') j++;
            b.Append(Charref(FromCps(a, i + 1, j)));
            i = j;
        }
        return b.ToString();
    }

    static string Charref(string s)
    {
        if (s[0] == '#')
        {
            bool hex = s[1] == 'x' || s[1] == 'X';
            string digits = (hex ? s.Substring(2) : s.Substring(1)).TrimEnd(';');
            BigInteger num = hex ? BigInteger.Parse("0" + digits, NumberStyles.HexNumber, CultureInfo.InvariantCulture)
                                 : BigInteger.Parse(digits, CultureInfo.InvariantCulture);
            if (num > 0x10FFFF) return "�";
            int n = (int)num;
            if (INVALID_CHARREFS.TryGetValue(n, out var r)) return r;
            if (n >= 0xD800 && n <= 0xDFFF) return "�";
            if (INVALID_CODEPOINTS.Contains(n)) return "";
            return char.ConvertFromUtf32(n);
        }
        if (HTML5.TryGetValue(s, out var v)) return v;
        var a = Cps(s);
        for (int x = a.Length - 1; x > 1; x--)
        {
            var head = FromCps(a, 0, x);
            if (HTML5.TryGetValue(head, out var hv)) return hv + FromCps(a, x, a.Length);
        }
        return "&" + s;
    }

    static string HtmlText(string text) => Unescape(Tags(Hidden(text)));

    static readonly string[] KEYWORDS = { "def", "function", "sub", "class", "func", "fn" };
    static bool IdentHead(int c) => c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
    static bool IdentTail(int c) => IdentHead(c) || IsDec(c);

    /** re.findall of `\b(?:def|function|sub|class|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)`. */
    static List<string> Defines(string text)
    {
        var a = Cps(text);
        var o = new List<string>();
        int p = 0;
        while (p < a.Length)
        {
            bool found = false;
            if (p == 0 || !IsW(a[p - 1]))
            {
                foreach (var kw in KEYWORDS)
                {
                    int k = 0;
                    while (k < kw.Length && At(a, p + k) == kw[k]) k++;
                    if (k < kw.Length) continue;
                    int q = p + kw.Length, s0 = q;
                    while (q < a.Length && SPACE.Has(a[q])) q++;
                    if (q == s0 || !(q < a.Length && IdentHead(a[q]))) continue;
                    int start = q;
                    while (q < a.Length && IdentTail(a[q])) q++;
                    o.Add(FromCps(a, start, q));
                    p = q;
                    found = true;
                    break;
                }
            }
            if (!found) p++;
        }
        return o;
    }

    /** How many times `\bname\b` occurs (name is an identifier: \w all through). */
    static int Named(string text, string name)
    {
        var a = Cps(text);
        var b = Cps(name);
        int n = 0, p = 0;
        while (p + b.Length <= a.Length)
        {
            int k = 0;
            while (k < b.Length && a[p + k] == b[k]) k++;
            if (k == b.Length && (p == 0 || !IsW(a[p - 1])) && (p + b.Length == a.Length || !IsW(a[p + b.Length]))) { n++; p += b.Length; }
            else p++;
        }
        return n;
    }

    // ── the pile ───────────────────────────────────────────────────────────
    static bool Holds(string w, ISet<string> toks)
    {
        if (toks.Contains(w)) return true;
        if (Len(w) < 4 || !Cps(w).Any(DIGIT.Has)) return false;
        foreach (var t in toks) if (t.EndsWith(w, StringComparison.Ordinal) || t.StartsWith(w, StringComparison.Ordinal)) return true;
        return false;
    }

    static bool Echo(string bite, string text)
    {
        var ws = Words(bite);
        var ts = Words(text);
        int n = Math.Min(ws.Count, ECHO_RUN);
        if (n < 3) return false;
        var runs = new HashSet<string>(StringComparer.Ordinal);
        for (int i = 0; i + n <= ts.Count; i++) runs.Add(string.Join("\u0000", ts.GetRange(i, n)));
        for (int i = 0; i + n <= ws.Count; i++) if (runs.Contains(string.Join("\u0000", ws.GetRange(i, n)))) return true;
        return false;
    }

    sealed class Chunk
    {
        public readonly string Path, Text, Id;
        public readonly int Line;
        public Chunk(string path, int line, string text) { Path = path; Line = line; Text = text; Id = H256(text); }
        public JObj Json() => Obj("path", Path, "line", new BigInteger(Line), "text", Text, "id", Id);
    }

    /** Path.rglob("*"), every entry: into folders, not through folder links. */
    static void Walk(string dir, List<string> parts, List<List<string>> o)
    {
        foreach (var p in Directory.EnumerateFileSystemEntries(dir))
        {
            var mine = new List<string>(parts) { System.IO.Path.GetFileName(p) };
            o.Add(mine);
            var info = new DirectoryInfo(p);
            if (info.Exists && info.LinkTarget == null) Walk(p, mine, o);
        }
    }

    static int ByParts(List<string> a, List<string> b)
    {
        for (int i = 0; i < Math.Min(a.Count, b.Count); i++)
        {
            int c = CodePointOrder.It.Compare(a[i], b[i]);
            if (c != 0) return c;
        }
        return a.Count.CompareTo(b.Count);
    }

    static string Suffix(string name) { int i = name.LastIndexOf('.'); return i > 0 && i < name.Length - 1 ? name.Substring(i) : ""; }

    static List<Chunk> Chunks(string pile)
    {
        var all = new List<List<string>>();
        Walk(pile, new List<string>(), all);
        all.Sort(ByParts);
        var o = new List<Chunk>();
        foreach (var rel in all)
        {
            var p = System.IO.Path.Combine(new[] { pile }.Concat(rel).ToArray());
            if (!File.Exists(p) || rel.Any(x => x.StartsWith('.'))) continue;
            string text;
            try
            {
                if (new FileInfo(p).Length > MAX_FILE) continue;
                var raw = File.ReadAllBytes(p);
                if (System.Array.IndexOf(raw, (byte)0, 0, Math.Min(raw.Length, SNIFF)) >= 0) continue;
                text = Utf8.GetString(raw);
            }
            catch (Exception e) when (e is IOException || e is DecoderFallbackException || e is UnauthorizedAccessException) { continue; }
            var suf = Suffix(rel[^1]).ToLowerInvariant();
            if (suf == ".html" || suf == ".htm") text = HtmlText(text);
            var relPath = string.Join("/", rel);
            var lines = Splitlines(text);
            lines.Add("");
            var para = new List<string>();
            int start = 1, size = 0;
            for (int i = 1; i <= lines.Count; i++)
            {
                var line = lines[i - 1];
                if (para.Count > 0 && (Blank(line) || size + Len(line) > MAX_CHUNK))
                {
                    o.Add(new Chunk(relPath, start, string.Join("\n", para)));
                    para.Clear();
                    size = 0;
                }
                if (!Blank(line))
                {
                    if (para.Count == 0) start = i;
                    para.Add(Take(line, MAX_CHUNK));
                    size += Len(para[^1]) + 1;
                }
            }
        }
        return o;
    }

    /** An exact rational, for the rank's scores. */
    readonly record struct Q(BigInteger N, BigInteger D)
    {
        public Q Plus(Q o)
        {
            var n = N * o.D + o.N * D;
            var d = D * o.D;
            var g = BigInteger.GreatestCommonDivisor(n, d);
            return g.IsZero ? new Q(n, d) : new Q(n / g, d / g);
        }
        public int CompareTo(Q o) => (N * o.D).CompareTo(o.N * D);
    }

    static List<Chunk> Rank(List<Chunk> pile, string bite)
    {
        var want = new List<string>();
        foreach (var w in Words(bite)) if (!FUNCTION.Contains(w) && !want.Contains(w)) want.Add(w);
        if (want.Count == 0) return new List<Chunk>();
        var held = new List<(Chunk c, HashSet<string> h)>();
        foreach (var c in pile)
        {
            var toks = new HashSet<string>(Words(c.Text), StringComparer.Ordinal);
            var h = want.Where(w => Holds(w, toks)).ToHashSet(StringComparer.Ordinal);
            if (new BigInteger(h.Count) * HOLD_DEN >= HOLD_NUM * want.Count) held.Add((c, h));
        }
        var df = want.ToDictionary(w => w, w => held.Count(x => x.h.Contains(w)), StringComparer.Ordinal);
        var scored = new List<(Q s, Chunk c)>();
        foreach (var (c, h) in held)
        {
            if (h.Count == 0) continue;
            var s = new Q(0, 1);
            foreach (var w in h) s = s.Plus(new Q(1, df[w]));
            scored.Add((s, c));
        }
        scored.Sort((x, y) =>
        {
            int c = y.s.CompareTo(x.s);
            if (c != 0) return c;
            c = CodePointOrder.It.Compare(x.c.Path, y.c.Path);
            return c != 0 ? c : x.c.Line.CompareTo(y.c.Line);
        });
        return scored.Take(TOP).Select(x => x.c).ToList();
    }

    static JObj Hint(string why, string? where, string? next)
    {
        var m = Obj("why", why);
        if (where != null) m["where"] = where;
        if (next != null) m["next"] = next;
        return m;
    }

    static List<object?> Look(List<Chunk> pile, string bite, List<Chunk> served)
    {
        var o = new List<object?>();
        var ws = Words(bite);
        var want = new List<string>();
        foreach (var w in ws) if (!FUNCTION.Contains(w) && !want.Contains(w)) want.Add(w);
        if (want.Count == 0) o.Add(Hint("every word is a common one: name the thing you mean", null, null));
        var every = new HashSet<string>(StringComparer.Ordinal);
        foreach (var c in pile) every.UnionWith(Words(c.Text));
        var absent = want.Where(w => !Holds(w, every)).ToList();
        if (absent.Count > 0)
            o.Add(Hint("no file in your folder has " + string.Join(", ", absent), "it may be called something else there", NEXT["other words"]));
        foreach (var c in served)
        {
            var names = new List<string>();
            foreach (var nm in Defines(c.Text)) if (!names.Contains(nm)) names.Add(nm);
            foreach (var name in names)
            {
                var uses = new SortedSet<string>(CodePointOrder.It);
                foreach (var d in pile) if (!ReferenceEquals(d, c) && Named(d.Text, name) > 0) uses.Add(d.Path);
                if (Named(c.Text, name) > 1) uses.Add(c.Path);
                o.Add(Hint(name + " is defined at " + c.Path + ":" + c.Line,
                    uses.Count > 0 ? "also named in " + string.Join(", ", uses) : "nothing else in your folder names it", NEXT["unused"]));
            }
        }
        if (ws.Count > 0 && MAKE.Contains(ws[0])) o.Add(Hint("this asks for something made", null, NEXT["made"]));
        return o;
    }

    // ── the door ───────────────────────────────────────────────────────────
    static readonly string NOT_A_ROW = "a row is exactly {path, data, cites, claim}";

    static string? Door(object? prop, bool parsed, ISet<string> served)
    {
        var keys = new[] { "path", "data", "cites", "claim" };
        if (!parsed || prop is not JObj p || p.Count != 4 || !keys.All(p.Has)) return NOT_A_ROW;
        if (p["path"] is not string path || path.Length == 0 || path.StartsWith('/') || path.StartsWith('\\') || path.Split('/').Contains(".."))
            return "a path is plain and relative, inside the box";
        if (p["data"] is not string || p["cites"] is not List<object?>) return "data is text and cites a list";
        if (p["claim"] is not string claim || Blank(claim)) return "a claim says why";
        foreach (var c in (List<object?>)p["cites"]!) if (c is not string cs || !served.Contains(cs)) return "a cite that the last bite didn't serve";
        return null;
    }

    // ── the record: genesis's, in the one script's format ──────────────────
    sealed class Read
    {
        public readonly List<JObj> Rows = new();
        public readonly List<string> Breaks = new();
    }

    /** str() of a value, as Python prints it in a message. */
    static string PyStr(object? v) => v switch
    {
        null => "None",
        bool b => b ? "True" : "False",
        BigInteger n => n.ToString(CultureInfo.InvariantCulture),
        _ => v.ToString() ?? "",
    };

    static Read ReadBytes(byte[] bytes)
    {
        var r = new Read();
        var data = Utf8.GetString(bytes);
        if (data.Length > 0 && !data.EndsWith('\n'))
        {
            r.Breaks.Add("torn tail: the last line is unterminated; a human looks first");
            return r;
        }
        object? prev = GENESIS;
        var lines = data.Length == 0 ? System.Array.Empty<string>() : data.Substring(0, data.Length - 1).Split('\n');
        for (int i = 0; i < lines.Length; i++)
        {
            object? row;
            try { row = Parser.Loads(lines[i]); } catch (Exception e) when (e is JsonError || e is FormatException || e is OverflowException) { row = null; }
            if (row is not JObj m || !ROW_KEYS.All(m.Has))
            {
                r.Breaks.Add($"line {i}: garbled, not a record row");
                continue;
            }
            var body = m.Copy();
            body.Remove("hash");
            string hash;
            try { hash = H256(Canon(body)); } catch (ArgumentException) { hash = ""; }
            if (!Equals(m["prev"], prev) || !Equals(hash, m["hash"])) r.Breaks.Add($"row {PyStr(m["n"])}: chain broken");
            if (m["n"] is not BigInteger n || n != r.Rows.Count)
            {
                var shown = m["n"] is string sn ? $"'{sn}'" : PyStr(m["n"]);
                r.Breaks.Add($"row {shown}: out of place, it is row {r.Rows.Count}");
            }
            r.Rows.Add(m);
            prev = m["hash"];
        }
        return r;
    }

    /** The record read as kernel.py reads it: without a lock. */
    static Read ReadRecord(string path)
    {
        if (!File.Exists(path)) return new Read();
        using var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        var bytes = new byte[f.Length];
        f.ReadExactly(bytes);
        return ReadBytes(bytes);
    }

    [System.Runtime.InteropServices.DllImport("libc", SetLastError = true)]
    static extern int flock(int fd, int operation);
    const int LOCK_EX = 2;

    /** The record, opened for writing under the lock kernel.py takes: a
     *  blocking exclusive flock (libc's own; .NET's lock emulation is off, see
     *  Kernel.csproj, since it would also take a shared one on every read). On
     *  Windows there is none, as kernel.py has none there. */
    static FileStream Locked(string path)
    {
        var f = new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.ReadWrite);
        if (!OperatingSystem.IsWindows() && flock((int)f.SafeFileHandle.DangerousGetHandle(), LOCK_EX) != 0)
        {
            f.Dispose();
            throw new IOException("the record could not be locked");
        }
        return f;
    }

    static JObj Append(string box, string ts, string kind, string who, JObj fields)
    {
        var path = System.IO.Path.Combine(box, "record.jsonl");
        JObj row;
        using (var f = Locked(path))
        {
            var existing = new byte[f.Length];
            f.ReadExactly(existing);
            var r = ReadBytes(existing); // the true tail, read under the lock
            if (r.Breaks.Count > 0) throw new IOException(string.Join("; ", r.Breaks));
            bool sealedRow = kind == "seal" && who == "human";
            row = Obj("n", new BigInteger(r.Rows.Count), "kind", kind, "who", who, "family", "kernel",
                "standing", sealedRow ? "sealed" : "unattested", "version", VERSION, "ts", ts);
            var rest = fields.Copy();
            row["where"] = rest.Has("where") ? rest["where"] : null;
            rest.Remove("where");
            foreach (var k in rest.Keys) row[k] = rest[k];
            row["prev"] = r.Rows.Count > 0 ? r.Rows[^1]["hash"] : GENESIS;
            row["hash"] = H256(Canon(row));
            f.Seek(0, SeekOrigin.End);
            var line = Utf8.GetBytes(Canon(row) + "\n");
            f.Write(line);
            f.Flush(true);
        }
        // .NET can't fsync a directory; the first row's name rests on the file system's own journal
        return row;
    }

    // ── the box: theirs ────────────────────────────────────────────────────
    static JObj Config(string box) => (JObj)Parser.Loads(File.ReadAllText(System.IO.Path.Combine(box, "kernel.json"), Utf8))!;

    /** Path.resolve(): absolute, every link resolved as far as the path exists. */
    static string Resolved(string p)
    {
        var full = System.IO.Path.GetFullPath(p);
        var parts = full.Split('/', StringSplitOptions.RemoveEmptyEntries);
        var cur = "/";
        for (int i = 0; i < parts.Length; i++)
        {
            var next = System.IO.Path.Combine(cur, parts[i]);
            FileSystemInfo info = Directory.Exists(next) ? new DirectoryInfo(next) : new FileInfo(next);
            if (info.Exists && info.LinkTarget != null)
            {
                var target = info.ResolveLinkTarget(true);
                next = target != null ? Resolved(target.FullName) : next;
            }
            cur = next;
        }
        return cur;
    }

    static bool Inside(string p, string box)
    {
        var a = Resolved(p);
        var b = Resolved(box);
        return a == b || a.StartsWith(b.EndsWith('/') ? b : b + "/", StringComparison.Ordinal);
    }

    static (byte[]? key, string? why) ReadKey(string file, string box)
    {
        if (Inside(file, box)) return (null, "the key can't live inside the box");
        try
        {
            if (!OperatingSystem.IsWindows())
            {
                var mode = File.GetUnixFileMode(file);
                var others = UnixFileMode.GroupRead | UnixFileMode.GroupWrite | UnixFileMode.GroupExecute
                    | UnixFileMode.OtherRead | UnixFileMode.OtherWrite | UnixFileMode.OtherExecute;
                if ((mode & others) != 0) return (null, $"{file} is readable beyond its owner; chmod 600 it");
            }
            var hex = Strip(File.ReadAllText(file, Utf8));
            try { return (Convert.FromHexString(hex), null); } catch (FormatException) { return (null, "the key could not be read: ValueError"); }
        }
        catch (FileNotFoundException) { return (null, "the key could not be read: FileNotFoundError"); }
        catch (UnauthorizedAccessException) { return (null, "the key could not be read: PermissionError"); }
        catch (IOException) { return (null, "the key could not be read: OSError"); }
    }

    static int Init(string box, string pile, string key, string ts)
    {
        if (File.Exists(System.IO.Path.Combine(box, "record.jsonl"))) { Console.Error.WriteLine("refused: this box already has a record"); return REFUSED; }
        if (!Directory.Exists(pile)) { Console.Error.WriteLine($"refused: {pile} is not a folder"); return REFUSED; }
        if (Inside(key, box)) { Console.Error.WriteLine("refused: the key can't live inside the box"); return REFUSED; }
        if (!File.Exists(key))
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(System.IO.Path.GetFullPath(key))!);
            var opts = new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write };
            if (!OperatingSystem.IsWindows()) opts.UnixCreateMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;
            using var f = new FileStream(key, opts);
            f.Write(Encoding.ASCII.GetBytes(Hex(RandomNumberGenerator.GetBytes(32)) + "\n"));
        }
        Directory.CreateDirectory(box);
        var cfg = Obj("pile", Resolved(pile), "key", Resolved(key));
        File.WriteAllText(System.IO.Path.Combine(box, "kernel.json"), DumpsDefault(cfg, true) + "\n", Utf8);
        var row = Append(box, ts, "boot", "kernel", Obj("loop", "open", "pile_sha256", H256((string)cfg["pile"]!)));
        Console.WriteLine($"open loop, row {PyStr(row["n"])} {((string)row["hash"]!).Substring(0, 16)}: the box is yours");
        return ALIVE;
    }

    static Dictionary<string, JObj> Sealed(string box, List<JObj> rows, List<string> order)
    {
        var (key, _) = ReadKey((string)Config(box)["key"]!, box);
        var o = new Dictionary<string, JObj>(StringComparer.Ordinal);
        foreach (var r in rows)
        {
            if (r.Get("kind") is "seal" && key != null)
            {
                var subj = r.Has("subject") ? r["subject"] : "";
                var want = Utf8.GetBytes(Proof(key, subj as string ?? PyStr(subj)));
                var got = Utf8.GetBytes(PyStr(r.Has("proof") ? r["proof"] : null));
                if (want.Length == got.Length && CryptographicOperations.FixedTimeEquals(want, got))
                {
                    var s = (string)r["subject"]!;
                    if (!o.ContainsKey(s)) order.Add(s);
                    o[s] = r;
                }
            }
        }
        return o;
    }

    static bool RefusedBreaks(Read r)
    {
        if (r.Breaks.Count == 0) return false;
        Console.Error.WriteLine("refused:\n  " + string.Join("\n  ", r.Breaks));
        return true;
    }

    static string PropFile(string box, string subj) => System.IO.Path.Combine(box, "proposals", subj.Substring(9) + ".json");

    static int Bite(string box, string text, string ts)
    {
        var r = ReadRecord(System.IO.Path.Combine(box, "record.jsonl"));
        if (RefusedBreaks(r)) return REFUSED;
        var key = BiteKey(text);
        if (key == null) { Console.Error.WriteLine("refused: a bite with no words"); return REFUSED; }
        var order = new List<string>();
        Sealed(box, r.Rows, order);
        foreach (var subj in order) // a sealed answer for these words, first
        {
            var prop = (JObj)Parser.Loads(File.ReadAllText(PropFile(box, subj), Utf8))!;
            if (prop.Get("bite_key") is string bk && bk == key)
            {
                Append(box, ts, "bite", "kernel", Obj("bite_key", key, "answered_by", subj, "served", new List<object?>()));
                Console.WriteLine(DumpsShown(Obj("state", "sealed", "answer", prop["data"], "by", subj)));
                return ALIVE;
            }
        }
        var pile = Chunks((string)Config(box)["pile"]!);
        var served = Rank(pile, text);
        var refs = new List<object?>();
        var answers = new List<Chunk>();
        foreach (var c in served)
        {
            bool e = Echo(text, c.Text);
            refs.Add(Obj("id", c.Id, "where", c.Path + ":" + c.Line, "echo", e));
            if (!e) answers.Add(c);
        }
        Append(box, ts, "bite", "kernel", Obj("bite_key", key, "served", refs));
        var state = answers.Count > 0 ? "populated" : refs.Count > 0 ? "echo" : "empty";
        var doc = Obj("state", state, "bite_key", key);
        var shown = new List<object?>();
        for (int i = 0; i < served.Count; i++)
        {
            var m = ((JObj)refs[i]!).Copy();
            m["text"] = served[i].Text;
            shown.Add(m);
        }
        doc["served"] = shown;
        var hints = Look(pile, text, answers.Count > 0 ? served : new List<Chunk>());
        if (hints.Count > 0) doc["look"] = hints;
        Console.WriteLine(DumpsShown(doc));
        return answers.Count > 0 ? ALIVE : ESCALATE;
    }

    static int Propose(string box, List<string> lines, string ts)
    {
        var r = ReadRecord(System.IO.Path.Combine(box, "record.jsonl"));
        if (RefusedBreaks(r)) return REFUSED;
        var last = Enumerable.Reverse(r.Rows).FirstOrDefault(x => x.Get("kind") is "bite");
        if (last == null) { Console.Error.WriteLine("refused: propose answers a bite; bite first"); return REFUSED; }
        var served = new HashSet<string>(StringComparer.Ordinal);
        if (last.Get("served") is List<object?> sv) foreach (var s in sv) served.Add((string)((JObj)s!)["id"]!);
        int waiting = 0;
        for (int i = 0; i < lines.Count; i++)
        {
            object? prop = null;
            bool parsed = true;
            try { prop = Parser.Loads(lines[i]); } catch (Exception e) when (e is JsonError || e is FormatException || e is OverflowException) { parsed = false; }
            var why = Door(prop, parsed, served);
            if (why == null)
            {
                var p = (JObj)prop!;
                var subj = SubjectOf(p);
                Directory.CreateDirectory(System.IO.Path.Combine(box, "proposals"));
                var kept = p.Copy();
                kept["bite_key"] = last["bite_key"];
                File.WriteAllText(PropFile(box, subj), DumpsDefault(kept, false), Utf8);
                Append(box, ts, "proposal", "kernel", Obj("subject", subj, "verdict", "pass", "bite_row", last["n"]));
                Console.WriteLine($"pass  {subj}  waiting on your seal");
                waiting++;
            }
            else
            {
                Append(box, ts, "proposal", "kernel", Obj("verdict", "refused", "line", new BigInteger(i), "why", why));
                Console.WriteLine($"refused  line {i}: {why}");
            }
        }
        return waiting > 0 ? ESCALATE : ALIVE;
    }

    static int Seal(string box, string subj, string ts)
    {
        var r = ReadRecord(System.IO.Path.Combine(box, "record.jsonl"));
        if (RefusedBreaks(r)) return REFUSED;
        if (!r.Rows.Any(x => x.Get("kind") is "proposal" && x.Get("subject") is string s && s == subj))
        { Console.Error.WriteLine($"refused: {subj} is not a proposal in this box"); return REFUSED; }
        var (key, why) = ReadKey((string)Config(box)["key"]!, box);
        if (key == null) { Console.Error.WriteLine($"refused: {why}"); return REFUSED; }
        var prop = (JObj)Parser.Loads(File.ReadAllText(PropFile(box, subj), Utf8))!;
        if (SubjectOf(prop) != subj) { Console.Error.WriteLine("refused: the proposal's bytes changed since it was proposed"); return REFUSED; }
        Append(box, ts, "seal", "human", Obj("subject", subj, "proof", Proof(key, subj)));
        var outPath = System.IO.Path.Combine(box, "written", (string)prop["path"]!);
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(outPath)!);
        File.WriteAllText(outPath, (string)prop["data"]!, Utf8);
        Console.WriteLine($"sealed {subj}; written to {System.IO.Path.GetRelativePath(box, outPath)}");
        return ALIVE;
    }

    static int Verify(string box)
    {
        var r = ReadRecord(System.IO.Path.Combine(box, "record.jsonl"));
        if (r.Breaks.Count > 0) { Console.WriteLine(string.Join("\n", r.Breaks)); return REFUSED; }
        var good = Sealed(box, r.Rows, new List<string>());
        var forged = r.Rows.Where(x => x.Get("kind") is "seal" && !(x.Get("subject") is string s && good.ContainsKey(s))).Select(x => PyStr(x["n"])).ToList();
        if (forged.Count > 0) { Console.WriteLine($"the chain holds; seal rows [{string.Join(", ", forged)}] don't prove under your key"); return ESCALATE; }
        Console.WriteLine($"the chain holds: {r.Rows.Count} rows, {good.Count} sealed");
        return ALIVE;
    }

    static int Out(string box, string ts)
    {
        var r = ReadRecord(System.IO.Path.Combine(box, "record.jsonl"));
        if (RefusedBreaks(r)) return REFUSED;
        var good = Sealed(box, r.Rows, new List<string>());
        var waiting = r.Rows.Where(x => x.Get("kind") is "proposal" && x.Get("verdict") is "pass" && !(x.Get("subject") is string s && good.ContainsKey(s)))
            .Select(x => (string)x["subject"]!).ToList();
        var tip = r.Rows.Count > 0 ? r.Rows[^1]["hash"] : GENESIS;
        Append(box, ts, "out", "kernel", Obj("waiting", new BigInteger(waiting.Count), "tip", tip));
        foreach (var s in waiting) Console.WriteLine($"waiting on your seal: {s}");
        Console.WriteLine($"{r.Rows.Count + 1} rows; {good.Count} sealed; {waiting.Count} waiting");
        return waiting.Count > 0 ? ESCALATE : ALIVE;
    }

    // ── the vectors: every case, answered, for compare.py ──────────────────
    static int Vectors(string casesFile, string resultsFile)
    {
        var c = (JObj)((JObj)Parser.Loads(File.ReadAllText(casesFile, Utf8))!)["cases"]!;
        List<JObj> Part(string k) => L(c[k]).Select(x => (JObj)x!).ToList();
        var res = new JObj();
        res["canon"] = Part("canon").Select(o => { var s = Canon(o["in"]); return (object?)Obj("canon", s, "sha256", H256(s)); }).ToList();
        res["words"] = Part("words").Select(o => { var t = (string)o["text"]!; return (object?)Obj("words", Words(t).Cast<object?>().ToList(), "bite_key", BiteKey(t)); }).ToList();
        var hm = (JObj)c["hmac"]!;
        res["hmac"] = Obj(
            "rfc4231", L(hm["rfc4231"]).Select(x => { var m = (JObj)x!; return (object?)Obj("mac", Hmac(Convert.FromHexString((string)m["key"]!), Convert.FromHexString((string)m["msg"]!))); }).ToList(),
            "seal", L(hm["seal"]).Select(x => { var m = (JObj)x!; return (object?)Obj("proof", Proof(Convert.FromHexString((string)m["key"]!), (string)m["subject"]!)); }).ToList());
        res["html"] = Part("html").Select(o => (object?)Obj("out", HtmlText((string)o["in"]!))).ToList();
        res["holds"] = Part("holds").Select(o => (object?)Obj("out", Holds((string)o["w"]!, L(o["toks"]).Select(x => (string)x!).ToHashSet(StringComparer.Ordinal)))).ToList();
        res["echo"] = Part("echo").Select(o => (object?)Obj("out", Echo((string)o["bite"]!, (string)o["text"]!))).ToList();
        var piles = new List<List<Chunk>>();
        var tmp = Directory.CreateTempSubdirectory("kernel-vectors-").FullName;
        try
        {
            int n = 0;
            foreach (var o in Part("chunks"))
            {
                var root = System.IO.Path.Combine(tmp, "pile" + n++);
                Directory.CreateDirectory(root);
                foreach (var f in L(o["pile"]).Select(x => (JObj)x!))
                {
                    var p = System.IO.Path.Combine(new[] { root }.Concat(L(f["parts"]).Select(x => (string)x!)).ToArray());
                    Directory.CreateDirectory(System.IO.Path.GetDirectoryName(p)!);
                    if (f.Has("b64")) File.WriteAllBytes(p, Convert.FromBase64String((string)f["b64"]!));
                    else File.WriteAllBytes(p, Utf8.GetBytes(string.Concat(Enumerable.Repeat((string)f["repeat"]!, I(f["n"]))) + (string)f["then"]!));
                }
                piles.Add(Chunks(root));
            }
        }
        finally { Directory.Delete(tmp, true); }
        res["chunks"] = piles.Select(ch => (object?)Obj("chunks", ch.Select(x => (object?)x.Json()).ToList())).ToList();
        res["rank"] = Part("rank").Select(o => (object?)Obj("ids", Rank(piles[I(o["pile"])], (string)o["bite"]!).Select(x => (object?)x.Id).ToList())).ToList();
        res["look"] = Part("look").Select(o =>
        {
            var pile = piles[I(o["pile"])];
            var served = L(o["served"]).Select(at => pile[I(at)]).ToList();
            return (object?)Obj("look", Look(pile, (string)o["bite"]!, served));
        }).ToList();
        res["door"] = Part("door").Select(o =>
        {
            object? prop = null;
            bool parsed = true;
            try { prop = Parser.Loads((string)o["line"]!); } catch (Exception e) when (e is JsonError || e is FormatException || e is OverflowException) { parsed = false; }
            return (object?)Obj("why", Door(prop, parsed, L(o["served"]).Select(x => (string)x!).ToHashSet(StringComparer.Ordinal)));
        }).ToList();
        res["subject"] = Part("subject").Select(o => (object?)Obj("subject", SubjectOf((JObj)o["prop"]!))).ToList();
        File.WriteAllText(resultsFile, Canon(res) + "\n", Utf8);
        return ALIVE;
    }

    // ── the command line ───────────────────────────────────────────────────
    const string USAGE = "kernel.py: the smallest script that runs the whole loop, for anyone. Python 3.9+, stdlib only.";

    static int Run(string[] argv)
    {
        var dll = typeof(Kernel).Assembly.Location;
        var tables = Environment.GetEnvironmentVariable("KERNEL_TABLES")
            ?? System.IO.Path.Combine(AppContext.BaseDirectory, "..", "..", "kernel_tables.json");
        LoadTables(tables);
        VERSION = File.Exists(dll) ? Sha256(File.ReadAllBytes(dll)) : GENESIS;
        if (argv.Length > 0 && argv[0] == "vectors")
        {
            if (argv.Length != 3) { Console.Error.WriteLine("vectors CASES.json RESULTS.json"); return REFUSED; }
            return Vectors(argv[1], argv[2]);
        }
        var verb = argv.Length > 0 ? argv[0] : "";
        var opts = new Dictionary<string, string>(StringComparer.Ordinal);
        var args = new List<string>();
        for (int i = 1; i < argv.Length; i++)
        {
            if (new[] { "--box", "--pile", "--key", "--file", "--now" }.Contains(argv[i])) opts[argv[i]] = i + 1 < argv.Length ? argv[++i] : "";
            else args.Add(argv[i]);
        }
        string Opt(string k) => opts.TryGetValue(k, out var v) ? v : "";
        if (Opt("--box") == "") { Console.Error.WriteLine(USAGE); return REFUSED; }
        var box = Opt("--box");
        var ts = Opt("--now") != "" ? Opt("--now") : DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
        if (verb == "init" && Opt("--pile") != "" && Opt("--key") != "" && args.Count == 0) return Init(box, Opt("--pile"), Opt("--key"), ts);
        if (!File.Exists(System.IO.Path.Combine(box, "kernel.json"))) { Console.Error.WriteLine("refused: not a kernel box; init first"); return REFUSED; }
        if (verb == "bite" && args.Count == 1) return Bite(box, args[0], ts);
        if (verb == "propose" && Opt("--file") != "" && args.Count == 0) return Propose(box, Splitlines(Utf8.GetString(File.ReadAllBytes(Opt("--file")))), ts);
        if (verb == "seal" && args.Count == 1 && args[0].StartsWith("proposal:", StringComparison.Ordinal)) return Seal(box, args[0], ts);
        if (verb == "verify" && args.Count == 0) return Verify(box);
        if (verb == "out" && args.Count == 0) return Out(box, ts);
        Console.Error.WriteLine(USAGE);
        return REFUSED;
    }

    public static int Main(string[] argv)
    {
        var utf8 = new UTF8Encoding(false);
        Console.OutputEncoding = utf8;
        Console.SetOut(new StreamWriter(Console.OpenStandardOutput(), utf8) { AutoFlush = true, NewLine = "\n" });
        Console.SetError(new StreamWriter(Console.OpenStandardError(), utf8) { AutoFlush = true, NewLine = "\n" });
        try { return Run(argv); }
        catch (IOException e) { Console.Error.WriteLine("refused: " + e.Message); return REFUSED; }
    }
}
