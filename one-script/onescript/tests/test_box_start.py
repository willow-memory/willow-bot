"""The box starts right after the first bite: the tests gate asks the bot's
venv, the Forge is found from a clone, a check-in with no index builds one, and
the CLI's repo set is the clones beside willow-bot. Each guard has a test that
attempts what it forbids."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, boot, seed  # noqa: E402
from onescript.__main__ import _siblings as real_siblings  # noqa: E402
from onescript.tests.test_api import cfg, rows  # noqa: E402,F401
from onescript.tests.test_seed import index_into_box  # noqa: E402
from onescript.tests.test_store import BITE, DOCS, box  # noqa: E402,F401
from onescript.tests.test_xref import make  # noqa: E402

VENV_PY = "/venv/bin/python"


def suite(tmp_path, **extra) -> dict:
    return {
        "name": "onescript",
        "argv": [VENV_PY, "-m", "pytest"],
        "cwd": str(tmp_path),
        "needs": ["pytest"],
        "python": VENV_PY,
        **extra,
    }


# ── A: the tests gate asks the venv's interpreter ────────────────────────────
def test_a_module_this_process_has_is_not_the_venvs(tmp_path):
    """pytest is importable here; the venv says it isn't. The gate believes the
    venv and never runs the suite."""
    ran = []

    def runner(argv, cwd, timeout=600, env=None):
        ran.append(argv)
        return 0, "pytest"  # the probe: pytest is missing in the venv

    (row,) = boot.tests_gate([suite(tmp_path)], runner=runner)
    assert (row["verdict"], row["why"]) == (
        "failing",
        "can't run on the box: can't run: needs ['pytest'] in the venv",
    )
    assert [a[:2] for a in ran] == [[VENV_PY, "-c"]]  # the probe alone ran


def test_a_venv_with_its_needs_runs_the_suite_in_the_venv(tmp_path):
    ran = []

    def runner(argv, cwd, timeout=600, env=None):
        ran.append(argv)
        return 0, "-" if argv[1] == "-c" else "3 passed"

    (row,) = boot.tests_gate([suite(tmp_path)], runner=runner)
    assert row["verdict"] == "satisfied" and row["result"] == "3 passed"
    assert ran[-1] == [VENV_PY, "-m", "pytest"]


def test_a_venv_that_wont_start_is_missing_everything(tmp_path):
    def runner(argv, cwd, timeout=600, env=None):
        return 127, "no such file"

    (row,) = boot.tests_gate([suite(tmp_path)], runner=runner)
    assert row["verdict"] == "failing" and "needs ['pytest']" in row["why"]


def test_the_gate_cfg_runs_the_suite_in_the_venv_when_there_is_one(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(cli.NESTED, raising=False)
    bare = cli._gate_cfg(False, "", tmp_path)["tests"][0]
    assert bare["argv"][0] == sys.executable and "python" not in bare
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "python").write_text("")
    got = cli._gate_cfg(False, "", tmp_path)["tests"][0]
    py = str(tmp_path / "bin" / "python")
    assert got["argv"][0] == py and got["python"] == py


# ── B: the Forge from a clone ────────────────────────────────────────────────
STUB = """
class EntryError(Exception):
    pass


class _E:
    major, answer, candidates = "game", None, []

    def to_dict(self):
        return {"major": self.major}


def open_bite(sentence, *, project_id, builder_id, responder, box, **kw):
    return _E()
"""


@pytest.fixture
def no_forge(monkeypatch):
    """No Forge imported, and none left behind: a stub imported here must not
    stand in for the real one in a later test."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    saved = {
        n: sys.modules.pop(n) for n in ("forge", "forge.entry") if n in sys.modules
    }
    yield
    for name in ("forge", "forge.entry"):
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def test_a_forge_clone_is_found_without_an_install(box, tmp_path, no_forge):  # noqa: F811
    clone = tmp_path / "forge-clone"
    (clone / "forge").mkdir(parents=True)
    (clone / "forge" / "__init__.py").write_text("")
    (clone / "forge" / "entry.py").write_text(STUB)
    res = api.forge_bite(replace(box["cfg"], forge=clone), BITE)
    assert res["state"] == "populated" and res["entry"]["major"] == "game"


