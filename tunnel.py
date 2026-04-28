"""
tunnel.py — cloudflared tunnel lifecycle for willow-bot.
b17: WBTUN1  ΔΣ=42

Starts a named cloudflared tunnel in a subprocess.
Named tunnel survives reboots and keeps a stable URL.
Requires: cloudflared installed, `willow-bot` tunnel pre-created via:
    cloudflared tunnel create willow-bot
    cloudflared tunnel route dns willow-bot bot.yourdomain.com
"""
import logging
import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("willow-bot.tunnel")

_TUNNEL_NAME = os.getenv("CLOUDFLARE_TUNNEL_NAME", "willow-bot")
_BOT_PORT    = int(os.getenv("BOT_PORT", "9000"))
_CONFIG_PATH = Path.home() / ".cloudflared" / "config.yml"

_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def _write_config() -> None:
    """Write cloudflared config.yml pointing at the local bot port."""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(f"""tunnel: {_TUNNEL_NAME}
credentials-file: {Path.home() / '.cloudflared' / (_TUNNEL_NAME + '.json')}

ingress:
  - service: http://localhost:{_BOT_PORT}
""")
    log.info("cloudflared config written to %s", _CONFIG_PATH)


def start() -> bool:
    """Start the cloudflared tunnel subprocess. Returns True if started."""
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            log.info("tunnel already running (pid %s)", _proc.pid)
            return True

        cloudflared = shutil.which("cloudflared")
        if not cloudflared:
            log.error("cloudflared not found on PATH — tunnel not started")
            return False

        _write_config()

        try:
            _proc = subprocess.Popen(
                [cloudflared, "tunnel", "--config", str(_CONFIG_PATH), "run"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            log.info("cloudflared tunnel started (pid %s)", _proc.pid)

            # Log stderr in background so errors surface
            def _log_stderr():
                for line in _proc.stderr:
                    line = line.strip()
                    if line:
                        log.info("[cloudflared] %s", line)

            threading.Thread(target=_log_stderr, daemon=True).start()
            return True
        except Exception as e:
            log.error("failed to start cloudflared: %s", e)
            return False


def stop() -> None:
    """Gracefully stop the tunnel subprocess."""
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.send_signal(signal.SIGTERM)
            try:
                _proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _proc.kill()
            log.info("cloudflared tunnel stopped")
        _proc = None


def status() -> dict:
    """Return tunnel process status."""
    with _lock:
        if _proc is None:
            return {"running": False, "pid": None}
        alive = _proc.poll() is None
        return {"running": alive, "pid": _proc.pid if alive else None}
