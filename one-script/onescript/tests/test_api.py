"""api: the one script as function calls, end to end, no terminal.

check-in -> scope -> seal -> serve -> the model's proposals through a turn ->
the human's seal over one -> only that one written -> check-out.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, gate, nestor_seal  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
CAP = 10**6  # the caller's cap on served text; serve needs one

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
except ImportError:  # CI installs it; the ed25519 tests below skip without it
    Ed25519PrivateKey = None

needs_ed25519 = pytest.mark.skipif(
    Ed25519PrivateKey is None, reason="cryptography is not installed"
)
# the operator's key: its public half is in the keyring file, the private half
# is in Nestor's UI; tests hold it only to play the human sealing a pair
OPERATOR = Ed25519PrivateKey.generate() if Ed25519PrivateKey else None


def pub(priv) -> str:
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


@pytest.fixture
def cfg(tmp_path):
    """A box with no ruff, no clone and no pytest: the phone."""
    grove = tmp_path / "grove"
    (grove / "governance").mkdir(parents=True)
    (grove / "governance" / "CONSTITUTION.md").write_text("CONST-III CONST-VI")
    ci = grove / "ci.yml"
    ci.write_text("pip install ruff==0.16.7")
    keys = tmp_path / "keys" / "keys.json"
    keys.parent.mkdir(mode=0o700)
    keys.write_text(json.dumps({"desk": "aa" * 32, "human": HUMAN_KEY.hex()}))
    keys.chmod(0o600)
    ring = tmp_path / "operator" / "verifiers.json"
    ring.parent.mkdir()
    if OPERATOR is not None:
        entry = {"name": "sean campbell", "key": pub(OPERATOR), "kind": "ed25519"}
        ring.write_text(json.dumps({"verifiers": [entry]}))
    return api.Config(
        box=tmp_path / "box",
        keys=keys,
        constitution=grove / "governance" / "CONSTITUTION.md",
        ci=ci,
        root=tmp_path / "no-clone-here",
        grove=grove,
        venv=tmp_path / "no-venv",
        now="2026-10-07T00:00:00Z",
        no_tests=True,
        keyring=ring,
        nestor_db=tmp_path / "operator" / "nestor.db",
        phone=True,
    )


def rows(cfg) -> list[dict]:
    text = (cfg.box / "record.jsonl").read_text()
    return [json.loads(x) for x in text.splitlines()]


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def line(path="notes/a.md", data="hello\n", cites=("x",), claim="a note") -> str:
    return json.dumps(
        {"path": path, "data": data, "cites": list(cites), "claim": claim}
    )


def served(cfg) -> tuple[dict, list[str]]:
    """Check in, propose a scope, seal it, serve it. The card and the served ids."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    assert card["code"] == 0 and card["subject"].startswith("serve:")
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "populated", res
    return card, [t["id"] for t in res["doc"]["tables"]]


def test_the_whole_path_by_function_calls(cfg):
    card, ids = served(cfg)
    assert card["stack"] and card["stack"][0]["name"] == "who=run"
    res = api.take_proposals(
        cfg, [line(cites=ids[:1]), line("notes/b.md", "other", ids[:1], "another")]
    )
    props = res["out"]["proposals"]
    assert [p["verdict"] for p in props] == ["pass", "pass"]
    assert not (cfg.box / "notes").exists()  # judged, nothing written

    sealed = api.seal_proposal(
        cfg, props[0]["subject"], proof=proof(props[0]["subject"])
    )
    assert sealed["sealed"] and sealed["written"]["path"] == "notes/a.md"
    assert (cfg.box / "notes" / "a.md").read_text() == "hello\n"
    assert not (cfg.box / "notes" / "b.md").exists()  # only the sealed one

    out = api.checkout(cfg)
    assert out["code"] == 0 and "NEEDS YOU" in out["screen"]
    from onescript import record

    assert record.Record(cfg.box, lambda: "", "", gate.token()).verify_chain() == []


def test_a_cite_outside_the_served_file_is_link_fail(cfg):
    _, ids = served(cfg)
    ghost = "f" * 64
    res = api.take_proposals(cfg, [line(cites=[ids[0], ghost])])
    (p,) = res["out"]["proposals"]
    assert p["verdict"] == "link_fail" and p["link_fail"][0]["cite"] == ghost
    # a link_fail is not sealable, and a seal over it writes nothing
    row = api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    assert row["sealed"] is False and "link_fail" in row["row"]["reason"]
    assert not (cfg.box / "notes").exists()


def test_with_nothing_served_every_cite_fails(cfg):
    assert api.checkin(cfg)["code"] == 0
    (p,) = api.take_proposals(cfg, [line(cites=["a" * 64])])["out"]["proposals"]
    assert p["verdict"] == "link_fail"


