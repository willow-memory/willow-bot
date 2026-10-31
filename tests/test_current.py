"""willow_bot.current — what should be current, and whether it is.

Read-only: every check only looks. The git checks run on real repos with a
local bare repo as their origin, so nothing here touches the network.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from willow_bot import current


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def _commit(repo: Path, name: str = "f.txt", text: str = "x") -> str:
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-q", "-m", f"{name} {text}")
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def root(tmp_path, monkeypatch):
    gh = tmp_path / "github"
    gh.mkdir()
    monkeypatch.setenv("GITHUB_ROOT", str(gh))
    return gh


def _pair(root: Path, org: str, name: str) -> tuple[Path, Path]:
    """A bare origin and a clone of it at ``root/org/name``, one commit in."""
    origin = root.parent / "origins" / org / f"{name}.git"
    origin.mkdir(parents=True)
    _git(origin, "init", "-q", "--bare", "-b", "main")
    clone = root / org / name
    clone.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    _git(clone, "checkout", "-q", "-b", "main")
    _commit(clone)
    _git(clone, "push", "-q", "-u", "origin", "main")
    return origin, clone


def _push_from_elsewhere(origin: Path, tmp: Path, n: int = 1) -> None:
    other = tmp / f"other-{origin.stem}"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    for i in range(n):
        _commit(other, "g.txt", str(i))
    _git(other, "push", "-q", "origin", "main")


def test_a_checkout_level_with_its_origin_is_current(root):
    _, clone = _pair(root, "org", "a")
    row = current.checkout(clone)
    assert (row["status"], row["state"], row["default"]) == ("populated", "current", "main")


def test_a_push_never_fetched_is_behind_unfetched_not_current(root, tmp_path):
    origin, clone = _pair(root, "org", "a")
    _push_from_elsewhere(origin, tmp_path, 2)

    row = current.checkout(clone)

    assert row["state"] == "behind" and row["behind"] == "unfetched"


def test_behind_ahead_and_diverged_are_counted_when_the_head_is_known(root, tmp_path):
    origin, clone = _pair(root, "org", "a")
    _push_from_elsewhere(origin, tmp_path, 3)
    _git(clone, "fetch", "-q")
    row = current.checkout(clone)
    assert (row["state"], row["behind"], row["ahead"]) == ("behind", 3, 0)
    _commit(clone, "mine.txt")
    row = current.checkout(clone)
    assert (row["state"], row["behind"], row["ahead"]) == ("diverged", 3, 1)


def test_another_branch_is_said_and_not_judged(root):
    _, clone = _pair(root, "org", "a")
    _git(clone, "checkout", "-q", "-b", "feature")
    row = current.checkout(clone)
    assert row["state"] == "off-default" and row["branch"] == "feature"


def test_an_origin_that_cant_be_asked_is_unreachable_never_current(root, tmp_path):
    origin, clone = _pair(root, "org", "a")
    origin.rename(tmp_path / "gone.git")
    row = current.checkout(clone)
    assert row["status"] == "unreachable" and row["state"] == "unreachable"


def test_worktrees_and_hidden_folders_are_not_more_checkouts(root):
    _, clone = _pair(root, "org", "a")
    (root / "org" / ".worktrees" / "x").mkdir(parents=True)
    (root / "org" / ".worktrees" / "x" / ".git").write_text("gitdir: elsewhere\n")
    (root / "org" / "worktrees").mkdir()
    (root / "org" / "worktrees" / ".git").write_text("gitdir: elsewhere\n")
    assert current._checkout_dirs(root) == [clone]


def _registry(tmp_path: Path, **rules) -> Path:
    path = tmp_path / "copies.json"
    path.write_text(json.dumps(rules))
    return path


def _files(root: Path, files: dict[str, str]) -> None:
    for ref, text in files.items():
        repo, path = ref.split(":", 1)
        where = root / repo
        if not (where / ".git").exists():
            where.mkdir(parents=True, exist_ok=True)
            _git(where, "init", "-q", "-b", "main")
        (where / path).parent.mkdir(parents=True, exist_ok=True)
        (where / path).write_text(text)


def test_a_byte_copy_is_current_only_when_its_bytes_are_the_sources(root, tmp_path):
    _files(root, {"o/a:copy.py": "same\n", "o/b:src.py": "same\n", "o/a:old.py": "older\n"})
    reg = _registry(tmp_path, bytes=[{"copy": "o/a:copy.py", "source": "o/b:src.py"},
                                     {"copy": "o/a:old.py", "source": "o/b:src.py"}])  # fmt: skip
    rows = current.copies(reg)["bytes"]
    assert [r["current"] for r in rows] == [True, False]


def test_a_known_difference_is_allowed_once_and_only_as_known(root, tmp_path):
    _files(root, {
        "o/a:sign_local.py": "from . import canon_local as canon\nbody\n",
        "o/a:twice.py": "from . import canon_local as canon\nfrom . import canon_local as canon\nbody\n",
        "o/b:sign.py": "from . import canon\nbody\n",
    })
    swap = [["from . import canon_local as canon\n", "from . import canon\n"]]
    reg = _registry(tmp_path, bytes=[{"copy": "o/a:sign_local.py", "source": "o/b:sign.py", "replace": swap},
                                     {"copy": "o/a:twice.py", "source": "o/b:sign.py", "replace": swap}])  # fmt: skip
    ok, odd = current.copies(reg)["bytes"]
    assert ok["current"] is True
    assert odd["current"] is False and "not the known difference" in odd["detail"]


def test_a_copy_whose_repo_isnt_here_is_unreachable_not_different(root, tmp_path):
    _files(root, {"o/b:src.py": "x\n"})
    reg = _registry(tmp_path, bytes=[{"copy": "o/missing:copy.py", "source": "o/b:src.py"}])
    [row] = current.copies(reg)["bytes"]
    assert row["status"] == "unreachable" and "no local checkout" in row["detail"]


def test_repos_are_found_case_blind_and_flat(root):
    (root / "willow-memory" / "Willow" / ".git").mkdir(parents=True)
    (root / "almanac-data" / ".git").mkdir(parents=True)
    assert current.repo_path("willow-memory/willow") == root / "willow-memory" / "Willow"
    assert current.repo_path("almanac-data/almanac-data") == root / "almanac-data"


def test_a_pin_behind_its_source_lists_what_landed_since(root, tmp_path):
    _files(root, {"o/bot:kernel.pin": "{}"})
    src = root / "o" / "willow"
    src.mkdir(parents=True)
    _git(src, "init", "-q", "-b", "main")
    pinned = _commit(src, "kernel.py", "v1")
    _commit(src, "kernel.py", "v2")
    _commit(src, "README.md", "docs only")
    sha_v1 = hashlib.sha256(b"v1").hexdigest()
    (root / "o" / "bot" / "kernel.pin").write_text(json.dumps({"commit": pinned, "sha256": sha_v1}))
    reg = _registry(tmp_path, pin=[{"pin": "o/bot:kernel.pin", "source": "o/willow:kernel.py"}])

    [row] = current.copies(reg)["pin"]

    assert row["current"] is False and row["pinned"] == pinned[:12]
    assert len(row["since"]) == 1 and row["since"][0].endswith("kernel.py v2"), "only what touched the file"
    (root / "o" / "bot" / "kernel.pin").write_text(json.dumps({"commit": pinned, "sha256": hashlib.sha256(b"v2").hexdigest()}))
    assert current.copies(reg)["pin"][0]["current"] is True


DRY_RUN = """\
=== agriculture-almanac ===
  = scripts/validate.py
  ~ AGENTS.md (would update)
  + docs/new.md

