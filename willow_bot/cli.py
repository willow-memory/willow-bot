"""Console entrypoints: willow-bot (webhook) and willow-bot-steward."""
from __future__ import annotations

import os
import sys


def webhook_main() -> None:
    """Run the FastAPI webhook via uvicorn (reads BOT_HOST / BOT_PORT)."""
    import uvicorn

    host = os.environ.get("BOT_HOST", "127.0.0.1")
    port = int(os.environ.get("BOT_PORT", "9000"))
    # Root-level bot:app kept for systemd compatibility during prove.
    uvicorn.run("bot:app", host=host, port=port, factory=False)


def steward_main(argv: list[str] | None = None) -> int:
    from willow_bot.steward.tick import main

    return main(argv if argv is not None else sys.argv[1:])
