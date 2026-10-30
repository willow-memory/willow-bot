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
— one sticky comment per PR, edited in place when a push moves the head.
The body states the bot's view only: whether an audit was dispatched and
whether any CI red legs are filed for that sha (``ci_filed`` keys are
``head_sha:check_run_id``). It carries no tick time: a body that only
changed its clock was an edit (an API call, an ``issue_comment`` event)
on every open PR every tick. What was last written per PR is remembered
in ``$WILLOW_HOME/willow-bot/voice_cache.json``, so an unchanged view
makes no call at all; it is re-checked once a day in case a human
deleted the comment.

Hygiene labels, from one live read of the PR (``github_app.get_pull``)
and the operator profile's ``pr_hygiene`` section:

- ``willow-bot/size/{xs,s,m,l,xl}`` — lines added plus deleted
- ``willow-bot/conflict`` — GitHub says the PR cannot merge (``dirty``)
- ``willow-bot/stale`` — the head commit is older than ``stale_days``
- ``willow-bot/title`` — off by default; the title is not a conventional
  commit (``feat: …``, ``fix(scope)!: …``)
- ``willow-bot/area/NAME`` — off by default; a file the PR changes matches
  one of the globs ``pr_hygiene.paths`` lists under NAME (shell-style, as
  ``fnmatch`` reads them, so ``*`` crosses ``/``). The changed files are
  read once per head (``github_app.list_pull_files``) and cached

A fact the bot cannot read this tick (the PR is unreadable, GitHub has
not computed mergeability yet, the head's date is unknown) leaves its
label as it is: absence of an answer never strips a label.

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

import fnmatch
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from typing import Any

SIZE_PREFIX = "willow-bot/size/"
CONFLICT_LABEL = "willow-bot/conflict"
STALE_LABEL = "willow-bot/stale"
TITLE_LABEL = "willow-bot/title"
AREA_PREFIX = "willow-bot/area/"
HYGIENE_PREFIXES = (SIZE_PREFIX, CONFLICT_LABEL, STALE_LABEL, TITLE_LABEL, AREA_PREFIX)
# Upper bounds (exclusive) on lines added + deleted.
SIZE_BUCKETS = ((10, "xs"), (50, "s"), (250, "m"), (1000, "l"))
_CONVENTIONAL = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\([^)]+\))?!?: \S"
)
_RECHECK_S = 24 * 3600  # an unchanged comment / check is re-written at most daily
_MAX_COMMIT_DATES = 500
_MAX_FILE_LISTS = 200
#: GitHub's pulls/files listing stops at 3000; ours reads this many
_FILES_READ = 300


def _fetch_pull(repo: str, pr_num: int) -> dict:
    import github_app

    return github_app.get_pull(repo, pr_num)


def _fetch_files(repo: str, pr_num: int) -> list[str]:
    import github_app

    return [str(f.get("filename") or "") for f in github_app.list_pull_files(repo, pr_num)]


def _fetch_commit_date(repo: str, sha: str) -> str:
    import github_app

    return github_app.get_commit_date(repo, sha)


def _cache_path():
    from willow_bot.paths import bot_dir

    return bot_dir() / "voice_cache.json"


def _load_cache() -> dict:
    p = _cache_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("prs", {})
    data.setdefault("commit_dates", {})
    data.setdefault("files", {})
    return data


def _save_cache(cache: dict) -> None:
    p = _cache_path()
    dates = cache.get("commit_dates") or {}
    if len(dates) > _MAX_COMMIT_DATES:
        cache["commit_dates"] = dict(list(dates.items())[-_MAX_COMMIT_DATES:])
    files = cache.get("files") or {}
    if len(files) > _MAX_FILE_LISTS:
        cache["files"] = dict(list(files.items())[-_MAX_FILE_LISTS:])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]


def _fresh(entry: dict | None, field: str, digest: str, now: float) -> bool:
    """True when ``field`` was last written with this exact content within
    the re-check window — nothing to send."""
    return (isinstance(entry, dict) and entry.get(field) == digest
            and now - float(entry.get(f"{field}_at") or 0) < _RECHECK_S)