def test_a_non_ascii_cite_is_a_link_fail_not_a_crash(cfg):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line(cites=["é퟿✓"])])["out"]["proposals"]
    assert p["verdict"] == "link_fail"


def test_an_unsealed_proposal_is_never_written(cfg):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    assert p["verdict"] == "pass"
    # a wrong proof: refused, nothing written
    bad = api.seal_proposal(cfg, p["subject"], proof="00" * 32)
    assert bad["sealed"] is False and bad["written"] is None
    # the write itself reads the seal from the record, not from the caller
    run, _ = api._open(cfg, "probe", ())
    refused = run.write_proposal(p["subject"])
    assert refused["kind"] == "refused" and "no human seal" in refused["reason"]
    assert not (cfg.box / "notes").exists()
    assert not [
        r for r in rows(cfg) if r["kind"] == "write" and r["where"] == "notes/a.md"
    ]


def test_a_seal_over_one_proposal_covers_no_other(cfg):
    _, ids = served(cfg)
    a, b = api.take_proposals(
        cfg, [line(cites=ids[:1]), line(data="changed", cites=ids[:1])]
    )["out"]["proposals"]
    assert a["subject"] != b["subject"]  # the bytes are part of the subject
    api.seal_proposal(cfg, a["subject"], proof=proof(a["subject"]))
    assert (cfg.box / "notes" / "a.md").read_text() == "hello\n"
    run, _ = api._open(cfg, "probe", ())
    assert run.write_proposal(b["subject"])["kind"] == "refused"


def test_a_scope_seal_is_not_a_proposal_seal(cfg):
    card, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    swapped = api.seal_scope(cfg, p["subject"], proof=proof(p["subject"]))
    assert (
        swapped["sealed"] is False and "not a scope subject" in swapped["row"]["reason"]
    )


@pytest.mark.parametrize(
    "bad, why",
    [
        ("{half a row", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        ('"just text"', "JSON object"),
        ('{"path": "a"}', "keys must be exactly"),
        (line()[:-1] + ', "extra": 1}', "keys must be exactly"),
        (
            json.dumps({"path": 1, "data": "", "cites": [], "claim": "c"}),
            "must be text",
        ),
        (
            json.dumps({"path": "a", "data": "", "cites": "x", "claim": "c"}),
            "list of served",
        ),
        (
            json.dumps({"path": "a", "data": "", "cites": [1], "claim": "c"}),
            "list of served",
        ),
        (line("../outside.md"), "relative to the box"),
        (line("/etc/passwd"), "relative to the box"),
        (line("a/../../b"), "relative to the box"),
        (line("a//b"), "relative to the box"),
        (line("./a"), "relative to the box"),
        (line("record.jsonl"), "temp or one the run keeps"),
        (line("served.json"), "temp or one the run keeps"),
        (line("tmp/x"), "temp or one the run keeps"),
        (line("a b.md"), "no line, paragraph or other separator"),
        (line("a b.md"), "no line, paragraph or other separator"),
        (line("a b.md"), "no line, paragraph or other separator"),
        (line("a　b.md"), "no line, paragraph or other separator"),
        (line("a /b.md"), "no space at the start or end of a segment"),
        (line("a/ b.md"), "no space at the start or end of a segment"),
        (line("a/b.md /c"), "no space at the start or end of a segment"),
        (line(data="\ud800"), "UTF-8"),
        ("[" * 100000, "not valid JSON"),
    ],
)
def test_a_malformed_proposal_is_refused_into_the_record_never_a_crash(cfg, bad, why):
    _, ids = served(cfg)
    res = api.take_proposals(cfg, [bad, line(cites=ids[:1])])
    refused, good = res["out"]["proposals"]
    assert refused["verdict"] == "refused" and why in refused["reason"]
    assert "path" not in refused  # nothing to act on
    assert good["verdict"] == "pass"  # the rest are still read
    assert refused in [r for r in rows(cfg) if r["kind"] == "proposal"]


def test_an_inner_ascii_space_in_a_name_is_still_a_name(cfg):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line("my notes/a b.md", cites=ids[:1])])["out"][
        "proposals"
    ]
    assert p["verdict"] == "pass"


def test_a_claim_that_says_nothing_is_refused_by_the_door(cfg):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1], claim="  ")])["out"][
        "proposals"
    ]
    assert p["verdict"] == "refused" and "says nothing" in p["reason"]


def test_the_file_of_proposals_is_read_line_by_line(cfg, tmp_path):
    _, ids = served(cfg)
    f = tmp_path / "f.jsonl"
    f.write_text(line(cites=ids[:1]) + "\n\n" + "oops\n")
    res = api.take_proposals(cfg, api.read_proposals(f))
    assert [p["verdict"] for p in res["out"]["proposals"]] == ["pass", "refused"]
    assert [p["line"] for p in res["out"]["proposals"]] == [1, 3]


