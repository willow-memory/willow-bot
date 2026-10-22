"""life.py -- Conway's Game of Life, as the table's verdict rows.

The most deterministic game there is: no players and no dice, so the first
board fixes every later one. The record holds that as rows:

  play   ``play(cfg, session, board=RLE | soup={seed, w, h, density},
         generations=N, rule="B3/S23")``: one ``open`` row (the rule and the
         first board, and for a soup its seed, drawn on the one stream), one
         ``step`` row per generation (its population, where it sits, and the
         SHA-256 of the board), and one ``result`` row: how it ended. Every
         row is a ``verdict`` written as the ``table`` identity
         (``table.verdict``'s gate, opened once), so none can be a seal.
  check  ``check(cfg, session)``: reads the session back and plays it again
         from its ``open`` row. ``satisfied`` when every step and the result
         are what the rule makes of that board, else ``failing`` at the first
         row that isn't.

The board is the live cells on an unbounded plane. Its text is RLE (the Life
community's format), written one way only, from the top-left live cell, so
one board has one hash wherever it sits; the step row says where it sits.
How it ended (``fate``) comes from the first repeat of that canonical hash:
``dies`` (nothing left), ``still`` (period 1, in place), ``oscillator``
(period p, in place), ``spaceship`` (period p, moved by ``shift``), or
``running`` (no repeat within the generations asked). A pattern that throws
off gliders, like the R-pentomino, never repeats as a whole and stays
``running``; its known populations are the engine's test.

A rule is any Life-like ``B.../S...`` (birth and survival counts, 0-8).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from . import api, table
from .states import EMPTY
from .stream import GENERATOR_ID, Stream

GAME = "life"
CONWAY = "B3/S23"
MAX_GENERATIONS = 5000
MAX_SOUP = 64  # a soup's side, in cells
SOUP_LABEL = b"life soup"

DIES, STILL, OSCILLATOR, SPACESHIP, RUNNING = (
    "dies", "still", "oscillator", "spaceship", "running",
)  # fmt: skip

Board = frozenset  # of (row, col)

_RULE = re.compile(r"^B([0-8]*)/S([0-8]*)$")


# ── the engine ────────────────────────────────────────────────────────────
def parse_rule(rule: str) -> tuple[frozenset, frozenset]:
    m = _RULE.match(rule.strip().upper()) if isinstance(rule, str) else None
    if m is None:
        raise ValueError(f"a rule is B<digits>/S<digits>, like {CONWAY}: {rule!r}")
    if "0" in m.group(1):
        raise ValueError("B0 fills the infinite plane at once: no board can hold it")
    return frozenset(map(int, m.group(1))), frozenset(map(int, m.group(2)))


def step(board: Board, rule: str = CONWAY) -> Board:
    born, keep = parse_rule(rule)
    counts: dict[tuple[int, int], int] = {}
    for r, c in board:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr or dc:
                    k = (r + dr, c + dc)
                    counts[k] = counts.get(k, 0) + 1
    alone = frozenset(k for k in board if k not in counts)  # no neighbour at all
    return frozenset(
        k for k, n in counts.items() if (n in keep if k in board else n in born)
    ) | (alone if 0 in keep else frozenset())


def origin(board: Board) -> tuple[int, int]:
    """The top-left corner of the board's bounding box ((0, 0) when empty)."""
    if not board:
        return (0, 0)
    return (min(r for r, _ in board), min(c for _, c in board))


def rle(board: Board) -> str:
    """The board's one RLE text, from its own top-left corner."""
    if not board:
        return "!"
    r0, c0 = origin(board)
    rows: dict[int, list[int]] = {}
    for r, c in board:
        rows.setdefault(r - r0, []).append(c - c0)
    out, last = [], 0
    for r in sorted(rows):
        if r > last:
            out.append(f"{r - last if r - last > 1 else ''}$")
        col, cells = 0, sorted(rows[r])
        i = 0
        while i < len(cells):
            j = i
            while j + 1 < len(cells) and cells[j + 1] == cells[j] + 1:
                j += 1
            if cells[i] > col:
                gap = cells[i] - col
                out.append(f"{gap if gap > 1 else ''}b")
            run = j - i + 1
            out.append(f"{run if run > 1 else ''}o")
            col, i = cells[j] + 1, j + 1
        last = r
    text = "".join(out) + "!"
    return "\n".join(text[i : i + 70] for i in range(0, len(text), 70))


