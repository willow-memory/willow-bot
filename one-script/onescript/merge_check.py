"""merge_check.py -- the one script's merge gate (flatten-2).

The operator, 2026-10-21: "What I am doing is seeding things that could be build
off of a very very basic one script. These are the tests that a chunk of code
can be merged into it cleanly, and the one script will still do the job the user
(me in this case) is asking it to do." Then: "make it executable, a merge_check
like promote_check."

The model is safe-app-store's ``stores/promote_check.py`` (read, not changed):
a fail-closed gate over an attestation file, mechanical [M] gates against
attested [A] ones, ``--json``, and ``--record`` that writes a mark only when every
gate passes and ``verified_by != author`` (Constitution 0.2: proposing and
ratifying never rest in the same hand). promote_check asks "can this chunk leave
as its own thing"; this asks the inverse: can this chunk come in and leave the
kernel intact.

    python -m onescript merge-check <merge.json> [--json] [--record] [--box BOX]
        [--repos DIR] [--skip-suite] [--python REPO=PATH]

``merge.json`` is one chunk's attestation (keys below). Eight gates, all
mechanical unless a ``why_not_fixture`` is attested (gate 7, then [A]). Anything
the gate cannot verify FAILS; nothing is skipped. A repo the attestation names
that is not beside the others FAILS the gate that needs it.

  1 the job still runs       the one-script suite, ``foundation/run_prompt_tests.py``,
                             then checkin -> lookup -> prompt -> checkout in a fresh
                             tmp box with tmp keys (``--no-tests`` only inside that
                             nested run, said in the result)
  2 no persisted byte moves  ``goldens`` exist, hold a test, run green and unskipped;
                             with ``--box``, verify_chain and verify_anchor are empty
  3 callers see no change    every named copy's parity test, and every test in the
                             copy's repo that imports the copy's module, run green
  4 one copy or a named copy an ``import`` copy reaches the kernel through an
                             ``onescript_*`` reach and defines no local version of the
                             rule; a ``named-copy`` has a parity test that exists and a
                             ``why``
  5 absence is said          each ``absent`` test exists, says ``unreachable``, runs
                             green; one per repo that has an ``import`` copy
  6 boundaries hold          ``never_reach``: ``willow-mcp:trust`` (AST walk, lazy
                             imports included, from willow-mcp's own closure-test
                             seeds), ``safe-app-store`` (no diff in the chunk's copies
                             against its default branch), ``willow-bot:wheel`` (the
                             ``willow_bot`` package imports nothing of the one script)
  7 same input, same output  the hidden-inputs fixture, byte-identical under two
                             PYTHONHASHSEEDs and LC_ALL=C, and its source names ``home``
  8 the human seals          ``verified_by`` set and != ``author``; ``home`` writes no
                             sealed standing, no human seal row, calls no seal verb

Gate 0, ``divergence`` (new, beside the eight, which are untouched): when the attestation
carries ``base_ref`` AND ``chunk_ref`` (optional; ``diverge_repos`` names the repos, default
``willow-bot``), ``onescript.diverge`` runs between them (levels 0-3) and gate 0 records the
set it found: its state, its sha-256 and its counts. It scopes nothing yet. It fails closed
when diverge is ``unreachable`` (a ref, a repo or a merge base missing) or when only one
of the two fields is given. An existing candidate with neither field says ``not attested``
and does not fail: the gate is new. It runs ahead of the eight and is kept apart from
``GATE_NAMES``/``check()``; ``run_cli`` puts it first and a record carries it as
``divergence``.

``--skip-suite`` exists for a quick look: gate 1 then FAILS as ``not run``, in a
dry run and in ``--record`` alike, so a skipped gate can never mint a record.

A named copy may carry ``"parity_repo"``: the repo its parity test lives in when it is not
the copy's own (a stdlib-only device app held from the kernel's side); the test must still
exist there and run green and unskipped.

A copy may carry ``"via": "grove/seat_boxes.py:one_script_module"`` (``path:function``
in the copy's own repo): it reaches the kernel through that repo-local helper, not
through a reach import of its own. Gate 4 then verifies the chain by AST, and every
link must hold or the copy FAILS: the helper file defines the function at module
level; the function itself reaches through an ``onescript_*`` reach (it uses a name
the file bound from one); and the copy file imports that function from that file
(or is that file). The names bound from the helper count as the reach in the "no
local version" test, as ``onescript_reach`` names do. This widens what the gate can
verify, never what it lets pass.

Known limits, said once: gate 4's "no local version" is by name (the public
top-level defs of ``home``, plus ``rule_names``); a def that delegates into the
reach (or, inside the kernel, the home module) is a wrapper, not a copy; a def that
only looks alike is named in ``lookalikes`` (repo, path, name, and a ``why``);
gate 6's walk follows literal ``import_module``
names only (the closure test it takes its seeds from does the same).

Tests run as an unprivileged user (the uid is a hidden input). When the process is
root (``os.geteuid() == 0``), every test run (the one-script suite, the prompt tests,
the nested checkin run, the goldens, a named copy's parity test, a repo's own tests,
the absent tests, the fixture) is run through ``setpriv --reuid --regid
--clear-groups`` (else ``runuser -u``) as ``nobody`` (``$MERGE_CHECK_RUN_AS`` names
another), in a throwaway copy: a plain ``mkdtemp`` under ``/tmp`` (traversable;
``/tmp/claude-0/...`` is 0700 root), holding a copy of each repo the run touches
(its worktree, without ``.git``), a copy of the interpreter's venv, a ``HOME``, all
chowned to that user, and ``PYTHONDONTWRITEBYTECODE=1``. Each gate result says the uid
its tests ran as. If privileges cannot be dropped (no such user, neither launcher, a
copy that fails), the gate FAILS with that reason; it never runs as root instead. The
temp dir is removed in a ``finally``. Not root: unchanged, run in place. Gate 2's
``--box`` check and the git calls read data and run no tests, so they stay in place.

Stdlib only; the suite and the other repos' tests run in subprocesses.
"""

from __future__ import annotations

import ast
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent
ONE_SCRIPT = PKG.parent  # onescript -> one-script

Result = tuple[str, bool, str]  # (gate, ok, detail)

_CHUNK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KINDS = ("import", "named-copy")
_UNSET = {"", "unset", "none", "null", "tbd", "todo"}
GATE_NAMES = [
    "1 job_runs [M]",
    "2 no_byte_moves [M]",
    "3 callers_unchanged [M]",
    "4 one_copy_or_named [M]",
    "5 absence_said [M]",
    "6 boundaries [M]",
    "7 same_in_same_out",
    "8 human_seals [M]",
]
GATE0_NAME = "0 divergence [M]"
TIMEOUT = 3600

# willow-mcp's trust seeds if its own closure test can't be read (the brief's list).
BRIEF_TRUST_SEEDS = [
    "cli_manifest_grant",
    "envelope_authoring",
    "envelopes",
    "governance_ledger",
    "human_session",
    "keyring",
    "manifest_grant_executor",
    "mcp_federation",
    "paths",
    "pgp",
    "trust_identity",
    "trust_marker",
    "trust_owner_verbs",
    "session_signing",
]
TRUST_TEST = "tests/test_trust_closure_no_onescript.py"
SEAL_VERBS = {
    "seal_scope",
    "seal_session",
    "seal_proposal",
    "writeback_seal",
    "seal",
}


# -- running as an unprivileged user ---------------------------------------------


def _euid() -> int:
    return os.geteuid()


class JailError(Exception):
    """Privileges cannot be dropped, so the tests must not run."""


_JAIL_PARENT = "/tmp"  # plain /tmp: $TMPDIR may be a 0700 root directory
_JAIL_PREFIX = "merge-check-nonroot-"
_NOT_COPIED = shutil.ignore_patterns(
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"
)


def jail_command(launcher: list[str], cmd: list[str]) -> list[str]:
    """The command line that runs ``cmd`` through the launcher."""
    return [*launcher, *cmd]


