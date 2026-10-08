"""nestor_seal — the human's seal as a sealed Nestor pair, so no step needs a tty.

The operator, 2026-10-07 (willows-grove docs/design/approval-broker.md:240):
"I won't have access to a terminal when this moves to an apk." A seal that is
an HMAC proof the human types into a shell can't be made on a phone. A Nestor
pair can: the human seals it in Nestor's own UI, and this module only READS
what they sealed.

A scope or a proposal seal is a sealed Nestor pair whose conclusion
(`target_text`) is the exact subject string. Nothing here signs, seals or
writes Nestor's store: it reads the pair and checks the signature the way
Nestor does, so a pair that Nestor would not serve as sealed is not a seal
here either. The signed message is Nestor's frozen seal encoding
(`nestor.signing._message`): the JSON array `[source_norm, target_text,
verifier]`, compact separators, `ensure_ascii=False`, UTF-8.

What is checked, and what is refused:

  status      the pair is `sealed`; a draft or pending pair is not a seal
  conclusion  `target_text` equals the subject exactly; a seal over a
              different string covers nothing
  verifier    a name in the keyring, not compromised; an unknown name has no
              key, so no seal. The keyring's `legacy_key` is NOT accepted: it
              proves the deployment signed, not that a person did
  signature   HMAC-SHA256 under the verifier's key, or ed25519 under their
              public key (needs `cryptography`; without it an ed25519 seal is
              refused, never waved through)

Stdlib only: willow-bot's CI and the box both run it without Nestor installed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from pathlib import Path


class Unverifiable(ValueError):
    """The keyring or store can't be read. Said plainly, never a pass."""


def message(source_norm: str, target_text: str, verifier: str) -> bytes:
    """The bytes a Nestor seal signature is taken over (frozen by Nestor)."""
    return json.dumps(
        [source_norm, target_text, verifier],
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def load_keyring(path: str | Path) -> dict:
    """Nestor's keyring file, as the verifying side needs it: name -> key."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = {}
        for v in raw.get("verifiers", []):
            entries[str(v["name"])] = {
                "key": bytes.fromhex(v["key"]),
                "kind": str(v.get("kind", "hmac")) or "hmac",
                "compromised": bool(v.get("compromised", False)),
            }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise Unverifiable(f"keyring unreadable: {type(e).__name__}") from e
    return {"verifiers": entries}


def _sig_ok(kind: str, key: bytes, msg: bytes, sig: str) -> tuple[bool, str]:
    if kind == "ed25519":
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
        except ImportError:
            return False, "an ed25519 seal needs the cryptography package here"
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(bytes.fromhex(sig), msg)
        except (InvalidSignature, ValueError):
            return False, "the seal's signature does not verify"
        return True, ""
    if kind == "hmac":
        good = hmac.new(key, msg, hashlib.sha256).hexdigest().encode()
        ok = hmac.compare_digest(good, sig.encode("utf-8", "replace"))
        return ok, "" if ok else "the seal's signature does not verify"
    return False, f"unknown key kind {kind!r}"


def check(
    pair: object, subject: str, keyring: dict, verifiers: frozenset | None = None
) -> tuple[str | None, str]:
    """(verifier, "") when `pair` is a sealed Nestor pair whose conclusion is
    exactly `subject`; else (None, why). Never raises on a bad pair."""
    if not isinstance(pair, dict):
        return None, "the pair is not a record"
    fields = ("source_norm", "target_text", "verifier", "seal_sig", "status")
    if not all(isinstance(pair.get(k), str) for k in fields):
        return None, "the pair is missing a field a seal is signed over"
    if pair["status"] != "sealed":
        return None, f"the pair is {pair['status']!r}, not sealed"
    if pair["target_text"] != subject:
        return None, "the pair's conclusion is not this subject"
    who = pair["verifier"]
    if verifiers is not None and who not in verifiers:
        return None, f"{who!r} is not a verifier this seal may come from"
    entry = keyring.get("verifiers", {}).get(who)
    if entry is None:
        return None, f"{who!r} is not in the keyring; no key backs the name"
    if entry["compromised"]:
        return None, f"{who!r}'s key is reported compromised; nothing it signed holds"
    ok, why = _sig_ok(
        entry["kind"],
        entry["key"],
        message(pair["source_norm"], pair["target_text"], who),
        pair["seal_sig"],
    )
    return (who, "") if ok else (None, why)


def pairs_for(db: str | Path, subject: str) -> list[dict]:
    """Every live sealed pair in Nestor's store whose conclusion is `subject`.
    Read-only: the database is opened `mode=ro`, so this can't seal anything."""
    uri = f"file:{Path(db).resolve()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            cur = con.execute(
                "SELECT source_norm, target_text, verifier, seal_sig, status "
                "FROM tm_pairs WHERE target_text = ? AND status = 'sealed' "
                "AND superseded_by = ''",
                (subject,),
            )
            names = ("source_norm", "target_text", "verifier", "seal_sig", "status")
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            con.close()
    except sqlite3.Error as e:
        raise Unverifiable(f"nestor store unreadable: {type(e).__name__}") from e
