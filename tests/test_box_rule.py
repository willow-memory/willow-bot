"""The box rule's edges and every place that reads it (Loki 757108E8).

No fallback (operator, 2026-09-28): the box is WILLOW_HOME, else
WILLOW_VAULT_BOX, an existing absolute directory. These pin the edges the
first two rounds left untested: the steward's state path, a relative
value, `~` expansion, the scripts' key path, the carried-over counter's
mode, and the carry-over lock held for the whole copy.
"""
from __future__ import annotations

import fcntl
import importlib.util
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from willow_bot import paths, persona_store
from willow_bot.steward import config

ROOT = Path(__file__).resolve().parents[1]


def _real_state_path():
    # tests/conftest.py wraps state_path in a sandbox guard; the guard calls
    # the real one, which is what these tests exercise.
    return config.state_path()


# ── the steward's state path ────────────────────────────────────────────────

def test_state_path_is_in_the_box(tmp_path):
    assert _real_state_path() == tmp_path / "loki_pr_watch_state.json"


def test_state_path_ignores_a_blank_vault_box_and_blank_overrides(monkeypatch, tmp_path):
    """N1: a blank WILLOW_VAULT_BOX used to put state at /loki_pr_watch_state.json."""
    monkeypatch.setenv("WILLOW_VAULT_BOX", "")
    monkeypatch.setenv("WILLOW_BOT_STEWARD_STATE", "  ")
    monkeypatch.setenv("LOKI_PR_WATCH_STATE", "")
    assert _real_state_path() == tmp_path / "loki_pr_watch_state.json"


def test_state_path_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("WILLOW_BOT_STEWARD_STATE", str(tmp_path / "custom.json"))
    assert _real_state_path() == tmp_path / "custom.json"


def test_state_path_with_no_box_refuses(monkeypatch):
    monkeypatch.delenv("WILLOW_HOME", raising=False)
    monkeypatch.delenv("WILLOW_VAULT_BOX", raising=False)
    with pytest.raises(paths.BoxNotConfigured):
        _real_state_path()


# ── relative and ~ values ───────────────────────────────────────────────────

def test_a_relative_box_is_refused_even_if_it_exists(monkeypatch, tmp_path):
    (tmp_path / "box").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WILLOW_HOME", "box")
    with pytest.raises(paths.BoxNotConfigured, match="not an absolute path"):
        paths.willow_home()


def test_a_tilde_box_is_expanded(monkeypatch, tmp_path):
    (tmp_path / "box").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_HOME", "~/box")
    assert paths.env_box("WILLOW_HOME") == tmp_path / "box"


# ── the scripts' key path ───────────────────────────────────────────────────

def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"_script_{name}", ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ["sync_webhook_secret", "list_installations"])
def test_script_key_path_is_the_box_never_dot_willow(monkeypatch, tmp_path, name):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    mod = _load_script(name)
    assert mod._key_path() == tmp_path / "secrets" / "willow-bot.pem"
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "~/k.pem")
    assert mod._key_path() == tmp_path / "home" / "k.pem"


def test_audit_script_key_path_is_the_box_never_dot_willow(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    mod = _load_script("audit_app_config")
    assert mod._key_path({}) == tmp_path / "secrets" / "willow-bot.pem"
    assert mod._key_path({"GITHUB_APP_PRIVATE_KEY_PATH": "/x/k.pem"}) == Path("/x/k.pem")


def test_sync_script_prints_the_hook_url_redacted(monkeypatch, tmp_path, capsys):
    """N2."""
    secret_url = "https://u:pw@hooks.example.invalid/relay/Ab3dEf9GhIjK?token=QS"
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "willow-bot.pem").write_text("PEM")
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("WEBHOOK_PUBLIC_URL", "https://new.example.invalid")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    mod = _load_script("sync_webhook_secret")

    class _R:
        def __init__(self, body):
            self.status_code, self._b, self.text = 200, body, ""

        def json(self):
            return self._b

    class _Req:
        def get(self, *a, **k):
            return _R({"url": secret_url})

        def patch(self, *a, **k):
            return _R({"url": secret_url})

    monkeypatch.setattr(mod, "requests", _Req())
    monkeypatch.setattr(mod, "_jwt", lambda: "JWT")
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "hooks.example.invalid/relay/…" in out
    for secret in ("u:pw", "Ab3dEf9GhIjK", "QS"):
        assert secret not in out, secret


# ── the carried-over counter ────────────────────────────────────────────────

def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()


def test_a_carried_over_counter_has_the_mode_a_fresh_one_gets(monkeypatch, tmp_path):
    legacy = tmp_path / "old"
    legacy.mkdir()
    _make_db(legacy / "willow-bot-sigh.db")
    monkeypatch.setattr(persona_store, "_legacy_dir", lambda: legacy)
    carried = persona_store.db_path("sigh")
    fresh = persona_store.db_path("rebase_shame")
    sqlite3.connect(fresh).close()
    assert stat.S_IMODE(carried.stat().st_mode) == stat.S_IMODE(fresh.stat().st_mode)


