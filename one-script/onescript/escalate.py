"""escalate — willow-bot's deterministic chain, run on the sealed served scope.

    python3 -m onescript escalate "<task>"

Code tables what it can, cuts what it escalates into small pieces, hands each
piece to a local rung, records every answer and every escalation by hash, and
turns whatever no rung answered into one human card.

The rulings this keeps (the operator's words, sealed):

- "Lets keep the tree" (bfe001f7): D0 first, the local tier only on ESCALATE,
  cloud only through the flowering checklist. Amended by "I still think the
  locals can do it, if we give them smaller pieces." (a9a14ce7): the local
  rung is not skipped, it works on small pieces.
- "In order to escalate the model would have to propose the reason why"
  (67d98145): an escalation is a proposal that gives its reason. It is never
  written as a file; it is recorded by hash, so the same piece stops at rung 1
  next time.
- Code first; ESCALATE is never a failure. "Everything goes through Willow":
  an escalated route goes to `willow`.
- "only along side" (6236a815): no proposals unless the served scope is
  populated. An empty or unreachable scope is escalated straight up by code
  and never handed to a model.

The rungs, in order, for each act:

    d0      code: `resolve_fixture` over the act. Whatever it closes is answered.
            Only its `escalate` goes on. Code also decides here, with no model,
            when the scope is empty (`empty_scope`) or a piece cannot fit the
            budget (`cap`).
    hash    the piece was seen before: its recorded answer or escalation is
            reused and no model runs.
    local   one small piece to one local model, through an injected callable
            (default: `ratatosk --onescript`, a subprocess).

One vocabulary for "couldn't answer": `answered`, or `escalated:<reason>`.

    reason          meaning
    d0_escalate     D0 had no answer for the act and said so (or needs flowering)
    local_escalate  the model returned path == "ESCALATE", its reason in `claim`
    uncited         an answer row with no cites, or a cite not in this piece
    silent          the rung returned nothing
    timeout         the rung did not return in time
    cap             a piece or a rung's output was over its cap
    rung_error      the rung failed (non-zero exit, unreadable output, a row the
                    door or the contract refused)
    empty_scope     nothing populated is served in this check-in

A model's rows go through `Run.turn`'s proposal handling (the door, the served
cites, `own_idea`); nothing is written, and an ESCALATE row is never a
proposal. Pieces that end escalated become ONE card (kind `review`, route
`willow`) with the same shape `human_required_enqueue` takes; this file records
and prints it, the broker verb files it.

The chain is willow-bot's own (`willow_bot/deterministic`): its resolvers and
its flowering row are reused here, not copied.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from .record import canon, h256
from .run import PKG

ROOT = PKG.parents[1]  # onescript -> one-script -> willow-bot

DEFAULT_MODEL = "llama3.2:3b"
#: Characters per piece, question included: sized for a 4096-token local window
#: (about 3 characters a token, leaving room for the frame and the reply).
PIECE_CHARS = 6000
#: The longest a rung may run, and the most output it may leave.
RUNG_TIMEOUT_S = 300.0
RUNG_OUT_CAP = 262144

ANSWERED = "answered"
ESCALATED = "escalated"
ESCALATE_PATH = "ESCALATE"  # the marker row: path == "ESCALATE", reason in claim

D0_ESCALATE = "d0_escalate"
LOCAL_ESCALATE = "local_escalate"
UNCITED = "uncited"
SILENT = "silent"
TIMEOUT = "timeout"
CAP = "cap"
RUNG_ERROR = "rung_error"
EMPTY_SCOPE = "empty_scope"
REASONS = (
    D0_ESCALATE,
    LOCAL_ESCALATE,
    UNCITED,
    SILENT,
    TIMEOUT,
    CAP,
    RUNG_ERROR,
    EMPTY_SCOPE,
)

RUNG_D0, RUNG_HASH, RUNG_LOCAL = "d0", "hash", "local"

ROW_KIND = "escalate_rung"


def label(outcome: str, reason: str | None) -> str:
    """The one mapping for "couldn't answer": `answered` or `escalated:<reason>`."""
    if outcome == ANSWERED:
        return ANSWERED
    if reason not in REASONS:
        raise ValueError(f"not an escalation reason: {reason!r}")
    return f"{ESCALATED}:{reason}"


class RungTimeout(Exception):
    """The rung did not return in time."""


class RungCap(Exception):
    """The rung's output went over its cap."""


