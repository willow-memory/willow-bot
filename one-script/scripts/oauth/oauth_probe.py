#!/usr/bin/env python3
"""Deterministic OAuth, checked the one-script way: forecast, run, propose.

Every probe is a wager first. `--forecast` writes one `wager` row per probe
into the box through `api.forecast` (by `desk`) BEFORE anything is sent, so
the prediction is on the chain ahead of the outcome. Then the probes run
against the live servers, `--runs` times each, every run from a fresh client.

An outcome is a path the wager declared. A probe whose runs disagree is
`drift`: the same inputs did not give the same answer, which is the thing
"deterministic" rules out. Drift is declared in every wager, so it can be
sealed like any other outcome.

Nothing here seals. Each outcome is printed as a proposal: the wager hash,
the outcome, and its subject (`reverse.outcome_subject`), for the human to
seal with `onescript seal-outcome` and then score with `reconcile`. The
human's key never comes near this script.

What it reaches: willows-grove's serve mode (the whole flow, approval by a
loopback POST) and willow-mcp's serve mode up to its upstream sign-in
(Google/Apple is the human's, so a willow-mcp code is never issued here).
Both on loopback, both started with throwaway homes; never point this at a
live serve with a real token store.

  python3 scripts/oauth/oauth_probe.py \
      --grove http://127.0.0.1:8767 --willow http://127.0.0.1:8765 \
      [--runs 5] [--forecast --box BOX --keys KEYS] [--label NAME] [--json]

Stdlib only. Exit 0 when every probe answered (a miss is data, not a
failure), 2 when a server could not be reached at all.
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
from dataclasses import dataclass
from pathlib import Path

DRIFT = "drift"
CALLBACK = "http://127.0.0.1:9/cb"
FOREIGN = "http://127.0.0.1:9/mcp"


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
        try:
            got = json.loads(self.body)
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
    return {
        k: v[0]
        for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()
    }


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# ── one server's side of the flow ────────────────────────────────────────────
@dataclass
class Server:
    base: str
    mcp_path: str
    approve_path: str
    scope: str

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
    ) -> Reply:
        q = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
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

    def mcp(self, token: str | None) -> Reply:
        hdrs = {"accept": "application/json, text/event-stream"}
        if token:
            hdrs["authorization"] = f"Bearer {token}"
        return call(
            "POST",
            self.url(self.mcp_path),
            headers=hdrs,
            body={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "oauth-probe", "version": "0"},
                },
            },
        )


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


def grove_run(g: Server) -> dict[str, str]:
    out: dict[str, str] = {}
    meta = call("GET", g.url("/.well-known/oauth-authorization-server")).json()
    out["g.metadata.issuer"] = (
        "matches" if meta.get("issuer") == g.base.rstrip("/") + "/" else "differs"
    )
    out["g.mcp.no_token"] = _mcp_path(g.mcp(None))

    reg = g.register()
    out["g.register"] = f"http_{reg.status}"
    client = reg.json()
    if "client_id" not in client:
        return out

    verifier, challenge = _pkce()
    a = g.authorize(client, challenge)
    pend = _pending(a, g.approve_path)
    out["g.authorize"] = "approval_page" if pend else f"http_{a.status}"

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
        _token_path(
            g.token(
                client,
                {
                    "grant_type": "authorization_code",
                    "code": wrong[0] if wrong else "",
                    "redirect_uri": CALLBACK,
                    "code_verifier": _pkce()[0],
                },
            )
        )
        if wrong
        else "no_code"
    )

    if pend:
        r = call(
            "POST", g.url(f"{g.approve_path}?pending={pend}"), form={"action": "allow"}
        )
        code = _q(r.location).get("code")
    else:
        code = None
    exch = {
        "grant_type": "authorization_code",
        "code": code or "",
        "redirect_uri": CALLBACK,
        "code_verifier": verifier,
    }
    tok = g.token(client, exch)
    out["g.token.good"] = _token_path(tok)
    out["g.token.code_replay"] = _token_path(g.token(client, exch))

    access = tok.json().get("access_token")
    refresh = tok.json().get("refresh_token")
    out["g.mcp.with_token"] = _mcp_path(g.mcp(access)) if access else "no_token"

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
        ft = g.token(
            client,
            {
                "grant_type": "authorization_code",
                "code": foreign[0],
                "redirect_uri": CALLBACK,
                "code_verifier": foreign[1],
                "resource": FOREIGN,
            },
        )
        fa = ft.json().get("access_token")
        out["g.audience.foreign_resource"] = (
            _mcp_path(g.mcp(fa)) if fa else "not_issued"
        )
    else:
        out["g.audience.foreign_resource"] = "refused_at_authorize"
    out["_grove_access"] = access or ""
    return out


# ── willow-mcp: up to the upstream sign-in ───────────────────────────────────
def willow_run(w: Server, grove_access: str) -> dict[str, str]:
    out: dict[str, str] = {}
    meta = call("GET", w.url("/.well-known/oauth-authorization-server")).json()
    out["w.metadata.issuer"] = (
        "matches" if meta.get("issuer") == w.base.rstrip("/") + "/" else "differs"
    )
    out["w.mcp.no_token"] = _mcp_path(w.mcp(None))
    reg = w.register()
    out["w.register"] = f"http_{reg.status}"
    client = reg.json()
    if "client_id" in client:
        _, challenge = _pkce()
        pend = _pending(w.authorize(client, challenge), w.approve_path)
        out["w.authorize"] = "approval_page" if pend else "refused"
        if pend:
            d = call("GET", w.url(f"{w.approve_path}?pending={pend}&action=deny"))
            loc = d.location if d.status in (302, 303, 307) else ""
            out["w.approve.deny"] = _q(loc).get("error", f"http_{d.status}")
    out["x.grove_token_at_willow"] = (
        _mcp_path(w.mcp(grove_access)) if grove_access else "no_token"
    )
    return out


# ── the wagers: what the desk expects, before anything is sent ──────────────
def _w(expect: str, p: float, *others: tuple[str, float]) -> list[dict]:
    rest = 1.0 - p - sum(q for _, q in others)
    dist = [{"path": expect, "p": p}, *({"path": a, "p": q} for a, q in others)]
    dist.append({"path": DRIFT, "p": round(rest, 10)})
    return dist


FORECASTS: dict[str, list[dict]] = {
    "g.metadata.issuer": _w("matches", 0.97, ("differs", 0.02)),
    "g.mcp.no_token": _w("http_401", 0.97, ("accepted", 0.01)),
    "g.register": _w("http_201", 0.95, ("http_400", 0.03)),
    "g.authorize": _w("approval_page", 0.95, ("http_400", 0.03)),
    "g.authorize.bad_redirect": _w("refused", 0.93, ("approval_page", 0.05)),
    "g.approve.deny": _w("access_denied", 0.92, ("http_200", 0.06)),
    "g.approve.pending_replay": _w("refused", 0.92, ("code_issued", 0.06)),
    "g.token.wrong_verifier": _w("invalid_grant", 0.93, ("issued", 0.04)),
    "g.token.good": _w("issued", 0.93, ("invalid_grant", 0.05)),
    "g.token.code_replay": _w("invalid_grant", 0.92, ("issued", 0.06)),
    "g.mcp.with_token": _w("accepted", 0.9, ("http_401", 0.04), ("http_403", 0.04)),
    "g.refresh.rotate": _w("issued", 0.9, ("invalid_grant", 0.08)),
    "g.refresh.old_reuse": _w("invalid_grant", 0.85, ("issued", 0.13)),
    # the gap flagged before the run: the desk expects a foreign-bound token
    # to be accepted, since validate_token_resource is unset
    "g.audience.foreign_resource": _w(
        "accepted", 0.75, ("http_401", 0.15), ("not_issued", 0.08)
    ),
    "w.metadata.issuer": _w("matches", 0.97, ("differs", 0.02)),
    "w.mcp.no_token": _w("http_401", 0.97, ("accepted", 0.01)),
    "w.register": _w("http_201", 0.9, ("http_400", 0.08)),
    "w.authorize": _w("approval_page", 0.93, ("refused", 0.05)),
    "w.approve.deny": _w("access_denied", 0.85, ("http_200", 0.13)),
    "x.grove_token_at_willow": _w("http_401", 0.95, ("accepted", 0.03)),
}


def settle(runs: list[dict[str, str]]) -> dict[str, dict]:
    """Each probe's runs, and the one outcome they come to: the path when every
    run agrees, else `drift` with what each run said."""
    settled: dict[str, dict] = {}
    for probe in FORECASTS:
        seen = [r.get(probe, "not_run") for r in runs]
        path = seen[0] if len(set(seen)) == 1 else DRIFT
        declared = {e["path"] for e in FORECASTS[probe]}
        settled[probe] = {
            "path": path if path in declared else DRIFT,
            "runs": seen,
            "declared": path in declared or path == DRIFT,
        }
    return settled


def run_all(grove: str, willow: str, n: int) -> list[dict[str, str]]:
    g = Server(grove, "/", "/grove-approve", "grove:read")
    w = Server(willow, "/mcp", "/mcp-approve", "willow")
    runs = []
    for _ in range(n):
        got = grove_run(g)
        got.update(willow_run(w, got.pop("_grove_access", "")))
        runs.append(got)
    return runs


def _reachable(base: str) -> bool:
    try:
        return (
            call(
                "GET",
                base.rstrip("/") + "/.well-known/oauth-authorization-server",
                timeout=3,
            ).status
            == 200
        )
    except OSError:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--grove", default="http://127.0.0.1:8767")
    ap.add_argument("--willow", default="http://127.0.0.1:8765")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument(
        "--forecast",
        action="store_true",
        help="write a wager per probe into --box before running",
    )
    ap.add_argument("--box", type=Path)
    ap.add_argument("--keys", type=Path)
    ap.add_argument("--label", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    for base in (a.grove, a.willow):
        if not _reachable(base):
            print(f"unreachable: {base}", file=sys.stderr)
            return 2

    wagers: dict[str, str] = {}
    tip = None
    if a.forecast:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from onescript import __main__ as cli
        from onescript import api

        if not (a.box and a.keys):
            ap.error("--forecast needs --box and --keys")
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
        for probe, dist in FORECASTS.items():
            got = api.forecast(cfg, f"oauth/{label}/{probe}", dist, by="desk")
            if got.get("code") != 0:
                print(json.dumps({"probe": probe, "forecast": got}), file=sys.stderr)
                return 1
            wagers[probe] = got["subject"]
            tip = got["tip"]

    settled = settle(run_all(a.grove, a.willow, a.runs))

    from_reverse = None
    if wagers:
        from onescript import reverse

        from_reverse = reverse.outcome_subject
    rows = []
    for probe, s in settled.items():
        expect = max(FORECASTS[probe], key=lambda e: e["p"])["path"]
        row = {
            "probe": probe,
            "expected": expect,
            "outcome": s["path"],
            "hit": s["path"] == expect,
            "runs": s["runs"],
        }
        if probe in wagers:
            outcome = {"path": s["path"]}
            row["proposal"] = {
                "wager": wagers[probe],
                "outcome": outcome,
                "subject": from_reverse(wagers[probe], outcome),
            }
        rows.append(row)

    if tip:
        # R3: the human seals this tip (it covers every wager) before any outcome
        print(json.dumps({"seal_tip_first": tip}))
    if a.json:
        for r in rows:
            print(json.dumps(r))
    else:
        w = max(len(r["probe"]) for r in rows)
        for r in rows:
            mark = "hit " if r["hit"] else "MISS"
            print(
                f"{mark}  {r['probe']:<{w}}  expected {r['expected']:<15} got {r['outcome']}"
            )
        misses = sum(not r["hit"] for r in rows)
        print(
            f"\n{len(rows)} probes x {a.runs} runs, {misses} missed the desk's top path"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
