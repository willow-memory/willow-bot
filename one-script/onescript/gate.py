"""4 gate — the doors. Every change passes here; every gate fails closed and loud.

Cites: CONST-III (default-deny; standing grants), CONST-II, §0.2, §0.3, CONST-V.2,
App. B (the probes boot runs against this module).

A change is PR-shaped: it says what it Cites, what it Amends, or None, because...
"The law" is the fixed half (the constitution's Trace IDs) plus the user's half
(grants, precedents, sealed rows). Python checks that a citation exists, is
known, and matches what the change touches. Whether it is *true* is a witness's
job, and only the human's seal makes it so.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import graph, nestor_seal
from . import sign as _sign
from .record import Verified

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "scan"))
import script_match as sm  # noqa: E402  (adopt, don't rewrite: checks 1-4 live there)
from .states import EMPTY, POPULATED

_TOKEN = object()  # the only key that mints a Verified; the record checks for it
HUMAN = "human"


class Refused(Exception):
    """A closed door. The reason is written down; nothing is guessed."""


@dataclass
class Decision:
    # pass | flagged | awaiting_seal | awaiting_grant | awaiting_mandate
    # | hard_close | refused
    verdict: str
    reason: str = ""
    card: dict = field(default_factory=dict)  # a grant or seal card, when one is needed
    match: dict = field(default_factory=dict)  # a script-match result, when relevant


def token() -> object:
    return _TOKEN


def sign(key: bytes, *parts: str) -> str:
    """The gate's HMAC seal; the one HMAC helper is `sign.hmac_parts`
    (flatten-2 F14), the bytes are unchanged."""
    return _sign.hmac_parts(key, *parts)


def verify(identity: dict, keys: dict[str, bytes]) -> Verified:
    """Box 2. The name and family are claims; the signature is the key."""
    who, family, sig = (
        identity.get("who"),
        identity.get("family", ""),
        identity.get("sig", ""),
    )
    key = keys.get(who or "")
    if key is None or not _sign.hmac_equal(sign(key, who, family), sig):
        raise Refused(f"unsigned agent at the door: {who!r}")
    if who == HUMAN:
        raise Refused(
            "the human seat is not entered by signature; it is a key in a hand"
        )
    return Verified(who, family, _TOKEN)


def system() -> Verified:
    """The run itself, for the rows it writes about its own steps."""
    return Verified("run", "python", _TOKEN)


def verify_seal(subject: str, proof: str, human_key: bytes) -> bool:
    """Only the human's key produces this. Presence is a label; this is authority."""
    return _sign.hmac_equal(sign(human_key, "seal", subject), proof)


def human(subject: str, proof: str, human_key: bytes) -> Verified:
    """The one way a 'human' stamp exists: a seal proof the human's key made."""
    if not verify_seal(subject, proof, human_key):
        raise Refused("seal proof does not verify; presence is a label, not a key")
    return Verified(HUMAN, HUMAN, _TOKEN)


def human_nestor(
    subject: str, pair: object, keyring: dict, verifiers: frozenset | None = None
) -> tuple[Verified, str]:
    """The other way a 'human' stamp exists, with no terminal: a sealed Nestor
    pair whose conclusion is exactly `subject`, signed by a verifier in the
    keyring. Returns the stamp and the verifier's name. Refused otherwise."""
    who, why = nestor_seal.check(pair, subject, keyring, verifiers)
    if who is None:
        raise Refused(f"nestor seal does not verify: {why}")
    return Verified(HUMAN, HUMAN, _TOKEN), who


def door(change: dict, law: dict, index: list[dict] | None = None) -> Decision:
    """Fail closed: any defect in the change, or in this function, is a refusal."""
    try:
        return _door(change, law, index or [])
    except Refused as e:
        return Decision("refused", str(e))
    except Exception as e:  # a gate that can't think clearly doesn't guess
        return Decision(
            "refused", f"gate error, failing closed: {type(e).__name__}: {e}"
        )


