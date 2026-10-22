/*
 * kernel.ts: kernel.py in TypeScript. Node 14 or later, its standard library
 * only (checked on Node 14.15.0 and 15.0.0, built by TypeScript 4.0.5, all
 * from 2020, and on Node 22 built by TypeScript 5.9; for a TypeScript before
 * 4.5, build with --target ES2020).
 *
 *   npm install && npx tsc            (builds dist/kernel.js)
 *   node dist/kernel.js init    --box DIR --pile FOLDER --key FILE
 *   node dist/kernel.js bite    --box DIR "your words"
 *   node dist/kernel.js propose --box DIR --file ROWS.jsonl
 *   node dist/kernel.js seal    --box DIR proposal:<sha256>
 *   node dist/kernel.js verify  --box DIR
 *   node dist/kernel.js out     --box DIR
 *   node dist/kernel.js vectors CASES.json RESULTS.json
 *
 * A port held to ../cases.json by ../compare.py, byte for byte and both ways
 * (its boxes verify under kernel.py, and kernel.py's under it).
 *
 * The kernel's four patterns are not regular expressions here (a page's hidden
 * script/style/comment spans, its tags, its character references, and a
 * definition's name) are small scanners here, written from Python's rules,
 * so this file can be followed line by line into a language whose regex
 * engine has no lookbehind or backreference (Go, Rust) or no astral code
 * points in a class (.NET). What Python decides that JavaScript decides
 * differently comes from ../kernel_tables.json, read out of Python by
 * gen.py: word characters, lowercasing and the final sigma, strip,
 * splitlines, isdigit, the HTML5 entities, and how re.I folds the letters
 * of "script" and "style". The tables are found at $KERNEL_TABLES, else
 * kernel_tables.json beside dist/.. (the kernel_vectors folder).
 *
 * Strings are handled as code points throughout: JavaScript's are UTF-16,
 * Python's are code points, and every length, slice and comparison here is
 * Python's.
 *
 * Exit codes, the kernel's: 0 a human said it, 42 not enough information to
 * say a human said this was true, 2 refused.
 */

import * as crypto from "crypto";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { TextDecoder } from "util"; // global since Node 11, but typed in util until much later

const ALIVE = 0;
const ESCALATE = 42;
const REFUSED = 2;

// ── JSON: Python's json.loads in, the kernel's canon out ───────────────────
/** JSON values as Python has them: objects keep their order (a Map), integers
 *  are exact (bigint), other numbers are floats (number), null is null. */
type J = null | boolean | bigint | number | string | J[] | JMap;
type JMap = Map<string, J>;

class JsonError extends Error {}

function loads(text: string): J {
  let i = 0;
  const s = text;
  const ws = () => {
    while (i < s.length && " \t\n\r".includes(s[i])) i++;
  };
  const value = (): J => {
    if (i >= s.length) throw new JsonError("expecting value");
    const c = s[i];
    if (c === "{") return object();
    if (c === "[") return array();
    if (c === '"') return str();
    for (const [lit, v] of [
      ["null", null],
      ["true", true],
      ["false", false],
      ["NaN", NaN],
      ["Infinity", Infinity],
      ["-Infinity", -Infinity],
    ] as [string, J][]) {
      if (s.startsWith(lit, i)) {
        i += lit.length;
        return v;
      }
    }
    return number();
  };
  const number = (): J => {
    const m = /^(-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?/.exec(s.slice(i));
    if (!m) throw new JsonError("expecting value");
    i += m[0].length;
    if (m[2] === undefined && m[3] === undefined) return BigInt(m[1]);
    return Number(m[0]);
  };
  const object = (): JMap => {
    i++;
    const out: JMap = new Map(); // a repeated key keeps its place, takes the last value
    ws();
    if (s[i] === "}") {
      i++;
      return out;
    }
    for (;;) {
      ws();
      if (s[i] !== '"') throw new JsonError("expecting key");
      const k = str();
      ws();
      if (s[i] !== ":") throw new JsonError("expecting :");
      i++;
      ws();
      out.set(k, value());
      ws();
      if (s[i] === ",") {
        i++;
        continue;
      }
      if (s[i] === "}") {
        i++;
        return out;
      }
      throw new JsonError("expecting , or }");
    }
  };
  const array = (): J[] => {
    i++;
    const out: J[] = [];
    ws();
    if (s[i] === "]") {
      i++;
      return out;
    }
    for (;;) {
      ws();
      out.push(value());
      ws();
      if (s[i] === ",") {
        i++;
        continue;
      }
      if (s[i] === "]") {
        i++;
        return out;
      }
      throw new JsonError("expecting , or ]");
    }
  };
  const str = (): string => {
    i++;
    let b = "";
    for (;;) {
      if (i >= s.length) throw new JsonError("unterminated string");
      const c = s[i++];
      if (c === '"') return b;
      if (c.charCodeAt(0) < 0x20) throw new JsonError("invalid control character");
      if (c !== "\\") {
        b += c;
        continue;
      }
      const e = s[i++];
      const simple: Record<string, string> = { '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" };
      if (e in simple) b += simple[e];
      else if (e === "u") {
        const h = s.slice(i, i + 4);
        if (!/^[0-9a-fA-F]{4}$/.test(h)) throw new JsonError("bad \\u escape");
        b += String.fromCharCode(parseInt(h, 16));
        i += 4;
      } else throw new JsonError("invalid escape");
    }
  };
  ws();
  const v = value();
  ws();
  if (i !== s.length) throw new JsonError("extra data");
  return v;
}

/** Code point order, as Python compares str: not UTF-16 unit order. */
function byCodePoint(a: string, b: string): number {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const p = x[i].codePointAt(0)!;
    const q = y[i].codePointAt(0)!;
    if (p !== q) return p < q ? -1 : 1;
  }
  return x.length - y.length;
}

function quote(s: string, ascii: boolean): string {
  let b = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    const ch = s[i];
    if (ch === '"') b += '\\"';
    else if (ch === "\\") b += "\\\\";
    else if (ch === "\n") b += "\\n";
    else if (ch === "\r") b += "\\r";
    else if (ch === "\t") b += "\\t";
    else if (ch === "\b") b += "\\b";
    else if (ch === "\f") b += "\\f";
    else if (c < 0x20 || (ascii && c > 0x7e)) b += "\\u" + c.toString(16).padStart(4, "0");
    else b += ch;
  }
  return b + '"';
}

/** json.dumps: `sort` keys by code point, `sep` [item, key] separators,
 *  `ascii` for ensure_ascii. No floats: kernel rows never hold one. */
function dumps(o: J, sort: boolean, sep: [string, string], ascii: boolean): string {
  if (o === null) return "null";
  if (typeof o === "boolean") return o ? "true" : "false";
  if (typeof o === "bigint") return o.toString();
  if (typeof o === "number") throw new Error("canon refuses a float");
  if (typeof o === "string") return quote(o, ascii);
  if (Array.isArray(o)) return "[" + o.map((v) => dumps(v, sort, sep, ascii)).join(sep[0]) + "]";
  const keys = Array.from(o.keys());
  if (sort) keys.sort(byCodePoint);
  return "{" + keys.map((k) => quote(k, ascii) + sep[1] + dumps(o.get(k)!, sort, sep, ascii)).join(sep[0]) + "}";
}

/** Family A, `sorted_compact_utf8`. */
const canon = (o: J) => dumps(o, true, [",", ":"], false);
/** json.dumps(o) with Python's defaults: kernel.json, the proposal files. */
const dumpsDefault = (o: J, sort: boolean) => dumps(o, sort, [", ", ": "], true);
/** json.dumps(o, ensure_ascii=False): what the person reads. */
const dumpsShown = (o: J) => dumps(o, false, [", ", ": "], false);

const obj = (...kv: [string, J][]): JMap => new Map(kv);

