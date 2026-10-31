"""willow_bot.pypi_fresh — each venv's packages against PyPI, offline.

PyPI's answers are stubbed (``_fetch_json``): nothing here reaches it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from willow_bot import pypi_fresh as pf

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


def _files(days_ago: float, yanked: bool = False) -> list[dict]:
    when = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return [{"upload_time_iso_8601": when, "yanked": yanked}]


def _project(**releases) -> dict:
    return {"releases": {v.replace("_", "."): f for v, f in releases.items()}}


def test_a_known_vulnerability_names_the_smallest_fix_and_whether_the_range_allows_it():
    proj = _project(**{"2_7_0": _files(90), "2_7_1": _files(1), "2_8_0": _files(30), "3_0_0": _files(20)})
    rel = {"vulnerabilities": [{"id": "A", "fixed_in": ["2.7.1"]}, {"id": "B", "fixed_in": ["2.8.0"]}]}
    got = pf.classify("urllib3", "2.7.0", [("requests", "<3,>=1.21.1")], proj, rel, NOW)
    assert got["kind"] == "vulnerable" and got["fix"] == "2.8.0", "the smallest release that fixes both"
    assert got["fix_in_range"] is True and got["vulnerabilities"] == ["A", "B"]
    capped = pf.classify("urllib3", "2.7.0", [("x", "<2.8")], proj, rel, NOW)
    assert capped["fix"] == "2.8.0" and capped["fix_in_range"] is False


def test_a_fix_waits_for_no_cooldown():
    proj = _project(**{"1_0_0": _files(90), "1_0_1": _files(0.5)})
    rel = {"vulnerabilities": [{"id": "A", "fixed_in": ["1.0.1"]}]}
    assert pf.classify("p", "1.0.0", [], proj, rel, NOW)["fix"] == "1.0.1"


def test_a_patch_is_an_old_enough_release_in_the_same_minor_and_every_range():
    proj = _project(**{"1_2_0": _files(90), "1_2_1": _files(10), "1_2_2": _files(3), "1_3_0": _files(30)})
    got = pf.classify("p", "1.2.0", [("a", ">=1.0")], proj, {}, NOW)
    assert (got["kind"], got["to"]) == ("patch", "1.2.1"), "1.2.2 is younger than the cooldown"


def test_beyond_the_patch_it_is_minor_or_major_and_the_persons():
    proj = _project(**{"1_2_0": _files(90), "1_3_0": _files(30), "2_0_0": _files(30)})
    assert pf.classify("p", "1.2.0", [], proj, {}, NOW)["kind"] == "minor"
    proj = _project(**{"1_2_0": _files(90), "2_0_0": _files(30)})
    got = pf.classify("p", "1.2.0", [("a", "<2")], proj, {}, NOW)
    assert got["kind"] == "major" and got["in_range"] is False


def test_an_exact_pin_is_on_purpose_and_a_wildcard_is_a_range():
    proj = _project(**{"0_42_0": _files(90), "0_42_9": _files(30)})
    assert pf.classify("uvicorn", "0.42.0", [("willow_bot", "==0.42.0")], proj, {}, NOW)["kind"] == "pinned"
    assert pf.classify("pygments", "0.42.0", [("httpx", "==0.*")], proj, {}, NOW)["kind"] == "patch"


def test_a_pinned_package_is_still_reported_vulnerable():
    proj = _project(**{"2_32_3": _files(200), "2_32_4": _files(100)})
    rel = {"vulnerabilities": [{"id": "A", "fixed_in": ["2.32.4"]}]}
    got = pf.classify("requests", "2.32.3", [("willow_bot", "==2.32.3")], proj, rel, NOW)
    assert got["kind"] == "vulnerable" and got["fix_in_range"] is False and got["pinned_by"] == ["willow_bot"]


def test_prereleases_and_yanked_releases_are_never_candidates():
    proj = _project(**{"1_0_0": _files(90), "1_0_1rc1": _files(30), "1_0_2": _files(30, yanked=True)})
    assert pf.classify("p", "1.0.0", [], proj, {}, NOW)["kind"] == "current"


def test_a_yanked_install_is_said():
    proj = _project(**{"1_0_0": _files(90)})
    assert pf.classify("p", "1.0.0", [], proj, {"info": {"yanked": True}}, NOW)["kind"] == "yanked"


def _venv(root: Path) -> Path:
    site = root / "lib" / "python3.12" / "site-packages"
    for name, version, meta in (
        ("urllib3", "2.7.0", ""),
        ("requests", "2.34.2", "Requires-Dist: urllib3<3,>=1.21.1\nRequires-Dist: idna<4,>=2.5\n"),
        ("private_pkg", "0.1.0", ""),
        ("pip", "26.2.1", ""),
    ):
        dist = site / f"{name}-{version}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(f"Name: {name}\n{meta}")
    (root / "pyvenv.cfg").write_text("version_info = 3.12.4\n")
    return root


def test_a_venv_report_asks_pypi_once_per_package_and_marks_the_unknown(tmp_path, monkeypatch):
    venv = _venv(tmp_path / "v")
    asked: list[str] = []

    def fetch(url: str) -> dict:
        asked.append(url)
        if "private-pkg" in url:
            raise RuntimeError("404 Not Found")
        if url.endswith("/urllib3/json"):
            return _project(**{"2_7_0": _files(90), "2_8_0": _files(30)})
        if url.endswith("/urllib3/2.7.0/json"):
            return {"vulnerabilities": [{"id": "A", "fixed_in": ["2.8.0"]}]}
        if url.endswith("/requests/json"):
            return _project(**{"2_34_2": _files(90)})
        return {}

    monkeypatch.setattr(pf, "_fetch_json", fetch)
    got = pf.venv_report(venv, pf._PyPI(), NOW)

    by = {p["name"]: p for p in got["packages"]}
    assert by["urllib3"]["kind"] == "vulnerable" and by["urllib3"]["fix_in_range"] is True
    assert by["private-pkg"]["status"] == "unreachable", "not on PyPI is unknown, never current"
    assert "requests" not in by, "current packages aren't listed"
    assert not any("/pip/" in u for u in asked), "pip's own tooling isn't asked about"
    assert got["counts"] == {"vulnerable": 1, "current": 1, "unreachable": 1}


def test_ranges_come_from_every_installed_requirement(tmp_path):
    venv = _venv(tmp_path / "v")
    assert pf.declared(venv)["urllib3"] == [("requests", "<3,>=1.21.1")]


@pytest.mark.parametrize("spec, exact", [("==1.2.3", True), ("===x", True), ("==2.*", False), (">=1,<2", False),
                                         ("", False)])  # fmt: skip
def test_what_counts_as_an_exact_pin(spec, exact):
    assert pf._exact(spec) is exact
