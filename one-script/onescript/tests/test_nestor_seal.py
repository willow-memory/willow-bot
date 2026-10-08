"""nestor_seal: the human's seal as a sealed Nestor pair, no terminal needed.

A seal is ed25519 under a public key in the OPERATOR's keyring. These tests need
`cryptography`; CI installs it, so the ed25519 path runs there (Loki 8E0652F5 F7).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    Encoding,
    PublicFormat,
)

from onescript import gate, nestor_seal  # noqa: E402

SUBJECT = "serve:" + "ab" * 32
PRIV = ed.Ed25519PrivateKey.generate()
PUB = PRIV.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def keyring(**over) -> dict:
    entry = {
        "key": PUB,
        "kind": "ed25519",
        "compromised": False,
        "revoked": False,
        "active": True,
    }
    return {"verifiers": {"sean campbell": {**entry, **over}}}


def pair(subject: str = SUBJECT, who: str = "sean campbell", priv=PRIV, **over) -> dict:
    """A sealed pair as Nestor stores it, signed over its frozen message."""
    msg = nestor_seal.message("approve this scope", subject, who)
    p = {
        "source_norm": "approve this scope",
        "target_text": subject,
        "verifier": who,
        "seal_sig": priv.sign(msg).hex(),
        "status": "sealed",
    }
    return {**p, **over}


def keyring_file(path: Path, *entries: dict) -> Path:
    path.write_text(json.dumps({"version": 1, "verifiers": list(entries)}))
    return path


def entry(**over) -> dict:
    return {"name": "sean campbell", "key": PUB.hex(), "kind": "ed25519", **over}


def test_the_message_is_nestors_frozen_encoding():
    """[source_norm, target_text, verifier], compact, UTF-8, not ASCII-escaped."""
    assert nestor_seal.message("é", "t", "v") == '["é","t","v"]'.encode("utf-8")


def test_a_sealed_pair_whose_conclusion_is_the_subject_is_a_seal():
    assert nestor_seal.check(pair(), SUBJECT, keyring()) == ("sean campbell", "")
    hum, who = gate.human_nestor(SUBJECT, pair(), keyring())
    assert (hum.who, who) == (gate.HUMAN, "sean campbell")


@pytest.mark.parametrize(
    "bad, why",
    [
        ({"status": "draft"}, "not sealed"),
        ({"status": "pending_review"}, "not sealed"),
        ({"target_text": "serve:" + "cd" * 32}, "not this subject"),
        ({"seal_sig": "00" * 64}, "does not verify"),
        ({"seal_sig": ""}, "does not verify"),
        ({"seal_sig": "zz"}, "does not verify"),
        ({"verifier": "someone else"}, "not in the keyring"),
        ({"source_norm": "a different question"}, "does not verify"),
    ],
)
def test_anything_but_a_sealed_exact_signed_pair_is_refused(bad, why):
    who, got = nestor_seal.check(pair(**bad), SUBJECT, keyring())
    assert who is None and why in got
    with pytest.raises(gate.Refused):
        gate.human_nestor(SUBJECT, pair(**bad), keyring())


def test_a_signature_made_for_another_conclusion_does_not_move():
    """The seal binds to its conclusion: relabel it and the signature breaks."""
    other = pair("serve:" + "cd" * 32)
    moved = {**other, "target_text": SUBJECT}
    assert nestor_seal.check(moved, SUBJECT, keyring())[0] is None


def test_a_compromised_revoked_or_inactive_key_and_an_unlisted_verifier_hold_nothing():
    assert (
        "compromised"
        in nestor_seal.check(pair(), SUBJECT, keyring(compromised=True))[1]
    )
    for over in ({"revoked": True}, {"active": False}):
        who, why = nestor_seal.check(pair(), SUBJECT, keyring(**over))
        assert who is None and "revoked or inactive" in why
    assert nestor_seal.check(pair(), SUBJECT, {"verifiers": {}})[0] is None


def test_the_verifier_allow_list_narrows_who_may_seal():
    allowed = frozenset({"sean campbell"})
    assert nestor_seal.check(pair(), SUBJECT, keyring(), allowed)[0] is not None
    assert nestor_seal.check(pair(), SUBJECT, keyring(), frozenset({"x"}))[0] is None


@pytest.mark.parametrize(
    "junk", [None, "a string", [], {"status": "sealed"}, {"seal_sig": 7}]
)
def test_a_malformed_pair_is_refused_not_a_crash(junk):
    assert nestor_seal.check(junk, SUBJECT, keyring())[0] is None


def test_a_key_the_caller_minted_is_not_the_operators_key():
    """Loki 8E0652F5 P2: a fresh key, labelled with the operator's name, signs
    a pair. Against the operator's keyring it verifies nothing."""
    forger = ed.Ed25519PrivateKey.generate()
    assert nestor_seal.check(pair(priv=forger), SUBJECT, keyring())[0] is None


def test_the_keyring_is_read_from_the_operators_file(tmp_path):
    f = keyring_file(
        tmp_path / "keyring.json", entry(), entry(name="gone", compromised=True)
    )
    ring = nestor_seal.load_keyring(f)
    assert nestor_seal.check(pair(), SUBJECT, ring)[0] == "sean campbell"
    assert "compromised" in nestor_seal.check(pair(who="gone"), SUBJECT, ring)[1]