// ── hashing and the seal ───────────────────────────────────────────────────
const sha256 = (data: Buffer | string) => crypto.createHash("sha256").update(data).digest("hex");
const h256 = (text: string) => sha256(Buffer.from(text, "utf8"));
const hmac = (key: Buffer, msg: Buffer) => crypto.createHmac("sha256", key).update(msg).digest("hex");
/** onescript/sign.py hmac_parts(key, "seal", subject). */
const proof = (key: Buffer, subject: string) => hmac(key, Buffer.from("seal\x1f" + subject, "utf8"));

// ── the tables: Python's decisions, read out of Python ─────────────────────
class Ranges {
  lo: number[];
  hi: number[];
  constructor(rs: J) {
    const list = rs as J[];
    this.lo = list.map((r) => Number((r as J[])[0] as bigint));
    this.hi = list.map((r) => Number((r as J[])[1] as bigint));
  }
  has(c: number): boolean {
    let a = 0;
    let z = this.lo.length - 1;
    while (a <= z) {
      const m = (a + z) >>> 1;
      if (c < this.lo[m]) z = m - 1;
      else if (c > this.hi[m]) a = m + 1;
      else return true;
    }
    return false;
  }
}

interface Tables {
  word: Ranges;
  space: Ranges;
  digit: Ranges;
  cased: Ranges;
  ignorable: Ranges;
  lower: Map<number, string>;
  lineBreaks: Set<number>;
  html5: Map<string, string>;
  invalidCharrefs: Map<number, string>;
  invalidCodepoints: Set<number>;
  tagFold: Map<string, Set<number>>;
  tagBackref: Map<string, Set<string>>; // "a,b" pairs
  GENESIS: string;
  TOP: number;
  SNIFF: number;
  MAX_FILE: number;
  MAX_CHUNK: number;
  ECHO_RUN: number;
  HOLD: [bigint, bigint];
  FUNCTION: Set<string>;
  MAKE: Set<string>;
  NEXT: Map<string, string>;
  ROW_KEYS: string[];
}

let T: Tables;
let VERSION = "";

function loadTables(file: string): void {
  const t = loads(fs.readFileSync(file, "utf8")) as JMap;
  const kc = t.get("kernel") as JMap;
  const sigma = t.get("sigma") as JMap;
  const num = (v: J) => Number(v as bigint);
  const strs = (v: J) => (v as J[]).map((x) => x as string);
  const tagFold = new Map<string, Set<number>>();
  for (const [k, v] of t.get("tag_fold") as JMap) tagFold.set(k, new Set((v as J[]).map(num)));
  const tagBackref = new Map<string, Set<string>>();
  for (const [k, v] of t.get("tag_backref") as JMap)
    tagBackref.set(k, new Set((v as J[]).map((p) => (p as J[]).map(num).join(","))));
  const hold = kc.get("HOLD") as J[];
  T = {
    word: new Ranges(t.get("word")!),
    space: new Ranges(t.get("space")!),
    digit: new Ranges(t.get("digit")!),
    cased: new Ranges(sigma.get("cased")!),
    ignorable: new Ranges(sigma.get("case_ignorable")!),
    lower: new Map(Array.from(t.get("lower") as JMap).map(([k, v]) => [Number(k), v as string])),
    lineBreaks: new Set((t.get("line_breaks") as J[]).map(num)),
    html5: new Map(Array.from(t.get("html5") as JMap).map(([k, v]) => [k, v as string])),
    invalidCharrefs: new Map(Array.from(t.get("invalid_charrefs") as JMap).map(([k, v]) => [Number(k), v as string])),
    invalidCodepoints: new Set((t.get("invalid_codepoints") as J[]).map(num)),
    tagFold,
    tagBackref,
    GENESIS: kc.get("GENESIS") as string,
    TOP: num(kc.get("TOP")!),
    SNIFF: num(kc.get("SNIFF")!),
    MAX_FILE: num(kc.get("MAX_FILE")!),
    MAX_CHUNK: num(kc.get("MAX_CHUNK")!),
    ECHO_RUN: num(kc.get("ECHO_RUN")!),
    HOLD: [hold[0] as bigint, hold[1] as bigint],
    FUNCTION: new Set(strs(kc.get("FUNCTION")!)),
    MAKE: new Set(strs(kc.get("MAKE")!)),
    NEXT: new Map(Array.from(kc.get("NEXT") as JMap).map(([k, v]) => [k, v as string])),
    ROW_KEYS: strs(kc.get("ROW_KEYS")!),
  };
}

// ── str, as Python has it ──────────────────────────────────────────────────
const cps = (s: string): number[] => Array.from(s, (ch) => ch.codePointAt(0)!);
const fromCps = (a: number[], from = 0, to = a.length): string => {
  let out = "";
  for (let i = from; i < to; i++) out += String.fromCodePoint(a[i]);
  return out;
};
const len = (s: string) => Array.from(s).length;
/** Python's \w: a word character or "_". */
const isW = (c: number) => c === 0x5f || T.word.has(c);

/** str.lower: the table, and U+03A3 by CPython's final-sigma context. */
function lower(s: string): string {
  const a = cps(s);
  let b = "";
  for (let i = 0; i < a.length; i++) {
    const c = a[i];
    if (c === 0x03a3) b += finalSigma(a, i) ? "ς" : "σ";
    else b += T.lower.get(c) ?? String.fromCodePoint(c);
  }
  return b;
}

function finalSigma(a: number[], i: number): boolean {
  let j = i - 1;
  while (j >= 0 && T.ignorable.has(a[j])) j--;
  if (j < 0 || !T.cased.has(a[j])) return false;
  j = i + 1;
  while (j < a.length && T.ignorable.has(a[j])) j++;
  return j === a.length || !T.cased.has(a[j]);
}

/** str.strip() == "" */
const blank = (s: string) => cps(s).every((c) => T.space.has(c));

/** str.strip() */
function strip(s: string): string {
  const a = cps(s);
  let i = 0;
  let j = a.length;
  while (i < j && T.space.has(a[i])) i++;
  while (j > i && T.space.has(a[j - 1])) j--;
  return fromCps(a, i, j);
}

/** str.splitlines(): "\r\n" is one break; no terminators kept. */
function splitlines(s: string): string[] {
  const a = cps(s);
  const out: string[] = [];
  let start = 0;
  let i = 0;
  while (i < a.length) {
    const c = a[i];
    if (T.lineBreaks.has(c)) {
      out.push(fromCps(a, start, i));
      i += c === 13 && a[i + 1] === 10 ? 2 : 1;
      start = i;
    } else i++;
  }
  if (start < a.length) out.push(fromCps(a, start));
  return out;
}

const take = (s: string, n: number) => {
  const a = Array.from(s);
  return a.length <= n ? s : a.slice(0, n).join("");
};

// ── words and the bite key: onescript/words.py ─────────────────────────────
function words(text: string): string[] {
  const out: string[] = [];
  let cur = "";
  for (const c of cps(lower(text))) {
    if (T.word.has(c)) cur += String.fromCodePoint(c);
    else if (cur) {
      out.push(cur);
      cur = "";
    }
  }
  if (cur) out.push(cur);
  return out;
}

function biteKey(text: string): string | null {
  const ws = words(text);
  return ws.length ? h256(ws.join(" ")) : null;
}

function subjectOf(prop: JMap): string {
  const four = obj(...(["path", "data", "cites", "claim"].map((k) => [k, prop.get(k) ?? null]) as [string, J][]));
  return "proposal:" + h256(canon(four));
}

// ── the scanners: the kernel's regular expressions, by hand ────────────────
const NL = 10;
const lt = 0x3c;
const gtC = 0x3e;

/** " " and one newline for each the match held: line numbers still point home. */
function kept(a: number[], from: number, to: number): string {
  let n = 0;
  for (let i = from; i < to; i++) if (a[i] === NL) n++;
  return " " + "\n".repeat(n);
}

