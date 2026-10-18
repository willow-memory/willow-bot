"""seed: the first bite's words against the box, once per check-in, three states."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api, pile, seed, xref  # noqa: E402
from onescript.tests.test_api import cfg, rows  # noqa: E402,F401
from onescript.tests.test_xref import make, sh  # noqa: E402


def index_into_box(cfg, *repos: Path) -> dict:  # noqa: F811
    """xref over `repos`, written through the record, as the CLI's --record does."""
    idx = xref.build([(r.name, r) for r in repos])
    ib, rb = (xref.canon(idx) + "\n").encode(), xref.report(idx).encode()
    xref.record_outputs(cfg.box, ib, rb, idx["inputs"])
    return idx


@pytest.fixture
def box(cfg, tmp_path):  # noqa: F811
    """Two repos beside the bot (cfg.root's parent is tmp_path), indexed, checked in."""
    grove = make(
        tmp_path,
        "docs",
        {
            "forge.md": "# The forge\n\nWhat's the first bite?\nthe forge asks\n",
            "notes.md": "the pile\nthe box\n",
        },
    )
    bot = make(tmp_path, "bot", {"run.py": "# the bite is scanned\nprint('forge')\n"})
    index_into_box(cfg, grove, bot)
    assert api.checkin(cfg)["code"] == 0
    return {"cfg": cfg, "docs": grove, "bot": bot}


def test_words_are_distinct_lowercased_and_sorted():
    assert seed.words("The FORGE, the forge's bite_2!") == [
        "2",
        "bite",
        "forge",
        "s",
        "the",
    ]
    assert seed.words("  ...  ") == []


def test_the_first_bite_seeds_rarest_first_with_whole_counts(box):
    res = api.seed(box["cfg"], "the Forge first bite", exact=True)
    assert res["counted_by"] == "scan" and "counted_why" not in res
    assert res["code"] == 0 and res["state"] == "seeded"
    keys = {k["word"]: k for k in res["keys"]}
    assert [k["word"] for k in res["keys"]] == ["first", "bite", "forge", "the"]
    assert keys["first"] == {
        "word": "first",
        "files": 1,
        "lines": 1,
        "anchors": [["docs", "forge.md", 3]],
    }
    assert keys["forge"]["files"] == 2 and keys["forge"]["lines"] == 3
    assert keys["the"]["files"] == 3  # everywhere, and it says so; nothing is dropped
    assert res["missed"] == [] and res["files_read"] == 3 and res["unreachable"] == {}


def test_the_row_holds_the_bite_as_a_hash_and_points_at_the_index(box):
    res = api.seed(box["cfg"], "the forge")
    row = rows(box["cfg"])[res["row"]]
    assert row["kind"] == "seed" and row["where"] == seed.INDEX
    assert row["bite_hash"] == api.record.h256("the forge")
    assert "bite" not in row and len(row["words"]) == 2  # keyed, never the words
    assert not {"forge", "the"} & set(
        json.dumps(row["words"]).strip('[]"').split('", "')
    )
    assert '"forge"' not in json.dumps(row) and '"the"' not in json.dumps(row)
    pile = {
        i["where"]: i["sha"]
        for i in json.loads((box["cfg"].box / "pile.json").read_text())["items"]
    }
    assert row["index_sha256"] == pile[seed.INDEX]


def test_nothing_in_the_box_is_a_cold_start(box):
    res = api.seed(box["cfg"], "zebra quokka")
    assert res["state"] == "escalated:cold_start"
    assert res["keys"] == [] and res["missed"] == ["quokka", "zebra"]


def test_the_first_bite_seeds_once_per_check_in(box):
    first = api.seed(box["cfg"], "the forge")
    again = api.seed(box["cfg"], "the box")
    assert again["code"] == 1 and f"row {first['row']}" in again["refused"]
    assert [r["kind"] for r in rows(box["cfg"])].count("seed") == 1
    assert api.checkin(box["cfg"])["code"] == 0  # a new check-in seeds again
    assert api.seed(box["cfg"], "the box")["state"] == "seeded"


def test_no_check_in_no_seed(cfg, tmp_path):  # noqa: F811
    index_into_box(cfg, make(tmp_path, "docs", {"a.md": "forge\n"}))
    res = api.seed(cfg, "forge")
    assert res["code"] == 1 and "no check-in" in res["refused"]
    assert "seed" not in [r["kind"] for r in rows(cfg)]


def test_no_words_seeds_nothing(box):
    assert api.seed(box["cfg"], " ?! ")["code"] == 1
    assert "seed" not in [r["kind"] for r in rows(box["cfg"])]


def test_no_index_in_the_pile_is_unreachable(cfg):  # noqa: F811
    assert api.checkin(cfg)["code"] == 0
    res = api.seed(cfg, "forge")
    assert res["state"] == "unreachable" and "no xref index" in res["why"]


def test_an_index_that_doesnt_hash_to_its_pointer_is_not_read(box):
    p = box["cfg"].box / seed.INDEX
    p.write_bytes(p.read_bytes().replace(b"forge.md", b"forgf.md"))
    res = api.seed(box["cfg"], "forge")
    assert res["state"] == "unreachable" and "pile pointer" in res["why"]


