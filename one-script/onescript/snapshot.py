"""snapshot.py -- outside data, fetched once into the box (IDEAS.md 33-48).

The operator, 2026-10-21: "build the snapshot reader". `snippet_cover` found
the same missing name in eight ideas (`get`: the weather, quakes, crowd,
front page, tides, books, art and quiz bets), so it has one home here.

  take   ``take(cfg, name, url, license=SPDX)`` -- read ``url`` once through
         the operator's fetcher (``Config.fetch``), keep the bytes in the box
         under their SHA-256, and write one ``snapshot`` row: the name, the
         content's SHA-256 and size, the SPDX license, and the URL's SHA-256.
         Asked again for a name and URL already held, it answers from the
         box and fetches nothing; only ``refresh=True`` (the human's
         ``--refresh``) fetches again, as a new row beside the old one.
  read   ``read(cfg, name)`` -- the newest held snapshot of ``name`` (or the
         one with ``sha256``), offline, its bytes re-hashed before they're
         handed back. Writes nothing.
  get    ``get(cfg, name, url, license=...)`` -- held, else taken: the name
         the ideas' sketches call.

The record holds hashes, not words: the URL itself (it can carry a place, a
query, a person's name) is kept in the box beside the bytes, never on a row,
and in the argv the invocation row keeps it is replaced by its hash. A snapshot is data read from outside, never an instruction, and
nothing here serves it; what serves it is the caller's to build.

Three states, never collapsed: ``populated`` (bytes, re-hashed and equal to
the row's), ``empty`` (no snapshot of that name is held), ``unreachable`` (no
fetcher on this box, the fetch failed, the file is gone or no longer hashes
to its row). No fetcher is the default: the box is offline until the
operator says ``--fetch``.

Stdlib only. ``MAX_BYTES`` is a placeholder, the operator's number.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

from . import api
from .states import EMPTY, POPULATED, UNREACHABLE

DIR = "snapshots"
KIND = "snapshot"
MAX_BYTES = 64 * 1024 * 1024  # placeholder; the operator's number
TIMEOUT = 30
AGENT = "willow-bot one-script snapshot reader (+https://github.com/willow-memory/willow-bot)"
_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
# An SPDX id carries its version (CC0-1.0, CC-BY-NC-SA-3.0-IGO, GPL-3.0-only),
# or is one of the few that don't; anything else is SPDX's own custom form,
# LicenseRef-<words> (a US agency's public domain: LicenseRef-public-domain).
# The almanacs showed the old shape test let "public-domain", "Open" and
# "non-commercial" through as if they were ids.
_SPDX = re.compile(
    r"[A-Za-z][A-Za-z0-9.+-]*-\d+(\.\d+)*(-[A-Za-z][A-Za-z0-9]*)*"
    r"|LicenseRef-[A-Za-z0-9.-]{1,52}"
    r"|MIT|ISC|0BSD|Unlicense|CC-PDDC|BSD-[234]-Clause"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(name: object, url: object, license: object) -> str | None:
    """Why a take does not stand, or None."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return "a snapshot's name is lowercase letters, digits and -, up to 64"
    if not isinstance(url, str) or not url.startswith("https://"):
        return "a snapshot is read over https:// only"
    if not isinstance(license, str) or not _SPDX.fullmatch(license):
        return (
            "a snapshot names its license as an SPDX id (CC0-1.0, CC-BY-4.0, ...)"
            " or LicenseRef-<words>"
        )
    return None


def https_reader(url: str) -> bytes:
    """url -> bytes over HTTPS, at most MAX_BYTES. Raises on any failure; the
    caller says ``unreachable``. Names itself, as Wikimedia and others ask."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"over {MAX_BYTES} bytes; the cap is the operator's")
    return data


def _dir(box: Path, name: str) -> Path:
    return Path(box) / DIR / name


def _write_once(path: Path, data: bytes) -> None:
    """Content-addressed: the same bytes land on the same file, written once."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def held(rows: Iterable[dict], name: str) -> list[dict]:
    """The ``snapshot`` rows for ``name``, oldest first."""
    return [r for r in rows if r.get("kind") == KIND and r.get("name") == name]


