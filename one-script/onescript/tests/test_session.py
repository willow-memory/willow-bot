"""One seal per session, at its end (`onescript.session`, `Config.seal_at_end`).

Each guard has a test that attempts what it forbids: serving a past session
nobody sealed, serving a denied one, a seal signed by a key the keyring doesn't
hold, sealing a session this box never had, and the old gate (no seal, nothing
served) when the mode is off."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, record, session  # noqa: E402
from onescript.tests.test_api import (  # noqa: E402,F401
    CAP,
    Ed25519PrivateKey,
    cfg,
    needs_ed25519,
    nestor_pair,
    nestor_store,
    rows,
)
from onescript.tests.test_forge_bite import Entry, fake_forge  # noqa: E402


@pytest.fixture
def at_end(cfg):  # noqa: F811
    return replace(cfg, seal_at_end=True)


def desk_rows_served(c) -> int:
    card = api.scope(c, match={"who": "desk"})
    doc = api.serve(c, card["spec"], max_chars=CAP)["doc"]
    return sum(len(t["rows"]) for t in doc["tables"]), doc


def one_session(c, bite: str) -> dict:
    """Check in, take one turn, check out (with no Nestor store, so the pair is
    not proposed and the seal comes from the test's own store)."""
    assert api.checkin(c)["code"] == 0
    api.turn(c, bite)
    return api.checkout(replace(c, nestor_db=None))["session"]


# ── during the session: nothing waits on a seal ─────────────────────────────
def test_an_unsealed_scope_serves_this_session_marked_so(at_end):
    assert api.checkin(at_end)["code"] == 0
    api.turn(at_end, "alpha")
    n, doc = desk_rows_served(at_end)
    assert doc["state"] == "populated" and doc["seal"] == "unsealed: this session"
    assert n > 0
    assert all(t["trust"] == "untrusted" for t in doc["tables"])


def test_with_the_mode_off_an_unsealed_scope_serves_nothing(cfg):  # noqa: F811
    assert api.checkin(cfg)["code"] == 0
    api.turn(cfg, "alpha")
    n, doc = desk_rows_served(cfg)
    assert (doc["state"], n) == ("empty", 0) and "no human seal" in doc["why"]
    assert "seal" not in doc


# ── check-out: one bundle, one proposal ─────────────────────────────────────
def test_checkout_writes_the_bundle_and_one_session_row(at_end):
    got = one_session(at_end, "alpha")
    (row,) = [r for r in rows(at_end) if r["kind"] == "session"]
    data = (at_end.box / row["where"]).read_bytes()
    assert row["subject"] == got["subject"] == session.subject(data)
    assert row["bundle_sha256"] == record.h256(data)
    b = json.loads(data)
    assert [a["did"] for a in b["actions"]] == ["checkin", "turn", "checkout"]
    assert b["rows"][0][0] == row["from"] and b["tip"] == b["rows"][-1][2]
    assert got["pair"]["state"] == "unreachable"


def test_checkout_proposes_one_draft_to_a_real_nestor(at_end, tmp_path):
    ns = pytest.importorskip("nestor.session_seal")
    from nestor.sqlite_store import SqliteStore

    c = replace(at_end, nestor_db=tmp_path / "operator" / "real-nestor.db")
    assert api.checkin(c)["code"] == 0
    got = api.checkout(c)["session"]
    assert got["pair"]["state"] == "proposed" and got["pair"]["status"] == "draft"
    store = SqliteStore(str(c.nestor_db))
    pair = store.memory_get(got["pair"]["id"])
    assert pair["target_text"] == got["subject"] and pair["source_lang"] == ns.DOMAIN
    assert ns.view(pair["id"], store=store)["state"] == "verified"


# ── the next session: what serves of the last ───────────────────────────────
def test_an_unanswered_session_is_never_served(at_end):
    one_session(at_end, "alpha")
    assert api.checkin(at_end)["report"]["sessions"]["waiting"] == 1
    api.turn(at_end, "beta")
    mine, _ = desk_rows_served(at_end)
    assert mine > 0
    alpha = [r for r in rows(at_end) if r["who"] == "desk"]
    assert len(alpha) == 2 * mine  # both sessions on the record, one served


@needs_ed25519
def test_a_sealed_session_is_read_back_and_served(at_end):
    subj = one_session(at_end, "alpha")["subject"]
    nestor_store(at_end, nestor_pair(subj))
    rep = api.checkin(at_end)["report"]
    assert rep["sessions"] == {"sealed": 1, "waiting": 0}
    (seal,) = [r for r in rows(at_end) if r["kind"] == "seal"]
    assert (seal["subject"], seal["via"], seal["who"]) == (subj, "nestor", "human")
    api.turn(at_end, "beta")
    both, _ = desk_rows_served(at_end)
    assert both == len([r for r in rows(at_end) if r["who"] == "desk"])


@needs_ed25519
def test_a_denied_session_stays_on_the_record_and_is_never_served(at_end):
    subj = one_session(at_end, "alpha")["subject"]
    nestor_store(at_end, nestor_pair(subj, status="rejected"))
    assert api.checkin(at_end)["report"]["sessions"]["waiting"] == 1
    api.turn(at_end, "beta")
    mine, _ = desk_rows_served(at_end)
    on_record = [r for r in rows(at_end) if r["who"] == "desk"]
    assert len(on_record) == 2 * mine
    assert not [r for r in rows(at_end) if r["kind"] == "seal"]


@needs_ed25519
def test_a_seal_by_a_key_the_keyring_doesnt_hold_seals_nothing(at_end):
    subj = one_session(at_end, "alpha")["subject"]
    nestor_store(at_end, nestor_pair(subj, priv=Ed25519PrivateKey.generate()))
    assert api.checkin(at_end)["report"]["sessions"]["waiting"] == 1
    assert not [r for r in rows(at_end) if r["kind"] == "seal"]


def test_a_session_this_box_never_had_cant_be_sealed(at_end):
    one_session(at_end, "alpha")
    assert api.checkin(at_end)["code"] == 0
    got = api.seal_session(at_end, "session:" + "ab" * 32)
    assert (
        got["sealed"] is False and got["row"]["reason"] == "not a session of this box"
    )


def test_rows_before_any_checkin_are_never_servable():
    ok = session.servable(
        [{"n": 0, "kind": "pin"}, {"n": 1, "kind": "invocation", "cmd": "checkin"}],
        set(),
    )
    assert (ok(0), ok(1), ok(5)) == (False, True, True)


# ── the Forge: card answers are drafts ──────────────────────────────────────
def test_the_forge_is_asked_to_defer_its_seals(cfg, monkeypatch):  # noqa: F811
    from onescript.tests.test_store import BITE, DOCS
    from onescript.tests.test_seed import index_into_box
    from onescript.tests.test_xref import make

    seen = []

    def behave(e, responder, EntryError):
        return Entry(major="game")

    mod = fake_forge(monkeypatch, behave)

    def open_bite(
        sentence, *, project_id, builder_id, responder, box, defer_seal=False, **kw
    ):
        seen.append(defer_seal)
        return Entry(major="game")

    mod.open_bite = open_bite
    index_into_box(cfg, make(cfg.box.parent, "docs", DOCS))
    for c in (cfg, replace(cfg, seal_at_end=True)):
        assert api.checkin(c)["code"] == 0
        assert api.forge_bite(c, BITE)["state"] == "populated"
    assert seen == [False, True]


def test_the_suite_never_sees_the_operators_nestor(tmp_path, monkeypatch, capsys):
    """Run under the tests gate on the operator's box, the suite inherits their
    $WILLOW_NESTOR_DB; a CLI check-out here must not propose to it."""
    import os

    from onescript import __main__ as cli
    from onescript.tests.conftest import NESTOR_ENV

    assert not any(os.environ.get(n) for n in NESTOR_ENV)
    assert cli.api.default_nestor_db() is None


def test_a_tests_undo_cant_bring_the_operators_nestor_back(monkeypatch):
    import os

    from onescript.tests.conftest import NESTOR_ENV

    monkeypatch.setattr(os, "sep", os.sep)  # anything, so undo has work to do
    monkeypatch.undo()
    assert not any(os.environ.get(n) for n in NESTOR_ENV)