def _door(change: dict, law: dict, index: list[dict]) -> Decision:
    kind = change["kind"]
    known = set(law["trace_ids"]) | set(law.get("user_rules", []))

    if change.get("standing") in ("sealed", "witnessed") or kind == "seal":
        raise Refused("a seal or a standing cannot be awarded through the door (§0.2)")

    cites, amends = change.get("cites", []), change.get("amends", [])
    because = (change.get("none_because") or "").strip()
    if not cites and not amends and not because:
        raise Refused("cites nothing and says nothing about why")
    unknown = sorted(set(cites + amends) - known)
    if unknown:
        raise Refused(f"cites unknown law: {unknown}")

    if kind == "law" or amends:
        return Decision(
            "awaiting_seal",
            "changes the law; only the human's key can seal it",
            card={"what": change.get("what", ""), "amends": amends or ["(law file)"]},
        )

    if kind == "egress":
        grant = next(
            (
                g
                for g in law.get("grants", [])
                if g["who"] == change["who"] and g["where"] == change["where"]
            ),
            None,
        )
        if grant is None:
            return Decision(
                "awaiting_grant",
                "outbound, and no standing grant covers it",
                card={k: change.get(k) for k in ("who", "what", "where", "bytes")},
            )

    touches = set(change.get("touches", []))
    if touches and cites and not (touches & set(cites)):
        return Decision(
            "flagged", f"cites {sorted(cites)} but touches {sorted(touches)}"
        )

    if kind == "script":
        e = sm.entry(
            change["source"], change.get("label", "script"), change.get("name")
        )
        e = sm.match(index + [e])[-1]
        small = len(e.get("features", ())) < law.get("min_features", 6)
        if (
            e["verdict"] == "similar" and small
        ):  # tonight's finding: small scripts over-group
            e["verdict"], e["of"] = "new", None
        if e["verdict"] != "new":
            return Decision(
                "flagged",
                f"{e['verdict']} of index[{e['of']}]: use it, extend it, "
                f"or write new and say why",
                match=e,
            )
        return Decision("pass", match=e)

    return Decision("pass")


def _closed(fn, *args) -> Decision:
    """Every door fails closed: a refusal, or any error in the door itself."""
    try:
        return fn(*args)
    except Refused as e:
        return Decision("refused", str(e))
    except Exception as e:
        return Decision(
            "refused", f"gate error, failing closed: {type(e).__name__}: {e}"
        )


# ── layer 6: boundaries — what leaves the box is classed and carded ──────────
PROVENANCE = ("authored", "transcript", "memory", "third-party")
FILE_BY_FILE = ("transcript", "memory")  # never covered by a blanket grant


def push_card(pile: dict, files: list[str], who: str, where: str, law: dict):
    """A push is egress. The card lists every file and its bytes, by provenance."""
    return _closed(_push_card, pile, files, who, where, law)


def _push_card(pile, files, who, where, law) -> Decision:
    ptr = {i["where"]: i for i in pile.get("items", [])}
    groups: dict[str, list[dict]] = {}
    for f in sorted(files):
        p = ptr.get(f)
        if p is None:
            raise Refused(f"'{f}' is not in the pile; nothing unlisted leaves the box")
        cls = p.get("provenance", "unknown")
        if cls not in PROVENANCE:
            raise Refused(
                f"'{f}' has no known provenance ({cls}); unclassed never leaves"
            )
        groups.setdefault(cls, []).append(
            {"where": f, "bytes": p["bytes"], "sha": p["sha"]}
        )
    card = {
        "who": who,
        "where": where,
        "groups": dict(sorted(groups.items())),
        "bytes": sum(i["bytes"] for g in groups.values() for i in g),
    }
    grant = next(
        (g for g in law.get("grants", []) if g["who"] == who and g["where"] == where),
        None,
    )
    if grant is None:
        return Decision(
            "awaiting_grant", "a push is outbound; no grant covers it", card
        )
    uncovered = [
        i["where"]
        for cls, items in groups.items()
        for i in items
        if (cls in FILE_BY_FILE and i["where"] not in grant.get("files", []))
        or (
            cls not in FILE_BY_FILE
            and cls not in grant.get("classes", [])
            and i["where"] not in grant.get("files", [])
        )
    ]
    if uncovered:
        card["uncovered"] = uncovered
        return Decision(
            "awaiting_grant",
            "the grant doesn't name these; transcript and memory files are "
            "granted file by file, never in a blanket",
            card,
        )
    return Decision("pass", card=card)


