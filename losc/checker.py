"""
losc/checker.py — Local Only Servers Club membership checker.
b17: LOSCCK  ΔΣ=42

Evaluates whether a GitHub repo qualifies for LOSC membership.
Called by the bot on fork events. Heuristic — not a hard gate.
"""
import logging
import re
from typing import Optional

import requests

log = logging.getLogger("willow-bot.losc")

# Paths that suggest a sovereignty manifest exists
_MANIFEST_PATHS = [
    "SAFE/",
    "safe-app-manifest.json",
    ".willow/",
    "manifest.json",
    "MANIFEST.md",
]

# Patterns that suggest cloud phone-home behavior
_CLOUD_PATTERNS = [
    re.compile(r'https?://.*\.amazonaws\.com', re.IGNORECASE),
    re.compile(r'https?://.*\.googleapis\.com', re.IGNORECASE),
    re.compile(r'mixpanel|segment\.io|amplitude|heap\.io|telemetry', re.IGNORECASE),
    re.compile(r'sentry\.io|bugsnag|rollbar', re.IGNORECASE),
    re.compile(r'analytics', re.IGNORECASE),
]

# Files to scan for cloud patterns
_SCAN_FILES = ["README.md", "package.json", "setup.py", "pyproject.toml", "Cargo.toml"]


def check(repo_full_name: str, auth_headers: dict) -> dict:
    """
    Check if a repo qualifies for LOSC membership.
    Returns a result dict with: qualified (bool), reasons (list), score (int 0-100).
    """
    reasons_for = []
    reasons_against = []

    # Check for manifest
    has_manifest = _check_manifest(repo_full_name, auth_headers)
    if has_manifest:
        reasons_for.append(f"has sovereignty manifest ({has_manifest})")
    else:
        reasons_against.append("no sovereignty manifest found")

    # Check for telemetry/cloud patterns in key files
    cloud_hits = _scan_for_cloud(repo_full_name, auth_headers)
    if cloud_hits:
        reasons_against.extend([f"possible cloud dependency: {h}" for h in cloud_hits])
    else:
        reasons_for.append("no obvious cloud phone-home patterns detected")

    # Check for a license (signals intent to share)
    has_license = _check_license(repo_full_name, auth_headers)
    if has_license:
        reasons_for.append(f"licensed ({has_license})")

    score = max(0, min(100, len(reasons_for) * 33 - len(reasons_against) * 20))
    qualified = has_manifest is not None and not cloud_hits

    return {
        "qualified": qualified,
        "score": score,
        "reasons_for": reasons_for,
        "reasons_against": reasons_against,
    }


def _check_manifest(repo_full_name: str, headers: dict) -> Optional[str]:
    """Return the manifest path found, or None."""
    for path in _MANIFEST_PATHS:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo_full_name}/contents/{path.rstrip('/')}",
                headers=headers, timeout=5,
            )
            if r.status_code == 200:
                return path
        except Exception:
            pass
    return None


def _scan_for_cloud(repo_full_name: str, headers: dict) -> list[str]:
    """Scan key files for cloud/telemetry patterns. Returns list of hits."""
    hits = []
    for filename in _SCAN_FILES:
        try:
            r = requests.get(
                f"https://raw.githubusercontent.com/{repo_full_name}/HEAD/{filename}",
                timeout=5,
            )
            if r.status_code != 200:
                continue
            content = r.text
            for pattern in _CLOUD_PATTERNS:
                match = pattern.search(content)
                if match:
                    hits.append(f"{filename}: {match.group(0)[:60]}")
                    break
        except Exception:
            pass
    return hits


def _check_license(repo_full_name: str, headers: dict) -> Optional[str]:
    """Return license name or None."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{repo_full_name}/license",
            headers=headers, timeout=5,
        )
        if r.status_code == 200:
            return r.json().get("license", {}).get("spdx_id", "unknown")
    except Exception:
        pass
    return None


def format_invite(repo_full_name: str, result: dict) -> str:
    """Format a LOSC invite comment."""
    if result["qualified"]:
        return (
            f"This fork looks local-first. "
            f"It may qualify for the [Local Only Servers Club](https://github.com/willow-bot/losc). "
            f"FRANK has noted it."
        )
    return ""