def find_launcher(user: str, uid: int, gid: int, which=None) -> list[str]:
    """``setpriv --reuid --regid --clear-groups --``, else ``runuser -u USER --``.
    Raises ``JailError`` when neither is there."""
    which = which or shutil.which
    sp = which("setpriv")
    if sp:
        return [sp, f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--"]
    ru = which("runuser")
    if ru:
        return [ru, "-u", user, "--"]
    raise JailError("neither setpriv nor runuser is installed to drop privileges")


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    os.chown(path, uid, gid, follow_symlinks=False)
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for n in dirnames + filenames:
            os.chown(os.path.join(dirpath, n), uid, gid, follow_symlinks=False)


class Jail:
    """A throwaway tree an unprivileged user can run the tests in."""

    def __init__(self, root: Path, user: str, uid: int, gid: int, launcher: list[str]):
        self.root = root
        self.user, self.uid, self.gid = user, uid, gid
        self.launcher = launcher
        self.maps: list[tuple[str, str]] = []
        self.copied: set[str] = set()
        self.home = root / "home"
        self.repos = root / "repos"

    @classmethod
    def make(cls, pl: "Places") -> "Jail":
        user = os.environ.get("MERGE_CHECK_RUN_AS", "nobody")
        try:
            pw = pwd.getpwnam(user)
        except KeyError:
            raise JailError(f"no user {user!r} to run the tests as") from None
        if pw.pw_uid == 0:
            raise JailError(f"{user!r} is root, not an unprivileged user")
        launcher = find_launcher(user, pw.pw_uid, pw.pw_gid)
        try:
            root = Path(tempfile.mkdtemp(prefix=_JAIL_PREFIX, dir=_JAIL_PARENT))
        except OSError as e:
            raise JailError(f"cannot make a temp dir under {_JAIL_PARENT}: {e}") from e
        jail = cls(root, user, pw.pw_uid, pw.pw_gid, launcher)
        try:
            os.chmod(root, 0o755)  # traversable by the user
            for d in (jail.home, jail.repos, jail.home / "tmp"):
                d.mkdir(parents=True, exist_ok=True)
            _chown_tree(jail.home, pw.pw_uid, pw.pw_gid)
            # siblings the tests may look for beside the repos: read-only links
            # (a repo the run touches is copied over its link when first needed)
            if pl.repos.is_dir():
                for sib in sorted(pl.repos.iterdir()):
                    if sib.is_dir():
                        (jail.repos / sib.name).symlink_to(sib.resolve())
        except Exception as e:  # leave nothing behind
            jail.close()
            raise JailError(f"cannot prepare the unprivileged copy: {e}") from e
        return jail

    # -- copying

    def _add_map(self, src: str, dst: str) -> None:
        self.maps.append((src, dst))
        self.maps.sort(key=lambda m: -len(m[0]))

    def ensure_repo(self, pl: "Places", name: str) -> None:
        if name in self.copied:
            return
        src = pl.repos / name
        if not src.is_dir():
            return
        dst = self.repos / name
        try:
            if dst.is_symlink():
                dst.unlink()
            shutil.copytree(src.resolve(), dst, symlinks=True, ignore=_NOT_COPIED)
            _chown_tree(dst, self.uid, self.gid)
        except (OSError, shutil.Error) as e:
            raise JailError(f"cannot copy {name} for the unprivileged run: {e}") from e
        self.copied.add(name)
        self._add_map(str(pl.repos / name), str(dst))
        resolved = str(src.resolve())
        if resolved != str(pl.repos / name):
            self._add_map(resolved, str(dst))

    def mapped(self, text: str) -> bool:
        return any(
            re.match(re.escape(src) + r"(?=$|[/:\s])", text) for src, _dst in self.maps
        )

    def ensure_tree(self, path: Path) -> None:
        """Copy a directory that is not one of the repos (diverge's throwaway
        worktrees) so the user can run in it."""
        real = path.resolve()
        if str(real) in ("/", "/tmp", "/home", "/root") or not real.is_dir():
            raise JailError(f"will not copy {path} for the unprivileged run")
        dst = self.root / "trees" / f"{len(self.copied)}-{real.name}"
        try:
            dst.parent.mkdir(exist_ok=True)
            shutil.copytree(real, dst, symlinks=True, ignore=_NOT_COPIED)
            _chown_tree(dst, self.uid, self.gid)
        except (OSError, shutil.Error) as e:
            raise JailError(f"cannot copy {path} for the unprivileged run: {e}") from e
        self.copied.add("tree:" + str(real))
        self._add_map(str(path), str(dst))
        if str(real) != str(path):
            self._add_map(str(real), str(dst))

    def venv_of(self, py: str) -> Path | None:
        """The venv a python lives in (the dir holding ``pyvenv.cfg``), or None
        for a system python the user can run as it is."""
        if not py:
            return None
        p = Path(py)
        if not p.is_absolute():
            found = shutil.which(py)
            if not found:
                return None
            p = Path(found)
        cfg = p.parent.parent / "pyvenv.cfg"
        return cfg.parent if cfg.is_file() else None

    def ensure_venv(self, venv: Path) -> None:
        key = "venv:" + str(venv)
        if key in self.copied:
            return
        dst = self.root / "venvs" / f"{len(self.copied)}-{venv.name}"
        try:
            dst.parent.mkdir(exist_ok=True)
            shutil.copytree(venv, dst, symlinks=True)
            _chown_tree(dst, self.uid, self.gid)
        except (OSError, shutil.Error) as e:
            raise JailError(
                f"cannot copy the venv {venv} for the unprivileged run: {e}"
            ) from e
        self.copied.add(key)
        self._add_map(str(venv), str(dst))

    def translate(self, text: str) -> str:
        for src, dst in self.maps:
            text = re.sub(re.escape(src) + r"(?=$|[/:\s])", lambda _m, d=dst: d, text)
        return text

    def scratch(self):
        import contextlib

        @contextlib.contextmanager
        def made():
            d = Path(tempfile.mkdtemp(prefix="scratch-", dir=self.home / "tmp"))
            try:
                _chown_tree(d, self.uid, self.gid)
                yield str(d)
            finally:
                shutil.rmtree(d, ignore_errors=True)

        return made()

    # -- running

    def run(self, pl: "Places", cmd, cwd, env, stdin, timeout) -> tuple[int, str]:
        """Run as the user, in the copy. Every repo and venv the command, the cwd
        or the environment names is copied first and its path translated."""
        texts = (
            [str(c) for c in cmd] + [str(cwd or "")] + [str(v) for v in env.values()]
        )
        blob = "\n".join(texts)
        names = (
            [
                n.name
                for n in sorted(pl.repos.iterdir())
                if n.is_dir()
                and re.search(re.escape(str(pl.repos / n.name)) + r"(?=$|[/:\s])", blob)
            ]
            if pl.repos.is_dir()
            else []
        )
        for name in names:
            self.ensure_repo(pl, name)
        if cwd and not self.mapped(str(cwd)):
            self.ensure_tree(Path(cwd))
        for py in {str(cmd[0]), sys.executable, sys.prefix + "/bin/python"}:
            v = self.venv_of(py)
            if v is not None and (py == str(cmd[0]) or str(v) in blob):
                self.ensure_venv(v)
        new_env = {k: self.translate(str(v)) for k, v in env.items()}
        new_env.update(
            HOME=str(self.home),
            TMPDIR=str(self.home / "tmp"),
            PYTHONDONTWRITEBYTECODE="1",
            USER=self.user,
            LOGNAME=self.user,
        )
        new_cmd = [self.translate(str(c)) for c in cmd]
        new_cwd = self.translate(str(cwd)) if cwd else str(self.home)
        if not Path(new_cwd).is_dir():
            raise JailError(
                f"the working directory {new_cwd} is not in the unprivileged copy"
            )
        return _run(
            jail_command(self.launcher, new_cmd),
            new_cwd,
            new_env,
            stdin=stdin,
            timeout=timeout,
        )

    def close(self) -> None:
        # only what this run made: a path we created under the temp parent
        if (
            self.root.name.startswith(_JAIL_PREFIX)
            and str(self.root.parent) == _JAIL_PARENT
        ):
            shutil.rmtree(self.root, ignore_errors=True)


# -- places ------------------------------------------------------------------


class Places:
    """Where the repos are and which one script is being checked."""

    def __init__(self, repos: Path, kernel: Path, python: dict[str, str] | None = None):
        self.repos = Path(repos)
        self.kernel = Path(kernel)
        self.python = dict(python or {})
        self.jail: Jail | None = None
        self.jail_error: str | None = None

    def run_tests(
        self, cmd, cwd, env, stdin: str | None = None, timeout: int = TIMEOUT
    ):
        """(rc, output, uid): run test code. Not root: in place, as ourselves. Root:
        only as an unprivileged user in a throwaway copy, else rc -1 and the reason
        (fail-closed: never as root)."""
        if _euid() != 0:
            rc, out = _run(cmd, cwd, env, stdin=stdin, timeout=timeout)
            return rc, out, _euid()
        if self._ensure_jail() is not None:
            return (
                -1,
                f"cannot drop privileges, so the tests did not run: {self.jail_error}",
                0,
            )
        try:
            rc, out = self.jail.run(self, cmd, cwd, env, stdin, timeout)
        except JailError as e:
            return -1, f"cannot drop privileges, so the tests did not run: {e}", 0
        return rc, out, self.jail.uid

    def _ensure_jail(self) -> str | None:
        """None when tests can run unprivileged, else why not."""
        if self.jail is None and self.jail_error is None:
            try:
                self.jail = Jail.make(self)
            except JailError as e:
                self.jail_error = str(e)
        return None if self.jail is not None else self.jail_error

    def scratch(self):
        """A scratch dir the user the tests run as can write (a context manager
        giving its path). Raises ``JailError`` when root cannot drop privileges."""
        if _euid() == 0:
            why = self._ensure_jail()
            if why is not None:
                raise JailError(why)
            return self.jail.scratch()
        return tempfile.TemporaryDirectory(prefix="merge-check-")

    def close(self) -> None:
        """Remove what this run created (the unprivileged copy), always."""
        jail, self.jail = self.jail, None
        if jail is not None:
            jail.close()

    def repo(self, name: str) -> Path | None:
        """A repo beside the others, or None. Never a guess."""
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return None
        p = self.repos / name
        return p if p.is_dir() else None

    def py(self, repo: str) -> str:
        return (
            self.python.get(repo)
            or os.environ.get("MERGE_CHECK_PYTHON_" + re.sub(r"\W", "_", repo).upper())
            or sys.executable
        )

    def env(self, extra: dict | None = None, repo: Path | None = None) -> dict:
        """The child environment: the kernel and the law pinned, nothing else of
        ours leaking a caller's ONESCRIPT_* in. No bytecode, no cache written
        into the trees being checked."""
        e = dict(os.environ)
        e["ONESCRIPT_HOME"] = str(self.kernel)
        grove = self.repos / "willows-grove"
        e["ONESCRIPT_GROVE"] = str(grove)
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        e.pop("PYTHONHASHSEED", None)
        paths = []
        if repo is not None:
            src = repo / "src"
            paths += [str(src)] if src.is_dir() else []
            paths.append(str(repo))
        else:
            paths.append(str(self.kernel))
        # never the caller's PYTHONPATH: a checked repo must find the one script
        # the attestation names (the reach), not whichever copy the caller has
        e["PYTHONPATH"] = os.pathsep.join(paths)
        e.update(extra or {})
        return e


def default_places(repos=None, python=None, environ=None) -> Places:
    """``--repos`` names a directory holding willow-bot; with none, the one
    script is ``$ONESCRIPT_HOME`` or this file's own, and the repos are beside
    its willow-bot (how ``reach`` finds siblings)."""
    environ = os.environ if environ is None else environ
    if repos:
        repos = Path(repos).expanduser().resolve()
        return Places(repos, repos / "willow-bot" / "one-script", python)
    home = environ.get("ONESCRIPT_HOME")
    kernel = Path(home).expanduser().resolve() if home else ONE_SCRIPT
    return Places(kernel.parent.parent, kernel, python)


# -- the attestation ----------------------------------------------------------


def _load(path: Path) -> dict | None:
    try:
        att = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as e:  # malformed is no attestation
        return {"_error": str(e)}
    return att if isinstance(att, dict) else {"_error": "not a JSON object"}


def _shape(att: dict) -> str | None:
    """Why the attestation can't be read as one, or None."""
    for k in ("chunk", "author", "home", "goldens"):
        if not isinstance(att.get(k), str) or not att[k].strip():
            return f"{k!r} missing or not a string"
    if not _CHUNK.match(att["chunk"]):
        return f"chunk {att['chunk']!r} is not a plain identifier"
    if not isinstance(att.get("verified_by", ""), str):
        return "'verified_by' is not a string"
    for k in ("copies", "absent", "never_reach"):
        if not isinstance(att.get(k), list):
            return f"{k!r} missing or not a list"
    for c in att["copies"]:
        if not isinstance(c, dict) or not all(
            isinstance(c.get(k), str) and c[k] for k in ("repo", "path", "kind")
        ):
            return f"a copy needs repo, path and kind: {c!r}"
    for c in att["copies"]:
        if "parity_repo" in c and not (
            isinstance(c["parity_repo"], str) and c["parity_repo"]
        ):
            return f"a copy's 'parity_repo' must be a repo name: {c!r}"
        if "via" in c and not (isinstance(c["via"], str) and c["via"]):
            return f"a copy's 'via' must be 'path:function': {c!r}"
    for a in att["absent"]:
        if not isinstance(a, dict) or not all(
            isinstance(a.get(k), str) and a[k] for k in ("repo", "test")
        ):
            return f"an absent entry needs repo and test: {a!r}"
    if not all(isinstance(n, str) for n in att["never_reach"]):
        return "never_reach holds a non-string"
    if not isinstance(att.get("fixture"), bool):
        return "'fixture' must be true or false"
    return None


def _within(base: Path, rel: str) -> Path | None:
    """``rel`` inside ``base``, or None (absolute, or climbing out)."""
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        return None
    p = (base / rel).resolve()
    try:
        p.relative_to(base.resolve())
    except ValueError:
        return None
    return p


# -- small AST and process helpers ---------------------------------------------


def _parse(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _run(cmd, cwd, env, stdin: str | None = None, timeout: int = TIMEOUT):
    """(rc, output); a timeout or a launch failure is rc -1, said."""
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {timeout}s"
    except OSError as e:
        return -1, f"could not run: {e}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)")


def _counts(out: str) -> dict:
    last = ""
    for line in out.strip().splitlines():
        if _COUNT.search(line):
            last = line
    c: dict = {}
    for n, word in _COUNT.findall(last):
        c[word.rstrip("s") if word.startswith("error") else word] = int(n)
    return c


def _pytest(pl, py, cwd, env, targets, strict_skip: bool) -> tuple[bool, str]:
    """Green means: rc 0, something passed, and (strict) nothing skipped. A
    skipped parity or absent test is not a proof. Run as an unprivileged user when
    we are root (``Places.run_tests``); the detail says the uid it ran as."""
    cmd = [py, "-m", "pytest", "-q", "-rsfE", "-p", "no:cacheprovider", *targets]
    rc, out, uid = pl.run_tests(cmd, cwd, env)
    if rc == -1 and uid == 0 and "cannot drop privileges" in out:
        return False, out
    c = _counts(out)
    shown = ", ".join(f"{v} {k}" for k, v in c.items()) or "no summary"
    shown += f"; as uid {uid}"
    if rc != 0:
        lines = out.strip().splitlines()
        bad = [x for x in lines if x.startswith(("FAILED ", "ERROR "))]
        tail = " | ".join(bad[:5] if bad else lines[-3:])
        return False, f"pytest exit {rc} ({shown}): {tail[:600]}"
    if not c.get("passed"):
        return False, f"nothing passed ({shown})"
    if strict_skip and c.get("skipped"):
        return False, f"a test skipped, which proves nothing ({shown})"
    return True, shown


def _test_defs(tree) -> set[str]:
    """``test_x`` and ``Class::test_x`` names a file defines."""
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(node.name)
        elif isinstance(node, ast.ClassDef):
            out.add(node.name)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.add(f"{node.name}::{sub.name}")
    return out


def _test_node(repo: Path, nodeid: str):
    """(file, name, FunctionDef-or-None) for ``path::test`` / ``path::Class::test``
    inside ``repo``; (None, why, None) if it can't be found."""
    path, _, name = nodeid.partition("::")
    f = _within(repo, path)
    if f is None or not f.is_file():
        return None, f"{path} is not a file in {repo.name}", None
    if not name:
        return None, f"{nodeid!r} names no test", None
    try:
        tree = _parse(f)
    except SyntaxError as e:
        return None, f"{path} does not parse: {e}", None
    if name not in _test_defs(tree):
        return None, f"{path} defines no {name}", None
    last = name.split("::")[-1]
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == last
        ),
        None,
    )
    return f, name, fn