/** `<(script|style)\b.*?</\1\s*>|<!--.*?-->` under re.S | re.I, matched at
 *  `i` (a "<"): the end of the match, or -1. The name's letters fold as re.I
 *  folds them (tag_fold); the closing name must equal the opening one as re.I's
 *  backreference compares (tag_backref); `\b` is "not a \w next"; `.*?` is the
 *  first closing tag that fits. */
function hiddenAt(a: number[], i: number): number {
  for (const name of ["script", "style"]) {
    const n = name.length;
    let ok = i + 1 + n <= a.length;
    for (let k = 0; ok && k < n; k++) ok = T.tagFold.get(name[k])!.has(a[i + 1 + k]);
    if (!ok) continue;
    const e = i + 1 + n;
    if (e < a.length && isW(a[e])) continue; // \b
    for (let k = e; k + 1 < a.length; k++) {
      if (a[k] !== lt || a[k + 1] !== 0x2f) continue;
      let same = k + 2 + n <= a.length;
      for (let x = 0; same && x < n; x++) same = T.tagBackref.get(name[x])!.has(a[i + 1 + x] + "," + a[k + 2 + x]);
      if (!same) continue;
      let p = k + 2 + n;
      while (p < a.length && T.space.has(a[p])) p++;
      if (p < a.length && a[p] === gtC) return p + 1;
    }
  }
  if (a[i + 1] === 0x21 && a[i + 2] === 0x2d && a[i + 3] === 0x2d) {
    for (let k = i + 4; k + 2 < a.length; k++) if (a[k] === 0x2d && a[k + 1] === 0x2d && a[k + 2] === gtC) return k + 3;
  }
  return -1;
}

function hidden(text: string): string {
  const a = cps(text);
  let out = "";
  let i = 0;
  while (i < a.length) {
    const m = a[i] === lt ? hiddenAt(a, i) : -1;
    if (m >= 0) {
      out += kept(a, i, m);
      i = m;
    } else out += String.fromCodePoint(a[i++]);
  }
  return out;
}

/** `<[^>]*>` */
function tags(text: string): string {
  const a = cps(text);
  let out = "";
  let i = 0;
  while (i < a.length) {
    if (a[i] === lt) {
      const q = a.indexOf(gtC, i + 1);
      if (q >= 0) {
        out += kept(a, i, q + 1);
        i = q + 1;
        continue;
      }
    }
    out += String.fromCodePoint(a[i++]);
  }
  return out;
}

const isDec = (c: number) => c >= 0x30 && c <= 0x39;
const isHex = (c: number) => isDec(c) || (c >= 0x41 && c <= 0x46) || (c >= 0x61 && c <= 0x66);
const NAME_STOP = new Set([9, 10, 12, 0x20, 0x3c, 0x26, 0x23, 0x3b]); // \t \n \f space < & # ;

/** html.unescape: `&(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)`,
 *  each reference replaced as CPython's _replace_charref does. */
function unescape(s: string): string {
  if (!s.includes("&")) return s;
  const a = cps(s);
  let out = "";
  let i = 0;
  while (i < a.length) {
    if (a[i] !== 0x26) {
      out += String.fromCodePoint(a[i++]);
      continue;
    }
    let j = i + 1;
    if (a[j] === 0x23) {
      if (isDec(a[j + 1] ?? -1)) {
        j += 1;
        while (j < a.length && isDec(a[j])) j++;
      } else if ((a[j + 1] === 0x78 || a[j + 1] === 0x58) && isHex(a[j + 2] ?? -1)) {
        j += 2;
        while (j < a.length && isHex(a[j])) j++;
      } else {
        out += "&";
        i++;
        continue;
      }
    } else {
      while (j < a.length && j - (i + 1) < 32 && !NAME_STOP.has(a[j])) j++;
      if (j === i + 1) {
        out += "&";
        i++;
        continue;
      }
    }
    if (a[j] === 0x3b) j++;
    out += charref(fromCps(a, i + 1, j));
    i = j;
  }
  return out;
}

function charref(s: string): string {
  if (s[0] === "#") {
    const hex = s[1] === "x" || s[1] === "X";
    const digits = (hex ? s.slice(2) : s.slice(1)).replace(/;$/, "");
    const big = BigInt(hex ? "0x" + digits : digits);
    if (big > 0x10ffffn) return "�";
    const n = Number(big);
    if (T.invalidCharrefs.has(n)) return T.invalidCharrefs.get(n)!;
    if (n >= 0xd800 && n <= 0xdfff) return "�";
    if (T.invalidCodepoints.has(n)) return "";
    return String.fromCodePoint(n);
  }
  if (T.html5.has(s)) return T.html5.get(s)!;
  const a = cps(s);
  for (let x = a.length - 1; x > 1; x--) {
    const head = fromCps(a, 0, x);
    if (T.html5.has(head)) return T.html5.get(head)! + fromCps(a, x);
  }
  return "&" + s;
}

const htmlText = (text: string) => unescape(tags(hidden(text)));

const KEYWORDS = ["def", "function", "sub", "class", "func", "fn"];
const identHead = (c: number) => c === 0x5f || (c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a);
const identTail = (c: number) => identHead(c) || isDec(c);

/** re.findall of `\b(?:def|function|sub|class|func|fn)\s+([A-Za-z_][A-Za-z0-9_]*)`. */
function defines(text: string): string[] {
  const a = cps(text);
  const out: string[] = [];
  let p = 0;
  outer: while (p < a.length) {
    if (p === 0 || !isW(a[p - 1])) {
      for (const kw of KEYWORDS) {
        let k = 0;
        while (k < kw.length && a[p + k] === kw.charCodeAt(k)) k++;
        if (k < kw.length) continue;
        let q = p + kw.length;
        const s0 = q;
        while (q < a.length && T.space.has(a[q])) q++;
        if (q === s0 || !(q < a.length && identHead(a[q]))) continue;
        const start = q;
        while (q < a.length && identTail(a[q])) q++;
        out.push(fromCps(a, start, q));
        p = q;
        continue outer;
      }
    }
    p++;
  }
  return out;
}

/** How many times `\bname\b` occurs (name is an identifier: \w all through). */
function named(text: string, name: string): number {
  const a = cps(text);
  const b = cps(name);
  let n = 0;
  let p = 0;
  while (p + b.length <= a.length) {
    let k = 0;
    while (k < b.length && a[p + k] === b[k]) k++;
    if (k === b.length && (p === 0 || !isW(a[p - 1])) && (p + b.length === a.length || !isW(a[p + b.length]))) {
      n++;
      p += b.length;
    } else p++;
  }
  return n;
}

// ── the pile ───────────────────────────────────────────────────────────────
function holds(w: string, toks: Set<string>): boolean {
  if (toks.has(w)) return true;
  if (len(w) < 4 || !cps(w).some((c) => T.digit.has(c))) return false;
  for (const t of toks) if (t.endsWith(w) || t.startsWith(w)) return true;
  return false;
}

function echo(bite: string, text: string): boolean {
  const ws = words(bite);
  const ts = words(text);
  const n = Math.min(ws.length, T.ECHO_RUN);
  if (n < 3) return false;
  const runs = new Set<string>();
  for (let i = 0; i + n <= ts.length; i++) runs.add(JSON.stringify(ts.slice(i, i + n)));
  for (let i = 0; i + n <= ws.length; i++) if (runs.has(JSON.stringify(ws.slice(i, i + n)))) return true;
  return false;
}

interface Chunk {
  path: string;
  line: number;
  text: string;
  id: string;
}

const chunkJson = (c: Chunk): J => obj(["path", c.path], ["line", BigInt(c.line)], ["text", c.text], ["id", c.id]);

/** Path.rglob("*"), every entry, by parts. */
function walk(dir: string, parts: string[], out: string[][]): void {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const mine = [...parts, e.name];
    out.push(mine);
    if (e.isDirectory()) walk(path.join(dir, e.name), mine, out);
  }
}

