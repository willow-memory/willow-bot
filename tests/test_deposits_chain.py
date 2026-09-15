"""Hash chain on ci_outcomes.jsonl.

Gap a6c0926d7e83 (hash chain sub-part). Every row appended by
willow_bot.deposits.append_local carries prev_hash and row_hash;
verify_chain walks the file and reports any break. Legacy rows (rows
written before the chain existed) are tolerated at the head of the
file — the chain begins where the first hash-carrying row appears.

Every path here is a unit test — no MCP, no network.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from willow_bot import deposits


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    return tmp_path


def _rec(*, repo: str = "willow-memory/willow-mcp", sha: str = "cafe" * 10,
         cid: int = 1, conclusion: str = "success") -> dict:
    return {
        "kind": "ci_outcome",
        "lane": "draft",
        "repo": repo,
        "head_sha": sha,
        "check_run_id": cid,
        "check_name": "Tests",
        "conclusion": conclusion,
    }


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ── append_local: every row is chained ─────────────────────────────────────


def test_first_row_starts_from_genesis(home: Path) -> None:
    rec = _rec(cid=1)
    path = deposits.append_local(rec)
    (row,) = _read_lines(path)
    assert row["prev_hash"] == deposits.GENESIS_HASH
    assert row["row_hash"] == deposits.compute_row_hash(row, deposits.GENESIS_HASH)


def test_second_row_chains_from_first(home: Path) -> None:
    r1 = _rec(cid=1)
    r2 = _rec(cid=2)
    deposits.append_local(r1)
    deposits.append_local(r2)
    rows = _read_lines(deposits.deposits_jsonl())
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert rows[1]["row_hash"] != rows[0]["row_hash"]


def test_append_returns_mutated_record_with_hashes(home: Path) -> None:
    """A caller inspecting the record after append sees exactly what
    landed on disk — the mirror step's ``record`` argument needs the
    hash fields so a downstream store_put persists them too."""
    rec = _rec(cid=42)
    deposits.append_local(rec)
    assert "prev_hash" in rec and "row_hash" in rec


def test_tip_file_tracks_the_current_head(home: Path) -> None:
    """The sidecar tip file lives next to the deposits file so an append
    is O(1) — no whole-file scan. A tail-scan is the fallback."""
    deposits.append_local(_rec(cid=1))
    deposits.append_local(_rec(cid=2))
    rows = _read_lines(deposits.deposits_jsonl())
    tip = (deposits.deposits_dir() / "ci_outcomes.chain.tip").read_text().strip()
    assert tip == rows[-1]["row_hash"]


def test_tip_file_recovers_from_missing_tip(home: Path) -> None:
    """A crash between deposits-write and tip-write leaves the tip
    behind. The next append reads the deposits tail and picks up where
    the chain left off — no double-chain, no restart from GENESIS."""
    deposits.append_local(_rec(cid=1))
    (deposits.deposits_dir() / "ci_outcomes.chain.tip").unlink()
    deposits.append_local(_rec(cid=2))
    rows = _read_lines(deposits.deposits_jsonl())
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]


# ── verify_chain: read invariants ──────────────────────────────────────────


def test_verify_empty_file_is_absent(home: Path) -> None:
    receipt = deposits.verify_chain(deposits.deposits_jsonl())
    assert receipt["present"] is False
    assert receipt["verified"] == 0
    assert receipt["broken_at"] is None


def test_verify_all_chained_rows(home: Path) -> None:
    for i in range(5):
        deposits.append_local(_rec(cid=i))
    receipt = deposits.verify_chain(deposits.deposits_jsonl())
    assert receipt["present"] is True
    assert receipt["chained_from"] == 1
    assert receipt["verified"] == 5
    assert receipt["broken_at"] is None


def test_verify_legacy_head_then_chain(home: Path) -> None:
    """A file that carries legacy rows (no hashes) at its head and
    chained rows at its tail is the shape a live box will have on
    upgrade. Verify names the chain-start line and counts what it
    verified from there."""
    path = deposits.deposits_jsonl()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "ci_outcome", "repo": "old", "cid": 0}) + "\n")
        fh.write(json.dumps({"kind": "ci_outcome", "repo": "old", "cid": 1}) + "\n")
    deposits.append_local(_rec(cid=100))
    deposits.append_local(_rec(cid=101))
    receipt = deposits.verify_chain(path)
    assert receipt["legacy_head"] == 2
    assert receipt["chained_from"] == 3
    assert receipt["verified"] == 2
    assert receipt["broken_at"] is None


def test_verify_detects_tampered_row_body(home: Path) -> None:
    """Edit a historical row's body without recomputing its row_hash —
    the recomputed hash no longer matches and the verifier flags the
    tampered row."""
    deposits.append_local(_rec(cid=1))
    deposits.append_local(_rec(cid=2))
    deposits.append_local(_rec(cid=3))
    path = deposits.deposits_jsonl()
    rows = _read_lines(path)
    rows[1]["conclusion"] = "failure"  # tamper — but keep the same row_hash
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    receipt = deposits.verify_chain(path)
    assert receipt["broken_at"] == 2
    assert "row_hash" in receipt["detail"]


def test_verify_detects_broken_prev_pointer(home: Path) -> None:
    """Replace a row's prev_hash with garbage — the chain breaks at
    that row (its prev does not match the previous row's row_hash)."""
    deposits.append_local(_rec(cid=1))
    deposits.append_local(_rec(cid=2))
    path = deposits.deposits_jsonl()
    rows = _read_lines(path)
    rows[1]["prev_hash"] = "0" * 64  # not the actual prev
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    receipt = deposits.verify_chain(path)
    assert receipt["broken_at"] == 2
    assert "prev_hash" in receipt["detail"]


def test_verify_detects_legacy_row_inside_chain(home: Path) -> None:
    """A row inside the chained region without hashes cannot continue
    the chain — the verifier flags it as a break."""
    deposits.append_local(_rec(cid=1))
    deposits.append_local(_rec(cid=2))
    path = deposits.deposits_jsonl()
    rows = _read_lines(path)
    # Strip hashes from row 2 → chain break at line 2.
    rows[1].pop("prev_hash", None)
    rows[1].pop("row_hash", None)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    receipt = deposits.verify_chain(path)
    assert receipt["broken_at"] == 2


def test_verify_detects_non_json_row(home: Path) -> None:
    deposits.append_local(_rec(cid=1))
    path = deposits.deposits_jsonl()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not valid json\n")
    receipt = deposits.verify_chain(path)
    assert receipt["broken_at"] == 2
    assert "not JSON" in receipt["detail"]


# ── canonical form: robustness ─────────────────────────────────────────────


def test_row_hash_is_stable_across_key_order(home: Path) -> None:
    """Two dicts with the same fields in different orders produce the
    same row_hash — sort_keys does the work. A caller building a rec
    from set-arithmetic does not accidentally desynchronize its hash."""
    a = {"kind": "ci_outcome", "repo": "r", "conclusion": "success"}
    b = {"conclusion": "success", "repo": "r", "kind": "ci_outcome"}
    assert deposits.compute_row_hash(a, "prev") == deposits.compute_row_hash(b, "prev")


def test_row_hash_ignores_chain_keys(home: Path) -> None:
    """A rec that ALREADY carries prev_hash and row_hash (a re-hash of
    an already-chained row for verification) computes the same hash as
    a rec without them — the chain fields never enter their own
    covered body."""
    base = {"kind": "ci_outcome", "repo": "r"}
    with_chain = {**base, "prev_hash": "abc", "row_hash": "def"}
    assert deposits.compute_row_hash(base, "prev") == deposits.compute_row_hash(with_chain, "prev")


def test_row_hash_changes_when_prev_changes(home: Path) -> None:
    """Two identical rows with different prev_hashes produce different
    row_hashes — the chain is a real chain, not a per-row Merkle."""
    rec = {"kind": "ci_outcome", "repo": "r"}
    h1 = deposits.compute_row_hash(rec, "0" * 64)
    h2 = deposits.compute_row_hash(rec, "1" * 64)
    assert h1 != h2


def test_row_hash_uses_sha256(home: Path) -> None:
    """The digest is a 64-hex-char string — sha256 in hex. A caller
    reading the file can verify with `hashlib.sha256` and stdlib json."""
    rec = {"a": 1}
    h = deposits.compute_row_hash(rec, deposits.GENESIS_HASH)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ── integration: deposit_from_check_run_payload chains too ─────────────────


def test_deposit_from_check_run_payload_chains(home: Path) -> None:
    """The deposit-writer path (called from fleet_bridge on every
    completed check) goes through append_local, so the chain covers
    every real ci_outcome the bot writes — not just direct callers of
    append_local."""
    payload = {
        "action": "completed",
        "repository": {"full_name": "forge-play/Forge"},
        "sender": {"type": "Bot"},
        "check_run": {
            "id": 999, "name": "Tests", "status": "completed",
            "conclusion": "success", "head_sha": "sha1" * 10,
            "html_url": "https://example.invalid/",
            "pull_requests": [{"number": 1}],
        },
    }
    deposits.deposit_from_check_run_payload(payload)
    deposits.deposit_from_check_run_payload({**payload, "check_run":
                                             {**payload["check_run"], "id": 1000}})
    receipt = deposits.verify_chain(deposits.deposits_jsonl())
    assert receipt["verified"] == 2
    assert receipt["broken_at"] is None
