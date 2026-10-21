"""reach.py -- how code outside willow-bot finds the one script, once (flatten-2 F6.5).

Four shims each found the one script their own way and said "unreachable"
their own way: the-table's ``host.py`` (``Unreachable`` and exit 2), catgrid's
``daily._beacon`` (``ImportError``), Grove's ``seat_boxes`` (``ImportError``)
and ``seat_cli.load_bot`` (``SeatUnavailable``). This is the one reach, the
shape the-table's ``host.py`` had, generalized. The operator: "Using the
onescript at every path." (D-a: willow-mcp and ratatosk import it too.)

Where it looks:

  one-script   an explicit ``home``, else ``$ONESCRIPT_HOME``, else
               ``<repos>/willow-bot/one-script``
  the law      an explicit ``grove``, else ``$ONESCRIPT_GROVE``, else
               ``<repos>/willows-grove``

``<repos>`` is the directory holding the consuming repo, passed as ``repos``
(a consumer reaches this through its bootstrap copy, which computes it; see
``reach_bootstrap.py``). With no ``repos`` and no env there is no place to
look, and the answer is ``unreachable``, never a guessed path.

Two answers, never collapsed: ``reachable`` (found; the words ``populated``
and ``empty`` are for rows, not for a place) or ``unreachable`` with why
(``states.UNREACHABLE``). A caller that cannot reach it says so in its own
exception or exit code; it never falls back to a copy of its own. The "why"
text is made here, so every caller says it the same way.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

from onescript.states import UNREACHABLE

ENV_HOME = "ONESCRIPT_HOME"
ENV_GROVE = "ONESCRIPT_GROVE"
REACHABLE = "reachable"


class Unreachable(RuntimeError):
    """The one script, or the law it reads, is not where it was looked for."""


# -- the sentences (the bootstrap carries the first two, pinned equal by a test)


def no_onescript(home) -> str:
    if home is None:
        return (
            "no one-script: this copy sits in no git checkout, so there is no "
            f"sibling to look beside (set {ENV_HOME})"
        )
    return f"no one-script at {home} (set {ENV_HOME})"


def wont_import(home, err) -> str:
    return f"the one-script at {home} would not import: {err}"


def no_law(grove) -> str:
    if grove is None:
        return f"no law: no willows-grove to look beside (set {ENV_GROVE})"
    return f"no law: willows-grove's constitution isn't at {grove} (set {ENV_GROVE})"


def present(home) -> bool:
    """Is a one script at ``home``? (The file a consumer hands off to.)"""
    return home is not None and (Path(home) / "onescript" / "reach.py").is_file()


def where(repos=None, home=None, grove=None) -> dict:
    """Where the one script and the law are looked for (a path, or None when
    there is nowhere to look)."""
    home = home or os.environ.get(ENV_HOME)
    grove = grove or os.environ.get(ENV_GROVE)
    repos = Path(repos) if repos is not None else None
    return {
        "onescript": Path(home).expanduser()
        if home
        else (repos / "willow-bot" / "one-script" if repos else None),
        "grove": Path(grove).expanduser()
        if grove
        else (repos / "willows-grove" if repos else None),
    }


def reach(repos=None, home=None, grove=None, law: bool = True) -> dict:
    """``{"state": "reachable", "onescript", "grove"}`` or
    ``{"state": "unreachable", "why"}``. ``law=False`` does not ask for the
    law (a caller that imports modules and reads no constitution)."""
    w = where(repos, home, grove)
    if not present(w["onescript"]):
        return {"state": UNREACHABLE, "why": no_onescript(w["onescript"])}
    if law and not (
        w["grove"] and (w["grove"] / "governance" / "CONSTITUTION.md").is_file()
    ):
        return {"state": UNREACHABLE, "why": no_law(w["grove"])}
    return {"state": REACHABLE, **w}


def module(name: str, repos=None, home=None):
    """One of the one script's modules (``stream``, ``states``, ...). Needs the
    one script, not the law. Raises ``Unreachable`` (with why)."""
    home = where(repos, home)["onescript"]
    if not present(home):
        raise Unreachable(no_onescript(home))
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    try:
        return importlib.import_module(f"onescript.{name}")
    except ImportError as e:
        raise Unreachable(wont_import(home, e)) from e
