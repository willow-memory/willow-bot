# willow-bot

GitHub App webhook receiver + orchestrator-feeding **steward** for the Willow
fleet. Propose-only: the App may comment and deposit draft CI outcomes; it
cannot commit.

Install: `pip install willow-bot`  
Docs: [INSTALL.md](INSTALL.md) · [docs/MOVE-STAY-BORROW.md](docs/MOVE-STAY-BORROW.md)

```bash
willow-bot              # webhook (uvicorn)
willow-bot-steward tick    # PR watch one-shot
willow-bot-steward status  # read-only surface: version, tick, inbox depth, cursors
willow-bot-steward status --brief  # one line per section
willow-bot-steward app-grant  # does the App have the permissions and events the code needs?
willow-bot-steward redeliver  # ask GitHub to resend webhook deliveries the bot never got (also every tick)
willow-bot-steward ci-backfill  # fill CI for open PRs with none recorded, from GitHub's check runs (also every tick)
willow-bot-steward contributions --days 30  # who contributed: outsiders, first-timers, per repo
willow-bot-steward contributions --backfill  # fill the ledger from GitHub history (core_repos, 90 days)
willow-bot-steward venvs [--plan]  # D2: which venvs are inside the bot, and how to recreate the rest inside (prints, runs nothing)
willow-bot-contribute [--user LOGIN]   # anyone: your projects, what waits on you, openings (your GITHUB_TOKEN or gh login, never the App's)
willow-bot-steward heartbeat  # curated willow-mcp tools when WILLOW_BOT_MCP=1
willow-bot-deterministic serve   # host loopback runner (Unix socket for Kart)
willow-bot-deterministic client --fixtures /path/to/S-growth-*.json dir
```

Home: [willow-memory/willow-bot](https://github.com/willow-memory/willow-bot)  
Anyone can run it as their own GitHub App. Who you are, which orgs and repos it
looks after, and where the voice speaks live in your install's operator profile
(`$WILLOW_HOME/willow-bot/operator.json`; see `deploy/operator.example.json`), never
in code. With no profile it discovers what it can and otherwise does nothing.
Match actors by `user.type == Bot`, never login.