function byParts(a: string[], b: string[]): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) {
    const c = byCodePoint(a[i], b[i]);
    if (c) return c;
  }
  return a.length - b.length;
}

function suffix(name: string): string {
  const i = name.lastIndexOf(".");
  return i > 0 && i < name.length - 1 ? name.slice(i) : "";
}

/** UTF-8, strictly, as Python's bytes.decode: a BOM is kept as a character. */
const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

function chunks(pile: string): Chunk[] {
  const all: string[][] = [];
  walk(pile, [], all);
  all.sort(byParts);
  const out: Chunk[] = [];
  for (const rel of all) {
    const p = path.join(pile, ...rel);
    let st: fs.Stats;
    try {
      st = fs.statSync(p);
    } catch {
      continue;
    }
    if (!st.isFile() || rel.some((part) => part.startsWith("."))) continue;
    let text: string;
    try {
      if (st.size > T.MAX_FILE) continue;
      const raw = fs.readFileSync(p);
      if (raw.subarray(0, T.SNIFF).includes(0)) continue;
      text = UTF8.decode(raw);
    } catch {
      continue;
    }
    if ([".html", ".htm"].includes(suffix(rel[rel.length - 1]).toLowerCase())) text = htmlText(text);
    const relPath = rel.join("/");
    const lines = [...splitlines(text), ""];
    let para: string[] = [];
    let start = 1;
    let size = 0;
    lines.forEach((line, k) => {
      const i = k + 1;
      if (para.length && (blank(line) || size + len(line) > T.MAX_CHUNK)) {
        const body = para.join("\n");
        out.push({ path: relPath, line: start, text: body, id: h256(body) });
        para = [];
        size = 0;
      }
      if (!blank(line)) {
        if (!para.length) start = i;
        para.push(take(line, T.MAX_CHUNK));
        size += len(para[para.length - 1]) + 1;
      }
    });
  }
  return out;
}

/** An exact rational, for the rank's scores. */
type Q = [bigint, bigint];
const gcd = (a: bigint, b: bigint): bigint => (b === 0n ? (a < 0n ? -a : a) : gcd(b, a % b));
const qAdd = (x: Q, y: Q): Q => {
  const n = x[0] * y[1] + y[0] * x[1];
  const d = x[1] * y[1];
  const g = gcd(n, d) || 1n;
  return [n / g, d / g];
};
const qCmp = (x: Q, y: Q) => {
  const l = x[0] * y[1];
  const r = y[0] * x[1];
  return l < r ? -1 : l > r ? 1 : 0;
};

type Alias = Map<string, string[]>;
const WORDS_BAD = ": one word = one or more words, a comma between";

/** kernel.py's parse_words: their words list as {their word: the folder's words}, or why not. */
function parseWords(text: string): [Alias, string | null] {
  const out: Alias = new Map();
  const lines = splitlines(text);
  for (let n = 1; n <= lines.length; n++) {
    const s = strip(lines[n - 1]);
    if (!s || s.startsWith("#")) continue;
    const eq = s.indexOf("=");
    const left = eq < 0 ? s : s.slice(0, eq);
    const right = eq < 0 ? "" : s.slice(eq + 1);
    const theirs = right.split(",").map((x) => words(x));
    if (eq < 0 || words(left).length !== 1 || theirs.some((t) => t.length !== 1))
      return [new Map(), `words list line ${n}${WORDS_BAD}`];
    const her = words(left)[0];
    if (!out.has(her)) out.set(her, []);
    const mine = out.get(her)!;
    for (const [t] of theirs) if (t !== her && !mine.includes(t)) mine.push(t);
  }
  const res: Alias = new Map();
  for (const [w, ts] of out) if (ts.length) res.set(w, [...ts].sort(byCodePoint));
  return [res, null];
}

/** kernel.py's read_words: [the list, its SHA-256, why not], kept like the key. */
function readWords(file: string, box: string): [Alias | null, string, string | null] {
  if (inside(file, box)) return [null, "", "the words list can't live inside the box"];
  try {
    if (process.platform !== "win32" && fs.statSync(file).mode & 0o077)
      return [null, "", `${file} is readable beyond its owner; chmod 600 it`];
    const raw = fs.readFileSync(file);
    let text: string;
    try {
      text = UTF8.decode(raw);
    } catch {
      return [null, "", "the words list could not be read: UnicodeDecodeError"];
    }
    const [alias, why] = parseWords(text);
    if (why !== null) return [null, "", why];
    return [alias, crypto.createHash("sha256").update(raw).digest("hex"), null];
  } catch (e) {
    return [null, "", `the words list could not be read: ${pyErrName(e)}`];
  }
}

/** Their word holds where it, or a word their list reads it as, does. */
function heldBy(w: string, toks: Set<string>, alias: Alias): boolean {
  return holds(w, toks) || (alias.get(w) ?? []).some((x) => holds(x, toks));
}

const idChar = (c: string) => (c >= "0" && c <= "9") || (c >= "a" && c <= "z") || (c >= "A" && c <= "Z") || c === "-" || c === "/";
const isMark = (c: string) => c === "-" || c === "/";

/** kernel.py's ids: the numbers that name one thing, in order, each once; never a date. */
function ids(text: string): string[] {
  const out: string[] = [];
  let i = 0;
  while (i < text.length) {
    let j = i;
    while (j < text.length && idChar(text[j])) j++;
    let a = i;
    let b = j;
    while (a < b && isMark(text[a])) a++;
    while (b > a && isMark(text[b - 1])) b--;
    const run = text.slice(a, b);
    if (/[0-9]/.test(run) && /[-/]/.test(run) && !newest(run) && !out.includes(run)) out.push(run);
    i = j === i ? j + 1 : j;
  }
  return out;
}

/** kernel.py's shape: digits as 9, letters as A, marks kept. */
const shape = (id: string) => [...id].map((c) => (c >= "0" && c <= "9" ? "9" : isMark(c) ? c : "A")).join("");

/** kernel.py's missing: what isn't there, beside the served chunks' files. */
function missing(pile: Chunk[], served: Chunk[]): J[] {
  const byFile = new Map<string, Set<string>>();
  for (const c of pile) {
    if (!byFile.has(c.path)) byFile.set(c.path, new Set());
    for (const id of ids(c.text)) byFile.get(c.path)!.add(id);
  }
  const every = new Set<string>();
  for (const f of byFile.values()) for (const id of f) every.add(id);
  const paths = [...new Set(served.map((c) => c.path))];
  const shapes = new Set<string>();
  for (const p of paths) for (const c of pile) if (c.path === p) for (const id of ids(c.text)) shapes.add(shape(id));
  const out: J[] = [];
  const files = [...byFile.keys()].sort(byCodePoint);
  for (const sh of shapes) {
    const group = [...every].filter((id) => shape(id) === sh);
    for (const path of files) {
      if (paths.includes(path)) continue;
      const mine = byFile.get(path)!;
      const have = group.filter((id) => mine.has(id));
      const k = have.length;
      if (!(2 * k >= group.length && k < group.length)) continue;
      const gone = group
        .filter((id) => !mine.has(id) && [...byFile.values()].some((f) => f.has(id) && have.some((h) => f.has(h))))
        .sort(byCodePoint);
      if (!gone.length) continue;
      out.push(obj(["why", `${gone.join(", ")}: not in ${path}`], ["where", `${path} names ${k} of the ${group.length} like them`]));
    }
  }
  return out;
}

/** kernel.py's cells: one line of comma-separated values, quotes kept whole. */
function cells(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted && ch === '"' && line[i + 1] === '"') {
      cur += '"';
      i++;
    } else if (ch === '"') quoted = !quoted;
    else if (ch === "," && !quoted) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

/** A decimal: its digits and its places (75.1 is [751n, 1]). */
type Num = [bigint, number];
const allDigits = (s: string) => [...s].every((c) => c >= "0" && c <= "9");