# ── layer 7: mandate — every act is traced to the human's words ─────────────
AUTHORISE = ("human",)  # a hook, subagent, summary or trigger never authorises


def mandate(act: dict, mandates: dict, known_names: set, constraints: list):
    """Before an act runs: whose words, do they cover it, is it new scope."""
    return _closed(_mandate, act, mandates, known_names, constraints)


def _mandate(act, mandates, known_names, constraints) -> Decision:
    m = mandates.get(act.get("mandate") or "")
    if m is None:
        return Decision(
            "awaiting_mandate", "no mandate: which of the human's words asked for this?"
        )
    if m["source"] not in AUTHORISE:
        raise Refused(f"asked by a {m['source']}, not the human")
    unresolved = sorted(n for n in act.get("refers_to", []) if n not in known_names)
    if unresolved:
        return Decision(
            "hard_close",
            f"the record has nothing named {unresolved}; asking, not guessing",
            card={"unresolved": unresolved, "words": m["words"], "turn": m["turn"]},
        )
    if act["kind"] not in m["covers"]:
        return Decision(
            "awaiting_mandate",
            f"the words ({m['words']!r}) cover {sorted(m['covers'])}, not "
            f"{act['kind']!r}",
            card={"turn": m["turn"], "words": m["words"], "kind": act["kind"]},
        )
    paths = act.get("paths", [])
    for c in constraints:
        if act["kind"] in c.get("kinds", [act["kind"]]) and any(
            p.startswith(pre) for p in paths for pre in c["paths"]
        ):
            raise Refused(f"standing rule: {c['rule']}")
    outside = sorted(
        p for p in paths if not any(p.startswith(s) for s in m.get("scope", [""]))
    )
    if outside:
        return Decision(
            "flagged",
            "new scope: offer it before starting it (Rule 4)",
            card={"paths": outside, "turn": m["turn"]},
        )
    return Decision("pass", card={"turn": m["turn"], "words": m["words"]})


# ── capabilities: the questions a normal system asks ─────────────────────────
# Operator, 2026-10-02: "More so what a normal computer system would ask. Do you
# want to allow python. Do you want to allow node. pytest. et al." … "the gate,
# on each, would just be that toggle. Run Once. Run For session. Run perm."
#
# Every command is classed into a capability (first match wins). The door asks
# per capability, not per command. The human's answer is one of four, and
# only a seal proof made by the human's key can give it; the model can't click.
CAPABILITIES = (
    ("pytest", r"\bpytest\b"),
    ("lint", r"\b(ruff|mypy|flake8|black)\b"),
    (
        "git write",
        r"\bgit (commit|push|merge|rebase|reset|checkout|switch|worktree|cherry-pick|stash|tag)\b|\bgit branch -[dD]\b",
    ),
    ("git read", r"\bgit\b"),
    (
        "package install",
        r"\b(pip|uv|npm|pnpm|yarn|cargo|apt|brew) (install|add|sync)\b|\buvx\b|\bnpx\b",
    ),
    ("network", r"\b(curl|wget|gh|ssh|scp|rsync)\b|https?://"),
    ("node", r"\b(node|deno|bun)\b"),
    ("python", r"\bpython3?\b|\.py\b"),
    ("systemd", r"\b(systemctl|journalctl)\b"),
    ("database", r"\b(psql|sqlite3|mysql)\b"),
    ("delete", r"\brm\b|\bshred\b|\btruncate\b"),
    ("shell", r"."),
)
SCOPES = ("once", "session", "permanent")  # and "no", which is always on the card
LADDER = (3, 7, 13, 23)  # the repetition ladder: the levels a capability climbs