def test_nothing_moves_before_a_checkin(cfg):
    for res in (
        api.scope(cfg),
        api.take_proposals(cfg, [line()]),
        api.seal_scope(cfg, "serve:x", proof="p"),
        api.serve(cfg, {"by": ["who"], "match": {}, "upto": 0}),
    ):
        assert res["code"] == 1 and "no check-in on record" in res["refused"]


def test_nothing_moves_after_a_checkout(cfg):
    assert api.checkin(cfg)["code"] == 0
    assert api.checkout(cfg)["code"] == 0
    res = api.take_proposals(cfg, [line()])
    assert res["code"] == 1 and "checked out" in res["refused"]


def test_serve_obeys_the_callers_cap_and_never_truncates(cfg):
    card, ids = served(cfg)
    full = api.serve(cfg, card["spec"], max_chars=CAP)["doc"]
    size = len(
        json.dumps(full, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    assert api.serve(cfg, card["spec"], max_chars=size)["state"] == "populated"
    over = api.serve(cfg, card["spec"], max_chars=size - 1)
    assert over["state"] == "empty" and "narrow the stack" in over["why"]
    assert over["doc"]["tables"] == []
    assert api.serve(cfg, card["spec"], max_chars=0)["state"] == "empty"
    on_disk = json.loads((cfg.box / "served.json").read_text())
    assert on_disk["state"] == "empty"


def test_an_unsealed_scope_serves_nothing(cfg):
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, match={"who": "run"})
    res = api.serve(cfg, card["spec"], max_chars=CAP)
    assert res["state"] == "empty" and "no human seal" in res["why"]


def test_serve_without_a_cap_serves_nothing(cfg):
    """Loki 8E0652F5 F9: the caller sizes the cap; no cap is `empty`, with the
    reason, on the record and on disk, never an uncapped document."""
    card, _ = served(cfg)
    res = api.serve(cfg, card["spec"])
    assert res["state"] == "empty" and "no max_chars" in res["why"]
    assert res["doc"]["tables"] == []
    assert json.loads((cfg.box / "served.json").read_text())["state"] == "empty"
    assert api.serve(cfg, card["spec"], max_chars=CAP)["state"] == "populated"


def test_the_serve_key_lives_beside_the_keys_and_is_fresh_each_checkin(cfg):
    card, ids = served(cfg)
    key = cfg.keys.parent / api.SERVE_KEY
    assert oct(key.stat().st_mode & 0o777) == "0o600"
    assert not (cfg.box / api.SERVE_KEY).exists()
    first = key.read_text()
    api.checkin(cfg)
    assert key.read_text() != first  # ids can't be linked from one session to the next
    card2 = api.scope(cfg, match={"who": "run"})
    api.seal_scope(cfg, card2["subject"], proof=proof(card2["subject"]))
    doc = api.serve(cfg, card2["spec"], max_chars=CAP)["doc"]
    assert doc["tables"][0]["id"] not in ids


def test_a_scope_stays_the_same_while_the_record_grows(cfg):
    """The spec pins the stack to the rows before it was proposed, so the rows
    every later command appends can't move a table id out from under a seal."""
    card, _ = served(cfg)
    api.take_proposals(cfg, [line()])
    again = api.serve(cfg, card["spec"], max_chars=CAP)
    assert again["state"] == "populated"


# ── the seal without a terminal ──────────────────────────────────────────────
def nestor_pair(
    subject: str, who="sean campbell", priv=None, **over
) -> dict:  # signed by the operator's key unless `priv` says otherwise
    msg = nestor_seal.message("approve", subject, who)
    p = {
        "source_norm": "approve",
        "target_text": subject,
        "verifier": who,
        "seal_sig": (priv or OPERATOR).sign(msg).hex(),
        "status": "sealed",
    }
    return {**p, **over}


def nestor_store(cfg, *pairs: dict, **over) -> None:
    """Nestor's store as the operator's box holds it, with these pairs in it."""
    con = sqlite3.connect(cfg.nestor_db)
    con.execute(
        "CREATE TABLE IF NOT EXISTS tm_pairs (source_norm TEXT, target_text TEXT,"
        " status TEXT, verifier TEXT, seal_sig TEXT,"
        " superseded_by TEXT NOT NULL DEFAULT '')"
    )
    for p in pairs:
        p = {**p, **over}
        con.execute(
            "INSERT INTO tm_pairs VALUES (?,?,?,?,?,?)",
            (
                p["source_norm"],
                p["target_text"],
                p["status"],
                p["verifier"],
                p["seal_sig"],
                p.get("superseded_by", ""),
            ),
        )
    con.commit()
    con.close()


def human_less(cfg) -> None:
    keys = json.loads(cfg.keys.read_text())
    del keys["human"]  # this box holds no human key at all
    cfg.keys.write_text(json.dumps(keys))


@needs_ed25519
def test_a_scope_and_a_proposal_are_sealed_by_nestor_pairs_no_key_typed(cfg):
    human_less(cfg)
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, match={"who": "run"})
    s = card["subject"]
    assert api.seal_scope(cfg, s, proof=proof(s))["sealed"] is False  # no key here
    nestor_store(cfg, nestor_pair(s))
    ok = api.seal_scope(cfg, s, pair=nestor_pair(s))
    assert ok["sealed"] and ok["row"]["via"] == "nestor"
    assert ok["row"]["verifier"] == "sean campbell" and ok["row"]["who"] == "human"

    doc = api.serve(cfg, card["spec"], max_chars=CAP)["doc"]
    ids = [t["id"] for t in doc["tables"]]
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    wrong = api.seal_proposal(cfg, p["subject"], pair=nestor_pair(s))  # the scope's
    assert wrong["sealed"] is False and "sealed in Nestor" in wrong["row"]["reason"]
    assert not (cfg.box / "notes").exists()
    nestor_store(cfg, nestor_pair(p["subject"]))
    done = api.seal_proposal(cfg, p["subject"])  # found in the store, no pair given
    assert done["sealed"] and (cfg.box / "notes" / "a.md").exists()


