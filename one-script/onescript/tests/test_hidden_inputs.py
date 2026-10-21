"""Hidden inputs (flatten-2 F17): what the answer must not depend on.

The web check found these ways identical code gives different bytes: set order
and ``hash()`` (``PYTHONHASHSEED``), directory listing order, locale
(``LC_ALL``/``LANG``, ``PYTHONUTF8``, the filesystem encoding), the environment
generally, Unicode form, and ``log``/``exp`` drift in the last bit across libm.
The operator: "Using the onescript at every path." One script, one answer.

A test, not a boot probe: the fixture takes about 0.3 s a process and runs in
five, and boot's probes are the gate's attacks, run at every check-in. This runs
in CI, where an input that is hidden today is one a run with a new Python, libc
or locale would meet tomorrow.

1. The fixture (`hidden_inputs_fixture`: canon in every named encoding, record
   rows and hashes, the pile's words / bite key / forms / ranking / store, the
   stream's draws, calibration) is byte-identical across ``PYTHONHASHSEED``,
   ``LC_ALL`` (C, C.UTF-8 when the machine has it, an ASCII-only Python), and a
   scrubbed environment.
2. A lint: no listing call (``listdir``, ``glob``, ``iterdir``, ``rglob``,
   ``scandir``, ``os.walk``) feeds anything unsorted, unless the call is on the
   allowlist below with the one-line reason its order cannot reach output.
3. Calibration's float math pinned: `brier` exactly, `log_score` to one ulp
   (libm may differ in the last bit, so the pin names the drift and does not
   pretend to prevent it).
"""

from __future__ import annotations

import ast
import math
import os
import subprocess
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import calibration  # noqa: E402

ONE_SCRIPT = Path(__file__).resolve().parents[2]


def _locales() -> list[str]:
    out = subprocess.run(
        ["locale", "-a"], capture_output=True, text=True, check=False
    ).stdout.split()
    return [x for x in ("C.UTF-8", "C.utf8", "en_US.UTF-8") if x in out][:1]


