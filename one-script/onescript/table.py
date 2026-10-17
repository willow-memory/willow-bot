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
    surface(rows, covered_through=, read_head=)  # the lines a seat shows (R13): sessions + agm

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

W4 break (the end-to-end breaker): a table forecast names a future drand
round (`api.forecast(table={world, sheets_sha, beacon_round})`), and its
session's `open` row carries that round and its public randomness, which is
the seed. `propose_outcome` takes an outcome only from one honest run of
those dice (`dice_problem`: seed id, continuous counters, totals and verdicts
re-read from each roll record) on the round's own randomness, fired after the
tip seal and as the operator's beacon reader (`Config.beacon`) says
(`beacon_problem`); else it escalates, or is `unreachable` with no reader.
So code can't grind seeds offline, relabel its dice, or reseed mid-session.
The beacon's BLS signature is still not checked (T10, said). A session id
the serve would withhold (40+ hex) is refused at the write.

R19 (integrator): the anchor runs one way only. The ai-game-master head goes
into the one-script record as an `agm_head` row under the human's sealed tip;
there is no reverse anchor into campaign.db and no `LedgerSink.head_n`, since
one sealed tip covers both chains and the rival forbids a second chain that
depends on one-script state.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Callable, Iterable

from . import api, beacon, gate, proposals, record, reverse
from . import serve as served
from .record import canon

#: C11 (SPEC-documented §9 (b)): the table's identity, beside `api.DESK`.
TABLE = ("table", "python")
LEDGER = "ai-game-master"

#: What a verdict row may say it records (SPEC-documented §9 (b), §12 (b);
#: SPEC R9 names the head row `agm_head`; R12 adds `escalate`: a model's
#: move pick that code refused (off the menu, ESCALATE, or unreachable) and
#: fell back from, recorded so the human sees it; never a seal).
WHATS = ("open", "roll", "step", "decision", "result", "agm_head", "escalate")

#: A verdict row carries none of these. The stamp is the record's own; the rest
#: are what a seal reader reads (`reverse._outcome_row`, `serve.sealed_verified`,
#: `record.verify_anchor`), so a verdict can never be taken for a seal (R10).
SEAL_FIELDS = frozenset(
    {"proof", "pair", "subject", "via", "verifier", "wager", "outcome"}
)
STAMP = frozenset(record.ROW_KEYS | {"where"})
_HEXRUN = re.compile(r"[0-9a-f]{40,}", re.IGNORECASE)

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
    sid = row.get("session")
    if not isinstance(sid, str) or not sid:
        return "a verdict row names its session"
    if any(unicodedata.category(c)[0] in "CZ" and c != " " for c in sid):
        # proposals.path_problem's rule: a card can't show it as what it is
        return "session is plain text: no control, format or separator character"
    if _HEXRUN.search(sid) or served._scrub(sid) != sid:
        # serve._scrub withholds a 64-hex run, so two such ids would be served
        # as one "[hash withheld]" and no brief could name either (W4 break 4);
        # 40+ hex is refused too: it reads as a hash on a card
        return "session id is shaped like a hash (40+ hex): the serve would withhold it"
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
    """Where a decision beat's fact is written once sealed: under its world,
    so two worlds' `s1::b3` are two paths (B2). A beat names its world."""
    return f"world/{pending['world']}/{pending['fact_id']}.md"


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
        elif not isinstance(pending.get("world"), str) or not pending["world"]:
            why = (
                "a pending beat names its world (N5): its path is world/<world>/<fact>"
            )
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