@needs_ed25519
def test_a_nestor_seal_that_does_not_verify_seals_nothing(cfg):
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    forger = Ed25519PrivateKey.generate()
    for bad in (
        nestor_pair(s, seal_sig="00" * 64),
        nestor_pair(s, who="stranger"),
        nestor_pair(s, priv=forger),  # the right name, a key the operator never had
    ):
        nestor_store(cfg, bad)
        got = api.seal_scope(cfg, s, pair=bad)
        assert got["sealed"] is False and got["row"]["kind"] == "refused"
    assert not [r for r in rows(cfg) if r["kind"] == "seal"]
    assert api.seal_scope(cfg, s)["sealed"] is False  # none of the store's rows verify


@needs_ed25519
def test_a_key_the_caller_minted_cannot_stand_in_for_the_operators(cfg):
    """Loki 8E0652F5 F1 / probe P2. The keyring is Config's, not the call's: a
    keyring handed to the call is not a parameter at all, and a pair signed by a
    fresh key labelled 'sean campbell' fails against the operator's own."""
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    forger = Ed25519PrivateKey.generate()
    pair = nestor_pair(p["subject"], priv=forger)
    nestor_store(cfg, pair)  # even a pair that is in Nestor's store
    with pytest.raises(TypeError):
        api.seal_proposal(cfg, p["subject"], pair=pair, keyring={"verifiers": {}})
    got = api.seal_proposal(cfg, p["subject"], pair=pair)
    assert got["sealed"] is False and "does not verify" in got["row"]["reason"]
    sealed_it = [
        r for r in rows(cfg) if r["kind"] == "seal" and r["subject"] == p["subject"]
    ]
    assert not sealed_it
    assert not (cfg.box / "notes").exists()


@needs_ed25519
def test_a_pair_the_store_no_longer_holds_as_sealed_is_not_a_seal(cfg):
    """Loki 8E0652F5 F6: a pair handed in as a dict is only a claim. Its current
    row in Nestor's store decides: superseded, rejected or gone seals nothing."""
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    pair = nestor_pair(s)
    gone = api.seal_scope(cfg, s, pair=pair)  # the store does not exist at all
    assert gone["sealed"] is False and "unreachable" in gone["row"]["reason"]
    nestor_store(cfg, pair, superseded_by="newer")
    assert api.seal_scope(cfg, s, pair=pair)["sealed"] is False
    cfg.nestor_db.unlink()
    nestor_store(cfg, pair, status="rejected")
    assert api.seal_scope(cfg, s, pair=pair)["sealed"] is False
    cfg.nestor_db.unlink()
    nestor_store(cfg, pair)
    assert api.seal_scope(cfg, s, pair=pair)["sealed"] is True


@needs_ed25519
def test_a_compromised_operator_key_seals_nothing(cfg):
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    nestor_store(cfg, nestor_pair(s))
    ring = json.loads(cfg.keyring.read_text())
    ring["verifiers"][0].update(
        revoked_at="2026-10-01T00:00:00+00:00", compromised=True
    )
    cfg.keyring.write_text(json.dumps(ring))
    got = api.seal_scope(cfg, s)
    assert got["sealed"] is False and "compromised" in got["row"]["reason"]


@needs_ed25519
def test_a_rotated_operator_key_still_verifies_its_past_seals(cfg):
    """Nestor's semantics: revoked, not compromised, makes no new seals but the
    ones it made stand (nestor/keyring.py, `Keyring.revoke`)."""
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    nestor_store(cfg, nestor_pair(s))
    ring = json.loads(cfg.keyring.read_text())
    ring["verifiers"][0].update(revoked_at="2026-10-01T00:00:00+00:00")
    cfg.keyring.write_text(json.dumps(ring))
    assert api.seal_scope(cfg, s)["sealed"] is True