class RungError(Exception):
    """The rung failed: it could not run, exited non-zero, or left garbage."""


#: (piece, question, model) -> the rows the model returned, as parsed objects.
#: It raises RungTimeout, RungCap or RungError, and nothing else is expected.
Rung = Callable[[dict, str, str], list]


class ChainUnavailable(Exception):
    """willow-bot's deterministic chain can't be imported here."""


def _chain():
    """willow-bot's chain, imported when an escalation needs it, so the rest of
    the one script never depends on it."""
    try:
        from willow_bot.deterministic import chain, resolvers
    except ImportError:
        if str(ROOT) not in sys.path:
            sys.path.append(str(ROOT))
        try:
            from willow_bot.deterministic import chain, resolvers
        except ImportError as e:
            raise ChainUnavailable(
                f"willow-bot's deterministic chain can't be imported: {e}"
            ) from e
    return chain, resolvers


# ── the default rung: Rat, per piece, in a subprocess ───────────────────────
def _rung_env() -> dict[str, str]:
    keep = ("PATH", "HOME", "LANG", "OLLAMA_HOST")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.setdefault("LANG", "C.UTF-8")
    return env


def ratatosk_rung(
    cmd: tuple[str, ...] | list[str] = ("ratatosk",),
    timeout: float = RUNG_TIMEOUT_S,
    out_cap: int = RUNG_OUT_CAP,
) -> Rung:
    """The local rung: `ratatosk --onescript --served <piece.json> --out
    <rows.jsonl> --model M "<question>"`, a fresh scratch directory per piece
    (Rat empties nothing), a minimal environment, a timeout."""
    cmd = tuple(cmd)

    def rung(piece: dict, question: str, model: str) -> list:
        with tempfile.TemporaryDirectory(prefix="onescript-escalate-") as d:
            served = Path(d) / "piece.json"
            out = Path(d) / "rows.jsonl"
            served.write_text(canon(piece) + "\n", encoding="utf-8")
            argv = [
                *cmd,
                "--onescript",
                "--served",
                str(served),
                "--out",
                str(out),
                "--model",
                model,
                question,
            ]
            try:
                done = subprocess.run(
                    argv,
                    cwd=d,
                    env=_rung_env(),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as e:
                raise RungTimeout(f"no return in {timeout:g}s") from e
            except OSError as e:
                raise RungError(f"can't run {cmd[0]}: {type(e).__name__}") from e
            if done.returncode != 0:
                raise RungError(f"{cmd[0]} exited {done.returncode}")
            if not out.is_file():
                return []
            if out.stat().st_size > out_cap:
                raise RungCap(f"output over {out_cap} bytes")
            text = out.read_text(encoding="utf-8", errors="replace")
        rows = []
        for i, ln in enumerate(text.splitlines(), 1):
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except (ValueError, RecursionError) as e:
                raise RungError(f"line {i} of the rung's output isn't JSON") from e
        return rows

    return rung


# ── the act: the sealed served scope + the task, as the chain reads it ──────
_CLASS_PREFIX = re.compile(r"^\s*(G[1-5])\s*:\s*")


def usable(doc: object) -> tuple[bool, str]:
    """(True, "") when `doc` is a populated served document of well-formed
    tables, else (False, why). Only a populated scope may reach a model."""
    if not isinstance(doc, dict):
        return False, "no served file stands in this check-in"
    state = doc.get("state")
    if state != "populated":
        return False, f"the scope is {state!r}: {doc.get('why') or 'nothing served'}"
    tables = doc.get("tables")
    if not isinstance(tables, list) or not tables:
        return False, "the served file holds no tables"
    for t in tables:
        if not (
            isinstance(t, dict)
            and isinstance(t.get("id"), str)
            and t["id"].strip()
            and isinstance(t.get("rows"), list)
            and isinstance(t.get("group"), dict)
        ):
            return False, "the served file isn't a served document"
    return True, ""


def act_of(task: str, doc: dict) -> dict:
    """The chain act for a task over a served scope: the shape `resolve_fixture`
    reads. One excerpt per served table, id = the table's served id, so a cite
    in an answer is a served id. The class is set by code from the task text: a
    brief that opens with `Route` is G1; `G1:`..`G5:` at the start names a class
    outright; anything else has none, and D0 escalates it."""
    _, resolvers = _chain()
    m = _CLASS_PREFIX.match(task)
    brief = task[m.end() :] if m else task
    if m:
        cls = m.group(1)
    else:
        cls = "G1" if resolvers.is_route_act({"brief": brief}) else ""
    return {
        "id": "escalate",
        "class": cls,
        "brief": brief,
        "excerpts": [
            {"id": t["id"], "text": "\n".join(canon(r) for r in t["rows"])}
            for t in doc["tables"]
        ],
        "expected": {},
    }


# ── the piece cutter ────────────────────────────────────────────────────────
def _piece_doc(doc: dict, table: dict, rows: list) -> dict:
    return {
        "state": "populated",
        "why": "",
        "return": doc.get("return", ""),
        "tables": [{**table, "rows": rows}],
    }


def piece_hash(piece: dict, question: str) -> str:
    return h256(canon({"piece": piece, "question": question}))


def cut(doc: dict, question: str, budget: int) -> list[dict]:
    """The served document as small pieces, deterministic and ordered: one
    piece per table; a table over `budget` characters (the piece and the
    question, canonically) is cut into row groups, each as many rows as fit. A
    single row that cannot fit alone is still a piece, marked `oversize`: code
    escalates it with reason `cap` and no model sees it."""
    out = []
    for t in doc["tables"]:
        rows = t["rows"]
        groups, cur = [], []
        for r in rows:
            trial = cur + [r]
            if cur and len(canon(_piece_doc(doc, t, trial))) + len(question) > budget:
                groups.append(cur)
                cur = [r]
            else:
                cur = trial
        if cur:
            groups.append(cur)
        for g in groups:
            piece = _piece_doc(doc, t, g)
            out.append(
                {
                    "piece": piece,
                    "hash": piece_hash(piece, question),
                    "oversize": len(canon(piece)) + len(question) > budget,
                    "table": t["id"],
                    "group": t["group"],
                }
            )
    return out


# ── judging what a rung brought back ────────────────────────────────────────
def judge(rows: list, table_ids: set[str]) -> dict:
    """{"outcome", "reason", "detail", "cites"} for a rung's rows over one
    piece. Code decides; the model's word is never taken as the verdict."""
    if not rows:
        return _j(ESCALATED, SILENT, "the rung returned nothing")
    if not all(isinstance(r, dict) for r in rows):
        return _j(ESCALATED, RUNG_ERROR, "the rung returned a row that isn't an object")
    marks = [r for r in rows if r.get("path") == ESCALATE_PATH]
    if marks:
        claim = marks[0].get("claim")
        cites = [c for r in marks for c in _cites(r)]
        return _j(
            ESCALATED,
            LOCAL_ESCALATE,
            claim if isinstance(claim, str) and claim.strip() else "no reason given",
            cites,
        )
    for r in rows:
        cites = r.get("cites")
        if (
            not isinstance(cites, list)
            or not cites
            or not all(isinstance(c, str) for c in cites)
        ):
            return _j(ESCALATED, UNCITED, "an answer row cites nothing")
        stray = [c for c in cites if c not in table_ids]
        if stray:
            return _j(ESCALATED, UNCITED, "a cite is not in this piece")
    return _j(ANSWERED, None, "", [c for r in rows for c in r["cites"]])


def _cites(row: dict) -> list[str]:
    c = row.get("cites")
    return [x for x in c if isinstance(x, str)] if isinstance(c, list) else []


def _j(outcome, reason, detail, cites=()):
    return {
        "outcome": outcome,
        "reason": reason,
        "detail": detail,
        "cites": list(dict.fromkeys(cites)),
    }


# ── the run ─────────────────────────────────────────────────────────────────
def _find_prior(run, h: str) -> dict | None:
    """The latest rung row recorded for this piece hash, if any."""
    for r in reversed(run.rec.rows()):
        if r["kind"] == ROW_KIND and r.get("piece") == h:
            return r
    return None


def _log(run, h: str, rung: str, model, j: dict, rows=None, **extra) -> dict:
    return run.rec.append(
        ROW_KIND,
        run.sys,
        where=f"escalate:{h}",
        piece=h,
        rung=rung,
        model=model,
        outcome=j["outcome"],
        reason=j["reason"],
        label=label(j["outcome"], j["reason"]),
        detail=j["detail"],
        cites=j["cites"],
        rows=rows or [],
        **extra,
    )


def _result(row: dict, group=None, ch=None) -> dict:
    """The per-piece JSON result from its record row. An escalated one is the
    chain's own flowering row, routed to willow."""
    res = {
        "piece": row["piece"],
        "row": row["n"],
        "rung": row["rung"],
        "model": row["model"],
        "outcome": row["outcome"],
        "label": row["label"],
        "detail": row["detail"],
        "cites": row["cites"],
        "rows": row["rows"],
        "group": group,
    }
    if row["outcome"] == ESCALATED and ch is not None:
        res = ch._flowering(res, row["reason"])
    return res


def _group_name(group: object) -> str:
    if isinstance(group, dict):
        return " · ".join(f"{k}={v}" for k, v in group.items()) or "(no group)"
    return "(no group)"


def _short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def make_card(question: str, left: list[dict], route: str) -> dict:
    """ONE card for everything no rung answered: `human_required`-shaped (kind
    `review`), title leading with the task, a summary line per piece with its
    rung and reason, source_ref the record rows."""
    n = len(left)
    lines = [
        f"- {r['label']} at {r['rung']}: {_short(r['detail'], 160) or '(no detail)'}"
        f" [{_group_name(r.get('group'))} · {r['piece'][:12]}]"
        for r in left
    ]
    return {
        "kind": "review",
        "title": f"{_short(question, 90)}: {n} piece{'s' if n != 1 else ''} "
        "no rung answered",
        "summary": "\n".join(lines),
        "source_ref": "record:" + ",".join(str(r["row"]) for r in left),
        "route": route,
    }


def run_escalate(
    run,
    ident: dict,
    task: str,
    *,
    rung: Rung,
    model: str = DEFAULT_MODEL,
    piece_chars: int = PIECE_CHARS,
) -> dict:
    """One task through the chain, on the current check-in's sealed served
    scope. Returns {"pieces", "answered", "escalated", "card", ...}; never
    raises on a rung's failure."""
    ch, resolvers = _chain()
    question = " ".join(task.split())
    doc = run.served_file()
    ok, why = usable(doc)
    out: dict = {
        "task": question,
        "state": doc.get("state") if isinstance(doc, dict) else None,
        "why": why,
        "model": model,
        "pieces": [],
    }
    run.rec.append(
        "escalate",
        run.sys,
        where="escalate",
        question=question,
        state=out["state"],
        why=why,
        served=h256(canon(doc)) if ok else None,
        model=model,
        piece_chars=piece_chars,
    )

    left: list[dict] = []  # rung results nobody answered

    def settle(res: dict) -> None:
        out["pieces"].append(res)
        if res["outcome"] == ESCALATED:
            left.append(res)

    def at_d0(h, j, rows=None, group=None, **extra):
        settle(_result(_log(run, h, RUNG_D0, None, j, rows, **extra), group, ch))

    # 'only along side': an unpopulated scope is escalated up by code, no model
    if not ok:
        h = h256(canon({"state": out["state"], "why": why, "question": question}))
        at_d0(h, _j(ESCALATED, EMPTY_SCOPE, why))
        return _finish(run, out, question, left, resolvers)

    # D0 first: whatever code closes is answered; only its ESCALATE goes on
    act = act_of(task, doc)
    h_act = h256(canon({"act": act, "question": question}))
    d0 = resolvers.resolve_fixture(act)
    status = d0.get("status")
    out["d0"] = {"status": status, "reason": d0.get("reason")}
    if status != resolvers.STATUS_ESCALATE:
        if status == resolvers.STATUS_FLOWERING_REQUIRED:
            detail = f"D0 needs flowering: {d0.get('reason') or 'no reason'}"
            at_d0(h_act, _j(ESCALATED, D0_ESCALATE, detail, d0.get("cites") or []))
        else:
            at_d0(
                h_act,
                _j(ANSWERED, None, d0.get("reason") or "", d0.get("cites") or []),
                [{"status": status, "answer": d0.get("answer")}],
            )
        return _finish(run, out, question, left, resolvers)

    # the pieces: each to the hash record first, then the local rung
    for p in cut(doc, question, piece_chars):
        h, group = p["hash"], p["group"]
        table_ids = {p["table"]}
        prior = _find_prior(run, h)
        if prior is not None:
            j = _j(prior["outcome"], prior["reason"], prior["detail"], prior["cites"])
            row = _log(
                run, h, RUNG_HASH, prior["model"], j, prior["rows"], reused=prior["n"]
            )
            settle(_result(row, group, ch))
            continue
        if p["oversize"]:
            at_d0(
                h,
                _j(ESCALATED, CAP, f"a row is over {piece_chars} characters"),
                group=group,
            )
            continue
        try:
            got = rung(p["piece"], question, model)
            if isinstance(got, list):
                j = judge(got, table_ids)
            else:
                j = _j(ESCALATED, RUNG_ERROR, "the rung didn't return a list of rows")
                got = []
        except RungTimeout as e:
            j, got = _j(ESCALATED, TIMEOUT, str(e)), []
        except RungCap as e:
            j, got = _j(ESCALATED, CAP, str(e)), []
        except Exception as e:  # a rung is not trusted to behave
            msg = str(e) if isinstance(e, RungError) else type(e).__name__
            j, got = _j(ESCALATED, RUNG_ERROR, msg), []
        rows = []
        if j["outcome"] == ANSWERED:
            # through the proposal handling: door, served cites, own_idea.
            # Judged and recorded; nothing is written.
            turned = run.turn(ident, f"escalate piece {h[:12]}", proposed=list(got))
            props = turned.get("proposals") if isinstance(turned, dict) else None
            bad = None
            if not isinstance(props, list) or len(props) != len(got):
                bad = "the turn took no proposals"
            else:
                bad = next(
                    (
                        f"{x.get('verdict')}: {x.get('reason')}"
                        for x in props
                        if x.get("verdict") != "pass"
                    ),
                    None,
                )
            if bad is not None:
                j = _j(ESCALATED, RUNG_ERROR, f"the door/contract refused a row: {bad}")
            else:
                rows = [
                    {k: r[k] for k in ("path", "data", "cites", "claim")} for r in got
                ]
        row = _log(run, h, RUNG_LOCAL, model, j, rows)
        settle(_result(row, group, ch))
    return _finish(run, out, question, left, resolvers)


def _finish(run, out: dict, question: str, left: list[dict], resolvers) -> dict:
    card = None
    if left:
        card = make_card(question, left, resolvers.ROUTE_TARGET)
        run.rec.append(
            "escalate_card",
            run.sys,
            where="escalate:card",
            card=card,
            rows=[r["row"] for r in left],
        )
    out["answered"] = sum(p["outcome"] == ANSWERED for p in out["pieces"])
    out["escalated"] = len(left)
    out["card"] = card
    return out
