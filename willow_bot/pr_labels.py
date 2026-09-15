"""Steward-state labels on a PR: reconcile the bot's owned label set.

Gap ``acfd27ae3259`` (labels sub-part). The bot has no visible label set
on a PR, so an operator scanning the PR queue cannot tell CI red from
needs-ratification from audit-dispatched from bot-opened without reading
the PR itself. This module gives the bot a label vocabulary under a
namespace it owns (``willow-bot/*``) and reconciles the currently applied
set to a desired set on every call — additive within its own namespace,
never touching labels a human or another bot applied.

One operation:

- ``reconcile_labels(repo, pr_number, desired, owned_prefix="willow-bot/")``
  — computes what to add and what to remove under the owned prefix and
  makes the two HTTP calls (POST /labels for add, DELETE for each remove).
  A repeat call with the same desired set is a no-op. Labels outside the
  prefix are read but not touched. Labels in ``desired`` that don't yet
  exist as repo labels are still requested — GitHub auto-creates a label
  it has never seen on a POST /labels call, which is the fleet's
  convention (nothing here writes to /labels/{name} to define color).

The vocabulary this ships with:

- ``willow-bot/ci-red`` — one or more terminal checks concluded red
- ``willow-bot/needs-ratification`` — human review before merge
- ``willow-bot/audit-dispatched`` — audit packet sent to Loki
- ``willow-bot/bot-opened`` — the PR was opened by willows-bot

Every string in ``desired`` must start with ``owned_prefix``; a caller
passing a label outside the namespace gets a receipt line, no HTTP call,
because reconciling would then delete it as soon as it left ``desired``.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

import requests

import github_app

log = logging.getLogger("willow-bot.pr_labels")


DEFAULT_PREFIX = "willow-bot/"

# The vocabulary — a caller may pass any label that starts with the prefix,
# but these are the ones the steward's own steps will use, listed once so
# a rename lands in one place.
LABEL_CI_RED = "willow-bot/ci-red"
LABEL_NEEDS_RATIFICATION = "willow-bot/needs-ratification"
LABEL_AUDIT_DISPATCHED = "willow-bot/audit-dispatched"
LABEL_BOT_OPENED = "willow-bot/bot-opened"


def _list_pr_labels(repo: str, pr_number: int, headers: dict) -> list[str]:
    """The label names currently applied to the PR. Bounded per page; a
    PR with 100+ labels is possible only in pathological cases but the
    walker keeps going so a hostile state is still convergent."""
    out: list[str] = []
    page = 1
    while True:
        r = requests.get(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/labels",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=10,
        )
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list) or not batch:
            break
        for lbl in batch:
            name = lbl.get("name") if isinstance(lbl, dict) else None
            if isinstance(name, str) and name:
                out.append(name)
        if len(batch) < 100:
            break
        page += 1
    return out


def _add_labels(repo: str, pr_number: int, labels: list[str], headers: dict) -> list[str]:
    """POST /labels adds — the response is the FULL current label set on
    the issue (per the API), not just the ones we added. GitHub auto-
    creates any label name it has never seen (default color) so a fresh
    fleet repo does not need a labels-yaml step first."""
    r = requests.post(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/labels",
        headers=headers,
        json={"labels": labels},
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, list):
        return []
    return [x.get("name", "") for x in payload if isinstance(x, dict)]


def _remove_label(repo: str, pr_number: int, label: str, headers: dict) -> None:
    """DELETE /labels/{name} removes one. 404 on that URL means the label
    was already gone (a concurrent reconcile beat us) — the caller reads
    the final list and moves on."""
    # requests handles path encoding of the label name.
    r = requests.delete(
        f"https://api.github.com/repos/{repo}/issues/{pr_number}/labels/{label}",
        headers=headers,
        timeout=10,
    )
    if r.status_code == 404:
        return
    r.raise_for_status()


def reconcile_labels(
    repo: str,
    pr_number: int,
    desired: Iterable[str],
    *,
    owned_prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    """Bring the PR's owned-prefix label set to exactly ``desired``.

    Labels outside ``owned_prefix`` are read (for the receipt) but not
    touched. Every string in ``desired`` must start with ``owned_prefix``
    or the whole call is refused — otherwise a caller passing a label
    outside its namespace would see it added and then immediately deleted
    on the next reconcile, which is worse than no label at all.

    Returns a receipt with the labels the bot found, the ones it added,
    the ones it removed, and the ones it could not remove. A repeat call
    with an unchanged ``desired`` is a no-op on the network — the diff is
    empty and no POST/DELETE is issued.
    """
    receipt: dict[str, Any] = {"repo": repo, "pr": pr_number, "prefix": owned_prefix}
    desired_set = {s for s in desired if isinstance(s, str) and s}
    out_of_ns = sorted(s for s in desired_set if not s.startswith(owned_prefix))
    if out_of_ns:
        receipt.update(status="could-not-run", action="skipped",
                       detail=f"desired must all start with {owned_prefix!r}; got {out_of_ns}")
        return receipt

    try:
        headers = github_app._auth_headers(repo)
    except Exception as exc:  # noqa: BLE001 — an unavailable App is a line, not a raise
        receipt.update(status="could-not-run", detail=f"auth: {exc}"[:400], action="skipped")
        return receipt

    try:
        current = _list_pr_labels(repo, pr_number, headers)
    except Exception as exc:  # noqa: BLE001
        receipt.update(status="could-not-run", detail=f"list: {exc}"[:400], action="skipped")
        return receipt

    current_set = set(current)
    owned_current = {c for c in current_set if c.startswith(owned_prefix)}
    to_add = sorted(desired_set - owned_current)
    to_remove = sorted(owned_current - desired_set)

    receipt["found_owned"] = sorted(owned_current)
    receipt["found_other"] = sorted(current_set - owned_current)

    added: list[str] = []
    removed: list[str] = []
    refused: list[dict[str, str]] = []

    if to_add:
        try:
            _add_labels(repo, pr_number, to_add, headers)
            added = list(to_add)
        except Exception as exc:  # noqa: BLE001
            refused.append({"op": "add", "labels": ",".join(to_add), "error": str(exc)[:300]})

    for lbl in to_remove:
        try:
            _remove_label(repo, pr_number, lbl, headers)
            removed.append(lbl)
        except Exception as exc:  # noqa: BLE001
            refused.append({"op": "remove", "labels": lbl, "error": str(exc)[:300]})

    receipt.update(
        status="ok" if not refused else "could-not-run",
        action="reconciled" if (added or removed) else "no-op",
        added=added, removed=removed, refused=refused,
    )
    return receipt
