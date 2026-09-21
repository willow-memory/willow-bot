"""Two steward wirings measured on 2026-09-20 (gaps 6031199ac4e1 and
b26c8232fa9d): the heartbeat calls ``net_authority_drain`` beside
``seal_drain`` and mirrors its two-half receipt honestly; the audit step
names the auditor envelope instead of refusing itself with EAMBIG every
tick. The MCP client is a fake — patched on the real module's ``call``
attribute, which is what ``from willow_bot.steward import mcp_client`` then
resolves (a sys.modules-only stub is bypassed once the real module was
imported earlier in the run, and the real client blocks ~9 min per leg).
"""
from __future__ import annotations

import json
import sys

import pytest

from willow_bot.steward import heartbeat, tick


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    monkeypatch.setenv("WILLOW_BOT_MCP_APP_ID", "willow")
    monkeypatch.delenv("WILLOW_BOT_STEWARD_TOOLS", raising=False)
    monkeypatch.delenv("WILLOW_BOT_STEWARD_STATE", raising=False)
    return tmp_path


def _patch_client(monkeypatch, fn):
    """Patch BOTH the package attribute and sys.modules, per the 09-19 lesson."""
    from willow_bot.steward import mcp_client as real

    monkeypatch.setattr(real, "call", fn)
    assert sys.modules["willow_bot.steward.mcp_client"].call is fn


# ── heartbeat: net_authority_drain rides beside seal_drain ───────────────────

def test_net_authority_drain_is_curated_immediately_after_seal_drain():
    names = [n for n, _ in heartbeat.DEFAULT_CURATED]
    assert names.index("net_authority_drain") == names.index("seal_drain") + 1


def _heartbeat_with(monkeypatch, reply_for):
    seen = []

    def call(name, inputs):
        seen.append((name, inputs))
        return reply_for(name)

    _patch_client(monkeypatch, call)
    return seen


def _tool_entry(receipt, name):
    return next(e for e in receipt["tools"] if e["tool"] == name)


def test_heartbeat_mirrors_a_populated_drain_with_its_counts(home, monkeypatch):
    populated = {
        "event": "net_authority_tick", "at": "2026-09-21T00:14:16Z", "state": "populated",
        "tasks": {"event": "net_authority_tick", "state": "populated", "held": 1,
                  "rows": [{"task_id": "8DBQJJG6", "state": "minted"}],
                  "counts": {"waiting": 0, "minted": 1, "refused": 0, "unreachable": 0},
                  "truncated": False, "inked": True},
        "leases": {"event": "net_lease_tick", "state": "empty", "requests": 0},
    }
    seen = _heartbeat_with(monkeypatch, lambda n: populated if n == "net_authority_drain" else {"ok": True})
    r = heartbeat.run_heartbeat()
    assert r["status"] == "ok"
    assert ("net_authority_drain", {"app_id": "willow"}) in seen
    e = _tool_entry(r, "net_authority_drain")
    assert e["outcome"] == "ok" and e["state"] == "populated"
    assert e["tasks.held"] == 1
    assert e["tasks.counts"] == {"waiting": 0, "minted": 1, "refused": 0, "unreachable": 0}
    assert e["tasks.truncated"] is False
    assert e["leases.requests"] == 0
    # the rows themselves stay out of the heartbeat — keys only
    assert "rows" not in json.dumps(e)


def test_heartbeat_mirrors_an_empty_drain_as_empty(home, monkeypatch):
    empty = {"event": "net_authority_tick", "at": "t", "state": "empty",
             "tasks": {"state": "empty", "held": 0},
             "leases": {"state": "empty", "requests": 0}}
    _heartbeat_with(monkeypatch, lambda n: empty if n == "net_authority_drain" else {"ok": True})
    e = _tool_entry(heartbeat.run_heartbeat(), "net_authority_drain")
    assert e["state"] == "empty" and e["tasks.held"] == 0 and e["leases.requests"] == 0


def test_heartbeat_mirrors_an_unreachable_drain_and_never_reads_it_as_nothing_held(home, monkeypatch):
    """Signer socket down / queue blind: the verb answers unreachable with
    both halves None. The heartbeat carries state+reason and NO held/counts
    — an absent key, not a zero."""
    unreachable = {"event": "net_authority_tick", "at": "t", "state": "unreachable",
                   "reason": "queue_unavailable", "tasks": None, "leases": None}
    _heartbeat_with(monkeypatch, lambda n: unreachable if n == "net_authority_drain" else {"ok": True})
    e = _tool_entry(heartbeat.run_heartbeat(), "net_authority_drain")
    assert e["outcome"] == "ok" and e["state"] == "unreachable"
    assert e["reason"] == "queue_unavailable"
    assert "tasks.held" not in e and "tasks.counts" not in e and "leases.requests" not in e


