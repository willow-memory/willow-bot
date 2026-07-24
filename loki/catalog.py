"""
catalog.py — Loki's repo <-> catalog identity crosswalk.
b17: LOKI3

Loki scans *top-level repos* under ``~/github`` (safe-app-grove, willow-mcp,
willow-bot, ...), but the SAFE App Store catalog keys apps by a short ``id``
(``grove``, ``willow-grove``) that rarely equals the repo directory name
(``safe-app-grove``, ``safe-app-willow-grove``). The original checks compared
the directory name to the raw id set, which produced two classes of false
positive:

  1. **Prefix/name mismatch** — ``safe-app-grove`` never matched id ``grove``,
     so a cataloged app looked uncataloged.
  2. **Infra repos** — ``willow-mcp`` / ``willow-bot`` / ``willow-2.0`` are not
     store apps at all, yet tripped Hermes ("uncataloged repo with commits")
     on every scan.

This module fixes both. :class:`CatalogIndex` resolves a repo directory to its
catalog identity through a crosswalk (id, ``repository`` URL basename, the
``safe-app-`` prefix stripped, and the repo's own ``safe-app-manifest.json``
``app_id``). :func:`has_manifest` / :func:`read_manifest_app_id` let the watcher
gate on "does this repo even declare itself a SAFE app?" — so infra repos, which
carry no ``safe-app-manifest.json``, are never mistaken for missing apps.

Stdlib only. No network.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("loki.catalog")

_APP_PREFIX = "safe-app-"
_MANIFEST_NAME = "safe-app-manifest.json"


def has_manifest(repo_path) -> bool:
    """True if ``repo_path`` declares itself a SAFE app (has a manifest).

    Infra repos (willow-mcp, willow-bot, willow-2.0, ...) carry no
    ``safe-app-manifest.json`` and so return ``False`` — the signal the watcher
    uses to leave them out of "should be cataloged" checks entirely.
    """
    return (Path(repo_path) / _MANIFEST_NAME).is_file()


def read_manifest_app_id(repo_path) -> Optional[str]:
    """Return a repo's declared ``app_id`` from its ``safe-app-manifest.json``.

    ``None`` if there is no manifest or it can't be parsed / has no ``app_id``.
    """
    manifest = Path(repo_path) / _MANIFEST_NAME
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        return None
    app_id = data.get("app_id")
    return app_id if isinstance(app_id, str) and app_id else None


def _repo_basename(repository: str) -> str:
    """Basename of a ``repository`` URL/path, minus any ``.git`` suffix."""
    if not repository:
        return ""
    name = repository.rstrip("/").split("/")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


class CatalogIndex:
    """A resolver from a repo directory name to its catalog identity.

    Build with :meth:`load`; ask :meth:`is_cataloged` whether a repo on disk is
    already in the catalog, or :meth:`resolve` for the canonical id it maps to.
    """

    def __init__(self, ids: set[str], repo_names: set[str]) -> None:
        self.ids = ids
        self.repo_names = repo_names

    @property
    def identities(self) -> set[str]:
        """Every string that counts as "known": ids plus repository basenames."""
        return self.ids | self.repo_names

    @classmethod
    def load(cls, catalog_path) -> "CatalogIndex":
        """Load ids and ``repository`` basenames from a catalog.json.

        Never raises: a missing/broken catalog yields an empty index (which
        errs toward *silence*-safe behavior only where callers also gate on
        :func:`has_manifest`; on its own an empty index treats nothing as
        cataloged).
        """
        ids: set[str] = set()
        repo_names: set[str] = set()
        try:
            data = json.loads(Path(catalog_path).read_text())
        except (OSError, ValueError) as exc:
            log.warning("Could not load catalog %s: %s", catalog_path, exc)
            return cls(ids, repo_names)

        for app in data.get("apps", []):
            app_id = app.get("id")
            if isinstance(app_id, str) and app_id:
                ids.add(app_id)
            base = _repo_basename(app.get("repository", ""))
            if base:
                repo_names.add(base)
        return cls(ids, repo_names)

    def _candidates(self, repo_dir_name: str, manifest_app_id: Optional[str]) -> list[str]:
        cands = [repo_dir_name]
        if repo_dir_name.startswith(_APP_PREFIX):
            cands.append(repo_dir_name[len(_APP_PREFIX):])
        if manifest_app_id:
            cands.append(manifest_app_id)
        return cands

    def resolve(self, repo_dir_name: str,
                manifest_app_id: Optional[str] = None) -> Optional[str]:
        """Return the canonical catalog id a repo maps to, or ``None``.

        Tries, in order: the directory name and (if ``manifest_app_id`` given)
        the manifest app_id against catalog ids; the ``safe-app-`` prefix
        stripped; and the directory name against ``repository`` basenames
        (mapping the basename back to its id).
        """
        for cand in self._candidates(repo_dir_name, manifest_app_id):
            if cand in self.ids:
                return cand
        # Directory name equals a repository basename -> it's that app; the id
        # is the basename minus the safe-app- prefix when present, else itself.
        if repo_dir_name in self.repo_names:
            stripped = repo_dir_name[len(_APP_PREFIX):] if repo_dir_name.startswith(_APP_PREFIX) else repo_dir_name
            return stripped if stripped in self.ids else repo_dir_name
        return None

    def is_cataloged(self, repo_dir_name: str,
                     manifest_app_id: Optional[str] = None) -> bool:
        """True if the repo resolves to a catalog entry."""
        return self.resolve(repo_dir_name, manifest_app_id) is not None