def parse_rle(text: str) -> Board:
    """Live cells from RLE: ``#`` comments and the ``x = ...`` header are
    skipped, ``b`` is dead, ``o`` (or any other letter) alive, ``$`` ends a
    row, ``!`` ends the board."""
    cells, r, c, n = set(), 0, 0, ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.lower().startswith("x"):
            continue
        for ch in s:
            if ch.isdigit():
                n += ch
                continue
            k = int(n) if n else 1
            n = ""
            if ch == "!":
                return frozenset(cells)
            if ch == "$":
                r, c = r + k, 0
            elif ch in "b.":
                c += k
            elif ch.isalpha():
                cells.update((r, c + i) for i in range(k))
                c += k
            elif not ch.isspace():
                raise ValueError(f"not RLE: {ch!r}")
    return frozenset(cells)


def board_sha(board: Board) -> str:
    return hashlib.sha256(rle(board).encode("ascii")).hexdigest()


def soup(seed: str, w: int, h: int, density: int) -> Board:
    """A random first board, drawn on the one stream: each cell of the w x h
    box is alive when a draw below 100 is under ``density`` (a percent)."""
    if not (1 <= w <= MAX_SOUP and 1 <= h <= MAX_SOUP and 0 <= density <= 100):
        raise ValueError(f"a soup is 1..{MAX_SOUP} on a side, density 0..100")
    s = Stream.from_seed(seed.encode("utf-8"), SOUP_LABEL)
    return frozenset(
        (r, c) for r in range(h) for c in range(w) if s.below(100) < density
    )


def run(board: Board, generations: int, rule: str = CONWAY) -> tuple[list, dict]:
    """Every generation after the first, as (board, its sha), and the fate."""
    parse_rule(rule)
    seen = {board_sha(board): (0, origin(board))}
    out = []
    for g in range(1, generations + 1):
        board = step(board, rule)
        sha = board_sha(board)
        out.append((board, sha))
        if not board:
            return out, {"fate": DIES, "at": g}
        if sha in seen:
            g0, (r0, c0) = seen[sha]
            r1, c1 = origin(board)
            shift = [r1 - r0, c1 - c0]
            period = g - g0
            fate = (
                SPACESHIP if shift != [0, 0] else STILL if period == 1 else OSCILLATOR
            )
            return out, {"fate": fate, "at": g0, "period": period, "shift": shift}
        seen[sha] = (g, origin(board))
    return out, {"fate": RUNNING, "at": generations}


# ── the record ────────────────────────────────────────────────────────────
def _first(board: object, soup_: object) -> tuple[Board | None, dict, str | None]:
    """(the first board, what the open row says of it, why not)."""
    if (board is None) == (soup_ is None):
        return None, {}, "a game starts from a board (RLE) or a soup, one of them"
    try:
        if board is not None:
            if not isinstance(board, str):
                return None, {}, "a board is RLE text"
            return parse_rle(board), {}, None
        if not isinstance(soup_, dict) or set(soup_) != {"seed", "w", "h", "density"}:
            return None, {}, "a soup is {seed, w, h, density}"
        if not isinstance(soup_["seed"], str) or not soup_["seed"]:
            return None, {}, "a soup's seed is text"
        b = soup(soup_["seed"], soup_["w"], soup_["h"], soup_["density"])
        return b, {"soup": dict(soup_), "generator": GENERATOR_ID}, None
    except (TypeError, ValueError) as e:
        return None, {}, str(e)