def test_heartbeat_reports_a_raising_drain_as_could_not_run(home, monkeypatch):
    def call(name, inputs):
        if name == "net_authority_drain":
            raise RuntimeError("MCP server did not initialize within 90s")
        return {"ok": True}

    _patch_client(monkeypatch, call)
    e = _tool_entry(heartbeat.run_heartbeat(), "net_authority_drain")
    assert e["outcome"] == "could-not-run" and "90s" in e["detail"]


# ── audit: the auditor envelope is named, never guessed ──────────────────────

_ENVELOPES = [
    {"envelope_id": "env-dispatch-d999e1950cce",
     "bounds": {"to_agents": ["loki", "hanuman", "ada"], "task_class": "build-work-order"}},
    {"envelope_id": "env-dispatch-8cabfd667263",
     "bounds": {"to_agents": "loki", "task_class": "auditor"}},
    {"envelope_id": "env-dispatch-301fa3b93732",
     "bounds": {"to_agents": ["jeles"], "task_class": "librarian"}},
]


def _state_with_pending(home, *keys, envelope_id=None):
    from willow_bot.steward.config import state_path

    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    st = {
        "seen": list(keys), "open": list(keys), "merged_synced": [], "inbox_consumed": [],
        "webhook_signals": [],
        "pending_audit": [{"repo_pr": k, "title": f"t {k}", "url": f"https://x/{k}"} for k in keys],
        "audit_dispatched": {},
    }
    if envelope_id:
        st[tick._AUDIT_ENVELOPE_STATE_KEY] = envelope_id
    p.write_text(json.dumps(st))
    return p


class _AmbiguousThenOk:
    """EAMBIG-with-envelopes on a bare call; a packet when the id is named."""

    def __init__(self, envelopes=_ENVELOPES, accept="env-dispatch-8cabfd667263"):
        self.calls, self.envelopes, self.accept = [], envelopes, accept
        self._n = 0

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        eid = inputs.get("envelope_id")
        if not eid:
            return {"error": "EAMBIG", "reason": "multiple active envelopes govern this verb",
                    "envelope_ids": [e["envelope_id"] for e in self.envelopes],
                    "envelopes": self.envelopes}
        if eid != self.accept:
            return {"error": "ENOENT: envelope does not govern dispatch for 'willow'"}
        self._n += 1
        return {"dispatch_id": f"D{self._n}", "status": "pending"}


def test_auditor_envelope_is_picked_by_bounds_not_by_id():
    assert tick._auditor_envelope_from(_ENVELOPES) == "env-dispatch-8cabfd667263"
    # list spelling of to_agents works too
    listed = [{"envelope_id": "env-dispatch-x", "bounds": {"to_agents": ["ada", "loki"], "task_class": "auditor"}}]
    assert tick._auditor_envelope_from(listed) == "env-dispatch-x"
    # no auditor row, wrong agent, malformed rows: nothing is picked
    assert tick._auditor_envelope_from([_ENVELOPES[0], _ENVELOPES[2]]) is None
    assert tick._auditor_envelope_from([{"envelope_id": "e", "bounds": {"to_agents": "ada", "task_class": "auditor"}}]) is None
    assert tick._auditor_envelope_from("not a list") is None
    assert tick._auditor_envelope_from([None, {"bounds": None}]) is None


def test_first_eambig_resolves_the_envelope_and_retries_the_same_pr_in_the_tick(home, monkeypatch):
    p = _state_with_pending(home, "forge-play/Forge#33", "oakenscrolls-office#8")
    c = _AmbiguousThenOk()
    _patch_client(monkeypatch, c)
    r = tick.run_audit()
    assert r["status"] == "ok"
    assert [d["repo_pr"] for d in r["dispatched"]] == ["forge-play/Forge#33", "oakenscrolls-office#8"]
    assert r["refused"] == []
    assert r["envelope_id"] == "env-dispatch-8cabfd667263" and r["envelope_resolved"] is True
    # bare call, retry with the id, then the second PR straight with the id
    ids = [inputs.get("envelope_id") for _, inputs in c.calls]
    assert ids == [None, "env-dispatch-8cabfd667263", "env-dispatch-8cabfd667263"]
    assert all(inputs["role"] == "auditor" and inputs["to_app"] == "loki" for _, inputs in c.calls)
    st = json.loads(p.read_text())
    assert st[tick._AUDIT_ENVELOPE_STATE_KEY] == "env-dispatch-8cabfd667263"
    assert st["pending_audit"] == []