def test_the_indexed_bytes_count_not_the_working_tree(box):
    (box["docs"] / "notes.md").write_text("zebra\n")  # edited after the index
    sh(box["docs"], "commit", "-qam", "two")  # and committed: HEAD moved on
    res = api.seed(box["cfg"], "zebra pile")
    assert res["missed"] == ["zebra"]
    assert [k["word"] for k in res["keys"]] == ["pile"]


def test_bytes_that_dont_hash_to_the_index_are_skipped(cfg, tmp_path):  # noqa: F811
    grove = make(tmp_path, "docs", {"a.md": "forge\n"})
    idx = xref.build([("docs", grove)])
    idx["arrival"]["docs"]["files"][0]["sha256"] = "0" * 64
    ib = (xref.canon(idx) + "\n").encode()
    xref.record_outputs(cfg.box, ib, b"", idx["inputs"])
    assert api.checkin(cfg)["code"] == 0
    res = api.seed(cfg, "forge")
    assert res["state"] == "unreachable"
    assert res["skipped"] == {"bytes don't hash to the index": 1}


def test_a_repo_with_no_checkout_is_said(box, tmp_path):
    moved = tmp_path / "elsewhere"
    box["bot"].rename(moved)
    res = api.seed(box["cfg"], "scanned forge", exact=True)
    assert res["unreachable"] == {"bot": "no checkout to read it from"}
    assert res["missed"] == ["scanned"]
    assert box["cfg"].box.exists()


def test_a_named_repo_is_read_from_where_it_is_named(box, tmp_path):
    moved = tmp_path / "elsewhere"
    box["bot"].rename(moved)
    res = api.seed(box["cfg"], "scanned", repos={"bot": moved})
    assert res["state"] == "seeded" and res["unreachable"] == {}


def test_same_box_same_bite_same_seed(box):
    idx, _, _ = seed.read_index(api._open(box["cfg"], "t", ())[0].rec)
    where = {"docs": box["docs"], "bot": box["bot"]}
    ws = seed.words("the forge first bite")
    assert seed.scan(idx, where, ws) == seed.scan(idx, where, ws)


def test_the_cli_wants_name_equals_path(box, capsys):
    with pytest.raises(SystemExit):
        cli.main(["--box", str(box["cfg"].box), "seed", "forge", "--repo", "nopath"])
    assert "NAME=PATH" in capsys.readouterr().err


def test_without_a_store_key_the_row_leaves_the_words_out(box):
    from dataclasses import replace

    nokey = replace(box["cfg"], keys=box["cfg"].keys.parent / "elsewhere" / "keys.json")
    res = api._hidden(
        nokey, ["forge", "the"], {"keys": [{"word": "forge"}], "missed": ["x"]}
    )
    assert res["words"] == [] and "keys" not in res and "missed" not in res
    assert "no store key" in res["hidden"]


# ── D6: the counts come from the store; the scan only when asked ──────────
def test_by_default_the_store_counts_chunks_and_files(box):
    res = api.seed(box["cfg"], "the Forge first bite")
    assert res["counted_by"] == "store" and res["state"] == "seeded"
    keys = {k["word"]: k for k in res["keys"]}
    assert set(keys["first"]) == {"word", "files", "chunks", "anchors"}
    assert keys["first"]["files"] == 1 and keys["first"]["chunks"] >= 1
    assert keys["first"]["anchors"][0][:2] == ["docs", "forge.md"]
    assert keys["the"]["files"] == 3  # nothing dropped as too common, here too
    assert [k["word"] for k in res["keys"]][0] == "first"  # rarest first
    assert res["missed"] == []


def test_the_store_and_the_scan_agree_on_what_is_missed_and_where(box):
    bite = "zebra forge first"
    store = api.seed(box["cfg"], bite)
    assert api.checkin(box["cfg"])["code"] == 0
    scan = api.seed(box["cfg"], bite, exact=True)
    assert store["missed"] == scan["missed"] == ["zebra"]
    files = {k["word"]: k["files"] for k in scan["keys"]}
    assert {k["word"]: k["files"] for k in store["keys"]} == files


def test_with_no_store_the_scan_counts_and_says_why(box):
    path = pile.store_path(box["cfg"].box)
    with open(path, "ab") as f:
        f.write(b"\0")
    res = api.seed(box["cfg"], "forge first")
    assert res["counted_by"] == "scan" and "lines" in res["keys"][0]
    assert res["counted_why"].startswith("no store attached: ")
    assert "doesn't hash to its row" in res["counted_why"]


def test_the_seed_row_hashes_store_counted_words_too(box):
    res = api.seed(box["cfg"], "the forge")
    row = rows(box["cfg"])[res["row"]]
    assert row["counted_by"] == "store"
    assert '"forge"' not in json.dumps(row) and '"the"' not in json.dumps(row)


def test_the_cli_passes_exact(box, monkeypatch):
    got = {}

    def fake(cfg, bite, **kw):  # noqa: F811
        got.update(kw)
        return {"code": 0}

    monkeypatch.setattr(api, "seed", fake)
    cli.main(["--box", str(box["cfg"].box), "seed", "forge", "--exact"])
    assert got["exact"] is True
    cli.main(["--box", str(box["cfg"].box), "seed", "forge"])
    assert got["exact"] is False