@needs_ed25519
def test_a_keyring_with_private_material_seals_nothing(cfg):
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    nestor_store(cfg, nestor_pair(s))
    ring = json.loads(cfg.keyring.read_text())
    ring["verifiers"][0]["private_key"] = "ab" * 32
    cfg.keyring.write_text(json.dumps(ring))
    got = api.seal_scope(cfg, s)
    assert got["sealed"] is False and "public keys only" in got["row"]["reason"]


def test_no_keyring_or_store_configured_is_unreachable_not_a_pass(cfg):
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    for bare in (replace(cfg, keyring=None), replace(cfg, nestor_db=None)):
        got = api.seal_scope(bare, s, pair={"status": "sealed"})
        assert got["sealed"] is False
        assert got["row"]["state"] == "unreachable"


@needs_ed25519
def test_without_cryptography_a_nestor_seal_is_unreachable_with_that_reason(
    cfg, monkeypatch
):
    """Loki 8E0652F5 F7: not a silent refusal of every real seal."""
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    nestor_store(cfg, nestor_pair(s))
    monkeypatch.setattr(nestor_seal, "have_ed25519", lambda: False)
    got = api.seal_scope(cfg, s)
    assert got["sealed"] is False
    assert got["row"]["state"] == "unreachable"
    assert "cryptography" in got["row"]["reason"]


def test_the_keyring_and_the_store_cannot_live_in_the_box(cfg):
    for field in ("keyring", "nestor_db"):
        inside = replace(cfg, **{field: cfg.box / "x.json"})
        assert "can't live inside the box" in api.checkin(inside)["refused"]


# ── the stored row is not the proposal (Loki 8E0652F5 F2) ───────────────────
def forge_row(cfg, **fields) -> dict:
    """What anyone who can write the box's record can do: append a row that
    chains and carries no token the run issued."""
    from onescript import record

    rs = rows(cfg)
    row = {
        "n": len(rs),
        "kind": None,
        "who": "desk",
        "family": "claude",
        "standing": "unattested",
        "version": rs[-1]["version"],
        "ts": "2026-10-07T00:00:00Z",
        "prev": rs[-1]["hash"],
        "where": None,
        **fields,
    }
    row["hash"] = record.h256(record.canon(row))
    with (cfg.box / "record.jsonl").open("a") as f:
        f.write(record.canon(row) + "\n")
    return row


def test_a_forged_proposal_row_with_a_harmless_hash_writes_nothing(cfg, tmp_path):
    """Probe P1: a benign subject, a forged row carrying '../ESCAPED.txt' and other
    bytes. The seal covers the benign hash, not these bytes."""
    _, ids = served(cfg)
    (good,) = api.take_proposals(cfg, [line("ok.txt", "hello", ids[:1])])["out"][
        "proposals"
    ]
    forge_row(
        cfg,
        kind="proposal",
        where="../ESCAPED.txt",
        path="../ESCAPED.txt",
        data="EVIL",
        cites=ids[:1],
        claim="a note",
        subject=good["subject"],
        verdict="pass",
    )
    # the forged row is the latest for the subject; it still must not be used
    res = api.seal_proposal(cfg, good["subject"], proof=proof(good["subject"]))
    assert not (tmp_path / "ESCAPED.txt").exists()
    assert (cfg.box / "ok.txt").read_text() == "hello"  # the real one, its own bytes
    assert res["written"]["path"] == "ok.txt"


def test_a_forged_row_alone_hashes_to_nothing_and_is_never_sealed_or_written(
    cfg, tmp_path
):
    _, ids = served(cfg)
    subject = "proposal:" + "ab" * 32
    forge_row(
        cfg,
        kind="proposal",
        where="../ESCAPED.txt",
        path="../ESCAPED.txt",
        data="EVIL",
        cites=ids[:1],
        claim="c",
        subject=subject,
        verdict="pass",
    )
    res = api.seal_proposal(cfg, subject, proof=proof(subject))
    assert res["sealed"] is False and res["written"] is None
    assert "hashes to this subject" in res["row"]["reason"]
    # even with a seal already on the record (a human sealed this hash), the
    # write recomputes from the stored bytes and writes nothing
    forge_row(cfg, kind="seal", who="human", subject=subject, where=subject)
    run, _ = api._open(cfg, "probe", ())
    out = run.write_proposal(subject)
    assert out["kind"] == "refused" and "hashes to this subject" in out["reason"]
    assert not (tmp_path / "ESCAPED.txt").exists()
    assert not [r for r in rows(cfg) if r["kind"] == "write" and "ESCAPED" in r["path"]]