/** kernel.py's number: a cell that is a decimal number, or null. */
function number(cell: string): Num | null {
  const s = strip(cell);
  const neg = s.startsWith("-");
  const body = neg ? s.slice(1) : s;
  const dot = body.indexOf(".");
  const whole = dot < 0 ? body : body.slice(0, dot);
  const frac = dot < 0 ? "" : body.slice(dot + 1);
  if (!whole || !allDigits(whole) || !allDigits(frac) || (dot >= 0 && !frac)) return null;
  const n = BigInt(whole + frac);
  return [neg ? -n : n, frac.length];
}

/** kernel.py's day: days since 1970-01-01 for a YYYYMMDD. */
function day(d: string): number {
  let y = Number(d.slice(0, 4));
  const m = Number(d.slice(4, 6));
  const dd = Number(d.slice(6));
  if (m <= 2) y -= 1;
  const era = Math.floor(y / 400);
  const yoe = y - era * 400;
  const doy = Math.floor((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + dd - 1;
  return era * 146097 + yoe * 365 + Math.floor(yoe / 4) - Math.floor(yoe / 100) + doy - 719468;
}

function shown([v, places]: Num): string {
  const digits = (v < 0n ? -v : v).toString().padStart(places + 1, "0");
  const body = digits.slice(0, digits.length - places) + (places ? "." + digits.slice(digits.length - places) : "");
  return (v < 0n ? "-" : "") + body;
}

const isoDate = (d: string) => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
const pow10 = (n: number) => 10n ** BigInt(n);

function change(a: Num, b: Num): Num {
  const places = Math.max(a[1], b[1]);
  return [b[0] * pow10(places - b[1]) - a[0] * pow10(places - a[1]), places];
}

const absN = (v: bigint) => (v < 0n ? -v : v);
const bigger = (a: Num, b: Num) => absN(a[0]) * pow10(b[1]) > absN(b[0]) * pow10(a[1]);
const moved = (d: Num) => (d[0] === 0n ? "no change" : `${d[0] > 0n ? "up" : "down"} ${shown([absN(d[0]), d[1]])}`);
const WEEK = 7;
const WEEKS = 4;

/** The latest row at least a week before d, or -1. */
function weekBefore(dates: string[], d: string): number {
  let at = -1;
  dates.forEach((x, i) => {
    if (day(x) <= day(d) - WEEK) at = i;
  });
  return at;
}

/** kernel.py's series: numbers over time, in a table of dated rows. */
function series(file: string, text: string): J[] {
  const out: J[] = [];
  const lines = splitlines(text).filter((ln) => strip(ln));
  if (lines.length < 3) return out;
  const head = cells(lines[0]);
  if (newest(lines[0]) || head.length < 2) return out;
  const rows: [string, string[]][] = [];
  for (const ln of lines.slice(1)) {
    const cs = cells(ln);
    const d = newest(cs[0]);
    const c0 = strip(cs[0]);
    if (d && (c0 === d || c0 === isoDate(d))) rows.push([d, cs]);
  }
  if (rows.length < 2 || rows.some((r, i) => i > 0 && r[0] <= rows[i - 1][0])) return out;
  for (let col = 1; col < head.length; col++) {
    const got: [string, Num][] = [];
    for (const [d, cs] of rows) {
      const n = col < cs.length ? number(cs[col]) : null;
      if (n) got.push([d, n]);
    }
    if (got.length < 2) continue;
    const ds = got.map(([d]) => d);
    const [d1, last] = got[got.length - 1];
    const name = strip(head[col]) || `column ${col + 1}`;
    const b = weekBefore(ds, d1);
    if (b < 0) continue;
    const [d0, then] = got[b];
    let why = `${name}: ${shown(last)} on ${isoDate(d1)}, ${moved(change(then, last))} from ${shown(then)} on ${isoDate(d0)}`;
    let best: [Num, string, string] | null = null;
    for (const [d, n] of got) {
      if (day(d) <= day(d1) - WEEK * WEEKS) continue;
      const w = weekBefore(ds, d);
      if (w < 0) continue;
      const diff = change(got[w][1], n);
      if (best === null || bigger(diff, best[0])) best = [diff, got[w][0], d];
    }
    if (best !== null && !(best[1] === d0 && best[2] === d1))
      why += `; the most in one week of the last ${WEEKS}: ${moved(best[0])}, ${isoDate(best[1])} to ${isoDate(best[2])}`;
    out.push(obj(["why", why], ["where", `${file}: ${rows.length} dated rows, ${isoDate(rows[0][0])} to ${isoDate(rows[rows.length - 1][0])}`]));
  }
  return out;
}

/** How many ASCII digits run from i. */
function digits(t: string, i: number): number {
  let j = i;
  while (j < t.length && t[j] >= "0" && t[j] <= "9") j++;
  return Math.max(0, j - i);
}

function date(y: string, m: string, d: string): string {
  const ok = (y.startsWith("19") || y.startsWith("20")) && m >= "01" && m <= "12" && d >= "01" && d <= "31";
  return ok ? y + m + d : "";
}

/** kernel.py's newest: the latest ISO or compact date written in t, or "". */
function newest(t: string): string {
  let best = "";
  let i = 0;
  while (i < t.length) {
    let n = digits(t, i);
    if (n === 0) {
      i++;
      continue;
    }
    let got = "";
    if (n === 8) {
      got = date(t.slice(i, i + 4), t.slice(i + 4, i + 6), t.slice(i + 6, i + 8));
    } else if (n === 4 && t[i + 4] === "-" && digits(t, i + 5) === 2 && t[i + 7] === "-" && digits(t, i + 8) === 2) {
      got = date(t.slice(i, i + 4), t.slice(i + 5, i + 7), t.slice(i + 8, i + 10));
      if (got) n = 10;
    }
    if (got > best) best = got;
    i += n;
  }
  return best;
}

/** kernel.py's dates: each chunk's date, carried down its file; 0 for none. */
function dates(pile: Chunk[]): number[] {
  const out: number[] = [];
  let path: string | null = null;
  let cur = "";
  for (const c of pile) {
    if (c.path !== path) {
      path = c.path;
      cur = newest(c.path);
    }
    const t = newest(c.text);
    if (t > cur) cur = t;
    out.push(cur ? Number(cur) : 0);
  }
  return out;
}

function rank(pile: Chunk[], bite: string, alias: Alias): Chunk[] {
  const want = new Set(words(bite).filter((w) => !T.FUNCTION.has(w)));
  if (!want.size) return [];
  const when = dates(pile);
  const held: [Chunk, Set<string>, number][] = [];
  pile.forEach((c, k) => {
    const toks = new Set([...words(c.text), ...words(c.path)]); // a folder's name is the person's word too
    const h = new Set([...want].filter((w) => heldBy(w, toks, alias)));
    if (BigInt(h.size) * T.HOLD[1] >= T.HOLD[0] * BigInt(want.size)) held.push([c, h, when[k]]);
  });
  const df = new Map<string, bigint>();
  for (const w of want) df.set(w, BigInt(held.filter(([, h]) => h.has(w)).length));
  const scored: [Q, Chunk, number][] = [];
  for (const [c, h, d] of held) {
    if (!h.size) continue;
    let s: Q = [0n, 1n];
    for (const w of h) s = qAdd(s, [1n, df.get(w)!]);
    scored.push([s, c, d]);
  }
  // a log grows downward: on one date, the later line is the newer one
  scored.sort(
    (x, y) =>
      qCmp(y[0], x[0]) ||
      y[2] - x[2] ||
      byCodePoint(x[1].path, y[1].path) ||
      (x[2] ? y[1].line - x[1].line : x[1].line - y[1].line),
  );
  return scored.slice(0, T.TOP).map(([, c]) => c);
}

function look(pile: Chunk[], bite: string, served: Chunk[], alias: Alias): J[] {
  const out: J[] = [];
  const ws = words(bite);
  const want = [...new Set(ws)].filter((w) => !T.FUNCTION.has(w));
  const hint = (why: string, where: string | null, next: string | null): J => {
    const m: JMap = obj(["why", why]);
    if (where !== null) m.set("where", where);
    if (next !== null) m.set("next", next);
    return m;
  };
  if (!want.length) out.push(hint("every word is a common one: name the thing you mean", null, null));
  const every = new Set<string>();
  for (const c of pile) for (const w of [...words(c.text), ...words(c.path)]) every.add(w);
  const absent = want.filter((w) => !heldBy(w, every, alias));
  for (const w of want) {
    // said, so a match their list made is never taken for theirs
    if (holds(w, every)) continue;
    const read = (alias.get(w) ?? []).filter((x) => holds(x, every));
    if (read.length) out.push(hint(`read ${w} as ${read.join(", ")}, from your words list`, null, null));
  }
  if (absent.length)
    out.push(hint("no file in your folder has " + absent.join(", "), "it may be called something else there", T.NEXT.get("other words")!));
  for (const c of served) {
    for (const name of new Set(defines(c.text))) {
      const uses = new Set<string>();
      for (const d of pile) if (d !== c && named(d.text, name) > 0) uses.add(d.path);
      if (named(c.text, name) > 1) uses.add(c.path);
      const sorted = [...uses].sort(byCodePoint);
      out.push(
        hint(
          `${name} is defined at ${c.path}:${c.line}`,
          sorted.length ? "also named in " + sorted.join(", ") : "nothing else in your folder names it",
          T.NEXT.get("unused")!,
        ),
      );
    }
  }
  out.push(...missing(pile, served));
  for (const p of new Set(served.map((c) => c.path)))
    out.push(...series(p, pile.filter((x) => x.path === p).map((x) => x.text).join("\n")));
  if (ws.length && T.MAKE.has(ws[0])) out.push(hint("this asks for something made", null, T.NEXT.get("made")!));
  return out;
}

// ── the door ───────────────────────────────────────────────────────────────
function door(prop: J | undefined, served: Set<string>): string | null {
  const keys = ["path", "data", "cites", "claim"];
  if (!(prop instanceof Map) || prop.size !== 4 || !keys.every((k) => prop.has(k)))
    return "a row is exactly {path, data, cites, claim}";
  const p = prop.get("path");
  if (typeof p !== "string" || !p || p.startsWith("/") || p.startsWith("\\") || p.split("/").includes(".."))
    return "a path is plain and relative, inside the box";
  if (typeof prop.get("data") !== "string" || !Array.isArray(prop.get("cites"))) return "data is text and cites a list";
  const claim = prop.get("claim");
  if (typeof claim !== "string" || blank(claim)) return "a claim says why";
  for (const c of prop.get("cites") as J[])
    if (typeof c !== "string" || !served.has(c)) return "a cite that the last bite didn't serve";
  return null;
}

// ── the record: genesis's, in the one script's format ──────────────────────
interface Read {
  rows: JMap[];
  breaks: string[];
}

/** str() of a value, as Python prints it in a message. */
function pyStr(v: J | undefined): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  return String(v);
}

function read(file: string): Read {
  const r: Read = { rows: [], breaks: [] };
  if (!fs.existsSync(file)) return r;
  const data = UTF8.decode(fs.readFileSync(file));
  if (data && !data.endsWith("\n")) {
    r.breaks.push("torn tail: the last line is unterminated; a human looks first");
    return r;
  }
  let prev: J = T.GENESIS;
  const lines = data ? data.slice(0, -1).split("\n") : [];
  lines.forEach((line, i) => {
    let row: J | undefined;
    try {
      row = loads(line);
    } catch {
      row = undefined;
    }
    if (!(row instanceof Map) || !T.ROW_KEYS.every((k) => (row as JMap).has(k))) {
      r.breaks.push(`line ${i}: garbled, not a record row`);
      return;
    }
    const body = new Map(row);
    body.delete("hash");
    let hash = "";
    try {
      hash = h256(canon(body));
    } catch {
      hash = "";
    }
    if (row.get("prev") !== prev || hash !== row.get("hash")) r.breaks.push(`row ${pyStr(row.get("n"))}: chain broken`);
    const n = row.get("n");
    if (typeof n !== "bigint" || n !== BigInt(r.rows.length)) {
      const shown = typeof n === "string" ? `'${n}'` : pyStr(n);
      r.breaks.push(`row ${shown}: out of place, it is row ${r.rows.length}`);
    }
    r.rows.push(row);
    prev = row.get("hash") ?? null;
  });
  return r;
}

/** One writer at a time. Node has no flock, so the lock is a directory made
 *  beside the record (mkdir is atomic): it keeps two writers of this port
 *  apart, not this port and kernel.py (which flocks the record itself). */
function withLock<V>(box: string, fn: () => V): V {
  const lock = path.join(box, "record.jsonl.ts-lock");
  const until = Date.now() + 10000;
  for (;;) {
    try {
      fs.mkdirSync(lock);
      break;
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code !== "EEXIST" || Date.now() > until) throw e;
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 20);
    }
  }
  try {
    return fn();
  } finally {
    fs.rmdirSync(lock);
  }
}