def test_the_lock_is_held_for_the_whole_copy(monkeypatch, tmp_path):
    """N3: a second opener is shut out while the copy runs, not only at the check."""
    legacy = tmp_path / "old"
    legacy.mkdir()
    _make_db(legacy / "willow-bot-sigh.db")
    monkeypatch.setattr(persona_store, "_legacy_dir", lambda: legacy)
    real_carry = persona_store._carry_over
    seen = {}

    def _probe(src, target):
        with open(target.parent / ".carry.lock", "a+") as other:
            try:
                fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                seen["held"] = True
            else:
                seen["held"] = False
                fcntl.flock(other.fileno(), fcntl.LOCK_UN)
        real_carry(src, target)

    monkeypatch.setattr(persona_store, "_carry_over", _probe)
    persona_store.db_path("sigh")
    assert seen == {"held": True}


@pytest.mark.parametrize("box", ["relative/box", "/nonexistent/willow-box"])
def test_install_service_refuses_a_box_that_is_not_an_existing_absolute_dir(box):
    import subprocess

    env = {k: v for k, v in os.environ.items() if k not in ("WILLOW_HOME",)}
    env["WILLOW_BOT_VAULT_BOX"] = box
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install-service.sh"), "--print", "willow-bot"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "not an existing absolute directory" in r.stderr


# ── Loki 87DF2A04 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mask", [0o002, 0o027, 0o077])
def test_a_carried_over_counter_matches_a_fresh_one_under_any_umask(monkeypatch, tmp_path, mask):
    """F2/F4 (Loki 87DF2A04, 407AC2A3): SQLite creates 0644 less the umask;
    the carry-over must match it under every umask — 027 and 077 are the
    ones that prove the umask is actually applied, not only CI's 022."""
    legacy = tmp_path / "old"
    legacy.mkdir()
    _make_db(legacy / "willow-bot-sigh.db")
    monkeypatch.setattr(persona_store, "_legacy_dir", lambda: legacy)
    old = os.umask(mask)
    try:
        carried = persona_store.db_path("sigh")
        fresh = persona_store.db_path("rebase_shame")
        sqlite3.connect(fresh).close()
    finally:
        os.umask(old)
    assert stat.S_IMODE(carried.stat().st_mode) == stat.S_IMODE(fresh.stat().st_mode)
    assert stat.S_IMODE(carried.stat().st_mode) == 0o644 & ~mask


def test_state_override_expands_tilde(monkeypatch, tmp_path):
    """M03."""
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("WILLOW_BOT_STEWARD_STATE", "~/s.json")
    assert _real_state_path() == tmp_path / "h" / "s.json"


def test_install_service_refuses_an_existing_relative_box(monkeypatch, tmp_path):
    """M26: the absolute-path half of the installer's check."""
    import subprocess

    (tmp_path / "box").mkdir()
    env = {k: v for k, v in os.environ.items() if k != "WILLOW_HOME"}
    env["WILLOW_BOT_VAULT_BOX"] = "box"
    r = subprocess.run(["bash", str(ROOT / "scripts" / "install-service.sh"), "--print", "willow-bot"],
                       env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "not an existing absolute directory" in r.stderr


@pytest.mark.parametrize("matches", [True, False])
def test_audit_script_prints_the_hook_url_redacted(monkeypatch, tmp_path, capsys, matches):
    """M23/M24: both the match and the mismatch line."""
    import json
    import sys
    import types

    secret = "https://u:pw@hooks.example.invalid/relay/cOXMpBiOXVtcrqwe"
    hook_url = secret + "/webhook"
    (tmp_path / "k.pem").write_text("PEM")
    (tmp_path / ".env").write_text(
        f"GITHUB_APP_ID=1\nGITHUB_APP_PRIVATE_KEY_PATH={tmp_path / 'k.pem'}\n"
        f"WEBHOOK_PUBLIC_URL={secret if matches else 'https://other.example.invalid/x/QQtokenQQ'}\n")
    mod = _load_script("audit_app_config")
    monkeypatch.setattr(mod, "_ROOT", tmp_path)

    calls = {"n": 0}

    def _run(cmd, **kw):
        calls["n"] += 1
        out = json.dumps({"repositories": [], "installations": []}) if calls["n"] == 1 else ""
        return types.SimpleNamespace(returncode=0 if calls["n"] == 1 else 1, stdout=out, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", _run)

    class _Resp:
        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    answers = {"https://api.github.com/app": {"name": "willows-bot", "permissions": {}, "events": []},
               "https://api.github.com/app/hook/config": {"url": hook_url, "secret": "WEBHOOKSECRET"}}
    fake_requests = types.SimpleNamespace(
        get=lambda url, **kw: _Resp(answers.get(url, [])))
    fake_jwt = types.SimpleNamespace(encode=lambda *a, **k: "JWT")
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "jwt", fake_jwt)
    mod.main()
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "hook URL" in ln)
    assert ("matches" in line) is matches
    assert "hooks.example.invalid/relay/…/webhook" in line
    for s in ("u:pw", "cOXMpBiOXVtcrqwe", "QQtokenQQ", "WEBHOOKSECRET"):
        assert s not in out, s
