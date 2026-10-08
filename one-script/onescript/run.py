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

from . import boot, gate, predict, proposals, record, resolve, reverse, serve, view
from .record import h256

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
    ) -> dict:
        """Write the one file the model reads. Before check-in there's no serve
        key, and serve fails closed. `max_chars` is the caller's cap."""
        return serve.serve(self.rec, tables, scope, self.serve_key, max_chars=max_chars)

    # ── proposals: the model's Write is a row, and a seal is what writes it ──
    def served_doc(self) -> dict:
        """What the model was last served, read back from the box. Nothing
        served, or a file that isn't a served document, is a document with no
        tables, so every cite against it fails."""
        try:
            doc = json.loads((self.rec.box / serve.OUT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"tables": []}
        return doc if isinstance(doc, dict) else {"tables": []}

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
            rows.append(
                self.rec.append(
                    "proposal",
                    who,
                    where=p["path"],
                    turn=n,
                    line=got["line"],
                    path=p["path"],
                    sha=h256(p["data"]),
                    data=p["data"],
                    cites=p["cites"],
                    claim=p["claim"],
                    subject=proposals.subject(p),
                    verdict="link_fail" if lost else d.verdict,
                    reason="a cite is not in the served file" if lost else d.reason,
                    link_fail=lost,
                    card=d.card,
                )
            )
        return rows

    def write_proposal(self, subject: str) -> dict:
        """Write one proposal, only if it passed and the human sealed it. The
        seal is read from the record, never taken on a caller's word."""
        rows = self.rec.rows()
        prop = next(
            (
                r
                for r in reversed(rows)
                if r["kind"] == "proposal" and r.get("subject") == subject
            ),
            None,
        )
        why = None
        if prop is None:
            why = "no such proposal on record"
        elif prop["verdict"] != "pass":
            why = f"the proposal is {prop['verdict']}, not a pass; nothing written"
        elif subject not in serve.sealed(rows):
            why = "no human seal over this proposal; nothing written"
        if why is not None:
            return self.rec.append(
                "refused", self.sys, at="write_proposal", reason=why, subject=subject
            )
        data = prop["data"].encode("utf-8")
        done = [
            r
            for r in rows
            if r["kind"] == "write"
            and r["path"] == prop["path"]
            and r["sha"] == h256(data)
        ]
        if done:
            return done[-1]
        return self.rec.write_file(
            self.sys, prop["path"], data, cites=prop["cites"], provenance="authored"
        )

    def say(self, text: str, facts: dict) -> list[dict]:
        """Layer 5: the claims in an output, checked before the human reads it.
        The text itself is never rewritten; the rows sit beside it."""
        rows = gate.check_claims(text, facts)
        self.claims += rows
        self.rec.append("claims", self.sys, text_hash=h256(text), claims=rows)
        return rows

    def seal(self, subject: str, proof: str, human_key: bytes) -> dict:
        try:
            hum = gate.human(subject, proof, human_key)
        except gate.Refused as e:
            return self.rec.append(
                "refused", self.sys, at="seal", reason=str(e), subject=subject
            )
        return self.rec.append("seal", hum, where=subject, subject=subject)

    def seal_nestor(
        self,
        subject: str,
        pair: object,
        keyring: dict,
        verifiers: frozenset | None = None,
    ) -> dict:
        """The human's seal as a sealed Nestor pair whose conclusion is exactly
        `subject`: no terminal, no key typed. The row says whose pair it was."""
        try:
            hum, who = gate.human_nestor(subject, pair, keyring, verifiers)
        except gate.Refused as e:
            return self.rec.append(
                "refused", self.sys, at="seal", reason=str(e), subject=subject
            )
        return self.rec.append(
            "seal", hum, where=subject, subject=subject, via="nestor", verifier=who
        )

    def seal_tip(self, proof: str, human_key: bytes) -> dict:
        """The human seals the record's tip; the anchor keeps it outside the box.
        A broken chain is never anchored: sealing it would bless the break."""
        tip, breaks = self.rec.tip(), self.rec.verify_chain()
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
        self.rec.set_anchor(tip, proof)
        return self.rec.append("seal", hum, where=subject, subject=subject)

    def night(self, questions, models, budget: resolve.Budget, present) -> str:
        got, how = resolve.night_pool(list(questions), list(models), budget, present)
        for a in got:
            self.answers.append(a)
            self.rec.append("night_answer", self.sys, **a)
        self.rec.append(
            "night", self.sys, status=how, spent=budget.spent, units=budget.units
        )
        return how

    def checkout(
        self,
        *,
        changed_law=frozenset(),
        sealed=None,
        task_of=None,
        boot_report: dict | None = None,
    ) -> tuple[dict, str]:
        rep = reverse.reconcile(
            self.rec.box,
            self.rec.pile(),
            self.rec.rows(),
            self.version,
            changed_law=changed_law,
            answers=self.answers,
            sealed=sealed,
            task_of=task_of,
            predictions=self.graded,
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
