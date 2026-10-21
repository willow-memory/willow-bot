"""Signing and keys, pinned (flatten-2 F14).

RFC 8032 section 7.1 test vectors, the non-canonical ``s`` and public-key
vectors the pinned verifier must refuse, and the byte outputs the repointed
sites made BEFORE they were repointed (D-e: bytes already persisted never
change). The expected hex and bytes are literals on purpose. Every key made
here is a throwaway.
"""

from __future__ import annotations

import json
import sys

import pytest

from onescript import canon, gate, nestor_seal, serve, sign

# RFC 8032 section 7.1: (secret, public, message, signature)
RFC8032 = [
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b",
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00",
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a",
    ),
]

crypto = pytest.mark.skipif(
    not sign.have_ed25519(), reason="needs the cryptography package"
)


@crypto
@pytest.mark.parametrize("secret,public,msg,sig", RFC8032)
def test_rfc8032_vectors_sign_and_verify(secret, public, msg, sig):
    priv, m = bytes.fromhex(secret), bytes.fromhex(msg)
    assert sign.ed25519_public_hex(priv) == public
    assert sign.ed25519_sign(priv, m) == sig
    assert sign.ed25519_verify_hex(public, m, sig) == (True, "")
    # one flipped message byte is a refusal that ran, not an unreachable
    assert sign.ed25519_verify_hex(public, m + b"x", sig) == (False, sign.BAD_SIGNATURE)


@crypto
@pytest.mark.parametrize("secret,public,msg,sig", RFC8032)
def test_non_canonical_s_is_refused_before_the_library(
    secret, public, msg, sig, monkeypatch
):
    raw = bytes.fromhex(sig)
    s = int.from_bytes(raw[32:], "little")
    malleable = raw[:32] + (s + sign.L).to_bytes(32, "little")  # same point, s >= L
    ok, why = sign.ed25519_verify(bytes.fromhex(public), bytes.fromhex(msg), malleable)
    assert (ok, why) == (False, "the signature's s is not canonical (s >= L)")
    # explicit: refused even if the library would have said yes
    monkeypatch.setitem(
        sys.modules, "cryptography.hazmat.primitives.asymmetric.ed25519", None
    )
    assert (
        sign.ed25519_verify(bytes.fromhex(public), bytes.fromhex(msg), malleable)[1]
        == why
    )


def test_s_exactly_L_and_L_minus_1_boundary():
    assert sign.signature_problem(b"\0" * 32 + sign.L.to_bytes(32, "little"))
    assert (
        sign.signature_problem(b"\0" * 32 + (sign.L - 1).to_bytes(32, "little")) == ""
    )
    assert sign.signature_problem(b"\0" * 63)
    assert sign.signature_problem(b"\0" * 65)


SMALL_ORDER = [
    "0100000000000000000000000000000000000000000000000000000000000000",
    "ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "0000000000000000000000000000000000000000000000000000000000000000",
    "0000000000000000000000000000000000000000000000000000000000000080",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a",
    "c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05",
    "26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85",
]
NON_CANONICAL = [  # y >= p
    "edffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "eeffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
    "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f",
]


@pytest.mark.parametrize("key", SMALL_ORDER)
def test_small_order_public_keys_are_refused(key):
    assert (
        sign.public_key_problem(bytes.fromhex(key))
        == "the public key is a small-order point"
    )
    ok, why = sign.ed25519_verify(bytes.fromhex(key), b"m", b"\0" * 64)
    assert ok is False and "small-order" in why


@pytest.mark.parametrize("key", NON_CANONICAL)
def test_non_canonical_public_keys_are_refused(key):
    assert (
        sign.public_key_problem(bytes.fromhex(key))
        == "the public key is a non-canonical encoding"
    )


@crypto
def test_real_keys_pass_the_key_rules_and_wrong_lengths_do_not():
    for _, public, _, _ in RFC8032:
        assert sign.public_key_problem(bytes.fromhex(public)) == ""
    assert sign.public_key_problem(b"\1" * 31)
    assert sign.public_key_problem("00" * 32)  # str, not bytes
    assert sign.ed25519_verify_hex("zz", b"m", "00")[0] is False


@crypto
def test_generated_throwaway_key_round_trips():
    priv = sign.ed25519_generate()
    assert len(priv) == 32
    pub = sign.ed25519_public_hex(priv)
    sig = sign.ed25519_sign(priv, b"hello")
    assert sign.ed25519_verify_hex(pub, b"hello", sig)[0] is True
    assert sign.ed25519_verify_hex(pub, b"hellp", sig)[0] is False


def test_without_the_library_the_answer_is_unreachable_not_a_pass(monkeypatch):
    for name in (
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        "cryptography.exceptions",
    ):
        monkeypatch.setitem(sys.modules, name, None)
    _, public, msg, sig = RFC8032[0][0], *RFC8032[0][1:]
    assert sign.have_ed25519() is False
    assert sign.ed25519_verify_hex(public, bytes.fromhex(msg), sig) == (
        False,
        sign.NO_CRYPTO,
    )


@crypto
def test_verifier_pin_records_library_and_version():
    import cryptography

    pin = sign.verifier_pin()
    assert pin["id"] == "wgrv-sign-ed25519/v1"
    assert (
        pin["library"] == "cryptography" and pin["version"] == cryptography.__version__
    )
    assert any("s < L" in r for r in pin["rules"])
    json.dumps(pin)  # a record can stamp it


