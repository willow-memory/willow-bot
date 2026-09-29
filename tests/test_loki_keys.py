"""Loki reads keys from the box, never the tombstoned ~/.willow (2026-09-29).

Order: the process env (a unit loads the box's $WILLOW_HOME/env), then the
box's Fernet vault ($WILLOW_VAULT_BOX, else $WILLOW_HOME). A vault sitting
in ~/.willow is never consulted, even when it holds the key asked for.
"""
from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("httpx")
fernet = pytest.importorskip("cryptography.fernet")

from loki import cerebras  # noqa: E402


def _vault(root, **creds):
    root.mkdir(parents=True, exist_ok=True)
    key = fernet.Fernet.generate_key()
    (root / "vault.key").write_bytes(key)
    f = fernet.Fernet(key)
    conn = sqlite3.connect(str(root / "vault.db"))
    conn.execute("CREATE TABLE credentials (name TEXT PRIMARY KEY, value_enc BLOB)")
    for name, value in creds.items():
        conn.execute("INSERT INTO credentials VALUES (?, ?)", (name, f.encrypt(value.encode())))
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in ("WILLOW_VAULT_BOX", "WILLOW_HOME", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_env_wins_before_any_vault(monkeypatch, tmp_path):
    _vault(tmp_path / "box", GROQ_API_KEY="from-vault")
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "box"))
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert cerebras._read_vault("GROQ_API_KEY") == "from-env"


def test_box_vault_under_willow_home(monkeypatch, tmp_path):
    _vault(tmp_path / "box", CEREBRAS_API_KEY="from-box")
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "box"))
    assert cerebras._read_vault("CEREBRAS_API_KEY") == "from-box"


def test_vault_box_beats_willow_home(monkeypatch, tmp_path):
    _vault(tmp_path / "home-box", CEREBRAS_API_KEY="from-home")
    _vault(tmp_path / "vault-box", CEREBRAS_API_KEY="from-vault-box")
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "home-box"))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path / "vault-box"))
    assert cerebras._read_vault("CEREBRAS_API_KEY") == "from-vault-box"


def test_tombstoned_home_vault_is_never_read(tmp_path):
    _vault(tmp_path / "home" / ".willow", CEREBRAS_API_KEY="from-tombstone")
    (tmp_path / "home" / ".willow" / ".master.key").write_bytes(
        (tmp_path / "home" / ".willow" / "vault.key").read_bytes()
    )
    with pytest.raises(KeyError):
        cerebras._read_vault("CEREBRAS_API_KEY")


def test_missing_key_in_box_vault_is_a_keyerror(monkeypatch, tmp_path):
    _vault(tmp_path / "box", GROQ_API_KEY="only-groq")
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "box"))
    with pytest.raises(KeyError):
        cerebras._read_vault("CEREBRAS_API_KEY")
