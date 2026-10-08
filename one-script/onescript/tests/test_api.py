"""api: the one script as function calls, end to end, no terminal.

check-in -> scope -> seal -> serve -> the model's proposals through a turn ->
the human's seal over one -> only that one written -> check-out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, gate, nestor_seal  # noqa: E402

HUMAN_KEY = b"passkey-in-a-coat-pocket"
NESTOR_KEY = b"nestor-keyring-hmac-key-0123456"


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
    )


def rows(cfg) -> list[dict]:
    text = (cfg.box / "record.jsonl").read_text()
    return [json.loads(x) for x in text.splitlines()]


def proof(subject: str) -> str:
    return gate.sign(HUMAN_KEY, "seal", subject)


def line(path="notes/a.md", data="hello\n", cites=(), claim="a note") -> str:
    return json.dumps(
        {"path": path, "data": data, "cites": list(cites), "claim": claim}
    )


def served(cfg) -> tuple[dict, list[str]]:
    """Check in, propose a scope, seal it, serve it. The card and the served ids."""
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, by=("who",), match={"who": "run"})
    assert card["code"] == 0 and card["subject"].startswith("serve:")
    assert api.seal_scope(cfg, card["subject"], proof=proof(card["subject"]))["sealed"]
    res = api.serve(cfg, card["spec"])
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
        (line("record.jsonl"), "temp or one the run keeps"),
        (line("served.json"), "temp or one the run keeps"),
        (line("tmp/x"), "temp or one the run keeps"),
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
    full = api.serve(cfg, card["spec"])["doc"]
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
    res = api.serve(cfg, card["spec"])
    assert res["state"] == "empty" and "no human seal" in res["why"]


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
    assert api.serve(cfg, card2["spec"])["doc"]["tables"][0]["id"] not in ids


def test_a_scope_stays_the_same_while_the_record_grows(cfg):
    """The spec pins the stack to the rows before it was proposed, so the rows
    every later command appends can't move a table id out from under a seal."""
    card, _ = served(cfg)
    api.take_proposals(cfg, [line()])
    again = api.serve(cfg, card["spec"])
    assert again["state"] == "populated"


# ── the seal without a terminal ──────────────────────────────────────────────
def nestor_pair(subject: str, who="sean campbell", **over) -> dict:
    msg = nestor_seal.message("approve", subject, who)
    sig = hmac.new(NESTOR_KEY, msg, hashlib.sha256).hexdigest()
    p = {
        "source_norm": "approve",
        "target_text": subject,
        "verifier": who,
        "seal_sig": sig,
        "status": "sealed",
    }
    return {**p, **over}


RING = {
    "verifiers": {
        "sean campbell": {"key": NESTOR_KEY, "kind": "hmac", "compromised": False}
    }
}


def test_a_scope_and_a_proposal_are_sealed_by_nestor_pairs_no_key_typed(cfg, tmp_path):
    keys = json.loads(cfg.keys.read_text())
    del keys["human"]  # this box holds no human key at all
    cfg.keys.write_text(json.dumps(keys))
    assert api.checkin(cfg)["code"] == 0
    card = api.scope(cfg, match={"who": "run"})
    s = card["subject"]
    assert api.seal_scope(cfg, s, proof=proof(s))["sealed"] is False  # no key here
    ok = api.seal_scope(cfg, s, pair=nestor_pair(s), keyring=RING)
    assert ok["sealed"] and ok["row"]["via"] == "nestor"
    assert ok["row"]["verifier"] == "sean campbell" and ok["row"]["who"] == "human"

    doc = api.serve(cfg, card["spec"])["doc"]
    ids = [t["id"] for t in doc["tables"]]
    (p,) = api.take_proposals(cfg, [line(cites=ids[:1])])["out"]["proposals"]
    wrong = api.seal_proposal(
        cfg,
        p["subject"],
        pair=nestor_pair(s),
        keyring=RING,  # a pair for the scope
    )
    assert wrong["sealed"] is False and "not this subject" in wrong["row"]["reason"]
    assert not (cfg.box / "notes").exists()
    done = api.seal_proposal(
        cfg, p["subject"], pair=nestor_pair(p["subject"]), keyring=RING
    )
    assert done["sealed"] and (cfg.box / "notes" / "a.md").exists()


def test_a_nestor_seal_that_does_not_verify_seals_nothing(cfg):
    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    for bad in (
        nestor_pair(s, status="draft"),
        nestor_pair(s, seal_sig="00" * 32),
        nestor_pair(s, who="stranger"),
        nestor_pair("serve:" + "cd" * 32),
    ):
        got = api.seal_scope(cfg, s, pair=bad, keyring=RING)
        assert got["sealed"] is False and got["row"]["kind"] == "refused"
    assert not [r for r in rows(cfg) if r["kind"] == "seal"]
    no_ring = api.seal_scope(cfg, s, pair=nestor_pair(s))
    assert "needs the keyring" in no_ring["row"]["reason"]


def test_a_nestor_store_and_keyring_file_seal_it(cfg, tmp_path):
    import sqlite3

    assert api.checkin(cfg)["code"] == 0
    s = api.scope(cfg, match={"who": "run"})["subject"]
    db = tmp_path / "nestor.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE tm_pairs (source_norm TEXT, target_text TEXT, status TEXT,"
        " verifier TEXT, seal_sig TEXT, superseded_by TEXT NOT NULL DEFAULT '')"
    )
    p = nestor_pair(s)
    con.execute(
        "INSERT INTO tm_pairs VALUES (?,?,?,?,?,'')",
        (p["source_norm"], p["target_text"], p["status"], p["verifier"], p["seal_sig"]),
    )
    con.commit()
    con.close()
    ring = tmp_path / "keyring.json"
    ring.write_text(
        json.dumps({"verifiers": [{"name": "sean campbell", "key": NESTOR_KEY.hex()}]})
    )
    got = api.seal_scope(cfg, s, nestor_db=db, keyring=ring)
    assert got["sealed"] and got["row"]["via"] == "nestor"
    gone = api.seal_scope(cfg, s, nestor_db=tmp_path / "gone.db", keyring=ring)
    assert gone["sealed"] is False and "unreadable" in gone["row"]["reason"]


# ── the CLI is the same path ─────────────────────────────────────────────────
def test_the_cli_walks_the_same_path(cfg, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "CONSTITUTION", cfg.constitution)
    monkeypatch.setattr(cli, "CI", cfg.ci)
    monkeypatch.setattr(cli, "ROOT", cfg.root)
    monkeypatch.setattr(cli, "GROVE", cfg.grove)
    base = [
        "--box", str(cfg.box), "--keys", str(cfg.keys), "--venv", str(cfg.venv),
        "--now", cfg.now, "--no-tests",
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
    assert js("serve", *flags)["state"] == "populated"
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