function append(box: string, ts: string, kind: string, who: string, fields: JMap): JMap {
  const file = path.join(box, "record.jsonl");
  const row = withLock(box, () => {
    const r = read(file); // the true tail, read under the lock
    if (r.breaks.length) throw new Error(r.breaks.join("; "));
    const sealed = kind === "seal" && who === "human";
    const row: JMap = obj(
      ["n", BigInt(r.rows.length)],
      ["kind", kind],
      ["who", who],
      ["family", "kernel"],
      ["standing", sealed ? "sealed" : "unattested"],
      ["version", VERSION],
      ["ts", ts],
    );
    const rest = new Map(fields);
    row.set("where", rest.has("where") ? rest.get("where")! : null);
    rest.delete("where");
    for (const [k, v] of rest) row.set(k, v);
    row.set("prev", r.rows.length ? r.rows[r.rows.length - 1].get("hash")! : T.GENESIS);
    row.set("hash", h256(canon(row)));
    const fd = fs.openSync(file, "a");
    try {
      fs.writeSync(fd, Buffer.from(canon(row) + "\n", "utf8"));
      fs.fsyncSync(fd);
    } finally {
      fs.closeSync(fd);
    }
    return row;
  });
  if (row.get("n") === 0n) {
    try {
      const d = fs.openSync(box, "r"); // the new name survives a crash
      try {
        fs.fsyncSync(d);
      } finally {
        fs.closeSync(d);
      }
    } catch {
      // a directory that can't be opened here: as kernel.py without O_DIRECTORY
    }
  }
  return row;
}

// ── the box: theirs ────────────────────────────────────────────────────────
const config = (box: string) => loads(fs.readFileSync(path.join(box, "kernel.json"), "utf8")) as JMap;

/** Path.resolve(): absolute, symlinks resolved as far as the path exists. */
function resolved(p: string): string {
  const abs = path.resolve(p);
  const tail: string[] = [];
  let probe = abs;
  while (!fs.existsSync(probe)) {
    const parent = path.dirname(probe);
    if (parent === probe) break;
    tail.unshift(path.basename(probe));
    probe = parent;
  }
  try {
    return path.join(fs.realpathSync(probe), ...tail);
  } catch {
    return abs;
  }
}

function inside(p: string, box: string): boolean {
  const a = resolved(p);
  const b = resolved(box);
  return a === b || a.startsWith(b.endsWith(path.sep) ? b : b + path.sep);
}

function pyErrName(e: unknown): string {
  const code = (e as NodeJS.ErrnoException).code;
  if (code === "ENOENT") return "FileNotFoundError";
  if (code === "EACCES" || code === "EPERM") return "PermissionError";
  return "OSError";
}