def _child(**env: str) -> str:
    """The fixture's output in a child whose environment is only what is named
    here, the paths it needs, and these."""
    keep = {
        k: os.environ[k]
        for k in (
            "PATH",
            "HOME",
            "PYTHONPATH",
            "WILLOW_HOME",
            "ONESCRIPT_HOME",
            "ONESCRIPT_GROVE",
            "TMPDIR",
        )
        if k in os.environ
    }
    keep["PYTHONPATH"] = os.pathsep.join(
        [str(ONE_SCRIPT), *([keep["PYTHONPATH"]] if "PYTHONPATH" in keep else [])]
    )
    p = subprocess.run(
        [sys.executable, "-m", "onescript.tests.hidden_inputs_fixture"],
        cwd=ONE_SCRIPT,
        env={**keep, **env},
        capture_output=True,
        text=True,
        encoding="ascii",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert p.returncode == 0, p.stderr[-2000:]
    return p.stdout


VARIANTS = {
    "hashseed-0": {"PYTHONHASHSEED": "0"},
    "hashseed-12345": {"PYTHONHASHSEED": "12345"},
    "hashseed-random": {"PYTHONHASHSEED": "random"},
    "locale-C": {"PYTHONHASHSEED": "1", "LC_ALL": "C", "LANG": "C"},
    "ascii-python": {
        "PYTHONHASHSEED": "2",
        "LC_ALL": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONIOENCODING": "ascii",
    },
    "utf8-mode": {"PYTHONHASHSEED": "3", "PYTHONUTF8": "1"},
}


def test_the_fixture_is_byte_identical_across_hash_seed_and_locale():
    base = _child(PYTHONHASHSEED="0", LC_ALL="C")
    assert len(base) > 1000 and '"rank"' in base, "the fixture ran and said something"
    # the rank is JSON inside JSON, so its quotes are escaped
    assert '\\"hits\\":[{' in base, "the pile ranked something; empty proves little"
    variants = dict(VARIANTS)
    for loc in _locales():
        variants[f"locale-{loc}"] = {
            "PYTHONHASHSEED": "4",
            "LC_ALL": loc,
            "LANG": loc,
        }
    differ = {n for n, env in variants.items() if _child(**env) != base}
    assert not differ, f"output moved with a hidden input in: {sorted(differ)}"


def test_the_fixture_does_move_when_a_set_order_leaks(monkeypatch):
    """The fixture can fail: a word list left in set order differs by seed."""
    code = (
        "import json,sys;"
        "from onescript import seed;"
        "print(list({w for w in seed.WORD.findall("
        "'alpha beta gamma delta epsilon zeta eta theta iota kappa lambda')}))"
    )
    got = {
        subprocess.run(
            [sys.executable, "-c", code],
            cwd=ONE_SCRIPT,
            env={**os.environ, "PYTHONHASHSEED": s},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for s in ("0", "1", "2", "3")
    }
    assert len(got) > 1


# ── 2. the listing lint ─────────────────────────────────────────────────────
LISTING = {"listdir", "glob", "iglob", "iterdir", "rglob", "scandir"}

#: (file relative to one-script, call name, the enclosing function): why the
#: order cannot reach a hash, a record row, a served document or a seed.
ALLOW = {
    (
        "onescript/reverse.py",
        "rglob",
        "three_way",
    ): "keyed into a dict, then walked as sorted(set(on_disk) | ...)",
    (
        "deep_thought.py",
        "walk",
        "walk_for",
    ): "dirnames sorted in place; each name's list is sorted on return",
    (
        "deep_thought.py",
        "walk",
        "measure_venvs",
    ): "dirnames sorted in place; the hits go into a set read only through sorted()",
}


def _sources() -> list[Path]:
    out = [
        p for p in (ONE_SCRIPT / "onescript").glob("*.py") if p.name != "__pycache__"
    ]
    out += [ONE_SCRIPT / "prompt.py", ONE_SCRIPT / "deep_thought.py"]
    for sub in ("scripts", "foundation"):
        out += list((ONE_SCRIPT / sub).rglob("*.py"))
    return sorted(out)


def _unsorted(path: Path) -> list[tuple[str, str, int]]:
    """(call, enclosing function, line) for each listing call that has no
    ``sorted(...)`` around it in its statement."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute):
            name = f.attr
            is_walk = (
                name == "walk" and isinstance(f.value, ast.Name) and f.value.id == "os"
            )
            if name not in LISTING and not is_walk:
                continue
        else:
            continue
        up, wrapped, fn = node, False, "<module>"
        while up in parent:
            up = parent[up]
            if (
                isinstance(up, ast.Call)
                and isinstance(up.func, ast.Name)
                and up.func.id == "sorted"
            ):
                wrapped = True
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = up.name
                break
        if not wrapped:
            bad.append((f.attr, fn, node.lineno))
    return bad


def test_no_listing_feeds_anything_unsorted():
    seen, left = set(), []
    for path in _sources():
        rel = path.relative_to(ONE_SCRIPT).as_posix()
        for call, fn, line in _unsorted(path):
            key = (rel, call, fn)
            seen.add(key)
            if key not in ALLOW:
                left.append(f"{rel}:{line} {call}() in {fn}()")
    assert not left, (
        "unsorted listing (wrap in sorted(), or allowlist with a reason):\n"
        + "\n".join(left)
    )
    assert set(ALLOW) <= seen, f"stale allowlist entries: {sorted(set(ALLOW) - seen)}"


def test_the_lint_sees_an_unsorted_listing(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "import os\n"
        "def a(p):\n    return [x for x in p.glob('*')]\n"
        "def b(p):\n    return sorted(x for x in p.glob('*'))\n"
        "def c(p):\n    return os.listdir(p)\n"
        "def d(t):\n    return [n for n in ast.walk(t)]\n"
    )
    assert sorted((c, fn) for c, fn, _ in _unsorted(f)) == [
        ("glob", "a"),
        ("listdir", "c"),
    ]


# ── 3. float drift, named ───────────────────────────────────────────────────
def test_calibration_floats_are_pinned():
    # brier is + - * only: exact everywhere.
    assert repr(calibration.brier(0.7, True)) == "0.09000000000000002"
    assert repr(calibration.brier(0.9, False)) == "0.81"
    # log_score calls libm: one ulp of room, no more.
    for p, want in ((0.7, 0.35667494393873245), (0.99, 0.01005033585350145)):
        got = calibration.log_score(p, True)
        assert abs(got - want) <= math.ulp(want), (p, got, want)