def capability(command: str) -> str:
    """The capability a command needs. VAR=value prefixes are dropped first,
    so a token in an env assignment never decides the class or reaches a card."""
    words = command.strip().split()
    while words and "=" in words[0] and not words[0].startswith(("-", "=")):
        words.pop(0)
    cmd = " ".join(words)
    return next(name for name, pat in CAPABILITIES if re.search(pat, cmd))


def level(times: int) -> int:
    """The highest ladder rung a count has reached; 0 below the first."""
    return max((n for n in LADDER if times >= n), default=0)


def allow(
    command: str, session: str, grants: list[dict], seen: dict | None = None
) -> Decision:
    """The capability door. Fails closed: no grant that covers it, no run."""
    return _closed(_allow, command, session, grants, seen or {})


def _allow(command, session, grants, seen) -> Decision:
    cap = capability(command)
    for g in grants:
        if g["capability"] != cap or g.get("revoked"):
            continue
        if g["scope"] == "permanent":
            return Decision("pass", f"{cap}: permanent grant", card={"grant": g["id"]})
        if g["scope"] == "session" and g["session"] == session:
            return Decision(
                "pass", f"{cap}: granted for this session", card={"grant": g["id"]}
            )
        if g["scope"] == "once" and not g.get("used"):
            g["used"] = True  # consumed by this one act; the record keeps the row
            return Decision("pass", f"{cap}: granted once", card={"grant": g["id"]})
    times = seen.get(cap, 0) + 1
    rung = level(times)
    card = {
        "capability": cap,
        "options": ["run once", "run for session", "run permanently", "no"],
        "seen": times,
        "level": rung,
    }
    if rung:  # the ladder offers; it never applies (§0.3)
        card["offer"] = (
            "run permanently?" if rung >= 13 else "run for session?"
        ) + f" ({cap} asked {times} times)"
    if cap in ("network", "package install"):
        card["egress"] = "what leaves the box goes on its own card, with the bytes"
    return Decision("awaiting_grant", f"{cap}: no grant covers it", card=card)


def grant(
    cap: str, scope: str, session: str, subject_proof: str, human_key: bytes, gid: str
) -> dict:
    """The human's answer to a capability card, as a row for the record.
    Refused unless the seal proof was made by the human's key over exactly
    this capability and scope: presence is a label; this is authority."""
    if cap not in {n for n, _ in CAPABILITIES}:
        raise Refused(f"unknown capability {cap!r}")
    if scope not in SCOPES:
        raise Refused(f"unknown scope {scope!r}; the answers are {SCOPES} or no")
    if not verify_seal(f"allow:{cap}:{scope}", subject_proof, human_key):
        raise Refused(
            "grant proof does not verify; only the human's key answers a card"
        )
    return {
        "id": gid,
        "capability": cap,
        "scope": scope,
        "session": session,
        "by": HUMAN,
    }


