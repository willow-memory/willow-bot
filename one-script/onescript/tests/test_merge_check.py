"""The merge gate (``onescript.merge_check``): each gate has a chunk it passes and
a planted violation it refuses, on small synthetic repos under tmp_path (fast,
hermetic: the real repos are never read). The operator: "These are the tests
that a chunk of code can be merged into it cleanly, and the one script will
still do the job the user ... is asking it to do."

The synthetic world is a kernel (a stub ``onescript`` whose CLI answers
checkin/lookup/prompt/checkout), willows-grove, willow-bot's ``willow_bot``
package, willow-mcp with a trust closure test, and a git-backed safe-app-store.
Gate 2's box check is also run once against the real ``Record``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import merge_check as mc  # noqa: E402
from onescript.record import Record, Verified  # noqa: E402

ONE_SCRIPT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _gate_logic_runs_in_place(monkeypatch):
    """These tests exercise the gates' logic on synthetic worlds; where the suite
    itself runs as root they must not each copy a venv. The unprivileged run has
    its own tests below (``test_nonroot_*``), which set the euid themselves."""
    monkeypatch.setattr(mc, "_euid", lambda: 1000)


def put(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


MAIN_STUB = """
    import json, sys
    args = sys.argv[1:]
    verb = [a for a in args if a in ("checkin", "lookup", "prompt", "checkout")][0]
    if verb == "lookup":
        print(json.dumps({"hits": []}))
    elif verb == "prompt":
        sys.stdin.read()
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "x"}}))
    else:
        print(verb.upper())