def test_a_stored_row_that_no_longer_passes_the_path_rule_or_the_door_writes_nothing(
    cfg,
):
    """The subject is a real hash of a real row, but the row's path breaks the
    rule (a row from an older, laxer run). The write re-runs the rule and the
    door on the bytes about to be written."""
    from onescript import proposals

    _, ids = served(cfg)
    for path, claim in (("../up.txt", "a note"), ("fine.txt", "   ")):
        p = {"path": path, "data": "x", "cites": ids[:1], "claim": claim}
        subject = proposals.subject(p)
        forge_row(
            cfg, kind="proposal", subject=subject, verdict="pass", where=path, **p
        )
        forge_row(cfg, kind="seal", who="human", subject=subject, where=subject)
        run, _ = api._open(cfg, "probe", ())
        out = run.write_proposal(subject)
        assert out["kind"] == "refused", path
        assert (
            "fails the contract now" in out["reason"]
            or "the door says" in out["reason"]
        )
    assert not (cfg.box / "fine.txt").exists()
    assert not (cfg.box.parent / "up.txt").exists()


# ── a symlink in the box is not a way out of it (F3) ────────────────────────
def test_a_symlink_in_the_box_does_not_carry_a_write_outside(cfg, tmp_path):
    """Probe P4: box/link -> outside; a sealed proposal at link/pwn.txt."""
    _, ids = served(cfg)
    outside = tmp_path / "outside"
    outside.mkdir()
    (cfg.box / "link").symlink_to(outside)
    (p,) = api.take_proposals(cfg, [line("link/pwn.txt", "S", ids[:1])])["out"][
        "proposals"
    ]
    res = api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    assert res["written"] is None and res["code"] == 1
    assert not (outside / "pwn.txt").exists() and not list(outside.iterdir())
    assert "symlink" in res["write"]["reason"]


def test_a_symlinked_file_is_replaced_not_followed(cfg, tmp_path):
    _, ids = served(cfg)
    target = tmp_path / "target.txt"
    target.write_text("keep")
    (cfg.box / "notes").mkdir()
    (cfg.box / "notes" / "a.md").symlink_to(target)
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    assert target.read_text() == "keep"
    assert not (cfg.box / "notes" / "a.md").is_symlink()


# ── a path is judged as written (F4) ─────────────────────────────────────────
@pytest.mark.parametrize(
    "path",
    [
        " /etc/passwd",
        "\t/abs",
        " lead.txt",
        "trail.txt ",
        "~/.bashrc",
        "a~b",
        "%2e%2e/x",
        "a\nb",
        "a\x1b[2Jb",
        "‮exe.txt",  # right-to-left override
        "a​b",  # zero-width space (format character)
        "a\\b",
        " ",
        "x" * 300,
    ],
)
def test_a_path_that_is_not_plain_relative_posix_is_refused(cfg, path):
    _, ids = served(cfg)
    (p,) = api.take_proposals(cfg, [line(path, "x", ids[:1])])["out"]["proposals"]
    assert p["verdict"] == "refused" and "path" in p["reason"]
    assert "subject" not in p


# ── the CLI is the same path ─────────────────────────────────────────────────
def test_the_cli_walks_the_same_path(cfg, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "ROOT", cfg.root)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    base = [
        "--box", str(cfg.box), "--keys", str(cfg.keys), "--venv", str(cfg.venv),
        "--now", cfg.now, "--no-tests", "--phone",
    ]  # fmt: skip

    def go(*cmd) -> tuple[int, str]:
        rc = cli.main([*base, *cmd])
        return rc, capsys.readouterr().out

    def js(*cmd) -> dict:
        rc, out = go(*cmd)
        assert rc == 0, out
        return json.loads(out)

    assert go("checkin")[0] == 0
    card = js("scope", "--by", "who", "--match", "who=run")
    spec = card["spec"]
    sealed = js("seal", card["subject"], "--proof", proof(card["subject"]))
    assert sealed["sealed"] is True
    flags = ["--by", "who", "--match", "who=run", "--upto", str(spec["upto"])]
    rc, out = go("serve", *flags)  # no cap: nothing is served uncapped
    assert rc == 1 and "--max-chars" in out
    assert js("serve", *flags, "--max-chars", str(CAP))["state"] == "populated"
    ids = [t["id"] for t in json.loads((cfg.box / "served.json").read_text())["tables"]]

    f = tmp_path / "f.jsonl"
    f.write_text(line(cites=ids) + "\n" + line("x.md", "no", ["0" * 64]) + "\nnope\n")
    out = js("turn", "the bite", "--proposal", str(f))
    assert [p["verdict"] for p in out["proposals"]] == ["pass", "link_fail", "refused"]
    subject = out["proposals"][0]["subject"]

    refused = go("seal", subject, "--proof", "00" * 32)
    assert refused[0] == 1 and not (cfg.box / "notes").exists()
    done = js("seal", subject, "--proof", proof(subject))
    assert done["sealed"] and (cfg.box / "notes" / "a.md").read_text() == "hello\n"
    assert go("checkout")[0] == 0