function readKey(file: string, box: string): [Buffer | null, string | null] {
  if (inside(file, box)) return [null, "the key can't live inside the box"];
  try {
    if (process.platform !== "win32" && fs.statSync(file).mode & 0o077)
      return [null, `${file} is readable beyond its owner; chmod 600 it`];
    const hex = strip(fs.readFileSync(file, "utf8"));
    if (!/^([0-9a-fA-F]{2})*$/.test(hex)) return [null, "the key could not be read: ValueError"];
    return [Buffer.from(hex, "hex"), null];
  } catch (e) {
    return [null, `the key could not be read: ${pyErrName(e)}`];
  }
}

function init(box: string, pile: string, key: string, ts: string, wl: string | null): number {
  if (fs.existsSync(path.join(box, "record.jsonl"))) {
    console.error("refused: this box already has a record");
    return REFUSED;
  }
  if (!fs.existsSync(pile) || !fs.statSync(pile).isDirectory()) {
    console.error(`refused: ${pile} is not a folder`);
    return REFUSED;
  }
  if (inside(key, box)) {
    console.error("refused: the key can't live inside the box");
    return REFUSED;
  }
  if (wl !== null) {
    const why = readWords(wl, box)[2];
    if (why !== null) {
      console.error(`refused: ${why}`);
      return REFUSED;
    }
  }
  if (!fs.existsSync(key)) {
    fs.mkdirSync(path.dirname(path.resolve(key)), { recursive: true });
    const fd = fs.openSync(key, "wx", 0o600);
    try {
      fs.writeSync(fd, crypto.randomBytes(32).toString("hex") + "\n");
    } finally {
      fs.closeSync(fd);
    }
  }
  fs.mkdirSync(box, { recursive: true });
  const cfg: JMap = obj(["pile", resolved(pile)], ["key", resolved(key)]);
  if (wl !== null) cfg.set("words", resolved(wl));
  fs.writeFileSync(path.join(box, "kernel.json"), dumpsDefault(cfg, true) + "\n");
  const row = append(box, ts, "boot", "kernel", obj(["loop", "open"], ["pile_sha256", h256(cfg.get("pile") as string)]));
  console.log(`open loop, row ${row.get("n")} ${(row.get("hash") as string).slice(0, 16)}: the box is yours`);
  return ALIVE;
}

function sealed(box: string, rows: JMap[]): Map<string, JMap> {
  const [key] = readKey(config(box).get("key") as string, box);
  const out = new Map<string, JMap>();
  for (const r of rows) {
    if (r.get("kind") === "seal" && key !== null) {
      const subj = r.has("subject") ? r.get("subject")! : "";
      const want = Buffer.from(proof(key, typeof subj === "string" ? subj : pyStr(subj)), "utf8");
      const got = Buffer.from(pyStr(r.has("proof") ? r.get("proof") : null), "utf8");
      if (want.length === got.length && crypto.timingSafeEqual(want, got)) out.set(r.get("subject") as string, r);
    }
  }
  return out;
}

function refusedBreaks(r: Read): boolean {
  if (!r.breaks.length) return false;
  console.error(["refused:", ...r.breaks].join("\n  "));
  return true;
}

const propFile = (box: string, subj: string) => path.join(box, "proposals", subj.slice(9) + ".json");

function bite(box: string, text: string, ts: string): number {
  const r = read(path.join(box, "record.jsonl"));
  if (refusedBreaks(r)) return REFUSED;
  const key = biteKey(text);
  if (key === null) {
    console.error("refused: a bite with no words");
    return REFUSED;
  }
  for (const subj of sealed(box, r.rows).keys()) {
    // a sealed answer for these words, first
    const prop = loads(fs.readFileSync(propFile(box, subj), "utf8")) as JMap;
    if (prop.get("bite_key") === key) {
      append(box, ts, "bite", "kernel", obj(["bite_key", key], ["answered_by", subj], ["served", []]));
      console.log(dumpsShown(obj(["state", "sealed"], ["answer", prop.get("data")!], ["by", subj])));
      return ALIVE;
    }
  }
  const cfg = config(box);
  let alias: Alias = new Map();
  const listed: [string, J][] = [];
  if (cfg.has("words")) {
    const [got, sha, why] = readWords(cfg.get("words") as string, box);
    if (why !== null) {
      console.error(`refused: ${why}`);
      return REFUSED;
    }
    alias = got!;
    listed.push(["words_sha256", sha]);
  }
  const pile = chunks(cfg.get("pile") as string);
  const served = rank(pile, text, alias);
  const refs = served.map((c) => obj(["id", c.id], ["where", `${c.path}:${c.line}`], ["echo", echo(text, c.text)]));
  const answers = served.filter((_, i) => !refs[i].get("echo"));
  append(box, ts, "bite", "kernel", obj(["bite_key", key], ["served", refs], ...listed));
  const state = answers.length ? "populated" : refs.length ? "echo" : "empty";
  const doc: JMap = obj(["state", state], ["bite_key", key]);
  doc.set(
    "served",
    refs.map((m, i) => {
      const shown = new Map(m);
      shown.set("text", served[i].text);
      return shown;
    }),
  );
  const hints = look(pile, text, answers.length ? served : [], alias);
  if (hints.length) doc.set("look", hints);
  console.log(dumpsShown(doc));
  return answers.length ? ALIVE : ESCALATE;
}

function propose(box: string, lines: string[], ts: string): number {
  const r = read(path.join(box, "record.jsonl"));
  if (refusedBreaks(r)) return REFUSED;
  const last = [...r.rows].reverse().find((row) => row.get("kind") === "bite");
  if (!last) {
    console.error("refused: propose answers a bite; bite first");
    return REFUSED;
  }
  const served = new Set(((last.get("served") as J[]) ?? []).map((s) => (s as JMap).get("id") as string));
  let waiting = 0;
  lines.forEach((line, i) => {
    let prop: J | undefined;
    try {
      prop = loads(line);
    } catch {
      prop = undefined;
    }
    const why = door(prop, served);
    if (why === null) {
      const p = prop as JMap;
      const subj = subjectOf(p);
      fs.mkdirSync(path.join(box, "proposals"), { recursive: true });
      const kept2 = new Map(p);
      kept2.set("bite_key", last.get("bite_key")!);
      fs.writeFileSync(propFile(box, subj), dumpsDefault(kept2, false));
      append(box, ts, "proposal", "kernel", obj(["subject", subj], ["verdict", "pass"], ["bite_row", last.get("n")!]));
      console.log(`pass  ${subj}  waiting on your seal`);
      waiting++;
    } else {
      append(box, ts, "proposal", "kernel", obj(["verdict", "refused"], ["line", BigInt(i)], ["why", why]));
      console.log(`refused  line ${i}: ${why}`);
    }
  });
  return waiting ? ESCALATE : ALIVE;
}

function seal(box: string, subj: string, ts: string): number {
  const r = read(path.join(box, "record.jsonl"));
  if (refusedBreaks(r)) return REFUSED;
  if (!r.rows.some((row) => row.get("kind") === "proposal" && row.get("subject") === subj)) {
    console.error(`refused: ${subj} is not a proposal in this box`);
    return REFUSED;
  }
  const [key, why] = readKey(config(box).get("key") as string, box);
  if (key === null) {
    console.error(`refused: ${why}`);
    return REFUSED;
  }
  const prop = loads(fs.readFileSync(propFile(box, subj), "utf8")) as JMap;
  if (subjectOf(prop) !== subj) {
    console.error("refused: the proposal's bytes changed since it was proposed");
    return REFUSED;
  }
  append(box, ts, "seal", "human", obj(["subject", subj], ["proof", proof(key, subj)]));
  const out = path.join(box, "written", prop.get("path") as string);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, prop.get("data") as string, "utf8");
  console.log(`sealed ${subj}; written to ${path.relative(box, out)}`);
  return ALIVE;
}