def test_a_place_with_no_engine_changes_nothing(box, tmp_path, monkeypatch, no_forge):  # noqa: F811
    monkeypatch.setitem(sys.modules, "forge", None)  # nothing importable either
    before = list(sys.path)
    (tmp_path / "empty" / "forge").mkdir(parents=True)  # no entry.py
    res = api.forge_bite(replace(box["cfg"], forge=tmp_path / "empty"), BITE)
    assert res["state"] == "unreachable" and "isn't importable" in res["why"]
    assert sys.path == before


# ── C: a check-in with no index builds one ───────────────────────────────────
def test_a_checkin_with_no_index_builds_one_and_attaches(cfg, tmp_path):  # noqa: F811
    repo = make(tmp_path, "docs", DOCS)
    res = api.checkin(replace(cfg, index_repos={"docs": repo}))
    st = res["report"]["store"]
    assert res["code"] == 0 and st["state"] == "attached"
    assert st["indexed"] == ["docs"]
    (x,) = [r for r in rows(cfg) if r["kind"] == "xref"]
    assert x["at"] == "checkin" and [i["repo"] for i in x["inputs"]] == ["docs"]
    assert (cfg.box / seed.INDEX).is_file()


def test_an_index_that_doesnt_hash_is_never_rebuilt_over(cfg, tmp_path):  # noqa: F811
    repo = make(tmp_path, "docs", DOCS)
    index_into_box(cfg, repo)
    with open(cfg.box / seed.INDEX, "ab") as f:
        f.write(b" ")
    tampered = (cfg.box / seed.INDEX).read_bytes()
    res = api.checkin(replace(cfg, index_repos={"docs": repo}))
    assert res["code"] == 1 and res["report"]["hard_close"]  # the record saw it
    assert "store" not in res["report"]
    assert [r.get("at") for r in rows(cfg) if r["kind"] == "xref"] == [None]
    assert (cfg.box / seed.INDEX).read_bytes() == tampered


def test_no_index_repos_is_no_index_as_before(cfg):  # noqa: F811
    st = api.checkin(cfg)["report"]["store"]
    assert (st["state"], st["why"]) == ("unreachable", seed.NO_INDEX)
    assert not [r for r in rows(cfg) if r["kind"] == "xref"]


# ── D: the CLI's repo set ────────────────────────────────────────────────────
def test_siblings_are_the_git_clones_beside(tmp_path):
    for name in ("b", "a"):
        (tmp_path / name / ".git").mkdir(parents=True)
    (tmp_path / "plain").mkdir()
    (tmp_path / "file").write_text("")
    assert real_siblings(tmp_path) == {"a": tmp_path / "a", "b": tmp_path / "b"}
    assert real_siblings(tmp_path / "missing") == {}


def test_the_cli_indexes_the_siblings_unless_repos_are_named(tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(
        api,
        "checkin",
        lambda c, argv=(): got.append(c) or {"code": 0, "report": {}, "nested": False},
    )
    monkeypatch.setattr(cli, "_checkin_screen", lambda rep, nested=False: "")
    monkeypatch.setattr(cli, "_siblings", lambda parent: {"near": Path("/n")})
    base = ["--box", str(tmp_path / "box"), "--keys", str(tmp_path / "k")]
    cli.main([*base, "checkin"])
    cli.main([*base, "--repo", "only=/o", "checkin"])
    assert [c.index_repos for c in got] == [{"near": Path("/n")}, {"only": Path("/o")}]
    assert got[0].repos is None and got[0].forge == cli.FORGE


def test_only_a_missing_index_is_built_never_a_broken_one(cfg, monkeypatch):  # noqa: F811
    """The record already hard-closes a box whose index file changed; this pins
    the store's own door behind it: any why but NO_INDEX builds nothing."""
    built = []
    broken = "the box's index doesn't hash to its pile pointer"
    monkeypatch.setattr(api.seed_mod, "read_index", lambda rec: (None, None, broken))
    monkeypatch.setattr(api, "_index_box", lambda c, run: built.append(c))
    st = api.checkin(replace(cfg, index_repos={"docs": Path("/d")}))["report"]["store"]
    assert (st["state"], st["why"], built) == ("unreachable", broken, [])
