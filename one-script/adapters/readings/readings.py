"""readings: two hints beside a kernel bite, built on top of kernel.py.

    python adapters/readings/readings.py bite --box DIR [--now TS] "your words"

It runs the kernel's own bite, then reads the chunks the kernel served and
adds, beside the kernel's answer:

    numbers over time   in a dated table, what moved in the last week, and the
                        most in any week of the last four
    has this date gone  in a log, a next date passed with nothing written since

Found by the Odile demo (demo/odile/STRIP.md). Built on the kernel's public
surface only (`chunks`, `newest`, `words`, `holds`, `held_by`, `config`,
`read_words`, `main`): the kernel never imports this, and runs the same
without it. It writes nothing: the kernel's bite row is the record, and these
hints, like the kernel's own, are printed for the person and never kept.

A third, "what isn't there" (an id a file naming most of its kind doesn't),
was taken out after the blind runs: on Hollis's and Teodora's folders it
produced only noise (demo/hollis/STRIP.md, demo/teodora/STRIP.md).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import kernel  # noqa: E402
from kernel import held_by, holds, newest, words  # noqa: E402,F401


def cells(line: str) -> list[str]:
    """One line of comma-separated values: a cell in double quotes may hold
    commas, and "" inside it is one quote."""
    out, cur, quoted, i = [], [], False, 0
    while i < len(line):
        ch = line[i]
        if quoted and ch == '"' and line[i + 1 : i + 2] == '"':
            cur.append('"')
            i += 1
        elif ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    return out + ["".join(cur)]


def number(cell: str) -> tuple[int, int] | None:
    """A cell that is a decimal number, as (digits, places): 75.1 is (751, 1).
    ASCII digits, an optional minus and point, nothing else."""
    s = cell.strip()
    whole, _, frac = s[1:].partition(".") if s[:1] == "-" else s.partition(".")
    ok = whole and all("0" <= c <= "9" for c in whole + frac) and (frac or "." not in s)
    if not ok:
        return None
    n = int(whole + frac)
    return (-n if s[:1] == "-" else n), len(frac)


def day(d: str) -> int:
    """Days since 1970-01-01 for a YYYYMMDD, by integer arithmetic alone."""
    y, m, dd = int(d[:4]), int(d[4:6]), int(d[6:])
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + dd - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def shown(n: tuple[int, int]) -> str:
    v, places = n
    digits = str(abs(v)).rjust(places + 1, "0")
    body = digits[: len(digits) - places] + ("." + digits[-places:] if places else "")
    return ("-" if v < 0 else "") + body


def iso(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


WEEK = 7  # days: a change is said against the row a week before the latest
WEEKS = 4  # and the biggest move in any one week, over this many back


def change(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """b - a, exactly, at the finer of their two places."""
    places = max(a[1], b[1])
    return b[0] * 10 ** (places - b[1]) - a[0] * 10 ** (places - a[1]), places


def bigger(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """|a| > |b|, exactly: each scaled by the other's places."""
    return abs(a[0]) * 10 ** b[1] > abs(b[0]) * 10 ** a[1]


def moved(diff: tuple[int, int]) -> str:
    if diff[0] == 0:
        return "no change"
    return f"{'up' if diff[0] > 0 else 'down'} {shown((abs(diff[0]), diff[1]))}"


def week_before(got: list, d: str) -> tuple[str, tuple[int, int]] | None:
    """The latest row at least a week before `d`, or None."""
    before = [(x, n) for x, n in got if day(x) <= day(d) - WEEK]
    return before[-1] if before else None


