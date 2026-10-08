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
  verifier    a name in the OPERATOR's keyring (fixed in `Config`, never from the
              caller or the pair) whose key is not compromised. A key revoked
              without compromise still verifies the seals it made, as in
              Nestor (it makes no new ones); an unknown name has no key, so
              no seal. The keyring's `legacy_key`
              is NOT accepted: it proves the deployment signed, not that a
              person did
  signature   ed25519 under the verifier's public key. An HMAC entry is a
              shared secret, so it is refused when the keyring is loaded.
              Without `cryptography` the answer is `unreachable`, never a
              silent refusal of every real seal, and never a pass
  standing    the pair must still be `sealed` and unsuperseded in Nestor's
              store right now (`current`); a pair handed in as a dict is only
              ever a claim about a row, never the row

Needs `cryptography` for ed25519. Nestor itself need not be installed.
"""

from __future__ import annotations

import json
import os
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


#: Said when the ed25519 check can't run here. It is `unreachable`, not a
#: refusal of the seal itself: the seal was never judged.
NO_CRYPTO = "unreachable: an ed25519 seal needs the cryptography package here"

#: A keyring is public keys. An entry with a field named for private material is
#: refused, whatever else it holds.
_PRIVATE = ("priv", "secret", "seed", "passphrase", "signing", "hmac")


def have_ed25519() -> bool:
    """Whether this interpreter can check an ed25519 seal at all."""
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
    except ImportError:
        return False
    return True


def load_keyring(path: str | Path) -> dict:
    """The operator's keyring file, as the verifying side needs it: name -> key.

    Only ed25519 public keys are accepted. An HMAC entry is a shared secret
    (whoever can verify with it can also forge with it), and an entry that
    carries private material is a keyring someone else could have written from
    the signing side; both are refused, naming the entry, never skipped."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = {}
        for v in raw.get("verifiers", []):
            name = str(v["name"])
            odd = sorted(k for k in v if any(p in str(k).lower() for p in _PRIVATE))
            if odd:
                raise Unverifiable(
                    f"keyring entry {name!r} carries {odd}; a keyring holds public "
                    "keys only"
                )
            kind = str(v.get("kind", ""))
            if kind != "ed25519":
                raise Unverifiable(
                    f"keyring entry {name!r} is kind {kind!r}; only ed25519 "
                    "public keys are accepted"
                )
            key = bytes.fromhex(v["key"])
            if len(key) != 32:
                raise Unverifiable(f"keyring entry {name!r} is not a 32-byte key")
            # Nestor's own fields (`nestor.keyring.VerifierKey`): a key is revoked
            # when `revoked_at` is set; `compromised` says why that matters.
            entries[name] = {
                "key": key,
                "kind": kind,
                "compromised": bool(v.get("compromised", False)),
                "revoked": bool(v.get("revoked_at", "")),
                "reason": str(v.get("reason", "")),
            }
    except Unverifiable:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise Unverifiable(f"keyring unreadable: {type(e).__name__}") from e
    return {"verifiers": entries}


def export_public(src: str | Path, dst: str | Path) -> dict:
    """Write the public half of the signing keyring `src` to `dst`.

    The signing keyring holds secrets, and the box must never read it. This is
    the one door between them, run by the operator outside the box: it copies
    each ed25519 entry's Nestor fields (name, key, kind, revoked_at,
    compromised, reason, created_at) and nothing else, so `private` can't be
    carried over, and drops every HMAC entry (a shared
    secret: whoever can verify with it can forge with it) and `legacy_key`.
    The file is written 0644 and moved into place atomically. Returns
    {"exported": [names], "dropped_hmac": [names]}."""
    src, dst = Path(src), Path(dst)
    if src.resolve() == dst.resolve():
        raise Unverifiable("the export can't overwrite the keyring it reads")
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
        entries = raw["verifiers"]
        out, exported, dropped = [], [], []
        for v in entries:
            name = str(v["name"])
            if str(v.get("kind", "hmac")) != "ed25519":
                dropped.append(name)
                continue
            if len(bytes.fromhex(v["key"])) != 32:
                raise Unverifiable(f"keyring entry {name!r} is not a 32-byte key")
            out.append(
                {
                    "name": name,
                    "key": str(v["key"]),
                    "kind": "ed25519",
                    "revoked_at": str(v.get("revoked_at", "")),
                    "compromised": bool(v.get("compromised", False)),
                    "reason": str(v.get("reason", "")),
                    "created_at": str(v.get("created_at", "")),
                }
            )
            exported.append(name)
    except Unverifiable:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise Unverifiable(f"keyring unreadable: {type(e).__name__}") from e
    body = json.dumps({"version": 1, "verifiers": out}, indent=2, ensure_ascii=False)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        os.chmod(tmp, 0o644)  # the umask must not narrow what the box has to read
        load_keyring(tmp)  # what was written is what the box will accept
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return {"exported": exported, "dropped_hmac": dropped}


def _sig_ok(kind: str, key: bytes, msg: bytes, sig: str) -> tuple[bool, str]:
    if kind != "ed25519":
        return False, f"key kind {kind!r} is not accepted; only ed25519 public keys"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return False, NO_CRYPTO
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(bytes.fromhex(sig), msg)
    except (InvalidSignature, ValueError):
        return False, "the seal's signature does not verify"
    return True, ""


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
    # Nestor's semantics (nestor/keyring.py): a revoked key can make no new seals
    # but still verifies the ones it made (the person left, their checks stand);
    # a compromised key verifies nothing, since a theft can't be told from the
    # owner's own signature. This side only reads seals already made, so a
    # revoked key still passes and a compromised one never does.
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


_PAIR_FIELDS = ("source_norm", "target_text", "verifier", "seal_sig", "status")


def current(db: str | Path, subject: str, pair: object = None) -> list[dict]:
    """The live sealed pairs in Nestor's store for `subject`, read just now.
    With a `pair` handed in, only the stored rows that are that pair: what the
    caller holds is a claim, and the store's current row is the answer. A pair
    since superseded, unsealed or rejected is not in the store's live set, so
    it comes back empty."""
    live = pairs_for(db, subject)
    if pair is None:
        return live
    if not isinstance(pair, dict):
        return []
    return [p for p in live if all(p[k] == pair.get(k) for k in _PAIR_FIELDS)]