def test_a_remembered_envelope_is_used_from_the_first_call(home, monkeypatch):
    _state_with_pending(home, "o/r#1", envelope_id="env-dispatch-8cabfd667263")
    c = _AmbiguousThenOk()
    _patch_client(monkeypatch, c)
    r = tick.run_audit()
    assert len(c.calls) == 1 and c.calls[0][1]["envelope_id"] == "env-dispatch-8cabfd667263"
    assert r["dispatched"][0]["repo_pr"] == "o/r#1" and r["envelope_resolved"] is False


def test_no_auditor_envelope_is_refused_by_name_not_as_eambig(home, monkeypatch):
    p = _state_with_pending(home, "o/r#1")
    c = _AmbiguousThenOk(envelopes=[_ENVELOPES[0], _ENVELOPES[2]])
    _patch_client(monkeypatch, c)
    r = tick.run_audit()
    assert r["dispatched"] == [] and len(c.calls) == 1
    assert r["refused"][0]["error"].startswith("no auditor envelope")
    assert "EAMBIG" not in r["refused"][0]["error"]
    st = json.loads(p.read_text())
    assert st["pending_audit"][0]["last_error"].startswith("no auditor envelope")
    assert tick._AUDIT_ENVELOPE_STATE_KEY not in st


def test_a_remembered_envelope_that_stopped_governing_is_forgotten(home, monkeypatch):
    p = _state_with_pending(home, "o/r#1", envelope_id="env-dispatch-stale")
    c = _AmbiguousThenOk()  # accepts only the real auditor id; stale -> ENOENT
    _patch_client(monkeypatch, c)
    r = tick.run_audit()
    assert r["dispatched"] == [] and "ENOENT" in r["refused"][0]["error"]
    assert len(c.calls) == 1, "one honest refusal, no guessed retry this tick"
    st = json.loads(p.read_text())
    assert tick._AUDIT_ENVELOPE_STATE_KEY not in st and len(st["pending_audit"]) == 1
    # next tick re-resolves from the EAMBIG and lands it
    r2 = tick.run_audit()
    assert r2["dispatched"][0]["repo_pr"] == "o/r#1" and r2["envelope_resolved"] is True


def test_a_second_eambig_after_resolution_is_not_retried_forever(home, monkeypatch):
    """A tool that keeps answering EAMBIG even with the id named is a bug on
    the other side; the step refuses once per PR and moves on — bounded at
    two calls per PR (resolve, then the named retry that refuses), and the
    id that refused is not carried out of the tick."""
    p = _state_with_pending(home, "o/r#1", "o/r#2")
    calls = []

    def always_ambiguous(name, inputs):
        calls.append(inputs.get("envelope_id"))
        return {"error": "EAMBIG", "envelopes": _ENVELOPES}

    _patch_client(monkeypatch, always_ambiguous)
    r = tick.run_audit()
    assert r["dispatched"] == [] and len(r["refused"]) == 2
    assert all("EAMBIG" in x["error"] for x in r["refused"])
    assert calls == [None, "env-dispatch-8cabfd667263", None, "env-dispatch-8cabfd667263"]
    assert r["envelope_id"] is None and r["envelope_resolved"] is False
    assert tick._AUDIT_ENVELOPE_STATE_KEY not in json.loads(p.read_text())


def test_a_named_retry_that_refuses_does_not_carry_the_id_out_of_the_tick(home, monkeypatch):
    """One PR, so nothing behind it can clear the id by accident: resolve,
    name it, get refused — the id must be gone at tick end."""
    p = _state_with_pending(home, "o/r#1")
    _patch_client(monkeypatch, lambda n, i: {"error": "EAMBIG", "envelopes": _ENVELOPES})
    r = tick.run_audit()
    assert r["dispatched"] == [] and r["envelope_id"] is None
    assert tick._AUDIT_ENVELOPE_STATE_KEY not in json.loads(p.read_text())