function verify(box: string): number {
  const r = read(path.join(box, "record.jsonl"));
  if (r.breaks.length) {
    console.log(r.breaks.join("\n"));
    return REFUSED;
  }
  const good = sealed(box, r.rows);
  const forged = r.rows.filter((row) => row.get("kind") === "seal" && !good.has(row.get("subject") as string)).map((row) => String(row.get("n")));
  if (forged.length) {
    console.log(`the chain holds; seal rows [${forged.join(", ")}] don't prove under your key`);
    return ESCALATE;
  }
  console.log(`the chain holds: ${r.rows.length} rows, ${good.size} sealed`);
  return ALIVE;
}

function out(box: string, ts: string): number {
  const r = read(path.join(box, "record.jsonl"));
  if (refusedBreaks(r)) return REFUSED;
  const good = sealed(box, r.rows);
  const waiting = r.rows
    .filter((row) => row.get("kind") === "proposal" && row.get("verdict") === "pass" && !good.has(row.get("subject") as string))
    .map((row) => row.get("subject") as string);
  const tip = r.rows.length ? r.rows[r.rows.length - 1].get("hash")! : T.GENESIS;
  append(box, ts, "out", "kernel", obj(["waiting", BigInt(waiting.length)], ["tip", tip]));
  for (const s of waiting) console.log(`waiting on your seal: ${s}`);
  console.log(`${r.rows.length + 1} rows; ${good.size} sealed; ${waiting.length} waiting`);
  return waiting.length ? ESCALATE : ALIVE;
}

// ── the vectors: every case, answered, for compare.py ──────────────────────
function vectors(casesFile: string, resultsFile: string): number {
  const doc = loads(fs.readFileSync(casesFile, "utf8")) as JMap;
  const c = doc.get("cases") as JMap;
  const list = (k: string) => c.get(k) as JMap[];
  const res: JMap = new Map();
  res.set("canon", list("canon").map((o) => {
    const s = canon(o.get("in")!);
    return obj(["canon", s], ["sha256", h256(s)]);
  }));
  res.set("words", list("words").map((o) => {
    const t = o.get("text") as string;
    return obj(["words", words(t)], ["bite_key", biteKey(t)]);
  }));
  const hm = c.get("hmac") as JMap;
  res.set(
    "hmac",
    obj(
      ["rfc4231", (hm.get("rfc4231") as JMap[]).map((m) => obj(["mac", hmac(Buffer.from(m.get("key") as string, "hex"), Buffer.from(m.get("msg") as string, "hex"))]))],
      ["seal", (hm.get("seal") as JMap[]).map((m) => obj(["proof", proof(Buffer.from(m.get("key") as string, "hex"), m.get("subject") as string)]))],
    ),
  );
  res.set("html", list("html").map((o) => obj(["out", htmlText(o.get("in") as string)])));
  res.set("holds", list("holds").map((o) => obj(["out", holds(o.get("w") as string, new Set(o.get("toks") as string[]))])));
  res.set("echo", list("echo").map((o) => obj(["out", echo(o.get("bite") as string, o.get("text") as string)])));
  const piles: Chunk[][] = [];
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "kernel-vectors-"));
  try {
    list("chunks").forEach((o, n) => {
      const root = path.join(tmp, "pile" + n);
      fs.mkdirSync(root, { recursive: true });
      for (const f of o.get("pile") as JMap[]) {
        const p = path.join(root, ...(f.get("parts") as string[]));
        fs.mkdirSync(path.dirname(p), { recursive: true });
        if (f.has("b64")) fs.writeFileSync(p, Buffer.from(f.get("b64") as string, "base64"));
        else fs.writeFileSync(p, (f.get("repeat") as string).repeat(Number(f.get("n") as bigint)) + (f.get("then") as string), "utf8");
      }
      piles.push(chunks(root));
    });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  res.set("chunks", piles.map((ch) => obj(["chunks", ch.map(chunkJson)])));
  // a case's words list, as the vectors hold it ({} when it has none)
  const aliasOf = (o: JMap): Alias =>
    new Map([...((o.get("alias") as JMap | undefined) ?? new Map())].map(([w, ts]) => [w, ts as string[]]));
  res.set("rank", list("rank").map((o) => obj(["ids", rank(piles[Number(o.get("pile") as bigint)], o.get("bite") as string, aliasOf(o)).map((x) => x.id)])));
  res.set("newest", list("newest").map((o) => obj(["newest", newest(o.get("text") as string)])));
  res.set("series", list("series").map((o) => obj(["out", series(o.get("path") as string, o.get("text") as string)])));
  res.set(
    "ids",
    list("ids").map((o) => {
      const got = ids(o.get("text") as string);
      return obj(["ids", got], ["shapes", got.map(shape)]);
    }),
  );
  res.set(
    "wordlist",
    list("wordlist").map((o) => {
      const [alias, why] = parseWords(o.get("text") as string);
      return obj(["alias", new Map([...alias].map(([w, ts]) => [w, ts as J[]]))], ["why", why]);
    }),
  );
  res.set(
    "look",
    list("look").map((o) => {
      const pile = piles[Number(o.get("pile") as bigint)];
      const served = (o.get("served") as bigint[]).map((at) => pile[Number(at)]);
      return obj(["look", look(pile, o.get("bite") as string, served, aliasOf(o))]);
    }),
  );
  res.set(
    "door",
    list("door").map((o) => {
      let prop: J | undefined;
      try {
        prop = loads(o.get("line") as string);
      } catch {
        prop = undefined;
      }
      return obj(["why", door(prop, new Set(o.get("served") as string[]))]);
    }),
  );
  res.set("subject", list("subject").map((o) => obj(["subject", subjectOf(o.get("prop") as JMap)])));
  fs.writeFileSync(resultsFile, canon(res) + "\n");
  return ALIVE;
}

// ── the command line ───────────────────────────────────────────────────────
const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
const USAGE = "kernel.py: the smallest script that runs the whole loop, for anyone. Python 3.9+, stdlib only.";

function main(argv: string[]): number {
  loadTables(process.env.KERNEL_TABLES ?? path.join(__dirname, "..", "..", "kernel_tables.json"));
  VERSION = sha256(fs.readFileSync(__filename));
  if (argv[0] === "vectors") {
    if (argv.length !== 3) {
      console.error("vectors CASES.json RESULTS.json");
      return REFUSED;
    }
    return vectors(argv[1], argv[2]);
  }
  const verb = argv[0] ?? "";
  const opts = new Map<string, string>();
  const args: string[] = [];
  for (let i = 1; i < argv.length; i++) {
    if (["--box", "--pile", "--key", "--file", "--now", "--words"].includes(argv[i])) opts.set(argv[i], argv[++i] ?? "");
    else args.push(argv[i]);
  }
  if (!opts.get("--box")) {
    console.error(USAGE);
    return REFUSED;
  }
  const box = opts.get("--box")!;
  const ts = opts.get("--now") || now();
  if (verb === "init" && opts.get("--pile") && opts.get("--key") && !args.length)
    return init(box, opts.get("--pile")!, opts.get("--key")!, ts, opts.get("--words") || null);
  if (!fs.existsSync(path.join(box, "kernel.json"))) {
    console.error("refused: not a kernel box; init first");
    return REFUSED;
  }
  if (verb === "bite" && args.length === 1) return bite(box, args[0], ts);
  if (verb === "propose" && opts.get("--file") && !args.length)
    return propose(box, splitlines(UTF8.decode(fs.readFileSync(opts.get("--file")!))), ts);
  if (verb === "seal" && args.length === 1 && args[0].startsWith("proposal:")) return seal(box, args[0], ts);
  if (verb === "verify" && !args.length) return verify(box);
  if (verb === "out" && !args.length) return out(box, ts);
  console.error(USAGE);
  return REFUSED;
}

process.exitCode = main(process.argv.slice(2));
