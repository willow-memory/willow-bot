"""Outside data, fetched once into the box (snapshot.py; IDEAS.md 33-48).

A snapshot is read through the operator's fetcher once, kept under its
SHA-256, and answered from the box after that; the record holds hashes, never
the URL; no fetcher is `unreachable`, a held file that no longer hashes is
`unreachable`, and nothing held is `empty`. Every one of those is tried.
"""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from onescript import api, snapshot
from onescript.states import EMPTY, POPULATED, UNREACHABLE
from onescript.tests.test_table import cfg, rows  # noqa: F401  (the box and its keys)

URL = "https://example.org/quakes.geojson?lat=51.5&lon=-0.1"
DATA = b'{"features": []}'


class Fetcher:
    """The fetcher, offline: what each URL gives, and how often it was asked."""

    def __init__(self, pages: dict[str, bytes] | None = None) -> None:
        self.pages = dict({URL: DATA} if pages is None else pages)
        self.asked: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.asked.append(url)
        if url not in self.pages:
            raise LookupError(f"{url} is not served here")
        return self.pages[url]


@pytest.fixture
def box(cfg):
    return dataclasses.replace(cfg, fetch=Fetcher())


def snap_rows(c) -> list[dict]:
    return [r for r in rows(c) if r["kind"] == snapshot.KIND]


def test_a_take_keeps_the_bytes_under_their_hash_and_the_row_holds_no_url(box):
    res = snapshot.take(box, "quakes", URL, license="CC0-1.0")
    digest = hashlib.sha256(DATA).hexdigest()
    assert res["state"] == POPULATED and res["source"] == "fetched"
    assert res["sha256"] == digest and res["data"] == DATA
    assert (box.box / "snapshots" / "quakes" / f"{digest}.bin").read_bytes() == DATA
    (row,) = snap_rows(box)
    assert row["sha256"] == digest and row["license"] == "CC0-1.0"
    assert row["url_sha256"] == hashlib.sha256(URL.encode()).hexdigest()
    assert "lat=51.5" not in (box.box / "record.jsonl").read_text()


def test_asked_again_it_answers_from_the_box_and_fetches_nothing(box):
    snapshot.take(box, "quakes", URL, license="CC0-1.0")
    again = snapshot.get(box, "quakes", URL, license="CC0-1.0")
    assert again["state"] == POPULATED and again["source"] == "box"
    assert box.fetch.asked == [URL] and len(snap_rows(box)) == 1


def test_only_a_refresh_fetches_again_as_a_new_row(box):
    snapshot.take(box, "quakes", URL, license="CC0-1.0")
    box.fetch.pages[URL] = b'{"features": [1]}'
    res = snapshot.take(box, "quakes", URL, license="CC0-1.0", refresh=True)
    assert res["source"] == "fetched" and box.fetch.asked == [URL, URL]
    old, new = snap_rows(box)
    assert old["sha256"] != new["sha256"] and new["refresh"] is True
    assert snapshot.read(box, "quakes")["data"] == b'{"features": [1]}'
    assert snapshot.read(box, "quakes", sha256_of=old["sha256"])["data"] == DATA


def test_no_fetcher_is_unreachable_and_writes_no_snapshot(cfg):
    res = snapshot.take(cfg, "quakes", URL, license="CC0-1.0")
    assert res["state"] == UNREACHABLE and "--fetch" in res["why"]
    assert snap_rows(cfg) == []


def test_a_fetch_that_fails_is_unreachable_said(box):
    res = snapshot.take(box, "tides", "https://example.org/nope", license="CC0-1.0")
    assert res["state"] == UNREACHABLE and "LookupError" in res["why"]
    assert snap_rows(box) == []


def test_nothing_held_is_empty_not_unreachable(box):
    assert snapshot.read(box, "quakes")["state"] == EMPTY


def test_a_held_file_that_changed_is_unreachable_and_is_fetched_again(box):
    res = snapshot.take(box, "quakes", URL, license="CC0-1.0")
    (box.box / "snapshots" / "quakes" / f"{res['sha256']}.bin").write_bytes(b"x")
    read = snapshot.read(box, "quakes")
    assert read["state"] == UNREACHABLE and "no longer hashes" in read["why"]
    again = snapshot.get(box, "quakes", URL, license="CC0-1.0")
    assert again["source"] == "fetched" and len(snap_rows(box)) == 2


@pytest.mark.parametrize(
    "name,url,lic",
    [
        ("Quakes", URL, "CC0-1.0"),  # not lowercase
        ("quakes", "http://example.org/x", "CC0-1.0"),  # not https
        ("quakes", URL, "public domain"),  # not an SPDX id
        ("quakes", URL, "public-domain"),  # id-shaped, still not an SPDX id
        ("quakes", URL, "Open"),
        ("quakes", URL, "non-commercial"),
    ],
)
def test_a_bad_take_is_refused_before_the_box_opens(box, name, url, lic):
    res = snapshot.take(box, name, url, license=lic)
    assert "refused" in res and box.fetch.asked == []


def test_a_closed_box_moves_nothing(box):
    assert api.checkout(box)["code"] == 0
    res = snapshot.take(box, "quakes", URL, license="CC0-1.0")
    assert "refused" in res and box.fetch.asked == []


def test_text_and_json_read_a_populated_answer(box):
    res = snapshot.take(box, "quakes", URL, license="CC0-1.0")
    assert snapshot.as_json(res) == {"features": []}
    assert snapshot.text({"state": EMPTY}) is None


def test_the_url_in_argv_reaches_the_record_as_its_hash(box):
    argv = ["--fetch", "snapshot", "quakes", URL, "--license", "CC0-1.0"]
    snapshot.take(box, "quakes", URL, license="CC0-1.0", argv=argv)
    text = (box.box / "record.jsonl").read_text()
    assert "example.org" not in text
    assert f"url-sha256:{hashlib.sha256(URL.encode()).hexdigest()}" in text


@pytest.mark.parametrize(
    "lic",
    ["CC0-1.0", "CC-BY-NC-SA-3.0-IGO", "GPL-3.0-only", "MIT", "BSD-3-Clause",
     "LicenseRef-public-domain"],
)  # fmt: skip
def test_an_spdx_id_or_a_license_ref_stands(lic):
    assert snapshot.check("quakes", URL, lic) is None
