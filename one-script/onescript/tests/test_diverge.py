"""The divergence finder (``onescript.diverge``) on small synthetic git repos under
tmp_path: hermetic, the real repos are never read. The operator: "what the
smallest points of divergance we can isolate."

Each test builds a base commit and two branches, ``a`` and ``b``, and asks what the
tool says about the pair. A planted case for each rule: the unit both sides changed,
the change both made alike, formatting alone, a rename, a dependency through a real
import and one through a bare name that must not count, a non-Python hunk, no merge
base, a fork in two records, and the same bytes every time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import diverge as dv  # noqa: E402
from onescript import merge_check as mc  # noqa: E402
from onescript.record import Record, Verified  # noqa: E402

ONE_SCRIPT = Path(__file__).resolve().parents[2]
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=True,
    )
    return p.stdout.strip()


def put(repo: Path, files: dict[str, str | bytes | None]) -> None:
    for rel, text in files.items():
        f = repo / rel
        if text is None:
            f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            f.write_bytes(text)
        else:
            f.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


class World:
    """A repos dir holding one repo, ``r``, with branches ``a`` and ``b`` off ``main``."""

    def __init__(self, root: Path, base: dict):
        self.root = root
        self.repo = root / "r"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q", "-b", "main")
        put(self.repo, base)
        self.commit("base")

    def commit(self, msg: str) -> None:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg, "--allow-empty")

    def branch(self, name: str, files: dict, frm: str = "main") -> None:
        git(self.repo, "checkout", "-q", "-B", name, frm)
        for rel, v in files.items():
            if v is None:
                git(self.repo, "rm", "-q", rel)
        put(self.repo, {k: v for k, v in files.items() if v is not None})
        self.commit(name)
        git(self.repo, "checkout", "-q", "main")

    def diverge(self, a="a", b="b", **kw) -> dict:
        return dv.run(self.root, ["r"], a, b, **kw)

    def r(self, **kw) -> dict:
        return self.diverge(**kw)["repos"]["r"]


def world(tmp_path, base, a, b) -> World:
    w = World(tmp_path / "w", base)
    w.branch("a", a)
    w.branch("b", b)
    return w


def points(res) -> list[tuple]:
    return [
        (m["level"], m["kind"], m.get("unit") or m.get("file")) for m in res["minimal"]
    ]


MOD = """
    import os

    def alpha(x):
        return x + 1

    def beta(x):
        return x * 2

    class K:
        size = 1

        def go(self):
            return 1

        def stop(self):
            return 2