"""

WORLD = {
    # the kernel
    "willow-bot/one-script/onescript/__init__.py": "",
    "willow-bot/one-script/onescript/__main__.py": MAIN_STUB,
    "willow-bot/one-script/onescript/canon.py": """
        def canonical(x):
            return repr(x)

        def h256(x):
            return canonical(x)
    """,
    "willow-bot/one-script/onescript/tests/test_ok.py": "def test_ok():\n    assert True\n",
    "willow-bot/one-script/onescript/tests/test_canon.py": """
        from onescript import canon

        def test_golden():
            assert canon.canonical([1, 2]) == "[1, 2]"
    """,
    "willow-bot/one-script/onescript/tests/hidden_inputs_fixture.py": """
        from onescript import canon

        if __name__ == "__main__":
            print(canon.canonical({"a": 1}))
    """,
    "willow-bot/one-script/foundation/run_prompt_tests.py": "print('3.12 1 passed, 0 failed')\n",
    "willow-bot/willow_bot/__init__.py": "x = 1\n",
    "willows-grove/governance/CONSTITUTION.md": "# law\n",
    # willow-mcp: an import copy, a named copy, a trust closure
    "willow-mcp/src/willow_mcp/__init__.py": "",
    "willow-mcp/src/willow_mcp/onescript_reach.py": "class Unreachable(RuntimeError):\n    pass\n",
    "willow-mcp/src/willow_mcp/bound_receipt.py": """
        from . import onescript_reach

        def canonical(x):
            return onescript_reach.Unreachable
    """,
    "willow-mcp/src/willow_mcp/net_signer.py": "def canonical(x):\n    return repr(x)\n",
    "willow-mcp/src/willow_mcp/paths.py": "HOME = '/x'\n",
    "willow-mcp/tests/test_trust_closure_no_onescript.py": 'SEEDS = ["paths"]\n',
    "willow-mcp/tests/test_bound.py": """
        from willow_mcp import bound_receipt

        def test_bound():
            assert bound_receipt.canonical(1)
    """,
    "willow-mcp/tests/test_parity.py": """
        from willow_mcp import net_signer

        def test_parity():
            assert net_signer.canonical([1, 2]) == "[1, 2]"
    """,
    "willow-mcp/tests/test_absent.py": """
        def test_unreachable_is_said():
            # no kernel beside: the answer is unreachable, with a reason
            assert "unreachable"
    """,
}

ATT = {
    "chunk": "canon",
    "author": "agent:willow",
    "verified_by": "sean",
    "home": "onescript/canon.py",
    "goldens": "onescript/tests/test_canon.py",
    "copies": [
        {
            "repo": "willow-mcp",
            "path": "src/willow_mcp/bound_receipt.py",
            "kind": "import",
        },
        {
            "repo": "willow-mcp",
            "path": "src/willow_mcp/net_signer.py",
            "kind": "named-copy",
            "parity": "tests/test_parity.py::test_parity",
            "why": "trust owner",
        },
    ],
    "absent": [
        {"repo": "willow-mcp", "test": "tests/test_absent.py::test_unreachable_is_said"}
    ],
    "never_reach": ["willow-mcp:trust", "safe-app-store", "willow-bot:wheel"],
    "fixture": True,
}


def git(repo: Path, *a: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "w"
    put(root, WORLD)
    store = root / "safe-app-store"
    put(store, {"apps/x.py": "A = 1\n"})
    subprocess.run(["git", "init", "-q", "-b", "master", str(store)], check=True)
    git(store, "add", "-A")
    git(store, "commit", "-q", "-m", "base")
    return root


def pl(world: Path, **kw) -> mc.Places:
    return mc.Places(world, world / "willow-bot" / "one-script", **kw)


def att(**over) -> dict:
    a = json.loads(json.dumps(ATT))
    a.update(over)
    return a


def write_att(world: Path, a: dict, name="merge.json") -> Path:
    p = world / name
    p.write_text(json.dumps(a), encoding="utf-8")
    return p


def kernel_put(world, files):
    put(world / "willow-bot" / "one-script", files)


def mcp_put(world, files):
    put(world / "willow-mcp", files)


def run(world, n, a=None, **kw):
    """Run gate ``n`` (1-based) on the world; returns (ok, detail)."""
    a = a or att()
    p = pl(world)
    fn = {
        1: lambda: mc._gate1(a, p, kw.get("skip_suite", False)),
        2: lambda: mc._gate2(a, p, kw.get("box"), kw.get("anchor")),
        3: lambda: mc._gate3(a, p),
        4: lambda: mc._gate4(a, p),
        5: lambda: mc._gate5(a, p),
        6: lambda: mc._gate6(a, p),
        7: lambda: mc._gate7(a, p),
        8: lambda: mc._gate8(a, p),
    }[n]
    try:
        g, ok, detail = fn()
    finally:
        p.close()  # the unprivileged copy, if the gate made one
    assert g == mc.GATE_NAMES[n - 1]
    return ok, detail


# -- the attestation ----------------------------------------------------------


def test_a_missing_or_malformed_attestation_fails_closed(world):
    r = mc.check(world / "nope.json", pl(world))
    assert [x[1] for x in r] == [False] and "fail-closed" in r[0][2]
    bad = world / "bad.json"
    bad.write_text("{not json")
    assert mc.check(bad, pl(world))[0][1] is False
    for broken in (
        {**ATT, "fixture": "yes"},
        {**ATT, "copies": "x"},
        {**ATT, "chunk": "../x"},
    ):
        r = mc.check(write_att(world, broken), pl(world))
        assert len(r) == 1 and r[0][1] is False


# -- gate 1: the job still runs ----------------------------------------------


def test_gate1_passes_and_says_the_nested_run_skipped_tests(world):
    ok, d = run(world, 1)
    assert ok, d
    assert "--no-tests" in d and "nested" in d


def test_gate1_refuses_a_failing_suite(world):
    kernel_put(
        world, {"onescript/tests/test_ok.py": "def test_ok():\n    assert False\n"}
    )
    ok, d = run(world, 1)
    assert not ok and "suite" in d


def test_gate1_refuses_a_job_that_no_longer_checks_in(world):
    kernel_put(world, {"onescript/__main__.py": "import sys\nsys.exit(3)\n"})
    ok, d = run(world, 1)
    assert not ok and "checkin exit 3" in d


def test_gate1_refuses_a_checkout_that_refuses(world):
    kernel_put(
        world,
        {
            "onescript/__main__.py": MAIN_STUB
            + "    if verb == 'checkout':\n        print('refused: x')\n"
        },
    )
    ok, d = run(world, 1)
    assert not ok and "refused" in d


def test_gate1_skip_suite_fails_as_not_run(world):
    ok, d = run(world, 1, skip_suite=True)
    assert not ok and "not run" in d


# -- gate 2: no persisted byte moves ------------------------------------------


def test_gate2_passes_with_green_goldens(world):
    ok, d = run(world, 2)
    assert ok, d


def test_gate2_refuses_a_golden_that_moved(world):
    kernel_put(
        world,
        {
            "onescript/tests/test_canon.py": """
        from onescript import canon

        def test_golden():
            assert canon.canonical([1, 2]) == "[1,2]"
    """
        },
    )
    ok, d = run(world, 2)
    assert not ok and "goldens" in d


def test_gate2_refuses_missing_empty_and_skipped_goldens(world):
    assert not run(world, 2, att(goldens="onescript/tests/nope.py"))[0]
    kernel_put(world, {"onescript/tests/test_none.py": "X = 1\n"})
    ok, d = run(world, 2, att(goldens="onescript/tests/test_none.py"))
    assert not ok and "no test" in d
    kernel_put(
        world,
        {
            "onescript/tests/test_skip.py": """
        import pytest

        def test_g():
            pytest.skip("later")

        def test_h():
            assert True
    """
        },
    )
    ok, d = run(world, 2, att(goldens="onescript/tests/test_skip.py"))
    assert not ok and "skipped" in d
    assert not run(world, 2, att(goldens="../escape.py"))[0]


def _stub_record(world, breaks):
    kernel_put(
        world,
        {
            "onescript/record.py": """
        import json

        class Record:
            def __init__(self, box, clock, version, token, anchor=None):
                self.box = box

            def verify_chain(self):
                return json.loads((self.box / "breaks.json").read_text())

            def verify_anchor(self, human_key=None):
                return []
    """
        },
    )
    box = world / "box"
    box.mkdir(exist_ok=True)
    (box / "record.jsonl").write_text("")
    (box / "breaks.json").write_text(json.dumps(breaks))
    return box


def test_gate2_with_a_box_needs_an_empty_chain(world):
    assert run(world, 2, box=_stub_record(world, []))[0]
    ok, d = run(world, 2, box=_stub_record(world, ["row 3: chain broken"]))
    assert not ok and "chain broken" in d
    ok, d = run(world, 2, box=world / "nobox")
    assert not ok and "record.jsonl" in d


def test_gate2_box_check_runs_on_the_real_record(tmp_path):
    tok = object()
    box = tmp_path / "box"
    box.mkdir(exist_ok=True)
    rec = Record(box, lambda: "2026-01-01T00:00:00Z", "v", tok)
    rec.append("note", Verified("agent", "x", tok), where="t")
    rec.append("note", Verified("agent", "x", tok), where="t")
    a = att(goldens="onescript/tests/test_canon.py", home="onescript/canon.py")
    real = mc.Places(tmp_path, ONE_SCRIPT)
    assert mc._gate2(a, real, box, None)[1] is True
    lines = (box / "record.jsonl").read_text().splitlines()
    (box / "record.jsonl").write_text(
        lines[0].replace("note", "nope") + "\n" + lines[1] + "\n"
    )
    g, ok, d = mc._gate2(a, real, box, None)
    assert not ok and "breaks" in d


# -- gate 3: callers see no change --------------------------------------------


def test_gate3_passes_with_parity_and_importing_tests(world):
    ok, d = run(world, 3)
    assert ok, d
    assert "willow-mcp" in d


def test_gate3_refuses_a_parity_test_that_fails(world):
    mcp_put(
        world,
        {
            "tests/test_parity.py": """
        from willow_mcp import net_signer

        def test_parity():
            assert net_signer.canonical([1, 2]) == "[1,2]"
    """
        },
    )
    ok, d = run(world, 3)
    assert not ok and "parity" in d


def test_gate3_refuses_a_caller_test_that_fails(world):
    mcp_put(
        world,
        {
            "tests/test_bound.py": """
        from willow_mcp import bound_receipt

        def test_bound():
            assert not bound_receipt.canonical(1)
    """
        },
    )
    ok, d = run(world, 3)
    assert not ok and "willow-mcp tests" in d


def test_gate3_refuses_a_skipped_parity_test(world):
    mcp_put(
        world,
        {
            "tests/test_parity.py": """
        import pytest

        def test_parity():
            pytest.skip("no sibling")
    """
        },
    )
    ok, d = run(world, 3)
    assert not ok and "skipped" in d


def test_gate3_a_repo_not_beside_fails_never_skips(world):
    a = att()
    a["copies"][0]["repo"] = "ratatosk"
    ok, d = run(world, 3, a)
    assert not ok and "ratatosk" in d and "not beside" in d


def test_gate3_an_import_copy_no_test_imports_is_unverified(world):
    mcp_put(world, {"tests/test_bound.py": "def test_bound():\n    assert True\n"})
    ok, d = run(world, 3)
    assert not ok and "no test in the repo imports bound_receipt" in d


def test_gate3_a_named_copy_without_parity_fails(world):
    a = att()
    del a["copies"][1]["parity"]
    ok, d = run(world, 3, a)
    assert not ok and "no parity" in d


def test_gate3_no_copies_is_unverified(world):
    assert not run(world, 3, att(copies=[]))[0]


# -- gate 4: one copy, or a named copy -----------------------------------------


def test_gate4_passes(world):
    ok, d = run(world, 4)
    assert ok, d


def test_gate4_refuses_an_import_copy_that_defines_the_rule_locally(world):
    mcp_put(
        world,
        {
            "src/willow_mcp/bound_receipt.py": """
        from . import onescript_reach

        def canonical(x):
            return repr(x)

        def other():
            return onescript_reach.Unreachable
    """
        },
    )
    ok, d = run(world, 4)
    assert not ok and "defines the rule locally: canonical" in d


def test_gate4_a_local_rule_named_by_the_attestation_is_refused_too(world):
    mcp_put(
        world,
        {
            "src/willow_mcp/bound_receipt.py": """
        from . import onescript_reach

        def mine(x):
            return repr(x)
    """
        },
    )
    assert run(world, 4)[0]
    ok, d = run(world, 4, att(rule_names=["mine"]))
    assert not ok and "mine" in d


def test_gate4_a_wrapper_into_the_reach_is_not_a_copy(world):
    mcp_put(
        world,
        {
            "src/willow_mcp/bound_receipt.py": """
        from . import onescript_reach

        _canon = onescript_reach.Unreachable

        def canonical(x):
            return _canon
    """
        },
    )
    assert run(world, 4)[0]


def test_gate4_refuses_an_import_copy_that_imports_no_reach(world):
    mcp_put(
        world, {"src/willow_mcp/bound_receipt.py": "import json\n\nX = json.dumps(1)\n"}
    )
    ok, d = run(world, 4)
    assert not ok and "imports no onescript_* reach" in d


def test_gate4_refuses_a_named_copy_without_why_parity_or_a_real_parity_test(world):
    a = att()
    del a["copies"][1]["why"]
    assert "needs a 'why'" in run(world, 4, a)[1]
    a = att()
    del a["copies"][1]["parity"]
    assert "parity" in run(world, 4, a)[1]
    a = att()
    a["copies"][1]["parity"] = "tests/test_parity.py::test_gone"
    ok, d = run(world, 4, a)
    assert not ok and "defines no test_gone" in d


def test_gate4_unknown_kind_and_missing_files_fail(world):
    a = att()
    a["copies"][0]["kind"] = "vendored"
    assert not run(world, 4, a)[0]
    a = att()
    a["copies"][0]["path"] = "src/willow_mcp/nope.py"
    assert not run(world, 4, a)[0]
    a = att()
    a["copies"][0]["repo"] = "ratatosk"
    assert "not beside" in run(world, 4, a)[1]


def test_gate4_a_kernel_internal_copy_may_import_home_directly(world):
    kernel_put(
        world, {"onescript/user.py": "from . import canon\n\nY = canon.h256(1)\n"}
    )
    a = att(
        copies=[
            {
                "repo": "willow-bot",
                "path": "one-script/onescript/user.py",
                "kind": "import",
            }
        ]
    )
    assert run(world, 4, a)[0]
    kernel_put(world, {"onescript/user.py": "def canonical(x):\n    return x\n"})
    assert not run(world, 4, a)[0]


# -- gate 5: absence is said ----------------------------------------------------


def test_gate5_passes(world):
    ok, d = run(world, 5)
    assert ok, d


def test_gate5_refuses_a_missing_absent_test(world):
    a = att()
    a["absent"][0]["test"] = "tests/test_absent.py::test_gone"
    ok, d = run(world, 5, a)
    assert not ok and "test_gone" in d


def test_gate5_an_import_repo_with_no_absent_test_fails(world):
    ok, d = run(world, 5, att(absent=[]))
    assert not ok and "willow-mcp" in d


def test_gate5_refuses_a_test_that_never_says_unreachable(world):
    mcp_put(
        world,
        {
            "tests/test_absent.py": "def test_unreachable_is_said():\n    assert True\n".replace(
                "unreachable_is_said", "quiet"
            )
        },
    )
    a = att()
    a["absent"][0]["test"] = "tests/test_absent.py::test_quiet"
    ok, d = run(world, 5, a)
    assert not ok and "never says unreachable" in d


def test_gate5_refuses_a_failing_or_skipped_absent_test(world):
    mcp_put(
        world,
        {
            "tests/test_absent.py": """
        def test_unreachable_is_said():
            assert not "unreachable"
    """
        },
    )
    assert not run(world, 5)[0]
    mcp_put(
        world,
        {
            "tests/test_absent.py": """
        import pytest

        def test_unreachable_is_said():
            pytest.skip("unreachable")
    """
        },
    )
    ok, d = run(world, 5)
    assert not ok and "skipped" in d


def test_gate5_a_repo_not_beside_fails(world):
    a = att()
    a["absent"].append({"repo": "ratatosk", "test": "tests/t.py::test_x"})
    ok, d = run(world, 5, a)
    assert not ok and "ratatosk" in d


# -- gate 6: boundaries hold -----------------------------------------------------


def test_gate6_passes(world):
    ok, d = run(world, 6)
    assert ok, d
    assert "seeds read from tests/test_trust_closure_no_onescript.py" in d


def test_gate6_trust_refuses_a_lazy_reach_import(world):
    mcp_put(
        world,
        {
            "src/willow_mcp/paths.py": """
        def where():
            from . import onescript_reach
            return onescript_reach
    """
        },
    )
    ok, d = run(world, 6)
    assert not ok and "paths" in d and "willow-mcp:trust" in d


def test_gate6_trust_follows_imports_and_import_module(world):
    mcp_put(
        world,
        {
            "src/willow_mcp/paths.py": "from . import helper\n",
            "src/willow_mcp/helper.py": "import importlib\n\ndef f():\n    return importlib.import_module('willow_mcp.onescript_reach')\n",
        },
    )
    ok, d = run(world, 6)
    assert not ok and "helper" in d


def test_gate6_trust_a_step_under_install_steps_is_a_seed(world):
    mcp_put(
        world,
        {
            "src/willow_mcp/install_steps/__init__.py": "",
            "src/willow_mcp/install_steps/step.py": "from .. import onescript_reach\n",
        },
    )
    ok, d = run(world, 6)
    assert not ok and "install_steps" in d


def test_gate6_trust_seeds_fall_back_to_the_briefs_and_say_so(world):
    (world / "willow-mcp/tests/test_trust_closure_no_onescript.py").unlink()
    ok, d = run(world, 6)
    assert (
        not ok and "seeds not found" in d
    )  # the brief's seeds are not in this tiny repo
    for s in mc.BRIEF_TRUST_SEEDS:
        mcp_put(world, {f"src/willow_mcp/{s}.py": "X = 1\n"})
    ok, d = run(world, 6)
    assert ok and "seeds from the brief" in d


def test_gate6_a_seed_that_is_gone_fails(world):
    mcp_put(
        world,
        {"tests/test_trust_closure_no_onescript.py": 'SEEDS = ["paths", "gone"]\n'},
    )
    ok, d = run(world, 6)
    assert not ok and "gone" in d


def test_gate6_wheel_refuses_a_willow_bot_package_that_imports_the_one_script(world):
    put(
        world / "willow-bot",
        {"willow_bot/late.py": "def f():\n    import onescript.canon\n"},
    )
    ok, d = run(world, 6)
    assert not ok and "willow-bot:wheel" in d and "late.py" in d


def test_gate6_safe_app_store_refuses_a_changed_copy(world):
    a = att()
    a["copies"].append(
        {
            "repo": "safe-app-store",
            "path": "apps/x.py",
            "kind": "named-copy",
            "parity": "p::t",
            "why": "parts bin",
        }
    )
    ok, d = run(world, 6, a)
    assert ok, d
    (world / "safe-app-store/apps/x.py").write_text("A = 2\n")
    ok, d = run(world, 6, a)
    assert not ok and "differs from master" in d
    a["copies"][-1]["kind"] = "import"
    assert "import copy" in run(world, 6, a)[1]


def test_gate6_a_boundary_repo_not_beside_fails(world):
    import shutil

    shutil.rmtree(world / "safe-app-store")
    ok, d = run(world, 6)
    assert not ok and "safe-app-store" in d and "not beside" in d


def test_gate6_unknown_or_empty_never_reach_fails(world):
    assert not run(world, 6, att(never_reach=["somewhere"]))[0]
    assert not run(world, 6, att(never_reach=[]))[0]


# -- gate 7: same input, same output -----------------------------------------------


def test_gate7_passes(world):
    ok, d = run(world, 7)
    assert ok, d
    assert "identical" in d


def test_gate7_refuses_a_fixture_that_reads_a_hidden_input(world):
    kernel_put(
        world,
        {
            "onescript/tests/hidden_inputs_fixture.py": """
        from onescript import canon

        if __name__ == "__main__":
            print(canon.canonical({"a": 1}), hash("x"))
    """
        },
    )
    ok, d = run(world, 7)
    assert not ok and "differ" in d


def test_gate7_refuses_a_fixture_that_does_not_name_the_home_module(world):
    kernel_put(world, {"onescript/tests/hidden_inputs_fixture.py": 'print("canon")\n'})
    ok, d = run(world, 7)
    assert not ok and "does not name canon" in d


def test_gate7_fixture_false_fails_unless_attested(world):
    ok, d = run(world, 7, att(fixture=False))
    assert not ok and "why_not_fixture" in d
    ok, d = run(
        world, 7, att(fixture=False, why_not_fixture="it does arithmetic on nothing")
    )
    assert ok and "[A]" in d


def test_gate7_a_fixture_that_fails_to_run_fails(world):
    kernel_put(
        world,
        {
            "onescript/tests/hidden_inputs_fixture.py": "from onescript import canon\nraise SystemExit(4)\n"
        },
    )
    assert not run(world, 7)[0]


# -- gate 8: the human seals ---------------------------------------------------------


def test_gate8_passes(world):
    ok, d = run(world, 8)
    assert ok, d


@pytest.mark.parametrize("who", ["", "UNSET", "unset", "agent:willow", "Agent:Willow "])
def test_gate8_refuses_an_unset_or_same_hand_verifier(world, who):
    ok, d = run(world, 8, att(verified_by=who))
    assert not ok


SELF_SEALS = {
    "standing kw": "def f(rec):\n    rec.append('x', standing='sealed')\n",
    "standing dict": "ROW = {'kind': 'x', 'standing': 'sealed'}\n",
    "standing ifexp": "ROW = {'standing': 'sealed' if 1 else 'unattested'}\n",
    "standing assign": "def f(row):\n    row['standing'] = 'sealed'\n",
    "seal row dict": "ROW = {'kind': 'seal', 'who': 'human'}\n",
    "seal row append": "def f(rec, who):\n    rec.append('seal', who, subject='s')\n",
    "seal row kw": "def f(rec):\n    rec.append(kind='seal', who='human')\n",
    "seal verb": "def f(api, cfg):\n    return api.seal_scope(cfg)\n",
    "bare seal verb": "def f(x):\n    return writeback_seal(x)\n",
    "nestor seal": "def f(store):\n    store.seal('x')\n",
}


@pytest.mark.parametrize("name", sorted(SELF_SEALS))
def test_gate8_refuses_a_chunk_that_seals_itself(world, name):
    kernel_put(world, {"onescript/canon.py": SELF_SEALS[name]})
    ok, d = run(world, 8)
    assert not ok and "seals itself" in d, d


def test_gate8_a_chunk_that_only_reads_seals_passes(world):
    kernel_put(
        world,
        {
            "onescript/canon.py": (
                "def f(rows):\n    return [r for r in rows if r['standing'] == 'sealed' and r['kind'] == 'seal']\n"
            )
        },
    )
    assert run(world, 8)[0]


# -- the whole, and --record -----------------------------------------------------------


def full(world, a=None, **kw):
    p = write_att(world, a or att())
    return mc.check(p, pl(world), **kw), p


def test_a_chunk_that_clears_every_gate_is_mergeable_and_records(world):
    results, p = full(world)
    assert [g for g, _, _ in results] == mc.GATE_NAMES
    assert all(ok for _, ok, _ in results), results
    out, why = mc.record_merge(p, results, pl(world))
    assert out is not None, why
    rec = json.loads(out.read_text())
    assert out == world / "willow-bot/one-script/merged/canon.json"
    assert rec["verdict"] == "MERGEABLE" and rec["verified_by"] == "sean"
    assert [x["gate"] for x in rec["gates"]] == mc.GATE_NAMES
    again, why = mc.record_merge(p, results, pl(world))
    assert again is None and "already exists" in why


@pytest.mark.parametrize("who", ["agent:willow", "UNSET", ""])
def test_verified_by_the_author_or_unset_writes_nothing(world, who):
    a = att(verified_by=who)
    results, p = full(world, a)
    assert not results[7][1]  # gate 8 says so
    # and the writer refuses on its own, even handed a list that all passed
    allok = [(g, True, "forced") for g in mc.GATE_NAMES]
    out, why = mc.record_merge(p, allok, pl(world))
    assert out is None and "must be set and differ" in why
    assert not (world / "willow-bot/one-script/merged").exists()


def test_a_failed_gate_writes_nothing(world):
    kernel_put(world, {"onescript/canon.py": SELF_SEALS["seal verb"]})
    results, p = full(world)
    assert not all(ok for _, ok, _ in results)
    out, why = mc.record_merge(p, results, pl(world))
    assert out is None and "NOT MERGEABLE" in why
    assert not (world / "willow-bot/one-script/merged").exists()


def test_a_skipped_gate_never_records(world):
    results, p = full(world, skip_suite=True)
    assert results[0][1] is False and "not run" in results[0][2]
    assert all(ok for _, ok, _ in results[1:]), results
    out, why = mc.record_merge(p, results, pl(world))
    assert out is None
    assert not (world / "willow-bot/one-script/merged").exists()


def test_a_short_gate_list_never_records(world):
    p = write_att(world, att())
    out, why = mc.record_merge(p, [(g, True, "") for g in mc.GATE_NAMES[:7]], pl(world))
    assert out is None


def test_a_gate_that_crashes_fails_rather_than_passes(world, monkeypatch):
    def boom(*a):
        raise RuntimeError("x")

    monkeypatch.setattr(mc, "_gate4", boom)
    results, _ = full(world, skip_suite=True)
    assert results[3][1] is False and "unverified" in results[3][2]


# -- the verb ---------------------------------------------------------------------------


def test_the_verb_prints_json_and_records_only_on_a_pass(world, capsys):
    p = write_att(world, att())
    rc = cli.main(["merge-check", str(p), "--json", "--record", "--repos", str(world)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["mergeable"] is True
    assert out["record"]["written"].endswith("merged/canon.json")
    p = write_att(world, att(chunk="other", verified_by="agent:willow"), "o.json")
    rc = cli.main(
        [
            "merge-check",
            str(p),
            "--json",
            "--record",
            "--repos",
            str(world),
            "--skip-suite",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["mergeable"] is False and out["record"]["written"] is None
    assert not (world / "willow-bot/one-script/merged/other.json").exists()


def test_the_verb_text_names_each_gate_and_the_verdict(world, capsys):
    p = write_att(world, att(verified_by="UNSET"))
    rc = cli.main(["merge-check", str(p), "--repos", str(world), "--skip-suite"])
    text = capsys.readouterr().out
    assert rc == 1
    for g in mc.GATE_NAMES:
        assert g in text
    assert "NOT MERGEABLE" in text and "[FAIL] 8 human_seals" in text


def test_the_verb_leaves_the_live_box_alone(world, tmp_path, capsys):
    box = tmp_path / "live"
    p = write_att(world, att())
    cli.main(
        [
            "--box",
            str(box),
            "merge-check",
            str(p),
            "--repos",
            str(world),
            "--skip-suite",
        ]
    )
    assert not box.exists()


def test_gate5_a_kernel_internal_copy_needs_no_absent_test(world):
    kernel_put(
        world, {"onescript/user.py": "from . import canon\n\nY = canon.h256(1)\n"}
    )
    a = att(
        copies=[
            {
                "repo": "willow-bot",
                "path": "one-script/onescript/user.py",
                "kind": "import",
            }
        ],
        absent=[],
    )
    ok, d = run(world, 5, a)
    assert ok and "no consumer outside the kernel" in d
    # but a consumer outside it, with no absent test, still fails
    a["copies"].append(
        {
            "repo": "willow-mcp",
            "path": "src/willow_mcp/bound_receipt.py",
            "kind": "import",
        }
    )
    assert not run(world, 5, a)[0]


def test_a_checked_repo_never_inherits_the_callers_pythonpath(world, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/somewhere/else")
    repo = world / "willow-mcp"
    e = pl(world).env(repo=repo)
    assert "/somewhere/else" not in e["PYTHONPATH"].split(":")
    assert e["ONESCRIPT_HOME"] == str(world / "willow-bot" / "one-script")


def test_a_failing_test_is_named_in_the_detail(world):
    mcp_put(
        world,
        {
            "tests/test_parity.py": """
        def test_parity():
            assert False
    """
        },
    )
    ok, d = run(world, 3)
    assert not ok and "FAILED tests/test_parity.py::test_parity" in d


def test_gate4_a_kernel_wrapper_through_the_home_module_is_not_a_copy(world):
    kernel_put(
        world,
        {
            "onescript/user.py": "from .canon import canonical as _c\n\n\ndef canonical(x):\n    return _c(x)\n"
        },
    )
    a = att(
        copies=[
            {
                "repo": "willow-bot",
                "path": "one-script/onescript/user.py",
                "kind": "import",
            }
        ]
    )
    assert run(world, 4, a)[0]
    kernel_put(
        world,
        {
            "onescript/user.py": "from . import canon\n\n\ndef canonical(x):\n    return repr(x)\n"
        },
    )
    ok, d = run(world, 4, a)
    assert not ok and "canonical" in d


def test_gate4_a_lookalike_is_exempt_only_with_a_why(world):
    kernel_put(
        world,
        {
            "onescript/user.py": "from . import canon\n\n\ndef canonical(x):\n    return repr(x)\n"
        },
    )
    look = {
        "repo": "willow-bot",
        "path": "one-script/onescript/user.py",
        "name": "canonical",
    }
    a = att(
        copies=[
            {
                "repo": "willow-bot",
                "path": "one-script/onescript/user.py",
                "kind": "import",
            }
        ],
        lookalikes=[look],
    )
    assert not run(world, 4, a)[0]
    look["why"] = "a different rule that shares a name"
    assert run(world, 4, a)[0]


def test_gate3_only_tests_in_a_tests_directory_count_as_callers(world):
    mcp_put(
        world,
        {
            "scratch/test_x.py": "from willow_mcp import bound_receipt\n\nraise SystemExit(9)\n",
            "tests/test_bound.py": "def test_bound():\n    assert True\n",
        },
    )
    ok, d = run(world, 3)
    assert not ok and "no test in the repo imports bound_receipt" in d


# -- gate 4: a repo-local reach helper (``via``) ------------------------------

HELPER = """
    from . import onescript_reach

    def one_script_module(name):
        return onescript_reach.module(name)
