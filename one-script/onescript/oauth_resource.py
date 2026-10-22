"""oauth_resource: every code and token knows the resource it was issued for.

The finding (oauth_probe, 2026-10-21): Grove accepts an access token bound to
another server, because `AuthSettings.validate_token_resource` is unset. The
trap behind the obvious fix: neither GroveOAuthProvider nor willow-mcp's
provider records a token's `resource`, and with the check on the SDK refuses
any token whose resource is missing. Turning the setting on alone refuses
every token, the honest ones included (proven on a throwaway Grove copy the
same night: `g.mcp.with_token` went from accepted to 401).

`bind(provider, resource=..., legacy_until=...)` fixes the provider in place,
so the setting can go on:

| Step | What binding does |
|---|---|
| authorize -> code | the code carries `params.resource`, else this server's own resource (a code issued here, with no resource asked for, is for here) |
| code -> tokens | the access and refresh tokens carry the code's resource |
| refresh -> tokens | the new pair carries the old refresh token's resource |
| load | a token with a resource is returned as is; the SDK compares it to `resource_server_url` |
| a token minted before binding (no resource) | `legacy_until`: accepted as this server's until that time, refused after |

Binding swaps the instance's class for a subclass, so the provider's own
internal calls (willow-mcp's Google/Apple callbacks call `self.issue_code`)
go through the binding too, and everything else about the provider is
untouched: its routes, its pending store, its state file.

Wiring (in the server, beside the provider; not done in either repo):

    provider = oauth_resource.bind(
        GroveOAuthProvider(...), resource=_public_mcp_url(), legacy_until=...)
    AuthSettings(..., resource_server_url=_public_mcp_url(),
                 validate_token_resource=True)

`resource` must be the same string as `resource_server_url`.

Gaps, left for the operator (each is named where it bites):

- G1 `legacy_until` has no default on purpose: refuse old tokens now (pass
  None, everyone signs in once) or honour them for a window (an epoch time).
  The human's choice, not code's.
- G2 a requested resource that isn't this server is bound as asked and then
  refused at the bearer check. RFC 8707 also lets the authorize step refuse
  it up front (`invalid_target`); not done here.
- G3 Grove advertises `/mcp` and serves `/`; willow-mcp advertises `/` and
  serves `/mcp` (oauth_probe `*.resource.matches_served`). Binding compares
  against the advertised URL, so the check holds, but the advertised and
  served paths should still agree.
- G4 no revocation endpoint on either server (oauth_probe `g.revoke`).

Stdlib plus whatever SDK the provider already imports; this module imports no
SDK itself.
"""

from __future__ import annotations

import time

BOUND = "_oauth_resource_bound"


class _ResourceBound:
    """Mixed in ahead of the provider's own class by `bind`."""

    _resource: str
    _legacy_until: float | None

    def _for(self, asked: str | None) -> str:
        return asked or self._resource

    def _legacy_ok(self) -> bool:
        return self._legacy_until is not None and time.time() < self._legacy_until

    # ── authorize -> code ───────────────────────────────────────────────────
    def issue_code(self, client, params) -> str:
        code = super().issue_code(client, params)
        held = self._codes.get(code)
        if held is not None:
            want = self._for(getattr(params, "resource", None))
            self._codes[code] = held.model_copy(update={"resource": want})
        return code

    # ── code -> tokens ──────────────────────────────────────────────────────
    async def exchange_authorization_code(self, client, authorization_code):
        token = await super().exchange_authorization_code(client, authorization_code)
        self._stamp(token, self._for(authorization_code.resource))
        return token

    # ── refresh -> tokens ───────────────────────────────────────────────────
    async def load_refresh_token(self, client, refresh_token: str):
        rt = await super().load_refresh_token(client, refresh_token)
        if rt is None or rt.resource:
            return rt
        if not self._legacy_ok():
            return None
        return rt.model_copy(update={"resource": self._resource})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        token = await super().exchange_refresh_token(client, refresh_token, scopes)
        self._stamp(token, self._for(refresh_token.resource))
        return token

    # ── load ────────────────────────────────────────────────────────────────
    async def load_access_token(self, token: str):
        at = await super().load_access_token(token)
        if at is None or at.resource:
            return at
        if not self._legacy_ok():
            return None
        return at.model_copy(update={"resource": self._resource})

    def _stamp(self, token, resource: str) -> None:
        """Write the resource onto the stored rows the exchange just made."""
        state = self._state
        for kind, value in (
            ("access_tokens", token.access_token),
            ("refresh_tokens", getattr(token, "refresh_token", None)),
        ):
            row = state.get(kind, {}).get(value) if value else None
            if row is not None:
                row["resource"] = resource
        self._save_state()


def bind(provider, *, resource: str, legacy_until: float | None):
    """Bind `provider` in place to `resource` and return it.

    `resource` is this server's RFC 9728 resource, the same string the server
    passes as `resource_server_url`. `legacy_until` (G1) is required: None
    refuses tokens minted before binding at once; an epoch time honours them
    as this server's until then. Binding twice is refused, so a second wiring
    can't silently change the first one's resource."""
    if getattr(provider, BOUND, False):
        raise ValueError("provider is already bound")
    if not isinstance(resource, str) or not resource.startswith(
        ("http://", "https://")
    ):
        raise ValueError(f"resource must be an http(s) URL, got {resource!r}")
    if legacy_until is not None and (
        isinstance(legacy_until, bool) or not isinstance(legacy_until, (int, float))
    ):
        raise ValueError("legacy_until is an epoch time or None")
    for need in ("_codes", "_state", "_save_state", "issue_code"):
        if not hasattr(provider, need):
            raise TypeError(f"provider has no {need}: not a Grove-shaped provider")
    cls = type(provider)
    provider.__class__ = type(f"ResourceBound{cls.__name__}", (_ResourceBound, cls), {})
    provider._resource = resource
    provider._legacy_until = legacy_until
    setattr(provider, BOUND, True)
    return provider
