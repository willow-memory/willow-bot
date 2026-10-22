/*
 * Kernel.java: kernel.py in Java. JDK 17+, the standard library only.
 *
 *   javac -d out Kernel.java
 *   java -cp out Kernel init    --box DIR --pile FOLDER --key FILE
 *   java -cp out Kernel bite    --box DIR "your words"
 *   java -cp out Kernel propose --box DIR --file ROWS.jsonl
 *   java -cp out Kernel seal    --box DIR proposal:<sha256>
 *   java -cp out Kernel verify  --box DIR
 *   java -cp out Kernel out     --box DIR
 *   java -cp out Kernel vectors CASES.json RESULTS.json
 *
 * A port, not a translation to be trusted on its own: it answers
 * ../cases.json, and compare.py holds it to kernel.py byte for byte, both
 * ways (a box written here verifies under kernel.py, and the reverse).
 *
 * The kernel's four patterns are scanners here, not java.util.regex (the same
 * scanners as ../ts/kernel.ts): Java's case folding in a backreference isn't
 * Python's, which the vectors caught on <\u017fcript>...</SCRIPT>.
 *
 * What Python decides that Java decides differently is not retyped here. It
 * is loaded from kernel_tables.json, which gen.py reads out of Python:
 * which code points are word characters, what each lowercases to (and the
 * final-sigma context), what str.strip / str.splitlines / str.isdigit count,
 * the HTML5 entities and html.unescape's invalid-reference tables, and the
 * kernel's constants. So this JVM's Unicode version and regex engine never
 * decide a word. The tables are found at -Dkernel.tables, else
 * $KERNEL_TABLES, else kernel_tables.json in the working directory.
 *
 * Exit codes, the kernel's: 0 a human said it, 42 not enough information to
 * say a human said this was true, 2 refused.
 */

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class Kernel {
    static final int ALIVE = 0, ESCALATE = 42, REFUSED = 2;

    // ── JSON: Python's json.loads in, the kernel's canon out ────────────────
    /** JSON null, so a Java null never means two things. */
    static final Object NULL = new Object() {
        @Override public String toString() { return "None"; }
    };

    static final class Json {
        private final String s;
        private int i;

        private Json(String s) { this.s = s; }

        /** json.loads: whitespace " \t\n\r" around, one value, nothing after. */
        static Object loads(String text) {
            Json p = new Json(text);
            p.ws();
            Object v = p.value();
            p.ws();
            if (p.i != p.s.length()) throw new IllegalArgumentException("extra data");
            return v;
        }

        private void ws() {
            while (i < s.length() && " \t\n\r".indexOf(s.charAt(i)) >= 0) i++;
        }

        private Object value() {
            if (i >= s.length()) throw new IllegalArgumentException("expecting value");
            char c = s.charAt(i);
            switch (c) {
                case '{': return object();
                case '[': return array();
                case '"': return string();
                default:
            }
            if (s.startsWith("null", i)) { i += 4; return NULL; }
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            if (s.startsWith("NaN", i)) { i += 3; return Double.NaN; }
            if (s.startsWith("Infinity", i)) { i += 8; return Double.POSITIVE_INFINITY; }
            if (s.startsWith("-Infinity", i)) { i += 9; return Double.NEGATIVE_INFINITY; }
            return number();
        }

        private Object number() {
            Matcher m = NUMBER.matcher(s).region(i, s.length());
            if (!m.lookingAt()) throw new IllegalArgumentException("expecting value");
            i = m.end();
            if (m.group(2) == null && m.group(3) == null) return new BigInteger(m.group(1));
            return Double.parseDouble(m.group());
        }

        private static final Pattern NUMBER =
            Pattern.compile("(-?(?:0|[1-9][0-9]*))(\\.[0-9]+)?([eE][-+]?[0-9]+)?");

        private Map<String, Object> object() {
            i++;
            Map<String, Object> out = new LinkedHashMap<>(); // a repeated key keeps its place, takes the last value
            ws();
            if (i < s.length() && s.charAt(i) == '}') { i++; return out; }
            while (true) {
                ws();
                if (i >= s.length() || s.charAt(i) != '"') throw new IllegalArgumentException("expecting key");
                String k = string();
                ws();
                if (i >= s.length() || s.charAt(i) != ':') throw new IllegalArgumentException("expecting :");
                i++;
                ws();
                out.put(k, value());
                ws();
                if (i < s.length() && s.charAt(i) == ',') { i++; continue; }
                if (i < s.length() && s.charAt(i) == '}') { i++; return out; }
                throw new IllegalArgumentException("expecting , or }");
            }
        }

        private List<Object> array() {
            i++;
            List<Object> out = new ArrayList<>();
            ws();
            if (i < s.length() && s.charAt(i) == ']') { i++; return out; }
            while (true) {
                ws();
                out.add(value());
                ws();
                if (i < s.length() && s.charAt(i) == ',') { i++; continue; }
                if (i < s.length() && s.charAt(i) == ']') { i++; return out; }
                throw new IllegalArgumentException("expecting , or ]");
            }
        }

        private String string() {
            i++; // the opening quote
            StringBuilder b = new StringBuilder();
            while (true) {
                if (i >= s.length()) throw new IllegalArgumentException("unterminated string");
                char c = s.charAt(i++);
                if (c == '"') return b.toString();
                if (c < 0x20) throw new IllegalArgumentException("invalid control character");
                if (c != '\\') { b.append(c); continue; }
                if (i >= s.length()) throw new IllegalArgumentException("unterminated escape");
                char e = s.charAt(i++);
                switch (e) {
                    case '"': b.append('"'); break;
                    case '\\': b.append('\\'); break;
                    case '/': b.append('/'); break;
                    case 'b': b.append('\b'); break;
                    case 'f': b.append('\f'); break;
                    case 'n': b.append('\n'); break;
                    case 'r': b.append('\r'); break;
                    case 't': b.append('\t'); break;
                    case 'u':
                        if (i + 4 > s.length()) throw new IllegalArgumentException("bad \\u escape");
                        b.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                        i += 4;
                        break;
                    default: throw new IllegalArgumentException("invalid escape");
                }
            }
        }
    }

    /** Code point order, as Python compares str: not UTF-16 unit order. */
    static final Comparator<String> BY_CODE_POINT = (a, b) -> {
        int i = 0, j = 0;
        while (i < a.length() && j < b.length()) {
            int x = a.codePointAt(i), y = b.codePointAt(j);
            if (x != y) return Integer.compare(x, y);
            i += Character.charCount(x);
            j += Character.charCount(y);
        }
        return Integer.compare(a.length() - i, b.length() - j);
    };

    /** Family A, `sorted_compact_utf8`: json.dumps(sort_keys=True,
     *  separators=(",", ":"), ensure_ascii=False). No floats: the kernel's
     *  rows never hold one, and a port must not invent repr(float). */
    static String canon(Object o) {
        StringBuilder b = new StringBuilder();
        dump(o, b, true, false);
        return b.toString();
    }

    /** json.dumps(o, sort_keys=sort) with Python's default separators and
     *  ensure_ascii=True: kernel.json and the proposal files. */
    static String dumpsDefault(Object o, boolean sort) {
        StringBuilder b = new StringBuilder();
        dumpSpaced(o, b, sort);
        return b.toString();
    }

    @SuppressWarnings("unchecked")
    private static void dump(Object o, StringBuilder b, boolean sort, boolean ascii) {
        if (o == NULL || o == null) { b.append("null"); return; }
        if (o instanceof Boolean) { b.append(((Boolean) o) ? "true" : "false"); return; }
        if (o instanceof BigInteger || o instanceof Integer || o instanceof Long) { b.append(o.toString()); return; }
        if (o instanceof String) { quote((String) o, b, ascii); return; }
        if (o instanceof List) {
            b.append('[');
            boolean first = true;
            for (Object v : (List<Object>) o) {
                if (!first) b.append(',');
                first = false;
                dump(v, b, sort, ascii);
            }
            b.append(']');
            return;
        }
        if (o instanceof Map) {
            Map<String, Object> m = (Map<String, Object>) o;
            List<String> keys = new ArrayList<>(m.keySet());
            if (sort) keys.sort(BY_CODE_POINT);
            b.append('{');
            boolean first = true;
            for (String k : keys) {
                if (!first) b.append(',');
                first = false;
                quote(k, b, ascii);
                b.append(':');
                dump(m.get(k), b, sort, ascii);
            }
            b.append('}');
            return;
        }
        throw new IllegalArgumentException("canon refuses " + o.getClass().getSimpleName());
    }

    @SuppressWarnings("unchecked")
    private static void dumpSpaced(Object o, StringBuilder b, boolean sort) {
        if (o instanceof List) {
            b.append('[');
            boolean first = true;
            for (Object v : (List<Object>) o) {
                if (!first) b.append(", ");
                first = false;
                dumpSpaced(v, b, sort);
            }
            b.append(']');
        } else if (o instanceof Map) {
            Map<String, Object> m = (Map<String, Object>) o;
            List<String> keys = new ArrayList<>(m.keySet());
            if (sort) keys.sort(BY_CODE_POINT);
            b.append('{');
            boolean first = true;
            for (String k : keys) {
                if (!first) b.append(", ");
                first = false;
                quote(k, b, true);
                b.append(": ");
                dumpSpaced(m.get(k), b, sort);
            }
            b.append('}');
        } else {
            dump(o, b, sort, true);
        }
    }

    private static void quote(String s, StringBuilder b, boolean ascii) {
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                case '\b': b.append("\\b"); break;
                case '\f': b.append("\\f"); break;
                default:
                    if (c < 0x20 || (ascii && c > 0x7e)) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        b.append('"');
    }

    // ── hashing and the seal ────────────────────────────────────────────────
    static String hex(byte[] d) {
        StringBuilder b = new StringBuilder(d.length * 2);
        for (byte x : d) b.append(String.format("%02x", x & 0xff));
        return b.toString();
    }

    static byte[] unhex(String s) {
        if (s.length() % 2 != 0) throw new IllegalArgumentException("odd hex");
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) out[i] = (byte) Integer.parseInt(s.substring(2 * i, 2 * i + 2), 16);
        return out;
    }

    static String h256(String text) { return sha256(text.getBytes(StandardCharsets.UTF_8)); }

    static String sha256(byte[] data) {
        try {
            return hex(MessageDigest.getInstance("SHA-256").digest(data));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    static String hmac(byte[] key, byte[] msg) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            // an empty key is legal HMAC; SecretKeySpec refuses it, so pad one zero byte (same result)
            mac.init(new SecretKeySpec(key.length == 0 ? new byte[1] : key, "HmacSHA256"));
            return hex(mac.doFinal(msg));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** onescript/sign.py hmac_parts(key, "seal", subject). */
    static String proof(byte[] key, String subject) {
        return hmac(key, ("seal" + "\u001f" + subject).getBytes(StandardCharsets.UTF_8));
    }

    // ── the tables: Python's decisions, read out of Python ──────────────────
    static final class Ranges {
        final int[] lo, hi;

        Ranges(List<Object> rs) {
            lo = new int[rs.size()];
            hi = new int[rs.size()];
            for (int i = 0; i < rs.size(); i++) {
                List<?> r = (List<?>) rs.get(i);
                lo[i] = ((BigInteger) r.get(0)).intValue();
                hi[i] = ((BigInteger) r.get(1)).intValue();
            }
        }

        boolean has(int c) {
            int a = 0, z = lo.length - 1;
            while (a <= z) {
                int m = (a + z) >>> 1;
                if (c < lo[m]) z = m - 1;
                else if (c > hi[m]) a = m + 1;
                else return true;
            }
            return false;
        }
    }

    static Ranges WORD, SPACE, DIGIT, CASED, IGNORABLE;
    static Map<Integer, String> LOWER;
    static Set<Integer> LINE_BREAKS, INVALID_CODEPOINTS;
    static Map<String, String> HTML5;
    static Map<Integer, String> INVALID_CHARREFS;
    static String GENESIS;
    static int TOP, SNIFF, MAX_FILE, MAX_CHUNK, ECHO_RUN, HOLD_NUM, HOLD_DEN;
    static Set<String> FUNCTION, MAKE, ROW_KEYS;
    static Map<String, String> NEXT;
    static String VERSION;

    @SuppressWarnings("unchecked")
    static void loadTables(Path path) throws IOException {
        Map<String, Object> t = (Map<String, Object>) Json.loads(Files.readString(path));
        WORD = new Ranges((List<Object>) t.get("word"));
        SPACE = new Ranges((List<Object>) t.get("space"));
        DIGIT = new Ranges((List<Object>) t.get("digit"));
        Map<String, Object> sigma = (Map<String, Object>) t.get("sigma");
        CASED = new Ranges((List<Object>) sigma.get("cased"));
        IGNORABLE = new Ranges((List<Object>) sigma.get("case_ignorable"));
        LOWER = new HashMap<>();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) t.get("lower")).entrySet())
            LOWER.put(Integer.parseInt(e.getKey()), (String) e.getValue());
        LINE_BREAKS = new HashSet<>();
        for (Object o : (List<Object>) t.get("line_breaks")) LINE_BREAKS.add(((BigInteger) o).intValue());
        HTML5 = new HashMap<>();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) t.get("html5")).entrySet())
            HTML5.put(e.getKey(), (String) e.getValue());
        INVALID_CHARREFS = new HashMap<>();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) t.get("invalid_charrefs")).entrySet())
            INVALID_CHARREFS.put(Integer.parseInt(e.getKey()), (String) e.getValue());
        INVALID_CODEPOINTS = new HashSet<>();
        for (Object o : (List<Object>) t.get("invalid_codepoints")) INVALID_CODEPOINTS.add(((BigInteger) o).intValue());
        Map<String, Object> kc = (Map<String, Object>) t.get("kernel");
        GENESIS = (String) kc.get("GENESIS");
        TOP = ((BigInteger) kc.get("TOP")).intValue();
        SNIFF = ((BigInteger) kc.get("SNIFF")).intValue();
        MAX_FILE = ((BigInteger) kc.get("MAX_FILE")).intValue();
        MAX_CHUNK = ((BigInteger) kc.get("MAX_CHUNK")).intValue();
        ECHO_RUN = ((BigInteger) kc.get("ECHO_RUN")).intValue();
        List<Object> hold = (List<Object>) kc.get("HOLD");
        HOLD_NUM = ((BigInteger) hold.get(0)).intValue();
        HOLD_DEN = ((BigInteger) hold.get(1)).intValue();
        FUNCTION = strings((List<Object>) kc.get("FUNCTION"));
        MAKE = strings((List<Object>) kc.get("MAKE"));
        ROW_KEYS = strings((List<Object>) kc.get("ROW_KEYS"));
        NEXT = new HashMap<>();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) kc.get("NEXT")).entrySet())
            NEXT.put(e.getKey(), (String) e.getValue());

        TAG_FOLD = new HashMap<>();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) t.get("tag_fold")).entrySet()) {
            Set<Integer> cs = new HashSet<>();
            for (Object o : (List<Object>) e.getValue()) cs.add(((BigInteger) o).intValue());
            TAG_FOLD.put(e.getKey().charAt(0), cs);
        }
        TAG_BACKREF = new HashMap<>();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) t.get("tag_backref")).entrySet()) {
            Set<Long> ps = new HashSet<>();
            for (Object o : (List<Object>) e.getValue()) {
                List<Object> pr = (List<Object>) o;
                ps.add(pair(((BigInteger) pr.get(0)).intValue(), ((BigInteger) pr.get(1)).intValue()));
            }
            TAG_BACKREF.put(e.getKey().charAt(0), ps);
        }
    }

    static Map<Character, Set<Integer>> TAG_FOLD;
    static Map<Character, Set<Long>> TAG_BACKREF;

    static long pair(int a, int b) { return ((long) a << 32) | (b & 0xffffffffL); }


    private static Set<String> strings(List<Object> xs) {
        Set<String> out = new HashSet<>();
        for (Object o : xs) out.add((String) o);
        return out;
    }

    // ── str, as Python has it ───────────────────────────────────────────────
    static int[] cps(String s) { return s.codePoints().toArray(); }

    static String fromCps(int[] a, int from, int to) { return new String(a, from, to - from); }

    static int len(String s) { return s.codePointCount(0, s.length()); }

    /** str.lower: the table, and U+03A3 by CPython's final-sigma context. */
    static String lower(String s) {
        int[] a = cps(s);
        StringBuilder b = new StringBuilder(s.length());
        for (int i = 0; i < a.length; i++) {
            int c = a[i];
            if (c == 0x03A3) {
                b.append(finalSigma(a, i) ? "ς" : "σ");
            } else {
                String l = LOWER.get(c);
                if (l != null) b.append(l);
                else b.appendCodePoint(c);
            }
        }
        return b.toString();
    }

    private static boolean finalSigma(int[] a, int i) {
        int j = i - 1;
        while (j >= 0 && IGNORABLE.has(a[j])) j--;
        if (j < 0 || !CASED.has(a[j])) return false;
        j = i + 1;
        while (j < a.length && IGNORABLE.has(a[j])) j++;
        return j == a.length || !CASED.has(a[j]);
    }

    /** str.strip() == "" */
    static boolean blank(String s) {
        return s.codePoints().allMatch(SPACE::has);
    }

    /** str.strip() */
    static String strip(String s) {
        int[] a = cps(s);
        int i = 0, j = a.length;
        while (i < j && SPACE.has(a[i])) i++;
        while (j > i && SPACE.has(a[j - 1])) j--;
        return fromCps(a, i, j);
    }

    /** str.splitlines(): "\r\n" is one break; no terminators kept. */
    static List<String> splitlines(String s) {
        List<String> out = new ArrayList<>();
        int[] a = cps(s);
        int start = 0, i = 0;
        while (i < a.length) {
            int c = a[i];
            if (LINE_BREAKS.contains(c)) {
                out.add(fromCps(a, start, i));
                i += (c == '\r' && i + 1 < a.length && a[i + 1] == '\n') ? 2 : 1;
                start = i;
            } else {
                i++;
            }
        }
        if (start < a.length) out.add(fromCps(a, start, a.length));
        return out;
    }

    static String take(String s, int n) {
        int[] a = cps(s);
        return a.length <= n ? s : fromCps(a, 0, n);
    }

    // ── words and the bite key: onescript/words.py ──────────────────────────
    static List<String> words(String text) {
        List<String> out = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (int c : cps(lower(text))) {
            if (WORD.has(c)) {
                cur.appendCodePoint(c);
            } else if (cur.length() > 0) {
                out.add(cur.toString());
                cur.setLength(0);
            }
        }
        if (cur.length() > 0) out.add(cur.toString());
        return out;
    }

    static String biteKey(String text) {
        List<String> ws = words(text);
        return ws.isEmpty() ? null : h256(String.join(" ", ws));
    }

    @SuppressWarnings("unchecked")
    static String subjectOf(Map<String, Object> prop) {
        Map<String, Object> four = new LinkedHashMap<>();
        for (String k : new String[] {"path", "data", "cites", "claim"}) four.put(k, prop.get(k));
        return "proposal:" + h256(canon(four));
    }

    // ── the pile ────────────────────────────────────────────────────────────
    // ── the scanners: the kernel's regular expressions, by hand (as in ../ts/kernel.ts) ──
    /** Python's \w: a word character or "_". */
    static boolean isW(int c) { return c == '_' || WORD.has(c); }

    private static int at(int[] a, int i) { return i < a.length ? a[i] : -1; }

    /** " " and one newline for each the match held: line numbers still point home. */
    private static String kept(int[] a, int from, int to) {
        int n = 0;
        for (int i = from; i < to; i++) if (a[i] == '\n') n++;
        return " " + "\n".repeat(n);
    }

    /** `<(script|style)\b.*?</\1\s*>|<!--.*?-->` under re.S | re.I, at `i` (a "<"):
     *  the name folds as re.I folds it (tag_fold); the closing name equals the
     *  opening one as re.I's backreference compares (tag_backref). */
    static int hiddenAt(int[] a, int i) {
        for (String name : new String[] {"script", "style"}) {
            int n = name.length();
            boolean ok = i + 1 + n <= a.length;
            for (int k = 0; ok && k < n; k++) ok = TAG_FOLD.get(name.charAt(k)).contains(a[i + 1 + k]);
            if (!ok) continue;
            int e = i + 1 + n;
            if (e < a.length && isW(a[e])) continue; // \b
            for (int k = e; k + 1 < a.length; k++) {
                if (a[k] != '<' || a[k + 1] != '/') continue;
                boolean same = k + 2 + n <= a.length;
                for (int x = 0; same && x < n; x++)
                    same = TAG_BACKREF.get(name.charAt(x)).contains(pair(a[i + 1 + x], a[k + 2 + x]));
                if (!same) continue;
                int p = k + 2 + n;
                while (p < a.length && SPACE.has(a[p])) p++;
                if (p < a.length && a[p] == '>') return p + 1;
            }
        }
        if (at(a, i + 1) == '!' && at(a, i + 2) == '-' && at(a, i + 3) == '-')
            for (int k = i + 4; k + 2 < a.length; k++) if (a[k] == '-' && a[k + 1] == '-' && a[k + 2] == '>') return k + 3;
        return -1;
    }

    static String hidden(String text) {
        int[] a = cps(text);
        StringBuilder b = new StringBuilder();
        int i = 0;
        while (i < a.length) {
            int m = a[i] == '<' ? hiddenAt(a, i) : -1;
            if (m >= 0) {
                b.append(kept(a, i, m));
                i = m;
            } else {
                b.appendCodePoint(a[i++]);
            }
        }
        return b.toString();
    }

    /** `<[^>]*>` */
    static String tags(String text) {
        int[] a = cps(text);
        StringBuilder b = new StringBuilder();
        int i = 0;
        while (i < a.length) {
            if (a[i] == '<') {
                int q = -1;
                for (int k = i + 1; k < a.length; k++) if (a[k] == '>') { q = k; break; }
                if (q >= 0) {
                    b.append(kept(a, i, q + 1));
                    i = q + 1;
                    continue;
                }
            }
            b.appendCodePoint(a[i++]);
        }
        return b.toString();
    }

    private static boolean isDec(int c) { return c >= '0' && c <= '9'; }

    private static boolean isHex(int c) { return isDec(c) || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f'); }

    private static final Set<Integer> NAME_STOP = Set.of((int) '\t', (int) '\n', (int) '\f', (int) ' ', (int) '<', (int) '&', (int) '#', (int) ';');

    /** html.unescape: `&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)`. */
    static String unescape(String s) {
        if (s.indexOf('&') < 0) return s;
        int[] a = cps(s);
        StringBuilder b = new StringBuilder();
        int i = 0;
        while (i < a.length) {
            if (a[i] != '&') {
                b.appendCodePoint(a[i++]);
                continue;
            }
            int j = i + 1;
            if (at(a, j) == '#') {
                if (isDec(at(a, j + 1))) {
                    j += 1;
                    while (j < a.length && isDec(a[j])) j++;
                } else if ((at(a, j + 1) == 'x' || at(a, j + 1) == 'X') && isHex(at(a, j + 2))) {
                    j += 2;
                    while (j < a.length && isHex(a[j])) j++;
                } else {
                    b.append('&');
                    i++;
                    continue;
                }
            } else {
                while (j < a.length && j - (i + 1) < 32 && !NAME_STOP.contains(a[j])) j++;
                if (j == i + 1) {
                    b.append('&');
                    i++;
                    continue;
                }
            }
            if (at(a, j) == ';') j++;
            b.append(charref(fromCps(a, i + 1, j)));
            i = j;
        }
        return b.toString();
    }

    private static final String[] KEYWORDS = {"def", "function", "sub", "class", "func", "fn"};

    private static boolean identHead(int c) { return c == '_' || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }

    private static boolean identTail(int c) { return identHead(c) || isDec(c); }

    /** re.findall of `\b(?:def|function|sub|class|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)`. */
    static List<String> defines(String text) {
        int[] a = cps(text);
        List<String> out = new ArrayList<>();
        int p = 0;
        outer:
        while (p < a.length) {
            if (p == 0 || !isW(a[p - 1])) {
                for (String kw : KEYWORDS) {
                    int k = 0;
                    while (k < kw.length() && at(a, p + k) == kw.charAt(k)) k++;
                    if (k < kw.length()) continue;
                    int q = p + kw.length(), s0 = q;
                    while (q < a.length && SPACE.has(a[q])) q++;
                    if (q == s0 || !(q < a.length && identHead(a[q]))) continue;
                    int start = q;
                    while (q < a.length && identTail(a[q])) q++;
                    out.add(fromCps(a, start, q));
                    p = q;
                    continue outer;
                }
            }
            p++;
        }
        return out;
    }

    /** How many times `\bname\b` occurs (name is an identifier: \w all through). */
    static int named(String text, String name) {
        int[] a = cps(text), b = cps(name);
        int n = 0, p = 0;
        while (p + b.length <= a.length) {
            int k = 0;
            while (k < b.length && a[p + k] == b[k]) k++;
            if (k == b.length && (p == 0 || !isW(a[p - 1])) && (p + b.length == a.length || !isW(a[p + b.length]))) {
                n++;
                p += b.length;
            } else {
                p++;
            }
        }
        return n;
    }

    private static String charref(String s) {
        if (s.charAt(0) == '#') {
            String digits = s.charAt(1) == 'x' || s.charAt(1) == 'X' ? s.substring(2) : s.substring(1);
            while (digits.endsWith(";")) digits = digits.substring(0, digits.length() - 1);
            BigInteger num = new BigInteger(digits, s.charAt(1) == 'x' || s.charAt(1) == 'X' ? 16 : 10);
            if (num.compareTo(BigInteger.valueOf(0x10FFFF)) > 0) return "�";
            int n = num.intValue();
            if (INVALID_CHARREFS.containsKey(n)) return INVALID_CHARREFS.get(n);
            if (n >= 0xD800 && n <= 0xDFFF) return "�";
            if (INVALID_CODEPOINTS.contains(n)) return "";
            return new String(Character.toChars(n));
        }
        if (HTML5.containsKey(s)) return HTML5.get(s);
        int[] a = cps(s);
        for (int x = a.length - 1; x > 1; x--) {
            String head = fromCps(a, 0, x);
            if (HTML5.containsKey(head)) return HTML5.get(head) + fromCps(a, x, a.length);
        }
        return "&" + s;
    }

    static String htmlText(String text) {
        return unescape(tags(hidden(text)));
    }

    static boolean holds(String w, Set<String> toks) {
        if (toks.contains(w)) return true;
        if (len(w) < 4 || !w.codePoints().anyMatch(DIGIT::has)) return false;
        for (String t : toks) if (t.endsWith(w) || t.startsWith(w)) return true;
        return false;
    }

    static boolean echo(String bite, String text) {
        List<String> ws = words(bite), ts = words(text);
        int n = Math.min(ws.size(), ECHO_RUN);
        if (n < 3) return false;
        Set<List<String>> runs = new HashSet<>();
        for (int i = 0; i + n <= ts.size(); i++) runs.add(ts.subList(i, i + n));
        for (int i = 0; i + n <= ws.size(); i++) if (runs.contains(ws.subList(i, i + n))) return true;
        return false;
    }

    static final class Chunk {
        final String path, text, id;
        final int line;

        Chunk(String path, int line, String text) {
            this.path = path;
            this.line = line;
            this.text = text;
            this.id = h256(text);
        }

        Map<String, Object> json() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("path", path);
            m.put("line", BigInteger.valueOf(line));
            m.put("text", text);
            m.put("id", id);
            return m;
        }
    }

    /** Path.rglob("*"), sorted as Python sorts paths: by their parts. */
    private static void walk(Path dir, List<String> parts, List<List<String>> out) throws IOException {
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir)) {
            for (Path p : ds) {
                List<String> mine = new ArrayList<>(parts);
                mine.add(p.getFileName().toString());
                out.add(mine);
                if (Files.isDirectory(p, LinkOption.NOFOLLOW_LINKS)) walk(p, mine, out);
            }
        }
    }

    static final Comparator<List<String>> BY_PARTS = (a, b) -> {
        for (int i = 0; i < Math.min(a.size(), b.size()); i++) {
            int c = BY_CODE_POINT.compare(a.get(i), b.get(i));
            if (c != 0) return c;
        }
        return Integer.compare(a.size(), b.size());
    };

    static String suffix(String name) {
        int i = name.lastIndexOf('.');
        return (i > 0 && i < name.length() - 1) ? name.substring(i) : "";
    }

    static List<Chunk> chunks(Path pile) throws IOException {
        List<List<String>> all = new ArrayList<>();
        walk(pile, new ArrayList<>(), all);
        all.sort(BY_PARTS);
        List<Chunk> out = new ArrayList<>();
        for (List<String> rel : all) {
            Path p = pile;
            for (String part : rel) p = p.resolve(part);
            if (!Files.isRegularFile(p)) continue;
            boolean hidden = false;
            for (String part : rel) hidden |= part.startsWith(".");
            if (hidden) continue;
            String text;
            try {
                if (Files.size(p) > MAX_FILE) continue;
                byte[] raw = Files.readAllBytes(p);
                boolean nul = false;
                for (int i = 0; i < Math.min(raw.length, SNIFF); i++) nul |= raw[i] == 0;
                if (nul) continue;
                text = StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(raw)).toString();
            } catch (CharacterCodingException e) {
                continue;
            } catch (IOException e) {
                continue;
            }
            String name = rel.get(rel.size() - 1);
            String suf = suffix(name).toLowerCase(java.util.Locale.ROOT);
            if (suf.equals(".html") || suf.equals(".htm")) text = htmlText(text);
            String relPath = String.join("/", rel);
            List<String> lines = new ArrayList<>(splitlines(text));
            lines.add("");
            List<String> para = new ArrayList<>();
            int start = 1, size = 0;
            for (int i = 1; i <= lines.size(); i++) {
                String line = lines.get(i - 1);
                if (!para.isEmpty() && (blank(line) || size + len(line) > MAX_CHUNK)) {
                    out.add(new Chunk(relPath, start, String.join("\n", para)));
                    para.clear();
                    size = 0;
                }
                if (!blank(line)) {
                    if (para.isEmpty()) start = i;
                    para.add(take(line, MAX_CHUNK));
                    size += len(para.get(para.size() - 1)) + 1;
                }
            }
        }
        return out;
    }

    /** An exact rational, for the rank's scores. */
    static final class Q implements Comparable<Q> {
        final BigInteger n, d;

        Q(BigInteger n, BigInteger d) {
            BigInteger g = n.gcd(d);
            this.n = g.signum() == 0 ? n : n.divide(g);
            this.d = g.signum() == 0 ? d : d.divide(g);
        }

        Q plus(Q o) { return new Q(n.multiply(o.d).add(o.n.multiply(d)), d.multiply(o.d)); }

        @Override public int compareTo(Q o) { return n.multiply(o.d).compareTo(o.n.multiply(d)); }
    }

    static List<Chunk> rank(List<Chunk> pile, String bite) {
        Set<String> want = new LinkedHashSet<>(words(bite));
        want.removeAll(FUNCTION);
        if (want.isEmpty()) return new ArrayList<>();
        List<Chunk> heldC = new ArrayList<>();
        List<Set<String>> heldH = new ArrayList<>();
        for (Chunk c : pile) {
            Set<String> toks = new HashSet<>(words(c.text));
            Set<String> h = new HashSet<>();
            for (String w : want) if (holds(w, toks)) h.add(w);
            if ((long) h.size() * HOLD_DEN >= (long) HOLD_NUM * want.size()) {
                heldC.add(c);
                heldH.add(h);
            }
        }
        Map<String, Integer> df = new HashMap<>();
        for (String w : want) {
            int n = 0;
            for (Set<String> h : heldH) if (h.contains(w)) n++;
            df.put(w, n);
        }
        List<Object[]> scored = new ArrayList<>();
        for (int i = 0; i < heldC.size(); i++) {
            Set<String> h = heldH.get(i);
            if (h.isEmpty()) continue;
            Q s = new Q(BigInteger.ZERO, BigInteger.ONE);
            for (String w : h) s = s.plus(new Q(BigInteger.ONE, BigInteger.valueOf(df.get(w))));
            scored.add(new Object[] {s, heldC.get(i)});
        }
        scored.sort((x, y) -> {
            int c = ((Q) y[0]).compareTo((Q) x[0]);
            if (c != 0) return c;
            Chunk a = (Chunk) x[1], b = (Chunk) y[1];
            c = BY_CODE_POINT.compare(a.path, b.path);
            return c != 0 ? c : Integer.compare(a.line, b.line);
        });
        List<Chunk> out = new ArrayList<>();
        for (int i = 0; i < Math.min(TOP, scored.size()); i++) out.add((Chunk) scored.get(i)[1]);
        return out;
    }

    static List<Map<String, Object>> look(List<Chunk> pile, String bite, List<Chunk> served) {
        List<Map<String, Object>> out = new ArrayList<>();
        List<String> ws = words(bite);
        List<String> want = new ArrayList<>();
        for (String w : new LinkedHashSet<>(ws)) if (!FUNCTION.contains(w)) want.add(w);
        if (want.isEmpty()) out.add(hint("every word is a common one: name the thing you mean", null, null));
        Set<String> every = new HashSet<>();
        for (Chunk c : pile) every.addAll(words(c.text));
        List<String> absent = new ArrayList<>();
        for (String w : want) if (!holds(w, every)) absent.add(w);
        if (!absent.isEmpty())
            out.add(hint("no file in your folder has " + String.join(", ", absent),
                "it may be called something else there", NEXT.get("other words")));
        for (Chunk c : served) {
            for (String name : new LinkedHashSet<>(defines(c.text))) {
                TreeSet<String> uses = new TreeSet<>(BY_CODE_POINT);
                for (Chunk d : pile) if (d != c && named(d.text, name) > 0) uses.add(d.path);
                if (named(c.text, name) > 1) uses.add(c.path);
                out.add(hint(name + " is defined at " + c.path + ":" + c.line,
                    uses.isEmpty() ? "nothing else in your folder names it" : "also named in " + String.join(", ", uses),
                    NEXT.get("unused")));
            }
        }
        if (!ws.isEmpty() && MAKE.contains(ws.get(0))) out.add(hint("this asks for something made", null, NEXT.get("made")));
        return out;
    }

    private static Map<String, Object> hint(String why, String where, String next) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("why", why);
        if (where != null) m.put("where", where);
        if (next != null) m.put("next", next);
        return m;
    }

    // ── the door ────────────────────────────────────────────────────────────
    @SuppressWarnings("unchecked")
    static String door(Object prop, Set<String> served) {
        Set<String> keys = Set.of("path", "data", "cites", "claim");
        if (!(prop instanceof Map) || !((Map<String, Object>) prop).keySet().equals(keys))
            return "a row is exactly {path, data, cites, claim}";
        Map<String, Object> p = (Map<String, Object>) prop;
        Object path = p.get("path");
        if (!(path instanceof String) || ((String) path).isEmpty()
            || ((String) path).startsWith("/") || ((String) path).startsWith("\\")
            || Arrays.asList(((String) path).split("/", -1)).contains(".."))
            return "a path is plain and relative, inside the box";
        if (!(p.get("data") instanceof String) || !(p.get("cites") instanceof List))
            return "data is text and cites a list";
        if (!(p.get("claim") instanceof String) || blank((String) p.get("claim")))
            return "a claim says why";
        for (Object c : (List<Object>) p.get("cites"))
            if (!(c instanceof String) || !served.contains(c)) return "a cite that the last bite didn't serve";
        return null;
    }

    // ── the record: genesis's, in the one script's format ───────────────────
    static final class Read {
        final List<Map<String, Object>> rows = new ArrayList<>();
        final List<String> breaks = new ArrayList<>();
    }

    @SuppressWarnings("unchecked")
    static Read read(Path path) throws IOException {
        Read r = new Read();
        if (!Files.exists(path)) return r;
        String data;
        try {
            data = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(Files.readAllBytes(path))).toString();
        } catch (CharacterCodingException e) {
            throw new IOException("the record is not UTF-8");
        }
        if (!data.isEmpty() && !data.endsWith("\n")) {
            r.breaks.add("torn tail: the last line is unterminated; a human looks first");
            return r;
        }
        String prev = GENESIS;
        String[] lines = data.isEmpty() ? new String[0] : data.substring(0, data.length() - 1).split("\n", -1);
        for (int i = 0; i < lines.length; i++) {
            Object row;
            try {
                row = Json.loads(lines[i]);
            } catch (RuntimeException e) {
                row = null;
            }
            if (!(row instanceof Map) || !((Map<String, Object>) row).keySet().containsAll(ROW_KEYS)) {
                r.breaks.add("line " + i + ": garbled, not a record row");
                continue;
            }
            Map<String, Object> m = (Map<String, Object>) row;
            Map<String, Object> body = new LinkedHashMap<>(m);
            body.remove("hash");
            String hash;
            try {
                hash = h256(canon(body));
            } catch (IllegalArgumentException e) {
                hash = "";
            }
            if (!Objects.equals(m.get("prev"), prev) || !Objects.equals(hash, m.get("hash")))
                r.breaks.add("row " + pyStr(m.get("n")) + ": chain broken");
            Object n = m.get("n");
            if (!(n instanceof BigInteger) || !n.equals(BigInteger.valueOf(r.rows.size())))
                r.breaks.add("row " + pyRepr(n) + ": out of place, it is row " + r.rows.size());
            r.rows.add(m);
            prev = m.get("hash") instanceof String ? (String) m.get("hash") : String.valueOf(m.get("hash"));
        }
        return r;
    }

    private static String pyRepr(Object o) {
        if (o instanceof String) return "'" + o + "'";
        if (o instanceof Boolean) return ((Boolean) o) ? "True" : "False";
        return String.valueOf(o);
    }

    static Map<String, Object> append(Path box, String ts, String kind, String who, Map<String, Object> fields)
        throws IOException {
        Path path = box.resolve("record.jsonl");
        Map<String, Object> row;
        try (FileChannel f = FileChannel.open(path, StandardOpenOption.CREATE, StandardOpenOption.WRITE,
                 StandardOpenOption.APPEND);
             FileLock held = f.lock()) {
            assert held.isValid();
            Read r = read(path); // the true tail, read under the lock
            if (!r.breaks.isEmpty()) throw new IOException(String.join("; ", r.breaks));
            boolean sealed = kind.equals("seal") && who.equals("human");
            row = new LinkedHashMap<>();
            row.put("n", BigInteger.valueOf(r.rows.size()));
            row.put("kind", kind);
            row.put("who", who);
            row.put("family", "kernel");
            row.put("standing", sealed ? "sealed" : "unattested");
            row.put("version", VERSION);
            row.put("ts", ts);
            Map<String, Object> rest = new LinkedHashMap<>(fields);
            row.put("where", rest.containsKey("where") ? rest.remove("where") : NULL);
            row.putAll(rest);
            row.put("prev", r.rows.isEmpty() ? GENESIS : r.rows.get(r.rows.size() - 1).get("hash"));
            row.put("hash", h256(canon(row)));
            f.write(ByteBuffer.wrap((canon(row) + "\n").getBytes(StandardCharsets.UTF_8)));
            f.force(true);
        }
        if (((BigInteger) row.get("n")).intValue() == 0) { // the new name survives a crash
            try (FileChannel d = FileChannel.open(box, StandardOpenOption.READ)) {
                d.force(true);
            } catch (IOException | UnsupportedOperationException e) {
                // a directory that can't be opened here: as kernel.py without O_DIRECTORY
            }
        }
        return row;
    }

    static Map<String, Object> fields(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) m.put((String) kv[i], kv[i + 1] == null ? NULL : kv[i + 1]);
        return m;
    }

    // ── the box: theirs ─────────────────────────────────────────────────────
    @SuppressWarnings("unchecked")
    static Map<String, Object> config(Path box) throws IOException {
        return (Map<String, Object>) Json.loads(Files.readString(box.resolve("kernel.json")));
    }

    /** Path.resolve(): absolute, symlinks resolved as far as the path exists. */
    static Path resolved(Path p) {
        Path abs = p.toAbsolutePath().normalize();
        Path probe = abs;
        List<String> tail = new ArrayList<>();
        while (probe != null && !Files.exists(probe)) {
            tail.add(0, probe.getFileName().toString());
            probe = probe.getParent();
        }
        try {
            Path real = probe == null ? abs.getRoot() : probe.toRealPath();
            for (String t : tail) real = real.resolve(t);
            return real;
        } catch (IOException e) {
            return abs;
        }
    }

    static boolean inside(Path p, Path box) { return resolved(p).startsWith(resolved(box)); }

    static final class Key {
        final byte[] key;
        final String why;

        Key(byte[] key, String why) { this.key = key; this.why = why; }
    }

    static Key readKey(Path path, Path box) {
        if (inside(path, box)) return new Key(null, "the key can't live inside the box");
        try {
            try {
                Set<PosixFilePermission> perms = Files.getPosixFilePermissions(path);
                for (PosixFilePermission x : perms)
                    if (x.name().startsWith("GROUP") || x.name().startsWith("OTHERS"))
                        return new Key(null, path + " is readable beyond its owner; chmod 600 it");
            } catch (UnsupportedOperationException e) {
                // not POSIX: as kernel.py, no mode check
            }
            return new Key(unhex(strip(Files.readString(path))), null);
        } catch (IOException e) {
            return new Key(null, "the key could not be read: " + pyErr(e));
        } catch (RuntimeException e) {
            return new Key(null, "the key could not be read: ValueError");
        }
    }

    private static String pyErr(IOException e) {
        if (e instanceof java.nio.file.NoSuchFileException) return "FileNotFoundError";
        if (e instanceof java.nio.file.AccessDeniedException) return "PermissionError";
        return "OSError";
    }

    static int init(Path box, Path pile, Path key, String ts) throws IOException {
        if (Files.exists(box.resolve("record.jsonl"))) {
            System.err.println("refused: this box already has a record");
            return REFUSED;
        }
        if (!Files.isDirectory(pile)) {
            System.err.println("refused: " + pile + " is not a folder");
            return REFUSED;
        }
        if (inside(key, box)) {
            System.err.println("refused: the key can't live inside the box");
            return REFUSED;
        }
        if (!Files.exists(key)) {
            if (key.toAbsolutePath().getParent() != null) Files.createDirectories(key.toAbsolutePath().getParent());
            byte[] k = new byte[32];
            new SecureRandom().nextBytes(k);
            try {
                Files.createFile(key, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
            } catch (UnsupportedOperationException e) {
                Files.createFile(key);
            }
            Files.writeString(key, hex(k) + "\n");
        }
        Files.createDirectories(box);
        Map<String, Object> cfg = new LinkedHashMap<>();
        cfg.put("pile", resolved(pile).toString());
        cfg.put("key", resolved(key).toString());
        Files.writeString(box.resolve("kernel.json"), dumpsDefault(cfg, true) + "\n");
        Map<String, Object> row = append(box, ts, "boot", "kernel",
            fields("loop", "open", "pile_sha256", h256((String) cfg.get("pile"))));
        System.out.println("open loop, row " + row.get("n") + " " + ((String) row.get("hash")).substring(0, 16)
            + ": the box is yours");
        return ALIVE;
    }

    @SuppressWarnings("unchecked")
    static Map<String, Map<String, Object>> sealed(Path box, List<Map<String, Object>> rows) throws IOException {
        Key k = readKey(Path.of((String) config(box).get("key")), box);
        Map<String, Map<String, Object>> out = new LinkedHashMap<>();
        for (Map<String, Object> r : rows) {
            if ("seal".equals(r.get("kind")) && k.key != null) {
                Object subj = r.containsKey("subject") ? r.get("subject") : "";
                String want = proof(k.key, subj instanceof String ? (String) subj : String.valueOf(subj));
                Object got = r.containsKey("proof") ? r.get("proof") : NULL;
                if (MessageDigest.isEqual(want.getBytes(StandardCharsets.UTF_8),
                        pyStr(got).getBytes(StandardCharsets.UTF_8)))
                    out.put((String) r.get("subject"), r);
            }
        }
        return out;
    }

    private static String pyStr(Object o) {
        if (o == NULL) return "None";
        if (o instanceof Boolean) return ((Boolean) o) ? "True" : "False";
        return String.valueOf(o);
    }

    private static boolean refusedBreaks(Read r) {
        if (r.breaks.isEmpty()) return false;
        StringBuilder b = new StringBuilder("refused:");
        for (String x : r.breaks) b.append("\n  ").append(x);
        System.err.println(b);
        return true;
    }

    private static Path propFile(Path box, String subj) { return box.resolve("proposals").resolve(subj.substring(9) + ".json"); }

    @SuppressWarnings("unchecked")
    static int bite(Path box, String text, String ts) throws IOException {
        Read r = read(box.resolve("record.jsonl"));
        if (refusedBreaks(r)) return REFUSED;
        String key = biteKey(text);
        if (key == null) {
            System.err.println("refused: a bite with no words");
            return REFUSED;
        }
        for (String subj : sealed(box, r.rows).keySet()) { // a sealed answer for these words, first
            Map<String, Object> prop = (Map<String, Object>) Json.loads(Files.readString(propFile(box, subj)));
            if (key.equals(prop.get("bite_key"))) {
                append(box, ts, "bite", "kernel", fields("bite_key", key, "answered_by", subj, "served", new ArrayList<>()));
                Map<String, Object> doc = new LinkedHashMap<>();
                doc.put("state", "sealed");
                doc.put("answer", prop.get("data"));
                doc.put("by", subj);
                System.out.println(dumpsSpacedUtf8(doc));
                return ALIVE;
            }
        }
        List<Chunk> pile = chunks(Path.of((String) config(box).get("pile")));
        List<Chunk> served = rank(pile, text);
        List<Object> refs = new ArrayList<>();
        List<Chunk> answers = new ArrayList<>();
        for (Chunk c : served) {
            boolean e = echo(text, c.text);
            refs.add(fields("id", c.id, "where", c.path + ":" + c.line, "echo", e));
            if (!e) answers.add(c);
        }
        append(box, ts, "bite", "kernel", fields("bite_key", key, "served", refs));
        String state = !answers.isEmpty() ? "populated" : !refs.isEmpty() ? "echo" : "empty";
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("state", state);
        doc.put("bite_key", key);
        List<Object> shown = new ArrayList<>();
        for (int i = 0; i < served.size(); i++) {
            Map<String, Object> m = new LinkedHashMap<>((Map<String, Object>) refs.get(i));
            m.put("text", served.get(i).text);
            shown.add(m);
        }
        doc.put("served", shown);
        List<Map<String, Object>> hints = look(pile, text, answers.isEmpty() ? new ArrayList<>() : served);
        if (!hints.isEmpty()) doc.put("look", hints);
        System.out.println(dumpsSpacedUtf8(doc));
        return answers.isEmpty() ? ESCALATE : ALIVE;
    }

    /** json.dumps(doc, ensure_ascii=False): what the person reads. */
    static String dumpsSpacedUtf8(Object o) {
        // the spaced form with non-ASCII kept: re-quote the ascii form's escapes is
        // lossy, so dump directly
        StringBuilder b = new StringBuilder();
        spacedUtf8(o, b);
        return b.toString();
    }

    @SuppressWarnings("unchecked")
    private static void spacedUtf8(Object o, StringBuilder b) {
        if (o instanceof List) {
            b.append('[');
            boolean first = true;
            for (Object v : (List<Object>) o) {
                if (!first) b.append(", ");
                first = false;
                spacedUtf8(v, b);
            }
            b.append(']');
        } else if (o instanceof Map) {
            b.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> e : ((Map<String, Object>) o).entrySet()) {
                if (!first) b.append(", ");
                first = false;
                quote(e.getKey(), b, false);
                b.append(": ");
                spacedUtf8(e.getValue(), b);
            }
            b.append('}');
        } else {
            dump(o, b, false, false);
        }
    }

    @SuppressWarnings("unchecked")
    static int propose(Path box, List<String> lines, String ts) throws IOException {
        Read r = read(box.resolve("record.jsonl"));
        if (refusedBreaks(r)) return REFUSED;
        Map<String, Object> last = null;
        for (int i = r.rows.size() - 1; i >= 0; i--)
            if ("bite".equals(r.rows.get(i).get("kind"))) { last = r.rows.get(i); break; }
        if (last == null) {
            System.err.println("refused: propose answers a bite; bite first");
            return REFUSED;
        }
        Set<String> served = new HashSet<>();
        Object sv = last.get("served");
        if (sv instanceof List) for (Object s : (List<Object>) sv) served.add((String) ((Map<String, Object>) s).get("id"));
        int waiting = 0;
        for (int i = 0; i < lines.size(); i++) {
            Object prop;
            try {
                prop = Json.loads(lines.get(i));
            } catch (RuntimeException e) {
                prop = null;
            }
            String why = door(prop, served);
            if (why == null) {
                Map<String, Object> p = (Map<String, Object>) prop;
                String subj = subjectOf(p);
                Files.createDirectories(box.resolve("proposals"));
                Map<String, Object> kept = new LinkedHashMap<>(p);
                kept.put("bite_key", last.get("bite_key"));
                Files.writeString(propFile(box, subj), dumpsDefault(kept, false));
                append(box, ts, "proposal", "kernel", fields("subject", subj, "verdict", "pass", "bite_row", last.get("n")));
                System.out.println("pass  " + subj + "  waiting on your seal");
                waiting++;
            } else {
                append(box, ts, "proposal", "kernel", fields("verdict", "refused", "line", BigInteger.valueOf(i), "why", why));
                System.out.println("refused  line " + i + ": " + why);
            }
        }
        return waiting > 0 ? ESCALATE : ALIVE;
    }

    @SuppressWarnings("unchecked")
    static int seal(Path box, String subj, String ts) throws IOException {
        Read r = read(box.resolve("record.jsonl"));
        if (refusedBreaks(r)) return REFUSED;
        boolean known = false;
        for (Map<String, Object> row : r.rows)
            known |= "proposal".equals(row.get("kind")) && subj.equals(row.get("subject"));
        if (!known) {
            System.err.println("refused: " + subj + " is not a proposal in this box");
            return REFUSED;
        }
        Key k = readKey(Path.of((String) config(box).get("key")), box);
        if (k.key == null) {
            System.err.println("refused: " + k.why);
            return REFUSED;
        }
        Map<String, Object> prop = (Map<String, Object>) Json.loads(Files.readString(propFile(box, subj)));
        if (!subjectOf(prop).equals(subj)) {
            System.err.println("refused: the proposal's bytes changed since it was proposed");
            return REFUSED;
        }
        append(box, ts, "seal", "human", fields("subject", subj, "proof", proof(k.key, subj)));
        Path out = box.resolve("written").resolve((String) prop.get("path"));
        Files.createDirectories(out.getParent());
        Files.writeString(out, (String) prop.get("data"));
        System.out.println("sealed " + subj + "; written to " + box.relativize(out));
        return ALIVE;
    }

    static int verify(Path box) throws IOException {
        Read r = read(box.resolve("record.jsonl"));
        if (!r.breaks.isEmpty()) {
            System.out.println(String.join("\n", r.breaks));
            return REFUSED;
        }
        Map<String, Map<String, Object>> good = sealed(box, r.rows);
        List<String> forged = new ArrayList<>();
        for (Map<String, Object> row : r.rows)
            if ("seal".equals(row.get("kind")) && !good.containsKey(row.get("subject"))) forged.add(String.valueOf(row.get("n")));
        if (!forged.isEmpty()) {
            System.out.println("the chain holds; seal rows [" + String.join(", ", forged) + "] don't prove under your key");
            return ESCALATE;
        }
        System.out.println("the chain holds: " + r.rows.size() + " rows, " + good.size() + " sealed");
        return ALIVE;
    }

    static int out(Path box, String ts) throws IOException {
        Read r = read(box.resolve("record.jsonl"));
        if (refusedBreaks(r)) return REFUSED;
        Map<String, Map<String, Object>> good = sealed(box, r.rows);
        List<String> waiting = new ArrayList<>();
        for (Map<String, Object> row : r.rows)
            if ("proposal".equals(row.get("kind")) && "pass".equals(row.get("verdict")) && !good.containsKey(row.get("subject")))
                waiting.add((String) row.get("subject"));
        Object tip = r.rows.isEmpty() ? GENESIS : r.rows.get(r.rows.size() - 1).get("hash");
        append(box, ts, "out", "kernel", fields("waiting", BigInteger.valueOf(waiting.size()), "tip", tip));
        for (String s : waiting) System.out.println("waiting on your seal: " + s);
        System.out.println((r.rows.size() + 1) + " rows; " + good.size() + " sealed; " + waiting.size() + " waiting");
        return waiting.isEmpty() ? ALIVE : ESCALATE;
    }

    // ── the vectors: every case, answered, for compare.py ───────────────────
    @SuppressWarnings("unchecked")
    static int vectors(Path cases, Path results) throws IOException {
        Map<String, Object> doc = (Map<String, Object>) Json.loads(Files.readString(cases));
        Map<String, Object> c = (Map<String, Object>) doc.get("cases");
        Map<String, Object> out = new LinkedHashMap<>();

        List<Object> canonOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("canon")) {
            String s = canon(((Map<String, Object>) o).get("in"));
            canonOut.add(fields("canon", s, "sha256", h256(s)));
        }
        out.put("canon", canonOut);

        List<Object> wordsOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("words")) {
            String t = (String) ((Map<String, Object>) o).get("text");
            wordsOut.add(fields("words", new ArrayList<Object>(words(t)), "bite_key", biteKey(t)));
        }
        out.put("words", wordsOut);

        Map<String, Object> hm = (Map<String, Object>) c.get("hmac");
        List<Object> rfc = new ArrayList<>(), seals = new ArrayList<>();
        for (Object o : (List<Object>) hm.get("rfc4231")) {
            Map<String, Object> m = (Map<String, Object>) o;
            rfc.add(fields("mac", hmac(unhex((String) m.get("key")), unhex((String) m.get("msg")))));
        }
        for (Object o : (List<Object>) hm.get("seal")) {
            Map<String, Object> m = (Map<String, Object>) o;
            seals.add(fields("proof", proof(unhex((String) m.get("key")), (String) m.get("subject"))));
        }
        out.put("hmac", fields("rfc4231", rfc, "seal", seals));

        List<Object> htmlOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("html")) htmlOut.add(fields("out", htmlText((String) ((Map<String, Object>) o).get("in"))));
        out.put("html", htmlOut);

        List<Object> holdsOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("holds")) {
            Map<String, Object> m = (Map<String, Object>) o;
            Set<String> toks = new HashSet<>();
            for (Object t : (List<Object>) m.get("toks")) toks.add((String) t);
            holdsOut.add(fields("out", holds((String) m.get("w"), toks)));
        }
        out.put("holds", holdsOut);

        List<Object> echoOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("echo")) {
            Map<String, Object> m = (Map<String, Object>) o;
            echoOut.add(fields("out", echo((String) m.get("bite"), (String) m.get("text"))));
        }
        out.put("echo", echoOut);

        List<List<Chunk>> piles = new ArrayList<>();
        List<Object> chunksOut = new ArrayList<>();
        Path tmp = Files.createTempDirectory("kernel-vectors");
        try {
            int n = 0;
            for (Object o : (List<Object>) c.get("chunks")) {
                Path root = tmp.resolve("pile" + n++);
                Files.createDirectories(root);
                for (Object f : (List<Object>) ((Map<String, Object>) o).get("pile")) {
                    Map<String, Object> fm = (Map<String, Object>) f;
                    Path p = root;
                    for (Object part : (List<Object>) fm.get("parts")) p = p.resolve((String) part);
                    Files.createDirectories(p.getParent());
                    if (fm.containsKey("b64")) {
                        Files.write(p, Base64.getDecoder().decode((String) fm.get("b64")));
                    } else {
                        String body = ((String) fm.get("repeat")).repeat(((BigInteger) fm.get("n")).intValue()) + fm.get("then");
                        Files.write(p, body.getBytes(StandardCharsets.UTF_8));
                    }
                }
                List<Chunk> ch = chunks(root);
                piles.add(ch);
                List<Object> js = new ArrayList<>();
                for (Chunk x : ch) js.add(x.json());
                chunksOut.add(fields("chunks", js));
            }
        } finally {
            deleteTree(tmp);
        }
        out.put("chunks", chunksOut);

        List<Object> rankOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("rank")) {
            Map<String, Object> m = (Map<String, Object>) o;
            List<Object> ids = new ArrayList<>();
            for (Chunk x : rank(piles.get(((BigInteger) m.get("pile")).intValue()), (String) m.get("bite"))) ids.add(x.id);
            rankOut.add(fields("ids", ids));
        }
        out.put("rank", rankOut);

        List<Object> lookOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("look")) {
            Map<String, Object> m = (Map<String, Object>) o;
            List<Chunk> pile = piles.get(((BigInteger) m.get("pile")).intValue());
            List<Chunk> served = new ArrayList<>();
            for (Object at : (List<Object>) m.get("served")) served.add(pile.get(((BigInteger) at).intValue()));
            lookOut.add(fields("look", new ArrayList<Object>(look(pile, (String) m.get("bite"), served))));
        }
        out.put("look", lookOut);

        List<Object> doorOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("door")) {
            Map<String, Object> m = (Map<String, Object>) o;
            Object prop;
            try {
                prop = Json.loads((String) m.get("line"));
            } catch (RuntimeException e) {
                prop = null;
            }
            Set<String> served = new HashSet<>();
            for (Object s : (List<Object>) m.get("served")) served.add((String) s);
            doorOut.add(fields("why", door(prop, served)));
        }
        out.put("door", doorOut);

        List<Object> subjOut = new ArrayList<>();
        for (Object o : (List<Object>) c.get("subject"))
            subjOut.add(fields("subject", subjectOf((Map<String, Object>) ((Map<String, Object>) o).get("prop"))));
        out.put("subject", subjOut);

        Files.writeString(results, canon(out) + "\n");
        return ALIVE;
    }

    static void deleteTree(Path p) throws IOException {
        if (Files.isDirectory(p, LinkOption.NOFOLLOW_LINKS)) {
            try (DirectoryStream<Path> ds = Files.newDirectoryStream(p)) {
                for (Path x : ds) deleteTree(x);
            }
        }
        Files.deleteIfExists(p);
    }

    // ── the command line ────────────────────────────────────────────────────
    static String now() {
        return ZonedDateTime.now(ZoneOffset.UTC).format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'"));
    }

    static final String USAGE = "kernel.py: the smallest script that runs the whole loop, for anyone. Python 3.9+, stdlib only.";

    static Path tablesPath() {
        String p = System.getProperty("kernel.tables");
        if (p == null) p = System.getenv("KERNEL_TABLES");
        return Path.of(p == null ? "kernel_tables.json" : p);
    }

    static String selfVersion() {
        try (InputStream in = Kernel.class.getResourceAsStream("Kernel.class")) {
            if (in == null) return GENESIS;
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            in.transferTo(b);
            return sha256(b.toByteArray());
        } catch (IOException e) {
            return GENESIS;
        }
    }

    static int main2(String[] argv) throws IOException {
        loadTables(tablesPath());
        VERSION = selfVersion();
        if (argv.length >= 1 && argv[0].equals("vectors")) {
            if (argv.length != 3) {
                System.err.println("vectors CASES.json RESULTS.json");
                return REFUSED;
            }
            return vectors(Path.of(argv[1]), Path.of(argv[2]));
        }
        String verb = argv.length > 0 ? argv[0] : "";
        Map<String, String> opts = new HashMap<>();
        List<String> args = new ArrayList<>();
        for (int i = 1; i < argv.length; i++) {
            String a = argv[i];
            if (List.of("--box", "--pile", "--key", "--file", "--now").contains(a)) {
                opts.put(a, i + 1 < argv.length ? argv[++i] : "");
            } else {
                args.add(a);
            }
        }
        if (opts.getOrDefault("--box", "").isEmpty()) {
            System.err.println(USAGE);
            return REFUSED;
        }
        Path box = Path.of(opts.get("--box"));
        String ts = opts.getOrDefault("--now", "");
        if (ts.isEmpty()) ts = now();
        if (verb.equals("init") && !opts.getOrDefault("--pile", "").isEmpty()
            && !opts.getOrDefault("--key", "").isEmpty() && args.isEmpty())
            return init(box, Path.of(opts.get("--pile")), Path.of(opts.get("--key")), ts);
        if (!Files.exists(box.resolve("kernel.json"))) {
            System.err.println("refused: not a kernel box; init first");
            return REFUSED;
        }
        if (verb.equals("bite") && args.size() == 1) return bite(box, args.get(0), ts);
        if (verb.equals("propose") && !opts.getOrDefault("--file", "").isEmpty() && args.isEmpty())
            return propose(box, splitlines(Files.readString(Path.of(opts.get("--file")))), ts);
        if (verb.equals("seal") && args.size() == 1 && args.get(0).startsWith("proposal:")) return seal(box, args.get(0), ts);
        if (verb.equals("verify") && args.isEmpty()) return verify(box);
        if (verb.equals("out") && args.isEmpty()) return out(box, ts);
        System.err.println(USAGE);
        return REFUSED;
    }

    public static void main(String[] argv) {
        int code;
        try {
            code = main2(argv);
        } catch (IOException e) {
            System.err.println("refused: " + e.getMessage());
            code = REFUSED;
        }
        System.out.flush();
        System.exit(code);
    }
}
