"""table — the table's results as rows (J4) and the ai-game-master head (J9).

SPEC W2 (`/home/user/sandbox/box-build/SPEC.md` phase W2-rows-and-head,
rulings R9, R10, R3, R19); SPEC-documented §9 J4 and §12 J9; SPEC-rival §2,
§8 R3-one-record, adversarial tests T4, T5, T7.

    verdict(cfg, row)                 # code writes one `verdict` row as `table`
    agm_head(cfg, n=, head=)          # the ai-game-master ledger head, as one verdict row
    propose_beat(cfg, pending, cites) # a decision beat as a proposal the human seals
    beat_seal(cfg, subject)           # sealed / unsealed / unreachable, by the proof
    propose_outcome(rows, wager, key) # code proposes a forecast's outcome; never seals
    outcome_from_verdicts(cfg, wager, human_key=FILE)  # the same, opened from the box
    basis(key)                        # the R3 hook reverse.reconcile_wagers asks
    reconcile_table(cfg, human_key=FILE)               # api.reconcile with basis bound
    check_agm_head(rows, read_head)   # satisfied / failing / unreachable (+ unanchored)

A `verdict` row is code's record of what happened at the table. It is never a
seal (R10, the A5 class): it is written under the verified `table` identity
(C11: a keys-file entry, family `python`, like `desk`), its kind is always
`verdict`, and it can carry none of the fields a seal reader looks at
(`proof`, `pair`, `subject`, `via`, `verifier`, `wager`, `outcome`). Only a
human proof makes anything count; code here proposes and reads, and writes no
`seal` row.

Two limits, said rather than hidden:

- The `table` identity is a WRITE gate, not a row signature. `verdict` refuses
  a writer without the table key, but a row on disk carries no signature of
  its own: anyone who can write `record.jsonl` can append a re-chained row
  labelled `who: table` (the same as any row; record.py's module docstring).
  What closes that is the human's sealed tip, and `seal-tip` lists every
  verdict session and agm_head row it would newly cover (`api.covers`), so
  the human never signs over table rows blind.
- The `table` key is a full turn seat (N2): `gate.verify` takes any identity
  in the keys file, so a holder of the table key can also run `Run.turn` as
  `table`. Its rows are stamped `table` and judged like any seat's; it has no
  seal power (`gate.verify` refuses the human seat by signature).

R3 goes through the one door: `Run.reconciled` binds `basis(human_key)` into
`reverse.reconcile_wagers` whenever the record holds table verdict rows, so
`reconcile`, `needs`, `checkout` and the morning screen all demote an outcome
the verdict rows do not carry, and calibration and misses never see it.

R19 (integrator): the anchor runs one way only. The ai-game-master head goes
into the one-script record as an `agm_head` row under the human's sealed tip;
there is no reverse anchor into campaign.db and no `LedgerSink.head_n`, since
one sealed tip covers both chains and the rival forbids a second chain that
depends on one-script state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

from . import api, gate, proposals, record, reverse
from .record import canon

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

#: The check's states, as SPEC-documented §12 (b) names them, plus the empty
#: arm: no agm_head row on record, so nothing to compare (not satisfied, not
#: failing).
SATISFIED, FAILING, UNREACHABLE, UNANCHORED = (
    "satisfied",
    "failing",
    "unreachable",
    "unanchored",
)


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
        return (
            None,
            f"a verdict is written as {TABLE[0]!r}, not {identity.get('who')!r}",
        )
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
    (SPEC-documented §9 `api.verdict`; CLI `verdict --json FILE`; C11).
    Refused, with only the refusal appended: a closed box, an unverified
    identity, or a row that fails `check_row`. Returns {"row", "code"}."""
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
    db: str | None = None,
    argv: Iterable[str] = ("agm-head",),
) -> dict:
    """R9: the ai-game-master ledger head as ONE `verdict` row (`what:
    agm_head`; CLI `ledger-head`), so the next tip the human seals covers it.
    `n` is the ledger's row count, `head` its last hash (`verify_ledger.py`'s
    chain, recomputed by the caller), `db_sha` the file's hash as anchored and
    `db` where it was read (N4). The answer carries the tip to seal next."""
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
        row["db_sha"] = db_sha
    if db is not None:
        row["db"] = str(db)
    res = verdict(cfg, row, argv=argv)
    if res.get("code") == 0:
        res["tip"] = record.tip_subject(res["row"])  # seal it: the head is covered
    return res


