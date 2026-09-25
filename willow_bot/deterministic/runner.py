"""Run frozen S-growth JSON fixtures against loopback Ollama."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from willow_bot.deterministic.ollama import chat
from willow_bot.deterministic.policy import Policy


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def growth_prompt(fixture: dict) -> str:
    parts = [fixture.get("brief", "").strip()]
    excerpts = fixture.get("excerpts") or []
    if excerpts:
        blocks = []
        for ex in excerpts:
            if not isinstance(ex, dict):
                continue
            eid = ex.get("id", "ex")
            text = ex.get("text", "")
            blocks.append(f"[{eid}]\n{text}")
        parts.append("Excerpts:\n" + "\n\n".join(blocks))
    parts.append("Respond in plain text. Cite excerpt ids when the brief asks for cites.")
    return "\n\n".join(p for p in parts if p)


def list_growth_fixtures(fixtures_dir: Path) -> list[Path]:
    return sorted(fixtures_dir.glob("S-growth-*.json"))


def run_growth_fixtures(
    policy: Policy,
    *,
    fixtures_dir: Path,
    model: str,
    out_path: Path,
    limit: int | None = None,
) -> dict:
    """Append one JSON object per fixture to ``out_path``. Returns a summary dict."""
    fixtures_dir = fixtures_dir.resolve()
    paths = list_growth_fixtures(fixtures_dir)
    if limit is not None and limit > 0:
        paths = paths[:limit]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_id = out_path.stem
    written = 0
    errors = 0
    with out_path.open("a", encoding="utf-8") as out:
        for fp in paths:
            try:
                fixture = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                row = {
                    "fixture_id": fp.name,
                    "run_id": run_id,
                    "model": model,
                    "arm": "growth",
                    "ts": _utc_now(),
                    "error": f"fixture_read: {exc}",
                }
                out.write(json.dumps(row, sort_keys=True) + "\n")
                errors += 1
                continue
            fid = fixture.get("id") or fp.stem
            prompt = growth_prompt(fixture)
            reply, latency_ms, err = chat(
                base_url=policy.ollama_base,
                model=model,
                prompt=prompt,
                timeout_s=policy.ollama_chat_timeout_s,
            )
            row = {
                "fixture_id": fid,
                "class": fixture.get("class"),
                "run_id": run_id,
                "model": model,
                "arm": "growth",
                "prompt_hash": _prompt_hash(prompt),
                "raw_reply": reply,
                "latency_ms": latency_ms,
                "ts": _utc_now(),
                "error": err,
            }
            out.write(json.dumps(row, sort_keys=True) + "\n")
            written += 1
            if err:
                errors += 1
    return {
        "fixtures_dir": str(fixtures_dir),
        "out_path": str(out_path),
        "model": model,
        "count": written,
        "errors": errors,
    }