def series(path: str, text: str) -> list[dict]:
    """Numbers over time: a table (comma-separated, a header first) whose rows
    start with a date, each later than the last, says for each column of
    numbers its latest value and how it stands to the one a week before. A
    ledger repeats its dates, so it is never a series; the kernel says what
    moved, never whether it matters."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3:
        return []
    head, rows = cells(lines[0]), []
    if newest(lines[0]) or len(head) < 2:
        return []
    for ln in lines[1:]:
        cs = cells(ln)
        d = newest(cs[0])
        if d and cs[0].strip() in (d, iso(d)):
            rows.append((d, cs))
    if len(rows) < 2 or any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
        return []
    out = []
    for col in range(1, len(head)):
        got = [(d, n) for d, cs in rows if col < len(cs) and (n := number(cs[col]))]
        if len(got) < 2:
            continue
        (d1, last), name = got[-1], head[col].strip() or f"column {col + 1}"
        if (b := week_before(got, d1)) is None:
            continue
        d0, then = b
        why = f"{name}: {shown(last)} on {iso(d1)}, {moved(change(then, last))} from "
        why += f"{shown(then)} on {iso(d0)}"
        # the biggest move in one week, among the last WEEKS: the latest week
        # can sit under a line that an earlier one crossed
        best = None
        for d, n in got:
            if day(d) > day(d1) - WEEK * WEEKS and (b := week_before(got, d)):
                diff = change(b[1], n)
                if best is None or bigger(diff, best[0]):  # a tie keeps the earlier
                    best = (diff, b[0], d)
        if best is not None and (best[1], best[2]) != (d0, d1):
            why += f"; the most in one week of the last {WEEKS}: {moved(best[0])}"
            why += f", {iso(best[1])} to {iso(best[2])}"
        out.append(
            {
                "why": why,
                "where": f"{path}: {len(rows)} dated rows, {iso(rows[0][0])} to "
                f"{iso(rows[-1][0])}",
            }
        )
    return out


MONTHS = {
    m: n
    for n, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        1,
    )
    for m in names
}  # fmt: skip


def _digit(ch: str) -> bool:
    return len(ch) == 1 and "0" <= ch <= "9"


def _letter(ch: str) -> bool:
    return len(ch) == 1 and ("a" <= ch <= "z" or "A" <= ch <= "Z")


def _runs(text: str) -> list[tuple[int, int]]:
    """Where each run of ASCII letters, or of ASCII digits, starts and ends."""
    out, i = [], 0
    while i < len(text):
        same = _digit if _digit(text[i]) else _letter if _letter(text[i]) else None
        if same is None:
            i += 1
            continue
        j = i
        while j < len(text) and same(text[j]):
            j += 1
        out.append((i, j))
        i = j
    return out


def written(text: str) -> list[tuple[str, str]]:
    """Every date written in `text`, in order, as (YYYYMMDD, as written): ISO
    and compact (`newest`'s), and English month names with a year, a day or
    not between ("Oct 2027" is its first; "March 20, 2026"). Between a month
    and the rest, only spaces, commas and points."""
    out, runs = [], _runs(text)
    for k, (a, b) in enumerate(runs):
        t = text[a:b]
        if _digit(t[0]):
            if len(t) == 8 and (d := newest(t)):
                out.append((d, t))
            elif len(t) == 4 and (d := newest(text[a : a + 10])) and d[:4] == t:
                if text[a : a + 10] == iso(d) and not _digit(text[a + 10 : a + 11]):
                    out.append((d, iso(d)))
            continue
        if t.lower() not in MONTHS or k + 1 >= len(runs):
            continue

        def gap(x: int) -> bool:
            between = text[runs[x - 1][1] : runs[x][0]]
            return bool(between) and all(c in " ,." for c in between)

        def year(x: int) -> str | None:
            y = text[runs[x][0] : runs[x][1]]
            return y if len(y) == 4 and y[:2] in ("19", "20") and gap(x) else None

        m = f"{MONTHS[t.lower()]:02d}"
        nxt = text[runs[k + 1][0] : runs[k + 1][1]]
        if (
            _digit(nxt[0])
            and len(nxt) <= 2
            and 1 <= int(nxt) <= 31
            and gap(k + 1)
            and k + 2 < len(runs)
            and (y := year(k + 2))
        ):
            out.append((y + m + f"{int(nxt):02d}", text[a : runs[k + 2][1]]))
        elif y := year(k + 1):
            out.append((y + m + "01", text[a : runs[k + 1][1]]))
    return out


def gone_by(
    pile: list[dict], served: list[dict], today: str, want: set, alias: dict
) -> list[dict]:
    """Has this date passed: in a log (lines that start with an ISO date, and
    the lines under each), the latest entry under each heading (its first
    line, less the date) that names a later date, now gone by, with nothing
    written under that heading since, on a line that holds one of the bite's
    words. A log kept a file a year is one log: the served file's whole
    folder is read. Odile's "next: May 2026" under
    Harriet's last visit, and no visit since, is a tuning due."""
    out = []
    for folder in dict.fromkeys(c["path"].rpartition("/")[0] for c in served):
        entries, path, cur = [], None, None
        for c in pile:  # chunks order: a file's chunks in a row
            if c["path"].rpartition("/")[0] != folder:
                continue
            if c["path"] != path:
                path, cur = c["path"], None
            for line in c["text"].splitlines():
                s = line.strip()
                d = newest(s[:10])
                if d and s[:10] == iso(d) and not _digit(s[10:11]):
                    cur = (d, s[10:].strip(), [])
                    entries.append(cur)
                # a date counts where its line holds one of the bite's words
                # (or one their list reads it as): "who's due" reaches next:
                if cur is not None and any(
                    held_by(w, set(words(s)), alias) for w in want
                ):
                    cur[2].extend(w for w in written(s) if w[0] > cur[0])
        latest: dict[str, tuple] = {}
        for e in entries:  # a later entry, or a later line on the same day, wins
            if e[1] and e[0] >= latest.get(e[1], ("",))[0]:
                latest[e[1]] = e
        due = sorted(
            (min(e[2]), head)
            for head, e in latest.items()
            if e[2] and min(e[2])[0] <= today
        )
        if due:
            out.append(
                {
                    "why": "gone by, nothing written since: "
                    + "; ".join(f"{h} ({w})" for (_, w), h in due),
                    "where": f"{folder or '.'}/: {len(due)} of {len(latest)} "
                    f"headings, as of {iso(today)}",
                }
            )
    return out


def hints(
    pile: list[dict], served: list[dict], bite: str, alias: dict, today: str
) -> list[dict]:
    """Both readings, for what the kernel served for this bite."""
    want = {w for w in dict.fromkeys(words(bite)) if w not in kernel.FUNCTION}
    out = []
    if today:
        out += gone_by(pile, served, today, want, alias)
    for path in dict.fromkeys(c["path"] for c in served):  # the whole table
        out += series(path, "\n".join(c["text"] for c in pile if c["path"] == path))
    return out


def bite(argv: list[str]) -> int:
    """The kernel's bite, and the readings beside it. Its exit code is the
    kernel's; a refusal or a sealed answer passes through untouched."""
    got = io.StringIO()
    with contextlib.redirect_stdout(got):
        code = kernel.main(["bite", *argv])
    out = got.getvalue()
    try:
        doc = json.loads(out)
    except ValueError:
        print(out, end="")
        return code
    if doc.get("state") not in ("populated", "echo"):
        print(out, end="")
        return code
    opts, rest, it = {}, [], iter(argv)  # the kernel's own reading of argv
    for a in it:
        if a in ("--box", "--now"):
            opts[a] = next(it, "")
        else:
            rest.append(a)
    box = Path(opts["--box"])
    cfg = kernel.config(box)
    alias = kernel.read_words(Path(cfg["words"]), box)[0] if "words" in cfg else {}
    pile = kernel.chunks(Path(cfg["pile"]))
    at = {f"{c['path']}:{c['line']}": c for c in pile}
    served = [at[s["where"]] for s in doc["served"] if not s["echo"]]
    today = newest((opts.get("--now") or kernel.now())[:10])
    if more := hints(pile, served, rest[0], alias or {}, today):
        doc["look"] = doc.get("look", []) + more
    print(json.dumps(doc, ensure_ascii=False))
    return code


if __name__ == "__main__":
    if sys.argv[1:2] != ["bite"]:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        raise SystemExit(kernel.REFUSED)
    raise SystemExit(bite(sys.argv[2:]))
