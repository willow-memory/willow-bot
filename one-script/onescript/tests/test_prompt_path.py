"""The prompt hook's path (`prompt_path.py`), the one the human runs on every
Enter, loads its own pieces and nothing else: not the check-in, the gates'
runner, the ladder, the turn or the check-out (`run.py`), and not `api`. Held
two ways: by its import closure (lazy imports count; a `TYPE_CHECKING` import
is a type, not a load), and by what `python -m onescript prompt` actually
imports, run as the hook runs it."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

from onescript.tests.test_api import cfg  # noqa: F401  (the box fixture needs it)
from onescript.tests.test_store import BITE, box  # noqa: F401

PKG = Path(__file__).resolve().parent.parent
ONE = PKG.parent  # one-script/

# The record and its stamp (record, canon, sign, and stamp.py, the write
# token's home: the gate re-exports the same objects), the front door, the
# store and its lookup, the chunk serving.
ALLOWED = {
    "prompt_path",
    "opening",
    "boxstore",
    "chunk",
    "record",
    "canon",
    "sign",
    "stamp",
    "clock",
    "states",
    "scrub",
    "pile",
    "seed",
    "words",
    "xref",
    "mai_shape",  # xref's write-shape check, a leaf
}
NEVER = {
    "api",
    "run",
    "gate",
    "boot",
    "reverse",
    "resolve",
    "ladder",
    "view",
    "writeback",
}


def _type_only(node: ast.AST) -> bool:
    return isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)


def closure(start: str) -> set[str]:
    seen, todo = {start}, [start]
    while todo:
        tree = ast.parse((PKG / f"{todo.pop()}.py").read_text(encoding="utf-8"))
        skip = {id(n) for t in ast.walk(tree) if _type_only(t) for n in ast.walk(t)}
        for n in ast.walk(tree):
            if id(n) in skip or not (isinstance(n, ast.ImportFrom) and n.level == 1):
                continue
            names = [n.module.split(".")[0]] if n.module else [a.name for a in n.names]
            for m in names:
                if (PKG / f"{m}.py").exists() and m not in seen:
                    seen.add(m)
                    todo.append(m)
    return seen


def test_the_prompt_paths_closure_is_its_own():
    got = closure("prompt_path")
    assert got <= ALLOWED, f"the prompt path now reaches {sorted(got - ALLOWED)}"
    assert not got & NEVER


def test_the_hook_imports_only_its_path(tmp_path):
    """`python -m onescript prompt`, as `scripts/prompt-hook` runs it, with no
    check-in: it answers, and no module outside its path was imported."""
    probe = (
        "import runpy, sys, json, io\n"
        "sys.argv = ['onescript', '--box', sys.argv[1], '--keys', sys.argv[2], 'prompt']\n"
        "sys.stdin = io.StringIO(json.dumps({'prompt': 'the record lock'}))\n"
        "try:\n"
        "    runpy.run_module('onescript', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('onescript.'))),"
        " file=sys.stderr)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(ONE)}
    p = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            str(tmp_path / "box"),
            str(tmp_path / "k" / "keys.json"),
        ],
        cwd=ONE,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    loaded = {m.split(".")[1] for m in json.loads(p.stderr.strip().splitlines()[-1])}
    assert "hookSpecificOutput" in p.stdout, p.stdout + p.stderr
    assert loaded <= ALLOWED | {"__main__"}, sorted(loaded - ALLOWED)
    assert not loaded & NEVER


def test_any_other_verb_still_loads_the_whole_one_script():
    from onescript import __main__ as cli

    assert cli.api.prompt is cli.prompt_path.prompt
    assert cli._prompt_only(["--box", "b", "prompt", "--max-chars", "9"])
    assert not cli._prompt_only(["checkin"])
    assert not cli._prompt_only(["lookup", "prompt"])


def test_the_stamp_is_the_gates_own():
    """One token, two names: what the prompt path writes with is what the gate
    mints with, and the record refuses a stamp made with any other."""
    import pytest

    from onescript import gate, stamp
    from onescript.record import Verified

    assert gate.token() is stamp.token() and gate._TOKEN is stamp._TOKEN
    assert gate.system()._token is stamp.system()._token is stamp.token()
    rec_kw = {"box": None, "clock": lambda: "t", "version": "v", "token": stamp.token()}
    from onescript.record import Record

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        rec = Record(Path(d) / "box", rec_kw["clock"], "v", stamp.token())
        rec.append("note", gate.system())
        with pytest.raises(Exception):
            rec.append("note", Verified("run", "python", object()))


class TestOneReadPerRepo:
    """The prompt reads its chunks through one `pile.ChunkReader`: every file
    in one `cat-file --batch` per repo, each chunk answered as `read_chunk`
    answers it alone, its text or its why."""

    def chunks(self, box):  # noqa: F811
        from onescript import api, seed

        c = box["cfg"]
        index, _, _ = seed.read_index(
            api.record.Record(c.box, lambda: "", "", object())
        )
        repos = api._repos(c, index)
        hits = api.lookup(c, BITE)["hits"]
        good = [
            {"repo": r, "path": p, "line": n, "sha256": h["chunk"]}
            for h in hits
            for r, p, n in h["where"][:1]
        ]
        g = good[0]
        bad = [
            {**g, "line": g["line"] + 999},  # not at that line
            {**g, "sha256": "0" * 64},  # not that hash
            {**g, "path": "not-indexed.md"},  # not in the index
            {**g, "repo": "no-such-repo"},  # no arrival for it
        ]
        return index, repos, good + bad

    def test_the_batch_answers_each_chunk_as_one_read_would(self, box):  # noqa: F811
        from onescript import pile

        index, repos, chunks = self.chunks(box)
        reader = pile.ChunkReader(index, repos, chunks)
        for ch in chunks:
            assert reader(ch) == pile.read_chunk(index, repos, ch), ch
        assert any(reader(ch)[0] for ch in chunks)
        assert any(reader(ch)[0] is None for ch in chunks)

    def test_one_git_spawn_per_repo_however_many_chunks(self, box, monkeypatch):  # noqa: F811
        from onescript import pile, xref

        index, repos, chunks = self.chunks(box)
        spawned = []
        real = xref.subprocess.run

        def counted(argv, *a, **k):
            spawned.append(argv)
            return real(argv, *a, **k)

        monkeypatch.setattr(xref.subprocess, "run", counted)
        good = [c for c in chunks if pile._indexed(index, c)]
        assert len({(c["repo"], c["path"]) for c in good}) > 1  # several files
        pile.ChunkReader(index, repos, good)
        assert len(spawned) == len({c["repo"] for c in good})


class TestTheHookReadsTheCheckinsRepos:
    """`scripts/prompt-hook` passes no `--repo`, so a box whose repos don't all
    sit beside willow-bot never found its store ("still building"). Check-in
    now leaves its `--repo` map beside the keys, and a verb without one reads it."""

    def cfg(self, tmp_path, repos):
        from onescript import opening

        keys = tmp_path / "keys" / "keys.json"
        keys.parent.mkdir(mode=0o700)
        return opening.Config(
            box=tmp_path / "box",
            keys=keys,
            constitution=tmp_path / "c",
            ci=tmp_path / "ci",
            root=tmp_path,
            grove=tmp_path,
            venv=None,
            repos=repos,
        )

    def test_the_map_round_trips_and_is_the_owners_alone(self, tmp_path):
        from onescript import opening

        c = self.cfg(tmp_path, {"jeles": tmp_path / "hornbook" / "jeles"})
        opening.save_repos(c)
        saved = c.keys.parent / opening.REPOS
        assert oct(saved.stat().st_mode & 0o777) == "0o600"
        assert opening.load_repos(c.keys) == {"jeles": tmp_path / "hornbook" / "jeles"}

    def test_a_checkin_without_repos_leaves_none_behind(self, tmp_path):
        import dataclasses

        from onescript import opening

        c = self.cfg(tmp_path, {"a": tmp_path})
        opening.save_repos(c)
        opening.save_repos(dataclasses.replace(c, repos=None))
        assert opening.load_repos(c.keys) is None
        assert not (c.keys.parent / opening.REPOS).exists()

    def test_a_map_others_can_write_is_not_believed(self, tmp_path):
        from onescript import opening

        c = self.cfg(tmp_path, {"a": tmp_path})
        opening.save_repos(c)
        (c.keys.parent / opening.REPOS).chmod(0o622)
        assert opening.load_repos(c.keys) is None

    def test_the_hooks_config_reads_the_saved_map(self, tmp_path):
        import argparse

        from onescript import __main__ as cli
        from onescript import opening

        c = self.cfg(tmp_path, {"jeles": tmp_path / "j"})
        opening.save_repos(c)
        p = argparse.ArgumentParser()
        cli._add_globals(p)
        args = p.parse_args(["--box", str(c.box), "--keys", str(c.keys)])
        assert cli._config(args).repos == {"jeles": tmp_path / "j"}
        args = p.parse_args(
            ["--box", str(c.box), "--keys", str(c.keys), "--repo", f"x={tmp_path}"]
        )
        assert cli._config(args).repos == {"x": tmp_path}  # --repo still wins
