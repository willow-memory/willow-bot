"""nestor_seal: the human's seal as a sealed Nestor pair, no terminal needed."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import gate, nestor_seal  # noqa: E402

SUBJECT = "serve:" + "ab" * 32
KEY = b"rita-hmac-key-32-bytes-long-xxxx"


def keyring(**over) -> dict:
    entry = {"key": KEY, "kind": "hmac", "compromised": False}
    return {"verifiers": {"sean campbell": {**entry, **over}}}


def pair(subject: str = SUBJECT, who: str = "sean campbell", **over) -> dict:
    """A sealed pair as Nestor stores it, signed over its frozen message."""
    msg = nestor_seal.message("approve this scope", subject, who)
    p = {
        "source_norm": "approve this scope",
        "target_text": subject,
        "verifier": who,
        "seal_sig": hmac.new(KEY, msg, hashlib.sha256).hexdigest(),
        "status": "sealed",
    }
    return {**p, **over}


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
        ({"seal_sig": "00" * 32}, "does not verify"),
        ({"seal_sig": ""}, "does not verify"),
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


def test_a_compromised_key_and_an_unlisted_verifier_hold_nothing():
    assert (
        "compromised"
        in nestor_seal.check(pair(), SUBJECT, keyring(compromised=True))[1]
    )
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


def test_the_keyring_is_read_from_nestors_file(tmp_path):
    f = tmp_path / "keyring.json"
    f.write_text(
        json.dumps(
            {
                "version": 1,
                "verifiers": [
                    {"name": "sean campbell", "key": KEY.hex(), "kind": "hmac"},
                    {"name": "gone", "key": "00" * 32, "compromised": True},
                ],
                "legacy_key": "11" * 32,
            }
        )
    )
    ring = nestor_seal.load_keyring(f)
    assert nestor_seal.check(pair(), SUBJECT, ring)[0] == "sean campbell"
    assert "compromised" in nestor_seal.check(pair(who="gone"), SUBJECT, ring)[1]


def test_the_legacy_deployment_key_is_not_a_person(tmp_path):
    """A seal signed only by the deployment-wide key proves the deployment, not
    a human: it is refused here even though Nestor would serve it."""
    f = tmp_path / "keyring.json"
    f.write_text(json.dumps({"verifiers": [], "legacy_key": KEY.hex()}))
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


def test_a_missing_store_is_unverifiable_not_an_empty_answer(tmp_path):
    with pytest.raises(nestor_seal.Unverifiable):
        nestor_seal.pairs_for(tmp_path / "nope.db", SUBJECT)


def test_an_ed25519_seal_verifies_when_the_package_is_here():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    priv = ed.Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    msg = nestor_seal.message("approve this scope", SUBJECT, "sean campbell")
    p = pair(seal_sig=priv.sign(msg).hex())
    ring = keyring(key=pub, kind="ed25519")
    assert nestor_seal.check(p, SUBJECT, ring)[0] == "sean campbell"
    bad = {**p, "seal_sig": priv.sign(b"something else").hex()}
    assert nestor_seal.check(bad, SUBJECT, ring)[0] is None
