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


def fetch_job_log(repo: str, job_id: object) -> tuple[str | None, dict[str, Any]]:
    """The job's plain-text log, or ``(None, receipt)`` on any failure.

    Three receipted outcomes beyond ``ok``: ``missing_permission`` ==
    ``"actions:read"`` on a 403 (the one permission this endpoint can
    need — never guessed from response text, always this exact call
    site's own requirement); a 404 (job or log gone); anything else as
    ``detail``. Never raises.
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
            receipt.update(status="could-not-run", missing_permission="actions:read",
                           detail="GitHub 403 fetching job logs — the App installation lacks actions:read")
            return None, receipt
        if resp.status_code == 404:
            receipt.update(status="could-not-run", detail="job log not found (404)")
            return None, receipt
        try:
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            receipt.update(status="could-not-run", detail=str(exc)[:400])
            return None, receipt
        chunks: list[bytes] = []
        total = 0
        truncated = False
        for chunk in resp.iter_content(chunk_size=_CHUNK):
            if not chunk:
                continue
            remaining = MAX_LOG_BYTES - total
            if remaining <= 0:
                truncated = True
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total += remaining
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        resp.close()
    text = b"".join(chunks).decode("utf-8", errors="replace")
    receipt.update(status="ok", bytes=total, truncated=truncated)
    return text, receipt