"""


# -- levels 0-2 ------------------------------------------------------------------


def test_the_same_unit_changed_two_ways_is_the_divergence_point(tmp_path):
    w = world(
        tmp_path,
        {"m.py": MOD},
        {"m.py": MOD.replace("x + 1", "x + 10")},
        {"m.py": MOD.replace("x + 1", "x + 20").replace("x * 2", "x * 3")},
    )
    res = w.diverge()
    r = res["repos"]["r"]
    assert res["state"] == "populated" and r["state"] == "populated"
    assert [(u["file"], u["unit"]) for u in r["units"]["both_different"]] == [
        ("m.py", "alpha")
    ]
    assert [u["unit"] for u in r["units"]["b_only"]] == ["beta"]
    assert points(r) == [(2, "unit", "alpha")]
    assert r["files"]["both_different"][0]["path"] == "m.py"
    assert r["base"] == git(w.repo, "rev-parse", "main")
    assert res["counts"]["units_both_different"] == 1 and res["counts"]["minimal"] == 1


def test_the_same_change_on_both_sides_is_convergent_not_a_divergence(tmp_path):
    same = MOD.replace("x + 1", "x + 7")
    w = world(tmp_path, {"m.py": MOD}, {"m.py": same}, {"m.py": same})
    res = w.diverge()
    r = res["repos"]["r"]
    assert res["state"] == "empty" and r["state"] == "empty"
    assert [u["unit"] for u in r["units"]["both_same"]] == ["alpha"]
    assert r["units"]["both_different"] == [] and r["minimal"] == []
    assert r["files"]["both_same"][0]["path"] == "m.py"


def test_formatting_and_comments_are_no_change(tmp_path):
    reformatted = MOD.replace(
        "def alpha(x):\n    return x + 1",
        "def alpha(x):\n    # a comment\n\n    return (\n        x   +   1\n    )",
    )
    w = world(
        tmp_path,
        {"m.py": MOD},
        {"m.py": reformatted},
        {"m.py": MOD.replace("x + 1", "x + 20")},
    )
    r = w.r()
    assert r["state"] == "empty"
    assert r["units"]["a_only"] == [] and r["units"]["both_different"] == []
    assert [u["unit"] for u in r["units"]["b_only"]] == ["alpha"]


def test_a_method_is_its_own_unit_and_the_class_shell_stays_put(tmp_path):
    w = world(
        tmp_path,
        {"m.py": MOD},
        {"m.py": MOD.replace("return 1\n", "return 10\n")},
        {
            "m.py": MOD.replace("return 1\n", "return 11\n").replace(
                "return 2", "return 22"
            )
        },
    )
    r = w.r()
    assert [u["unit"] for u in r["units"]["both_different"]] == ["K.go"]
    assert [u["unit"] for u in r["units"]["b_only"]] == ["K.stop"]
    assert not any(u["unit"] == "K" for g in dv.GROUPS for u in r["units"][g])


def test_a_class_attribute_two_ways_diverges(tmp_path):
    w = world(
        tmp_path,
        {"m.py": MOD},
        {"m.py": MOD.replace("size = 1", "size = 2")},
        {"m.py": MOD.replace("size = 1", "size = 3")},
    )
    assert [u["unit"] for u in w.r()["units"]["both_different"]] == ["K.size"]


def test_a_rename_on_one_side_still_meets_the_edit_on_the_other(tmp_path):
    w = World(tmp_path / "w", {"old.py": MOD, "keep.txt": "x\n"})
    git(w.repo, "checkout", "-q", "-B", "a", "main")
    git(w.repo, "mv", "old.py", "new.py")
    put(w.repo, {"new.py": MOD.replace("x + 1", "x + 10")})
    w.commit("a")
    git(w.repo, "checkout", "-q", "-B", "b", "main")
    put(w.repo, {"old.py": MOD.replace("x + 1", "x + 20")})
    w.commit("b")
    git(w.repo, "checkout", "-q", "main")
    r = w.r()
    assert [(u["file"], u["unit"]) for u in r["units"]["both_different"]] == [
        ("old.py", "alpha")
    ]
    row = r["files"]["both_different"][0]
    assert row["path"] == "old.py" and row["a"] == {"status": "R", "path": "new.py"}
    assert row["b"] == {"status": "M", "path": "old.py"}


def test_a_rename_to_two_different_paths_is_a_path_point(tmp_path):
    w = World(tmp_path / "w", {"old.py": MOD})
    git(w.repo, "checkout", "-q", "-B", "a", "main")
    git(w.repo, "mv", "old.py", "x.py")
    w.commit("a")
    git(w.repo, "checkout", "-q", "-B", "b", "main")
    git(w.repo, "mv", "old.py", "y.py")
    w.commit("b")
    git(w.repo, "checkout", "-q", "main")
    r = w.r()
    assert r["state"] == "populated"
    assert [(m["kind"], m["a"], m["b"]) for m in r["minimal"]] == [
        ("path", "x.py", "y.py")
    ]


def test_a_file_added_on_both_sides_differently_diverges_by_unit(tmp_path):
    w = world(
        tmp_path,
        {"keep.txt": "x\n"},
        {"n.py": "def f():\n    return 1\n\ndef g():\n    return 5\n"},
        {"n.py": "def f():\n    return 2\n\ndef g():\n    return 5\n"},
    )
    r = w.r()
    assert [u["unit"] for u in r["units"]["both_different"]] == ["f"]
    assert [u["unit"] for u in r["units"]["both_same"]] == ["g"]


def test_modify_against_delete_diverges_for_the_units_the_edit_touched(tmp_path):
    w = world(
        tmp_path,
        {"m.py": MOD},
        {"m.py": MOD.replace("x + 1", "x + 10")},
        {"m.py": None},
    )
    r = w.r()
    assert [u["unit"] for u in r["units"]["both_different"]] == ["alpha"]
    assert r["state"] == "populated"


# -- non-Python files: hunks -----------------------------------------------------

TXT = "".join(f"line {i}\n" for i in range(1, 41))


def test_a_text_hunk_both_sides_changed_differently_diverges(tmp_path):
    w = world(
        tmp_path,
        {"notes.md": TXT},
        {
            "notes.md": TXT.replace("line 5\n", "line five\n").replace(
                "line 30\n", "thirty\n"
            )
        },
        {"notes.md": TXT.replace("line 5\n", "line FIVE\n")},
    )
    r = w.r()
    assert [
        (h["file"], h["base_a"], h["base_b"]) for h in r["hunks"]["both_different"]
    ] == [("notes.md", [5, 1], [5, 1])]
    assert [h["base"] for h in r["hunks"]["a_only"]] == [[30, 1]]
    assert points(r) == [(1, "hunk", "notes.md")]


def test_hunks_in_different_places_or_alike_are_not_divergence(tmp_path):
    w = world(
        tmp_path,
        {"notes.md": TXT},
        {"notes.md": TXT.replace("line 5\n", "x\n").replace("line 9\n", "same\n")},
        {"notes.md": TXT.replace("line 20\n", "y\n").replace("line 9\n", "same\n")},
    )
    r = w.r()
    assert r["state"] == "empty"
    assert len(r["hunks"]["both_same"]) == 1
    assert len(r["hunks"]["a_only"]) == 1 and len(r["hunks"]["b_only"]) == 1


def test_binary_files_are_compared_by_blob_only(tmp_path):
    w = world(
        tmp_path,
        {"i.bin": b"\0\1\2"},
        {"i.bin": b"\0\1\3"},
        {"i.bin": b"\0\1\4"},
    )
    r = w.r()
    assert [(m["level"], m["kind"], m["file"]) for m in r["minimal"]] == [
        (1, "file", "i.bin")
    ]
    w2 = world(
        tmp_path / "x", {"i.bin": b"\0\1\2"}, {"i.bin": b"\0\x09"}, {"i.bin": b"\0\x09"}
    )
    assert w2.r()["state"] == "empty"


# -- level 3: dependencies -------------------------------------------------------

LIB = """
    def helper(x):
        return x + 1

    def other(x):
        return x
