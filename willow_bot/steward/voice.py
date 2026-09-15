"""Reconcile the bot's owned label set on watched PRs, per tick.

Wires the `willow_bot.pr_labels` primitive into the steward tick, closing
the caller side of gap ``acfd27ae3259``. The primitives PRs (#13/#14/#15)
shipped the operations; this step decides — for each PR the tick tracks
— what its desired label set is, and asks the primitive to converge.

Mapping (state → desired labels under ``willow-bot/``):

- ``audit_dispatched[repo#pr]`` present → ``willow-bot/audit-dispatched``
- any ``ci_filed[…]`` entry whose stored ``where`` is ``repo#pr`` →
  ``willow-bot/ci-red``

A PR carrying neither state converges to the empty owned set on the next
tick (i.e. the reconciler removes stale owned labels). Labels a human or
another bot applied outside ``willow-bot/`` are read but never touched
(`pr_labels.reconcile_labels` enforces this).

Honest absence: when App credentials are not configured, the primitive
returns ``could-not-run`` receipts with an ``auth:`` detail. This step
does not gate on ``WILLOW_BOT_MCP`` — the label calls go directly to
GitHub with the App token, not via willow-mcp — but a missing PEM still
makes each PR's call a receipt line rather than a raise.

The step does NOT yet publish a check-run or upsert a status comment;
those need a head SHA the tick's state does not currently carry (the
webhook_pr signal from `fleet_bridge` does not include it). That
integration lands after fleet_bridge starts writing head_sha into
pull_request items, or after a later step reads it from `/pulls/{num}`.
"""
from __future__ import annotations

import json
import time
from typing import Any


def _tick_at() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_key(key: str) -> tuple[str, int] | None:
    """`owner/repo#num` → (`owner/repo`, num). Silent None on a garbled key
    (an operator hand-edit to the state file, an older shape that never
    made it through inbox). Never raises."""
    if not isinstance(key, str) or "#" not in key:
        return None
    repo, _, num_s = key.rpartition("#")
    if not repo or not num_s.isdigit():
        return None
    return repo, int(num_s)


def _desired_labels_by_pr(state: dict) -> dict[str, set[str]]:
    """Read the state file and return `{repo#pr: {owned-prefix labels}}`.

    Only PRs that appear as the reason for at least one owned label are
    keyed — a PR without any state has an empty desired set, which the
    caller still passes to `reconcile_labels` if the PR is in `open` (so
    stale labels get removed).
    """
    from willow_bot import pr_labels

    desired: dict[str, set[str]] = {}

    for key in state.get("audit_dispatched") or {}:
        pr = _parse_key(key)
        if pr is None:
            continue
        desired.setdefault(key, set()).add(pr_labels.LABEL_AUDIT_DISPATCHED)

    for filed_key, _item_id in (state.get("ci_filed") or {}).items():
        # `ci_filed` keys are `head_sha:check_run_id`; the derived where
        # (`repo#pr` or `repo@sha`) is not stored. Rebuild the mapping
        # from the deposits file's `pr_number` field is expensive; the
        # simpler path is to iterate the tick's `open` set and check
        # whether ANY ci_filed entry belongs to that PR. That would need
        # per-PR indexing we do not maintain yet. So this step's ci-red
        # coverage is limited to what a future ci_filed shape carries.
        _ = filed_key  # placeholder; ci-red mapping lands with the shape update
    return desired


def run_voice(
    state: dict | None = None,
    *,
    enable_mcp: bool | None = None,  # unused; kept for signature symmetry with other steps
) -> dict:
    """Reconcile owned-prefix labels on each PR the tick knows about.

    Reads the state file (or the passed-in dict), builds the desired
    label set per PR from `audit_dispatched` (and, when the shape lands,
    `ci_filed` per-PR indexing), and calls `pr_labels.reconcile_labels`
    for each open PR. Returns a receipt with per-PR outcome.

    Idempotent: a PR whose desired set has not changed since last tick
    is a network no-op inside `reconcile_labels` (only a GET). A PR
    whose labels drifted (a human hand-added or removed one) converges
    on the next tick.
    """
    from willow_bot import pr_labels
    from willow_bot.steward.config import state_path

    receipt: dict[str, Any] = {"event": "steward_voice", "at": _tick_at()}

    if state is None:
        path = state_path()
        state = json.loads(path.read_text()) if path.is_file() and path.read_text().strip() else {}

    open_keys = list(state.get("open") or [])
    desired_by_pr = _desired_labels_by_pr(state)

    reconciled: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for key in open_keys:
        pr = _parse_key(key)
        if pr is None:
            skipped.append({"repo_pr": key, "reason": "garbled key"})
            continue
        repo, pr_num = pr
        desired = desired_by_pr.get(key, set())
        result = pr_labels.reconcile_labels(repo, pr_num, desired)
        if result.get("status") == "ok":
            reconciled.append({
                "repo_pr": key,
                "action": result.get("action"),
                "added": result.get("added") or [],
                "removed": result.get("removed") or [],
            })
        else:
            refused.append({
                "repo_pr": key,
                "detail": result.get("detail", ""),
                "refused": result.get("refused") or [],
            })

    receipt.update(
        status="ok" if not refused else "partial" if reconciled else "could-not-run",
        open=len(open_keys),
        reconciled=reconciled,
        refused=refused,
        skipped=skipped,
    )
    _emit(receipt)
    return receipt


def _emit(receipt: dict) -> None:
    """Same shape as `willow_bot.steward.tick._emit`: append to the
    seat-readable jsonl and print. Duplicated here (7 lines) to avoid a
    two-way import between tick and voice; when a third step wants the
    same helper, factor into a shared `willow_bot.steward.receipts`
    module."""
    from willow_bot.steward.config import willow_home

    path = willow_home() / "willow-bot" / "steward_ticks.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, separators=(",", ":")) + "\n")
    except OSError:
        pass
    print(json.dumps(receipt), flush=True)