# ---- the seal message: Nestor's frozen bytes (nestor.signing._message)

SEAL_CASES = [
    (("src", "serve:abc", "ada"), b'["src","serve:abc","ada"]'),
    (("sé", "☃ 日本", "v"), '["sé","☃ 日本","v"]'.encode()),
    (("", "", ""), b'["","",""]'),
]


@pytest.mark.parametrize("args,want", SEAL_CASES)
def test_seal_message_bytes_are_nestors(args, want):
    assert sign.seal_message(*args) == want
    assert sign.seal_message(*args) == canon.compact_utf8.bytes(list(args))


def test_nestor_seal_names_are_the_one_scripts():
    assert nestor_seal.message is sign.seal_message
    assert nestor_seal.load_keyring is sign.load_keyring
    assert nestor_seal.Unverifiable is sign.Unverifiable
    assert nestor_seal.NO_CRYPTO == sign.NO_CRYPTO
    assert nestor_seal.have_ed25519 is sign.have_ed25519


# ---- the public-only keyring rules

PUB = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"


def _ring(tmp_path, *entries):
    p = tmp_path / "ring.json"
    p.write_text(json.dumps({"version": 1, "verifiers": list(entries)}))
    return p


def _entry(**kw):
    return {"name": "ada", "key": PUB, "kind": "ed25519", **kw}


def test_keyring_accepts_a_public_ed25519_entry(tmp_path):
    ring = sign.load_keyring(
        _ring(tmp_path, _entry(revoked_at="2026-01-01", reason="left"))
    )
    assert ring == {
        "verifiers": {
            "ada": {
                "key": bytes.fromhex(PUB),
                "kind": "ed25519",
                "compromised": False,
                "revoked": True,
                "reason": "left",
            }
        }
    }


@pytest.mark.parametrize(
    "entry,needle",
    [
        (_entry(private="00"), "carries ['private']"),
        (_entry(seed="00"), "carries ['seed']"),
        (_entry(hmac_key="00"), "carries ['hmac_key']"),
        (_entry(Passphrase="x"), "carries ['Passphrase']"),
        (_entry(kind="hmac"), "only ed25519"),
        (_entry(kind=""), "only ed25519"),
        (_entry(key="00" * 31), "not a 32-byte key"),
        (_entry(key="00" * 33), "not a 32-byte key"),
    ],
)
def test_keyring_refuses_what_nestor_seal_refused(tmp_path, entry, needle):
    with pytest.raises(
        sign.Unverifiable, match=needle.replace("[", r"\[").replace("]", r"\]")
    ):
        sign.load_keyring(_ring(tmp_path, entry))


def test_keyring_unreadable_is_unverifiable_never_a_pass(tmp_path):
    for bad in ("{not json", "[]", json.dumps({"verifiers": [{"kind": "ed25519"}]})):
        p = tmp_path / "bad.json"
        p.write_text(bad)
        with pytest.raises(sign.Unverifiable, match="keyring unreadable"):
            sign.load_keyring(p)
    with pytest.raises(sign.Unverifiable, match="keyring unreadable"):
        sign.load_keyring(tmp_path / "missing.json")


# ---- the HMAC seals: bytes the gate (and the willow-mcp sites) made before

KEY = b"k" * 32


def test_hmac_bytes_are_the_old_outputs():
    assert gate.sign(KEY, "seal", "allow:é:x") == (
        "b752b3e27cb227c91b83a163a0c568f6afe6825f1a0159721fb770e7a19f1ada"
    )
    assert sign.hmac_parts(KEY, "bot", "claude") == (
        "e7da744180de18521725b90db99e7511e5ebe59ca211fec6150c66faf5cde42f"
    )
    assert sign.hmac_hex(KEY, b'{"a":1}') == (
        "1d3154043cab846e480d276b293ac8d8443514ce52ccd43a7c118d42f9bc5b5b"
    )


def test_hmac_equal_never_matches_a_wrong_or_odd_proof():
    good = sign.hmac_hex(KEY, b"x")
    assert sign.hmac_equal(good, good) and sign.hmac_equal(good.encode(), good)
    assert not sign.hmac_equal(good, good[:-1] + ("0" if good[-1] != "0" else "1"))
    assert not sign.hmac_equal(good, "é" * 64)  # non-ASCII: a mismatch, not a raise
    assert not sign.hmac_equal(good, "\ud800" * 64)  # lone surrogate too
    assert not sign.hmac_equal("", good)


def test_gate_seal_and_door_still_hold():
    proof = gate.sign(KEY, "seal", "subj")
    assert gate.verify_seal("subj", proof, KEY) is True
    assert gate.verify_seal("subj", proof + "0", KEY) is False
    assert gate.verify_seal("other", proof, KEY) is False
    assert gate.verify_seal("subj", "é" * 64, KEY) is False


def test_serve_check_cites_uses_the_one_comparison():
    doc = {"tables": [{"id": "t-1"}, {"id": "t-2"}]}
    assert serve.check_cites(doc, ["t-1", "t-2"]) == []
    bad = serve.check_cites(doc, ["t-1", "t-9", "é"])
    assert [b["cite"] for b in bad] == ["t-9", "é"]
