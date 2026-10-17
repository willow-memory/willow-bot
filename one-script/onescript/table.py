"""table — the table's results as rows (J4) and the ai-game-master head (J9).

SPEC W2 (`/home/user/sandbox/box-build/SPEC.md` phase W2-rows-and-head,
rulings R9, R10, R3); SPEC-documented §9 J4 and §12 J9; SPEC-rival §2, §8
R3-one-record, adversarial tests T4, T5, T7.

    verdict(cfg, row)                 # code writes one `verdict` row as `table`
    agm_head(cfg, n=, head=)          # the ai-game-master ledger head, as one verdict row
    propose_beat(cfg, pending, cites) # a decision beat as a proposal the human seals
    beat_seal(cfg, subject)           # sealed / unsealed / unreachable, by the proof
    propose_outcome(rows, wager, key) # code proposes a forecast's outcome; never seals
    outcome_from_verdicts(cfg, wager, human_key=FILE)  # the same, opened from the box
    reconcile_table(cfg, human_key=FILE)               # api.reconcile + the R3 basis
    check_agm_head(rows, read_head)   # equal / differs / unreachable (+ unanchored)

A `verdict` row is code's record of what happened at the table. It is never a
seal (R10, the A5 class): it is written under the verified `table` identity
(C11: a keys-file entry, family `python`, like `desk`), its kind is always
`verdict`, and it can carry none of the fields a seal reader looks at
(`proof`, `pair`, `subject`, `via`, `verifier`, `wager`, `outcome`). Only a
human proof makes anything count; code here proposes and reads, and writes no
`seal` row.

Every function is new; `api.py` and `reverse.py` are used, not changed, so the
J1 work beside this merges clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from . import api, gate, proposals, record, reverse
from .record import canon, h256

#: C11 (SPEC-documented §9 (b)): the table's identity, beside `api.DESK`.
TABLE = ("table", "python")
LEDGER = "ai-game-master"

#: What a verdict row may say it records (SPEC-documented §9 (b), §12 (b);
#: SPEC R9 names the head row `agm_head`).
WHATS = ("open", "roll", "step", "decision", "result", "agm_head")

#: A verdict row carries none of these. The stamp is the record's own; the rest
#: are what a seal reader reads (`reverse._outcome_row`, `serve.sealed_verified`,
#: `record.verify_anchor`), so a verdict can never be taken for a seal (R10).
SEAL_FIELDS = frozenset(
    {"proof", "pair", "subject", "via", "verifier", "wager", "outcome"}
)
STAMP = frozenset(record.ROW_KEYS | {"where"})


# ── writing ────────────────────────────────────────────────────────────────
def _identity(run, identity: dict | None) -> tuple[object | None, str | None]:
    """The table's verified stamp, or why not. With no identity handed over the
    box signs as `table` from its keys file; a box with no `table` key, or an
    identity whose signature does not check, is refused (gate.verify)."""
    if identity is None:
        key = run.keys.get(TABLE[0])
        if key is None:
            return None, "no table key on this box (keys file entry 'table')"
        identity = {
            "who": TABLE[0],
            "family": TABLE[1],
            "sig": gate.sign(key, *TABLE),
        }
    if identity.get("who") != TABLE[0]:
        return None, f"a verdict is written as {TABLE[0]!r}, not {identity.get('who')!r}"
    try:
        return gate.verify(identity, run.keys), None
    except gate.Refused as e:
        return None, str(e)


def check_row(row: object) -> str | None:
    """Why a verdict row is refused, or None when it stands."""
    if not isinstance(row, dict):
        return "a verdict row is a JSON object"
    if row.get("kind", "verdict") != "verdict":
        return f"a verdict row is kind 'verdict', never {row.get('kind')!r}"
    bad = sorted((set(row) - {"kind", "where"}) & STAMP)
    if bad:
        return f"the stamp is the record's, not the row's: {bad}"
    seal = sorted(set(row) & SEAL_FIELDS)
    if seal:
        return f"a verdict is never a seal (R10): it can't carry {seal}"
    if row.get("what") not in WHATS:
        return f"what is one of {list(WHATS)}"
    if not isinstance(row.get("session"), str) or not row["session"]:
        return "a verdict row names its session"
    try:
        canon(row).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as e:
        return f"not JSON text: {type(e).__name__}"
    return None


def verdict(
    cfg: api.Config,
    row: dict,
    *,
    identity: dict | None = None,
    argv: Iterable[str] = ("verdict",),
) -> dict:
    """Append one code-written `verdict` row as the verified `table` identity
    (SPEC-documented §9 `api.verdict`; C11). Refused, with only the refusal
    appended: a closed box, an unverified identity, or a row that fails
    `check_row`. Returns {"row", "code"}."""
    run, info = api._open(cfg, "verdict", argv)
    if run is None:
        return info
    if (refusal := api._need_open(run, "verdict")) is not None:
        return refusal
    who, why = _identity(run, identity)
    if why is None:
        why = check_row(row)
    if why is not None:
        got = run.rec.append("refused", run.sys, at="verdict", reason=why)
        return {"refused": why, "row": got, "code": 1}
    fields = {k: v for k, v in row.items() if k not in ("kind", "where")}
    got = run.rec.append("verdict", who, where=row.get("where"), **fields)  # type: ignore[arg-type]
    return {"row": got, "code": 0}


def agm_head(
    cfg: api.Config,
    *,
    n: int,
    head: str,
    session: str = "agm",
    ledger: str = LEDGER,
    db_sha: str | None = None,
    argv: Iterable[str] = ("agm-head",),
) -> dict:
    """R9: the ai-game-master ledger head as ONE `verdict` row (`what:
    agm_head`), so the next tip the human seals covers it. `n` is the ledger's
    row count, `head` its last hash (`verify_ledger.py`'s chain, recomputed by
    the caller). The answer carries the tip to seal next."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return api._refused("n is the ledger's row count", 1)
    if not isinstance(head, str) or not head:
        return api._refused("head is the ledger's last hash", 1)
    row = {
        "what": "agm_head",
        "session": session,
        "ledger": ledger,
        "ledger_n": n,  # `n` is the record's stamp; the ledger's count is ledger_n
        "head": head,
        "verdict": "agm_head",
        "reason": f"{ledger} ledger: {n} rows, head {head}",
    }
    if db_sha is not None:
        row["db_sha"] = db_sha  # as of the anchor; compared never (SPEC-doc §12 (e))
    res = verdict(cfg, row, argv=argv)
    if res.get("code") == 0:
        res["tip"] = record.tip_subject(res["row"])  # seal it: the head is covered
    return res