def test_the_cli_serve_wants_the_row_the_scope_named(cfg, capsys, monkeypatch):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests"]
    cli.main([*base, "checkin"])
    capsys.readouterr()
    assert cli.main([*base, "serve"]) == 1
    assert "--upto" in capsys.readouterr().out


# ── the model's own ideas, marked by code (operator, 2026-10-07) ────────────
def test_an_idea_of_its_own_is_marked_by_code_and_leads_the_card(cfg):
    _, ids = served(cfg)
    own, cited = api.take_proposals(
        cfg, [line(cites=[]), line("notes/b.md", "other", ids[:1], "another")]
    )["out"]["proposals"]
    assert own["verdict"] == "pass" and own["own_idea"] is True
    assert next(iter(own["card"])) == "own_idea"  # the card leads with the marker
    assert cited["own_idea"] is False and "own_idea" not in cited["card"]
    stored = [r for r in rows(cfg) if r["kind"] == "proposal"]
    assert [r["own_idea"] for r in stored] == [True, False]


def test_the_model_cannot_mark_its_own_idea(cfg):
    """Code sets `own_idea`; a row that brings the key is outside the contract."""
    served(cfg)
    bad = json.loads(line(cites=[]))
    bad["own_idea"] = False
    (p,) = api.take_proposals(cfg, [json.dumps(bad)])["out"]["proposals"]
    assert p["verdict"] == "refused" and "keys must be exactly" in p["reason"]


def test_an_own_idea_is_sealed_and_written_like_any_proposal(cfg):
    served(cfg)
    (p,) = api.take_proposals(cfg, [line(cites=[])])["out"]["proposals"]
    done = api.seal_proposal(cfg, p["subject"], proof=proof(p["subject"]))
    assert done["sealed"] and (cfg.box / "notes" / "a.md").exists()


def test_an_own_idea_with_nothing_served_is_refused(cfg):
    assert api.checkin(cfg)["code"] == 0  # nothing served
    (p,) = api.take_proposals(cfg, [line(cites=[])])["out"]["proposals"]
    assert p["verdict"] == "refused" and "populated" in p["reason"]
    assert "path" not in p  # nothing to act on


def test_an_own_idea_beside_an_empty_served_state_is_refused(cfg):
    card, _ = served(cfg)
    empty = api.serve(cfg, card["spec"], max_chars=1)  # too big for the cap
    assert empty["state"] == "empty"
    (p,) = api.take_proposals(cfg, [line(cites=[])])["out"]["proposals"]
    assert p["verdict"] == "refused" and "populated" in p["reason"]
    (q,) = api.take_proposals(cfg, [line(cites=["a" * 64])])["out"]["proposals"]
    assert q["verdict"] == "link_fail"  # a cited row keeps the served-id check


def test_the_served_return_line_allows_an_idea_of_its_own(cfg):
    card, _ = served(cfg)
    doc = json.loads((cfg.box / "served.json").read_text())
    assert '"cites": []' in doc["return"] and "marked as yours" in doc["return"]


