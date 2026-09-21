"""Fetch a red job's log and pull the failure block out of it, plain
stdlib (+ requests, already a dependency via github_app), no willow-mcp.

The steward files a review item on a red check_run but has never shown
anyone WHY it went red — gaps 1d28527ad324, 2c2ab8bd9209 (2026-09-21,
PR #594 went red twice, silence). willow-mcp is getting the same
extraction rules for `pr_checks_read` (packet feat/pr-checks-failure-block)
but a broker outage must not take the bot's own voice down with it, so
the rules are implemented here independently: same shape, no import from
willow-mcp.

``fetch_job_log`` calls
``GET /repos/{repo}/actions/jobs/{job_id}/logs`` under the GitHub App's
installation token; GitHub Actions job logs and their check-runs share
one id space, so a check_run's own id is a valid job id here. The
request follows GitHub's redirect to blob storage automatically
(``requests`` does this by default) and the body is read in bounded
chunks so a huge log cannot be pulled entirely into memory before the
cap is enforced.

``extract_failure_block`` tries, in order, a pytest ``FAILURES`` block,
a ruff ``file:line:col`` block, then a generic ``##[error]`` window —
the same three rules willow-mcp's ``pr_checks_read`` uses. Nothing
matching falls back to the log's own tail, named ``tail`` so a reader
can tell "extracted" from "gave up and showed the end".

The byte cap keeps the TAIL of the log, not the head (Loki's audit,
finding 3 — the old head-kept cap handed a >5 MB log's mid-log noise to
the extractor labelled ``source: "tail"`` while the real pytest
``FAILURES`` block, which sits at the end of the log, was already gone).
``fetch_job_log`` streams into a bounded buffer and drops from the FRONT
whenever it grows past ``MAX_LOG_BYTES``, so what survives is always the
log's own last ``MAX_LOG_BYTES`` bytes.

A 403 is never guessed into a permission name from the status code
alone: ``classify_403`` reads ``X-RateLimit-Remaining`` / ``Retry-After``
first (a rate limit is a pause, not a missing permission) and otherwise
the response body's own ``message`` — only GitHub's own wording for "you
can't do this" (``Resource not accessible by integration`` / ``Not
Found`` for a repo the App is not installed on) is reported as a named
permission; anything else is ``forbidden_unknown`` with the message
quoted, never invented.
"""
from __future__ import annotations

import re
from typing import Any

import requests

import github_app

# GitHub App job logs run large on a flaky matrix leg; bounded so one
# call cannot pull an unbounded blob into memory.
MAX_LOG_BYTES = 5 * 1024 * 1024
_CHUNK = 65536

# The failure block, once extracted, is trimmed to this many lines before
# it goes in a PR comment — a comment body has its own cap and a 50k-line
# log must not blow through it.
TRIM_LINES = 120

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ", re.M)

_PYTEST_FAILURES_RE = re.compile(r"^_* ?FAILURES ?_*$|^=+ ?FAILURES ?=+$", re.M)
_PYTEST_TERMINAL_RE = re.compile(
    r"^=+.*\b(passed|failed|error|warning)s?\b.*=+\s*$", re.M | re.I
)
_RUFF_LINE_RE = re.compile(r"^\S+\.py:\d+:\d+:.*$", re.M)


def strip_timestamps(text: str) -> str:
    """Drop GitHub's leading ``2026-09-21T00:12:34.1234567Z `` timestamp
    from every line. A line with none passes through unchanged."""
    return _TIMESTAMP_RE.sub("", text)


def _extract_pytest(text: str) -> str | None:
    m = _PYTEST_FAILURES_RE.search(text)
    if not m:
        return None
    tail = text[m.start():]
    # The short test summary's own count line ("=== 2 failed, 10 passed
    # in 3.45s ===") is the last terminal-looking `===` banner in the
    # tail — walk to the LAST match so a block with both a "FAILURES"
    # banner and a later summary banner ends at the summary, not at the
    # first banner found.
    terminals = list(_PYTEST_TERMINAL_RE.finditer(tail))
    if terminals:
        return tail[: terminals[-1].end()].strip("\n")
    return tail.strip("\n")


def _extract_ruff(text: str) -> str | None:
    lines = _RUFF_LINE_RE.findall(text)
    return "\n".join(lines) if lines else None


def _extract_generic(text: str) -> str | None:
    lines = text.splitlines()
    idxs = [i for i, ln in enumerate(lines) if "##[error]" in ln]
    if not idxs:
        return None
    start = max(0, idxs[0] - 40)
    end = idxs[-1] + 1
    return "\n".join(lines[start:end])


def _trim(block: str) -> tuple[str, bool]:
    lines = block.splitlines()
    if len(lines) <= TRIM_LINES:
        return block, False
    return "\n".join(lines[-TRIM_LINES:]), True