def propose_beat(
    cfg: api.Config,
    pending: dict,
    cites: list[str],
    *,
    argv: Iterable[str] = ("propose-beat",),
) -> dict:
    """R10 / C10: a decision beat becomes one proposal the human seals with
    `api.seal_proposal` (a proof or a Nestor pair); code never writes a seal.
    The proposal is `{"path": "world/<fact_id>.md", "data": fact, "cites":
    [...], "claim": ...}` (SPEC-documented §9 (b)), recorded as a `proposal`
    row under the `table` identity, judged by the door and the contract.

    Departure (SPEC-documented §9 says `turn --proposal`): `turn` takes a
    model's rows and checks their cites against the served file. The table is
    code proposing the world's own authored fact, so its cites are the record
    rows the beat follows (the session's verdict row hashes), and they are
    checked against the record, not a served file."""
    run, info = api._open(cfg, "propose-beat", argv)
    if run is None:
        return info
    if (refusal := api._need_open(run, "propose_beat")) is not None:
        return refusal
    who, why = _identity(run, None)
    p = None
    if why is None:
        if not isinstance(pending, dict) or not {"fact_id", "fact"} <= set(pending):
            why = "a pending beat carries fact_id and fact"
        else:
            p, why = proposals.check(
                {
                    "path": f"world/{pending['fact_id']}.md",
                    "data": str(pending["fact"]),
                    "cites": list(cites),
                    "claim": f"proposed by {pending.get('proposed_by', 'the world')}; "
                    "awaits the human's seal",
                }
            )
            why = why or None
    if why is None and p is not None:
        hashes = {r["hash"] for r in run.rec.rows() if r["kind"] == "verdict"}
        lost = [c for c in p["cites"] if c not in hashes]
        if not p["cites"] or lost:
            why = f"a beat cites the verdict rows it follows; not on record: {lost}"
    if why is not None or p is None:
        got = run.rec.append("refused", run.sys, at="propose_beat", reason=why)
        return {"refused": why, "row": got, "code": 1}
    d = gate.door(
        {"kind": "edit", "path": p["path"], "who": TABLE[0], "none_because": p["claim"]},
        run.law,
        run._script_index(),
    )
    sub = proposals.subject(p)
    got = run.rec.append(
        "proposal",
        who,  # type: ignore[arg-type]
        where=p["path"],
        path=p["path"],
        sha=h256(p["data"]),
        data=p["data"],
        cites=p["cites"],
        claim=p["claim"],
        subject=sub,
        verdict=d.verdict,
        reason=d.reason,
        link_fail=[],
        own_idea=False,
        card=d.card,
        fact_id=pending["fact_id"],
    )
    return {"row": got, "subject": sub, "code": 0 if d.verdict == "pass" else 1}


