"""credentials.resolve reads the operator data vault, not ~/.willow/secrets."""
from __future__ import annotations

from pathlib import Path

import pytest

import credentials


def test_vault_box_has_no_default_box(monkeypatch, tmp_path):
    """No fallback (operator, 2026-09-28): the box is wherever
    willow-data-vault provisioned it, named by the env — never guessed from
    $USER or one operator's layout."""
    from willow_bot.paths import BoxNotConfigured

    monkeypatch.delenv("WILLOW_VAULT_BOX", raising=False)
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.setenv("USER", "sean-campbell")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(BoxNotConfigured, match="provision.sh"):
        credentials.vault_box()
    with pytest.raises(BoxNotConfigured):
        credentials.resolve(require_complete=False)
    assert list(tmp_path.iterdir()) == []  # nothing created under a guessed path


def test_vault_box_follows_the_same_rule_as_the_state(monkeypatch, tmp_path):
    """One rule (Loki A726C6F8): WILLOW_HOME, else WILLOW_VAULT_BOX — the
    same order paths.willow_home() reads, so secrets and state never split."""
    (tmp_path / "home").mkdir()
    (tmp_path / "box").mkdir()
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path / "box"))
    assert credentials.vault_box() == tmp_path / "home"
    monkeypatch.setenv("WILLOW_HOME", "  ")
    assert credentials.vault_box() == tmp_path / "box"


def test_resolve_from_secrets_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    for k in ("GITHUB_APP_ID", "GITHUB_WEBHOOK_SECRET", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    pem = secrets / "willow-bot.pem"
    pem.write_text("-----BEGIN RSA PRIVATE KEY-----\nTEST\n-----END RSA PRIVATE KEY-----\n")
    (secrets / "willow-bot.env").write_text(
        "GITHUB_APP_ID=12345\nGITHUB_WEBHOOK_SECRET=s3cret\n"
    )
    cred = credentials.resolve(require_complete=True)
    assert cred.app_id == "12345"
    assert cred.webhook_secret == "s3cret"
    assert "TEST" in cred.private_key_pem
    assert cred.private_key_path == pem


def test_env_overrides_vault_files(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "willow-bot.env").write_text(
        "GITHUB_APP_ID=from-file\nGITHUB_WEBHOOK_SECRET=from-file\n"
    )
    (secrets / "willow-bot.pem").write_text("FILEKEY")
    monkeypatch.setenv("GITHUB_APP_ID", "from-env")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "env-secret")
    cred = credentials.resolve(require_complete=True)
    assert cred.app_id == "from-env"
    assert cred.webhook_secret == "env-secret"


def test_incomplete_raises_with_vault_path(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    for k in ("GITHUB_APP_ID", "GITHUB_WEBHOOK_SECRET", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "secrets").mkdir()
    try:
        credentials.resolve(require_complete=True)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert str(tmp_path) in str(exc)
