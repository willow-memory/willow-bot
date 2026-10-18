"""forge_bite: the Forge's first bite with this session's pile as its box
(D9 A). The Forge is soft: a stand-in `forge.entry` drives every state here,
and one test runs the real one when it is importable with Nestor."""

from __future__ import annotations

import sys
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import api  # noqa: E402
from onescript.tests.test_api import cfg, rows  # noqa: E402,F401
from onescript.tests.test_store import BITE, box  # noqa: E402,F401


@dataclass(frozen=True)
class Hit:
    source: str


@dataclass(frozen=True)
class Option:
    label: str
    tradeoff: str


@dataclass(frozen=True)
class Decision:
    surface: str
    options: tuple


@dataclass
class Entry:
    major: str | None = None
    answer: str | None = None
    candidates: list = field(default_factory=list)
    tiers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def fake_forge(monkeypatch, behave):
    """A `forge.entry` whose `open_bite` hands the box `Hit`s and then does
    `behave(responder)`: returns a major, asks, or refuses."""
    mod = types.ModuleType("forge.entry")

    class EntryError(Exception):
        pass

    def open_bite(sentence, *, project_id, builder_id, responder, box, **kw):  # noqa: F811
        mod.called = {"project_id": project_id, "builder_id": builder_id, **kw}
        cands = list(box.lookup([Hit(w) for w in ("ad", "breaks")]))
        e = Entry(candidates=cands, tiers={"box": box.name})
        return behave(e, responder, EntryError)

    mod.EntryError, mod.open_bite = EntryError, open_bite
    pkg = types.ModuleType("forge")
    pkg.entry = mod
    monkeypatch.setitem(sys.modules, "forge", pkg)
    monkeypatch.setitem(sys.modules, "forge.entry", mod)
    return mod


def answers(e, responder, EntryError):
    e.major = "web"
    return e


def forge_rows(cfg):  # noqa: F811
    return [r for r in rows(cfg) if r["kind"] == "forge_bite"]


def test_the_pile_is_the_forges_box(box, monkeypatch):  # noqa: F811
    fake_forge(monkeypatch, answers)
    res = api.forge_bite(box["cfg"], BITE)
    assert res["code"] == 0 and res["state"] == "populated"
    assert res["box"]["state"] == "populated" and res["entry"]["candidates"]
    assert res["entry"]["candidates"][0]["name"].startswith("docs:")
    [row] = forge_rows(box["cfg"])
    assert row["state"] == "populated" and row["major"] == "web"
    assert row["box"] == "populated" and row["bite_hash"] == api.record.h256(BITE)
    assert "Nobody" not in str(row)  # the bite as its hash, never its words


def test_a_choice_comes_back_as_a_card_and_nothing_is_chosen(box, monkeypatch):  # noqa: F811
    def asks(e, responder, EntryError):
        d = Decision(
            "could be web, mobile — which major?",
            (Option("web", "a"), Option("mobile", "b")),
        )
        responder.choose(d)
        raise AssertionError("the responder answered for the maker")

    fake_forge(monkeypatch, asks)
    res = api.forge_bite(box["cfg"], BITE)
    assert res["state"] == "needs_you" and res["card"]["ask"] == "choose"
    assert [o["label"] for o in res["card"]["options"]] == ["web", "mobile"]
    assert forge_rows(box["cfg"])[0]["major"] is None


def test_a_confirm_is_never_said_yes_to(box, monkeypatch):  # noqa: F811
    def confirms(e, responder, EntryError):
        assert responder.confirm("you said app before — same?") is not None
        raise AssertionError("confirmed for the maker")

    fake_forge(monkeypatch, confirms)
    res = api.forge_bite(box["cfg"], BITE)
    assert res["state"] == "needs_you" and res["card"]["ask"] == "confirm"


def test_the_forges_refusal_is_unreachable_and_said(box, monkeypatch):  # noqa: F811
    def refuses(e, responder, EntryError):
        raise EntryError("Nestor is unavailable")

    fake_forge(monkeypatch, refuses)
    res = api.forge_bite(box["cfg"], BITE)
    assert res["state"] == "unreachable" and "Nestor is unavailable" in res["why"]


def test_no_keyword_and_no_candidate_is_empty(box, monkeypatch):  # noqa: F811
    def nothing(e, responder, EntryError):
        e.candidates = []
        return e

    fake_forge(monkeypatch, nothing)
    assert api.forge_bite(box["cfg"], BITE)["state"] == "empty"


def test_no_forge_is_unreachable_and_said(box, monkeypatch):  # noqa: F811
    monkeypatch.setitem(sys.modules, "forge", None)
    res = api.forge_bite(box["cfg"], BITE)
    assert res["state"] == "unreachable" and "isn't importable" in res["why"]
    assert forge_rows(box["cfg"])[0]["box"] == "unreachable"


def test_no_store_is_the_boxs_unreachable_with_its_why(box, monkeypatch):  # noqa: F811
    fake_forge(monkeypatch, answers)
    with open(api.pile_mod.store_path(box["cfg"].box), "ab") as f:
        f.write(b"\0")
    res = api.forge_bite(box["cfg"], BITE)
    assert res["box"]["state"] == "unreachable"
    assert res["box"]["why"].startswith("no store attached: ")
    assert res["entry"]["candidates"] == []


def test_a_closed_box_is_refused(box, monkeypatch):  # noqa: F811
    fake_forge(monkeypatch, answers)
    api.checkout(box["cfg"])
    assert api.forge_bite(box["cfg"], BITE)["code"] == 1
    assert forge_rows(box["cfg"]) == []


def test_the_cli_passes_project_builder_and_root(tmp_path, monkeypatch):
    got = {}

    def fake(cfg, bite, **kw):  # noqa: F811
        got.update(kw, bite=bite)
        return {"code": 0}

    monkeypatch.setattr(api, "forge_bite", fake)
    argv = ["forge", "an app", "--project", "p1", "--builder", "b1", "--root", "/r"]
    cli.main(["--box", str(tmp_path / "box"), "--keys", str(tmp_path / "k"), *argv])
    assert (got["bite"], got["project_id"], got["builder_id"]) == ("an app", "p1", "b1")
    assert got["root"] == Path("/r")


def test_the_real_forge_takes_the_pile_as_its_box(box, tmp_path, monkeypatch):  # noqa: F811
    entry = pytest.importorskip("forge.entry")
    if not entry.checkpoint_memory.nestor_available():
        pytest.skip("Nestor isn't installed here")
    monkeypatch.setenv("FORGE_HOME", str(tmp_path / "forge-home"))
    res = api.forge_bite(
        box["cfg"], "an app for the ad breaks", project_id="demo", root=tmp_path / "cp"
    )
    assert res["code"] == 0 and res["state"] in ("populated", "needs_you")
    assert res["box"]["state"] in ("populated", "empty")
    if res["state"] == "populated":
        assert res["entry"]["tiers"]["box"].startswith("willow pile")