"""


def edges(r):
    return [
        (
            e["from"]["side"],
            e["from"]["unit"],
            e["to"]["side"],
            e["to"]["unit"],
            e["via"],
        )
        for e in r["edges"]
    ]


def test_an_edge_through_from_import(tmp_path):
    base = {
        "pkg/__init__.py": "",
        "pkg/lib.py": LIB,
        "pkg/use.py": "from pkg.lib import helper\n\ndef run_it(v):\n    return helper(v)\n",
    }
    w = world(
        tmp_path,
        base,
        {
            "pkg/use.py": "from pkg.lib import helper\n\ndef run_it(v):\n    return helper(v) * 2\n"
        },
        {"pkg/lib.py": LIB.replace("x + 1", "x + 2")},
    )
    r = w.r()
    assert edges(r) == [("a", "run_it", "b", "helper", "from-import")]
    e = r["edges"][0]
    assert e["from"]["file"] == "pkg/use.py" and e["to"]["file"] == "pkg/lib.py"
    assert (3, "edge", None) in [
        (m["level"], m["kind"], m.get("unit")) for m in r["minimal"]
    ]
    assert r["state"] == "populated" and r["units"]["both_different"] == []


def test_an_edge_through_import_as(tmp_path):
    base = {
        "lib.py": LIB,
        "use.py": "import lib as z\n\ndef run_it(v):\n    return z.helper(v)\n",
    }
    w = world(
        tmp_path,
        base,
        {"lib.py": LIB.replace("x + 1", "x + 5")},
        {"use.py": base["use.py"].replace("z.helper(v)", "z.helper(v) + 1")},
    )
    r = w.r()
    assert edges(r) == [("b", "run_it", "a", "helper", "module-alias")]


def test_an_edge_through_a_relative_import(tmp_path):
    base = {
        "pkg/__init__.py": "",
        "pkg/lib.py": LIB,
        "pkg/use.py": "from . import lib\nfrom .lib import other\n\n"
        "def one(v):\n    return lib.helper(v)\n\ndef two(v):\n    return other(v)\n",
    }
    w = world(
        tmp_path,
        base,
        {
            "pkg/lib.py": LIB.replace("x + 1", "x + 5").replace(
                "return x\n", "return -x\n"
            )
        },
        {
            "pkg/use.py": base["pkg/use.py"]
            .replace("(v)\n\ndef two", "(v) + 1\n\ndef two")
            .replace("return other(v)", "return other(v) + 2")
        },
    )
    r = w.r()
    assert edges(r) == [
        ("b", "one", "a", "helper", "relative"),
        ("b", "two", "a", "other", "relative"),
    ]


def test_an_edge_through_the_files_own_names_and_self(tmp_path):
    src = """
        def base(x):
            return x

        class C:
            def a(self):
                return 1

            def b(self):
                return self.a() + base(0)
    """
    w = world(
        tmp_path,
        {"m.py": src},
        {"m.py": src.replace("return 1", "return 2")},
        {
            "m.py": src.replace("+ base(0)", "+ base(1)").replace(
                "return x", "return x + 1"
            )
        },
    )
    r = w.r()
    got = edges(r)
    assert ("b", "C.b", "a", "C.a", "self") in got
    assert ("a", "C.a", "b", "base", "same-file") not in got  # C.a calls nothing


def test_a_bare_name_collision_is_not_an_edge(tmp_path):
    # B changed `run`, `get` and `mark` in lib.py. A's units use a DIFFERENT object's
    # .get / .run / a local `mark`, and one imports lib but never uses those names.
    lib = "def run():\n    return 1\n\ndef get():\n    return 2\n\ndef mark():\n    return 3\n"
    use = """
        import lib

        def one(d):
            return d.get("k") + d.run()

        def two(items):
            mark = len(items)
            return mark

        def three():
            return lib.unrelated

        def four(run):
            return run()
    """
    w = world(
        tmp_path,
        {"lib.py": lib, "use.py": use},
        {
            "use.py": use.replace('"k"', '"j"')
            .replace("len(items)", "len(items) + 1")
            .replace("lib.unrelated", "lib.unrelated + 1")
            .replace("return run()", "return run() + 1")
        },
        {
            "lib.py": lib.replace("return 1", "return 10")
            .replace("return 2", "return 20")
            .replace("return 3", "return 30")
        },
    )
    r = w.r()
    assert {u["unit"] for u in r["units"]["a_only"]} == {"one", "two", "three", "four"}
    assert {u["unit"] for u in r["units"]["b_only"]} == {"run", "get", "mark"}
    assert r["edges"] == [] and r["state"] == "empty"


def test_a_target_changed_alike_on_both_sides_is_no_edge(tmp_path):
    base = {
        "lib.py": LIB,
        "use.py": "from lib import helper\n\ndef f():\n    return helper(1)\n",
    }
    new_lib = LIB.replace("x + 1", "x + 9")
    w = world(
        tmp_path,
        base,
        {"lib.py": new_lib, "use.py": base["use.py"].replace("helper(1)", "helper(2)")},
        {"lib.py": new_lib},
    )
    assert w.r()["edges"] == []


# -- level 0 and unreachable -----------------------------------------------------


def test_no_merge_base_is_said_and_unreachable(tmp_path):
    w = World(tmp_path / "w", {"m.py": MOD})
    git(w.repo, "checkout", "-q", "--orphan", "orph")
    git(w.repo, "rm", "-rf", "-q", ".")
    put(w.repo, {"z.py": "x = 1\n"})
    w.commit("orphan")
    git(w.repo, "checkout", "-q", "main")
    res = w.diverge("main", "orph")
    r = res["repos"]["r"]
    assert res["state"] == "unreachable" and r["state"] == "unreachable"
    assert "no merge base" in r["reason"] and "no merge base" in res["reason"]


def test_a_missing_ref_or_repo_is_unreachable_and_said(tmp_path):
    w = world(
        tmp_path,
        {"m.py": MOD},
        {"m.py": MOD + "\nQ = 1\n"},
        {"m.py": MOD + "\nR = 1\n"},
    )
    res = w.diverge("a", "nope")
    assert res["state"] == "unreachable" and "'nope'" in res["repos"]["r"]["reason"]
    res = dv.run(w.root, ["r", "ghost"], "a", "b")
    assert res["state"] == "unreachable"
    assert res["repos"]["r"]["state"] == "populated"  # still reported
    assert "ghost" in res["repos"]["ghost"]["reason"] and "ghost" in res["reason"]
    res = dv.run(w.root, ["../r"], "a", "b")
    assert res["state"] == "unreachable" and "plain name" in res["reason"]


def test_identical_refs_are_empty_not_unreachable(tmp_path):
    w = world(tmp_path, {"m.py": MOD}, {"m.py": MOD + "\nQ = 1\n"}, {"m.py": MOD})
    assert w.diverge("a", "a")["state"] == "empty"


# -- determinism -----------------------------------------------------------------


def _busy(tmp_path) -> World:
    base = {
        "lib.py": LIB,
        "use.py": "import lib\n\ndef f():\n    return lib.helper(1)\n",
        "n.md": TXT,
        "m.py": MOD,
    }
    return world(
        tmp_path,
        base,
        {
            "lib.py": LIB.replace("x + 1", "x + 2"),
            "n.md": TXT.replace("line 3\n", "A\n"),
            "m.py": MOD.replace("x + 1", "x + 10"),
            "u.py": "Q = 1\n",
        },
        {
            "use.py": base["use.py"].replace("(1)", "(2)"),
            "n.md": TXT.replace("line 3\n", "B\n"),
            "m.py": MOD.replace("x + 1", "x + 20"),
            "u.py": "Q = 2\n",
        },
    )


def test_same_refs_same_bytes(tmp_path):
    w = _busy(tmp_path)
    one, two = dv.canon.bytes(w.diverge()), dv.canon.bytes(w.diverge())
    assert one == two
    assert dv.result_hash(w.diverge()) == dv.h256(one)
    assert json.loads(one)["state"] == "populated"
    assert str(tmp_path).encode() not in one  # no absolute path leaks into the bytes


def test_same_bytes_under_two_hash_seeds(tmp_path):
    w = _busy(tmp_path)
    outs = []
    for seed in ("1", "977"):
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "onescript",
                "diverge",
                "a",
                "b",
                "--repo",
                "r",
                "--repos",
                str(w.root),
                "--json",
            ],
            cwd=ONE_SCRIPT,
            capture_output=True,
            env={**os.environ, "PYTHONHASHSEED": seed, "LC_ALL": "C"},
        )
        assert p.returncode == 1, p.stderr
        outs.append(p.stdout)
    assert outs[0] == outs[1]
    assert outs[0] == dv.canon.bytes(w.diverge()) + b"\n"


# -- level 4: behaviour ----------------------------------------------------------

PKG = {
    "pkg/__init__.py": "",
    "pkg/core.py": "def val():\n    return 1\n\ndef other():\n    return 5\n",
    "tests/test_core.py": "from pkg import core\n\ndef test_val():\n    assert core.val() == 1\n",
    "tests/test_other.py": "from pkg import core\n\ndef test_other():\n    assert core.other() == 5\n",
}


def test_a_merge_conflict_is_a_divergence_point_with_its_hunks(tmp_path):
    w = world(
        tmp_path,
        PKG,
        {"pkg/core.py": PKG["pkg/core.py"].replace("return 1", "return 2")},
        {"pkg/core.py": PKG["pkg/core.py"].replace("return 1", "return 3")},
    )
    r = w.r(behaviour=True)
    beh = r["behaviour"]
    assert beh["merge"] == "conflict"
    assert [(c["file"], len(c["lines"])) for c in beh["conflicts"]] == [
        ("pkg/core.py", 2)
    ]
    assert (4, "conflict") in [(m["level"], m["kind"]) for m in r["minimal"]]
    assert r["counts"]["conflicts"] == 1
    assert (
        beh["tests"]["a"]["state"] == "empty"
        and "conflicts" in beh["tests"]["a"]["detail"]
    )


def test_a_clean_merge_runs_each_sides_changed_tests_and_says_pass_or_fail(tmp_path):
    w = world(
        tmp_path,
        PKG,
        {
            "pkg/core.py": PKG["pkg/core.py"].replace("return 1", "return 2"),
            "tests/test_core.py": PKG["tests/test_core.py"].replace("== 1", "== 2"),
        },
        {
            "pkg/core.py": PKG["pkg/core.py"].replace("return 5", "return 6"),
            "tests/test_other.py": PKG["tests/test_other.py"].replace("== 5", "== 6"),
        },
    )
    r = w.r(behaviour=True, runner=None)
    beh = r["behaviour"]
    assert beh["merge"] == "clean" and beh["conflicts"] == []
    assert beh["tests"]["a"]["state"] == "pass" and beh["tests"]["a"]["tests"] == [
        "tests/test_core.py"
    ]
    assert beh["tests"]["b"]["state"] == "pass"
    assert r["state"] == "empty"  # disjoint units, both sides green on the merge


def test_a_side_whose_tests_fail_on_the_merge_is_a_divergence_point(tmp_path):
    # A edits val() and its test; B edits a different line of the same function's
    # file such that the merge is textually clean but A's test no longer holds.
    core = "def val():\n    return 1\n\n\n\n\n\ndef scale():\n    return 10\n"
    base = {
        **PKG,
        "pkg/core.py": core,
        "tests/test_core.py": "from pkg import core\n\ndef test_val():\n    assert core.val() == 1\n",
    }
    w = world(
        tmp_path,
        base,
        {
            "tests/test_core.py": base["tests/test_core.py"].replace(
                "== 1", "== 1 and core.scale() == 10"
            )
        },
        {"pkg/core.py": core.replace("return 10", "return 11")},
    )
    r = w.r(behaviour=True)
    assert r["behaviour"]["merge"] == "clean"
    assert r["behaviour"]["tests"]["a"]["state"] == "fail"
    assert r["behaviour"]["tests"]["b"]["state"] == "empty"
    # level 3 saw it coming: A's test now reaches the unit B changed
    assert edges(r) == [("a", "test_val", "b", "scale", "from-import")]
    assert [
        (m["level"], m["side"]) for m in r["minimal"] if m["kind"] == "test-failure"
    ] == [(4, "a")]
    assert r["state"] == "populated"


def test_behaviour_never_writes_the_repo_or_its_branches(tmp_path):
    w = world(
        tmp_path,
        PKG,
        {"pkg/core.py": PKG["pkg/core.py"].replace("return 1", "return 2")},
        {"pkg/core.py": PKG["pkg/core.py"].replace("return 5", "return 6")},
    )
    before = (
        git(w.repo, "for-each-ref"),
        git(w.repo, "status", "--porcelain"),
        git(w.repo, "worktree", "list"),
    )
    w.r(behaviour=True)
    after = (
        git(w.repo, "for-each-ref"),
        git(w.repo, "status", "--porcelain"),
        git(w.repo, "worktree", "list"),
    )
    assert before == after
    assert not (w.repo / ".git" / "MERGE_HEAD").exists()
    assert not list(Path(dv.tempfile.gettempdir()).glob("diverge-*"))


# -- the record ------------------------------------------------------------------


def test_record_writes_one_row_and_rerunning_gives_the_same_hash(tmp_path):
    w = _busy(tmp_path)
    box = tmp_path / "box"
    out, err = [], []
    clock = lambda: "2026-01-01T00:00:00Z"  # noqa: E731
    rc1 = dv.run_cli(
        "a",
        "b",
        repos=w.root,
        names=["r"],
        record=True,
        box=box,
        clock=clock,
        out=out.append,
        err=err.append,
    )
    rc2 = dv.run_cli(
        "a",
        "b",
        repos=w.root,
        names=["r"],
        record=True,
        box=box,
        clock=clock,
        out=out.append,
        err=err.append,
    )
    assert rc1 == rc2 == 1
    rec = Record(box, clock, "v", object())
    rows = rec.rows()
    assert [r["kind"] for r in rows] == ["diverge", "diverge"]
    assert (
        rows[0]["result_hash"] == rows[1]["result_hash"] == dv.result_hash(w.diverge())
    )
    assert rows[0]["refs"] == {"a": "a", "b": "b"} and rows[0]["state"] == "populated"
    assert rows[0]["repos"]["r"]["base"] == git(w.repo, "rev-parse", "main")
    assert rec.verify_chain() == []
    assert rows[0]["who"] == "run" and rows[0]["standing"] == "unattested"


def test_record_refuses_a_broken_box_and_writes_nothing(tmp_path):
    w = _busy(tmp_path)
    box = tmp_path / "box"
    dv.run_cli(
        "a",
        "b",
        repos=w.root,
        names=["r"],
        record=True,
        box=box,
        out=lambda s: 0,
        err=lambda s: 0,
    )
    path = box / "record.jsonl"
    path.write_text(path.read_text().replace('"diverge"', '"divergX"'))
    err = []
    rc = dv.run_cli(
        "a",
        "b",
        repos=w.root,
        names=["r"],
        record=True,
        box=box,
        out=lambda s: 0,
        err=err.append,
    )
    assert rc == 2 and "broken" in err[0]
    assert len(path.read_text().splitlines()) == 1


# -- the verb --------------------------------------------------------------------


def test_the_verb_exit_codes_and_text(tmp_path, capsys):
    w = _busy(tmp_path)
    rc = cli.main(["diverge", "a", "b", "--repo", "r", "--repos", str(w.root)])
    text = capsys.readouterr().out
    assert (
        rc == 1
        and "state: populated" in text
        and "L2 unit" in text
        and "result sha256" in text
    )
    rc = cli.main(["diverge", "a", "a", "--repo", "r", "--repos", str(w.root)])
    assert rc == 0 and "state: empty" in capsys.readouterr().out
    rc = cli.main(["diverge", "a", "nope", "--repo", "r", "--repos", str(w.root)])
    assert rc == 2 and "unreachable" in capsys.readouterr().out
    rc = cli.main(["diverge", "a", "--repos", str(w.root)])
    assert rc == 2


def test_the_verb_record_goes_to_the_named_box(tmp_path, capsys):
    w = _busy(tmp_path)
    box = tmp_path / "bx"
    rc = cli.main(
        [
            "--box",
            str(box),
            "--now",
            "2026-01-01T00:00:00Z",
            "diverge",
            "a",
            "b",
            "--repo",
            "r",
            "--repos",
            str(w.root),
            "--json",
            "--record",
        ]
    )
    assert rc == 1
    assert Record(box, lambda: "", "v", object()).rows()[0]["kind"] == "diverge"
    assert json.loads(capsys.readouterr().out)["state"] == "populated"


# -- boxes -----------------------------------------------------------------------


def _box(path: Path, kinds: list[str], tag: str = "") -> Record:
    rec = Record(path, lambda: "2026-01-01T00:00:00Z", "v", tok)
    for i, k in enumerate(kinds):
        rec.append(k, who, text=f"{tag}{i}" if tag and k == "note" else "")
    return rec


tok = object()
who = Verified("willow", "self-declared", tok)


def test_two_boxes_that_fork_say_the_row_and_what_followed(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _box(a, ["note", "note", "note"])
    _box(b, ["note", "note", "note"])
    ra = Record(a, lambda: "2026-01-01T00:00:00Z", "v", tok)
    rb = Record(b, lambda: "2026-01-01T00:00:00Z", "v", tok)
    ra.append("note", who, text="left")
    ra.append("write", who, text="x")
    rb.append("note", who, text="right")
    res = dv.compare_boxes(a, b)
    assert (
        res["state"] == "populated"
        and res["split_at"] == 3
        and res["relation"] == "forked"
    )
    assert res["common_rows"] == 3
    assert res["a"] == {
        "rows_after": 2,
        "kinds": ["note", "write"],
        "counts": {"note": 1, "write": 1},
    }
    assert res["b"] == {"rows_after": 1, "kinds": ["note"], "counts": {"note": 1}}


def test_boxes_one_extending_the_other_and_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _box(a, ["note", "note"])
    _box(b, ["note", "note"])
    assert dv.compare_boxes(a, b)["state"] == "empty"
    Record(b, lambda: "2026-01-01T00:00:00Z", "v", tok).append("seal", who)
    res = dv.compare_boxes(a, b)
    assert (
        res["split_at"] == 2
        and res["relation"] == "extends"
        and res["a"]["rows_after"] == 0
    )


def test_a_broken_or_missing_chain_is_unreachable_and_said(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _box(a, ["note", "note"])
    _box(b, ["note", "note"])
    p = b / "record.jsonl"
    lines = p.read_text().splitlines()
    p.write_text(lines[0].replace('"note"', '"nope"') + "\n" + lines[1] + "\n")
    res = dv.compare_boxes(a, b)
    assert (
        res["state"] == "unreachable"
        and "broken" in res["reason"]
        and "'b'" in res["reason"]
    )
    res = dv.compare_boxes(a, tmp_path / "ghost")
    assert res["state"] == "unreachable" and "no record.jsonl" in res["reason"]


def test_the_boxes_verb_and_its_pairing(tmp_path, capsys):
    a, b = tmp_path / "a", tmp_path / "b"
    _box(a, ["note"])
    _box(b, ["note", "note"])
    rc = cli.main(["diverge", "--box-a", str(a), "--box-b", str(b), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["boxes"]["split_at"] == 1
    assert cli.main(["diverge", "--box-a", str(a)]) == 2


# -- merge_check gate 0 ----------------------------------------------------------


def _att(**kw) -> dict:
    a = {
        "chunk": "c",
        "author": "x",
        "verified_by": "y",
        "home": "h",
        "goldens": "g",
        "copies": [],
        "absent": [],
        "never_reach": [],
        "fixture": True,
    }
    a.update(kw)
    return a


def _gate0(tmp_path, att, w):
    p = tmp_path / "merge.json"
    p.write_text(json.dumps(att))
    return mc._gate0(p, mc.Places(w.root, w.root / "willow-bot" / "one-script"))


def test_gate0_not_attested_does_not_fail(tmp_path):
    w = _busy(tmp_path)
    g, ok, detail = _gate0(tmp_path, _att(), w)
    assert g == "0 divergence [M]" and ok and "not attested" in detail
    assert mc.GATE_NAMES[0].startswith("1 ")  # the eight are untouched


def test_gate0_records_the_set_and_scopes_nothing(tmp_path):
    w = _busy(tmp_path)
    g, ok, detail = _gate0(
        tmp_path, _att(base_ref="main", chunk_ref="a", diverge_repos=["r"]), w
    )
    assert ok and "scopes nothing" in detail
    g, ok, detail = _gate0(
        tmp_path, _att(base_ref="a", chunk_ref="b", diverge_repos=["r"]), w
    )
    assert ok and "populated" in detail and "units both-different 2" in detail


def test_gate0_fails_closed_when_diverge_is_unreachable_or_half_given(tmp_path):
    w = _busy(tmp_path)
    g, ok, detail = _gate0(
        tmp_path, _att(base_ref="main", chunk_ref="nope", diverge_repos=["r"]), w
    )
    assert not ok and "unreachable" in detail and "fail-closed" in detail
    g, ok, detail = _gate0(tmp_path, _att(base_ref="main", diverge_repos=["r"]), w)
    assert not ok and "come together" in detail
    g, ok, detail = _gate0(tmp_path, _att(base_ref="main", chunk_ref="a"), w)
    assert not ok  # default repo willow-bot is not beside: unreachable, not skipped


def test_gate0_failure_blocks_the_record(tmp_path):
    w = _busy(tmp_path)
    p = tmp_path / "merge.json"
    p.write_text(json.dumps(_att()))
    pl = mc.Places(w.root, w.root / "willow-bot" / "one-script")
    allok = [(g, True, "forced") for g in mc.GATE_NAMES]
    out, why = mc.record_merge(p, allok, pl, ("0 divergence [M]", False, "unreachable"))
    assert out is None and "gate 0" in why
