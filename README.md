# willow-bot

GitHub App webhook receiver + orchestrator-feeding **steward** for the Willow
fleet. Propose-only: `willows-bot` may comment and deposit draft CI outcomes; it
cannot commit.

Install: `pip install willow-bot`  
Docs: [INSTALL.md](INSTALL.md) · [docs/MOVE-STAY-BORROW.md](docs/MOVE-STAY-BORROW.md)

```bash
willow-bot              # webhook (uvicorn)
willow-bot-steward tick    # PR watch one-shot
willow-bot-steward status  # read-only surface: version, tick, inbox depth, cursors
willow-bot-steward heartbeat  # curated willow-mcp tools when WILLOW_BOT_MCP=1
```

Home: [willow-memory/willow-bot](https://github.com/willow-memory/willow-bot)  
App: `willows-bot` (id 4001890) — match actors by `user.type == Bot`, never login.
