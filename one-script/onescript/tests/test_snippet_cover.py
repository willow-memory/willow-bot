"""snippet_cover: each idea's calls sorted into here / loose / outside / missing."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

SCAN = Path(__file__).resolve().parents[2] / "scripts" / "scan" / "snippet_cover.py"
spec = importlib.util.spec_from_file_location("snippet_cover", SCAN)
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

FENCE = "`" * 3


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    r = tmp_path / "r"
    for name, text in files.items():
        (r / name).parent.mkdir(parents=True, exist_ok=True)
        (r / name).write_text(text)
    env = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "add", "."], check=True)
    subprocess.run(["git", "-C", str(r), *env, "commit", "-qm", "x"], check=True)
    return r


def ideas(*blocks: tuple[int, str]) -> str:
    return "\n".join(
        f"### {n}. idea {n}\n\n{FENCE}python\n{src}\n{FENCE}\n" for n, src in blocks
    )


def test_each_call_is_sorted_by_where_it_lives(tmp_path):
    here = sc.index_repos(
        {
            "r": repo(
                tmp_path,
                {
                    "pkg/__init__.py": "",
                    "pkg/dice.py": "def d20(rng):\n    return 1\n",
                    "net.py": "def get(url):\n    return url\n",
                    "db.py": "def connect():\n    pass\n",
                },
            )
        }
    )
    items = sc.cover(
        ideas(
            (1, "import dice\ndice.d20(rng)"),  # module.name: here
            (2, "from pkg.dice import d20\nd20(rng)"),  # imported from here
            (3, "data = get(url)"),  # bare, defined here: loose
            (4, "from bm25s import BM25\nBM25()"),  # outside
            (5, "dice.d21(rng)"),  # module here, name not
            (6, "c = Counter(xs)\nrows.append(c)"),  # stdlib and a value
            (8, "P = re.compile('x')\nP.findall(s)"),  # a value the snippet made
            (9, "rows = db.execute(q)"),  # a value sharing a module's name
        ),
        here,
    )
    by = {i["n"]: i for i in items}
    assert by[1]["verdict"] == "compiled" and by[1]["here"] == ["dice.d20"]
    assert by[2]["verdict"] == "compiled"
    assert by[3]["verdict"] == "needs building" and by[3]["loose"] == {"get": 1}
    assert by[4]["verdict"] == "needs outside" and by[4]["outside"] == ["BM25"]
    assert by[5]["missing"] == ["dice.d21"]
    assert by[6]["verdict"] == "stdlib only"
    assert by[8]["verdict"] == "stdlib only" and by[8]["missing"] == []
    assert by[9]["missing"] == [] and by[5]["missing"] == ["dice.d21"]


def test_only_committed_files_count(tmp_path):
    r = repo(tmp_path, {"a.py": "def kept():\n    pass\n"})
    (r / "b.py").write_text("def uncommitted():\n    pass\n")
    here = sc.index_repos({"r": r})
    assert "kept" in here["defined"] and "uncommitted" not in here["defined"]


def test_an_idea_with_two_snippets_takes_the_worse_verdict(tmp_path):
    here = sc.index_repos({"r": repo(tmp_path, {"dice.py": "def d20():\n    pass\n"})})
    text = ideas((7, "import dice\ndice.d20()")) + f"\n{FENCE}python\nnope()\n{FENCE}\n"
    (item,) = sc.merge_items(sc.cover(text, here))
    assert item["verdict"] == "needs building" and item["missing"] == ["nope"]


def test_same_input_same_bytes(tmp_path):
    r = repo(tmp_path, {"dice.py": "def d20():\n    return 1\n"})
    text = ideas((1, "import dice\ndice.d20()"), (2, "x = y(1)"))
    once = sc.report(sc.merge_items(sc.cover(text, sc.index_repos({"r": r}))))
    again = sc.report(sc.merge_items(sc.cover(text, sc.index_repos({"r": r}))))
    assert once == again
