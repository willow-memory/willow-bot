"""credentials.py — resolve willow-bot secrets from the operator data vault.

Precedence (same shape as willow-mcp integrations):
  1. Process env (explicit override / tests)
  2. ``<box>/secrets/willow-bot.env`` (non-PEM settings)
  3. Fernet vault keys under ``willow-bot/…`` (when vault.key is present)
  4. Default PEM path: ``<box>/secrets/willow-bot.pem``

The box is ``$WILLOW_HOME``, else ``$WILLOW_VAULT_BOX`` — the same rule the
state uses. With neither set, or a path that does not exist, there is no box
and resolution raises: never a guessed path, never ``~/.willow``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_VAULT_APP_ID = "willow-bot/app_id"
_VAULT_WEBHOOK = "willow-bot/webhook_secret"
_VAULT_PEM = "willow-bot/private_key"


def vault_box() -> Path:
    """The box, by the same rule as the state it sits beside:
    ``$WILLOW_HOME``, else ``$WILLOW_VAULT_BOX`` (``willow_bot.paths``). No
    default, and it must exist — else ``BoxNotConfigured``."""
    from willow_bot.paths import BOX_ENV, env_box

    return env_box(*BOX_ENV)


def secrets_dir() -> Path:
    return vault_box() / "secrets"


def default_pem_path() -> Path:
    return secrets_dir() / "willow-bot.pem"


def vault_env_path() -> Path:
    return secrets_dir() / "willow-bot.env"


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key:
            out[key] = val
    return out


def _fernet_read(name: str) -> str | None:
    try:
        from willow_mcp.vault import default_vault  # optional dep on host box

        return default_vault().read(name)
    except Exception:
        return None


@dataclass(frozen=True)
class BotCredentials:
    app_id: str
    webhook_secret: str
    private_key_pem: str
    private_key_path: Path | None
    source: str  # short provenance for preflight (never the secret)


def resolve(*, require_complete: bool = True) -> BotCredentials:
    """Load bot credentials. Raises RuntimeError when require_complete and a
    required field is missing — refuse to start unsigned."""
    file_env = _parse_env_file(vault_env_path())

    def _pick(*names: str, file_keys: tuple[str, ...] = ()) -> str:
        for n in names:
            v = os.environ.get(n, "").strip()
            if v:
                return v
        for k in file_keys or names:
            v = file_env.get(k, "").strip()
            if v:
                return v
        return ""

    app_id = _pick("GITHUB_APP_ID", file_keys=("GITHUB_APP_ID",))
    if not app_id:
        app_id = (_fernet_read(_VAULT_APP_ID) or "").strip()

    webhook = _pick("GITHUB_WEBHOOK_SECRET", file_keys=("GITHUB_WEBHOOK_SECRET",))
    if not webhook:
        webhook = (_fernet_read(_VAULT_WEBHOOK) or "").strip()

    pem_path_raw = _pick(
        "GITHUB_APP_PRIVATE_KEY_PATH",
        file_keys=("GITHUB_APP_PRIVATE_KEY_PATH",),
    )
    pem_path: Path | None = Path(pem_path_raw).expanduser() if pem_path_raw else None
    pem_text = ""
    source_bits: list[str] = []

    if pem_path and pem_path.is_file():
        pem_text = pem_path.read_text(encoding="utf-8").strip()
        source_bits.append(f"pem:{pem_path}")
    else:
        default = default_pem_path()
        if default.is_file():
            pem_path = default
            pem_text = default.read_text(encoding="utf-8").strip()
            source_bits.append(f"pem:{default}")
        else:
            pem_text = (_fernet_read(_VAULT_PEM) or "").strip()
            if pem_text:
                pem_path = None
                source_bits.append("fernet:willow-bot/private_key")

    if app_id:
        source_bits.append("app_id:set")
    if webhook:
        source_bits.append("webhook:set")

    missing = []
    if not app_id:
        missing.append("GITHUB_APP_ID (env, secrets/willow-bot.env, or vault willow-bot/app_id)")
    if not webhook:
        missing.append(
            "GITHUB_WEBHOOK_SECRET (env, secrets/willow-bot.env, or vault willow-bot/webhook_secret)"
        )
    if not pem_text:
        missing.append(
            f"App private key ({default_pem_path()} or vault willow-bot/private_key)"
        )

    if missing and require_complete:
        box = vault_box()
        raise RuntimeError(
            "willow-bot credentials incomplete — expected under the operator data "
            f"vault ({box}). Missing: " + "; ".join(missing)
        )

    return BotCredentials(
        app_id=app_id,
        webhook_secret=webhook,
        private_key_pem=pem_text,
        private_key_path=pem_path,
        source=",".join(source_bits) or "empty",
    )