class _BoundsMoved:
    """Planted: Loki 09922563. The remembered envelope's bounds changed (say
    the auditor row was re-issued under a new id): naming the OLD id now
    answers EAMBIG with a bounds mismatch and lists the current rows — the
    branch that used to fall through, keep the stale id, and refuse every
    PR every tick forever."""

    def __init__(self, stale="env-dispatch-8cabfd667263", current="env-dispatch-NEW"):
        self.stale, self.current, self.calls, self._n = stale, current, [], 0
        self.envelopes = [dict(e) for e in _ENVELOPES]
        for e in self.envelopes:
            if e["envelope_id"] == stale:
                e["envelope_id"] = current

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        eid = inputs.get("envelope_id")
        if eid == self.stale:
            return {"error": "EAMBIG: bounds mismatch", "fields": ["task_class"],
                    "envelopes": self.envelopes}
        if not eid:
            return {"error": "EAMBIG", "envelopes": self.envelopes}
        if eid != self.current:
            return {"error": "ENOENT: envelope does not govern dispatch"}
        self._n += 1
        return {"dispatch_id": f"D{self._n}", "status": "pending"}


def test_an_eambig_against_a_remembered_id_forgets_it_and_re_resolves(home, monkeypatch):
    p = _state_with_pending(home, "o/r#1", "o/r#2", envelope_id="env-dispatch-8cabfd667263")
    c = _BoundsMoved()
    _patch_client(monkeypatch, c)
    r = tick.run_audit()
    # PR 1: named the stale id -> EAMBIG bounds mismatch -> forgotten, refused honestly.
    assert r["refused"][0]["repo_pr"] == "o/r#1" and "bounds mismatch" in r["refused"][0]["error"]
    # PR 2, same tick: no id now -> bare EAMBIG -> re-resolved to the current row -> lands.
    assert r["dispatched"] == [{"repo_pr": "o/r#2", "dispatch_id": "D1"}]
    assert r["envelope_id"] == "env-dispatch-NEW" and r["envelope_resolved"] is True
    ids = [inputs.get("envelope_id") for _, inputs in c.calls]
    assert ids == ["env-dispatch-8cabfd667263", None, "env-dispatch-NEW"]
    st = json.loads(p.read_text())
    assert st[tick._AUDIT_ENVELOPE_STATE_KEY] == "env-dispatch-NEW"
    assert [i["repo_pr"] for i in st["pending_audit"]] == ["o/r#1"]
    # next tick the stale-refused PR lands under the remembered current id, first call
    c2 = _BoundsMoved()
    _patch_client(monkeypatch, c2)
    r2 = tick.run_audit()
    assert r2["dispatched"][0]["repo_pr"] == "o/r#1" and len(c2.calls) == 1
    assert c2.calls[0][1]["envelope_id"] == "env-dispatch-NEW"


def test_a_lone_pr_refused_on_a_stale_id_is_re_resolved_next_tick_not_never(home, monkeypatch):
    """The liveness half with nothing behind it in the queue: one PR, stale
    id, EAMBIG — the id must not survive the tick."""
    p = _state_with_pending(home, "o/r#1", envelope_id="env-dispatch-8cabfd667263")
    _patch_client(monkeypatch, _BoundsMoved())
    r = tick.run_audit()
    assert r["dispatched"] == [] and r["envelope_id"] is None
    assert tick._AUDIT_ENVELOPE_STATE_KEY not in json.loads(p.read_text())
    _patch_client(monkeypatch, _BoundsMoved())
    r2 = tick.run_audit()
    assert r2["dispatched"][0]["repo_pr"] == "o/r#1" and r2["envelope_resolved"] is True


def test_heartbeat_mirrors_each_halfs_own_state(home, monkeypatch):
    """A None half and an upstream rename must read differently: the half's
    `state` rides beside its numbers, so `tasks.state` present with no
    `tasks.held` is a rename, and both absent is a blind half."""
    reply = {"event": "net_authority_tick", "at": "t", "state": "populated",
             "tasks": {"state": "populated", "held": 2, "counts": {}, "truncated": False},
             "leases": None}
    _heartbeat_with(monkeypatch, lambda n: reply if n == "net_authority_drain" else {"ok": True})
    e = _tool_entry(heartbeat.run_heartbeat(), "net_authority_drain")
    assert e["tasks.state"] == "populated" and e["tasks.held"] == 2
    assert "leases.state" not in e and "leases.requests" not in e
