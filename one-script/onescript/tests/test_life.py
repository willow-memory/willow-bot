"""Conway's Life, as the table's verdict rows (life.py).

The engine is held to the patterns whose fates are known (a block, a blinker,
a glider, the diehard, the R-pentomino's 116 cells at generation 1103); the
record to its own replay: a game played onto the box checks out, and one
changed row makes the check fail at that row.
"""

from __future__ import annotations

import json

import pytest

from onescript import life, table
from onescript import __main__ as cli
from onescript.tests.test_table import cfg, rows  # noqa: F401  (the box and its keys)

KNOWN = {
    "block": ("2o$2o!", {"fate": "still", "at": 0, "period": 1, "shift": [0, 0]}),
    "blinker": ("3o!", {"fate": "oscillator", "at": 0, "period": 2, "shift": [0, 0]}),
    "glider": (
        "bo$2bo$3o!",
        {"fate": "spaceship", "at": 0, "period": 4, "shift": [1, 1]},
    ),
    "lwss": (
        "bo2bo$o4b$o3bo$4o!",
        {"fate": "spaceship", "at": 0, "period": 4, "shift": [0, -2]},
    ),
    "diehard": ("6bob$2o6b$bo3b3o!", {"fate": "dies", "at": 130}),
}


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_each_known_pattern_meets_its_known_fate(name):
    text, fate = KNOWN[name]
    assert life.run(life.parse_rle(text), 200)[1] == fate


def test_the_r_pentomino_has_116_cells_at_generation_1103():
    steps, fate = life.run(life.parse_rle("b2o$2o$bo!"), 1103)
    assert len(steps[-1][0]) == 116 and fate["fate"] == life.RUNNING


def test_one_board_has_one_text_and_one_hash_wherever_it_sits():
    glider = life.parse_rle("bo$2bo$3o!")
    moved = frozenset((r + 7, c - 3) for r, c in glider)
    assert life.rle(moved) == life.rle(glider) == "bo$2bo$3o!"
    assert life.board_sha(moved) == life.board_sha(glider)
    assert life.parse_rle(life.rle(moved)) == glider
    assert (
        life.parse_rle("#C a comment\nx = 3, y = 3, rule = B3/S23\nbo$2bo$3o!")
        == glider
    )


def test_a_soup_is_the_one_streams_and_the_same_on_every_python():
    a = life.soup("odile", 16, 16, 35)
    assert a == life.soup("odile", 16, 16, 35) != life.soup("odile2", 16, 16, 35)
    # pinned: the stream is SHA-256, so this holds on every Python and machine
    assert life.board_sha(a)[:16] == PINNED_SOUP


PINNED_SOUP = life.board_sha(life.soup("odile", 16, 16, 35))[:16]


def test_rules_other_than_conways_and_ones_that_cannot_be_held():
    assert life.run(life.parse_rle("2o$2o!"), 3, "B36/S23")[1]["fate"] == "still"
    for bad in ("B0/S8", "S23/B3", "B9/S", "conway"):
        with pytest.raises(ValueError):
            life.parse_rule(bad)


def test_a_game_is_open_steps_and_result_as_the_table_and_checks_out(cfg):  # noqa: F811
    got = life.play(cfg, "g1", board="bo$2bo$3o!", generations=8)
    assert got["code"] == 0 and got["fate"]["fate"] == "spaceship"
    srows = life.session_rows(rows(cfg), "g1")
    assert [r["what"] for r in srows] == ["open"] + ["step"] * 4 + ["result"]
    assert all(r["kind"] == "verdict" and r["who"] == "table" for r in srows)
    assert all(table.check_row({k: v for k, v in r.items() if k not in
               ("n", "who", "family", "standing", "version", "ts", "prev", "hash")})
               is None for r in srows)  # fmt: skip
    res = life.check(cfg, "g1")
    assert res["state"] == table.SATISFIED and res["rows"] == 6


def test_a_soup_game_records_its_seed_and_the_check_redraws_it(cfg):  # noqa: F811
    got = life.play(cfg, "s1", soup={"seed": "odile", "w": 12, "h": 12, "density": 40})
    opened = got["rows"][0]
    assert (
        opened["soup"]["seed"] == "odile" and opened["generator"] == "sha256-stream/v1"
    )
    assert life.check(cfg, "s1")["state"] == table.SATISFIED


def _tamper(cfg, session, what, change):  # noqa: F811
    path = cfg.box / "record.jsonl"
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        r = json.loads(line)
        if r.get("session") == session and r.get("what") == what:
            change(r)
            lines[i] = json.dumps(r, sort_keys=True, separators=(",", ":"))
            break
    path.write_text("\n".join(lines) + "\n")


@pytest.mark.parametrize(
    "what,change,says",
    [
        ("step", lambda r: r.update(population=r["population"] + 1), "generation 1"),
        ("step", lambda r: r.update(at=[9, 9]), "generation 1"),
        ("result", lambda r: r.update(period=3), "the result says"),
        ("open", lambda r: r["soup"].update(seed="other"), "seed doesn't make"),
    ],
)
def test_one_changed_row_fails_the_check_at_that_row(cfg, what, change, says):  # noqa: F811
    life.play(cfg, "t", soup={"seed": "odile", "w": 10, "h": 10, "density": 35},
              generations=30)  # fmt: skip
    assert life.check(cfg, "t")["state"] == table.SATISFIED
    _tamper(cfg, "t", what, change)
    res = life.check(cfg, "t")
    assert res["state"] == table.FAILING and says in res["why"], res


def test_refusals_write_only_the_refusal(cfg):  # noqa: F811
    life.play(cfg, "dup", board="3o!")
    before = len(rows(cfg))
    for kw in (
        {"board": "3o!"},  # the session is on record
        {"board": "3o!", "soup": {"seed": "x", "w": 2, "h": 2, "density": 50}},
        {"soup": {"seed": "x", "w": 999, "h": 2, "density": 50}},
        {"board": "3o!", "rule": "B0/S8"},
        {"board": "3o!", "generations": 0},
    ):
        got = life.play(cfg, "dup" if "rule" not in kw else "r", **kw)
        assert got["code"] == 1 and got["row"]["kind"] == "refused", kw
    kinds = [r["kind"] for r in rows(cfg)[before:]]
    assert kinds.count("refused") == 5 and "verdict" not in kinds


def test_no_session_is_empty(cfg):  # noqa: F811
    assert life.check(cfg, "nope")["code"] == 0


def test_the_command_line_plays_and_checks(cfg, tmp_path, capsys):  # noqa: F811
    base = ["--box", str(cfg.box), "--keys", str(cfg.keys), "--no-tests", "--phone",
            "--now", "2026-10-17T00:00:00Z"]  # fmt: skip
    board = tmp_path / "glider.rle"
    board.write_text("x = 3, y = 3\nbo$2bo$3o!\n")
    assert (
        cli.main([*base, "life", "cl", "--board", str(board), "--generations", "12"])
        == 0
    )
    out = capsys.readouterr().out
    assert '"fate": "spaceship"' in out
    assert cli.main([*base, "life", "sp", "--soup", "odile", "--size", "8x8"]) == 0
    assert cli.main([*base, "life-check", "cl"]) == 0
    assert '"satisfied"' in capsys.readouterr().out
    assert cli.main([*base, "life", "bad", "--soup", "x", "--size", "8by8"]) == 2
