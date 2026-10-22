"""oauth_resource on the real GroveOAuthProvider, read from willows-grove.

What has to hold for `validate_token_resource=True` to be safe: an honest
token (no resource asked) is bound to this server and passes the SDK's bearer
check; a token asked for another resource is bound to it and fails; refresh
keeps the binding; a token minted before binding obeys `legacy_until` (G1).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from onescript import __main__ as cli  # noqa: E402
from onescript import oauth_resource  # noqa: E402

pytestmark = pytest.mark.needs_grove

sdk = pytest.importorskip("mcp.server.auth.provider")
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend  # noqa: E402
from mcp.shared.auth import OAuthClientInformationFull  # noqa: E402

sys.path.insert(0, str(cli.GROVE))
mcp_auth = pytest.importorskip("grove.mcp_auth")

HERE = "https://grove.example.test/mcp"
ELSEWHERE = "https://other.example.test/mcp"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def run(coro):
    return asyncio.run(coro)


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="client-1",
        client_name="Test Client",
        redirect_uris=[CALLBACK],
        grant_types=["authorization_code", "refresh_token"],
        scope="grove",
    )


def _params(resource: str | None = None):
    return sdk.AuthorizationParams(
        state="s",
        scopes=["grove"],
        code_challenge="E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
        redirect_uri=CALLBACK,
        redirect_uri_provided_explicitly=True,
        resource=resource,
    )


def _provider(tmp_path, legacy_until=None, bound=True):
    p = mcp_auth.GroveOAuthProvider(
        token_path=tmp_path / "grove_mcp_token", base_url="https://grove.example.test"
    )
    if bound:
        oauth_resource.bind(p, resource=HERE, legacy_until=legacy_until)
    return p


def _tokens(p, resource=None):
    client = _client()
    code = p.issue_code(client, _params(resource))
    held = run(p.load_authorization_code(client, code))
    return client, held, run(p.exchange_authorization_code(client, held))


def _passes(resource: str | None) -> bool:
    """The SDK's own bearer check, as the server runs it with the setting on."""
    backend = BearerAuthBackend(None, resource_server_url=HERE)
    return backend._issued_for_this_resource(resource)


# ── the honest flow and the foreign one ─────────────────────────────────────
def test_an_honest_token_is_bound_here_and_passes(tmp_path):
    p = _provider(tmp_path)
    _, code, tok = _tokens(p)
    assert code.resource == HERE
    at = run(p.load_access_token(tok.access_token))
    assert at.resource == HERE and _passes(at.resource)


def test_a_token_asked_for_elsewhere_is_bound_there_and_fails(tmp_path):
    p = _provider(tmp_path)
    _, _, tok = _tokens(p, resource=ELSEWHERE)
    at = run(p.load_access_token(tok.access_token))
    assert at.resource == ELSEWHERE and not _passes(at.resource)


def test_unbound_the_honest_token_fails_the_check(tmp_path):
    """The trap the binding exists for: setting on, provider unbound."""
    p = _provider(tmp_path, bound=False)
    _, _, tok = _tokens(p)
    at = run(p.load_access_token(tok.access_token))
    assert at.resource is None and not _passes(at.resource)


# ── refresh ─────────────────────────────────────────────────────────────────
def test_refresh_keeps_the_binding(tmp_path):
    p = _provider(tmp_path)
    client, _, tok = _tokens(p, resource=ELSEWHERE)
    rt = run(p.load_refresh_token(client, tok.refresh_token))
    assert rt.resource == ELSEWHERE
    new = run(p.exchange_refresh_token(client, rt, []))
    assert run(p.load_access_token(new.access_token)).resource == ELSEWHERE
    assert run(p.load_refresh_token(client, new.refresh_token)).resource == ELSEWHERE


def test_the_binding_survives_a_restart(tmp_path):
    p = _provider(tmp_path)
    _, _, tok = _tokens(p)
    again = _provider(tmp_path)  # a new process over the same state file
    assert run(again.load_access_token(tok.access_token)).resource == HERE


# ── tokens minted before binding (G1) ───────────────────────────────────────
def _legacy(tmp_path):
    old = _provider(tmp_path, bound=False)
    client, _, tok = _tokens(old)
    return client, tok


@pytest.mark.parametrize(
    "until,kept",
    [(None, False), (-60, False), (3600, True)],
    ids=["none", "past", "future"],
)
def test_a_legacy_token_obeys_legacy_until(tmp_path, until, kept):
    client, tok = _legacy(tmp_path)
    p = _provider(tmp_path, legacy_until=None if until is None else time.time() + until)
    at = run(p.load_access_token(tok.access_token))
    rt = run(p.load_refresh_token(client, tok.refresh_token))
    if kept:
        assert at.resource == HERE and _passes(at.resource)
        assert rt.resource == HERE
        new = run(p.exchange_refresh_token(client, rt, []))
        assert run(p.load_access_token(new.access_token)).resource == HERE
    else:
        assert at is None and rt is None


# ── bind's own refusals ─────────────────────────────────────────────────────
def test_bind_twice_is_refused(tmp_path):
    p = _provider(tmp_path)
    with pytest.raises(ValueError, match="already bound"):
        oauth_resource.bind(p, resource=ELSEWHERE, legacy_until=None)


@pytest.mark.parametrize("bad", ["", "grove", "ftp://x/mcp", None])
def test_bind_refuses_a_resource_that_is_not_a_url(tmp_path, bad):
    p = _provider(tmp_path, bound=False)
    with pytest.raises(ValueError):
        oauth_resource.bind(p, resource=bad, legacy_until=None)


@pytest.mark.parametrize("bad", [True, "soon", [1]])
def test_bind_refuses_a_legacy_until_that_is_not_a_time(tmp_path, bad):
    p = _provider(tmp_path, bound=False)
    with pytest.raises(ValueError):
        oauth_resource.bind(p, resource=HERE, legacy_until=bad)


def test_bind_refuses_what_is_not_a_grove_shaped_provider():
    with pytest.raises(TypeError):
        oauth_resource.bind(object(), resource=HERE, legacy_until=None)


def test_the_provider_keeps_its_own_class_underneath(tmp_path):
    p = _provider(tmp_path)
    assert isinstance(p, mcp_auth.GroveOAuthProvider)
    assert type(p).__name__ == "ResourceBoundGroveOAuthProvider"