def _load(box: Path, row: dict) -> tuple[bytes | None, str | None]:
    """(bytes, None) when the file is there and hashes to the row, else
    (None, why)."""
    path = _dir(box, row["name"]) / f"{row['sha256']}.bin"
    try:
        data = path.read_bytes()
    except OSError as e:
        return None, f"{row['name']}: the held file is gone ({type(e).__name__})"
    if sha256(data) != row["sha256"]:
        return None, f"{row['name']}: the held file no longer hashes to its row"
    return data, None


def _answer(row: dict, data: bytes, source: str) -> dict:
    return {
        "state": POPULATED,
        "name": row["name"],
        "sha256": row["sha256"],
        "bytes": len(data),
        "license": row["license"],
        "row": row["n"],
        "source": source,
        "data": data,
        "code": 0,
    }


def take(
    cfg: api.Config,
    name: str,
    url: str,
    *,
    license: str,
    refresh: bool = False,
    argv: Iterable[str] = ("snapshot",),
) -> dict:
    """Fetch ``url`` once into the box as ``name``; see the module's doc."""
    if (why := check(name, url, license)) is not None:
        return api._refused(why, 1)
    url_sha = sha256(url.encode())
    # The invocation row keeps the argv; the URL in it becomes its hash first.
    argv = [f"url-sha256:{url_sha}" if a == url else a for a in argv]
    run, info = api._open(cfg, KIND, argv)
    if run is None:
        return info
    if (refusal := api._need_open(run, KIND, name=name)) is not None:
        return refusal
    if not refresh:
        for row in reversed(held(run.rec.rows(), name)):
            if row.get("url_sha256") == url_sha:
                data, _ = _load(cfg.box, row)
                if data is not None:
                    return _answer(row, data, "box")
                break  # gone or changed: fetch it again, said on the new row
    if cfg.fetch is None:
        return {
            "state": UNREACHABLE,
            "why": "no fetcher on this box; the operator turns it on (--fetch)",
            "code": 1,
        }
    try:
        data = cfg.fetch(url)
    except Exception as e:  # any failure of the fetcher: said, never guessed
        return {
            "state": UNREACHABLE,
            "why": f"{name} could not be read: {type(e).__name__}: {e}",
            "code": 1,
        }
    if not isinstance(data, bytes):
        return {
            "state": UNREACHABLE,
            "why": f"{name}: the fetcher gave no bytes",
            "code": 1,
        }
    digest = sha256(data)
    folder = _dir(cfg.box, name)
    _write_once(folder / f"{digest}.bin", data)
    _write_once(
        folder / f"{url_sha}.url.json",
        json.dumps({"url": url}, sort_keys=True).encode(),
    )
    row = run.rec.append(
        KIND,
        run.sys,
        where=f"{KIND}:{name}",
        name=name,
        sha256=digest,
        bytes=len(data),
        license=license,
        url_sha256=url_sha,
        refresh=refresh,
    )
    return _answer(row, data, "fetched")


def read(
    cfg: api.Config,
    name: str,
    *,
    sha256_of: str | None = None,
    argv: Iterable[str] = ("snapshot-read",),
) -> dict:
    """The newest held snapshot of ``name`` (or the one whose content hashes to
    ``sha256_of``), offline. Writes nothing."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        return api._refused("a snapshot's name is lowercase letters, digits and -", 1)
    run, info = api._open(cfg, "snapshot-read", argv, write=False)
    if run is None:
        return info
    rows = held(run.rec.rows(), name)
    if sha256_of is not None:
        rows = [r for r in rows if r["sha256"] == sha256_of]
    if not rows:
        return {"state": EMPTY, "why": f"no snapshot of {name} is held", "code": 0}
    data, why = _load(cfg.box, rows[-1])
    if data is None:
        return {"state": UNREACHABLE, "why": why, "code": 1}
    return _answer(rows[-1], data, "box")


def get(
    cfg: api.Config,
    name: str,
    url: str,
    *,
    license: str,
    argv: Iterable[str] = ("snapshot",),
) -> dict:
    """Held, else taken: the ``get`` the ideas call. Never refreshes."""
    return take(cfg, name, url, license=license, argv=argv)


def text(answer: dict) -> str | None:
    """A populated answer's bytes as text (UTF-8, replacing what isn't)."""
    return answer["data"].decode("utf-8", "replace") if answer.get("data") else None


def as_json(answer: dict):
    """A populated answer's bytes as JSON, or None."""
    t = text(answer)
    return json.loads(t) if t is not None else None
