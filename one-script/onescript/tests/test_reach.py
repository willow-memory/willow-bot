"""The one reach (flatten-2 F6.5): where code outside willow-bot finds the one
script, and one bootstrap file every consumer carries byte for byte.

``onescript.reach`` is the reach; ``reach_bootstrap.py`` is the template of the
small file each consuming repo carries to find it. The operator: "Using the
onescript at every path."
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from onescript import reach, states

ONE = Path(__file__).resolve().parents[2]  # willow-bot/one-script
TEMPLATE = ONE / "onescript" / "reach_bootstrap.py"
REPOS = ONE.parents[1]  # the directory holding willow-bot

#: every consuming repo's copy of the template (repo, path inside it).
COPIES = [
    ("willows-grove", "grove/onescript_reach.py"),
    ("safe-app-store", "apps/the-table/the_table/onescript_reach.py"),
    ("safe-app-store", "apps/catgrid/catgrid/onescript_reach.py"),
    ("safe-app-store", "apps/oakenscrolls-office/onescript_reach.py"),
    ("willow-mcp", "src/willow_mcp/onescript_reach.py"),
    ("ratatosk", "ratatosk/onescript_reach.py"),
]


def load_copy(path: Path, tmp_path: Path):
    """The bootstrap, imported from a git checkout it sits in (so it can find
    its own place), without putting that checkout on sys.path."""
    spec = importlib.util.spec_from_file_location(f"_bootstrap_{abs(hash(path))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("ONESCRIPT_HOME", raising=False)
    monkeypatch.delenv("ONESCRIPT_GROVE", raising=False)


def fake_one_script(root: Path) -> Path:
    """A one-script tree whose reach is the real one (a copy), with a stub."""
    pkg = root / "onescript"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for name in ("reach.py", "states.py"):
        (pkg / name).write_text((ONE / "onescript" / name).read_text())
    return root


# -- the reach ----------------------------------------------------------------


def test_the_sentences_name_the_variable():
    assert "ONESCRIPT_HOME" in reach.no_onescript(Path("/nowhere"))
    assert "/nowhere" in reach.no_onescript(Path("/nowhere"))
    assert "ONESCRIPT_HOME" in reach.no_onescript(None)
    assert "ONESCRIPT_GROVE" in reach.no_law(Path("/g"))
    assert "ONESCRIPT_GROVE" in reach.no_law(None)


def test_where_prefers_explicit_then_env_then_the_sibling(
    monkeypatch, clean_env, tmp_path
):
    w = reach.where(repos=tmp_path)
    assert w == {
        "onescript": tmp_path / "willow-bot" / "one-script",
        "grove": tmp_path / "willows-grove",
    }
    monkeypatch.setenv("ONESCRIPT_HOME", str(tmp_path / "os"))
    monkeypatch.setenv("ONESCRIPT_GROVE", str(tmp_path / "g"))
    assert reach.where(repos=tmp_path) == {
        "onescript": tmp_path / "os",
        "grove": tmp_path / "g",
    }
    assert reach.where(home=tmp_path / "h")["onescript"] == tmp_path / "h"


def test_no_place_to_look_is_unreachable_never_a_guess(clean_env):
    assert reach.where() == {"onescript": None, "grove": None}
    got = reach.reach()
    assert got["state"] == states.UNREACHABLE and "ONESCRIPT_HOME" in got["why"]
    with pytest.raises(reach.Unreachable, match="ONESCRIPT_HOME"):
        reach.module("states")


def test_reachable_needs_the_law_unless_told_not_to(clean_env, tmp_path):
    home = fake_one_script(tmp_path / "os")
    got = reach.reach(home=home, grove=tmp_path / "no-grove")
    assert got["state"] == "unreachable" and "constitution" in got["why"]
    free = reach.reach(home=home, grove=tmp_path / "no-grove", law=False)
    assert free["state"] == "reachable" and free["onescript"] == home
    law = tmp_path / "g" / "governance"
    law.mkdir(parents=True)
    (law / "CONSTITUTION.md").write_text("law")
    assert reach.reach(home=home, grove=tmp_path / "g")["state"] == "reachable"


def test_a_missing_home_says_where_it_looked(clean_env, tmp_path):
    got = reach.reach(home=tmp_path / "nowhere")
    assert got["state"] == "unreachable" and "nowhere" in got["why"]
    with pytest.raises(reach.Unreachable, match="nowhere"):
        reach.module("stream", home=tmp_path / "nowhere")


def test_module_imports_the_one_scripts_module(clean_env):
    assert reach.module("states", home=ONE) is states


def test_a_module_that_will_not_import_is_unreachable_with_why(clean_env):
    with pytest.raises(reach.Unreachable, match="would not import"):
        reach.module("no_such_module_here", home=ONE)


# -- the bootstrap: one template, copies held to it ----------------------------


def test_the_template_hands_off_to_this_reach(clean_env):
    boot = load_copy(TEMPLATE, ONE)
    assert boot.module("states", home=ONE) is states
    assert boot.reach(home=ONE, law=False)["state"] == "reachable"


def test_the_bootstrap_says_unreachable_with_the_reaches_words(clean_env, tmp_path):
    boot = load_copy(TEMPLATE, ONE)
    gone = tmp_path / "nowhere"
    assert boot.reach(home=gone)["why"] == reach.no_onescript(gone)
    with pytest.raises(boot.Unreachable) as e:
        boot.module("states", home=gone)
    assert str(e.value) == reach.no_onescript(gone)
    assert boot.ENV_HOME == reach.ENV_HOME and boot.ENV_GROVE == reach.ENV_GROVE


def test_the_bootstrap_names_the_variable_when_it_has_no_checkout(
    clean_env, tmp_path, monkeypatch
):
    """An installed copy (in no git checkout), no env: nowhere to look."""
    lone = tmp_path / "site-packages" / "onescript_reach.py"
    lone.parent.mkdir()
    lone.write_bytes(TEMPLATE.read_bytes())
    boot = load_copy(lone, tmp_path)
    assert boot.where() == {"onescript": None, "grove": None}
    got = boot.reach()
    assert got["state"] == "unreachable" and got["why"] == reach.no_onescript(None)
    assert "ONESCRIPT_HOME" in got["why"]


def test_the_bootstrap_looks_beside_its_checkout(clean_env, tmp_path):
    work = tmp_path / "repos" / "consumer"
    (work / ".git").mkdir(parents=True)
    copy = work / "pkg" / "onescript_reach.py"
    copy.parent.mkdir()
    copy.write_bytes(TEMPLATE.read_bytes())
    boot = load_copy(copy, tmp_path)
    assert boot.where() == {
        "onescript": tmp_path / "repos" / "willow-bot" / "one-script",
        "grove": tmp_path / "repos" / "willows-grove",
    }
    got = boot.reach()
    assert got["state"] == "unreachable" and str(tmp_path / "repos") in got["why"]
    fake_one_script(tmp_path / "repos" / "willow-bot" / "one-script")
    assert boot.reach(law=False)["state"] == "reachable"


def test_the_bootstrap_and_the_reach_agree_on_where(clean_env, monkeypatch, tmp_path):
    boot = load_copy(TEMPLATE, ONE)
    monkeypatch.setenv("ONESCRIPT_HOME", str(tmp_path / "os"))
    monkeypatch.setenv("ONESCRIPT_GROVE", str(tmp_path / "g"))
    assert boot.where() == reach.where()
    monkeypatch.delenv("ONESCRIPT_HOME")
    monkeypatch.delenv("ONESCRIPT_GROVE")
    assert boot.where(home=tmp_path / "h", grove=tmp_path / "x") == reach.where(
        home=tmp_path / "h", grove=tmp_path / "x"
    )


def _sibling(repo: str, rel: str) -> Path:
    base = REPOS
    if repo == "willows-grove" and os.environ.get("ONESCRIPT_GROVE"):
        base = Path(os.environ["ONESCRIPT_GROVE"]).parent
    return base / repo / rel


@pytest.mark.parametrize(("repo", "rel"), COPIES, ids=[c[1] for c in COPIES])
def test_every_consumers_copy_is_the_template_byte_for_byte(repo, rel):
    """A copy that drifted would find the one script a different way."""
    copy = _sibling(repo, rel)
    if not copy.is_file():
        if os.environ.get("CI") and (_sibling(repo, "")).is_dir():
            pytest.fail(f"{repo} is beside but has no {rel}")
        pytest.skip(f"{repo} is not beside willow-bot (or has no {rel} yet)")
    assert copy.read_bytes() == TEMPLATE.read_bytes(), (
        f"{repo}/{rel} drifted from onescript/reach_bootstrap.py; "
        "edit the template and copy it out"
    )


def test_the_template_imports_only_the_standard_library():
    import ast

    names = set()
    for node in ast.walk(ast.parse(TEMPLATE.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names
