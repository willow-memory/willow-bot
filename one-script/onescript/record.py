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
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Callable

GENESIS = "0" * 64

#: Every row carries these. A line without them is garbled, whatever it parses as.
ROW_KEYS = frozenset(
    {"n", "kind", "who", "family", "standing", "version", "ts", "prev", "hash"}
)
TIP = "tip:"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
        box.mkdir(parents=True, exist_ok=True)

    # ── rows ────────────────────────────────────────────────────────────────
    def _read(self) -> tuple[list[dict], list[int]]:
        """The rows, and the line numbers (1-based) of every line that isn't one."""
        if not self.path.exists():
            return [], []
        rows, garbled = [], []
        text = self.path.read_bytes().decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
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
        return rows, garbled

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
        rows = self.rows()
        row = {
            "n": len(rows),
            "kind": kind,
            "who": who.who,
            "family": who.family,
            "standing": "sealed"
            if (kind == "seal" and who.who == "human")
            else "unattested",
            "version": self.version,
            "ts": self.clock(),
            "prev": rows[-1]["hash"] if rows else GENESIS,
            "where": where,
            **fields,
        }
        row["hash"] = h256(canon(row))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(canon(row) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return row

    def verify_chain(self) -> list[str]:
        """Every break, by row number. Empty means the chain holds."""
        rows, garbled = self._read()
        breaks = [f"line {i}: garbled, not a record row" for i in garbled]
        prev = GENESIS
        for row in rows:
            body = {k: v for k, v in row.items() if k != "hash"}
            if row.get("prev") != prev or h256(canon(body)) != row.get("hash"):
                breaks.append(f"row {row.get('n')}: chain broken")
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
        a = json.loads(self.anchor_path.read_text())
        if not isinstance(a, dict) or not {"n", "hash", "proof"} <= a.keys():
            raise ValueError("not an anchor")
        return a

    def set_anchor(self, tip: dict, proof: str) -> None:
        """Called only by Run.seal_tip, after the gate verified the proof."""
        if self.anchor_path is None:
            raise PermissionError("record: no anchor path; a tip can't be sealed")
        self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
        body = {"n": tip["n"], "hash": tip["hash"], "proof": proof}
        _atomic(self.anchor_path, (canon(body) + "\n").encode())

    def verify_anchor(self, human_key: bytes | None = None) -> list[str]:
        """Every way the chain fails to reach the sealed tip. Empty means it
        reaches it, or nothing was ever sealed (anchor_state() tells which).
        The proof is checked only where the human's key is present."""
        from .gate import verify_seal  # gate imports record; import here

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
        if human_key is not None and not verify_seal(
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
        return json.loads(self.pile_path.read_text())

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
    ) -> dict:
        """`provenance` is where the bytes came from: authored, transcript,
        memory or third-party. It's stamped at write time so a push never has
        to guess it; "unknown" never leaves the box (gate.push_card)."""
        if rel.startswith(("/tmp", "tmp/")) and not expect_vanish:
            raise PermissionError(
                f"record: '{rel}' is temp; nothing kept is written to temp"
            )
        target = self.box / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic(target, data)
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
        return self.append(
            "write", who, where=rel, path=rel, sha=ptr["sha"], cites=cites or []
        )


def tip_subject(tip: dict) -> str:
    """What the human signs to seal a tip: the row number and its full hash."""
    return f"{TIP}{tip['n']}:{tip['hash']}"


def _atomic(path: Path, data: bytes) -> None:
    """Never a half-written file, and readable like any other file (0644)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
