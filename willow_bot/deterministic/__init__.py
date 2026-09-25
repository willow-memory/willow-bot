"""Host-side loopback model runs for Kart-delegated deterministic tests.

Kart's sandbox cannot reach ``127.0.0.1:11434``. A long-lived ``serve`` process
on the host listens on a Unix socket under ``$WILLOW_HOME/willow-bot/``; Kart
tasks call ``client`` with the same policy the steward uses for venv paths.

Horizon (operator, 2026-09-24): if this delegate path holds, model weights and
the inference surface may move *into* the bot's boundary (managed lifecycle,
policy, and audit under ``$WILLOW_HOME/willow-bot/``) instead of ambient
``~/.ollama`` on the operator disk — same church/state split as secrets in the
vault vs code in git: the desk and Kart stay outside; the bot is the steward of
what may run locally, not a thin pipe to whatever happens to be on disk.
"""
