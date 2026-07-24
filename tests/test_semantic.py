"""
tests/test_semantic.py — willow-bot's first tests.
b17: LOKI3

The poetic proof: Loki catching redundant design. We seal the capabilities this
fleet actually has (including the ones over-designed this very session), then
show that a fresh spec re-describing one of them resolves back to the thing that
already exists — while a genuinely novel idea does not.

Offline: nestor is imported from the working tree (pip install -e /workspace/nestor);
stdlib + pytest only. Nothing writes into the repo — the ledger is redirected to
a tmp path and every store is a temp/in-memory SqliteStore.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nestor import cascade
from nestor.sqlite_store import SqliteStore

from loki.semantic import ExistenceIndex, spec_summary
from loki.accumulator import Accumulator, Signal


# -- fixtures --------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path):
    """Keep Nestor's hash-chained ledger out of the repo for every test."""
    cascade.set_ledger_path(tmp_path / "ledger.jsonl")
    yield


@pytest.fixture
def store(tmp_path):
    """A temp SqliteStore — nothing persists into the repo."""
    return SqliteStore(str(tmp_path / "existence.db"))


@pytest.fixture
def fleet_index(store):
    """An ExistenceIndex sealed with the capabilities this fleet already has.

    These are the real, existing things — including the components over-designed
    THIS session — that a new spec should be caught re-describing.
    """
    idx = ExistenceIndex(store=store)
    idx.seal_known(
        "shared entity graph component clusters edges resolve aliases",
        "willow-compose:entity-graph",
        {"source": "session", "gap": 15, "note": "entity-graph engine"},
    )
    idx.seal_known(
        "monitor watcher continuous watch witness",
        "willow-bot:loki",
        {"source": "session", "note": "the watcher itself"},
    )
    idx.seal_known(
        "row-level provenance content sha minhash source path",
        "willow-compose:provenance",
        {"source": "session", "note": "provenance rows"},
    )
    idx.seal_known(
        "translation memory fuzzy match sealed pairs",
        "nestor:memory",
        {"source": "session", "note": "the seal/serve memory"},
    )
    return idx


# -- the poetic proof: Loki catches redundant design -----------------------

def test_catches_entity_graph_redesign(fleet_index):
    res = fleet_index.resolve_existing("build a shared entity graph for the ledger pair")
    assert res["exists"] is True
    assert res["canonical"] == "willow-compose:entity-graph"
    assert res["confidence"] >= 0.60
    assert res["provenance"].get("source") == "session"


def test_catches_monitor_primitive_redesign(fleet_index):
    res = fleet_index.resolve_existing("add a native monitor / watch primitive")
    assert res["exists"] is True
    assert res["canonical"] == "willow-bot:loki"


def test_novel_idea_is_not_flagged(fleet_index):
    res = fleet_index.resolve_existing("a klein-bottle holographic quarterly tax optimizer")
    assert res["exists"] is False
    assert res["canonical"] is None


# -- build_from_catalog ----------------------------------------------------

def _tiny_catalog(tmp_path) -> Path:
    catalog = {
        "apps": [
            {
                "id": "law-gazelle",
                "name": "Law Gazelle",
                "description": "legal case intake tracker managing client matters and deadlines",
            },
            {
                "id": "grove",
                "name": "Grove",
                "description": "multi-agent message bus channels senders postgres notify",
            },
        ]
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    return path


def test_build_from_catalog_finds_paraphrase(store, tmp_path):
    catalog_path = _tiny_catalog(tmp_path)
    idx = ExistenceIndex(store=store)
    n = idx.build_from_catalog(str(catalog_path))
    assert n == 2

    # A paraphrase of the Grove app description resolves back to grove.
    res = idx.resolve_existing("a message bus for agents with channels and senders over postgres")
    assert res["exists"] is True
    assert res["canonical"] == "grove"
    assert res["provenance"] == {"source": "catalog", "id": "grove"}


# -- build_from_pieces (documented seam) -----------------------------------

def test_build_from_pieces_seals_offline(store):
    idx = ExistenceIndex(store=store)
    pieces = [
        {"piece_key": "wc:entity-graph", "label": "entity graph", "ref": "clusters edges aliases resolve"},
        {"piece_key": "wc:provenance", "label": "provenance", "ref": "content sha minhash source path"},
    ]
    n = idx.build_from_pieces(pieces)
    assert n == 2
    res = idx.resolve_existing("cluster edges and resolve aliases in an entity graph")
    assert res["exists"] is True
    assert res["canonical"] == "wc:entity-graph"
    assert res["provenance"]["source"] == "willow_compose.pieces"


# -- spec_summary ----------------------------------------------------------

def test_spec_summary_extracts_title_and_prose(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "# Entity Graph Service\n"
        "b17: XYZ1\n"
        "Status: Draft\n"
        "\n"
        "A shared entity graph that clusters edges and resolves aliases across the ledger pair.\n"
        "\n"
        "## Details\n"
        "Not part of the summary.\n"
    )
    summary = spec_summary(str(spec))
    assert "Entity Graph Service" in summary
    assert "resolves aliases" in summary
    assert "b17" not in summary
    assert "Status" not in summary
    assert "Not part of the summary" not in summary


def test_spec_summary_missing_file_is_empty():
    assert spec_summary("/no/such/spec.md") == ""


# -- check_mistletoe_semantic wiring ---------------------------------------

def test_check_mistletoe_semantic_fires_on_existing(fleet_index, tmp_path):
    acc = Accumulator(state_path=tmp_path / "state.json")
    spec = tmp_path / "redesign.md"
    spec.write_text(
        "# Shared Entity Graph\n"
        "\n"
        "Build a shared entity graph for the ledger pair to resolve aliases.\n"
    )
    text = spec_summary(str(spec))
    sig = acc.check_mistletoe_semantic(str(spec), text, fleet_index)
    assert sig is not None
    assert isinstance(sig, Signal)
    assert sig.trigger == "Mistletoe"
    assert sig.evidence["matched_canonical"] == "willow-compose:entity-graph"
    assert "confidence" in sig.evidence
    assert "provenance" in sig.evidence


def test_check_mistletoe_semantic_silent_on_novel(fleet_index, tmp_path):
    acc = Accumulator(state_path=tmp_path / "state.json")
    spec = tmp_path / "novel.md"
    spec.write_text(
        "# Klein Bottle Tax Optimizer\n"
        "\n"
        "A klein-bottle holographic quarterly tax optimizer for interdimensional filings.\n"
    )
    text = spec_summary(str(spec))
    sig = acc.check_mistletoe_semantic(str(spec), text, fleet_index)
    assert sig is None


def test_check_mistletoe_semantic_dedups(fleet_index, tmp_path):
    acc = Accumulator(state_path=tmp_path / "state.json")
    spec = tmp_path / "redesign.md"
    spec.write_text("# Shared Entity Graph\n\nBuild a shared entity graph for the ledger pair.\n")
    text = spec_summary(str(spec))
    first = acc.check_mistletoe_semantic(str(spec), text, fleet_index)
    assert first is not None
    # Seen once — the complementary checks never raise the same spec twice.
    second = acc.check_mistletoe_semantic(str(spec), text, fleet_index)
    assert second is None