"""
VIA_COPY = """
    from .helper import one_script_module

    _c = one_script_module("canon")

    def canonical(x):
        return _c.canonical(x)
"""


def via_att(**copy_over) -> dict:
    c = {
        "repo": "willow-mcp",
        "path": "src/willow_mcp/viauser.py",
        "kind": "import",
        "via": "src/willow_mcp/helper.py:one_script_module",
    }
    c.update(copy_over)
    return att(copies=[c], absent=[])


def via_world(world, helper=HELPER, copy=VIA_COPY):
    mcp_put(
        world,
        {"src/willow_mcp/helper.py": helper, "src/willow_mcp/viauser.py": copy},
    )


def test_gate4_via_a_helper_that_reaches_passes(world):
    via_world(world)
    ok, d = run(world, 4, via_att())
    assert ok, d
    # and without the via the same copy imports no reach, so it fails
    a = via_att()
    del a["copies"][0]["via"]
    assert not run(world, 4, a)[0]


def test_gate4_via_refuses_a_helper_that_does_not_reach(world):
    via_world(
        world,
        helper="""
        import importlib

        def one_script_module(name):
            return importlib.import_module("canon")
    """,
    )
    ok, d = run(world, 4, via_att())
    assert not ok and "imports no onescript_* reach" in d
    via_world(
        world,
        helper="""
        from . import onescript_reach

        def one_script_module(name):
            return object()
    """,
    )
    ok, d = run(world, 4, via_att())
    assert not ok and "does not reach through onescript_reach" in d


def test_gate4_via_refuses_a_missing_helper_or_function(world):
    via_world(world)
    ok, d = run(world, 4, via_att(via="src/willow_mcp/nope.py:one_script_module"))
    assert not ok and "is not a file" in d
    ok, d = run(world, 4, via_att(via="src/willow_mcp/helper.py:gone"))
    assert not ok and "defines no top-level function gone" in d
    ok, d = run(world, 4, via_att(via="no-colon"))
    assert not ok and "not 'path:function'" in d


def test_gate4_via_refuses_a_copy_that_does_not_import_the_helper(world):
    via_world(
        world,
        copy="""
        def canonical(x):
            return repr(x)
    """,
    )
    ok, d = run(world, 4, via_att())
    assert not ok and "does not import one_script_module" in d


def test_gate4_via_still_refuses_a_local_version_of_the_rule(world):
    via_world(
        world,
        copy="""
        from .helper import one_script_module

        _c = one_script_module("canon")

        def canonical(x):
            return repr(x)
    """,
    )
    ok, d = run(world, 4, via_att())
    assert not ok and "defines the rule locally: canonical" in d


def test_gate4_via_must_be_a_string(world):
    via_world(world)
    r = mc.check(write_att(world, via_att(via=3)), pl(world))
    assert r[0][0] == "attestation" and "'via'" in r[0][2]


# -- running as an unprivileged user -------------------------------------------

import os  # noqa: E402
import pwd  # noqa: E402


def _tmp_jails():
    return sorted(Path("/tmp").glob(mc._JAIL_PREFIX + "*"))


def test_nonroot_the_launcher_command_line():
    which = {"setpriv": "/usr/bin/setpriv", "runuser": "/usr/sbin/runuser"}.get
    got = mc.find_launcher("nobody", 4242, 4343, which=which)
    assert got == [
        "/usr/bin/setpriv",
        "--reuid=4242",
        "--regid=4343",
        "--clear-groups",
        "--",
    ]
    assert mc.jail_command(got, ["py", "-m", "pytest"]) == [*got, "py", "-m", "pytest"]
    only_runuser = {"runuser": "/usr/sbin/runuser"}.get
    got = mc.find_launcher("nobody", 4242, 4343, which=only_runuser)
    assert got == ["/usr/sbin/runuser", "-u", "nobody", "--"]
    with pytest.raises(mc.JailError, match="neither setpriv nor runuser"):
        mc.find_launcher("nobody", 4242, 4343, which=lambda _n: None)


def _root(monkeypatch):
    monkeypatch.setattr(mc, "_euid", lambda: 0)

    def never(*a, **k):
        raise AssertionError("tests ran as root: " + repr(a[:1]))

    monkeypatch.setattr(mc, "_run", never)


def test_nonroot_root_with_no_such_user_fails_closed_and_runs_nothing(
    world, monkeypatch
):
    _root(monkeypatch)
    monkeypatch.setenv("MERGE_CHECK_RUN_AS", "no-such-user-merge-check")
    before = _tmp_jails()
    for n in (3, 5):
        ok, d = run(world, n)
        assert not ok and "cannot drop privileges" in d and "no-such-user" in d, d
    assert _tmp_jails() == before


def test_nonroot_root_with_no_launcher_fails_closed(world, monkeypatch):
    _root(monkeypatch)
    monkeypatch.setattr(mc.shutil, "which", lambda _n: None)
    before = _tmp_jails()
    ok, d = run(world, 5)
    assert not ok and "neither setpriv nor runuser" in d, d
    assert _tmp_jails() == before


def test_nonroot_the_user_may_not_be_root(world, monkeypatch):
    _root(monkeypatch)
    monkeypatch.setenv("MERGE_CHECK_RUN_AS", "root")
    ok, d = run(world, 5)
    assert not ok and "not an unprivileged user" in d


def test_nonroot_a_copy_that_fails_leaves_nothing_behind(world, monkeypatch):
    _root(monkeypatch)
    if not pwd.getpwnam("nobody"):  # pragma: no cover
        pytest.skip("no nobody")
    monkeypatch.setattr(mc.shutil, "which", lambda n: "/usr/bin/" + n)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mc, "_chown_tree", boom)
    before = _tmp_jails()
    ok, d = run(world, 5)
    assert not ok and "cannot prepare the unprivileged copy" in d, d
    assert _tmp_jails() == before


def test_nonroot_not_root_runs_in_place_and_says_its_uid(world, monkeypatch):
    # the autouse fixture's euid is 1000: nothing is copied, nothing dropped
    before = _tmp_jails()
    ok, d = run(world, 5)
    assert ok, d
    assert "as uid 1000" in d or "as uid" in d
    assert _tmp_jails() == before


def test_nonroot_translate_maps_whole_path_segments_only(tmp_path):
    j = mc.Jail(tmp_path / "j", "nobody", 1, 1, ["x"])
    j._add_map("/r/willow-bot", "/j/repos/willow-bot")
    assert (
        j.translate("/r/willow-bot/a:/r/willow-bot")
        == "/j/repos/willow-bot/a:/j/repos/willow-bot"
    )
    assert j.translate("/r/willow-bot-fix/a") == "/r/willow-bot-fix/a"


_CAN_DROP = (
    os.geteuid() == 0
    and mc.shutil.which("setpriv") is not None
    and any(u.pw_name == "nobody" for u in pwd.getpwall())
)


@pytest.mark.skipif(
    not _CAN_DROP, reason="needs a root test process, setpriv and a user 'nobody'"
)
def test_nonroot_a_real_run_is_unprivileged_and_cleans_up(world, monkeypatch):
    """The real thing: the absent test itself asserts it is not root."""
    monkeypatch.setattr(mc, "_euid", os.geteuid)
    mcp_put(
        world,
        {
            "tests/test_absent.py": """
        import os

        def test_unreachable_is_said():
            # no kernel beside: unreachable; and not as root
            assert os.geteuid() != 0 and os.getuid() != 0
            assert os.environ["HOME"].startswith("/tmp/merge-check-nonroot-")
            assert os.environ["PYTHONDONTWRITEBYTECODE"] == "1"
            assert "unreachable"
    """
        },
    )
    before = _tmp_jails()
    a = att()
    r = mc.check(write_att(world, a), pl(world), skip_suite=True)
    by = {g: (ok, d) for g, ok, d in r}
    ok5, d5 = by[mc.GATE_NAMES[4]]
    assert ok5, d5
    assert "uid 65534" in d5 or f"uid {pwd.getpwnam('nobody').pw_uid}" in d5, d5
    ok3, d3 = by[mc.GATE_NAMES[2]]
    assert ok3 and f"uid {pwd.getpwnam('nobody').pw_uid}" in d3, d3
    assert _tmp_jails() == before, "the temp dir is removed"


# -- a named copy whose parity test lives in another repo (``parity_repo``) -------


def device_att(**over) -> dict:
    c = {
        "repo": "device",
        "path": "canon.py",
        "kind": "named-copy",
        "parity": "tests/test_parity.py::test_parity",
        "parity_repo": "willow-mcp",
        "why": "a stdlib-only device app held from the kernel's side",
    }
    c.update(over)
    return att(copies=[c], absent=[])


def test_gates_3_4_parity_repo_holds_a_copy_that_carries_no_test(world):
    put(world / "device", {"canon.py": "def canonical(x):\n    return repr(x)\n"})
    a = device_att()
    for n in (3, 4):
        ok, d = run(world, n, a)
        assert ok, d
    # without the parity_repo the test is looked for in the device repo, and is not there
    a = device_att()
    del a["copies"][0]["parity_repo"]
    for n in (3, 4):
        ok, d = run(world, n, a)
        assert not ok and "not a file in device" in d, d


def test_gates_3_4_parity_repo_refuses_a_missing_repo_or_test(world):
    put(world / "device", {"canon.py": "x = 1\n"})
    for n in (3, 4):
        ok, d = run(world, n, device_att(parity_repo="nowhere"))
        assert not ok and "not beside" in d, d
        ok, d = run(world, n, device_att(parity="tests/test_parity.py::test_gone"))
        assert not ok and "test_gone" in d, d


def test_gate3_parity_repo_still_refuses_a_failing_parity_test(world):
    put(world / "device", {"canon.py": "x = 1\n"})
    mcp_put(world, {"tests/test_parity.py": "def test_parity():\n    assert False\n"})
    ok, d = run(world, 3, device_att())
    assert not ok and "willow-mcp parity" in d, d


def test_parity_repo_must_be_a_string(world):
    r = mc.check(write_att(world, device_att(parity_repo=3)), pl(world))
    assert r[0][0] == "attestation" and "parity_repo" in r[0][2]


def test_gate3_a_symlinked_repo_is_found_and_its_tests_run(world, tmp_path):
    """The calibration repo sits beside the others as a symlink to its clone."""
    clone = tmp_path / "elsewhere" / "device"
    put(
        clone,
        {
            "canon.py": "def canonical(x):\n    return repr(x)\n",
            "tests/test_canon.py": "import canon\n\n\ndef test_it():\n    assert canon.canonical(1) == '1'\n",
        },
    )
    (world / "device").symlink_to(clone)
    p = pl(world)
    p.python["device"] = sys.executable
    g, ok, d = mc._gate3(device_att(), p)
    assert ok, d
    assert "device: 1 passed" in d


@pytest.mark.skipif(
    not _CAN_DROP, reason="needs a root test process, setpriv and a user 'nobody'"
)
def test_nonroot_a_tree_outside_the_repos_is_copied_for_the_user(
    world, tmp_path, monkeypatch
):
    """diverge's throwaway worktrees sit outside the repos; they are copied too.
    The environment is scrubbed: no proxy settings, no CA bundle under /root."""
    monkeypatch.setattr(mc, "_euid", os.geteuid)
    loose = tmp_path / "loose"
    put(
        loose,
        {
            "t.py": "import os\nprint(os.getuid(), os.getcwd())\n"
            "print(sorted(os.environ))\nopen('w', 'w').write('x')\n"
        },
    )
    env = dict(os.environ)
    env.update(
        SSL_CERT_FILE="/root/.ccr/ca-bundle.crt",
        HTTPS_PROXY="http://p:1",
        GH_TOKEN="secret",
        WILLOW_HOME="/root",
        ONESCRIPT_NOW="2026-10-20T12:00:00Z",
    )
    p = pl(world)
    try:
        rc, out, uid = p.run_tests([sys.executable, "t.py"], loose, env)
        jail = p.jail.root
        assert rc == 0, out
        assert uid == pwd.getpwnam("nobody").pw_uid
        first, keys = out.splitlines()[:2]
        assert first.split()[0] == str(uid) and str(jail) in first
        for gone in ("SSL_CERT_FILE", "HTTPS_PROXY", "GH_TOKEN", "WILLOW_HOME"):
            assert gone not in keys, keys
        assert "ONESCRIPT_NOW" in keys and "HOME" in keys
        assert not (loose / "w").exists(), (
            "the user wrote in the copy, not the original"
        )
    finally:
        p.close()
    assert not jail.exists()


def test_nonroot_a_term_signal_is_turned_into_an_exit(monkeypatch):
    got = {}
    monkeypatch.setattr(mc.signal, "signal", lambda sig, h: got.update(sig=sig, h=h))
    monkeypatch.setattr(mc, "check", lambda *a, **k: [])
    monkeypatch.setattr(mc, "_gate0", lambda *a, **k: (mc.GATE0_NAME, True, "x"))
    mc.run_cli("nonexistent.json", out=lambda _s: None)
    assert got["sig"] == mc.signal.SIGTERM
    with pytest.raises(SystemExit) as e:
        got["h"](15, None)
    assert e.value.code == 143