def beat_seal(
    cfg: api.Config, subject: str, *, argv: Iterable[str] = ("beat-seal",)
) -> dict:
    """Whether the human sealed a beat's proposal, read row by row
    (`Run.seal_rows`): `sealed` only for a seal row over `subject` whose own
    proof (or Nestor pair) re-checks now, with `by` the verifier's name for a
    pair, else "human"; `unreachable` when a seal is on record that can't be
    checked here; else `unsealed`. A row that only says `who: human` is
    `unsealed` (A5). Read-only: an `invocation` row and nothing else."""
    run, info = api._open(cfg, "beat-seal", argv)
    if run is None:
        return info
    rows = run.rec.rows()
    good, unchecked = run.seal_rows(rows)
    over = [r for r in rows if r["kind"] == "seal" and r.get("subject") == subject]
    ok = [r for r in over if r["n"] in good]
    if ok:
        r = ok[-1]
        return {"state": "sealed", "by": r.get("verifier") or "human", "seal": r["n"], "code": 0}
    lost = [r for r in over if r["n"] in unchecked]
    if lost:
        return {"state": "unreachable", "why": unchecked[lost[-1]["n"]], "code": 1}
    why = (
        "a seal row over this subject is on record, but no proof on it re-checks"
        if over
        else "no seal over this subject"
    )
    return {"state": "unsealed", "why": why, "code": 1}


# ── reading: the outcome code proposes (R3 tie-in) ─────────────────────────
def _escalate(why: str, **extra) -> dict:
    return {"state": "escalate", "escalate_to": "human", "reason": why, **extra}


def session_rows(rows: list[dict], session: str) -> list[dict]:
    return [r for r in rows if r["kind"] == "verdict" and r.get("session") == session]


def path_of(srows: list[dict]) -> tuple[str | None, str | None]:
    """The path a session's verdict rows say (each roll's `verdict` joined by
    "-" up to the first decision row, as `peaks.distribution` names paths), or
    (None, why). Each roll row's own `path` must be the path so far."""
    got: list[str] = []
    for r in srows:
        if r.get("what") == "roll":
            got.append(str(r.get("verdict")))
            if r.get("path") != "-".join(got):
                return None, f"row {r['n']}: its path {r.get('path')!r} is not the rolls so far"
        elif r.get("what") in ("decision", "result"):
            return "-".join(got), None
    return None, (
        "incomplete: no decision or result row closes this session; rows may "
        "have been cut, and with no sealed tip after them a cut is not seen (A7)"
    )


