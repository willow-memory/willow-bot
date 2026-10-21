"""3 record — append-only rows, the stamp, pile pointers.

Cites: CONST-VI (append and read; content inviolable), §0.5.

The record is the reference. Everything else is a view of it or a check on it.
Every row is stamped here, at the door, never by the thing that produced it:
who (an identity the gate verified), standing (always "unattested" on write),
the run's own version, the turn, and the time from the injected clock. Rows are
hash-chained, so an edit to a past row breaks every hash after it.

The chain alone can't see a cut or a rewrite: anyone who can write the file can
drop the last rows, or rehash every row after a forged one. So the tip the
human sealed is kept outside the box, in the anchor, with the seal proof that
only the human's key makes. A chain that no longer reaches that tip, or reaches
it with a different hash, is a break. Rows after the last sealed tip are still
open to both; the anchor covers what the human has sealed, no further.

A line that doesn't parse as a row is reported as a break by line number,
never a crash, so the hard-close rule (report, options, wait) still applies.

A crash mid-append can leave a last line with no newline. That line is not yet
written: rows() and verify_chain() leave it out and torn_tail() reports it, so
it is never a chain break. The same garble on any other line is a break. An
append refuses to land after a torn tail (TornTail) rather than glue its row
onto the fragment or silently drop it; a human decides, then truncates.

Durability: a row is fsynced, and the first row of a new record.jsonl also
fsyncs the directory so the file's name survives a crash. An fsync error
raises and is never retried (fsyncgate: after a failed fsync the kernel may
have dropped the dirty pages, so a second fsync can report success on data
that is gone).

THE KERNEL (flatten-2, the record piece; the deterministic loop, assert half).
Asserted forward, held by tests/test_record_kernel.py:
  R1  `record` imports only `canon`, `states` and `sign` (no gate, no graph,
      no nestor_seal, no mai_shape). The seal check is `sign.verify_seal`;
      the document-shape check is a `check=` hook the caller passes.
  R2  No byte of any row, anchor or file the record writes changes.
  R3  Every existing caller still gets what moved: verify_anchor's seal check
      (same HMAC as `gate.verify_seal`, which now delegates) and write_file's
      @markdownai shape check (each production caller passes `mai_shape.check`).
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import secrets
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

try:
    import fcntl
except ImportError:  # not POSIX: no flock; _locked() says so and runs unlocked
    fcntl = None  # type: ignore[assignment]

from . import canon as _canon
from . import sign as _sign

GENESIS = "0" * 64


class TornTail(RuntimeError):
    """The record ends in an unterminated line: a crash cut an append short.
    Nothing is appended until a human has looked at it."""


#: Every row carries these. A line without them is garbled, whatever it parses as.
ROW_KEYS = frozenset(
    {"n", "kind", "who", "family", "standing", "version", "ts", "prev", "hash"}
)
TIP = "tip:"


#: Family A (`onescript/canon.py`, flatten-2 F7): sorted, compact, non-ASCII literal.
canon = _canon.sorted_compact_utf8


def h256(data: bytes | str) -> str:
    """The full SHA-256, 256 bits. Never truncated: a cut digest is a cut chain."""
    return hashlib.sha256(
        data if isinstance(data, bytes) else data.encode()
    ).hexdigest()


def version_of(pkg_dir: Path) -> str:
    """The run's own version: one hash over every .py file in the package, in order."""
    files = sorted(p for p in pkg_dir.glob("*.py"))
    return h256(b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in files))


class Verified:
    """An identity the gate checked. Only gate.verify() makes one."""

    __slots__ = ("who", "family", "_token")

    def __init__(self, who: str, family: str, token: object):
        self.who, self.family, self._token = who, family, token