def _import_names(tree) -> list[str]:
    """Every dotted name an import statement can bring in, any nesting; relative
    ones keep their dots (``.canon``, ``..x``), as do the literal names handed to
    ``import_module`` / ``__import__``."""
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            out.append(prefix)
            sep = "" if prefix.endswith(".") or not prefix else "."
            out += [f"{prefix}{sep}{a.name}" for a in node.names if a.name != "*"]
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(arg.value)
    return out


_REACH = re.compile(r"(^|\.)onescript_\w*", re.I)
_ANYONE = re.compile(r"(^|\.)onescript", re.I)


def _is_reach(name: str) -> bool:
    return bool(_REACH.search(name.lstrip(".")))


def _repo_missing(name: str) -> str:
    return f"repo {name!r} is not beside the others (never skipped)"


# -- gate 1: the job still runs -----------------------------------------------


def _gate1(att, pl: Places, skip_suite: bool) -> Result:
    g = GATE_NAMES[0]
    if skip_suite:
        return g, False, "not run (--skip-suite): the job is unverified, which fails"
    k = pl.kernel
    if not (k / "onescript" / "__init__.py").is_file():
        return g, False, f"no one script at {k}"
    env = pl.env()
    ok, d = _pytest(pl, sys.executable, k, env, ["onescript/tests"], strict_skip=False)
    if not ok:
        return g, False, f"one-script suite: {d}"
    suite = d
    rp = k / "foundation" / "run_prompt_tests.py"
    if not rp.is_file():
        return g, False, "foundation/run_prompt_tests.py is missing"
    rc, out, uid = pl.run_tests([sys.executable, str(rp)], k, env)
    if rc != 0:
        return g, False, f"run_prompt_tests exit {rc}: {out.strip()[-200:]}"
    prompt = (
        out.strip().splitlines()[-1] if out.strip() else "ok"
    ) + f" (as uid {uid})"
    ok, d = _e2e(pl, env)
    if not ok:
        return g, False, d
    return g, True, f"suite {suite}; prompt tests {prompt}; {d}"