def bucket(total: int) -> str:
    """StorySession's read of a 2d6+stat sum: strong 10+, weak 7-9, miss 6-
    (`the_table/story_session.py`, restated: onescript imports no table code)."""
    return "strong" if total >= 10 else "weak" if total >= 7 else "miss"


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def world_problem(opened: dict, w: dict) -> tuple[list | None, str | None]:
    """(the session's stat sheets, None) or (None, why) (R23a). The open row
    must carry the world inputs (`world_inputs`, peaks.world_inputs: sheets,
    stats, stat map, beats) whose canonical SHA-256 is the wager's committed
    `sheets_sha` (peaks.world_key, pinned in e2e). A label alone is not
    enough: the sheets the dice were rolled against are re-hashed here."""
    inputs = opened.get("world_inputs")
    want = (w.get("table") or {}).get("sheets_sha")
    if not isinstance(inputs, dict) or not isinstance(inputs.get("sheets"), list):
        return None, "the open row carries no world inputs to re-hash (S2, R23a)"
    got = record.h256(
        json.dumps(inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )
    if got != want:
        return None, (
            f"the open row's world inputs hash to {got}, the forecast committed "
            f"{want}: another world or sheet (S2, R23a)"
        )
    return inputs["sheets"], None


def dice_problem(
    opened: dict,
    srows: list[dict],
    sheets: list,
    beats: list | None = None,
    stat_map: dict | None = None,
) -> str | None:
    """Why a session's roll rows are not one honest run of the dice its `open`
    row names, or None (W4 break 1-3, R23a). Read from the record alone: the
    open row's randomness is the seed (`seed_id` is its id); every roll is
    from that seed on the `sha256-stream/v1` generator (a legacy
    `random/global` row is refused), its counters run on from 0 with no gap
    or restart, and each roll is REPLAYED from the randomness
    (`beacon.roll_2d6`): the two dice the stream gives at `counter_before`,
    the counter they leave, `result` == dice + `stat_value`, `total` ==
    `result`, `verdict` == its bucket. The modifier is NEVER the roll
    record's: it is `sheets[seat][stat]` (the re-hashed world inputs,
    `world_problem`; peaks.sheets_for already maps each stat through the
    engine's aliases), and a record whose `stat_value` says otherwise is
    refused.

    The stat is not the writer's either (R22, R23a): roll i answers the
    forecast's action beat i (`beats`, from the re-hashed world inputs), and
    on a beat that is not a `set_piece` its stat must be the beat's
    `suggests` (compared after the engine alias map), the code's own pick.
    Stated limit: on a set piece the stat is a pick, and once the round is
    out the dice are public, so that pick is post-hoc; nothing here stops it."""
    rnd = opened.get("randomness")
    if not beacon.is_randomness(rnd):
        return "the open row carries no beacon randomness (64 hex) to seed the session"
    if opened.get("seed_id") != beacon.seed_id(rnd):
        return (
            f"the open row's seed_id {opened.get('seed_id')!r} is not the id of its "
            "randomness: the session was not seeded by the beacon"
        )
    at = 0
    k = -1
    alias = stat_map if isinstance(stat_map, dict) else {}
    for r in srows:
        if r.get("what") != "roll":
            continue
        k += 1
        roll = r.get("roll")
        if not isinstance(roll, dict):
            return f"row {r['n']}: a roll row with no roll record"
        if beats is not None:
            if k >= len(beats):
                return (
                    f"row {r['n']}: roll {k} has no action beat in the forecast's "
                    f"world ({len(beats)} before the first decision)"
                )
            beat = beats[k] if isinstance(beats[k], dict) else {}
            stat = roll.get("stat")
            want = beat.get("suggests")
            if beat.get("set_piece") is not True and alias.get(stat, stat) != alias.get(
                want, want
            ):
                return (
                    f"row {r['n']}: beat {beat.get('id')!r} is not a set piece, so "
                    f"code rolls its suggested {want!r}, not {stat!r} (a stat "
                    "picked after the dice, R22)"
                )
        if roll.get("generator_id") != beacon.GENERATOR_ID:
            return (
                f"row {r['n']}: rolled by generator {roll.get('generator_id')!r}, "
                f"not {beacon.GENERATOR_ID!r}: no table outcome from it"
            )
        if roll.get("seed_id") != opened["seed_id"]:
            return (
                f"row {r['n']}: rolled from seed {roll.get('seed_id')!r}, not the open "
                f"row's {opened['seed_id']!r} (a reseed mid-session)"
            )
        before, after = roll.get("counter_before"), roll.get("counter")
        if not (_int(before) and _int(after)) or before != at or after <= before:
            return (
                f"row {r['n']}: counters {before!r}->{after!r} do not run on from "
                f"{at} (a restart or a gap)"
            )
        at = after
        seat, stat = r.get("seat"), roll.get("stat")
        sheet = sheets[seat] if _int(seat) and 0 <= seat < len(sheets) else None
        mod = sheet.get(stat) if isinstance(sheet, dict) else None
        if not _int(mod):
            return (
                f"row {r['n']}: seat {seat!r} stat {stat!r} is not on the forecast's "
                "sheets"
            )
        if roll.get("stat_value") != mod:
            return (
                f"row {r['n']}: the roll record says stat_value "
                f"{roll.get('stat_value')!r}, the sheet says {mod} for seat {seat} "
                f"{stat!r}"
            )
        if not _int(roll.get("result")):
            return f"row {r['n']}: the roll record has no result"
        a, b, end = beacon.roll_2d6(rnd, before)
        if end != after:
            return (
                f"row {r['n']}: the stream's two dice end at counter {end}, the record "
                f"says {after}"
            )
        if roll["result"] != a + b + mod:
            return (
                f"row {r['n']}: result {roll['result']} is not the stream's dice "
                f"{a}+{b} + {mod} = {a + b + mod} (replayed from the randomness)"
            )
        if r.get("total") != roll["result"]:
            return (
                f"row {r['n']}: total {r.get('total')!r} is not its own dice's "
                f"{roll.get('result')!r}"
            )
        if r.get("verdict") != bucket(roll["result"]):
            return (
                f"row {r['n']}: verdict {r.get('verdict')!r} is not the dice's "
                f"{bucket(roll['result'])!r}"
            )
    return None


def beacon_problem(
    opened: dict,
    w: dict,
    tip_seal: dict,
    read: Callable[[int], dict] | None,
    seal_latest: object = None,
) -> tuple[str, str] | None:
    """(state, why) when the session's seed is not shown to be the forecast's
    beacon, or None. The round is the one the wager committed to; the
    reader could NOT read it when the forecast was made nor when the human
    sealed the tip that commits it (`beacon_latest` on the wager row, and
    `seal_latest`: the reader's newest round at that tip seal as the ANCHOR
    keeps it, outside the box, never the seal row's own field, which the
    writer could rewrite until the next tip; R23a: never a box clock); and
    the reader says that round's randomness is the open row's. The tip
    seal's own `ts` is compared too, but it proves nothing alone: a box
    clock set back passes it, so the reader's rounds carry the order. No
    reader, or one that fails: `unreachable`. Trusted, said: the reader
    (BLS not checked, T10), which must never report a `latest` older than a
    round it serves (`api._future_round` also asks `read(rnd)`)."""
    rnd = (w.get("table") or {}).get("beacon_round")
    if not _int(rnd):
        return "escalate", "the forecast names no beacon round (W4 break 1)"
    if opened.get("beacon_round") != rnd:
        return "escalate", (
            f"the session was seeded for round {opened.get('beacon_round')!r}, the "
            f"forecast awaits round {rnd}"
        )
    if not _int(seal_latest):
        # INVARIANTS §1: the anchor holds no reader round for this tip (missing,
        # unreadable, or sealed with no reader): unchecked here, not a finding
        return "unreachable", (
            f"the anchor holds no reader round for the tip seal: round {rnd} "
            "can't be shown to have been ahead of it (R23a)"
        )
    for what, seen in (
        ("forecast", w.get("beacon_latest")),
        ("tip seal", seal_latest),
    ):
        if not _int(seen):
            return "escalate", (
                f"the {what} holds no reader round: round {rnd} can't be shown to "
                f"have been ahead when it was made (R23a)"
            )
        if rnd <= seen:
            return "escalate", (
                f"round {rnd} was out when the {what} was made (the reader read up "
                f"to {seen}): its randomness may have been known"
            )
    sealed_at = beacon.epoch(str(tip_seal.get("ts")))
    if sealed_at is None or beacon.round_time(rnd) <= sealed_at:
        return "escalate", (
            f"round {rnd} fired at {beacon.round_time(rnd)}, not after the tip seal "
            f"that committed the forecast ({tip_seal.get('ts')}): its randomness "
            "may have been known"
        )
    if read is None:
        return "unreachable", (
            f"no beacon reader on this box: round {rnd}'s randomness can't be "
            f"checked; {beacon.BLS_UNVERIFIED}"
        )
    try:
        got = read(rnd)
    except Exception as e:  # any failure of the reader is unreachable, said
        return "unreachable", f"round {rnd} could not be read: {type(e).__name__}: {e}"
    fetched = got.get("randomness") if isinstance(got, dict) else None
    if not beacon.is_randomness(fetched):
        return "unreachable", f"the reader gave no randomness for round {rnd}"
    if fetched != opened.get("randomness"):
        return "escalate", (
            f"the session's randomness is not round {rnd}'s: the seed was not the "
            "beacon (a ground or reseeded seed)"
        )
    return None


def claimed_by(rows: list[dict], wager: str) -> list[dict]:
    """The `open` verdict rows that name this forecast."""
    return [
        r
        for r in rows
        if r["kind"] == "verdict"
        and r.get("what") == "open"
        and r.get("forecast") == wager
    ]


def propose_outcome(
    rows: list[dict],
    wager: str,
    human_key: bytes | None,
    read_beacon: Callable[[int], dict] | None = None,
    seal_rounds: dict | None = None,
) -> dict:
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

    W4 break 1-3: the session's seed is the forecast's beacon round
    (`beacon_problem`: the round the wager names, fired after the tip seal,
    its randomness as `read_beacon` reads it) and its roll rows are one
    honest run of those dice (`dice_problem`). With no reader the answer is
    `unreachable`; BLS is not checked (T10, said on every proposal).
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
    want = (
        {k: w["table"].get(k) for k in sorted(api.TABLE_KEYS)}
        if isinstance(w.get("table"), dict)
        else None
    )
    got = {k: reopened[0].get(k) for k in sorted(api.TABLE_KEYS)}
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
    sheets, bad = world_problem(reopened[0], w)
    if bad is None:
        inputs = reopened[0]["world_inputs"]
        beats = inputs.get("beats") if isinstance(inputs.get("beats"), list) else []
        bad = dice_problem(
            reopened[0], srows, sheets or [], beats, inputs.get("stat_map")
        )
    if bad is not None:
        return _escalate(bad, session=session)
    path, why = path_of(srows)
    if path is None:
        return _escalate(why or "no path", session=session)
    tip_n = int(str(rows[under[0]].get("subject", "")).split(":")[1])
    seal_latest = (seal_rounds or {}).get(str(tip_n))
    seeded = beacon_problem(reopened[0], w, rows[under[0]], read_beacon, seal_latest)
    if seeded is not None:
        state, why = seeded
        if state == "unreachable":
            return {"state": "unreachable", "reason": why, "session": session}
        return _escalate(why, session=session)
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
        "beacon": {
            "round": reopened[0]["beacon_round"],
            "checked": "randomness matches the operator's reader",
            "bls": beacon.BLS_UNVERIFIED,
        },
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
    out = propose_outcome(run.rec.rows(), wager, key, run.beacon, run.seal_rounds())
    return {**out, "code": 0 if out["state"] == "proposed" else 1}


def basis(
    human_key: bytes | None,
    read_beacon: Callable[[int], dict] | None = None,
    seal_rounds: dict | None = None,
) -> Callable[[dict, list[dict], dict], str | dict | None]:
    """The R3 hook for `reverse.reconcile_wagers` (B1). A table forecast (its
    wager names a `table` world) counts only with an outcome `propose_outcome`
    reads from verdict rows written after its tip seal: unclaimed, or claimed
    by rows that say another path, it escalates with why. A wager naming no
    table world is not the table's: a session's claim on it is a note, and its
    score stands (round 3 N4). Returns why (str), a note (dict) or None."""

    def check(wager: dict, rows: list[dict], outcome: dict) -> str | dict | None:
        claimed = bool(claimed_by(rows, wager["hash"]))
        if not isinstance(wager.get("table"), dict):
            # not a table forecast: a session's claim is noted, never counted,
            # and the score stands (N4)
            if claimed:
                return {"note": "a table session claims a non-table forecast; ignored"}
            return None
        if not claimed:
            return "no table session on record plays for this forecast"
        got = propose_outcome(rows, wager["hash"], human_key, read_beacon, seal_rounds)
        if got["state"] == "unreachable":
            return {"unreachable": str(got.get("reason"))}
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
    return api.reconcile(
        cfg, human_key=human_key, argv=argv, basis=basis(key, cfg.beacon)
    )


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


# ── R13: what the seat shows ───────────────────────────────────────────────
def _no_reader() -> dict:
    return {"state": "unreachable", "why": "no ledger reader was handed over"}


def surface(
    rows: list[dict],
    *,
    covered_through: int | None = None,
    read_head: Callable[[], dict] | None = None,
    key_given: bool = False,
    breaks: list[str] | None = None,
) -> dict | None:
    """The table's part of the reverse report, for `view.table_lines` (SPEC R13;
    nothing but a read of rows): one entry per verdict session (rows, the
    forecast it claims, whether a sealed tip covers all of it) and the
    `agm_head` check. None when the record holds no table verdict row, so a box
    that is not a table's shows nothing new. The agm check states are the
    documented ones plus `unanchored`; with an anchor on record and no
    `read_head`, it is `unreachable`, said.

    A session's `sealed` has four states, never collapsed: True (a sealed tip
    covers all of it), False (one does not), None (no human key was handed over,
    so no tip could be checked) and "none" (the key WAS given and no sealed tip
    on record checks out, so none can be said to cover it).

    B1: with `breaks` (the chain's, and the anchor's when a key was given) the
    answer is only {"state": "unreachable", "why": "chain broken at row N"}: a
    record that does not verify decides and satisfies nothing, so no session
    and no agm_head line is made from it."""
    mine = [r for r in rows if r["kind"] == "verdict" and r.get("who") == TABLE[0]]
    if not mine:
        return None
    if breaks:
        first = str(breaks[0])
        row = first[4:].split(":", 1)[0] if first.startswith("row ") else None
        why = f"chain broken at row {row}" if row else f"the record is broken: {first}"
        return {"state": "unreachable", "why": why}
    wagers = {r["hash"]: r.get("id") for r in rows if r["kind"] == "wager"}
    sessions: dict[str, dict] = {}
    for r in mine:
        if r.get("what") == "agm_head":
            continue
        sid = str(r.get("session"))
        got = sessions.setdefault(
            sid, {"session": sid, "rows": 0, "forecast": None, "last": 0}
        )
        got["rows"] += 1
        got["last"] = max(got["last"], r["n"])
        if r.get("forecast") and got["forecast"] is None:
            got["forecast"] = wagers.get(r["forecast"], str(r["forecast"])[:12])
    for got in sessions.values():
        got["sealed"] = (
            ("none" if key_given else None)
            if covered_through is None
            else got["last"] <= covered_through
        )
    agm = check_agm_head(rows, read_head or _no_reader, covered_through=covered_through)
    agm.pop("read", None)  # the ledger's own reading stays with the checker
    return {"state": "populated", "sessions": list(sessions.values()), "agm": agm}
