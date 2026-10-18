"""The store built in the background: check-in never waits on it, and the
first verb after it is ready attaches it (the operator: "loaded everything up
in the background, in anticipation of my next enter stroke")."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import api, pile  # noqa: E402
from onescript.tests.test_api import cfg, rows  # noqa: E402,F401
from onescript.tests.test_store import BITE, box  # noqa: E402,F401


@pytest.fixture
def bg(box, monkeypatch):  # noqa: F811
    """The same box, background on, its store gone, the spawn recorded."""
    spawned: list[Path] = []
    monkeypatch.setattr(api, "_spawn_build", spawned.append)
    pile.store_path(box["cfg"].box).unlink()
    return {
        **box,
        "cfg": replace(box["cfg"], store_background=True),
        "spawned": spawned,
    }


def stores(cfg) -> list[dict]:  # noqa: F811
    return [r for r in rows(cfg) if r["kind"] == "store"]


def test_check_in_does_not_wait_on_the_build(bg):
    res = api.checkin(bg["cfg"])
    assert res["code"] == 0 and res["report"]["store"]["state"] == "building"
    assert len(bg["spawned"]) == 1
    assert not pile.store_path(bg["cfg"].box).exists()  # nothing built in check-in


def test_before_the_build_is_done_the_store_says_so(bg):
    api.checkin(bg["cfg"])
    res = api.seed(bg["cfg"], BITE)
    assert res["store"]["state"] == "unreachable"
    assert "still building" in res["store"]["why"]
    assert stores(bg["cfg"])[-1]["state"] == "building"


def test_the_first_verb_after_the_build_attaches_it(bg):
    api.checkin(bg["cfg"])
    assert pile.build_from_spec(bg["spawned"][0]) == 0  # the builder, in-process
    res = api.seed(bg["cfg"], BITE)
    assert res["store"]["state"] == "attached" and res["rank"]
    last = stores(bg["cfg"])[-1]
    assert last["state"] == "attached" and last["how"] == "built in the background"
    assert last["store_sha256"] == pile.file_sha256(pile.store_path(bg["cfg"].box))


def test_a_warm_store_attaches_at_check_in_with_no_build(box, monkeypatch):  # noqa: F811
    spawned: list = []
    monkeypatch.setattr(api, "_spawn_build", spawned.append)
    res = api.checkin(replace(box["cfg"], store_background=True))
    assert res["report"]["store"]["state"] == "attached" and spawned == []


def test_a_build_in_progress_is_not_started_twice(bg):
    lock = pile.store_path(bg["cfg"].box).parent / pile.LOCK
    lock.write_text(str(os.getpid()))  # a live process holds it
    api.checkin(bg["cfg"])
    assert bg["spawned"] == [] and stores(bg["cfg"])[-1]["state"] == "building"
    assert pile.building(bg["cfg"].box)


def test_a_stale_lock_is_cleared(bg):
    lock = pile.store_path(bg["cfg"].box).parent / pile.LOCK
    lock.write_text("999999999")  # no such process
    assert not pile.building(bg["cfg"].box) and not lock.exists()


def test_the_builder_refuses_an_index_that_moved(bg):
    api.checkin(bg["cfg"])
    spec = bg["spawned"][0]
    text = spec.read_text().replace('"index_sha256": "', '"index_sha256": "0', 1)
    spec.write_text(text)
    assert pile.build_from_spec(spec) == 1
    assert not pile.store_path(bg["cfg"].box).exists()


def test_the_spec_names_where_the_key_is_never_the_key(bg):
    api.checkin(bg["cfg"])
    key, _ = api._store_key(bg["cfg"], bg["cfg"].box)
    text = bg["spawned"][0].read_text()
    assert key.hex() not in text and "key_path" in text