def _e2e(pl: Places, env: dict) -> tuple[bool, str]:
    """checkin -> lookup -> prompt -> checkout in a fresh tmp box, tmp keys.
    ``--no-tests`` is for this nested run only: the suite ran just above."""
    k = pl.kernel
    try:
        scratch = pl.scratch()
    except JailError as e:
        return False, f"cannot drop privileges, so the nested run did not run: {e}"
    uid = 0
    with scratch as t:
        base = [
            sys.executable,
            "-m",
            "onescript",
            "--box",
            f"{t}/box",
            "--keys",
            f"{t}/keys/keys.json",
            "--venv",
            sys.prefix,
            "--no-tests",
        ]
        steps = [
            ("checkin", ["checkin"], None),
            ("lookup", ["lookup", "the merge check bite"], None),
            ("prompt", ["prompt"], json.dumps({"prompt": "the merge check bite"})),
            ("checkout", ["checkout"], None),
        ]
        for name, argv, stdin in steps:
            rc, out, uid = pl.run_tests(base + argv, k, env, stdin=stdin)
            if rc != 0:
                return False, f"{name} exit {rc}: {out.strip()[-200:]}"
            if "BOX WON'T OPEN" in out or "\nrefused:" in "\n" + out:
                return False, f"{name} refused: {out.strip()[:200]}"
            if name in ("lookup", "prompt"):
                try:
                    j = json.loads(out)
                except ValueError:
                    return False, f"{name} did not answer in JSON: {out[:120]}"
                if name == "lookup" and "hits" not in j:
                    return False, "lookup answered with no 'hits'"
                if name == "prompt" and "hookSpecificOutput" not in j:
                    return False, "prompt answered with no hookSpecificOutput"
    return (
        True,
        f"checkin, lookup, prompt, checkout ran in a fresh box (--no-tests, nested; as uid {uid})",
    )


# -- gate 2: no persisted byte moves ------------------------------------------


_BOX_CHECK = (
    "import json,sys\n"
    "from pathlib import Path\n"
    "from onescript import record\n"
    "box=Path(sys.argv[1]); anchor=Path(sys.argv[2]) if sys.argv[2] else None\n"
    "r=record.Record(box, lambda: '', '', object(), anchor=anchor)\n"
    "print(json.dumps({'chain': r.verify_chain(), 'anchor': r.verify_anchor()}))\n"
)


def _gate2(att, pl: Places, box: Path | None, box_anchor: Path | None) -> Result:
    g = GATE_NAMES[1]
    k = pl.kernel
    f = _within(k, att["goldens"])
    if f is None or not f.is_file():
        return g, False, f"goldens {att['goldens']!r} is not a file in the one script"
    try:
        n = sum(
            1 for x in _test_defs(_parse(f)) if x.split("::")[-1].startswith("test")
        )
    except SyntaxError as e:
        return g, False, f"goldens do not parse: {e}"
    if n < 1:
        return g, False, "goldens hold no test"
    ok, d = _pytest(
        pl,
        sys.executable,
        k,
        pl.env(),
        [str(f.relative_to(k.resolve()))],
        strict_skip=True,
    )
    if not ok:
        return g, False, f"goldens: {d}"
    msg = f"goldens green ({d})"
    if box is None:
        return g, True, msg + "; no --box given, no record checked"
    box = Path(box)
    if not (box / "record.jsonl").is_file():
        return g, False, f"{msg}; --box {box} holds no record.jsonl"
    anchor = box_anchor or box.parent / "onescript-keys" / "anchor.json"
    rc, out = _run(
        [
            sys.executable,
            "-c",
            _BOX_CHECK,
            str(box),
            str(anchor) if anchor.exists() else "",
        ],
        k,
        pl.env(),
    )
    if rc != 0:
        return g, False, f"{msg}; box check exit {rc}: {out.strip()[-200:]}"
    try:
        res = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return g, False, f"{msg}; box check unreadable: {out[:120]}"
    breaks = res["chain"] + res["anchor"]
    if breaks:
        return g, False, f"{msg}; the box's record breaks: {breaks[:3]}"
    return g, True, msg + "; verify_chain and verify_anchor on the box are empty"


# -- gates 3, 4, 5: the copies ----------------------------------------------


def _copy_file(pl: Places, c: dict):
    """(repo_path, file, why-not)."""
    repo = pl.repo(c["repo"])
    if repo is None:
        return None, None, _repo_missing(c["repo"])
    f = _within(repo, c["path"])
    if f is None or not f.is_file():
        return repo, None, f"{c['repo']}:{c['path']} is not a file"
    return repo, f, None


def _tests_importing(repo: Path, stem: str) -> list[Path]:
    """Every test file in the repo that imports a module named ``stem``."""
    out = []
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages"}
    for p in sorted(repo.rglob("test_*.py")):
        parts = set(p.relative_to(repo).parts)
        # a test the repo's CI runs lives in a tests/ directory; an experiment
        # or a scratch test beside code is not a caller's proof
        if skip & parts or not ({"tests", "test"} & parts):
            continue
        try:
            names = _import_names(_parse(p))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue
        if any(stem in n.lstrip(".").split(".") for n in names):
            out.append(p)
    return out