=== climate-almanac ===
  o AGENTS.md (local override — not propagated)
  = scripts/validate.py

Dry run only. Re-run with --apply to write files.
"""


def test_the_engines_own_dry_run_decides_and_an_override_is_never_drift():
    got = current.parse_engine(DRY_RUN)
    assert got["agriculture-almanac"]["update"] == ["AGENTS.md"]
    assert got["agriculture-almanac"]["add"] == ["docs/new.md"]
    assert got["climate-almanac"]["override"] == ["AGENTS.md"]
    assert got["climate-almanac"]["update"] == []


def test_an_engine_report_names_the_verticals_behind_and_keeps_overrides_apart(root, tmp_path, monkeypatch):
    (root / "almanac-data" / ".git").mkdir(parents=True)
    monkeypatch.setattr(current, "_run_dry", lambda where, cmd: DRY_RUN)
    reg = _registry(tmp_path, engine=[{"workspace": "almanac-data/almanac-data", "dry_run": ["x"]}])
    [eng] = current.copies(reg)["engine"]
    assert eng["behind"] == {"agriculture-almanac": {"update": ["AGENTS.md"], "add": ["docs/new.md"]}}
    assert eng["overrides"] == {"climate-almanac": ["AGENTS.md"]}


def test_a_scheduled_workflow_is_stale_failed_never_run_or_unknown_per_repo(root, tmp_path, monkeypatch):
    (root / "almanac-data" / ".git").mkdir(parents=True)
    dry = "".join(f"=== {v} ===\n  = x\n" for v in ("fresh", "old", "red", "never", "down"))
    monkeypatch.setattr(current, "_run_dry", lambda where, cmd: dry)
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)

    def newest(repo, workflow):
        name = repo.split("/")[1]
        if name == "down":
            raise RuntimeError("GitHub App not configured")
        return {"fresh": {"created_at": "2026-10-30T11:00:00Z", "conclusion": "success"},
                "old": {"created_at": "2026-10-25T11:00:00Z", "conclusion": "success"},
                "red": {"created_at": "2026-10-30T11:00:00Z", "conclusion": "failure"},
                "never": None}[name]  # fmt: skip

    monkeypatch.setattr(current, "_newest_run", newest)
    reg = _registry(tmp_path, engine=[{"workspace": "almanac-data/almanac-data", "dry_run": ["x"]}],
                    workflow=[{"repos_of": "almanac-data/almanac-data", "workflow": "link-check.yml",
                               "max_age_days": 2}])  # fmt: skip

    runs = {r["repo"].split("/")[1]: r for r in current.copies(reg, now=now)["workflow"][0]["runs"]}

    assert (runs["fresh"]["stale"], runs["fresh"]["failed"]) == (False, False)
    assert runs["old"]["stale"] is True
    assert runs["red"]["failed"] is True
    assert runs["never"]["status"] == "empty"
    assert runs["down"]["status"] == "unreachable"


def _catalog_json(*entries) -> str:
    return json.dumps({"name": "x", "count": len(entries), "entries": list(entries)})


def test_a_catalog_names_only_live_entries_the_probe_got_no_response_from(root, tmp_path, monkeypatch):
    (root / "almanac-data" / ".git").mkdir(parents=True)
    dry = "".join(f"=== {v} ===\n  = x\n" for v in ("energy-almanac", "civic-almanac", "gone-almanac"))
    monkeypatch.setattr(current, "_run_dry", lambda where, cmd: dry)
    _files(root, {
        "almanac-data/energy-almanac:catalog.json": _catalog_json(
            {"id": "down", "status": "live", "observed": {"checked": "2026-10-30", "reachable": False}},
            {"id": "blocked", "status": "live", "observed": {"reachable": True, "http_status": 403}},
            {"id": "unprobed", "status": "live", "observed": {"checked": "2026-10-31", "reachable": None}},
            {"id": "already-dark", "status": "dark", "observed": {"reachable": False}},
            {"id": "static", "status": "frozen", "observed": {"reachable": False}},
        ),
        "almanac-data/civic-almanac:catalog.json": "not json",
    })  # fmt: skip
    reg = _registry(tmp_path, engine=[{"workspace": "almanac-data/almanac-data", "dry_run": ["x"]}],
                    catalog=[{"repos_of": "almanac-data/almanac-data", "path": "catalog.json"}])  # fmt: skip

    [cat] = current.copies(reg)["catalog"]
    rows = {v["repo"].split("/")[1]: v for v in cat["verticals"]}

    # A 403 is a response; an unprobed entry is unknown; dark is already decided.
    assert [e["id"] for e in rows["energy-almanac"]["unreached"]] == ["down", "static"]
    assert rows["energy-almanac"]["entries"] == 5
    assert rows["civic-almanac"]["status"] == "unreachable"
    assert rows["gone-almanac"]["status"] == "unreachable"  # no checkout, never "nothing unreached"


def test_a_catalog_rule_without_its_engine_is_unreachable(tmp_path):
    reg = _registry(tmp_path, catalog=[{"repos_of": "almanac-data/almanac-data"}])
    [cat] = current.copies(reg)["catalog"]
    assert cat["status"] == "unreachable"


def test_a_registry_that_cant_be_read_is_unreachable(tmp_path):
    assert current.copies(tmp_path / "nope.json")["status"] == "unreachable"


def test_the_shipped_registry_names_only_repo_paths_and_known_rules():
    rules = json.loads(current.REGISTRY.read_text(encoding="utf-8"))
    assert set(rules) <= {"_doc", "bytes", "pin", "engine", "workflow", "catalog"}
    for rule in rules["bytes"]:
        for ref in (rule["copy"], rule["source"]):
            repo, path = ref.split(":", 1)
            assert repo.count("/") == 1 and path and not path.startswith("/")