def propose_outcome(
    rows: list[dict], wager: str, human_key: bytes | None
) -> dict:
    """Code proposes a forecast's outcome from the table's verdict rows; it
    never seals it (R10, C4). The human seals what is proposed with
    `api.seal_outcome`, or does not.

    Which rows: the one session whose `open` row names this forecast
    (`forecast: <wager hash>`; a row can't name a hash before it exists).
    R3: every row of that session comes after a tip seal that holds the
    wager, `wager.n <= tip.n < tip_seal.n < verdict.n`, with the tip seal's
    proof re-checked against `human_key`. Anything else escalates: a verdict
    before the forecast (T4), no sealed tip between (T5), rows cut so the
    session never closes (T7, said, not prevented). Without the human's key
    no tip seal can be checked: `unreachable`, never collapsed into escalate.
    Returns {"state": "proposed", "outcome": {"path": P}, ...} or why not."""
    w = next((r for r in rows if r["kind"] == "wager" and r["hash"] == wager), None)
    if w is None:
        return _escalate("no wager row on record has that hash")
    if not reverse.in_place(rows):
        return {"state": "broken", "reason": "a row's n is not its place in these rows"}
    opens = [
        r
        for r in rows
        if r["kind"] == "verdict" and r.get("what") == "open" and r.get("forecast") == wager
    ]
    if not opens:
        return _escalate("no table session on record plays for this forecast")
    if len({r.get("session") for r in opens}) != 1:
        return _escalate("more than one table session claims this forecast")
    session = opens[0]["session"]
    srows = session_rows(rows, session)
    first = srows[0]["n"]
    if first < w["n"]:
        return _escalate(
            f"verdict row {first} of session {session!r} comes before the forecast "
            f"(row {w['n']}): wager.n < verdict.n is broken (T4)",
            session=session,
        )
    if human_key is None:
        return {
            "state": "unreachable",
            "reason": "no human key handed over: the tip seal can't be checked",
            "session": session,
        }
    tips = [(t, tn) for t, tn in reverse.tip_seals(rows, human_key) if w["n"] <= tn]
    under = next(((t, tn) for t, tn in sorted(tips) if t < first), None)
    if under is None:
        return _escalate(
            "no tip the human sealed holds the forecast before the table's first "
            f"verdict row {first} (wager.n <= tip.n < tip_seal.n < verdict.n, T5)",
            session=session,
        )
    path, why = path_of(srows)
    if path is None:
        return _escalate(why or "no path", session=session)
    idx, entry = reverse._match_distribution(w, {"path": path})
    if entry is None:
        return _escalate(
            f"the verdict rows say {path!r}, which the forecast does not declare",
            session=session,
        )
    return {
        "state": "proposed",
        "outcome": {"path": entry["path"]},
        "path_index": idx,
        "session": session,
        "basis": [r["n"] for r in srows],
        "under": {"tip_seal": under[0], "tip": under[1]},
        "subject": reverse.outcome_subject(wager, {"path": entry["path"]}),
    }


def _key(cfg: api.Config, human_key: Path | None) -> tuple[bytes | None, str | None]:
    return api.read_human_key(human_key, cfg.box)


def outcome_from_verdicts(
    cfg: api.Config,
    wager: str,
    *,
    human_key: Path | None = None,
    argv: Iterable[str] = ("outcome-from-verdicts",),
) -> dict:
    """`propose_outcome` over the box's record, read-only (an `invocation` row
    only). A broken chain is said as `broken`, apart from escalate and
    unreachable."""
    key, why = _key(cfg, human_key)
    if why is not None:
        return api._refused(why, 2)
    run, info = api._open(cfg, "outcome-from-verdicts", argv)
    if run is None:
        return info
    # T7: a cut under the human's sealed tip is seen here; a cut after the
    # last sealed tip is not (A7), and path_of says the session never closed
    breaks = run.rec.verify_chain() + run.rec.verify_anchor(key)
    if breaks:
        return {"state": "broken", "reason": f"the record is broken: {breaks}", "code": 1}
    out = propose_outcome(run.rec.rows(), wager, key)
    return {**out, "code": 0 if out["state"] == "proposed" else 1}


def reconcile_table(
    cfg: api.Config,
    *,
    human_key: Path | None = None,
    argv: Iterable[str] = ("reconcile",),
) -> dict:
    """`api.reconcile`, then the R3 tie-in for every forecast a table session
    plays for: the outcome the human sealed must be the one code proposes from
    verdict rows written after the forecast's tip seal. Where it is not (the
    rows came too early, there are none between, or they say another path),
    the wager's row escalates and loses its score; the human's seal is not
    overruled, only not counted as the table's. Forecasts no session claims
    are left as `api.reconcile` gave them."""
    res = api.reconcile(cfg, human_key=human_key, argv=argv)
    if "refused" in res:
        return res
    key, _ = _key(cfg, human_key)
    rows = record.Record(cfg.box, lambda: "", "", object()).rows()
    claimed = {
        r.get("forecast")
        for r in rows
        if r["kind"] == "verdict" and r.get("what") == "open"
    }
    wagers = {r["hash"]: r for r in rows if r["kind"] == "wager"}
    for wr in res["wagers"]["rows"]:
        if wr["subject"] not in claimed or wr["subject"] not in wagers:
            continue
        got = propose_outcome(rows, wr["subject"], key)
        wr["verdict_basis"] = got
        if wr.get("state") != "reconciled":
            continue
        if got["state"] != "proposed":
            why = got.get("reason", got["state"])
        elif got["path_index"] != wr.get("path_index"):
            why = (
                f"the sealed outcome {wr.get('actual_path')!r} is not what the verdict "
                f"rows say ({got['outcome']['path']!r})"
            )
        else:
            continue
        for k in ("brier", "log_score", "committed_under"):
            wr.pop(k, None)
        wr |= {"state": "escalate", "escalate_to": "human", "reason": f"R3: {why}"}
    ws = res["wagers"]
    ws["reconciled"] = [r["id"] for r in ws["rows"] if r["state"] == "reconciled"]
    ws["escalate"] = [r["id"] for r in ws["rows"] if r["state"] == "escalate"]
    return res