def _parity_home(pl: Places, c: dict):
    """(repo name, repo path or None) of the repo a named copy's parity test lives
    in: the copy's own, or the ``parity_repo`` it names (a copy that cannot carry
    a test of its own, e.g. a stdlib-only device app held from the kernel's side).
    The test must still exist and run green there."""
    name = c.get("parity_repo") or c["repo"]
    return name, pl.repo(name) if isinstance(name, str) else None


def _gate3(att, pl: Places) -> Result:
    g = GATE_NAMES[2]
    if not att["copies"]:
        return g, False, "no copies listed: the callers are unnamed, so unverified"
    by_repo: dict[str, set[str]] = {}
    why = []
    for c in att["copies"]:
        repo, f, bad = _copy_file(pl, c)
        if bad:
            why.append(bad)
            continue
        targets = set()
        if c["kind"] == "named-copy":
            if not c.get("parity"):
                why.append(
                    f"{c['repo']}:{c['path']} is a named copy with no parity test"
                )
                continue
            pname, prepo = _parity_home(pl, c)
            if prepo is None:
                why.append(f"parity test: {_repo_missing(str(pname))}")
                continue
            tf, name, _ = _test_node(prepo, c["parity"])
            if tf is None:
                why.append(f"{pname}: parity test: {name}")
                continue
            by_repo.setdefault(pname, set()).add(c["parity"])
        stem = f.stem if f.stem != "__init__" else f.parent.name
        found = _tests_importing(repo, stem)
        if not found and c["kind"] == "import":
            why.append(f"{c['repo']}:{c['path']}: no test in the repo imports {stem}")
        targets.update(os.path.relpath(p, repo) for p in found)
        if targets:
            by_repo.setdefault(c["repo"], set()).update(targets)
        elif c["repo"] not in by_repo and not c.get("parity_repo"):
            by_repo.setdefault(c["repo"], set())
    if why:
        return g, False, "; ".join(why)
    ran = []
    for name, targets in sorted(by_repo.items()):
        repo = pl.repo(name)
        if not targets:
            why.append(f"{name}: nothing to run")
            continue
        parity = sorted(t for t in targets if "::" in t)
        files = sorted(t for t in targets if "::" not in t)
        ok, d = (
            _pytest(pl, pl.py(name), repo, pl.env(repo=repo), parity, strict_skip=True)
            if parity
            else (True, "")
        )
        if not ok:
            why.append(f"{name} parity: {d}")
            continue
        if files:
            ok, d2 = _pytest(
                pl, pl.py(name), repo, pl.env(repo=repo), files, strict_skip=False
            )
            if not ok:
                why.append(f"{name} tests: {d2}")
                continue
            d = f"{d}; {d2}" if d else d2
        ran.append(f"{name}: {d}")
    if why:
        return g, False, "; ".join(why)
    return g, True, "; ".join(ran)


def _home_files(pl: Places, att) -> list[Path]:
    h = _within(pl.kernel, att["home"])
    if h is None or not h.exists():
        return []
    return [h] if h.is_file() else sorted(h.rglob("*.py"))


def _public_defs(path: Path) -> set[str]:
    try:
        tree = _parse(path)
    except SyntaxError:
        return set()
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not n.name.startswith("_")
        and not n.name.startswith("test")
        and n.name != "main"
    }


def _toplevel_defs(tree):
    """Defs at module level, including under top-level if/try (not methods)."""
    todo = list(tree.body)
    while todo:
        n = todo.pop(0)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield n
        elif isinstance(n, ast.If):
            todo += n.body + n.orelse
        elif isinstance(n, ast.Try):
            todo += (
                n.body
                + n.orelse
                + n.finalbody
                + [x for h in n.handlers for x in h.body]
            )


def _names_in(node) -> set[str]:
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}


def _reach_names(
    tree, home_stem: str | None = None, seeds: frozenset | set = frozenset()
) -> set[str]:
    """The names a file binds from a reach (``import onescript_reach``,
    ``from . import onescript_canon as c``) or, inside the kernel, from the home
    module (``from .words import words as _words``), and what it assigns from
    them. A def that calls through one of these is a wrapper, not a copy.
    ``seeds`` are names already known to reach (a verified ``via`` helper)."""
    names: set[str] = set(seeds)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _is_reach(a.name):
                    names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            home = home_stem is not None and (
                (node.level and (node.module or "").split(".")[-1] == home_stem)
                or (node.module or "").split(".")[:2] == ["onescript", home_stem]
            )
            for a in node.names:
                if _is_reach(prefix) or _is_reach(a.name) or home:
                    names.add(a.asname or a.name)
                elif (
                    home_stem is not None
                    and a.name == home_stem
                    and (node.level or node.module == "onescript")
                ):
                    names.add(a.asname or a.name)
    changed = True
    while changed:
        changed = False
        for n in ast.walk(tree):
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                if _names_in(n.value) & names:
                    tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
                    for t in tgts:
                        for x in ast.walk(t):
                            if isinstance(x, ast.Name) and x.id not in names:
                                names.add(x.id)
                                changed = True
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name not in names and _names_in(n) & names:
                    names.add(n.name)
                    changed = True
    return names


def _local_rules(
    tree, rule_names: set[str], home_stem: str | None = None, seeds=frozenset()
) -> list[str]:
    """Rule names the file defines itself rather than through the reach."""
    reach = _reach_names(tree, home_stem, seeds)
    out = []
    for d in _toplevel_defs(tree):
        if d.name in rule_names and d.name not in reach:
            out.append(d.name)
    return sorted(out)


def _imports_kernel_home(names, home_stem: str) -> bool:
    for n in names:
        parts = n.lstrip(".").split(".")
        if n.startswith(".") and parts[-1] == home_stem:
            return True
        if parts[0] == "onescript" and home_stem in parts[1:]:
            return True
    return False


def _via(repo: Path, f: Path, tree, spec: str) -> tuple[set[str], str | None]:
    """Verify a copy's ``via`` (``path:function`` in its own repo). Returns
    (the names the copy binds from the helper, why-not). Each link of the chain
    is checked by AST; one that cannot be shown FAILS."""
    path, _, fn_name = spec.partition(":")
    if not path or not fn_name or not fn_name.isidentifier():
        return set(), f"via {spec!r} is not 'path:function'"
    hf = _within(repo, path)
    if hf is None or not hf.is_file():
        return set(), f"via helper {path} is not a file in {repo.name}"
    try:
        htree = _parse(hf)
    except SyntaxError as e:
        return set(), f"via helper {path} does not parse: {e}"
    fn = next(
        (
            d
            for d in htree.body
            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
            and d.name == fn_name
        ),
        None,
    )
    if fn is None:
        return set(), f"via helper {path} defines no top-level function {fn_name}"
    if not any(_is_reach(n) for n in _import_names(htree)):
        return set(), f"via helper {path} imports no onescript_* reach"
    bound = _reach_names(htree) - {fn_name}
    if not (_names_in(fn) & bound):
        return (
            set(),
            f"via helper {path}:{fn_name} does not reach through onescript_reach",
        )
    if hf.resolve() == f.resolve():
        return {fn_name}, None
    mod = ".".join(Path(path).with_suffix("").parts)
    stem = Path(path).stem
    got: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        m = node.module or ""
        same = (
            (node.level == 0 and m == mod)
            or (node.level > 0 and m.split(".")[-1] == stem)
            or (node.level == 0 and m.split(".")[-1] == stem and mod.endswith(m))
        )
        if same:
            got |= {a.asname or a.name for a in node.names if a.name == fn_name}
    if not got:
        return set(), f"{f.name} does not import {fn_name} from {path}"
    return got, None


