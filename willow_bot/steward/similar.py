"""What's already on a repo's add-on list near what a pull request adds,
relayed from the repo's own CI into the bot's status comment.

The list is not the bot's. Willow's CI runs ``similar.py new`` over the
pull request and keeps the result as the run's ``similar`` artifact
(``similar.md``); the bot reads it for the PR's head and puts it in its one
status comment, which Willow's CONTRIBUTING.md promises. Only repos the
operator profile names under ``similar.repos`` are read.

Three states, never collapsed:

- ``populated`` — the list, ready for the comment;
- ``empty`` — the run says the pull request adds no add-on and no idea, or
  no run on this head has kept the artifact yet (CI may still be running);
- ``unreachable`` — the artifact couldn't be read (no ``actions: read``
  grant, an HTTP error, a broken zip), with why.

The artifact comes from a CI run on the pull request's own code, so on a
fork it is the fork's text: it is relayed as untrusted. HTML is escaped,
so it can't carry a comment marker the status comment's upsert would
mistake for its own, mentions are broken so it can't ping anyone, and it
is cut at ``MAX_CHARS``.
"""
from __future__ import annotations

import io
import zipfile

ARTIFACT = "similar"
MEMBER = "similar.md"
MAX_CHARS = 6000
#: what similar.py prints when a pull request adds nothing to the list
NOTHING_ADDED = "adds no add-on and no idea"


def clean(text: str) -> str:
    """The artifact's text made safe to post under the bot's name."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("@", "@\u200b")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rsplit("\n", 1)[0] + "\n\n…(cut; the full list is in the run's summary)"
    return text.strip()


def fetch(repo: str, head_sha: str) -> dict:
    """The ``similar`` list for this head, as one of the three states."""
    import github_app

    try:
        runs = github_app.list_workflow_runs(repo, head_sha)
    except Exception as exc:  # noqa: BLE001 — said as unreachable, never raised into the tick
        return {"state": "unreachable", "why": f"couldn't list CI runs: {str(exc)[:160]}"}
    for run in runs:
        run_id = run.get("id")
        if not run_id:
            continue
        try:
            artifacts = github_app.list_run_artifacts(repo, run_id)
        except Exception as exc:  # noqa: BLE001
            return {"state": "unreachable", "why": f"couldn't list run {run_id}'s artifacts: {str(exc)[:160]}"}
        found = next((a for a in artifacts if a.get("name") == ARTIFACT and not a.get("expired")), None)
        if not found:
            continue
        try:
            blob = github_app.download_artifact(repo, found["id"])
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                text = z.read(MEMBER).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return {"state": "unreachable", "why": f"couldn't read the {ARTIFACT} artifact: {str(exc)[:160]}"}
        if NOTHING_ADDED in text:
            return {"state": "empty", "why": "the pull request adds no add-on and no idea"}
        return {"state": "populated", "text": clean(text)}
    return {"state": "empty", "why": f"no CI run on this head has kept the {ARTIFACT} artifact yet"}


def section(result: dict | None) -> str:
    """The status comment's lines for a fetch result: the list when there
    is one, a line saying why when it couldn't be read, nothing otherwise."""
    if not result:
        return ""
    if result.get("state") == "populated":
        return "\n" + result["text"] + "\n"
    if result.get("state") == "unreachable":
        return f"- similar: couldn't read CI's list ({result.get('why', '')})\n"
    return ""