def beat_path(pending: dict) -> str:
    """Where a decision beat's fact is written once sealed: under its world
    when the beat names one, so two worlds' `s1::b3` are two paths (B2)."""
    world = pending.get("world")
    if world:
        return f"world/{world}/{pending['fact_id']}.md"
    return f"world/{pending['fact_id']}.md"


def propose_beat(
    cfg: api.Config,
    pending: dict,
    cites: list[str],
    *,
    argv: Iterable[str] = ("propose-beat",),
) -> dict:
    """R10 / C10: a decision beat becomes one proposal the human seals with
    `api.seal_proposal` (a proof or a Nestor pair); code never writes a seal.
    The proposal is `{"path": beat_path(pending), "data": fact, "cites":
    [...], "claim": ...}` (SPEC-documented §9 (b)), recorded as a `proposal`
    row under the `table` identity through `Run.append_proposal`, the same
    append the model's proposals take, judged by the door and the contract.

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
                    "path": beat_path(pending),
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
        {
            "kind": "edit",
            "path": p["path"],
            "who": TABLE[0],
            "none_because": p["claim"],
        },
        run.law,
        run._script_index(),
    )
    got = run.append_proposal(
        who,
        p,
        verdict=d.verdict,
        reason=d.reason,
        link_fail=[],
        own_idea=False,
        card=d.card,
        fact_id=pending["fact_id"],
    )
    return {
        "row": got,
        "subject": got["subject"],
        "code": 0 if d.verdict == "pass" else 1,
    }


def beat_seal(
    cfg: api.Config, subject: str, *, argv: Iterable[str] = ("beat-seal",)
) -> dict:
    """Whether the human sealed a beat's proposal, read row by row
    (`Run.seal_rows`): `sealed` only for a seal row over `subject` whose own
    proof (or Nestor pair) re-checks now, with `by` the verifier's name for a
    pair, else "human"; `unreachable` when a seal is on record that can't be
    checked here; else `unsealed`. A row that only says `who: human` is
    `unsealed` (A5). A `sealed` answer names what was sealed (B2): the stored
    proposal that really hashes to `subject` (`Run.find_proposal`), its
    `path` and `data`, so a caller can refuse a seal over another beat.
    Read-only: an `invocation` row and nothing else."""
    run, info = api._open(cfg, "beat-seal", argv)
    if run is None:
        return info
    rows = run.rec.rows()
    good, unchecked = run.seal_rows(rows)
    over = [r for r in rows if r["kind"] == "seal" and r.get("subject") == subject]
    ok = [r for r in over if r["n"] in good]
    if ok:
        prop, why = run.find_proposal(subject)
        if prop is None:
            return {"state": "unsealed", "why": f"sealed, but {why}", "code": 1}
        r = ok[-1]
        return {
            "state": "sealed",
            "by": r.get("verifier") or "human",
            "seal": r["n"],
            "path": prop["path"],
            "data": prop["data"],
            "fact_id": prop.get("fact_id"),
            "code": 0,
        }
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
                return (
                    None,
                    f"row {r['n']}: its path {r.get('path')!r} is not the rolls so far",
                )
        elif r.get("what") in ("decision", "result"):
            return "-".join(got), None
    return None, (
        "incomplete: no decision or result row closes this session; rows may "
        "have been cut, and with no sealed tip after them a cut is not seen (A7)"
    )


def claimed_by(rows: list[dict], wager: str) -> list[dict]:
    """The `open` verdict rows that name this forecast."""
    return [
        r
        for r in rows
        if r["kind"] == "verdict"
        and r.get("what") == "open"
        and r.get("forecast") == wager
    ]


def propose_outcome(rows: list[dict], wager: str, human_key: bytes | None) -> dict:
    """Code proposes a forecast's outcome from the table's verdict rows; it
    never seals it (R10, C4). The human seals what is proposed with
    `api.seal_outcome`, or does not.

    Which rows: the one session whose `open` row names this forecast
    (`forecast: <wager hash>`; a row can't name a hash before it exists),
    opened once (N3), on the world and stat sheets the forecast names (S2:
    the wager's `table` {world, sheets_sha} equals the open row's). R3: every
    row of that session comes after a tip seal that holds the wager,
    `wager.n <= tip.n < tip_seal.n < verdict.n`, with the tip seal's proof
    re-checked against `human_key`. Anything else escalates: a verdict before
    the forecast (T4), no sealed tip between (T5), rows cut so the session
    never closes (T7, said, not prevented). Without the human's key no tip
    seal can be checked: `unreachable`, never collapsed into escalate.
    Returns {"state": "proposed", "outcome": {"path": P}, ...} or why not."""
    w = next((r for r in rows if r["kind"] == "wager" and r["hash"] == wager), None)
    if w is None:
        return _escalate("no wager row on record has that hash")
    if not reverse.in_place(rows):
        return {"state": "broken", "reason": "a row's n is not its place in these rows"}
    opens = claimed_by(rows, wager)
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
    reopened = [r for r in srows if r.get("what") == "open"]
    if len(reopened) != 1:
        return _escalate(
            f"session id {session!r} was reused: opened {len(reopened)} times "
            f"(rows {[r['n'] for r in reopened]}); one session, one open row (N3)",
            session=session,
        )
    want, got = w.get("table"), {k: reopened[0].get(k) for k in sorted(api.TABLE_KEYS)}
    if not isinstance(want, dict):
        return _escalate(
            "the forecast names no world (its `table` {world, sheets_sha}); a "
            "session's rows can't be matched to it (S2)",
            session=session,
        )
    if want != got:
        return _escalate(
            f"the session played {got}, the forecast is about {want} (S2)",
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
        return {
            "state": "broken",
            "reason": f"the record is broken: {breaks}",
            "code": 1,
        }
    out = propose_outcome(run.rec.rows(), wager, key)
    return {**out, "code": 0 if out["state"] == "proposed" else 1}


def basis(human_key: bytes | None) -> Callable[[dict, list[dict], dict], str | None]:
    """The R3 hook for `reverse.reconcile_wagers` (B1): for a wager a table
    session claims, why its sealed outcome does not stand as the table's, or
    None. A wager no session claims is not the table's business (None). A
    claimed wager that declares no paths can't line up with verdict rows (N1).
    Otherwise the outcome must be the one `propose_outcome` reads from the
    verdict rows written after the forecast's tip seal."""

    def check(wager: dict, rows: list[dict], outcome: dict) -> str | None:
        if not claimed_by(rows, wager["hash"]):
            return None
        if not wager.get("distribution"):
            return (
                "a table session claims this wager, but it declares no paths "
                "for verdict rows to line up with (scores-only wagers are not "
                "the table's) (N1)"
            )
        got = propose_outcome(rows, wager["hash"], human_key)
        if got["state"] != "proposed":
            return str(got.get("reason", got["state"]))
        idx, _ = reverse._match_distribution(wager, outcome)
        if idx != got["path_index"]:
            return (
                f"the sealed outcome {outcome} is not what the verdict rows say "
                f"({got['outcome']['path']!r})"
            )
        return None

    return check


def reconcile_table(
    cfg: api.Config,
    *,
    human_key: Path | None = None,
    argv: Iterable[str] = ("reconcile",),
) -> dict:
    """`api.reconcile` with `basis` bound explicitly. `Run.reconciled` binds
    the same hook on its own whenever the box holds table verdict rows, so
    this is the plain verb said out loud, not a second door."""
    key, why = _key(cfg, human_key)
    if why is not None:
        return api._refused(why, 2)
    return api.reconcile(cfg, human_key=human_key, argv=argv, basis=basis(key))


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


def _db_sha(anchored: dict, got: dict) -> str:
    a, b = anchored.get("db_sha"), got.get("db_sha")
    if not a or not b:
        return "not compared"
    return "matches" if a == b else "differs"


def check_agm_head(
    rows: list[dict],
    read_head: Callable[[], dict],
    *,
    ledger: str = LEDGER,
    covered_through: int | None = None,
) -> dict:
    """R9's check: recompute the campaign.db head (`read_head`, injected; the
    table passes `agm_head.reader` over campaign.db) and compare it with the
    latest `agm_head` row. Three states, never collapsed (INVARIANTS §1), as
    SPEC-documented §12 (b) names them:

      satisfied    the ledger verifies and its (n, head) is the anchored one
      failing      the chain is broken, or it verifies to another n or head
                   ("fewer rows than anchored" for a cut)
      unreachable  the db is absent, unreadable, or predates the chain

    and, apart from all three, `unanchored` when no `agm_head` row is on
    record (nothing to compare against). Beside the state, `db_sha` says
    whether the file's bytes still hash as anchored: `matches`, `differs`
    or `not compared` (no hash on one side). It never moves the state: a
    later turn changes the file (SPEC-documented §12 (e)), and a db_sha that
    differs under `satisfied` says the file changed outside the ledger.

    Ledger-side A7, documented: the chain alone can't see rows appended and
    then cut back to the anchored head. Rows added after the anchor and
    deleted again leave (n, head) as anchored, so the state reads
    `satisfied`; only `db_sha` can show the file was touched. A head is
    covered only up to the agm_head row, as a one-script tip is.

    `covered` says whether a sealed tip holds the head row (`covered_through`,
    from `reverse.covered_through`); an uncovered head row can be cut unseen
    (A7)."""
    anchored = latest_agm_head(rows, ledger)
    try:
        got = read_head()
    except Exception as e:  # an injected reader that raises is unreachable, said
        got = {"state": "unreachable", "why": f"{type(e).__name__}: {e}"}
    if not isinstance(got, dict):
        got = {"state": "?", "why": repr(got)}
    base = {
        "ledger": ledger,
        "anchored": None
        if anchored is None
        else {
            "row": anchored["n"],
            "n": anchored.get("ledger_n"),
            "head": anchored.get("head"),
            "db": anchored.get("db"),
        },
        "read": got,
        "db_sha": "not compared" if anchored is None else _db_sha(anchored, got),
        "covered": anchored is not None
        and covered_through is not None
        and anchored["n"] <= covered_through,
    }
    if anchored is None:
        return {**base, "state": UNANCHORED, "why": "no agm_head row on record"}
    if got.get("state") == "unreachable":
        return {**base, "state": UNREACHABLE, "why": got.get("why", "unreachable")}
    if got.get("state") == "broken":
        return {
            **base,
            "state": FAILING,
            "why": f"the ledger chain is broken: {got.get('why')}",
        }
    if got.get("state") != "read":
        return {
            **base,
            "state": UNREACHABLE,
            "why": f"the reader said {got.get('state')!r}",
        }
    if (got.get("n"), got.get("head")) == (
        anchored.get("ledger_n"),
        anchored.get("head"),
    ):
        return {**base, "state": SATISFIED, "why": None}
    why = (
        f"fewer rows than anchored ({got.get('n')} < {anchored.get('ledger_n')})"
        if isinstance(got.get("n"), int)
        and isinstance(anchored.get("ledger_n"), int)
        and got["n"] < anchored["ledger_n"]
        else f"the ledger is at ({got.get('n')}, {got.get('head')}), anchored "
        f"({anchored.get('ledger_n')}, {anchored.get('head')})"
    )
    return {**base, "state": FAILING, "why": why}


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
    return {**out, "code": 0 if out["state"] == SATISFIED else 1}