# ── J9: the ai-game-master head, checked ───────────────────────────────────
def latest_agm_head(rows: list[dict], ledger: str = LEDGER) -> dict | None:
    got = [
        r
        for r in rows
        if r["kind"] == "verdict"
        and r.get("what") == "agm_head"
        and r.get("who") == TABLE[0]
        and r.get("ledger") == ledger
    ]
    return got[-1] if got else None


def check_agm_head(
    rows: list[dict],
    read_head: Callable[[], dict],
    *,
    ledger: str = LEDGER,
    covered_through: int | None = None,
) -> dict:
    """R9's check: recompute the campaign.db head (`read_head`, injected; the
    table passes `ledger_head.read` over campaign.db) and compare it with the
    latest `agm_head` row. Three states, never collapsed (INVARIANTS §1):

      equal        the ledger verifies and its (n, head) is the anchored one
      differs      the chain is broken, or it verifies to another n or head
      unreachable  the db is absent, unreadable, or predates the chain

    and, apart from all three, `unanchored` when no `agm_head` row is on
    record (nothing to compare against: not equal, not differs). `covered`
    says whether a sealed tip holds the head row (`covered_through`, from
    `reverse.covered_through`); an uncovered head row can be cut unseen (A7)."""
    anchored = latest_agm_head(rows, ledger)
    try:
        got = read_head()
    except Exception as e:  # an injected reader that raises is unreachable, said
        got = {"state": "unreachable", "why": f"{type(e).__name__}: {e}"}
    base = {
        "ledger": ledger,
        "anchored": None
        if anchored is None
        else {"row": anchored["n"], "n": anchored.get("ledger_n"), "head": anchored.get("head")},
        "read": got,
        "covered": anchored is not None
        and covered_through is not None
        and anchored["n"] <= covered_through,
    }
    if anchored is None:
        return {**base, "state": "unanchored", "why": "no agm_head row on record"}
    if got.get("state") == "unreachable":
        return {**base, "state": "unreachable", "why": got.get("why", "unreachable")}
    if got.get("state") == "broken":
        return {**base, "state": "differs", "why": f"the ledger chain is broken: {got.get('why')}"}
    if got.get("state") != "read":
        return {**base, "state": "unreachable", "why": f"the reader said {got.get('state')!r}"}
    if (got.get("n"), got.get("head")) == (anchored.get("ledger_n"), anchored.get("head")):
        return {**base, "state": "equal", "why": None}
    why = (
        f"fewer rows than anchored ({got.get('n')} < {anchored.get('ledger_n')})"
        if isinstance(got.get("n"), int)
        and isinstance(anchored.get("ledger_n"), int)
        and got["n"] < anchored["ledger_n"]
        else f"the ledger is at ({got.get('n')}, {got.get('head')}), anchored "
        f"({anchored.get('ledger_n')}, {anchored.get('head')})"
    )
    return {**base, "state": "differs", "why": why}


def agm_check(
    cfg: api.Config,
    read_head: Callable[[], dict],
    *,
    human_key: Path | None = None,
    argv: Iterable[str] = ("agm-check",),
) -> dict:
    """`check_agm_head` over the box's record, read-only (an `invocation` row
    only), with `covered` from the human's sealed tip when the key is handed
    over."""
    key, why = _key(cfg, human_key)
    if why is not None:
        return api._refused(why, 2)
    run, info = api._open(cfg, "agm-check", argv)
    if run is None:
        return info
    through = reverse.covered_through(run.rec, key)
    out = check_agm_head(run.rec.rows(), read_head, covered_through=through)
    return {**out, "code": 0 if out["state"] == "equal" else 1}