def size_label(changed: int) -> str:
    for bound, name in SIZE_BUCKETS:
        if changed < bound:
            return SIZE_PREFIX + name
    return SIZE_PREFIX + "xl"


def _parse_when(value: object) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _head_date(repo: str, key: str, head_sha: str, state: dict, cache: dict) -> datetime | None:
    """When the PR's head was pushed: the inbox's record of the push that
    named this head, else the commit's own date (read once per sha). Never
    the PR's ``updated_at``, which the bot's own edits move."""
    rec = (state.get("pr_heads") or {}).get(key)
    if isinstance(rec, dict) and rec.get("head_sha") == head_sha and rec.get("at"):
        when = _parse_when(rec["at"])
        if when:
            return when
    dates = cache.setdefault("commit_dates", {})
    if head_sha not in dates:
        try:
            dates[head_sha] = _fetch_commit_date(repo, head_sha)
        except Exception:  # noqa: BLE001 — unknown, not stale
            return None
    return _parse_when(dates[head_sha])


def _areas(raw: object) -> dict[str, list[str]]:
    """``pr_hygiene.paths`` as ``{name: [glob, ...]}``, keeping only names a
    label can carry (lowercase letters, digits and ``-``) and string globs."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for name, globs in raw.items():
        if isinstance(name, str) and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            kept = [g for g in (globs if isinstance(globs, list) else [globs]) if isinstance(g, str) and g]
            if kept:
                out[name] = kept
    return out


def _changed_files(repo: str, key: str, head_sha: str, cache: dict) -> list[str] | None:
    """The files the PR changes at this head, read once per head; None when
    GitHub can't be read, so the area labels stay as they are."""
    lists = cache.setdefault("files", {})
    slot = f"{key}@{head_sha}"
    if slot not in lists:
        _, _, num = key.rpartition("#")
        try:
            lists[slot] = _fetch_files(repo, int(num))
        except Exception:  # noqa: BLE001 — unknown, not empty
            return None
    return list(lists[slot])


def _hygiene(repo: str, key: str, pull: dict, state: dict, cache: dict,
             settings: dict, now: datetime) -> tuple[set[str], set[str]]:
    """(labels to have, label prefixes to leave as they are) for one PR."""
    desired: set[str] = set()
    keep: set[str] = set()
    if settings.get("size"):
        changed = pull.get("additions"), pull.get("deletions")
        if all(isinstance(n, int) for n in changed):
            desired.add(size_label(changed[0] + changed[1]))
        else:
            keep.add(SIZE_PREFIX)
    if settings.get("conflict"):
        if pull.get("mergeable_state") == "dirty":
            desired.add(CONFLICT_LABEL)
        elif pull.get("mergeable") is None:  # not computed yet
            keep.add(CONFLICT_LABEL)
    stale_days = settings.get("stale_days")
    if isinstance(stale_days, (int, float)) and not isinstance(stale_days, bool) and stale_days > 0:
        head_sha = str((pull.get("head") or {}).get("sha") or "")
        when = _head_date(repo, key, head_sha, state, cache) if head_sha else None
        if when is None:
            keep.add(STALE_LABEL)
        elif (now - when).total_seconds() > stale_days * 86400:
            desired.add(STALE_LABEL)
    if settings.get("conventional_titles") and not _CONVENTIONAL.match(str(pull.get("title") or "")):
        desired.add(TITLE_LABEL)
    areas = _areas(settings.get("paths"))
    if areas:
        head_sha = str((pull.get("head") or {}).get("sha") or "")
        files = _changed_files(repo, key, head_sha, cache) if head_sha else None
        if files is None:
            keep.add(AREA_PREFIX)
        else:
            desired |= {AREA_PREFIX + name for name, globs in areas.items()
                        if any(fnmatch.fnmatchcase(f, g) for f in files for g in globs)}
            if len(files) >= _FILES_READ:  # the listing may stop short: an absent area is unknown
                keep.add(AREA_PREFIX)
    return desired, keep


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
        if _head_is_red(state, key, head_sha):
            desired.setdefault(key, set()).add(pr_labels.LABEL_CI_RED)

    return desired


