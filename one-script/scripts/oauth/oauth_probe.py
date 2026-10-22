#!/usr/bin/env python3
"""Deterministic OAuth, checked the one-script way: forecast, run, propose.

Two kinds of probe, kept apart so no forecast is written after its answer was
seen:

- a **wager** is blind. `--forecast` writes it into the box through
  `api.forecast` (by `desk`) BEFORE anything is sent. Its outcome is a path
  the wager declared, printed as a proposal for the human's seal.
- a **pin** is an answer already seen (an earlier run, or a read made while
  comparing). It is never a wager. Its job is regression: the same request,
  asked again, must give the same answer. A pin that moves is `changed`.

Every probe runs `--runs` times, each run from a fresh client. Runs that
disagree settle to `drift`: the same inputs did not give the same answer,
which is what "deterministic" rules out. Drift is declared in every wager.

Nothing here seals. The human's key never comes near this script: wagers are
sealed with `onescript seal-tip` (the printed tip first, R3), then
`seal-outcome`, then scored with `reconcile`.

Targets:

- willows-grove serve mode on loopback: the whole flow, approval by the
  loopback POST.
- willow-mcp serve mode on loopback: up to its upstream sign-in. Google/Apple
  is the human's, so a willow-mcp code is never issued here.
- Google's hosted MCP servers (Gmail, Drive, Calendar) and accounts.google.com
  discovery, read-only and with no credentials at all: discovery documents,
  `initialize`, `tools/list`, and one read tool called without a token. Off
  with `--no-google`.

Start the two local servers with throwaway homes; never point this at a live
serve with a real token store.

  python3 scripts/oauth/oauth_probe.py \
      --grove http://127.0.0.1:8767 --willow http://127.0.0.1:8765 \
      [--runs 5] [--no-google] [--forecast --box BOX --keys KEYS] \
      [--label NAME] [--json]

Stdlib only. Exit 0 when every target answered (a miss is data, not a
failure), 2 when a target could not be reached at all.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DRIFT = "drift"
CHANGED = "changed"
CALLBACK = "http://127.0.0.1:9/cb"
FOREIGN = "http://127.0.0.1:9/mcp"
ACCEPT = "application/json, text/event-stream"
PROTOCOL = "2025-06-18"

GOOGLE_AS = "https://accounts.google.com/.well-known/openid-configuration"
# service -> (MCP endpoint, a read tool to call with no token)
GOOGLE_MCP = {
    "gmail": ("https://gmailmcp.googleapis.com/mcp/v1", "list_labels"),
    "drive": ("https://drivemcp.googleapis.com/mcp/v1", "list_recent_files"),
    "calendar": ("https://calendarmcp.googleapis.com/mcp/v1", "list_calendars"),
}


# ── the wire ─────────────────────────────────────────────────────────────────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


@dataclass
class Reply:
    status: int
    headers: dict
    body: str

    def json(self) -> dict:
        """The body as an object: plain JSON, or the last `data:` line of an
        SSE stream (streamable HTTP answers either way)."""
        text = self.body
        if "data:" in text and not text.lstrip().startswith("{"):
            lines = [x[5:].strip() for x in text.splitlines() if x.startswith("data:")]
            text = lines[-1] if lines else ""
        try:
            got = json.loads(text)
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}

    @property
    def location(self) -> str:
        return self.headers.get("location", "")


def call(method, url, *, form=None, body=None, headers=None, timeout=10.0) -> Reply:
    data = None
    hdrs = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["content-type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        hdrs["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return Reply(
                r.status,
                {k.lower(): v for k, v in r.headers.items()},
                r.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as e:
        return Reply(
            e.code,
            {k.lower(): v for k, v in e.headers.items()},
            e.read().decode("utf-8", "replace"),
        )


def _q(url: str) -> dict:
    query = urllib.parse.urlparse(url).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _rpc(method: str, params: dict | None = None, rid: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


INIT = _rpc(
    "initialize",
    {
        "protocolVersion": PROTOCOL,
        "capabilities": {},
        "clientInfo": {"name": "oauth-probe", "version": "0"},
    },
)


def _same_url(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


# ── one local server's side of the flow ──────────────────────────────────────
@dataclass
class Server:
    base: str
    mcp_path: str
    approve_path: str
    scope: str
    resource_meta: str

    def url(self, path: str) -> str:
        return self.base.rstrip("/") + path

    def register(self) -> Reply:
        return call(
            "POST",
            self.url("/register"),
            body={
                "redirect_uris": [CALLBACK],
                "client_name": "oauth-probe",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
                "scope": self.scope,
            },
        )

    def authorize(
        self,
        client: dict,
        challenge: str,
        *,
        redirect=CALLBACK,
        resource: str | None = None,
        method: str = "S256",
    ) -> Reply:
        q = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": method,
            "state": "probe-state",
            "scope": self.scope,
        }
        if resource:
            q["resource"] = resource
        return call("GET", self.url("/authorize?" + urllib.parse.urlencode(q)))

    def token(self, client: dict, form: dict) -> Reply:
        return call(
            "POST",
            self.url("/token"),
            form={
                "client_id": client["client_id"],
                "client_secret": client.get("client_secret", ""),
                **form,
            },
        )

    def mcp(self, token: str | None, msg: dict = INIT, session: str = "") -> Reply:
        hdrs = {"accept": ACCEPT}
        if token:
            hdrs["authorization"] = f"Bearer {token}"
        if session:
            hdrs["mcp-session-id"] = session
            hdrs["mcp-protocol-version"] = PROTOCOL
        return call("POST", self.url(self.mcp_path), headers=hdrs, body=msg)

    def session(self, token: str) -> str:
        """An initialized MCP session for `token`, or "" when none opened."""
        r = self.mcp(token)
        sid = r.headers.get("mcp-session-id", "")
        if r.status == 200:
            note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            self.mcp(token, note, sid)
        return sid if r.status == 200 else ""


def _pending(reply: Reply, approve_path: str) -> str | None:
    if reply.status in (302, 303, 307) and approve_path in reply.location:
        return _q(reply.location).get("pending")
    return None


def _token_path(r: Reply) -> str:
    if r.status == 200 and r.json().get("access_token"):
        return "issued"
    return r.json().get("error") or f"http_{r.status}"


def _mcp_path(r: Reply) -> str:
    return "accepted" if r.status == 200 else f"http_{r.status}"


def _issuer(s: Server) -> str:
    meta = call("GET", s.url("/.well-known/oauth-authorization-server")).json()
    return "matches" if _same_url(meta.get("issuer", ""), s.base) else "differs"


def _resource_vs_served(s: Server) -> str:
    """RFC 9728: the advertised resource is the URL a client will call."""
    r = call("GET", s.url(s.resource_meta))
    if r.status != 200:
        return f"http_{r.status}"
    adv = r.json().get("resource", "")
    return "matches" if _same_url(adv, s.url(s.mcp_path)) else "differs"


def _pkce_plain(s: Server, client: dict) -> str:
    v = secrets.token_urlsafe(48)
    r = s.authorize(client, v, method="plain")
    if _pending(r, s.approve_path):
        return "approval_page"
    return "refused"


def _revoke(s: Server, client: dict, access: str) -> str:
    r = call(
        "POST",
        s.url("/revoke"),
        form={
            "token": access,
            "client_id": client["client_id"],
            "client_secret": client.get("client_secret", ""),
        },
    )
    if r.status in (404, 405):
        return "no_endpoint"
    after = _mcp_path(s.mcp(access))
    return "revoked" if after != "accepted" else "ignored"


def _write_with_read_token(g: Server, access: str) -> str:
    """A write tool called on a `grove:read` token: the scope must stop it."""
    sid = g.session(access)
    if not sid:
        return "no_session"
    msg = _rpc(
        "tools/call",
        {
            "name": "grove_send_message",
            "arguments": {"channel_name": "probe", "content": "x", "sender": "probe"},
        },
        rid=7,
    )
    r = g.mcp(access, msg, sid)
    if r.status in (401, 403):
        return f"http_{r.status}"
    res = r.json().get("result") or {}
    err = r.json().get("error") or {}
    text = json.dumps(res.get("content", "")) + json.dumps(err)
    if "scope" in text.lower():
        return "scope_refused"
    if res.get("isError") or err:
        return "tool_error"
    return "allowed"


# ── Grove: the whole flow, approval by loopback POST ─────────────────────────
def _grove_grant(g: Server, client: dict, resource: str | None = None):
    """A code through the real approval POST: (code, verifier) or None."""
    verifier, challenge = _pkce()
    pend = _pending(g.authorize(client, challenge, resource=resource), g.approve_path)
    if not pend:
        return None
    r = call(
        "POST", g.url(f"{g.approve_path}?pending={pend}"), form={"action": "allow"}
    )
    code = _q(r.location).get("code") if r.status in (302, 303) else None
    return (code, verifier) if code else None


def _exchange(g: Server, client: dict, grant, **extra) -> Reply:
    code, verifier = grant
    return g.token(
        client,
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
            **extra,
        },
    )


def grove_run(g: Server) -> dict[str, str]:
    out: dict[str, str] = {}
    out["g.metadata.issuer"] = _issuer(g)
    out["g.mcp.no_token"] = _mcp_path(g.mcp(None))
    out["g.resource.matches_served"] = _resource_vs_served(g)

    reg = g.register()
    out["g.register"] = f"http_{reg.status}"
    client = reg.json()
    if "client_id" not in client:
        return out

    verifier, challenge = _pkce()
    a = g.authorize(client, challenge)
    pend = _pending(a, g.approve_path)
    out["g.authorize"] = "approval_page" if pend else f"http_{a.status}"
    out["g.pkce.plain"] = _pkce_plain(g, client)

    bad = g.authorize(client, challenge, redirect="http://127.0.0.1:9/elsewhere")
    out["g.authorize.bad_redirect"] = (
        "approval_page" if _pending(bad, g.approve_path) else "refused"
    )

    _, ch2 = _pkce()
    p2 = _pending(g.authorize(client, ch2), g.approve_path)
    d = call("POST", g.url(f"{g.approve_path}?pending={p2}"), form={"action": "deny"})
    out["g.approve.deny"] = _q(d.location).get("error", f"http_{d.status}")
    again = call(
        "POST", g.url(f"{g.approve_path}?pending={p2}"), form={"action": "allow"}
    )
    out["g.approve.pending_replay"] = (
        "code_issued" if _q(again.location).get("code") else "refused"
    )

    # the wrong verifier gets its own code, so the good exchange isn't decided by it
    wrong = _grove_grant(g, client)
    out["g.token.wrong_verifier"] = (
        _token_path(_exchange(g, client, (wrong[0], _pkce()[0])))
        if wrong
        else "no_code"
    )

    code = None
    if pend:
        r = call(
            "POST", g.url(f"{g.approve_path}?pending={pend}"), form={"action": "allow"}
        )
        code = _q(r.location).get("code")
    tok = _exchange(g, client, (code or "", verifier))
    out["g.token.good"] = _token_path(tok)
    out["g.token.code_replay"] = _token_path(
        _exchange(g, client, (code or "", verifier))
    )

    access = tok.json().get("access_token")
    refresh = tok.json().get("refresh_token")
    out["g.mcp.with_token"] = _mcp_path(g.mcp(access)) if access else "no_token"
    out["g.scope.write_with_read_token"] = (
        _write_with_read_token(g, access) if access else "no_token"
    )

    if refresh:
        rot = g.token(client, {"grant_type": "refresh_token", "refresh_token": refresh})
        out["g.refresh.rotate"] = _token_path(rot)
        out["g.refresh.old_reuse"] = _token_path(
            g.token(client, {"grant_type": "refresh_token", "refresh_token": refresh})
        )
    else:
        out["g.refresh.rotate"] = out["g.refresh.old_reuse"] = "no_refresh"

    # the audience gap: a token bound to another resource, presented here
    foreign = _grove_grant(g, client, resource=FOREIGN)
    if foreign:
        fa = _exchange(g, client, foreign, resource=FOREIGN).json().get("access_token")
        out["g.audience.foreign_resource"] = (
            _mcp_path(g.mcp(fa)) if fa else "not_issued"
        )
    else:
        out["g.audience.foreign_resource"] = "refused_at_authorize"

    # last: it may kill `access`, which the cross-server probe below then uses
    out["_grove_access"] = access or ""
    if access:
        spare = _grove_grant(g, client)
        sa = _exchange(g, client, spare).json().get("access_token") if spare else None
        out["g.revoke"] = _revoke(g, client, sa) if sa else "no_token"
    return out


# ── willow-mcp: up to the upstream sign-in ───────────────────────────────────
def willow_run(w: Server, grove_access: str) -> dict[str, str]:
    out: dict[str, str] = {}
    out["w.metadata.issuer"] = _issuer(w)
    out["w.mcp.no_token"] = _mcp_path(w.mcp(None))
    out["w.resource.matches_served"] = _resource_vs_served(w)
    reg = w.register()
    out["w.register"] = f"http_{reg.status}"
    client = reg.json()
    if "client_id" in client:
        _, challenge = _pkce()
        pend = _pending(w.authorize(client, challenge), w.approve_path)
        out["w.authorize"] = "approval_page" if pend else "refused"
        out["w.pkce.plain"] = _pkce_plain(w, client)
        if pend:
            d = call("GET", w.url(f"{w.approve_path}?pending={pend}&action=deny"))
            loc = d.location if d.status in (302, 303, 307) else ""
            out["w.approve.deny"] = _q(loc).get("error", f"http_{d.status}")
        r = call(
            "POST",
            w.url("/revoke"),
            form={"token": "not-a-token", "client_id": client["client_id"]},
        )
        out["w.revoke.endpoint"] = "absent" if r.status in (404, 405) else "present"
    out["x.grove_token_at_willow"] = (
        _mcp_path(w.mcp(grove_access)) if grove_access else "no_token"
    )
    return out


# ── Google: read-only, no credentials ────────────────────────────────────────
def google_run() -> dict[str, str]:
    out: dict[str, str] = {}
    meta = call("GET", GOOGLE_AS).json()
    methods = meta.get("code_challenge_methods_supported") or []
    out["google.as.pkce"] = "+".join(sorted(methods)) or "none"
    out["google.as.registration"] = (
        "dynamic" if meta.get("registration_endpoint") else "none"
    )
    out["google.as.revocation"] = (
        "advertised" if meta.get("revocation_endpoint") else "absent"
    )
    hdrs = {"accept": ACCEPT}
    for svc, (url, tool) in GOOGLE_MCP.items():
        base = url.split("/mcp")[0]
        path = urllib.parse.urlparse(url).path
        init = call("POST", url, headers=hdrs, body=INIT)
        out[f"google.{svc}.init_no_token"] = _mcp_path(init)
        tl = call("POST", url, headers=hdrs, body=_rpc("tools/list", rid=2))
        tools = (tl.json().get("result") or {}).get("tools")
        out[f"google.{svc}.tools_no_token"] = (
            f"tools_{len(tools)}" if isinstance(tools, list) else f"http_{tl.status}"
        )
        tc = call(
            "POST",
            url,
            headers=hdrs,
            body=_rpc("tools/call", {"name": tool, "arguments": {}}, rid=3),
        )
        chal = tc.headers.get("www-authenticate", "")
        out[f"google.{svc}.call_no_token"] = (
            "per_tool_challenge"
            if tc.status == 401 and f"/oauth-protected-resource/{tool}" in chal
            else "server_challenge"
            if tc.status == 401
            else f"http_{tc.status}"
        )
        top = call("GET", f"{base}/.well-known/oauth-protected-resource{path}").json()
        per = call("GET", f"{base}/.well-known/oauth-protected-resource/{tool}").json()
        out[f"google.{svc}.resource_consistent"] = (
            "matches"
            if top.get("resource") and top.get("resource") == per.get("resource")
            else "differs"
        )
    return out


# ── the wagers: blind, written before anything is sent ──────────────────────
def _w(expect: str, p: float, *others: tuple[str, float]) -> list[dict]:
    rest = 1.0 - p - sum(q for _, q in others)
    dist = [{"path": expect, "p": p}, *({"path": a, "p": q} for a, q in others)]
    dist.append({"path": DRIFT, "p": round(rest, 10)})
    return dist


WAGERS: dict[str, list[dict]] = {
    # Grove advertises `/mcp` but serves at `/` (seen in passing: the 401's
    # resource_metadata pointed at /mcp); the document's own field is unread
    "g.resource.matches_served": _w("differs", 0.8, ("matches", 0.15)),
    "g.pkce.plain": _w("refused", 0.85, ("approval_page", 0.12)),
    "g.scope.write_with_read_token": _w(
        "scope_refused",
        0.55,
        ("tool_error", 0.25),
        ("http_403", 0.1),
        ("allowed", 0.07),
    ),
    "g.revoke": _w("no_endpoint", 0.6, ("revoked", 0.3), ("ignored", 0.07)),
    "w.resource.matches_served": _w("differs", 0.7, ("matches", 0.25)),
    "w.pkce.plain": _w("refused", 0.85, ("approval_page", 0.12)),
    "w.revoke.endpoint": _w("absent", 0.7, ("present", 0.27)),
    # Gmail and Calendar were read while comparing; Drive's per-tool
    # document was not
    "google.drive.resource_consistent": _w("differs", 0.8, ("matches", 0.17)),
}

# ── the pins: answers already seen, held for regression ─────────────────────
PINNED: dict[str, str] = {
    # run 1, 2026-10-21 (20 wagers x 5 runs, no drift)
    "g.metadata.issuer": "matches",
    "g.mcp.no_token": "http_401",
    "g.register": "http_201",
    "g.authorize": "approval_page",
    "g.authorize.bad_redirect": "refused",
    "g.approve.deny": "http_200",
    "g.approve.pending_replay": "refused",
    "g.token.wrong_verifier": "invalid_grant",
    "g.token.good": "issued",
    "g.token.code_replay": "invalid_grant",
    "g.mcp.with_token": "accepted",
    "g.refresh.rotate": "issued",
    "g.refresh.old_reuse": "invalid_grant",
    "g.audience.foreign_resource": "accepted",
    "w.metadata.issuer": "matches",
    "w.mcp.no_token": "http_401",
    "w.register": "http_201",
    "w.authorize": "approval_page",
    "w.approve.deny": "http_200",
    "x.grove_token_at_willow": "http_401",
    # read while comparing against the Google connectors, same night
    "google.as.pkce": "S256+plain",
    "google.as.registration": "none",
    "google.as.revocation": "advertised",
    "google.gmail.init_no_token": "accepted",
    "google.drive.init_no_token": "accepted",
    "google.calendar.init_no_token": "accepted",
    "google.gmail.tools_no_token": "tools_23",
    "google.drive.tools_no_token": "tools_8",
    "google.calendar.tools_no_token": "tools_9",
    "google.gmail.call_no_token": "per_tool_challenge",
    "google.drive.call_no_token": "per_tool_challenge",
    "google.calendar.call_no_token": "per_tool_challenge",
    "google.gmail.resource_consistent": "differs",
    "google.calendar.resource_consistent": "differs",
}

assert not set(WAGERS) & set(PINNED), "a probe is a wager or a pin, never both"


@dataclass
class Settled:
    probe: str
    kind: str  # "wager" | "pin"
    expected: str
    path: str  # a declared path, DRIFT, or CHANGED
    runs: list[str] = field(default_factory=list)

    @property
    def hit(self) -> bool:
        return self.path == self.expected


def _top(dist: list[dict]) -> str:
    return max(dist, key=lambda e: e["p"])["path"]


def settle(runs: list[dict[str, str]], probes: set[str] | None = None) -> list[Settled]:
    """Each probe's runs, and the one outcome they come to.

    Runs that disagree are `drift`. A wager's answer that it never declared is
    `drift` too: `seal_outcome` takes only a declared path. A pin that agrees
    with itself but not with what was seen is `changed`."""
    want = probes if probes is not None else set(WAGERS) | set(PINNED)
    done: list[Settled] = []
    for probe in [*WAGERS, *PINNED]:
        if probe not in want:
            continue
        seen = [r.get(probe, "not_run") for r in runs]
        same = len(set(seen)) == 1
        if probe in WAGERS:
            declared = {e["path"] for e in WAGERS[probe]}
            path = seen[0] if same and seen[0] in declared else DRIFT
            done.append(Settled(probe, "wager", _top(WAGERS[probe]), path, seen))
        else:
            pin = PINNED[probe]
            path = DRIFT if not same else pin if seen[0] == pin else CHANGED
            done.append(Settled(probe, "pin", pin, path, seen))
    return done


def run_all(grove: str, willow: str, n: int, google: bool) -> list[dict[str, str]]:
    g = Server(
        grove,
        "/",
        "/grove-approve",
        "grove:read",
        "/.well-known/oauth-protected-resource/mcp",
    )
    w = Server(
        willow,
        "/mcp",
        "/mcp-approve",
        "willow",
        "/.well-known/oauth-protected-resource",
    )
    runs = []
    for _ in range(n):
        got = grove_run(g)
        got.update(willow_run(w, got.pop("_grove_access", "")))
        if google:
            got.update(google_run())
        runs.append(got)
    return runs


def _reachable(url: str) -> bool:
    try:
        return call("GET", url, timeout=5).status == 200
    except OSError:
        return False


def _probes(google: bool) -> set[str]:
    every = set(WAGERS) | set(PINNED)
    return every if google else {p for p in every if not p.startswith("google.")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--grove", default="http://127.0.0.1:8767")
    ap.add_argument("--willow", default="http://127.0.0.1:8765")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--no-google", action="store_true")
    ap.add_argument(
        "--forecast",
        action="store_true",
        help="write a wager per blind probe into --box before running",
    )
    ap.add_argument("--box", type=Path)
    ap.add_argument("--keys", type=Path)
    ap.add_argument("--label", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    google = not a.no_google
    if a.forecast and not (a.box and a.keys):
        ap.error("--forecast needs --box and --keys")

    meta = "/.well-known/oauth-authorization-server"
    targets = [a.grove.rstrip("/") + meta, a.willow.rstrip("/") + meta]
    if google:
        targets.append(GOOGLE_AS)
    for url in targets:
        if not _reachable(url):
            print(f"unreachable: {url}", file=sys.stderr)
            return 2

    probes = _probes(google)
    wagers: dict[str, str] = {}
    tip = None
    outcome_subject = None
    if a.forecast:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from onescript import __main__ as cli
        from onescript import api, reverse

        outcome_subject = reverse.outcome_subject
        cfg = api.Config(
            box=a.box,
            keys=a.keys,
            constitution=cli.CONSTITUTION,
            ci=cli.CI,
            root=cli.ROOT,
            grove=cli.GROVE,
            venv=Path("/nonexistent"),
            no_tests=True,
            phone=True,
        )
        opened = api.checkin(cfg)
        if opened.get("code") != 0:
            print(json.dumps({"checkin": opened}, default=str), file=sys.stderr)
            return 1
        label = a.label or secrets.token_hex(4)
        for probe, dist in WAGERS.items():
            if probe not in probes:
                continue
            got = api.forecast(cfg, f"oauth/{label}/{probe}", dist, by="desk")
            if got.get("code") != 0:
                print(json.dumps({"probe": probe, "forecast": got}), file=sys.stderr)
                return 1
            wagers[probe] = got["subject"]
            tip = got["tip"]

    settled = settle(run_all(a.grove, a.willow, a.runs, google), probes)

    rows = []
    for s in settled:
        row = {
            "probe": s.probe,
            "kind": s.kind,
            "expected": s.expected,
            "outcome": s.path,
            "hit": s.hit,
            "runs": s.runs,
        }
        if s.probe in wagers:
            outcome = {"path": s.path}
            row["proposal"] = {
                "wager": wagers[s.probe],
                "outcome": outcome,
                "subject": outcome_subject(wagers[s.probe], outcome),
            }
        rows.append(row)

    if tip:
        # R3: the human seals this tip (it covers every wager) before any outcome
        print(json.dumps({"seal_tip_first": tip}))
    if a.json:
        for r in rows:
            print(json.dumps(r))
        return 0
    w = max(len(r["probe"]) for r in rows)
    for r in rows:
        mark = "hit " if r["hit"] else "MISS"
        got = r["outcome"] if r["outcome"] != CHANGED else f"changed to {r['runs'][0]}"
        print(f"{mark}  {r['kind']:<5}  {r['probe']:<{w}}  {r['expected']:<15} {got}")
    for kind in ("wager", "pin"):
        mine = [r for r in rows if r["kind"] == kind]
        print(f"{kind}s: {len(mine)}, missed {sum(not r['hit'] for r in mine)}")
    print(f"{a.runs} runs each")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