class Record:
    def __init__(
        self,
        box: Path,
        clock: Callable[[], str],
        version: str,
        token: object,
        anchor: Path | None = None,
    ):
        self.box, self.clock, self.version, self._token = box, clock, version, token
        self.path = box / "record.jsonl"
        self.pile_path = box / "pile.json"
        if anchor is not None and anchor.resolve().is_relative_to(box.resolve()):
            raise PermissionError("record: the anchor can't live inside the box")
        self.anchor_path = anchor
        # The flock is per open file, so a second acquisition in this process
        # would block on the first: one lock, counted, and a thread lock beside it.
        self._tlock = threading.RLock()
        self._depth = 0
        # The last full parse, keyed by the file's (inode, size, mtime): one
        # verb reads the record several times, and a parse costs per row.
        self._parsed: tuple | None = None
        # The last read from the sealed tip, keyed by the record's and the
        # anchor's stat: what one verb's several reads share.
        self._tipped: tuple | None = None
        box.mkdir(parents=True, exist_ok=True)

    # ── rows ────────────────────────────────────────────────────────────────
    @contextmanager
    def _locked(self) -> Iterator[None]:
        """One writer at a time per box. Two processes (two per-prompt hook
        calls) that each read the tail before either appended would link to the
        same prev and fork the chain. The lock is an exclusive flock on
        `record.lock` in the box, held over read-tail, hash and append, and
        the tail is read under it, never cached. Without fcntl (not POSIX) there
        is no lock to take: that is said here, and the append runs as it did
        before, unserialized. Reentrant within a process: write_file holds it
        over the pile update and the row, and append takes it again."""
        with self._tlock:
            if fcntl is None or self._depth:  # reentrant: this process holds it
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            with (self.box / "record.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _stat_key(self) -> tuple | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _scan(self, fresh: bool = False) -> tuple[list[dict], list[int], int | None]:
        """The rows, the 1-based line numbers of every garbled line, and the
        line number of an unterminated last line (None when the file ends in a
        newline or is empty). The unterminated line is in neither of the others.

        Reused from the last parse while the file's (inode, size, mtime) is
        what it was then, or what this record's own append left it (under the
        lock); `fresh` always reads the file. Callers get their own copies."""
        key = self._stat_key()
        if not fresh and key is not None and self._parsed and self._parsed[0] == key:
            _, rows, garbled, torn = self._parsed
            return [dict(r) for r in rows], list(garbled), torn
        got = self._scan_file()
        self._parsed = (key, *got) if key is not None else None
        rows, garbled, torn = got
        return [dict(r) for r in rows], list(garbled), torn

    def _scan_file(self) -> tuple[list[dict], list[int], int | None]:
        if not self.path.exists():
            return [], [], None
        rows, garbled, torn = [], [], None
        text = self.path.read_bytes().decode("utf-8", errors="replace")
        # "\n" only: canon() writes ensure_ascii=False, so a row can hold a raw
        # U+2028/U+2029/U+0085 that str.splitlines() would cut the row at.
        lines = text.split("\n")
        tail = lines.pop()  # "" when the file ends in a newline
        if tail.strip():
            torn = len(lines) + 1
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if isinstance(row, dict) and ROW_KEYS <= row.keys():
                rows.append(row)
            else:
                garbled.append(i)
        return rows, garbled, torn

    @staticmethod
    def _row_of(line: str) -> dict | None:
        try:
            row = json.loads(line)
        except ValueError:
            return None
        return row if isinstance(row, dict) and ROW_KEYS <= row.keys() else None

    def _anchor_key(self) -> tuple | None:
        try:
            st = self.anchor_path.stat() if self.anchor_path else None
        except OSError:
            return None
        return None if st is None else (st.st_ino, st.st_size, st.st_mtime_ns)

    def _from_tip(self):
        """`_read_from_tip`, remembered while neither file has changed."""
        key = (self._stat_key(), self._anchor_key())
        if None in key:
            return None
        if self._tipped is not None and self._tipped[0] == key:
            return self._tipped[1]
        view = self._read_from_tip()
        self._tipped = (key, view)
        return view

    def _read_from_tip(self):
        """The record read from the human's sealed tip on, or None.

        The anchor (outside the box) names the tip row, {n, hash}. Its line is
        found by counting lines, not parsing them; it must be the n-th
        non-blank line and parse to that very row, or this is None and the
        caller reads the whole file. What comes back: the tip row, the rows
        after it, the raw lines before it (unparsed) and the torn tail's text,
        if any. The rows before the tip are taken to be the n rows the human
        sealed; `verify_chain` (and so every check-in) still reads them all."""
        try:
            a = self.anchor()
            data = self.path.read_bytes()
        except (ValueError, OSError):
            return None
        if not a or not isinstance(a.get("n"), int) or isinstance(a.get("n"), bool):
            return None
        lines = data.split(b"\n")
        tail = lines.pop()
        seen, at = -1, None
        for i, raw in enumerate(lines):
            if raw.strip():
                seen += 1
                if seen == a["n"]:
                    at = i
                    break
        if at is None:
            return None
        tip = self._row_of(lines[at].decode("utf-8", errors="replace"))
        if tip is None or tip.get("n") != a["n"] or tip.get("hash") != a["hash"]:
            return None
        after = []
        for raw in lines[at + 1 :]:
            text = raw.decode("utf-8", errors="replace")
            if text.strip() and (row := self._row_of(text)) is not None:
                after.append(row)
        torn = tail.decode("utf-8", errors="replace") if tail.strip() else None
        return tip, after, lines[:at], torn

    def _cached(self) -> tuple[list[dict], list[int], int | None] | None:
        """The remembered parse, while the file is still what it parsed."""
        key = self._stat_key()
        if key is not None and self._parsed and self._parsed[0] == key:
            return self._parsed[1], self._parsed[2], self._parsed[3]
        return None

    def tail_state(self) -> tuple[int, str, bool]:
        """(rows on record, the last row's hash or GENESIS, a torn tail?): what
        an append needs, from the remembered parse, else from the sealed tip,
        else from the whole file."""
        got = self._cached()
        if got is None and (view := self._from_tip()) is not None:
            tip, after, _, torn = view
            last = after[-1]["hash"] if after else tip["hash"]
            return tip["n"] + 1 + len(after), last, torn is not None
        rows, _, torn = got if got is not None else self._scan()
        return len(rows), rows[-1]["hash"] if rows else GENESIS, torn is not None

    def since_last(self, kind: str) -> tuple[dict | None, list[dict]]:
        """The last `kind` row and every row after it ((None, all rows) when
        there is none): from the remembered parse, else from the sealed tip
        when that row is after the tip, else from the whole file."""
        got = self._cached()
        if got is None and (view := self._from_tip()) is not None:
            tip, after, before, _ = view
            i = max((k for k, r in enumerate(after) if r["kind"] == kind), default=-1)
            if i >= 0:
                return after[i], [dict(r) for r in after[i + 1 :]]
            if tip["kind"] == kind:
                return tip, [dict(r) for r in after]
            # before the tip: back from it, parsing only lines that carry the
            # kind's own bytes, then the rows from that one on (this session's)
            needle = canon({"kind": kind})[1:-1].encode()
            for j in range(len(before) - 1, -1, -1):
                if needle not in before[j]:
                    continue
                row = self._row_of(before[j].decode("utf-8", errors="replace"))
                if row is None or row["kind"] != kind:
                    continue
                between = [
                    r
                    for raw in before[j + 1 :]
                    if raw.strip()
                    and (r := self._row_of(raw.decode("utf-8", errors="replace")))
                    is not None
                ]
                return row, [dict(r) for r in [*between, tip, *after]]
        rows = [dict(r) for r in got[0]] if got is not None else self.rows()
        i = max((k for k, r in enumerate(rows) if r["kind"] == kind), default=-1)
        return (rows[i] if i >= 0 else None), rows[i + 1 :]

    def has_kind(self, kind: str) -> bool:
        """Whether any row is of `kind`: the remembered parse, else the tail
        after the sealed tip and, before it, only the lines that hold the
        kind's own text (canon writes it as one run of bytes), else the file."""
        got = self._cached()
        if got is None and (view := self._from_tip()) is not None:
            tip, after, before, _ = view
            if any(r["kind"] == kind for r in [tip, *after]):
                return True
            needle = canon({"kind": kind})[1:-1].encode()  # "kind":"<kind>"
            return any(
                (row := self._row_of(raw.decode("utf-8", errors="replace"))) is not None
                and row["kind"] == kind
                for raw in before
                if needle in raw
            )
        rows = got[0] if got is not None else self.rows()
        return any(r["kind"] == kind for r in rows)

    def _read(self, fresh: bool = False) -> tuple[list[dict], list[int]]:
        """The rows, and the line numbers (1-based) of every line that isn't one.
        An unterminated last line is neither: see torn_tail()."""
        rows, garbled, _ = self._scan(fresh)
        return rows, garbled

    def torn_tail(self) -> str | None:
        """Says so when the record ends in an unterminated line (a crash cut an
        append short): not yet written, left out of rows(), not a break."""
        torn = self._scan(fresh=True)[2]
        return (
            None
            if torn is None
            else f"line {torn}: unterminated last line, not yet written (a crash "
            "cut an append short); ignored, and appends are refused until it is removed"
        )

    def rows(self) -> list[dict]:
        """The rows that parse. A garbled line is left out here and reported by
        verify_chain(), which every check-in runs first."""
        return self._read()[0]

    def append(
        self, kind: str, who: Verified, where: str | None = None, **fields
    ) -> dict:
        """Every row says where: the file written, the change judged, the act's
        destination, the subject sealed. None when the caller can't say; the
        record never guesses a place."""
        if not isinstance(who, Verified) or who._token is not self._token:
            raise PermissionError(
                "record: unverified identity; the stamp comes from the gate"
            )
        with self._locked():
            # the true tail, read under the lock: count, last hash, torn
            count, last, torn = self.tail_state()
            base = self._cached()  # the remembered parse this append extends
            pre = (self._stat_key(), self._anchor_key())  # the file this row joins
            if torn:
                raise TornTail(
                    f"record: {self.torn_tail()}. Refusing to append after it; "
                    "a human truncates the fragment (or restores it) first."
                )
            created = not self.path.exists()
            row = {
                "n": count,
                "kind": kind,
                "who": who.who,
                "family": who.family,
                "standing": "sealed"
                if (kind == "seal" and who.who == "human")
                else "unattested",
                "version": self.version,
                "ts": self.clock(),
                "prev": last,
                "where": where,
                **fields,
            }
            row["hash"] = h256(canon(row))
            line = canon(row)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())  # an error raises; never retried (fsyncgate)
            # Under the lock the file is what was read plus this line: the
            # remembered parse grows by the row as a reader would parse it.
            tipped = self._tipped  # extended only if it was read of this very file
            if tipped is not None and tipped[0] == pre and tipped[1] is not None:
                tip, after, before, _ = tipped[1]
                key = (self._stat_key(), self._anchor_key())
                self._tipped = (key, (tip, [*after, json.loads(line)], before, None))
            if base is not None:
                self._parsed = (
                    self._stat_key(),
                    [*base[0], json.loads(line)],
                    list(base[1]),
                    None,
                )
            if created:
                _fsync_dir(self.box)  # the new file's name must survive a crash
        return row

    def verify_chain(self) -> list[str]:
        """Every break, by row number. Empty means the chain holds.

        A row's `n` is its 0-based place in the record, and is checked as
        such: every reader orders by `n` (the sealed tip, the pin, a seal's
        place before or after a tip, a forecast before its outcome), and the
        hash chain alone only binds `n` to the bytes, not to the place. Rows
        after the sealed tip can be rewritten and re-hashed; a row whose label
        says it came earlier than it did would otherwise read as earlier."""
        rows, garbled = self._read(fresh=True)  # the file, never a remembered parse
        breaks = [f"line {i}: garbled, not a record row" for i in garbled]
        prev = GENESIS
        for i, row in enumerate(rows):
            body = {k: v for k, v in row.items() if k != "hash"}
            if row.get("prev") != prev or h256(canon(body)) != row.get("hash"):
                breaks.append(f"row {row.get('n')}: chain broken")
            n = row.get("n")
            if isinstance(n, bool) or n != i:
                breaks.append(f"row {n!r}: out of place, it is row {i} of the record")
            prev = row.get("hash")
        return breaks

    # ── the anchor: the sealed tip, kept where the record can't rewrite it ──
    def tip(self) -> dict | None:
        rows = self.rows()
        return {"n": rows[-1]["n"], "hash": rows[-1]["hash"]} if rows else None

    def anchor(self) -> dict | None:
        """The last sealed tip, or None when nothing was ever sealed.
        Unreadable is not None: it raises, and verify_anchor() says so."""
        if self.anchor_path is None or not self.anchor_path.exists():
            return None
        a = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        if not isinstance(a, dict) or not {"n", "hash", "proof"} <= a.keys():
            raise ValueError("not an anchor")
        return a

    def set_anchor(
        self, tip: dict, proof: str, beacon_latest: int | None = None
    ) -> None:
        """Called only by Run.seal_tip, after the gate verified the proof.
        `beacon_latest` is the operator's beacon reader's newest round as the
        human sealed this tip (R23a). It is kept HERE, outside the box, per tip
        row (`rounds`, carried over from the anchor it replaces), never only on
        the seal row, which the writer could rewrite until the next tip."""
        if self.anchor_path is None:
            raise PermissionError("record: no anchor path; a tip can't be sealed")
        self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        body = {"n": tip["n"], "hash": tip["hash"], "proof": proof}
        try:
            rounds = dict((self.anchor() or {}).get("rounds") or {})
        except (ValueError, OSError):
            rounds = {}
        if beacon_latest is not None:
            rounds[str(tip["n"])] = beacon_latest
        if rounds:
            body["rounds"] = rounds
        _atomic(self.anchor_path, (canon(body) + "\n").encode())

    def verify_anchor(self, human_key: bytes | None = None) -> list[str]:
        """Every way the chain fails to reach the sealed tip. Empty means it
        reaches it, or nothing was ever sealed (anchor_state() tells which).
        The proof is checked only where the human's key is present."""
        rows = self.rows()
        sealed = [
            r
            for r in rows
            if r["kind"] == "seal" and str(r.get("subject", "")).startswith(TIP)
        ]
        try:
            a = self.anchor()
        except (ValueError, OSError) as e:
            return [f"anchor: unreadable ({e}); the sealed tip can't be checked"]
        if a is None:
            return (
                [f"anchor: missing, but row {sealed[-1]['n']} sealed a tip"]
                if sealed
                else []
            )
        out = []
        if human_key is not None and not _sign.verify_seal(
            tip_subject(a), a["proof"], human_key
        ):
            out.append("anchor: its seal proof does not verify")
        at = next((r for r in rows if r["n"] == a["n"]), None)
        if at is None:
            last = rows[-1]["n"] if rows else None
            out.append(
                f"record: ends at row {last}, before the sealed tip at row {a['n']}; "
                "rows were cut"
            )
        elif at["hash"] != a["hash"]:
            out.append(
                f"record: row {a['n']} is not the row the human sealed; "
                "the chain was rewritten"
            )
        return out

    def anchor_state(self) -> str:
        """never (nothing sealed), sealed (a tip on record), or unreadable."""
        try:
            return "never" if self.anchor() is None else "sealed"
        except (ValueError, OSError):
            return "unreadable"

    # ── turns: a crash leaves an open row, not a gap ─────────────────────────
    def open_turn(self, who: Verified, turn: int, intent: str) -> dict:
        return self.append("turn_open", who, turn=turn, intent=intent)

    def close_turn(self, who: Verified, turn: int) -> dict:
        return self.append("turn_close", who, turn=turn)

    # ── files: every write adds its own pointer ──────────────────────────────
    def pile(self) -> dict:
        if not self.pile_path.exists():
            return {"items": []}
        return json.loads(self.pile_path.read_text(encoding="utf-8"))

    def _save_pile(self, pile: dict) -> None:
        pile["items"].sort(key=lambda i: i["where"])
        _atomic(
            self.pile_path, (json.dumps(pile, indent=1, sort_keys=True) + "\n").encode()
        )

    def write_file(
        self,
        who: Verified,
        rel: str,
        data: bytes,
        *,
        live: bool = False,
        expect_vanish: bool = False,
        cites: list[str] | None = None,
        provenance: str = "unknown",
        check: Callable[[str, bytes], str | None] | None = None,
    ) -> dict:
        """`provenance` is where the bytes came from: authored, transcript,
        memory or third-party. It's stamped at write time so a push never has
        to guess it; "unknown" never leaves the box (gate.push_card).

        `check(rel, data)` is the caller's shape check, run before anything is
        written: it raises PermissionError to refuse, or returns a lint word
        (stamped on the row as `lint`) or None. The record knows no document
        shape; every caller that writes a world doc passes `mai_shape.check`."""
        if rel.startswith(("/tmp", "tmp/")) and not expect_vanish:
            raise PermissionError(
                f"record: '{rel}' is temp; nothing kept is written to temp"
            )
        lint = (
            check(rel, data) if check else None
        )  # T-mai-1 (callers pass mai_shape.check)
        with self._locked():  # file, pointer and row: one writer start to end
            return self._write_file(
                who, rel, data, lint, live, expect_vanish, cites, provenance
            )

    def _write_file(
        self, who, rel, data, lint, live, expect_vanish, cites, provenance
    ) -> dict:
        _write_in_box(self.box, rel, data)
        ptr = {
            "where": rel,
            "sha": h256(data),
            "bytes": len(data),
            "provenance": provenance,
        }
        if live:
            ptr["live"] = True
        if expect_vanish:
            ptr["expect_vanish"] = True
        pile = self.pile()
        pile["items"] = [i for i in pile["items"] if i["where"] != rel] + [ptr]
        self._save_pile(pile)
        extra = {"lint": lint} if lint else {}
        return self.append(
            "write",
            who,
            where=rel,
            path=rel,
            sha=ptr["sha"],
            cites=cites or [],
            **extra,
        )


