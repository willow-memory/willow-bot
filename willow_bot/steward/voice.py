"""Reconcile the bot's owned label set on watched PRs, per tick.

Wires the `willow_bot.pr_labels` primitive into the steward tick, closing
the caller side of gap ``acfd27ae3259``. The primitives PRs (#13/#14/#15)
shipped the operations; this step decides — for each PR the tick tracks
— what its desired label set is, and asks the primitive to converge.

Mapping (state → desired labels under ``willow-bot/``):

- ``audit_dispatched[repo#pr]`` present → ``willow-bot/audit-dispatched``
- the PR's latest head_sha (from ``webhook_signals``, same lookup the
  voice step uses) has any ``ci_filed`` key with that ``head_sha:``
  prefix → ``willow-bot/ci-red``

A PR carrying neither state converges to the empty owned set on the next
tick (i.e. the reconciler removes stale owned labels). Labels a human or
another bot applied outside ``willow-bot/`` are read but never touched
(`pr_labels.reconcile_labels` enforces this).

Honest absence: when App credentials are not configured, the primitive
returns ``could-not-run`` receipts with an ``auth:`` detail. This step
does not gate on ``WILLOW_BOT_MCP`` — the label calls go directly to
GitHub with the App token, not via willow-mcp — but a missing PEM still
makes each PR's call a receipt line rather than a raise.

Voice sub-part, second half: for each open PR whose latest ``webhook_pr``
signal carries a ``head_sha`` (fleet_bridge now writes it; inbox carries
it through into ``webhook_signals``), this step also upserts one bot-owned
status comment on that PR via ``willow_bot.pr_voice.upsert_status_comment``
— keyed on the head_sha, so a force-push (new head_sha) opens a fresh
comment and leaves the old one, while a repeat tick against the same
head_sha updates the same comment in place. The body states the bot's
view only: whether an audit was dispatched, whether any CI red legs are
filed for that sha (``ci_filed`` keys are ``head_sha:check_run_id``), and
the tick time.

Voice sub-part, third half: for the same (repo, pr, head_sha) this step
also publishes one bot check-run via ``willow_bot.pr_voice.publish_check``
— ``willow_bot.pr_voice.CHECK_NAME``, upserted per head_sha exactly like
the comment (``publish_check`` GETs by ``(head_sha, name, filter=app)``
before deciding POST vs PATCH, so a re-run against the same sha never
opens a second check). The conclusion is the bot's own view, in this
order: ``failure`` when a red leg is filed for this sha; else ``success``
when the audit is dispatched or was never required; else ``neutral`` when
the audit is still pending and nothing else is known yet.
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

    # `ci_filed` keys are `head_sha:check_run_id`; the PR they belong to
    # is not stored there. `_latest_head_sha_by_pr` gives the other half
    # of the join (repo#pr -> its current head_sha, from webhook_signals),
    # so a PR gets `ci-red` exactly when a `ci_filed` key carries its
    # latest head_sha's prefix.
    for key, head_sha in _latest_head_sha_by_pr(state).items():
        if _ci_red_legs_for_sha(state, head_sha):
            desired.setdefault(key, set()).add(pr_labels.LABEL_CI_RED)

    return desired


def _latest_head_sha_by_pr(state: dict) -> dict[str, str]:
    """`{repo#pr: head_sha}` from the tick's `webhook_signals`.

    Only `webhook_pr` signals carry both `repo_pr` and `head_sha`;
    `webhook_check_run` signals are skipped (they key by `kind`, not
    `repo_pr`, and their own `head_sha` names a check's commit, not
    necessarily the PR's current head). Signals are stored oldest-first
    and capped at `_MAX_SIGNALS` in `inbox.ingest`, so a later entry for
    the same key overwrites an earlier one here — a force-push's fresh
    `synchronize` signal wins over its PR's `opened` signal. A signal
    with no `head_sha` (an item queued before fleet_bridge started
    writing it) leaves any prior mapping for that key untouched rather
    than blanking it back out.
    """
    out: dict[str, str] = {}
    for sig in state.get("webhook_signals") or []:
        if sig.get("kind") == "check_run":
            continue
        key = sig.get("repo_pr")
        sha = sig.get("head_sha")
        if key and sha:
            out[key] = sha
    return out


def _ci_red_legs_for_sha(state: dict, head_sha: str) -> list[str]:
    """`ci_filed` keys (`head_sha:check_run_id`) belonging to this sha,
    sorted. Empty when nothing has been filed for it yet."""
    prefix = f"{head_sha}:"
    return sorted(k for k in (state.get("ci_filed") or {}) if k.startswith(prefix))


def _ci_item_for_sha(state: dict, head_sha: str) -> dict | None:
    """The `ci_items` entry filed for this head, if any (one item per head
    by construction; the first match wins)."""
    for item in (state.get("ci_items") or {}).values():
        if isinstance(item, dict) and item.get("head_sha") == head_sha and item.get("id"):
            return item
    return None


def _ci_detail_line(item: dict | None) -> str | None:
    """The CI line the seat was told (pair 11ccb0f7), for the PR comment —
    same words on the PR as on the seat's channel: filed red, filed stuck,
    or resolved with why. None when nothing is filed for this head."""
    if not item:
        return None
    from willow_bot.steward import tick as _tick

    if item.get("resolved"):
        return _tick._ci_line_resolved(item)
    return _tick._ci_line_filed(item)


def _status_comment_body(key: str, head_sha: str, state: dict, *, at: str) -> str:
    """The bot's terse view for this (PR, head_sha): audit dispatched or
    not, CI red legs filed for this sha or none, the CI line as told to
    the seat (when an item is filed for this head), and the tick time. No
    exposition — a seat or operator reading the PR gets a few lines."""
    audit = "dispatched" if key in (state.get("audit_dispatched") or {}) else "not dispatched"
    red = _ci_red_legs_for_sha(state, head_sha)
    ci = f"{len(red)} red leg(s) filed" if red else "none filed"
    detail = _ci_detail_line(_ci_item_for_sha(state, head_sha))
    return (
        f"willow-bot status for `{head_sha[:12]}`\n"
        f"- audit: {audit}\n"
        f"- CI red: {ci}\n"
        + (f"- {detail}\n" if detail else "")
        + f"- last tick: {at}\n"
    )


def _audit_state_ok(key: str, state: dict) -> bool:
    """True when this PR's audit posture is settled: dispatched already,
    or never queued for one. False only when the PR sits in
    `pending_audit` with no matching `audit_dispatched` entry yet — an
    audit is required and has not happened."""
    if key in (state.get("audit_dispatched") or {}):
        return True
    pending_keys = {
        p.get("repo_pr") for p in (state.get("pending_audit") or []) if isinstance(p, dict)
    }
    return key not in pending_keys


def _check_conclusion(key: str, head_sha: str, state: dict) -> str:
    """The bot's check-run conclusion for this (PR, head_sha), in order:

    - ``failure`` — a red leg is filed for this sha (any `ci_filed` key
      with the `head_sha:` prefix), regardless of audit state.
    - ``success`` — no red leg, and the audit is dispatched or was never
      required (`_audit_state_ok`).
    - ``neutral`` — no red leg, but the audit is still pending — nothing
      is known yet.
    """
    if _ci_red_legs_for_sha(state, head_sha):
        return "failure"
    if _audit_state_ok(key, state):
        return "success"
    return "neutral"


def run_voice(
    state: dict | None = None,
    *,
    enable_mcp: bool | None = None,  # unused; kept for signature symmetry with other steps
) -> dict:
    """Reconcile owned-prefix labels on each PR the tick knows about, then
    upsert one status comment per PR that has a known head_sha.

    Reads the state file (or the passed-in dict), builds the desired
    label set per PR from `audit_dispatched` and `ci_filed` (joined
    through the PR's latest head_sha), and calls
    `pr_labels.reconcile_labels` for each open PR. Separately, for each
    open PR whose latest `webhook_pr` signal carries a `head_sha`, calls
    `pr_voice.upsert_status_comment` with the bot's terse view, then
    `pr_voice.publish_check` with the same view's conclusion. Returns a
    receipt with per-PR outcome for all three.

    Idempotent: a PR whose desired set has not changed since last tick
    is a network no-op inside `reconcile_labels` (only a GET). A PR
    whose labels drifted (a human hand-added or removed one) converges
    on the next tick. The comment upsert and the check-run publish are
    both keyed on head_sha — a repeat call for the same sha updates the
    same comment/check; a new sha (a force-push) opens a fresh one of
    each.
    """
    from willow_bot import pr_labels, pr_voice
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

    head_sha_by_pr = _latest_head_sha_by_pr(state)
    voiced: list[dict[str, Any]] = []
    voice_refused: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    check_refused: list[dict[str, Any]] = []

    for key in open_keys:
        pr = _parse_key(key)
        if pr is None:
            continue  # already recorded in `skipped` above
        head_sha = head_sha_by_pr.get(key)
        if not head_sha:
            continue  # no webhook_pr signal has named a head yet
        repo, pr_num = pr
        body = _status_comment_body(key, head_sha, state, at=receipt["at"])
        result = pr_voice.upsert_status_comment(repo, pr_num, head_sha, body)
        if result.get("status") == "ok":
            voiced.append({"repo_pr": key, "head_sha": head_sha, "action": result.get("action")})
        else:
            voice_refused.append({
                "repo_pr": key, "head_sha": head_sha, "detail": result.get("detail", ""),
            })

        conclusion = _check_conclusion(key, head_sha, state)
        check_result = pr_voice.publish_check(
            repo, head_sha, pr_voice.CHECK_NAME,
            status="completed", conclusion=conclusion,
            output={"title": "willow-bot", "summary": body},
        )
        if check_result.get("status") == "ok":
            checked.append({
                "repo_pr": key, "head_sha": head_sha,
                "action": check_result.get("action"), "conclusion": conclusion,
            })
        else:
            check_refused.append({
                "repo_pr": key, "head_sha": head_sha, "detail": check_result.get("detail", ""),
            })

    any_refused = bool(refused) or bool(voice_refused) or bool(check_refused)
    any_ok = bool(reconciled) or bool(voiced) or bool(checked)
    receipt.update(
        status="ok" if not any_refused else "partial" if any_ok else "could-not-run",
        open=len(open_keys),
        reconciled=reconciled,
        refused=refused,
        skipped=skipped,
        voiced=voiced,
        voice_refused=voice_refused,
        checked=checked,
        check_refused=check_refused,
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
