"""
tests/test_quips_integration.py — quips.pick() extras: runes on pr_opened, horoscope on pr_merged.
"""
import random

import quips


def test_pr_opened_includes_rune_reading(monkeypatch, tmp_path):
    monkeypatch.setattr(quips, "_DB_PATH", tmp_path / "contributors.db")
    quips.load_config()
    line = quips.pick("pr_opened", "someuser", sha="abc123deadbeef0000000000000000000000000000")
    assert "**" in line
    assert "—" in line
    assert line.count("\n\n") >= 1
    base, rune_line = line.rsplit("\n\n", 1)
    assert base
    assert rune_line.startswith(">")


def test_pr_opened_without_sha_has_no_rune(monkeypatch, tmp_path):
    monkeypatch.setattr(quips, "_DB_PATH", tmp_path / "contributors.db")
    quips.load_config()
    line = quips.pick("pr_opened", "someuser")
    assert not line.startswith(">")
    assert "\n\n" not in line


def test_pr_merged_includes_horoscope(tmp_path, monkeypatch):
    monkeypatch.setattr(quips, "_DB_PATH", tmp_path / "contributors.db")
    monkeypatch.delenv("FRANK_MODE", raising=False)
    monkeypatch.delenv("PROPHET_MODE", raising=False)
    monkeypatch.setattr(random, "random", lambda: 1.0)

    line = quips.pick("pr_merged", "someuser")

    assert "**Thrall someuser** —" in line
    assert "\n\n_Today" in line