def tip_subject(tip: dict) -> str:
    """What the human signs to seal a tip: the row number and its full hash."""
    return f"{TIP}{tip['n']}:{tip['hash']}"


def _write_in_box(box: Path, rel: str, data: bytes) -> None:
    """Write `data` at `rel` under `box`, and nowhere else.

    Every component of `rel` is opened relative to the one before it with
    O_NOFOLLOW, so a symlink in the box (planted by anyone who can write there)
    is refused rather than followed out. The file lands by an atomic rename in
    the directory held open, never by a path resolved again after the check.
    Raises PermissionError for a path that is not plain relative, or that
    crosses a symlink."""
    parts = rel.split("/")
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise PermissionError(f"record: '{rel}' is not a path inside the box")
    box.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    held = [os.open(box, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for part in parts[:-1]:
            try:
                nxt = os.open(part, flags, dir_fd=held[-1])
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=held[-1])
                nxt = os.open(part, flags, dir_fd=held[-1])
            held.append(nxt)
        name = parts[-1]
        tmp = f".{name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=held[-1],
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fchmod(f.fileno(), 0o644)
                os.fsync(f.fileno())
            os.replace(tmp, name, src_dir_fd=held[-1], dst_dir_fd=held[-1])
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=held[-1])
            except OSError:
                pass
            raise
    except OSError as e:
        if isinstance(e, PermissionError):
            raise
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise PermissionError(
                f"record: '{rel}' crosses a symlink or a file; nothing written"
            ) from e
        raise
    finally:
        for d in held:
            os.close(d)


def _fsync_dir(path: Path) -> None:
    """fsync a directory so a created or renamed entry is durable. Raises on
    error and never retries (fsyncgate). Not every platform can open a directory
    (Windows): there this is a no-op, said here."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic(path: Path, data: bytes) -> None:
    """Never a half-written file, and readable like any other file (0644)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