def _gate4(att, pl: Places) -> Result:
    g = GATE_NAMES[3]
    if not att["copies"]:
        return g, False, "no copies listed: every place the rule lived must be named"
    home_files = _home_files(pl, att)
    if not home_files:
        return g, False, f"home {att['home']!r} is not in the one script"
    rules: set[str] = set(att.get("rule_names") or [])
    for hf in home_files:
        rules |= _public_defs(hf)
    home_stem = home_files[0].stem
    # a rule that only looks alike stays, with the one line naming why
    looks = {
        (x["repo"], x["path"], x["name"])
        for x in att.get("lookalikes", [])
        if isinstance(x, dict) and str(x.get("why", "")).strip()
    }
    why, ok_n = [], 0
    for c in att["copies"]:
        label = f"{c['repo']}:{c['path']}"
        if c["kind"] not in _KINDS:
            why.append(f"{label}: kind {c['kind']!r} is neither import nor named-copy")
            continue
        repo, f, bad = _copy_file(pl, c)
        if bad:
            why.append(bad)
            continue
        if c["kind"] == "named-copy":
            if not str(c.get("why", "")).strip():
                why.append(f"{label}: a named copy needs a 'why'")
                continue
            if not c.get("parity"):
                why.append(f"{label}: a named copy needs a parity test")
                continue
            pname, prepo = _parity_home(pl, c)
            if prepo is None:
                why.append(f"{label}: parity test: {_repo_missing(str(pname))}")
                continue
            tf, name, _ = _test_node(prepo, c["parity"])
            if tf is None:
                why.append(f"{label}: parity test: {name}")
                continue
            ok_n += 1
            continue
        try:
            tree = _parse(f)
        except SyntaxError as e:
            why.append(f"{label} does not parse: {e}")
            continue
        names = _import_names(tree)
        in_kernel = _in_kernel(pl, c)
        seeds: set[str] = set()
        if c.get("via"):
            seeds, bad = _via(repo, f, tree, c["via"])
            if bad:
                why.append(f"{label}: via: {bad}")
                continue
        elif not (
            any(_is_reach(n) for n in names)
            or (in_kernel and _imports_kernel_home(names, home_stem))
        ):
            why.append(f"{label}: an import copy that imports no onescript_* reach")
            continue
        local = _local_rules(tree, rules, home_stem if in_kernel else None, seeds)
        local = [n for n in local if (c["repo"], c["path"], n) not in looks]
        if local:
            why.append(f"{label}: defines the rule locally: {', '.join(local)}")
            continue
        ok_n += 1
    if why:
        return g, False, "; ".join(why)
    return (
        g,
        True,
        f"{ok_n} copies: imports reach the kernel, named copies have parity and why",
    )


def _in_kernel(pl: Places, c: dict) -> bool:
    """Is this copy a file inside the one script itself?"""
    repo = pl.repo(c["repo"])
    if repo is None or repo.resolve() != pl.kernel.parent.resolve():
        return False
    f = _within(repo, c["path"])
    return f is not None and f.is_relative_to(pl.kernel.resolve())


def _gate5(att, pl: Places) -> Result:
    g = GATE_NAMES[4]
    # absence is said by a consumer outside the kernel; a copy inside the kernel
    # imports its sibling module, and the kernel cannot be absent from itself
    need = sorted(
        {
            c["repo"]
            for c in att["copies"]
            if c["kind"] == "import" and not _in_kernel(pl, c)
        }
    )
    have = {a["repo"] for a in att["absent"]}
    why = [f"{r}: an import copy and no absent test" for r in need if r not in have]
    ran = []
    for a in att["absent"]:
        repo = pl.repo(a["repo"])
        if repo is None:
            why.append(_repo_missing(a["repo"]))
            continue
        tf, name, fn = _test_node(repo, a["test"])
        if tf is None:
            why.append(f"{a['repo']}: absent test: {name}")
            continue
        src = ast.get_source_segment(tf.read_text(encoding="utf-8"), fn) if fn else ""
        if "unreach" not in (src or "").lower():
            why.append(f"{a['repo']}:{a['test']} never says unreachable")
            continue
        ok, d = _pytest(
            pl, pl.py(a["repo"]), repo, pl.env(repo=repo), [a["test"]], strict_skip=True
        )
        if not ok:
            why.append(f"{a['repo']}:{a['test']}: {d}")
            continue
        ran.append(f"{a['repo']}:{a['test'].split('::')[-1]} ({d})")
    if why:
        return g, False, "; ".join(why)
    if not ran:
        return g, True, "no consumer outside the kernel, so no absence to say"
    return g, True, "absent tests green: " + ", ".join(ran)


# -- gate 6: boundaries -------------------------------------------------------


def _trust_seeds(repo: Path) -> tuple[list[str], str]:
    f = repo / TRUST_TEST
    if f.is_file():
        try:
            for n in ast.walk(_parse(f)):
                if isinstance(n, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "SEEDS" for t in n.targets
                ):
                    seeds = ast.literal_eval(n.value)
                    if seeds and all(isinstance(s, str) for s in seeds):
                        return list(seeds), f"seeds read from {TRUST_TEST}"
        except (SyntaxError, ValueError):
            pass
    return list(BRIEF_TRUST_SEEDS), f"seeds from the brief ({TRUST_TEST} unreadable)"


def _trust_closure(src: Path, pkg: str, seeds: list[str]):
    """(closure {module: names}, missing seeds)."""

    def file_for(mod: str) -> Path | None:
        parts = mod.split(".")
        if parts[0] != pkg:
            return None
        base = src.joinpath(*parts[1:])
        if base.with_suffix(".py").is_file():
            return base.with_suffix(".py")
        if (base / "__init__.py").is_file():
            return base / "__init__.py"
        return None

    def absolute(path: Path, mod: str) -> list[str]:
        here = mod.split(".") if path.name == "__init__.py" else mod.split(".")[:-1]
        out = []
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                out += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = here[: len(here) - (node.level - 1)]
                    prefix = ".".join(base + ([node.module] if node.module else []))
                else:
                    prefix = node.module or ""
                out.append(prefix)
                out += [f"{prefix}.{a.name}" for a in node.names if a.name != "*"]
            elif isinstance(node, ast.Call):
                fn = node.func
                nm = getattr(fn, "attr", getattr(fn, "id", ""))
                if nm in ("import_module", "__import__") and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        out.append(a0.value)
        return out

    missing = [s for s in seeds if file_for(f"{pkg}.{s}") is None]
    todo = [f"{pkg}.{s}" for s in seeds]
    steps = src / "install_steps"
    todo += [
        f"{pkg}.install_steps.{p.stem}"
        for p in sorted(steps.glob("*.py"))
        if p.stem != "__init__"
    ]
    todo.append(f"{pkg}.install_steps")
    seen: dict[str, list[str]] = {}
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        path = file_for(mod)
        if path is None:
            continue
        names = absolute(path, mod)
        seen[mod] = names
        for n in names:
            for cand in (n, n.rsplit(".", 1)[0]):
                if file_for(cand) is not None:
                    todo.append(cand)
        parts = mod.split(".")
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]
    return seen, missing


def _wheel_hits(pkg_dir: Path) -> list[str]:
    bad = []
    for p in sorted(pkg_dir.rglob("*.py")):
        try:
            names = _import_names(_parse(p))
        except (SyntaxError, UnicodeDecodeError) as e:
            bad.append(f"{p.relative_to(pkg_dir)} does not parse: {e}")
            continue
        hit = [n for n in names if _ANYONE.search(n)]
        if hit:
            bad.append(f"{p.relative_to(pkg_dir)} imports {sorted(set(hit))[:3]}")
    return bad


def _default_branch(repo: Path) -> str | None:
    for b in ("master", "main"):
        rc, _ = _run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", b],
            None,
            os.environ.copy(),
            timeout=60,
        )
        if rc == 0:
            return b
    return None