# ── layer 5: claims — what the run says is checked before the human reads it ─
_WORDS = dict(
    zip(
        "one two three four five six seven eight nine ten eleven twelve".split(),
        range(1, 13),
    )
)
_GREEN = re.compile(r"\b(tests? pass(?:ed|es)?|all green|is green|went green)\b", re.I)
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_TURN = re.compile(r"\bT(\d{1,4})\b")
_QUOTE = re.compile(r'["\u201c]([^"\u201c\u201d\n]{20,})["\u201d]')
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`[^`\n]*`")


def _prose(text: str) -> str:
    """Quotes are only read in prose: not across code blocks, inline code or
    table cells, which paired quote marks across a whole file (2026-10-02)."""
    text = _INLINE.sub(" ", _FENCE.sub(" ", text))
    return "\n".join(x for x in text.splitlines() if not x.lstrip().startswith("|"))


def _norm(text: str) -> str:
    text = re.sub(r"(?m)^\s*>\s?", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _in(span: list, lo: int, hi: int) -> bool:
    return span[0] <= lo and hi <= span[1]


def _covered(spans: list, lo: int, hi: int) -> bool:
    """Whether every line of lo..hi lies in the union of `spans` (adjacent spans
    join: one ends on the line before the next begins)."""
    reach = lo
    for a, b in sorted(spans):
        if a > reach:
            break
        reach = max(reach, b + 1)
        if reach > hi:
            return True
    return False


def _symbol_row(c: dict, entry: object) -> dict:
    """One qualified cite against what the graph said about its file (T-cbm-1).
    `verified`: the name is defined there, or the line falls inside a symbol.
    `unverified` (struck): the graph is fresh and stamped and holds no such file
    or no such name there. `unchecked`: the graph was stale, unreachable, not
    asked, lacks the project (no stamp), the file name is ambiguous, or a line
    or range falls outside every symbol (an import, a constant, past the end);
    the record says which, and nothing is claimed either way."""
    base = {"at": c["at"], "kind": "symbol", "claim": c["claim"]}
    if not isinstance(entry, dict) or "state" not in entry:
        return {**base, "verdict": "unchecked", "record": "the graph was not asked"}
    state = entry["state"]
    if state in ("stale", "unreachable"):
        return {
            **base,
            "verdict": "unchecked",
            "record": f"graph {state}: {entry.get('why', '')}".rstrip(": "),
        }
    if state == EMPTY and not entry.get("stamp"):
        # S1: no stamp means the project itself is not in the graph: nothing was
        # looked at, so nothing is struck
        return {
            **base,
            "verdict": "unchecked",
            "record": "project not in the graph",
        }
    if state != POPULATED:  # empty and stamped: a fresh graph holds no symbol there
        return {
            **base,
            "verdict": "unverified",
            "record": entry.get("why") or "not in the graph",
        }
    if state == "overflow":  # more files cited than a sentence may: struck
        return {
            **base,
            "verdict": "unverified",
            "record": entry.get("why", "too many files cited"),
        }
    paths = entry.get("paths") or []
    names, ref = entry.get("names") or {}, c["ref"]
    at = (entry.get("stamp") or {}).get("head_sha", "")[:12]
    per = entry.get("by_path") or ({paths[0]: names} if len(paths) == 1 else {})
    if len(paths) != 1 and not per:
        return {
            **base,
            "verdict": "unchecked",
            "record": f"{c['file']} names {len(paths)} files in the graph: "
            + ", ".join(paths[:3]),
        }
    if ref[0].isdigit():
        lo, _, hi = ref.partition("-")
        lo, hi = int(lo), int(hi or lo)
        if lo > hi:
            return {**base, "verdict": "unverified", "record": "a reversed range"}
        # S2: a line is verified inside a symbol; a range only when the union of
        # the symbols it crosses covers every line of it. A line outside every
        # symbol is an import, a constant or past the end: the graph cannot say.
        hit = [p for p, nm in per.items() if _covered(sum(nm.values(), []), lo, hi)]
        if len(hit) == 1:
            return {**base, "verdict": "verified", "record": f"{hit[0]} at {at}"}
        if len(hit) > 1:
            return _ambiguous(base, c, hit)
        return {
            **base,
            "verdict": "unchecked",
            "record": f"outside every symbol the graph holds in {c['file']} at {at}",
        }

    def defines(nm: dict) -> bool:
        if "." in ref:  # Class.method: the method sits inside that class
            cls, meth = ref.split(".", 1)
            return any(
                _in(k, m[0], m[1]) for k in nm.get(cls, []) for m in nm.get(meth, [])
            )
        return ref in nm

    hit = [p for p, nm in per.items() if defines(nm)]
    if len(hit) == 1:  # an ambiguous basename: exactly one file defines it
        return {**base, "verdict": "verified", "record": f"{hit[0]} at {at}"}
    if len(hit) > 1:
        return _ambiguous(base, c, hit)
    where = paths[0] if len(paths) == 1 else f"all {len(paths)} files named {c['file']}"
    return {**base, "verdict": "unverified", "record": f"not in {where} at {at}"}


def _ambiguous(base: dict, c: dict, hit: list[str]) -> dict:
    """Two or more files named like the cite all define it: the cite does not say
    which, and nothing is claimed either way."""
    return {
        **base,
        "verdict": "unchecked",
        "record": f"{c['file']} names {len(hit)} files that define it: "
        + ", ".join(sorted(hit)[:3]),
    }


def check_claims(text: str, facts: dict) -> list[dict]:
    """Checkable claims only. A claim the record can't check isn't listed:
    it stays agent-reported, and the stamp says so.

    `facts["symbols"]` ({file: `graph.symbols_of(...)`}, from `graph.symbols_for`)
    turns on the `symbol` kind (T-cbm-1): each qualified `file.py:name` or
    `file.py:line` cite is read against the code graph. Without that key no
    symbol row is listed. Bare names are never read."""
    rows = []
    for noun, value in sorted(facts.get("counts", {}).items()):
        pat = re.compile(
            rf"\b(\d+|{'|'.join(_WORDS)})\s+(?:[\w`./-]+\s+){{0,2}}?{re.escape(noun)}\b",
            re.I,
        )
        for m in pat.finditer(text):
            said = m.group(1).lower()
            n = int(said) if said.isdigit() else _WORDS[said]
            ok = n == value
            rows.append(
                {
                    "at": m.start(),
                    "kind": "count",
                    "claim": m.group(0),
                    "verdict": "verified" if ok else "unverified",
                    "record": value,
                }
            )
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if not _GREEN.search(sentence):
            continue
        shas = _SHA.findall(sentence)
        green = facts.get("green_sha")
        ok = bool(green) and any(
            green.startswith(s) or s.startswith(green) for s in shas
        )
        why = (
            ""
            if ok
            else "names no commit"
            if not shas
            else f"the record has green on {green or 'nothing'}"
        )
        rows.append(
            {
                "at": text.find(sentence),
                "kind": "result",
                "claim": sentence.strip(),
                "verdict": "verified" if ok else "unverified",
                "record": why or green,
            }
        )
    last = facts.get("turns")
    if last is not None:
        for m in _TURN.finditer(text):
            ok = 1 <= int(m.group(1)) <= last
            rows.append(
                {
                    "at": m.start(),
                    "kind": "turn",
                    "claim": m.group(0),
                    "verdict": "verified" if ok else "unverified",
                    "record": f"turns 1-{last}",
                }
            )
    said_by_human = _norm(facts.get("operator_text", ""))
    if said_by_human:
        prose = _prose(text)
        for m in _QUOTE.finditer(prose):
            ok = _norm(m.group(1)) in said_by_human
            rows.append(
                {
                    "at": m.start(),
                    "kind": "quote",
                    "claim": m.group(1)[:60],
                    "verdict": "verified" if ok else "unverified",
                    "record": "" if ok else "not in the human's turns",
                }
            )
    if "symbols" in facts:
        got = facts["symbols"] if isinstance(facts["symbols"], dict) else {}
        rows += [
            _symbol_row(c, got.get(c["file"])) for c in graph.qualified_cites(text)
        ]
    return sorted(rows, key=lambda r: (r["at"], r["kind"]))