def _latest_head_sha_by_pr(state: dict) -> dict[str, str]:
    """`{repo#pr: head_sha}`: the inbox step's durable `pr_heads` map, over
    the tick's `webhook_signals` for anything it does not name yet (a state
    written before `pr_heads` existed). The signals window alone loses a
    quiet PR's head once ~100 check-run signals roll it out.

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
    for key, rec in (state.get("pr_heads") or {}).items():
        if isinstance(rec, dict) and rec.get("head_sha"):
            out[key] = rec["head_sha"]
    return out


def _head_is_red(state: dict, key: str, head_sha: str) -> bool:
    """Whether this PR's head is red NOW. The ci step keeps each head's
    latest conclusion per check in `ci_head_legs` (`repo#pr@sha`), so a
    re-run that went green on the same commit clears it. `ci_filed` only
    ever grows, so it is the answer only for a head the legs no longer
    name (pruned, or a state from before them)."""
    from willow_bot.steward.tick import _CI_RED

    legs = (state.get("ci_head_legs") or {}).get(f"{key}@{head_sha}")
    if isinstance(legs, dict) and legs:
        return any(c in _CI_RED for c in legs.values())
    return bool(_ci_red_legs_for_sha(state, head_sha))


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


def _status_comment_body(key: str, head_sha: str, state: dict, similar: dict | None = None) -> str:
    """The bot's terse view for this (PR, head_sha): audit dispatched or
    not, CI red legs filed for this sha or none, and the CI line as told
    to the seat (when an item is filed for this head). No clock: the body
    changes only when the view does. No exposition — a seat or operator
    reading the PR gets a few lines. For a repo whose CI keeps a
    ``similar`` artifact, its list follows (``steward.similar``)."""
    from willow_bot.steward import similar as _similar

    audit = "dispatched" if key in (state.get("audit_dispatched") or {}) else "not dispatched"
    red = _ci_red_legs_for_sha(state, head_sha)
    if red and not _head_is_red(state, key, head_sha):
        ci = f"{len(red)} red leg(s) filed, green since (re-run)"
    else:
        ci = f"{len(red)} red leg(s) filed" if red else "none filed"
    detail = _ci_detail_line(_ci_item_for_sha(state, head_sha))
    return (
        f"willow-bot status for `{head_sha[:12]}`\n"
        f"- audit: {audit}\n"
        f"- CI red: {ci}\n"
        + (f"- {detail}\n" if detail else "")
        + _similar.section(similar)
    )


def _similar_for(repo: str, head_sha: str, entry: dict) -> dict | None:
    """The ``similar`` list for a PR whose repo the profile names, from the
    voice cache when this head's answer is settled (a list, or a run that
    says nothing was added), else read again: CI may still be running, or a
    read that failed may work next tick. None for any other repo."""
    from willow_bot import operator_profile
    from willow_bot.steward import similar as _similar

    if repo.lower() not in operator_profile.similar_repos():
        return None
    held = entry.get("similar")
    if isinstance(held, dict) and held.get("sha") == head_sha and held.get("settled"):
        return held.get("result")
    result = _similar.fetch(repo, head_sha)
    settled = result.get("state") == "populated" or (
        result.get("state") == "empty" and _similar.NOTHING_ADDED in result.get("why", "")
    )
    entry["similar"] = {"sha": head_sha, "settled": settled, "result": result}
    return result


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
    if _head_is_red(state, key, head_sha):
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
    on the next tick. The comment is one per PR and the check one per
    head sha; either is written only when its content changed (or once a
    day), so a quiet PR costs no writes.
    """
    from willow_bot import operator_profile, pr_labels, pr_voice
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
    unread: list[dict[str, Any]] = []
    cache = _load_cache()
    settings = operator_profile.pr_hygiene()
    now = datetime.now(timezone.utc)

    for key in open_keys:
        pr = _parse_key(key)
        if pr is None:
            skipped.append({"repo_pr": key, "reason": "garbled key"})
            continue
        repo, pr_num = pr
        desired = set(desired_by_pr.get(key, set()))
        try:
            pull = _fetch_pull(repo, pr_num)
        except Exception as exc:  # noqa: BLE001 — unread PR: its hygiene labels stay as they are
            unread.append({"repo_pr": key, "detail": str(exc)[:200]})
            keep: set[str] = set(HYGIENE_PREFIXES)
        else:
            hygiene, keep = _hygiene(repo, key, pull, state, cache, settings, now)
            desired |= hygiene
        if keep:
            result = pr_labels.reconcile_labels(repo, pr_num, desired, keep=sorted(keep))
        else:
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
    check_skipped: list[dict[str, Any]] = []

    for key in open_keys:
        pr = _parse_key(key)
        if pr is None:
            continue  # already recorded in `skipped` above
        head_sha = head_sha_by_pr.get(key)
        if not head_sha:
            continue  # no webhook_pr signal has named a head yet
        repo, pr_num = pr
        entry = cache["prs"].setdefault(key, {})
        stamp = time.time()
        body = _status_comment_body(key, head_sha, state, _similar_for(repo, head_sha, entry))
        comment_digest = _digest(head_sha, body)
        if _fresh(entry, "comment", comment_digest, stamp):
            voiced.append({"repo_pr": key, "head_sha": head_sha, "action": "unchanged"})
        else:
            result = pr_voice.upsert_status_comment(repo, pr_num, head_sha, body)
            if result.get("status") == "ok":
                voiced.append({"repo_pr": key, "head_sha": head_sha, "action": result.get("action")})
                entry.update(comment=comment_digest, comment_at=stamp)
            else:
                voice_refused.append({
                    "repo_pr": key, "head_sha": head_sha, "detail": result.get("detail", ""),
                })

        conclusion = _check_conclusion(key, head_sha, state)
        check_digest = _digest(head_sha, conclusion, body)
        if _fresh(entry, "check", check_digest, stamp):
            checked.append({"repo_pr": key, "head_sha": head_sha,
                            "action": "unchanged", "conclusion": conclusion})
            continue
        check_result = pr_voice.publish_check(
            repo, head_sha, pr_voice.CHECK_NAME,
            status="completed", conclusion=conclusion,
            output={"title": "willow-bot", "summary": body},
        )
        if check_result.get("status") == "ok":
            entry.update(check=check_digest, check_at=stamp)
            checked.append({
                "repo_pr": key, "head_sha": head_sha,
                "action": check_result.get("action"), "conclusion": conclusion,
            })
        elif check_result.get("status") == "skipped":
            # The grant does not cover check-runs: said once per PR, not a
            # refusal — the status comment above carries the same view.
            check_skipped.append({
                "repo_pr": key, "head_sha": head_sha, "detail": check_result.get("detail", ""),
            })
        else:
            check_refused.append({
                "repo_pr": key, "head_sha": head_sha, "detail": check_result.get("detail", ""),
            })

    open_set = set(open_keys)
    cache["prs"] = {k: v for k, v in cache["prs"].items() if k in open_set and v}
    _save_cache(cache)

    any_refused = bool(refused) or bool(voice_refused) or bool(check_refused)
    any_ok = bool(reconciled) or bool(voiced) or bool(checked)
    receipt.update(
        status="ok" if not any_refused else "partial" if any_ok else "could-not-run",
        open=len(open_keys),
        reconciled=reconciled,
        refused=refused,
        skipped=skipped,
        unread=unread,
        voiced=voiced,
        voice_refused=voice_refused,
        checked=checked,
        check_refused=check_refused,
        check_skipped=check_skipped,
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
