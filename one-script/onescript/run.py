"""run — the gate is the script. Check-in, each turn, check-out, reconcile.

The sequencer only. No logic of its own lives here: every decision is made by one
of the seven parts, and this file only calls them in order.

  check-in  = boot (probes + the four gates) -> predict
  each turn = verify -> open -> predict -> door -> (resolve) -> record -> grade -> close
  each proposal = parse -> door -> served cites -> record (nothing written)
  each seal = the human's, over one subject; a sealed pass is the only write
  each act = mandate (layer 7) -> push card (layer 6, when it leaves the box)
  each say  = claims checked against the record (layer 5) before the human reads
  check-out = reverse -> view
  night     = resolve.night_pool inside a budget, yielding to presence
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from . import (
    boot,
    gate,
    nestor_seal,
    predict,
    proposals,
    record,
    resolve,
    reverse,
    serve,
    view,
)
from .record import h256

_TIP = object()  # `held`: work out how far old seals reach here

PKG = Path(__file__).resolve().parent


class Run:
    def __init__(
        self,
        box: Path,
        keys: dict[str, bytes],
        law: dict,
        clock: Callable[[], str],
        anchor: Path | None = None,
    ):
        self.keys, self.law = keys, law
        self.version = record.version_of(PKG)
        self.rec = record.Record(box, clock, self.version, gate.token(), anchor)
        self.sys = gate.system()
        self.answers: list[dict] = []
        self.graded: list[dict] = []
        self.claims: list[dict] = []
        self.acts: list[dict] = []
        self.serve_key: bytes | None = None  # made at check-in, never the seal key
        # what a Nestor seal row is re-checked by; set from the operator's
        # Config, never from a caller (api._open)
        self.keyring: Path | None = None
        self.nestor_db: Path | None = None
        # the operator's drand reader, from Config (W4 break 1); None: offline
        self.beacon = None

    def checkin(self, gate_cfg: dict | None = None) -> dict:
        report = boot.boot(self.rec, self.keys, self.law, gate_cfg)
        self.serve_key = serve.session_key()  # ids hold for this session only
        self.rec.append(
            "boot",
            self.sys,
            hard_close=report["hard_close"],
            lines=report["lines"],
            options=report["options"],
            probes=report["probes"],
            gates=report["gates"],
            egress=report["egress"],
            anchor=report["anchor"],
        )
        for s in report["settled"]:  # a deferred gate ran: the result, beside its row
            self.rec.append("deferred_result", self.sys, **s)
        return report

    def turn(
        self,
        identity: dict,
        bite: str,
        change: dict | None = None,
        question: str = "",
        sources=(),
        models=(),
        budget: resolve.Budget | None = None,
        proposed: Iterable | None = None,
    ) -> dict:
        try:
            who = gate.verify(identity, self.keys)
        except gate.Refused as e:
            return self.rec.append(
                "refused",
                self.sys,
                at="identity",
                reason=str(e),
                claimed=identity.get("who"),
            )
        n = 1 + sum(r["kind"] == "turn_open" for r in self.rec.rows())
        self.rec.open_turn(who, n, bite)
        pred = predict.declare(self.rec.rows())
        self.rec.append("predict", self.sys, turn=n, prediction=pred)

        out: dict = {"turn": n}
        if change is not None:
            change = {**change, "who": who.who}
            d = gate.door(change, self.law, self._script_index())
            row = self.rec.append(
                "door",
                who,
                where=change.get("path") or change.get("where"),
                turn=n,
                verdict=d.verdict,
                reason=d.reason,
                card=d.card,
                cites=change.get("cites", []),
            )
            out["door"] = row
            if change["kind"] == "script" and d.match:
                self._record_script(who, n, d.match)
            if d.verdict == "pass" and change.get("path") is not None:
                out["write"] = self.rec.write_file(
                    who,
                    change["path"],
                    change["data"],
                    live=change.get("live", False),
                    expect_vanish=change.get("expect_vanish", False),
                    cites=change.get("cites", []),
                )
        if proposed is not None:
            out["proposals"] = self._take(who, n, proposed)
        if question:
            a = resolve.answer(
                question,
                list(sources),
                list(models),
                budget or resolve.Budget(0),
                self.law,
                who.who,
            )
            out["answer"] = self.rec.append("answer", who, turn=n, **a)

        self.rec.append("bite", self.sys, turn=n, bite=bite)
        if pred["likely"]:
            g = predict.grade(pred, bite)
            self.graded.append(g)
            self.rec.append("grade", self.sys, turn=n, **g)
        self.rec.close_turn(who, n)
        return out

    def act(
        self,
        act: dict,
        mandates: dict,
        constraints: list,
        known_names: set | None = None,
    ) -> dict:
        """Layers 7 then 6. Nothing here performs the act: it says whether it
        may run, and what card the human must see first."""
        names = known_names if known_names is not None else self._known_names()
        d = gate.mandate(act, mandates, names, constraints)
        if d.verdict == "pass" and act["kind"] == "push":
            d = gate.push_card(
                self.rec.pile(), act["files"], act["who"], act["where"], self.law
            )
        row = {
            "kind": act["kind"],
            "verdict": d.verdict,
            "reason": d.reason,
            "card": d.card,
        }
        self.acts.append(row)
        self.rec.append(
            "act",
            self.sys,
            where=act.get("where"),
            mandate=act.get("mandate"),
            act_kind=row["kind"],
            verdict=row["verdict"],
            reason=row["reason"],
            card=row["card"],
        )
        return row

    def serve(
        self,
        tables: list[dict],
        scope: list[str] | None,
        max_chars: int | None = None,
        read_chunk=None,
    ) -> dict:
        """Write the one file the model reads. Before check-in there's no serve
        key, and serve fails closed. `max_chars` is the caller's cap."""
        return serve.serve(
            self.rec,
            tables,
            scope,
            self.serve_key,
            max_chars=max_chars,
            read_chunk=read_chunk,
            human_key=self.keys.get(gate.HUMAN),
            keyring=self.keyring,
            nestor_db=self.nestor_db,
            **self.old_seals(self.keys.get(gate.HUMAN)),
        )

    def held(
        self,
        rows: list[dict] | None = None,
        human_key: bytes | None = None,
        through: dict | object = _TIP,
    ) -> tuple[set[str], dict[str, str]]:
        """(held, unreachable): the subjects whose seal proof re-checks against
        this run's key, keyring and store (`serve.sealed_verified`). A row that
        only says `who == "human"` is in neither. `human_key` stands in for the
        run's own when given, for the proofs and the tip alike; `through` is the
        `old_seals` answer when the caller already worked it out."""
        key = human_key if human_key is not None else self.keys.get(gate.HUMAN)
        old = self.old_seals(key) if through is _TIP else through
        return serve.sealed_verified(
            self.rec.rows() if rows is None else rows,
            key,
            self.keyring,
            self.nestor_db,
            **old,  # type: ignore[arg-type]
        )

    def old_seals(self, human_key: bytes | None) -> dict:
        """How far a seal row with no proof may count, as `sealed_verified`
        takes it: `through`, the pinned tip while the sealed tip still covers
        it, and `unpinned` when the box has no pin yet (`reverse.pinned`)."""
        tip = reverse.covered_through(self.rec, human_key)
        if tip is None:
            return {"through": None, "unpinned": None}
        pin, why = reverse.pinned(self.rec, human_key)
        if why is not None:
            return {"through": None, "unpinned": (tip, why)}
        return {"through": None if pin is None else min(pin, tip), "unpinned": None}

    def _anchor(self) -> dict | None:
        try:
            return self.rec.anchor()
        except (ValueError, OSError):
            return None

    def seal_rounds(self) -> dict[str, int]:
        """The reader's newest round at each tip seal, by tip row, as the
        anchor (outside the box) keeps them (R23a); {} when unreadable."""
        a = self._anchor() or {}
        got = a.get("rounds") if isinstance(a, dict) else None
        return dict(got) if isinstance(got, dict) else {}

    _NOW = object()

    def pin(self, anchor: dict | None | object = _NOW) -> dict | None:
        """Write the `pin` row, once: the tip the human had sealed when this
        code first wrote to the box, so old seal rows with no proof count only
        up to it (`reverse.pinned`). No anchor, or one that can't be read, pins
        no tip, and then no proofless row counts. `api._open` calls it on every
        verb, right after the `invocation` row; `seal_tip` hands over the anchor
        as it was before the new tip replaced it. A pin already on record is left alone,
        and None is returned."""
        if any(r["kind"] == "pin" for r in self.rec.rows()):
            return None
        a = self._anchor() if anchor is Run._NOW else anchor
        if not isinstance(a, dict):
            return self.rec.append("pin", self.sys, tip_n=None)
        return self.rec.append(
            "pin", self.sys, tip_n=a["n"], tip_hash=a["hash"], proof=a["proof"]
        )

    def seal_rows(
        self, rows: list[dict] | None = None
    ) -> tuple[set[int], dict[int, str]]:
        """Per row, not per subject: the `n` of every seal row whose own proof
        re-checks, and of every one that can't be checked here, with why.
        `held` answers whether a subject is sealed; a reader that takes
        anything else from the seal row (a spec beside it, its stamp) needs
        this, since a forged row over a sealed subject is in `held` too."""
        rows = self.rec.rows() if rows is None else rows
        key = self.keys.get(gate.HUMAN)
        old = self.old_seals(key)
        ring = self.keyring
        if ring is not None and not isinstance(ring, dict):
            try:
                ring = nestor_seal.load_keyring(ring)
            except nestor_seal.Unverifiable:
                pass  # sealed_verified says why, row by row
        good: set[int] = set()
        unchecked: dict[int, str] = {}
        for r in rows:
            if r.get("kind") != "seal" or not isinstance(r.get("n"), int):
                continue
            ok, why = serve.sealed_verified([r], key, ring, self.nestor_db, **old)
            if ok:
                good.add(r["n"])
            elif why:
                unchecked[r["n"]] = next(iter(why.values()))
        return good, unchecked

    # ── proposals: the model's Write is a row, and a seal is what writes it ──
    def _current_serve(self) -> tuple[dict, bytes] | None:
        """This check-in's last `serve` row and the served file's bytes, only
        while the file hashes to that row's `sha`; else None."""
        rows = self.rec.rows()
        start = max((i for i, r in enumerate(rows) if r["kind"] == "boot"), default=-1)
        mine = [r for r in rows[start + 1 :] if r["kind"] == "serve"]
        if not mine:
            return None
        last = mine[-1]
        try:
            data = (self.rec.box / serve.OUT).read_bytes()
        except OSError:
            return None
        if h256(data) != last.get("sha"):
            return None
        return last, data

    def served_file(self) -> dict | None:
        """The whole served document (state, return, tables with their rows)
        as the model was handed it in THIS check-in, under the same hash
        match as `served_doc`; None when no such file stands."""
        cur = self._current_serve()
        if cur is None:
            return None
        try:
            doc = json.loads(cur[1])
        except ValueError:
            return None
        return doc if isinstance(doc, dict) else None

    def served_doc(self) -> dict:
        """What the model was served in THIS check-in, judged from the record.

        The state and the served ids come from the current check-in's last
        `serve` row, and only while the file in the box hashes to that row's
        `sha`. A file left by an earlier session, edited, or written by hand
        doesn't match any row of this check-in, so it counts for nothing: the
        document is empty, every cite fails and no idea of its own is accepted.
        The file is never the authority; the record's row is."""
        cur = self._current_serve()
        if cur is None:
            return {"tables": []}
        last = cur[0]
        ids = last.get("served")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return {"tables": []}
        return {"state": last.get("state"), "tables": [{"id": i} for i in ids]}

    def _take(self, who, n: int, items: Iterable) -> list[dict]:
        """Each proposal through the door and the served cites. Recorded and
        judged; nothing is written. A malformed row is `refused`, not a crash."""
        doc = self.served_doc()
        rows = []
        for got in proposals.read(items):
            p = got["proposal"]
            if p is None:
                rows.append(
                    self.rec.append(
                        "proposal",
                        who,
                        turn=n,
                        line=got["line"],
                        verdict="refused",
                        reason=got["why"],
                    )
                )
                continue
            d = gate.door(
                {
                    "kind": "edit",
                    "path": p["path"],
                    "who": who.who,
                    "none_because": p["claim"],  # the door wants a stated why
                },
                self.law,
                self._script_index(),
            )
            lost = serve.check_cites(doc, p["cites"])
            # The model's own idea: no cites. Code, never the model, marks it, and
            # accepts it only alongside a populated served state ("only along
            # side"); with nothing served it is refused, not guessed at.
            own = not p["cites"]
            if own and doc.get("state") != "populated":
                rows.append(
                    self.rec.append(
                        "proposal",
                        who,
                        turn=n,
                        line=got["line"],
                        verdict="refused",
                        reason="no cites, and nothing is served as populated; "
                        "an idea of its own is accepted only alongside served state",
                        own_idea=True,
                    )
                )
                continue
            card = {"own_idea": True, **d.card} if own else d.card
            rows.append(
                self.append_proposal(
                    who,
                    p,
                    verdict="link_fail" if lost else d.verdict,
                    reason="a cite is not in the served file" if lost else d.reason,
                    link_fail=lost,
                    own_idea=own,
                    card=card,
                    turn=n,
                    line=got["line"],
                )
            )
        return rows

    def append_proposal(
        self,
        who,
        p: dict,
        *,
        verdict: str,
        reason: str,
        link_fail: list,
        own_idea: bool,
        card: dict,
        **extra,
    ) -> dict:
        """One judged `proposal` row, the shape `find_proposal` and
        `seal_proposal` read: the model's rows (`_take`) and the table's
        decision beats (`table.propose_beat`) both go through here."""
        return self.rec.append(
            "proposal",
            who,
            where=p["path"],
            path=p["path"],
            sha=h256(p["data"]),
            data=p["data"],
            cites=p["cites"],
            claim=p["claim"],
            subject=proposals.subject(p),
            verdict=verdict,
            reason=reason,
            link_fail=link_fail,
            own_idea=own_idea,
            card=card,
            **extra,
        )

    def find_proposal(self, subject: str) -> tuple[dict | None, str | None]:
        """The stored proposal row for `subject` that really is that proposal,
        or (None, why). The record's rows are only a place the bytes were put:
        a row is the proposal the human sealed only if the hash of its own
        path, data, cites and claim is the subject. A row that says one subject
        and carries other bytes is skipped, not trusted; and the path rule and
        the door run again on the bytes that would be written."""
        rows = [
            r
            for r in self.rec.rows()
            if r["kind"] == "proposal" and r.get("subject") == subject
        ]
        if not rows:
            return None, "no such proposal on record"
        whole = [
            r
            for r in rows
            if all(k in r for k in proposals.KEYS) and self._hashes_to(r, subject)
        ]
        if not whole:
            return None, "no stored row hashes to this subject; nothing written"
        prop = whole[-1]
        if prop["verdict"] != "pass":
            return (
                None,
                f"the proposal is {prop['verdict']}, not a pass; nothing written",
            )
        fields = {k: prop[k] for k in proposals.KEYS}
        _, bad = proposals.check(fields)
        if bad:
            return None, f"the stored proposal fails the contract now: {bad}"
        d = gate.door(
            {
                "kind": "edit",
                "path": prop["path"],
                "who": prop["who"],
                "none_because": prop["claim"],
            },
            self.law,
            self._script_index(),
        )
        if d.verdict != "pass":
            return None, f"the door says {d.verdict} on these bytes: {d.reason}"
        return prop, None

    def pooled(self) -> list[dict]:
        """The pooled unsealed pass set, read-only: every proposal on record
        that stands as a pass (`find_proposal`: hashes to its subject, fits the
        contract, passes the door now) and that the record holds no verified
        human seal over (`held`). A seal that can't be checked here leaves the
        proposal listed, marked `seal: unreachable`. One entry per subject, in
        the order each subject first appeared; the latest valid row for a
        subject is the one that counts. Nothing is written and nothing is
        sealed."""
        rows = self.rec.rows()
        held, unchecked = self.held(rows)
        subjects = dict.fromkeys(
            r["subject"]
            for r in rows
            if r["kind"] == "proposal"
            and r.get("verdict") == "pass"
            and isinstance(r.get("subject"), str)
            and r["subject"] not in held
        )
        out = []
        for s in subjects:
            prop, why = self.find_proposal(s)
            if why is None and prop is not None:
                item = {"subject": s, **{k: prop[k] for k in sorted(proposals.KEYS)}}
                if s in unchecked:
                    item |= {"seal": "unreachable", "why": unchecked[s]}
                out.append(item)
        return out

    @staticmethod
    def _hashes_to(row: dict, subject: str) -> bool:
        try:
            return proposals.subject({k: row[k] for k in proposals.KEYS}) == subject
        except (TypeError, ValueError):
            return False

    def write_proposal(self, subject: str) -> dict:
        """Write one proposal, only if it passed and the human sealed it. The
        seal is read from the record, never taken on a caller's word; and what
        is written is recomputed from the stored path and data, which must hash
        to the sealed subject, so a row edited after the fact writes nothing."""
        rows = self.rec.rows()
        prop, why = self.find_proposal(subject)
        state = {}
        if why is None:
            held, unchecked = self.held(rows)
            if subject in unchecked and subject not in held:
                why = f"{unchecked[subject]}; nothing written"
                state = {"state": "unreachable"}
            elif subject not in held:
                why = "no human seal over this proposal; nothing written"
        if why is not None:
            return self.rec.append(
                "refused",
                self.sys,
                at="write_proposal",
                reason=why,
                subject=subject,
                **state,
            )
        data = prop["data"].encode("utf-8")
        on_disk = self.rec.box / prop["path"]
        done = [
            r
            for r in rows
            if r["kind"] == "write"
            and r["path"] == prop["path"]
            and r["sha"] == h256(data)
        ]
        # a write row is a claim: it counts only while the bytes are really there
        if done and on_disk.is_file() and on_disk.read_bytes() == data:
            return done[-1]
        try:
            return self.rec.write_file(
                self.sys, prop["path"], data, cites=prop["cites"], provenance="authored"
            )
        except (PermissionError, OSError) as e:
            return self.rec.append(
                "refused",
                self.sys,
                at="write_proposal",
                reason=f"the write was refused: {e}",
                subject=subject,
            )

    def say(self, text: str, facts: dict, *, symbols=None) -> list[dict]:
        """Layer 5: the claims in an output, checked before the human reads it.
        The text itself is never rewritten; the rows sit beside it.

        `symbols` (T-cbm-1) is a callable `text -> {file: entry}` (see
        `graph.symbols_for`) that turns on the `symbol` kind when the facts do
        not already carry one. Left None, no symbol row is made."""
        if symbols is not None and "symbols" not in facts:
            facts = {**facts, "symbols": symbols(text)}
        rows = gate.check_claims(text, facts)
        self.claims += rows
        self.rec.append("claims", self.sys, text_hash=h256(text), claims=rows)
        return rows

    def wager(
        self,
        wid: str,
        distribution: list[dict] | None = None,
        scores: dict | None = None,
        *,
        by: str | None = None,
        committed: dict | None = None,
        commit: str | None = None,
        table: dict | None = None,
        beacon_latest: int | None = None,
    ) -> dict:
        """Record a declared wager. Its row hash is the subject a human seal
        names to say what happened; nothing here grades it.

        `api.forecast` passes `by` (a role, `reverse.ROLES`), `committed` (a
        foreign commitment, when the forecast was made outside the record) and
        `commit=reverse.COMMIT`, which makes the outcome count only by chain
        order under the human's sealed tip (SPEC R3). Left out, the row has the
        older shape, byte for byte."""
        extra: dict = {}
        if commit is not None:
            extra = {"by": by, "committed": committed, "commit": commit}
        if table is not None:  # the world and sheets a table forecast is about
            extra["table"] = table
        if beacon_latest is not None:  # the reader's newest round then (R23a)
            extra["beacon_latest"] = beacon_latest
        return self.rec.append(
            "wager",
            self.sys,
            id=wid,
            distribution=distribution or [],
            scores=scores,
            **extra,
        )

    def seal(
        self,
        subject: str,
        proof: str,
        human_key: bytes,
        outcome: dict | None = None,
    ) -> dict:
        """The proof rides on the row: it is what a reader re-checks (`held`),
        since a row's `who` is only a label anyone writing the record can set.

        A wager seal passes the wager's row hash as `subject` and its `outcome`
        (path_index / path / winner). What the human signs is then the
        outcome's own subject, `reverse.outcome_subject(wager, outcome)`, so the
        outcome is inside what was signed: a proof copied onto another row
        can't carry a different outcome. The row keeps `wager` and `outcome`
        beside it, and a reader works the subject out again from them."""
        extra: dict = {}
        if outcome is not None:
            extra = {"wager": subject, "outcome": outcome}
            subject = reverse.outcome_subject(subject, outcome)
        try:
            hum = gate.human(subject, proof, human_key)
        except gate.Refused as e:
            return self.rec.append(
                "refused", self.sys, at="seal", reason=str(e), subject=subject
            )
        return self.rec.append(
            "seal", hum, where=subject, subject=subject, proof=proof, **extra
        )

    def seal_nestor(
        self,
        subject: str,
        pair: object,
        keyring: dict,
        verifiers: frozenset | None = None,
        outcome: dict | None = None,
    ) -> dict:
        """The human's seal as a sealed Nestor pair whose conclusion is exactly
        `subject`: no terminal, no key typed. The row says whose pair it was,
        and carries the pair's signed fields, so a reader can re-check it.

        A wager seal passes the wager's row hash as `subject` and its
        `outcome`, as `seal` does: the pair's conclusion must then be
        `reverse.outcome_subject(wager, outcome)`, and the row keeps `wager`
        and `outcome` beside the pair. Recorded gap (SPEC-documented C6): an
        outcome sealed by pair still counts only under a tip sealed by the
        human's key; `reverse.covered_through` does not take a Nestor-sealed
        tip, and this does not change that."""
        extra: dict = {}
        if outcome is not None:
            extra = {"wager": subject, "outcome": outcome}
            subject = reverse.outcome_subject(subject, outcome)
        try:
            hum, who = gate.human_nestor(subject, pair, keyring, verifiers)
        except gate.Refused as e:
            return self.rec.append(
                "refused", self.sys, at="seal", reason=str(e), subject=subject
            )
        signed = {k: pair[k] for k in nestor_seal.PAIR_FIELDS}  # type: ignore[index]
        return self.rec.append(
            "seal",
            hum,
            where=subject,
            subject=subject,
            via="nestor",
            verifier=who,
            pair=signed,
            **extra,
        )

    def seal_tip(
        self,
        proof: str,
        human_key: bytes,
        tip: dict | None = None,
        beacon_latest: int | None = None,
    ) -> dict:
        """The human seals the record's tip; the anchor keeps it outside the box.
        A broken chain is never anchored: sealing it would bless the break.

        `tip` ({n, hash}) names the row the human signed, for a seal made in a
        later invocation than the row (the verb's own `invocation` row moves
        the record's tip before the proof can arrive). It must still be that
        row, and no older than the tip already anchored. Left out, the
        record's tip as it stands now."""
        breaks = self.rec.verify_chain()
        if tip is None:
            tip = self.rec.tip()
        elif not breaks:
            at = next((r for r in self.rec.rows() if r["n"] == tip.get("n")), None)
            old = self._anchor()
            why = None
            if at is None or at["hash"] != tip.get("hash"):
                why = f"row {tip.get('n')} is not on record with that hash"
            elif old is not None and tip["n"] < old["n"]:
                why = f"row {tip['n']} is older than the anchored tip, row {old['n']}"
            if why is not None:
                return self.rec.append(
                    "refused",
                    self.sys,
                    at="seal_tip",
                    reason=why,
                    subject=record.tip_subject(tip),
                )
        if tip is None or breaks:
            why = "nothing to seal" if tip is None else f"the chain is broken: {breaks}"
            return self.rec.append("refused", self.sys, at="seal_tip", reason=why)
        subject = record.tip_subject(tip)
        try:
            hum = gate.human(subject, proof, human_key)
        except gate.Refused as e:
            return self.rec.append(
                "refused", self.sys, at="seal_tip", reason=str(e), subject=subject
            )
        before = self._anchor()  # the tip a first pin keeps, read before it moves
        self.rec.set_anchor(tip, proof, beacon_latest)
        extra = {} if beacon_latest is None else {"beacon_latest": beacon_latest}
        row = self.rec.append(
            "seal", hum, where=subject, subject=subject, proof=proof, **extra
        )
        self.pin(before)
        return row

    def night(self, questions, models, budget: resolve.Budget, present) -> str:
        got, how = resolve.night_pool(list(questions), list(models), budget, present)
        for a in got:
            self.answers.append(a)
            self.rec.append("night_answer", self.sys, **a)
        self.rec.append(
            "night", self.sys, status=how, spent=budget.spent, units=budget.units
        )
        return how

    def reconciled(
        self,
        *,
        changed_law=frozenset(),
        sealed=None,
        task_of=None,
        human_key: bytes | None = None,
        basis=None,
        read_head=None,
        breaks: list[str] | None = None,
    ) -> dict:
        """The reverse report over the record as it stands: pure, it appends
        nothing. `checkout` draws its screen from it; `api.needs` reads it.
        The record is read, and its tip checked, once.

        `basis` is the R3 hook `reverse.reconcile_wagers` asks about each
        sealed outcome (`table.basis`). Whenever the record holds table verdict
        rows or a wager naming a table world it is bound HERE, to the
        operator's beacon reader, and a caller's `basis` is ignored (R23a: the
        reader is the operator's, never the caller's), so every report
        (reconcile, needs, peek, checkout, the morning screen) goes through the
        one door. A caller's `basis` is used only on a box with no table.

        `breaks` are the chain and anchor breaks as read BEFORE this verb's
        own rows were appended (`api._breaks_before`, W4 break 7): a record
        cut after its sealed tip then reads "rows were cut", where the
        invocation row landing at the cut would read as a rewrite.

        `read_head` (R13) is the ai-game-master ledger reader `table.surface`
        hands `table.check_agm_head`; it only matters for a table box, where
        the report gains a `table` key (sessions and the agm_head check)."""
        rows = self.rec.rows()
        if any(
            (r["kind"] == "verdict" and r.get("who") == "table")
            or (r["kind"] == "wager" and isinstance(r.get("table"), dict))
            for r in rows
        ):
            from . import table  # table imports api, which imports this module

            # R23a: on a table box the hook is the Run's, bound to the
            # operator's reader (Config.beacon); a caller's `basis` is ignored
            basis = table.basis(human_key, self.beacon, self.seal_rounds())
        key = human_key if human_key is not None else self.keys.get(gate.HUMAN)
        through = reverse.covered_through(self.rec, key)
        held, unreachable = self.held(rows, key, self.old_seals(key))
        chain = (
            breaks
            if breaks is not None
            else self.rec.verify_chain()
            + (self.rec.verify_anchor(human_key) if human_key is not None else [])
        )
        report = reverse.reconcile(
            self.rec.box,
            self.rec.pile(),
            rows,
            self.version,
            changed_law=changed_law,
            answers=self.answers,
            sealed=sealed,
            task_of=task_of,
            predictions=self.graded,
            # a wager outcome counts only when the caller handed the key over
            wagers_through=through if human_key is not None else None,
            held=held,
            unreachable=unreachable,
            wagers_key=human_key,  # tip seals re-checked row by row (SPEC R3)
            wagers_basis=basis,
            # a broken record is said, never read as "no seal" (INVARIANTS §1)
            wagers_breaks=chain if human_key is not None else None,
        )
        from . import table  # table imports api, which imports this module

        # a tip counts as covering only when the caller handed the key over
        shown = table.surface(
            rows,
            covered_through=through if human_key is not None else None,
            read_head=read_head,
            key_given=human_key is not None,
            breaks=chain,  # B1: a broken record shows the table unreachable
        )
        if shown is not None:
            report["table"] = shown
        return report

    def checkout(
        self,
        *,
        changed_law=frozenset(),
        sealed=None,
        task_of=None,
        boot_report: dict | None = None,
        human_key: bytes | None = None,
        basis=None,
        read_head=None,
        breaks: list[str] | None = None,
    ) -> tuple[dict, str]:
        """`human_key` lets reverse check the sealed anchor tip; without it no
        wager outcome counts (it cannot be shown to be covered). `basis` as
        `reconciled` takes it."""
        rep = self.reconciled(
            changed_law=changed_law,
            sealed=sealed,
            task_of=task_of,
            human_key=human_key,
            basis=basis,
            read_head=read_head,
            breaks=breaks,
        )
        screen = view.morning(rep, boot_report, self.claims, self.acts)
        self.rec.append("reconcile", self.sys, report_hash=h256(screen))
        return rep, screen

    def _known_names(self) -> set:
        """Names the record holds: bites, turn intents, files written."""
        rows = self.rec.rows()
        return (
            {r["bite"] for r in rows if r["kind"] == "bite"}
            | {r["intent"] for r in rows if r["kind"] == "turn_open"}
            | {r["path"] for r in rows if r["kind"] == "write"}
        )

    # ── the script index lives in the record, not in memory ──────────────────
    def _script_index(self) -> list[dict]:
        return [
            {
                "label": f"row {r['n']}",
                "name": r.get("name"),
                "sha": r["sha"],
                "shape": r["shape"],
                "features": frozenset(r["features"]),
            }
            for r in self.rec.rows()
            if r["kind"] == "script"
        ]

    def _record_script(self, who, n: int, m: dict) -> None:
        rows = [r for r in self.rec.rows() if r["kind"] == "script"]
        root = rows[m["of"]]["family_root"] if m.get("of") is not None else m["sha"]
        self.rec.append(
            "script",
            who,
            turn=n,
            name=m.get("name"),
            sha=m["sha"],
            shape=m.get("shape", ""),
            features=sorted(m.get("features", ())),
            verdict=m["verdict"],
            family_root=root,
        )