def _boundary_trust(pl: Places) -> tuple[bool, str]:
    repo = pl.repo("willow-mcp")
    if repo is None:
        return False, "willow-mcp:trust: " + _repo_missing("willow-mcp")
    src = repo / "src" / "willow_mcp"
    if not src.is_dir():
        return False, "willow-mcp:trust: src/willow_mcp is not there"
    seeds, how = _trust_seeds(repo)
    closure, missing = _trust_closure(src, "willow_mcp", seeds)
    if missing:
        return False, f"willow-mcp:trust: seeds not found: {missing} ({how})"
    if len(closure) < len(seeds):
        return (
            False,
            "willow-mcp:trust: the walk found less than its seeds (broken walk)",
        )
    bad = {m: [n for n in ns if _ANYONE.search(n)] for m, ns in closure.items()}
    bad = {m: h for m, h in bad.items() if h}
    inside = [
        m for m in closure if m.rsplit(".", 1)[-1].lower().startswith("onescript")
    ]
    if bad or inside:
        shown = "; ".join(
            f"{m} -> {sorted(set(h))[:2]}" for m, h in sorted(bad.items())[:4]
        )
        return False, (
            f"willow-mcp:trust: {len(bad)} closure modules import the one script"
            f" ({shown}){'; reach modules inside: ' + str(inside) if inside else ''}"
        )
    return True, f"willow-mcp:trust: {len(closure)} modules walked, none reach ({how})"


def _boundary_store(att, pl: Places) -> tuple[bool, str]:
    repo = pl.repo("safe-app-store")
    if repo is None:
        return False, "safe-app-store: " + _repo_missing("safe-app-store")
    mine = [c for c in att["copies"] if c["repo"] == "safe-app-store"]
    if not mine:
        return True, "safe-app-store: the chunk has no copies there, none changed"
    base = _default_branch(repo)
    if base is None:
        return False, "safe-app-store: no master or main to diff against"
    bad = []
    for c in mine:
        if c["kind"] == "import":
            bad.append(f"{c['path']} is an import copy (a change to a parts bin)")
            continue
        if _within(repo, c["path"]) is None:
            bad.append(f"{c['path']} is outside the repo")
            continue
        rc, out = _run(
            ["git", "-C", str(repo), "diff", "--quiet", base, "--", c["path"]],
            None,
            os.environ.copy(),
            timeout=120,
        )
        if rc != 0:
            bad.append(f"{c['path']} differs from {base}")
    if bad:
        return False, "safe-app-store: " + "; ".join(bad)
    return True, f"safe-app-store: {len(mine)} copies, no diff against {base}"


def _boundary_wheel(pl: Places) -> tuple[bool, str]:
    repo = pl.repo("willow-bot")
    if repo is None:
        return False, "willow-bot:wheel: " + _repo_missing("willow-bot")
    pkg = repo / "willow_bot"
    if not pkg.is_dir():
        return False, "willow-bot:wheel: no willow_bot package there"
    bad = _wheel_hits(pkg)
    if bad:
        return False, "willow-bot:wheel: " + "; ".join(bad[:4])
    return (
        True,
        "willow-bot:wheel: the willow_bot package imports nothing of the one script",
    )


def _gate6(att, pl: Places) -> Result:
    g = GATE_NAMES[5]
    if not att["never_reach"]:
        return g, False, "never_reach names no boundary: name the ones that apply"
    known = {
        "willow-mcp:trust": lambda: _boundary_trust(pl),
        "safe-app-store": lambda: _boundary_store(att, pl),
        "willow-bot:wheel": lambda: _boundary_wheel(pl),
    }
    why, ok = [], []
    for name in att["never_reach"]:
        fn = known.get(name)
        if fn is None:
            why.append(f"{name!r} is not a boundary this gate can check")
            continue
        passed, d = fn()
        (ok if passed else why).append(d)
    if why:
        return g, False, "; ".join(why)
    return g, True, "; ".join(ok)


# -- gate 7: same input, same output -------------------------------------------


def _gate7(att, pl: Places) -> Result:
    g = GATE_NAMES[6]
    if not att["fixture"]:
        reason = str(att.get("why_not_fixture", "")).strip()
        if reason:
            return g, True, f"[A] no fixture, attested: {reason}"
        return g, False, "fixture is false and no why_not_fixture is attested"
    k = pl.kernel
    fx = k / "onescript" / "tests" / "hidden_inputs_fixture.py"
    if not fx.is_file():
        return g, False, "the hidden-inputs fixture is not in the one script"
    home_files = _home_files(pl, att)
    if not home_files:
        return g, False, f"home {att['home']!r} is not in the one script"
    stem = home_files[0].stem
    try:
        names = _import_names(_parse(fx))
    except SyntaxError as e:
        return g, False, f"the fixture does not parse: {e}"
    named = any(
        n.lstrip(".").split(".")[-1] == stem
        and (n.startswith("onescript") or n.startswith("."))
        for n in names
    )
    if not named:
        return (
            g,
            False,
            f"the fixture's source does not name {stem} (home {att['home']})",
        )
    outs = []
    for seed in ("1", "2"):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(k),
            "PYTHONHASHSEED": seed,
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        rc, out, uid = pl.run_tests(
            [sys.executable, "-m", "onescript.tests.hidden_inputs_fixture"], k, env
        )
        if rc != 0 or not out.strip():
            return (
                g,
                False,
                f"fixture (PYTHONHASHSEED={seed}) exit {rc}: {out.strip()[-160:]}",
            )
        outs.append(out)
    if outs[0] != outs[1]:
        return (
            g,
            False,
            "fixture bytes differ between PYTHONHASHSEED=1 and 2 under LC_ALL=C",
        )
    return (
        g,
        True,
        f"fixture names {stem}; {len(outs[0])} bytes identical under 2 hash seeds, LC_ALL=C (as uid {uid})",
    )


# -- gate 8: the human seals ---------------------------------------------------


def _has_const(node, value: str) -> bool:
    return any(isinstance(x, ast.Constant) and x.value == value for x in ast.walk(node))


