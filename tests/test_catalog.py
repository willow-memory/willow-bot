"""
tests/test_catalog.py — the repo<->catalog crosswalk that stops Loki over-firing.
b17: LOKI3

Proves the two false positives are fixed: a cataloged app whose repo dir differs
from its id (safe-app-grove -> grove) is recognized as cataloged, and infra repos
(willow-mcp) that declare no safe-app-manifest.json are never treated as apps.

stdlib + pytest only; every store is a tmp dir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loki.catalog import (
    CatalogIndex,
    has_manifest,
    read_manifest_app_id,
)
from loki.accumulator import Accumulator, Signal


# -- fixtures --------------------------------------------------------------

@pytest.fixture
def catalog_path(tmp_path) -> Path:
    catalog = {
        "apps": [
            {"id": "grove", "name": "Grove",
             "repository": "https://github.com/rudi193-cmd/safe-app-grove"},
            {"id": "willow-grove", "name": "Willow Grove",
             "repository": "https://github.com/rudi193-cmd/safe-app-willow-grove"},
            {"id": "law-gazelle", "name": "Law Gazelle", "path": "apps/law-gazelle"},
        ]
    }
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps(catalog))
    return p


@pytest.fixture
def catalog(catalog_path) -> CatalogIndex:
    return CatalogIndex.load(catalog_path)


def _make_repo(root: Path, name: str, app_id: str | None = None) -> Path:
    repo = root / name
    repo.mkdir()
    if app_id is not None:
        (repo / "safe-app-manifest.json").write_text(json.dumps({"app_id": app_id}))
    return repo


# -- the crosswalk: no more false "uncataloged" -----------------------------

def test_id_match(catalog):
    assert catalog.is_cataloged("grove")


def test_repository_basename_match(catalog):
    # The real bug: the repo dir is safe-app-grove, the id is grove.
    assert catalog.is_cataloged("safe-app-grove")
    assert catalog.resolve("safe-app-grove") == "grove"
    assert catalog.is_cataloged("safe-app-willow-grove")
    assert catalog.resolve("safe-app-willow-grove") == "willow-grove"


def test_prefix_strip_without_repository_field(tmp_path):
    # law-gazelle has no repository field; a repo dir safe-app-law-gazelle
    # should still resolve via the safe-app- prefix strip.
    cat = CatalogIndex.load(
        _write_catalog(tmp_path, [{"id": "law-gazelle", "name": "Law Gazelle"}])
    )
    assert cat.is_cataloged("safe-app-law-gazelle")
    assert cat.resolve("safe-app-law-gazelle") == "law-gazelle"


def test_manifest_app_id_fallback(catalog):
    # An oddly-named repo dir whose manifest declares a cataloged app_id.
    assert catalog.is_cataloged("some-weird-worktree-dir", manifest_app_id="law-gazelle")


def test_infra_repo_is_not_cataloged(catalog):
    # willow-mcp / willow-bot are infra, not apps — not in the catalog at all.
    assert not catalog.is_cataloged("willow-mcp")
    assert catalog.resolve("willow-mcp") is None


def test_load_missing_catalog_is_empty(tmp_path):
    cat = CatalogIndex.load(tmp_path / "nope.json")
    assert cat.identities == set()
    assert not cat.is_cataloged("grove")


# -- manifest helpers: the infra gate --------------------------------------

def test_has_manifest_and_app_id(tmp_path):
    app = _make_repo(tmp_path, "safe-app-willow-grove", app_id="willow-grove")
    infra = _make_repo(tmp_path, "willow-mcp", app_id=None)
    assert has_manifest(app)
    assert read_manifest_app_id(app) == "willow-grove"
    assert not has_manifest(infra)
    assert read_manifest_app_id(infra) is None


# -- accumulator checks now use the crosswalk ------------------------------

def test_check_mistletoe_silent_on_dir_id_mismatch(catalog, tmp_path):
    # A spec inside safe-app-grove must NOT fire — grove is cataloged.
    acc = Accumulator(state_path=tmp_path / "state.json")
    spec = "/home/sean-campbell/github/safe-app-grove/docs/spec.md"
    sig = acc.check_mistletoe(spec, catalog, manifest_app_id="grove")
    assert sig is None


def test_check_mistletoe_fires_on_truly_uncataloged(catalog, tmp_path):
    acc = Accumulator(state_path=tmp_path / "state.json")
    spec = "/home/sean-campbell/github/safe-app-newthing/spec.md"
    sig = acc.check_mistletoe(spec, catalog, manifest_app_id="newthing")
    assert isinstance(sig, Signal)
    assert sig.trigger == "Mistletoe"
    assert sig.evidence["repo"] == "safe-app-newthing"
    # dedup: same spec never fires twice
    assert acc.check_mistletoe(spec, catalog, manifest_app_id="newthing") is None


def test_check_hermes_silent_on_cataloged_via_crosswalk(catalog, tmp_path):
    acc = Accumulator(state_path=tmp_path / "state.json")
    repo = "/home/sean-campbell/github/safe-app-willow-grove"
    sig = acc.check_hermes(repo, catalog, last_commit_ts=1.0, manifest_app_id="willow-grove")
    assert sig is None


def _write_catalog(tmp_path, apps) -> Path:
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({"apps": apps}))
    return p
