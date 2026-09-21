"""`annul`: the honest correction of a chained deposits file (gap 9).

Rows are never rewritten. An annul row is appended through the same chain,
names what it voids (row_hash; record_id for legacy rows), why, and under
what FRANK authorization. The verifier counts it and breaks on a void that
names nothing; readers (run_ci, the mirror) skip voided rows; the mirror
retracts copies it already landed; a head whose every deposit is voided
drops out of ci_heads; the CLI is dry-run by default. Every test runs under
the autouse tmp_path home — the rule this row type exists for.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

from willow_bot import deposits as dep
from willow_bot.steward import tick
from willow_bot.steward.config import state_path


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_BOT_MCP", "1")
    return tmp_path


class _Client:
    """A fake MCP: `store_delete` answers like the real one — {deleted: true}
    only for an id a store_put landed, {deleted: false} otherwise, never an
    error."""

    def __init__(self):
        self.calls = []
        self.n = 0
        self.stored: set[str] = set()

    def __call__(self, name, inputs):
        self.calls.append((name, inputs))
        self.n += 1
        if name == "store_put":
            self.stored.add(inputs["record_id"])
        if name == "store_delete":
            hit = inputs["record_id"] in self.stored
            self.stored.discard(inputs["record_id"])
            return {"deleted": hit, "record_id": inputs["record_id"]}
        return {"ok": True, "id": f"hr-{self.n}"}

    def named(self, name):
        return [i for n, i in self.calls if n == name]


def _use(monkeypatch, client):
    monkeypatch.setattr("willow_bot.steward.mcp_client.call", client)


FORGE = "forge-play/Forge"
FIX = "cc9aab19ba2502e14e331e20e699f634fb4cb1a2"
REAL = "1111111111111111111111111111111111111111"
URL = "https://example.invalid/runs/11"


def _row(repo, sha, cid, name, conclusion, *, pr=None, url=None, received_at=None):
    rec = dep.ci_outcome_record(repo=repo, head_sha=sha, check_run_id=cid, check_name=name,
                                conclusion=conclusion, pr_number=pr, received_at=received_at)
    rec["html_url"] = url or f"https://github.com/{repo}/actions/runs/1/job/{cid}"
    return rec


def _legacy(repo, sha, cid, name, conclusion, *, pr=None, url=None):
    """A row written before the chain existed: appended raw, no hashes."""
    rec = _row(repo, sha, cid, name, conclusion, pr=pr, url=url)
    p = dep.deposits_jsonl()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    return rec


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ── the row chains and verifies ──────────────────────────────────────────────

def test_annul_is_chained_and_counted_by_the_verifier(home):
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    b = _row(FORGE, FIX, 12, "lint", "failure", pr=4, url=URL)
    c = _row("x/y", REAL, 1, "test", "success", pr=9)
    for r in (a, b, c):
        dep.append_local(r)
    ann = dep.annul_record(voids=[a["row_hash"], b["row_hash"]], reason="test fixture rows",
                           authorization="frank-ee02b299")
    dep.append_local(ann)
    assert ann["prev_hash"] == c["row_hash"]
    v = dep.verify_chain(dep.deposits_jsonl())
    assert v["broken_at"] is None and v["verified"] == 4
    assert v["annul_rows"] == 1 and v["annulled"] == 2 and v["annulled_legacy"] == 0
    assert v["tip"] == ann["row_hash"]
    # A later row chains onto the annul like any row.
    d = _row("x/y", REAL, 2, "lint", "success", pr=9)
    dep.append_local(d)
    assert d["prev_hash"] == ann["row_hash"]
    assert dep.verify_chain(dep.deposits_jsonl())["verified"] == 5


def test_a_void_naming_a_hash_not_in_the_file_is_a_break(home):
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4)
    dep.append_local(a)
    dep.append_local(dep.annul_record(voids=["f" * 64], reason="r", authorization="x"))
    v = dep.verify_chain(dep.deposits_jsonl())
    assert v["broken_at"] == 2 and "not in the file before it" in v["detail"]


def test_a_void_naming_a_later_row_is_a_break(home):
    """An annul cannot void what comes after it — the hash did not exist."""
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4)
    dep.append_local(a)
    later = _row(FORGE, FIX, 12, "lint", "failure", pr=4)
    later_hash = dep.compute_row_hash(later, "irrelevant")
    dep.append_local(dep.annul_record(voids=[later_hash], reason="r", authorization="x"))
    v = dep.verify_chain(dep.deposits_jsonl())
    assert v["broken_at"] == 2


def test_a_second_annul_of_the_same_row_is_already(home):
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4)
    dep.append_local(a)
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="r", authorization="x"))
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="again", authorization="x"))
    v = dep.verify_chain(dep.deposits_jsonl())
    assert v["broken_at"] is None and v["annulled"] == 1 and v["annul_rows"] == 2
    assert v["already"] == [{"line": 3, "row_hash": a["row_hash"]}]


def test_a_legacy_row_is_voided_by_record_id(home):
    leg = _legacy(FORGE, FIX, 5, "test", "failure", pr=4, url=URL)
    _legacy(FORGE, FIX, 5, "test", "failure", pr=4, url=URL)  # a replay: same record_id, second row
    a = _row("x/y", REAL, 1, "test", "success", pr=9)
    dep.append_local(a)
    dep.append_local(dep.annul_record(voids=[], voids_legacy=[dep.record_id_for(leg)],
                                      reason="legacy fixture", authorization="x"))
    v = dep.verify_chain(dep.deposits_jsonl())
    assert v["broken_at"] is None and v["legacy_head"] == 2
    # One id, two rows: the verifier says both numbers (Loki 717E236C).
    assert v["annulled_legacy"] == 1 and v["annulled"] == 0 and v["annulled_rows"] == 2
    rows = dep.read_rows(dep.deposits_jsonl())
    assert [voided for _, voided in rows] == [True, True, False, False]
    # A legacy id not in the file is a break too.
    dep.append_local(dep.annul_record(voids=[], voids_legacy=["ci-nope-0"], reason="r", authorization="x"))
    assert dep.verify_chain(dep.deposits_jsonl())["broken_at"] == 5


def test_an_annul_row_is_never_itself_voided(home):
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4)
    dep.append_local(a)
    ann = dep.annul_record(voids=[a["row_hash"]], reason="r", authorization="x")
    dep.append_local(ann)
    voids = dep.VoidSet()
    voids.take(ann)
    assert voids.voided(a) and not voids.voided(ann)


# ── readers skip voided rows ─────────────────────────────────────────────────

def _prime():
    dep.append_local(_row("x/y", "0" * 40, 0, "older", "success"))
    assert tick.run_ci(enable_mcp=False)["first_run_skipped_bytes"] > 0


def test_run_ci_never_files_a_voided_red(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    dep.append_local(a)
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="fixture", authorization="x"))
    r = tick.run_ci()
    assert r["filed"] == [] and r["red"] == [] and r["annulled"] == 1
    assert c.named("human_required_enqueue") == []


def test_run_ci_honours_an_annul_before_its_offset(home, monkeypatch):
    """The annul was appended in an earlier tick; a voided row that lands
    AFTER it (a replay, a stray append) is still skipped."""
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    dep.append_local(a)
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="fixture", authorization="x"))
    tick.run_ci()
    # The SAME row bytes appended again would get a new hash; simulate a
    # legacy replay instead: a raw copy without hashes, voided by record_id.
    dep.append_local(dep.annul_record(voids=[], voids_legacy=[dep.record_id_for(a)],
                                      reason="replay guard", authorization="x"))
    _legacy(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    r = tick.run_ci()
    assert r["filed"] == [] and r["annulled"] == 1
    assert c.named("human_required_enqueue") == []


def test_mirror_skips_voided_rows_and_retracts_already_mirrored_copies(home, monkeypatch):
    c = _Client()
    _use(monkeypatch, c)
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    b = _row("x/y", REAL, 1, "test", "success", pr=9)
    dep.append_local(a)
    dep.append_local(b)
    r1 = tick.run_mirror()
    assert r1["mirrored"] == 2 and r1["annulled"] == 0 and r1["annulled_mirrors"] == 0
    # The annul lands after the mirror already copied `a`.
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="fixture", authorization="x"))
    late = _row(FORGE, FIX, 12, "lint", "failure", pr=4, url=URL)
    dep.append_local(late)
    dep.append_local(dep.annul_record(voids=[late["row_hash"]], reason="fixture", authorization="x"))
    r2 = tick.run_mirror()
    deletes = c.named("store_delete")
    # Only `a` had reached the store; `late` was voided before the mirror
    # got to it, so there is nothing of it to retract and it is never put.
    assert [d["record_id"] for d in deletes] == [dep.record_id_for(a)]
    assert deletes[0]["collection"] == dep.COLLECTION
    puts = [p["record_id"] for p in c.named("store_put")]
    assert dep.record_id_for(late) not in puts
    assert r2["mirrored"] == 0 and r2["annulled_mirrors"] == 1 and r2["annulled"] == 1
    assert r2["status"] == "ok" and r2["behind"] == 0


def test_mirror_retracts_a_voided_legacy_row_by_record_id(home, monkeypatch):
    c = _Client()
    _use(monkeypatch, c)
    leg = _legacy(FORGE, FIX, 5, "test", "failure", pr=4, url=URL)
    tick.run_mirror()
    dep.append_local(dep.annul_record(voids=[], voids_legacy=[dep.record_id_for(leg)],
                                      reason="legacy fixture", authorization="x"))
    r = tick.run_mirror()
    assert [d["record_id"] for d in c.named("store_delete")] == [dep.record_id_for(leg)]
    assert r["annulled_mirrors"] == 1


def test_mirror_counts_only_what_the_store_actually_deleted(home, monkeypatch):
    """A legacy row the mirror never landed (offset was past it before the
    mirror ever ran) is not retracted; a delete the store answers
    {deleted: false} to is not counted (Loki 717E236C)."""
    c = _Client()
    _use(monkeypatch, c)
    leg = _legacy(FORGE, FIX, 5, "test", "failure", pr=4, url=URL)
    a = _row("x/y", REAL, 1, "test", "success", pr=9)
    dep.append_local(a)
    tick.run_mirror()  # lands both: leg (legacy) and a
    # The store forgets `leg` out of band; the annul then finds nothing to delete.
    c.stored.discard(dep.record_id_for(leg))
    dep.append_local(dep.annul_record(voids=[], voids_legacy=[dep.record_id_for(leg)],
                                      reason="legacy fixture", authorization="x"))
    r = tick.run_mirror()
    assert [d["record_id"] for d in c.named("store_delete")] == [dep.record_id_for(leg)]
    assert r["annulled_mirrors"] == 0 and r["status"] == "ok"
    # A legacy id the mirror has not reached yet (after its offset) is not
    # retracted — nothing of it is in the store to retract.
    c2 = _Client()
    _use(monkeypatch, c2)
    unlanded = _legacy(FORGE, FIX, 6, "lint", "failure", pr=4, url=URL)
    dep.append_local(dep.annul_record(voids=[], voids_legacy=[dep.record_id_for(unlanded)],
                                      reason="voided before it was mirrored", authorization="x"))
    r3 = tick.run_mirror()
    assert c2.named("store_delete") == [] and c2.named("store_put") == []
    assert r3["annulled_mirrors"] == 0 and r3["annulled"] == 1


# ── prune: a fully-voided head leaves ci_heads ───────────────────────────────

def test_a_head_whose_every_deposit_is_voided_drops_out_of_ci_heads(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    base = time.time()
    # A PR-less fixture head (the case Loki 346276F9 named: never pruned).
    a = _row(FORGE, FIX, 11, "test", "failure", url=URL, received_at=_iso(base))
    dep.append_local(a)
    r1 = tick.run_ci()
    assert len(r1["filed"]) == 1
    state = json.loads(state_path().read_text())
    assert tick._ci_pr_key(FORGE, None, FIX) in state["ci_heads"]
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="fixture", authorization="x"))
    r2 = tick.run_ci()
    assert r2["pruned"]["voided_heads"] == 1
    state = json.loads(state_path().read_text())
    assert tick._ci_pr_key(FORGE, None, FIX) not in state["ci_heads"]
    assert not any(v.get("head_sha") == FIX for v in state["ci_items"].values())
    # ci_filed keeps its (inert) key: a re-read is skipped before it could file.
    assert any(k.startswith(f"{FIX}:") for k in state["ci_filed"])


def test_a_head_with_one_honest_row_left_is_kept(home, monkeypatch):
    _prime()
    c = _Client()
    _use(monkeypatch, c)
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    b = _row(FORGE, FIX, 12, "lint", "failure", pr=4)
    dep.append_local(a)
    dep.append_local(b)
    tick.run_ci()
    dep.append_local(dep.annul_record(voids=[a["row_hash"]], reason="one of two", authorization="x"))
    r = tick.run_ci()
    assert "voided_heads" not in (r.get("pruned") or {})
    state = json.loads(state_path().read_text())
    assert FIX in state["ci_heads"][tick._ci_pr_key(FORGE, 4, FIX)]


# ── the CLI: dry-run by default, one row on --apply ─────────────────────────

def test_cli_dry_run_prints_the_plan_and_writes_nothing(home, capsys):
    _legacy(FORGE, FIX, 5, "test", "failure", pr=4, url=URL)
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    b = _row("x/y", REAL, 1, "test", "success", pr=9)
    dep.append_local(a)
    dep.append_local(b)
    before = dep.deposits_jsonl().read_text()
    rc = tick.main(["annul", "--match", URL])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "dry-run" and out["apply"] is False
    assert out["count"] == 2 and out["voids"] == [a["row_hash"]]
    assert out["voids_legacy"] == [dep.record_id_for(_row(FORGE, FIX, 5, "test", "failure"))]
    assert out["rows"][0]["html_url"] == URL and out["legacy_rows"][0]["check_run_id"] == 5
    assert out["already"] == []
    # Every distinct (repo, head) the plan would void is named, with its row count.
    assert out["heads"] == [{"repo": FORGE, "head_sha": FIX, "rows": 2}]
    assert out["matchers"] == {"match": URL}
    assert dep.deposits_jsonl().read_text() == before


def test_cli_apply_appends_one_annul_and_a_second_apply_finds_nothing(home, capsys):
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    b = _row(FORGE, FIX, 12, "lint", "failure", pr=4, url=URL)
    dep.append_local(a)
    dep.append_local(b)
    rc = tick.main(["annul", "--match", URL, "--reason", "test fixture rows from a Kart suite run",
                    "--authorization", "frank-ee02b299", "--apply"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "applied" and sorted(out["voids"]) == sorted([a["row_hash"], b["row_hash"]])
    rows = [json.loads(ln) for ln in dep.deposits_jsonl().read_text().splitlines()]
    assert len(rows) == 3 and rows[-1]["kind"] == "annul"
    assert rows[-1]["authorization"] == "frank-ee02b299" and rows[-1]["row_hash"] == out["row_hash"]
    v = dep.verify_chain(dep.deposits_jsonl())
    assert v["broken_at"] is None and v["annulled"] == 2
    rc2 = tick.main(["annul", "--match", URL, "--reason", "r", "--authorization", "x", "--apply"])
    assert rc2 == 1
    out2 = json.loads(capsys.readouterr().out)
    assert out2["status"] == "refused" and out2["already_count"] == 2 and out2["count"] == 0
    assert len(dep.deposits_jsonl().read_text().splitlines()) == 3


def test_cli_apply_refuses_without_reason_or_authorization(home, capsys):
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    dep.append_local(a)
    assert tick.main(["annul", "--match", URL, "--apply"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "refused" and "required" in out["detail"]
    assert len(dep.deposits_jsonl().read_text().splitlines()) == 1


def test_cli_matchers_are_scoped_and_anded(home, capsys):
    """`--match`/`--url` see html_url only (a sha in the url does not count
    as a sha match); `--sha` is a head prefix; `--repo` is exact; together
    they AND (Loki 717E236C: an unscoped substring reached real rows)."""
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    b = _row(FORGE, REAL, 12, "test", "failure", pr=5, url=f"https://github.com/x/runs/{FIX}")
    c = _row("other/repo", FIX, 13, "test", "failure", pr=6, url=URL)
    for r in (a, b, c):
        dep.append_local(r)
    tick.main(["annul", "--sha", FIX[:12]])
    out = json.loads(capsys.readouterr().out)
    assert sorted(out["voids"]) == sorted([a["row_hash"], c["row_hash"]]) and out["count"] == 2
    assert out["heads"] == [{"repo": FORGE, "head_sha": FIX, "rows": 1},
                            {"repo": "other/repo", "head_sha": FIX, "rows": 1}]
    tick.main(["annul", "--match", FIX])  # url substring: only b's url carries the sha
    out = json.loads(capsys.readouterr().out)
    assert out["voids"] == [b["row_hash"]]
    tick.main(["annul", "--url", "example.invalid", "--repo", FORGE])
    out = json.loads(capsys.readouterr().out)
    assert out["voids"] == [a["row_hash"]] and out["matchers"] == {"url": "example.invalid", "repo": FORGE}
    tick.main(["annul", "--repo", "nobody/nothing"])
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_cli_refuses_without_any_matcher(home, capsys):
    dep.append_local(_row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL))
    assert tick.main(["annul", "--reason", "r", "--authorization", "x", "--apply"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "refused" and "at least one" in out["detail"]
    assert len(dep.deposits_jsonl().read_text().splitlines()) == 1


def test_cli_apply_refuses_a_plan_spanning_heads_unless_allow_multi(home, capsys):
    """The live-file lesson: `example.invalid` matched a second fixture head
    (willows-grove @ prove0deadbe) beside Forge#4. The plan names both and
    --apply stops until the operator says --allow-multi."""
    a = _row(FORGE, FIX, 11, "test", "failure", pr=4, url=URL)
    g = _row("willow-memory/willows-grove", "prove0deadbe" + "0" * 28, 900001, "prove-deposit", "success",
             url="https://example.invalid/check/900001")
    dep.append_local(a)
    dep.append_local(g)
    rc = tick.main(["annul", "--url", "example.invalid", "--reason", "fixtures", "--authorization", "x", "--apply"])
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "refused" and "2 distinct" in out["detail"]
    assert [h["repo"] for h in out["heads"]] == [FORGE, "willow-memory/willows-grove"]
    assert len(dep.deposits_jsonl().read_text().splitlines()) == 2
    rc2 = tick.main(["annul", "--url", "example.invalid", "--reason", "fixtures", "--authorization", "x",
                     "--apply", "--allow-multi"])
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["status"] == "applied" and sorted(out2["voids"]) == sorted([a["row_hash"], g["row_hash"]])
    assert dep.verify_chain(dep.deposits_jsonl())["annulled_rows"] == 2