# ── served state is this check-in's, from the record (Loki 46C49FF9 R2) ────
def test_a_stale_served_file_from_an_earlier_checkin_is_not_populated(cfg):
    _, ids = served(cfg)
    assert api.checkout(cfg)["code"] == 0
    assert api.checkin(cfg)["code"] == 0  # a new session; served.json still sits there
    assert (cfg.box / "served.json").exists()
    (own,) = api.take_proposals(cfg, [line(cites=[])])["out"]["proposals"]
    assert own["verdict"] == "refused" and "populated" in own["reason"]
    (old,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    assert old["verdict"] == "link_fail"


def test_a_hand_written_served_file_is_not_populated(cfg):
    assert api.checkin(cfg)["code"] == 0
    (cfg.box / "served.json").write_text(
        json.dumps({"state": "populated", "tables": [{"id": "ab" * 32}]})
    )
    (own,) = api.take_proposals(cfg, [line(cites=[])])["out"]["proposals"]
    assert own["verdict"] == "refused"
    (c,) = api.take_proposals(cfg, [line(cites=["ab" * 32])])["out"]["proposals"]
    assert c["verdict"] == "link_fail"


def test_an_edited_served_file_no_longer_matches_its_serve_row(cfg):
    _, ids = served(cfg)
    f = cfg.box / "served.json"
    doc = json.loads(f.read_text())
    doc["tables"].append({"id": "cd" * 32})
    f.write_text(json.dumps(doc))  # same session, but not the bytes that were served
    (own,) = api.take_proposals(cfg, [line(cites=[])])["out"]["proposals"]
    assert own["verdict"] == "refused"
    (c,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    assert c["verdict"] == "link_fail"


def test_a_serve_this_checkin_makes_own_ideas_and_cites_good_again(cfg):
    served(cfg)
    assert api.checkout(cfg)["code"] == 0
    card, ids = served(cfg)  # check in again and serve again
    (own, cited) = api.take_proposals(
        cfg, [line(cites=[]), line("n/b.md", "x", ids[:1], "c")]
    )["out"]["proposals"]
    assert own["verdict"] == "pass" and cited["verdict"] == "pass"


# ── own_idea leads each proposal row, on the screen and the CLI ─────────────
def test_the_checkout_screen_leads_each_proposal_row_with_own_idea(cfg):
    _, ids = served(cfg)
    api.take_proposals(cfg, [line(cites=[]), line("n/b.md", "x", ids[:1], "c")])
    api.take_proposals(cfg, ["not json"])
    screen = api.checkout(cfg)["screen"]
    rows_ = [x for x in screen.splitlines() if "proposal ·" in x]
    assert len(rows_) == 3
    assert rows_[0].split("proposal · ")[1].startswith("own_idea: yes")
    assert rows_[1].split("proposal · ")[1].startswith("own_idea: no")
    assert rows_[2].split("proposal · ")[1].startswith("own_idea: n/a")


def test_the_cli_turn_output_leads_each_proposal_row_with_own_idea(
    cfg, capsys, monkeypatch, tmp_path
):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    served(cfg)
    capsys.readouterr()
    f = tmp_path / "f.jsonl"
    f.write_text(line(cites=[]) + "\nnope\n")
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests"]
    assert cli.main([*base, "turn", "the bite", "--proposal", str(f)]) == 0
    props = json.loads(capsys.readouterr().out)["proposals"]
    assert [next(iter(p)) for p in props] == ["own_idea", "own_idea"]
    assert [p["own_idea"] for p in props] == [True, None]


# ── the Nestor store and the keyring defaults ───────────────────────────────
def test_the_store_is_willow_nestor_db_else_nestor_db(monkeypatch, tmp_path):
    monkeypatch.delenv("WILLOW_NESTOR_DB", raising=False)
    monkeypatch.delenv("NESTOR_DB", raising=False)
    assert api.default_nestor_db() is None
    monkeypatch.setenv("NESTOR_DB", str(tmp_path / "n.db"))
    assert api.default_nestor_db() == tmp_path / "n.db"
    monkeypatch.setenv("WILLOW_NESTOR_DB", str(tmp_path / "w.db"))
    assert api.default_nestor_db() == tmp_path / "w.db"  # the broker's name wins


def test_neither_store_variable_is_unreachable_with_that_reason(cfg, monkeypatch):
    monkeypatch.delenv("WILLOW_NESTOR_DB", raising=False)
    monkeypatch.delenv("NESTOR_DB", raising=False)
    bare = replace(cfg, nestor_db=api.default_nestor_db())
    assert api.checkin(bare)["code"] == 0
    s = api.scope(bare, match={"who": "run"})["subject"]
    got = api.seal_scope(bare, s, pair={"status": "sealed"})
    assert got["sealed"] is False and got["row"]["state"] == "unreachable"
    assert "$WILLOW_NESTOR_DB or $NESTOR_DB" in got["row"]["reason"]


def test_the_default_keyring_is_the_public_export_never_the_signing_file(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("WILLOW_KEYRING", raising=False)
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    got = api.default_keyring()
    assert got == tmp_path / "config" / "verifiers.public.json"
    assert got.name != "verifiers.json"
    monkeypatch.delenv("WILLOW_HOME")
    assert api.default_keyring() is None


# ── keys export, off the box ────────────────────────────────────────────────
@needs_ed25519
def test_the_cli_exports_the_public_half_and_leaves_the_source_alone(tmp_path, capsys):
    priv_hex = OPERATOR.private_bytes_raw().hex()
    src = tmp_path / "verifiers.json"
    src.write_text(
        json.dumps(
            {
                "verifiers": [
                    {
                        "name": "sean campbell",
                        "key": pub(OPERATOR),
                        "kind": "ed25519",
                        "private": priv_hex,
                    },
                    {"name": "shared", "key": "5e" * 32, "kind": "hmac"},
                ]
            }
        )
    )
    before = src.read_bytes()
    dst = tmp_path / "config" / "verifiers.public.json"
    assert cli.main(["keys", "export", "--from", str(src), "--to", str(dst)]) == 0
    out = capsys.readouterr().out
    assert "kept     sean campbell" in out and "dropped  shared" in out
    assert priv_hex not in dst.read_text() and "5e" * 32 not in dst.read_text()
    assert src.read_bytes() == before
    assert not (tmp_path / "box").exists()  # no record, no invocation row
    assert (
        cli.main(["keys", "export", "--from", str(tmp_path / "nope"), "--to", str(dst)])
        == 2
    )
    assert "refused" in capsys.readouterr().out