@pytest.mark.parametrize(
    "bad, why",
    [
        (entry(kind="hmac"), "only ed25519"),
        ({"name": "x", "key": "00" * 32}, "only ed25519"),
        (entry(private_key="ab" * 32), "public keys only"),
        (entry(secret="x"), "public keys only"),
        (entry(seed="x"), "public keys only"),
        (entry(key="00" * 16), "32-byte"),
    ],
)
def test_a_keyring_with_a_shared_secret_or_private_material_is_refused(
    tmp_path, bad, why
):
    """Loki 8E0652F5 F1: a keyring is public keys. Anything else is refused
    by name, never skipped past."""
    f = keyring_file(tmp_path / "k.json", entry(name="fine"), bad)
    with pytest.raises(nestor_seal.Unverifiable, match=why):
        nestor_seal.load_keyring(f)


def test_the_legacy_deployment_key_is_not_a_person(tmp_path):
    """A seal signed only by the deployment-wide key proves the deployment, not
    a human: it is refused here even though Nestor would serve it."""
    f = tmp_path / "keyring.json"
    f.write_text(json.dumps({"verifiers": [], "legacy_key": PUB.hex()}))
    assert nestor_seal.check(pair(), SUBJECT, nestor_seal.load_keyring(f))[0] is None


def test_an_unreadable_keyring_is_said_plainly(tmp_path):
    for body in (None, "not json", '{"verifiers": [{"key": "zz"}]}'):
        f = tmp_path / "k.json"
        if body is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(body)
        with pytest.raises(nestor_seal.Unverifiable):
            nestor_seal.load_keyring(f)


def test_without_cryptography_the_answer_is_unreachable_not_a_refusal(monkeypatch):
    """Loki 8E0652F5 F7: where the package is absent, a real seal is neither
    waved through nor refused as forged; the check says it could not run."""
    import builtins

    real = builtins.__import__

    def no_crypto(name, *a, **k):
        if name.startswith("cryptography"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_crypto)
    assert nestor_seal.have_ed25519() is False
    who, why = nestor_seal.check(pair(), SUBJECT, keyring())
    assert who is None and why == nestor_seal.NO_CRYPTO
    assert why.startswith("unreachable")


def store(tmp_path: Path, *rows: dict) -> Path:
    """A Nestor store with the columns a seal read needs (sqlite_store's own)."""
    db = tmp_path / "nestor.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE tm_pairs (id TEXT PRIMARY KEY, source_norm TEXT, target_text"
        " TEXT, status TEXT, verifier TEXT, seal_sig TEXT, superseded_by TEXT"
        " NOT NULL DEFAULT '')"
    )
    for i, r in enumerate(rows):
        con.execute(
            "INSERT INTO tm_pairs VALUES (?,?,?,?,?,?,?)",
            (
                str(i),
                r["source_norm"],
                r["target_text"],
                r["status"],
                r["verifier"],
                r["seal_sig"],
                r.get("superseded_by", ""),
            ),
        )
    con.commit()
    con.close()
    return db


def test_pairs_come_from_the_store_read_only(tmp_path):
    db = store(
        tmp_path,
        pair(),
        pair(status="draft"),
        pair("serve:" + "cd" * 32),
        {**pair(), "superseded_by": "x"},
    )
    found = nestor_seal.pairs_for(db, SUBJECT)
    assert len(found) == 1 and nestor_seal.check(found[0], SUBJECT, keyring())[0]
    with pytest.raises(sqlite3.OperationalError):  # opened mode=ro: it can't write
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.execute("DELETE FROM tm_pairs")


def test_a_pair_in_hand_counts_only_while_the_store_still_holds_it_sealed(tmp_path):
    """Loki 8E0652F5 F6: the dict is a claim about a row, never the row."""
    held = pair()
    (tmp_path / "a").mkdir()
    live = store(tmp_path / "a", held)
    assert nestor_seal.current(live, SUBJECT, held) == [held]
    for name, row in (
        ("superseded", {**held, "superseded_by": "newer"}),
        ("rejected", {**held, "status": "rejected"}),
        ("draft", {**held, "status": "draft"}),
    ):
        (tmp_path / name).mkdir()
        db = store(tmp_path / name, row)
        assert nestor_seal.current(db, SUBJECT, held) == [], name
    other = tmp_path / "other"
    other.mkdir()
    db = store(other, pair("serve:" + "cd" * 32))  # a different conclusion
    assert nestor_seal.current(db, SUBJECT, held) == []
    assert nestor_seal.current(live, SUBJECT, "not a record") == []
    # a hand-made pair the store never saw is not in it
    forged = pair(priv=ed.Ed25519PrivateKey.generate())
    assert nestor_seal.current(live, SUBJECT, forged) == []


def test_a_missing_store_is_unverifiable_not_an_empty_answer(tmp_path):
    with pytest.raises(nestor_seal.Unverifiable):
        nestor_seal.pairs_for(tmp_path / "nope.db", SUBJECT)


def test_an_ed25519_seal_of_something_else_does_not_verify():
    p = pair()
    bad = {**p, "seal_sig": PRIV.sign(b"something else").hex()}
    assert nestor_seal.check(bad, SUBJECT, keyring())[0] is None
