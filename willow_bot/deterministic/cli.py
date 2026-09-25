"""``willow-bot-deterministic`` — host runner + Kart client."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from willow_bot.deterministic.policy import load_policy
from willow_bot.deterministic.runner import run_growth_fixtures
from willow_bot.deterministic.socket_client import client_op
from willow_bot.deterministic.socket_server import client_run, serve_forever


def _run_id_default() -> str:
    return (
        datetime.now(timezone.utc)
        .strftime("layer-b-growth-%Y%m%dT%H%M%SZ")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Host loopback model runs (Unix socket delegate for Kart)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser(
        "serve", help="Listen on $WILLOW_HOME/willow-bot/deterministic.sock"
    )

    def _serve(_args: argparse.Namespace) -> int:
        serve_forever()
        return 0

    p_serve.set_defaults(func=_serve)

    p_run = sub.add_parser("run", help="Run on this host (operator shell, not Kart)")
    p_run.add_argument("--fixtures", type=Path, required=True)
    p_run.add_argument("--model", default="")
    p_run.add_argument("--run-id", default="")
    p_run.add_argument("--limit", type=int, default=0)
    p_run.set_defaults(func=_cmd_run)

    p_client = sub.add_parser("client", help="Delegate to serve (use from Kart tasks)")
    p_client.add_argument("--fixtures", type=Path, required=True)
    p_client.add_argument("--model", default="")
    p_client.add_argument("--run-id", default="")
    p_client.add_argument("--limit", type=int, default=0)
    p_client.set_defaults(func=_cmd_client)

    p_ladder = sub.add_parser(
        "ladder",
        help="Run client for each chain tier (§8 ladder; one JSONL per model)",
    )
    p_ladder.add_argument("--fixtures", type=Path, required=True)
    p_ladder.add_argument(
        "--models",
        default="",
        help="Comma-separated models; default is policy chain_tiers",
    )
    p_ladder.add_argument("--limit", type=int, default=0)
    p_ladder.set_defaults(func=_cmd_ladder)

    for name, op, help_text in (
        ("health", "health", "Ollama reachability + /api/ps (via serve)"),
        ("tags", "ollama_tags", "List Ollama model tags (via serve)"),
        ("ps", "ollama_ps", "Ollama loaded models (via serve)"),
        ("runs", "runs_summary", "Recent layer-b-growth JSONL sizes (via serve)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=_cmd_socket_op, socket_op=op)

    p_probe = sub.add_parser(
        "probe-model", help="One short chat to test a model (via serve)"
    )
    p_probe.add_argument("--model", required=True)
    p_probe.add_argument("--timeout", type=float, default=30.0)
    p_probe.set_defaults(func=_cmd_probe_model)

    p_status = sub.add_parser(
        "status",
        help="Health + runs; writes $WILLOW_HOME/willow-bot/runs/flowering-kart-overlay.json",
    )
    p_status.add_argument("--fixtures", type=Path, default=None)
    p_status.set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


def _cmd_run(args: argparse.Namespace) -> int:
    policy = load_policy()
    model = (args.model or "").strip() or policy.default_model
    run_id = (args.run_id or "").strip() or _run_id_default()
    limit = args.limit if args.limit > 0 else None
    out_path = policy.runs_dir / f"{run_id}.jsonl"
    summary = run_growth_fixtures(
        policy,
        fixtures_dir=args.fixtures,
        model=model,
        out_path=out_path,
        limit=limit,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("errors", 0) == 0 else 1


def _cmd_ladder(args: argparse.Namespace) -> int:
    policy = load_policy()
    missing = _require_socket(policy)
    if missing is not None:
        return missing
    if (args.models or "").strip():
        tiers = [m.strip() for m in args.models.split(",") if m.strip()]
    else:
        tiers = list(policy.chain_tiers)
    limit = args.limit if args.limit > 0 else None
    ladder_out: list[dict] = []
    rc = 0
    for model in tiers:
        run_id = _run_id_default()
        result = client_run(
            policy,
            fixtures_dir=args.fixtures,
            model=model,
            run_id=run_id,
            limit=limit,
        )
        ladder_out.append({"model": model, "run_id": run_id, **result})
        if not result.get("ok"):
            rc = 1
        elif result.get("errors", 0):
            rc = 1
    print(json.dumps({"ok": rc == 0, "tiers": ladder_out}, indent=2, sort_keys=True))
    return rc


def _require_socket(policy) -> int | None:
    if not policy.socket_path.is_socket():
        print(
            f"Error: deterministic socket missing at {policy.socket_path} "
            "(start: willow-bot-deterministic serve)",
            file=sys.stderr,
        )
        return 2
    return None


def _cmd_socket_op(args: argparse.Namespace) -> int:
    policy = load_policy()
    missing = _require_socket(policy)
    if missing is not None:
        return missing
    req: dict = {"op": args.socket_op}
    if args.socket_op == "runs_summary":
        req["limit"] = 15
    result = client_op(policy, req, timeout_s=60.0)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def _cmd_probe_model(args: argparse.Namespace) -> int:
    policy = load_policy()
    missing = _require_socket(policy)
    if missing is not None:
        return missing
    timeout = max(1.0, float(args.timeout))
    result = client_op(
        policy,
        {
            "op": "probe_model",
            "model": args.model.strip(),
            "timeout_s": timeout,
        },
        timeout_s=timeout + 5.0,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def _cmd_status(args: argparse.Namespace) -> int:
    policy = load_policy()
    missing = _require_socket(policy)
    if missing is not None:
        return missing
    req: dict = {"op": "status"}
    if args.fixtures is not None:
        req["fixtures_dir"] = str(args.fixtures)
    result = client_op(policy, req, timeout_s=60.0)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


def _cmd_client(args: argparse.Namespace) -> int:
    policy = load_policy()
    missing = _require_socket(policy)
    if missing is not None:
        return missing
    model = (args.model or "").strip() or policy.default_model
    run_id = (args.run_id or "").strip() or _run_id_default()
    limit = args.limit if args.limit > 0 else None
    result = client_run(
        policy,
        fixtures_dir=args.fixtures,
        model=model,
        run_id=run_id,
        limit=limit,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result.get("ok"):
        return 1
    return 0 if result.get("errors", 0) == 0 else 1
