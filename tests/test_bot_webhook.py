"""bot.py webhook boundary — signature check + delivery-id dedup.

Gap acfd27ae3259 (webhook idempotency sub-part): a redelivered
X-GitHub-Delivery is a no-op at the boundary. The dedup module itself
has its own unit tests; this file exercises the FastAPI wiring so a
regression in bot.py (a dropped header, a mis-ordered check) is caught
here rather than in production.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import sys
from pathlib import Path

import pytest


WEBHOOK_SECRET = "s3cret-for-tests-only"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def bot_app(tmp_path, monkeypatch):
    """Prime the env with a stub PEM and secret, import bot.py, and hand
    back a TestClient. Credentials are LAZY (bot._cred()), so the module
    imports without touching the vault — but the first request through
    `_verify_signature` resolves them, so per-test env still matters.
    Between tests, `bot.reset_credential_cache()` drops the cached
    BotCredentials so the next test sees its own WEBHOOK_SECRET.

    The delivery-seen LRU is captured at call time from state_path(), so
    a per-test WILLOW_HOME already isolates it — no module reload needed.
    """
    pem = tmp_path / "willow-bot.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")

    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("GITHUB_APP_ID", "999999")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(pem))
    monkeypatch.delenv("WILLOW_BOT_DELIVERY_STATE", raising=False)

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))

    bot = importlib.import_module("bot")
    bot.reset_credential_cache()  # each test re-resolves from its own env
    from fastapi.testclient import TestClient

    # Silence the outbound "post comment" call — this test does not exercise
    # github_app.
    monkeypatch.setattr(bot.router, "route", lambda *_a, **_k: None)

    return TestClient(bot.app)


def _post(client, body: dict, *, event: str = "pull_request", delivery: str = "d-1",
          sig: str | None = None) -> tuple[int, dict]:
    raw = json.dumps(body).encode()
    resp = client.post(
        "/webhook",
        content=raw,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig if sig is not None else _sign(raw),
            "Content-Type": "application/json",
        },
    )
    try:
        return resp.status_code, resp.json()
    except Exception:  # noqa: BLE001
        return resp.status_code, {"raw": resp.text}


def test_import_bot_does_not_resolve_credentials(tmp_path, monkeypatch) -> None:
    """A fresh import of bot.py must succeed even when the vault has
    NOTHING wired — no PEM, no app id, no webhook secret. The
    credential resolve is lazy; the FIRST signature check is what
    triggers it. This is the shape a systemd unit needs so a rotated
    PEM does not crash the worker at import time."""
    # Point every credential source at an empty tmp dir.
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("WILLOW_BOT_DELIVERY_STATE", raising=False)

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))
    for mod in ("bot", "willow_bot.delivery_dedup"):
        sys.modules.pop(mod, None)

    # No exception at import time even though credentials.resolve would
    # raise if called now.
    bot = importlib.import_module("bot")
    assert bot.app is not None
    # And a resolve, if forced, would fail — proving the import path
    # never touched it.
    with pytest.raises(RuntimeError):
        bot._cred()


def test_first_delivery_dispatches(bot_app) -> None:
    status, body = _post(bot_app, {"action": "opened"}, delivery="d-first")
    assert status == 200
    assert body["ok"] is True
    assert body.get("dedup") is None
    assert body.get("delivery") == "d-first"


def test_second_delivery_of_the_same_id_is_a_dedup(bot_app) -> None:
    status1, body1 = _post(bot_app, {"action": "opened"}, delivery="d-replay")
    assert status1 == 200 and body1.get("dedup") is None
    status2, body2 = _post(bot_app, {"action": "opened"}, delivery="d-replay")
    assert status2 == 200
    assert body2.get("dedup") == "delivery_seen"
    assert body2.get("delivery") == "d-replay"


def test_bad_signature_is_401_before_dedup(bot_app) -> None:
    """A wrong signature must fail before any state is touched — otherwise
    a bogus caller could push arbitrary strings into the delivery LRU.
    The dedup file must not be created on this path."""
    from willow_bot import delivery_dedup

    status, body = _post(bot_app, {"action": "opened"}, delivery="d-bogus",
                         sig="sha256=" + "0" * 64)
    assert status == 401
    assert not delivery_dedup.state_path().is_file()


def test_missing_delivery_header_still_dispatches(bot_app) -> None:
    """A webhook without X-GitHub-Delivery (a hand-fired curl during a
    debug session) is accepted once. An empty id is not persisted, so
    two empty-id posts each dispatch — the signature check is what keeps
    non-GitHub traffic out."""
    status1, body1 = _post(bot_app, {"action": "opened"}, delivery="")
    status2, body2 = _post(bot_app, {"action": "opened"}, delivery="")
    assert status1 == 200 and status2 == 200
    assert body1.get("dedup") is None and body2.get("dedup") is None
    assert body1.get("delivery") is None and body2.get("delivery") is None


def test_dedup_is_per_delivery_id_not_per_body(bot_app) -> None:
    """Two identical bodies with two distinct delivery ids both dispatch —
    the dedup key is the GitHub-asserted delivery id, not the request
    body. (Confirming this: an operator manually redelivering a webhook
    from the GitHub UI reuses the id; two independent events happen to
    share a body would each get their own id.)"""
    status1, _ = _post(bot_app, {"action": "opened"}, delivery="d-A")
    status2, _ = _post(bot_app, {"action": "opened"}, delivery="d-B")
    assert status1 == 200 and status2 == 200
    # And the SECOND with each id is the dedup.
    _, body_A2 = _post(bot_app, {"action": "opened"}, delivery="d-A")
    _, body_B2 = _post(bot_app, {"action": "opened"}, delivery="d-B")
    assert body_A2.get("dedup") == "delivery_seen"
    assert body_B2.get("dedup") == "delivery_seen"


def test_dedup_survives_a_module_reload(tmp_path, monkeypatch) -> None:
    """The LRU is on disk, so a bot process restarted between deliveries
    (systemd rolled the unit, uvicorn exited on a signal) still dedups
    the pending redelivery when it lands. Simulated here by importing
    bot once, posting, then re-importing after clearing sys.modules to
    prove the state came from disk, not from a module-level cache."""
    pem = tmp_path / "willow-bot.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n")
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("WILLOW_VAULT_BOX", str(tmp_path))
    monkeypatch.setenv("GITHUB_APP_ID", "999999")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", str(pem))
    monkeypatch.delenv("WILLOW_BOT_DELIVERY_STATE", raising=False)
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo_root))

    for i in range(2):
        for mod in ("bot", "willow_bot.delivery_dedup"):
            sys.modules.pop(mod, None)
        bot = importlib.import_module("bot")
        bot.reset_credential_cache()
        monkeypatch.setattr(bot.router, "route", lambda *_a, **_k: None)
        from fastapi.testclient import TestClient
        client = TestClient(bot.app)
        status, body = _post(client, {"action": "opened"}, delivery="d-persistent")
        if i == 0:
            assert body.get("dedup") is None  # first process: new
        else:
            assert body.get("dedup") == "delivery_seen"  # after reload: dedup


def test_dispatch_only_runs_once_per_delivery_id(bot_app) -> None:
    """The visible guarantee: `router.route` is called exactly once per
    unique delivery id, no matter how many times the same id shows up."""
    from bot import router  # already patched to a no-op by the fixture
    calls: list = []
    router.route = lambda *args, **kwargs: calls.append(args)  # type: ignore[assignment]

    _post(bot_app, {"action": "opened"}, delivery="d-count")
    _post(bot_app, {"action": "opened"}, delivery="d-count")
    _post(bot_app, {"action": "opened"}, delivery="d-count")
    assert len(calls) == 1
    _post(bot_app, {"action": "opened"}, delivery="d-count-other")
    assert len(calls) == 2