def _seal_findings(tree) -> list[str]:
    """What in a module seals, or writes what only a seal may."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            pairs = {
                k.value: v
                for k, v in zip(n.keys, n.values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            if "standing" in pairs and _has_const(pairs["standing"], "sealed"):
                out.append(f"line {n.lineno}: writes standing='sealed'")
            kind = pairs.get("kind")
            if (
                isinstance(kind, ast.Constant)
                and kind.value == "seal"
                and "who" in pairs
            ):
                out.append(f"line {n.lineno}: writes a seal row with a who")
        elif isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            tgts = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in tgts:
                key = None
                if isinstance(t, ast.Name):
                    key = t.id
                elif isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant):
                    key = t.slice.value
                if key == "standing" and _has_const(n.value, "sealed"):
                    out.append(f"line {n.lineno}: sets standing='sealed'")
        elif isinstance(n, ast.Call):
            fn = n.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            if name in SEAL_VERBS:
                out.append(f"line {n.lineno}: calls the seal verb {name}")
            kws = {k.arg: k.value for k in n.keywords if k.arg}
            if "standing" in kws and _has_const(kws["standing"], "sealed"):
                out.append(f"line {n.lineno}: passes standing='sealed'")
            seal_kind = (
                n.args
                and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "seal"
            ) or (
                isinstance(kws.get("kind"), ast.Constant)
                and kws["kind"].value == "seal"
            )
            if seal_kind and (len(n.args) >= 2 or "who" in kws):
                out.append(f"line {n.lineno}: writes a seal row with a who")
    return out


def _gate8(att, pl: Places) -> Result:
    g = GATE_NAMES[7]
    author = att["author"].strip()
    ver = str(att.get("verified_by", "")).strip()
    if ver.lower() in _UNSET:
        return g, False, "verified_by is not set: the human seals, the chunk does not"
    if ver.lower() == author.lower():
        return (
            g,
            False,
            f"verified_by == author ({author!r}): proposing and ratifying never rest in the same hand",
        )
    files = _home_files(pl, att)
    if not files:
        return g, False, f"home {att['home']!r} is not in the one script"
    found = []
    for f in files:
        try:
            found += [f"{f.name}: {x}" for x in _seal_findings(_parse(f))]
        except SyntaxError as e:
            return g, False, f"{f.name} does not parse: {e}"
    if found:
        return g, False, "the chunk seals itself: " + "; ".join(found[:3])
    return g, True, f"verified_by {ver!r} != author {author!r}; home writes no seal"


# -- the run ----------------------------------------------------------------


def check(
    att_path, pl: Places, box=None, box_anchor=None, skip_suite=False
) -> list[Result]:
    att = _load(att_path)
    if att is None:
        return [("attestation", False, f"{att_path} does not exist (fail-closed)")]
    if "_error" in att:
        return [("attestation", False, f"merge.json is malformed: {att['_error']}")]
    bad = _shape(att)
    if bad:
        return [("attestation", False, f"merge.json: {bad}")]
    gates = [
        (GATE_NAMES[0], lambda: _gate1(att, pl, skip_suite)),
        (GATE_NAMES[1], lambda: _gate2(att, pl, box, box_anchor)),
        (GATE_NAMES[2], lambda: _gate3(att, pl)),
        (GATE_NAMES[3], lambda: _gate4(att, pl)),
        (GATE_NAMES[4], lambda: _gate5(att, pl)),
        (GATE_NAMES[5], lambda: _gate6(att, pl)),
        (GATE_NAMES[6], lambda: _gate7(att, pl)),
        (GATE_NAMES[7], lambda: _gate8(att, pl)),
    ]
    out: list[Result] = []
    try:
        for name, fn in gates:
            try:
                out.append(fn())
            except Exception as e:  # a gate that crashes has verified nothing
                out.append(
                    (
                        name,
                        False,
                        f"gate crashed, so unverified: {type(e).__name__}: {e}",
                    )
                )
    finally:
        pl.close()  # the unprivileged copy, if this run made one
    return out


def _gate0(att_path, pl: Places) -> Result:
    """Gate 0: the divergence between the attested ``base_ref`` and ``chunk_ref``,
    recorded and scoping nothing. Not attested is not a failure (the gate is new);
    attested and unreachable is."""
    att = _load(att_path) or {}
    if "_error" in att:
        return GATE0_NAME, True, "not attested (no readable attestation)"
    given = [k for k in ("base_ref", "chunk_ref") if k in att]
    if not given:
        return (
            GATE0_NAME,
            True,
            "not attested (no base_ref and chunk_ref); nothing scoped",
        )
    if len(given) == 1:
        return (
            GATE0_NAME,
            False,
            f"only {given[0]!r} is attested; base_ref and chunk_ref come together",
        )
    base, chunk = att["base_ref"], att["chunk_ref"]
    names = att.get("diverge_repos", ["willow-bot"])
    if (
        not all(isinstance(x, str) and x.strip() for x in (base, chunk))
        or not isinstance(names, list)
        or not names
        or not all(isinstance(n, str) for n in names)
    ):
        return (
            GATE0_NAME,
            False,
            "base_ref, chunk_ref and diverge_repos must be non-empty strings",
        )
    from . import diverge

    res = diverge.run(pl.repos, names, base, chunk)
    h = diverge.result_hash(res)
    if res["state"] == "unreachable":
        return (
            GATE0_NAME,
            False,
            f"diverge is unreachable (fail-closed): {res['reason']}",
        )
    c = res["counts"]
    return (
        GATE0_NAME,
        True,
        f"{res['state']}: {c['minimal']} divergence point(s) between {base} and {chunk} "
        f"(units both-different {c['units_both_different']}, edges {c['edges']}); "
        f"recorded, scopes nothing; sha256 {h[:12]}",
    )


def _gates_payload(results: list[Result]) -> list[dict]:
    return [{"gate": g, "ok": ok, "detail": d} for g, ok, d in results]


def _head(repo: Path | None) -> str:
    if repo is None:
        return "absent"
    rc, out = _run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        None,
        os.environ.copy(),
        timeout=60,
    )
    return out.strip() if rc == 0 else "unknown"


def record_merge(
    att_path, results: list[Result], pl: Places, gate0: Result | None = None
) -> tuple[Path | None, str]:
    """Write one-script/merged/<chunk>.json for a passing chunk. Every refusal
    comes before the first write, so a refused record leaves the tree untouched:
    a failed or unrun gate; ``verified_by`` unset or == ``author`` (enforced here
    on its own, not inherited from gate 8); a chunk name that is not a plain
    identifier; a record that already exists (re-minting is deliberate)."""
    if len(results) != len(GATE_NAMES) or not all(ok for _, ok, _ in results):
        return None, "chunk is NOT MERGEABLE: no record written (fail-closed)"
    if gate0 is not None and not gate0[1]:
        return (
            None,
            "chunk is NOT MERGEABLE (gate 0 divergence): no record written (fail-closed)",
        )
    if [g for g, _, _ in results] != GATE_NAMES:
        return None, "refusing to record: the gate list is not the eight (fail-closed)"
    att = _load(att_path) or {}
    author = str(att.get("author", "")).strip()
    ver = str(att.get("verified_by", "")).strip()
    if not author or ver.lower() in _UNSET or ver.lower() == author.lower():
        return None, (
            f"refusing to record: verified_by={ver!r} must be set and differ "
            f"from author={author!r}; proposing and ratifying never rest in "
            "the same hand (0.2)"
        )
    chunk = att.get("chunk", "")
    if not isinstance(chunk, str) or not _CHUNK.match(chunk):
        return None, f"refusing to record: chunk {chunk!r} is not a plain identifier"
    out = pl.kernel / "merged" / f"{chunk}.json"
    if out.exists():
        return (
            None,
            f"refusing to record: {out} already exists; remove it deliberately to re-mint",
        )
    record = {
        "chunk": chunk,
        "verdict": "MERGEABLE",
        "author": author,
        "verified_by": ver,
        "home": att.get("home"),
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kernel_head": _head(pl.kernel.parent),
        "repos_head": {
            c["repo"]: _head(pl.repo(c["repo"])) for c in att.get("copies", [])
        },
        "gates": _gates_payload(results),
    }
    if gate0 is not None:
        record["divergence"] = {"gate": gate0[0], "ok": gate0[1], "detail": gate0[2]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out, f"recorded at {out}"


def run_cli(
    attestation,
    *,
    repos=None,
    box=None,
    as_json=False,
    record=False,
    skip_suite=False,
    python=None,
    box_anchor=None,
    out=None,
) -> int:
    """The verb: exit 0 only when every gate passed (and, with ``record``, the
    record was written)."""
    out = out or sys.stdout.write
    try:  # a TERM must still run the finally that removes the unprivileged copy
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    except ValueError:  # not the main thread
        pass
    att_path = Path(attestation).resolve()
    pl = default_places(repos, python)
    results = check(att_path, pl, box=box, box_anchor=box_anchor, skip_suite=skip_suite)
    try:
        g0 = _gate0(att_path, pl)
    except Exception as e:  # a gate that crashes has verified nothing
        g0 = (
            GATE0_NAME,
            False,
            f"gate crashed, so unverified: {type(e).__name__}: {e}",
        )
    mergeable = bool(results) and all(ok for _, ok, _ in results) and g0[1]
    written, reason = None, ""
    if record:
        written, reason = record_merge(att_path, results, pl, g0)
    if as_json:
        payload = {
            "attestation": str(att_path),
            "kernel": str(pl.kernel),
            "repos": str(pl.repos),
            "mergeable": mergeable,
            "divergence": {"gate": g0[0], "ok": g0[1], "detail": g0[2]},
            "gates": _gates_payload(results),
        }
        if record:
            payload["record"] = {
                "written": str(written) if written else None,
                "reason": reason,
            }
        out(json.dumps(payload, indent=2) + "\n")
    else:
        out(f"\nmerge candidate: {att_path.stem}  (kernel {pl.kernel})\n")
        out(f"  [{'PASS' if g0[1] else 'FAIL'}] {g0[0]:26} {g0[2]}\n")
        for g, ok, d in results:
            out(f"  [{'PASS' if ok else 'FAIL'}] {g:26} {d}\n")
        out(
            f"\n  verdict: {'MERGEABLE' if mergeable else 'NOT MERGEABLE (fail-closed)'}\n"
        )
        if record:
            out(f"  record:  {reason}\n")
        out("\n")
    if record:
        return 0 if (mergeable and written is not None) else 1
    return 0 if mergeable else 1