def extract_failure_block(log_text: str) -> dict[str, Any]:
    """``{"block": str, "trimmed": bool, "source": "pytest"|"ruff"|"generic"|"tail"}``.

    Tries pytest, then ruff, then a generic ``##[error]`` window; a log
    matching none of the three falls back to its own last ``TRIM_LINES``
    lines, marked ``source: "tail"`` — the block is never empty for any
    non-empty log, but a ``tail`` source tells a reader the block is a
    guess at relevance, not a located failure.
    """
    stripped = strip_timestamps(log_text)
    for source, fn in (("pytest", _extract_pytest), ("ruff", _extract_ruff), ("generic", _extract_generic)):
        block = fn(stripped)
        if block:
            text, trimmed = _trim(block)
            return {"block": text, "trimmed": trimmed, "source": source}
    tail_lines = stripped.splitlines()[-TRIM_LINES:]
    return {"block": "\n".join(tail_lines), "trimmed": len(stripped.splitlines()) > TRIM_LINES, "source": "tail"}


# GitHub's own wording for "the App cannot do this" — the ONLY body
# messages a 403 is ever mapped to a named permission from. Anything else
# (a rate limit, SAML enforcement, something new) is reported honestly as
# `forbidden_unknown` rather than guessed into a permission that may not
# be the actual problem.
_KNOWN_PERMISSION_MESSAGES = frozenset({
    "Resource not accessible by integration",
    "Not Found",
})


def classify_403(resp: Any) -> dict[str, Any]:
    """Distinguish a rate limit from a real permission denial on a 403,
    and never invent a permission name from the status code alone.

    Checked in order: (1) ``X-RateLimit-Remaining: 0`` or a ``Retry-After``
    header — a limiter, not a permission problem, so this is
    ``{"kind": "rate_limited", ...}`` and the caller should retry later;
    (2) the response body's own ``message`` — only GitHub's own exact
    wording for "you can't do this" (`_KNOWN_PERMISSION_MESSAGES`) maps to
    ``{"kind": "missing_permission"}``; (3) anything else is
    ``{"kind": "forbidden_unknown", "message": ...}`` — the message is
    quoted, not interpreted, so a reader can judge for themselves.
    """
    headers = getattr(resp, "headers", None) or {}
    remaining = headers.get("X-RateLimit-Remaining")
    retry_after = headers.get("Retry-After")
    if remaining == "0" or retry_after is not None:
        return {"kind": "rate_limited", "retry_after": retry_after}
    message = ""
    try:
        body = resp.json()
        if isinstance(body, dict):
            message = str(body.get("message") or "")
    except Exception:  # noqa: BLE001 — an unparsable body is just an empty message
        message = ""
    if message in _KNOWN_PERMISSION_MESSAGES:
        return {"kind": "missing_permission", "message": message}
    return {"kind": "forbidden_unknown", "message": message or "403 with no message"}


def fetch_job_log(repo: str, job_id: object) -> tuple[str | None, dict[str, Any]]:
    """The job's plain-text log, or ``(None, receipt)`` on any failure.

    Receipted outcomes beyond ``ok``: ``missing_permission`` ==
    ``"actions:read"`` on a 403 whose body names it exactly
    (``classify_403``); ``status: "rate_limited"`` on a 403 that is
    actually a rate limit (never named as a permission); a 404 (job or
    log gone); anything else as ``detail``. Never raises.
    """
    receipt: dict[str, Any] = {"repo": repo, "job_id": job_id}
    try:
        headers = github_app._auth_headers(repo)
    except Exception as exc:  # noqa: BLE001 — an unavailable App is a line, not a raise
        receipt.update(status="could-not-run", detail=f"auth: {exc}"[:400])
        return None, receipt
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs",
            headers=headers, timeout=15, stream=True,
        )
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"fetch: {exc}"[:400])
        return None, receipt
    try:
        if resp.status_code == 403:
            cls = classify_403(resp)
            if cls["kind"] == "rate_limited":
                receipt.update(status="rate_limited", detail="GitHub rate limit fetching job logs",
                               retry_after=cls.get("retry_after"))
            elif cls["kind"] == "missing_permission":
                receipt.update(status="could-not-run", missing_permission="actions:read",
                               detail=f"GitHub 403 ({cls['message']}) fetching job logs — "
                                      "the App installation lacks actions:read")
            else:
                receipt.update(status="could-not-run", forbidden_unknown=True,
                               detail=f"GitHub 403 fetching job logs: {cls['message']}"[:400])
            return None, receipt
        if resp.status_code == 404:
            receipt.update(status="could-not-run", detail="job log not found (404)")
            return None, receipt
        try:
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            receipt.update(status="could-not-run", detail=str(exc)[:400])
            return None, receipt
        # Keep the TAIL: a rolling buffer that drops from the FRONT once it
        # grows past the cap, so what survives is always the log's own last
        # MAX_LOG_BYTES bytes — the pytest FAILURES / short-summary block a
        # CI log ends with, not whatever ran first.
        buf = bytearray()
        truncated = False
        for chunk in resp.iter_content(chunk_size=_CHUNK):
            if not chunk:
                continue
            buf.extend(chunk)
            if len(buf) > MAX_LOG_BYTES:
                truncated = True
                del buf[: len(buf) - MAX_LOG_BYTES]
    finally:
        resp.close()
    text = bytes(buf).decode("utf-8", errors="replace")
    receipt.update(status="ok", bytes=len(buf), truncated=truncated)
    return text, receipt
