"""5 resolve — the ladder. The user's own sources first; the model last.

Cites: CONST-IV (Ground: an answer from a source is Cited; from a model, Ungrounded),
CONST-XII (resources are assigned, never seized), CONST-III (web is egress).

Rungs, in order: the user's sources -> web (only through the gate) -> local
models -> cloud models (egress again). The night pool runs every question past
models from several families, inside a recorded budget, and stops the moment the
human is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import gate


@dataclass(frozen=True)
class Model:
    name: str
    family: str
    file_hash: str
    local: bool
    cost: int
    ask: Callable[[str], object]


class Budget:
    """A recorded resource envelope (XII.1). Nothing expands its own (XII.2)."""

    def __init__(self, units: int):
        self.units, self.spent = units, 0

    def spend(self, n: int) -> bool:
        if self.spent + n > self.units:
            return False
        self.spent += n
        return True


def answer(
    question: str,
    sources: list[tuple[str, Callable]],
    models: list[Model],
    budget: Budget,
    law: dict,
    who: str = "run",
) -> dict:
    for name, look in sources:
        got = look(question)
        if got is not None:
            return {
                "q": question,
                "answer": got,
                "from": name,
                "rung": "source",
                "ground": "cited",
            }

    web = gate.door(
        {
            "kind": "egress",
            "who": who,
            "what": f"look up: {question}",
            "where": "web",
            "bytes": len(question.encode()),
            "cites": ["CONST-III"],
        },
        law,
    )
    if web.verdict != "pass":
        parked = {
            "q": question,
            "answer": None,
            "rung": "web",
            "parked": web.verdict,
            "card": web.card,
        }
    else:
        parked = (
            None  # a granted web rung would run here; none is wired in this skeleton
        )

    for m in sorted(models, key=lambda m: (not m.local, m.cost, m.name)):
        if not m.local:
            d = gate.door(
                {
                    "kind": "egress",
                    "who": who,
                    "what": f"ask {m.name}",
                    "where": m.name,
                    "bytes": len(question.encode()),
                    "cites": ["CONST-III"],
                },
                law,
            )
            if d.verdict != "pass":
                continue
        if not budget.spend(m.cost):
            return {"q": question, "answer": None, "rung": "model", "stopped": "budget"}
        return {
            "q": question,
            "answer": m.ask(question),
            "from": m.name,
            "family": m.family,
            "model_hash": m.file_hash,
            "rung": "model",
            "ground": "ungrounded",
        }

    return parked or {"q": question, "answer": None, "rung": "none"}


def night_pool(
    questions: list[str],
    models: list[Model],
    budget: Budget,
    present: Callable[[], bool],
) -> tuple[list[dict], str]:
    """Every question past every local model, for witnessing. Yields to presence."""
    out = []
    for q in questions:
        for m in sorted(
            (m for m in models if m.local), key=lambda m: (m.family, m.name)
        ):
            if present():
                return out, "yielded: the human is here"
            if not budget.spend(m.cost):
                return out, "stopped: budget spent"
            out.append(
                {
                    "q": q,
                    "answer": m.ask(q),
                    "from": m.name,
                    "family": m.family,
                    "model_hash": m.file_hash,
                }
            )
    return out, "complete"