def play(
    cfg: api.Config,
    session: str,
    *,
    board: str | None = None,
    soup: dict | None = None,
    generations: int = 100,
    rule: str = CONWAY,
    argv: Iterable[str] = ("life",),
) -> dict:
    """Play one game into the record: open, a step per generation, a result.
    Refused, with only the refusal appended, for a closed box, no table key,
    a session already on record, or a board, rule or count that won't do."""
    first, said, why = _first(board, soup)
    if why is None:
        try:
            rule = "B{}/S{}".format(
                *("".join(map(str, sorted(x))) for x in parse_rule(rule))
            )
        except ValueError as e:
            why = str(e)
    if why is None and not (
        isinstance(generations, int) and 1 <= generations <= MAX_GENERATIONS
    ):
        why = f"generations is 1..{MAX_GENERATIONS}"
    run_, info = api._open(cfg, "life", argv)
    if run_ is None:
        return info
    if (refusal := api._need_open(run_, "life")) is not None:
        return refusal
    who, bad = table._identity(run_, None)
    why = why or bad
    if why is None and session_rows(run_.rec.rows(), session):
        why = f"session {session!r} is on record already"
    opened = {"what": "open", "session": session, "game": GAME, "rule": rule}
    if why is None:
        opened.update(said, board=rle(first), population=len(first))
        why = table.check_row(opened)
    if why is not None:
        got = run_.rec.append("refused", run_.sys, at="life", reason=why)
        return {"refused": why, "row": got, "code": 1}
    steps, fate = run(first, generations, rule)
    rows = [opened]
    for g, (b, sha) in enumerate(steps, 1):
        rows.append({
            "what": "step", "session": session, "game": GAME, "gen": g,
            "population": len(b), "at": list(origin(b)), "board_sha256": sha,
        })  # fmt: skip
    rows.append({"what": "result", "session": session, "game": GAME, **fate})
    for r in rows:
        if (bad := table.check_row(r)) is not None:  # never on a row the rule made
            raise AssertionError(f"life wrote a row table.check_row refuses: {bad}")
    written = [run_.rec.append("verdict", who, **r) for r in rows]
    return {"rows": written, "fate": fate, "code": 0}


def session_rows(rows: list[dict], session: str) -> list[dict]:
    return [
        r
        for r in rows
        if r.get("kind") == "verdict"
        and r.get("game") == GAME
        and r.get("session") == session
    ]


def check(
    cfg: api.Config, session: str, *, argv: Iterable[str] = ("life-check",)
) -> dict:
    """Play the session again from its open row and hold every row to it."""
    run_, info = api._open(cfg, "life-check", argv, write=False)
    if run_ is None:
        return info
    srows = session_rows(run_.rec.rows(), session)
    if not srows:
        return {"state": EMPTY, "why": f"no life session {session!r}", "code": 0}

    def failing(why: str) -> dict:
        return {"state": table.FAILING, "why": why, "code": 1}

    opened, rest = srows[0], srows[1:]
    if opened.get("what") != "open" or not rest or rest[-1].get("what") != "result":
        return failing("a session is one open, its steps, and one result")
    first, _, why = _first(
        opened.get("board") if "soup" not in opened else None, opened.get("soup")
    )
    if why is not None:
        return failing(f"the open row: {why}")
    if rle(first) != opened.get("board"):
        return failing("the soup's seed doesn't make the board on record")
    steps, fate = run(first, len(rest) - 1, opened.get("rule", CONWAY))
    if len(steps) != len(rest) - 1:
        return failing(f"{len(rest) - 1} steps on record, the rule makes {len(steps)}")
    for g, ((b, sha), r) in enumerate(zip(steps, rest), 1):
        want = {
            "gen": g,
            "population": len(b),
            "at": list(origin(b)),
            "board_sha256": sha,
        }
        got = {k: r.get(k) for k in want}
        if r.get("what") != "step" or got != want:
            return failing(f"generation {g} (row {r.get('n')}) isn't the rule's")
    got = {k: v for k, v in rest[-1].items() if k in fate}
    if got != fate or set(rest[-1]) & {"fate", "at", "period", "shift"} != set(fate):
        return failing(f"the result says {got}, the rule says {fate}")
    return {
        "state": table.SATISFIED,
        "rows": len(srows),
        "fate": fate,
        "code": 0,
    }
