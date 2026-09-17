"""
horoscope.py — deterministic daily horoscope, keyed on login + UTC date.
Same login, same day, same reading. Always.
"""
import hashlib
from datetime import datetime, timezone

_TEMPLATES = [
    "a variable you named in haste will forgive you",
    "the pull request you are avoiding is not going anywhere",
    "your tests know something you don't",
    "today is not the day for a rebase",
    "the bug is not where you think it is",
    "a comment you wrote six months ago is about to be read aloud",
    "the merge conflict is smaller than it looks",
    "someone will approve your PR without reading it, and that is fine",
    "the log you need is the one you did not think to add",
    "a dependency will update itself without asking",
    "the meeting could have been a commit message",
    "your CI will pass on the third try, not the first",
    "the branch you deleted was the one you needed",
    "silence from the reviewer is not agreement",
    "the config file is lying to you, gently",
    "today favors small diffs",
    "a typo will cost you forty minutes and teach you nothing",
    "the thing you refactored will not be touched again for a year",
    "your instincts about the flaky test are correct",
    "the documentation is wrong, but only slightly",
    "a stale branch will outlive you",
    "the fix is one line, but finding it is not",
    "today is a good day to read the error message fully",
    "the code review will be kinder than you expect",
    "your commit history will be judged, quietly",
    "the feature flag you forgot about is still on",
    "a coworker will ask a question you asked yourself last week",
    "the deploy will go smoothly, which will worry you",
    "today rewards the person who reads the diff twice",
    "the ticket is more finished than the ticket admits",
]


def reading(login: str, date: str | None = None, title: str = "") -> str:
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    digest = hashlib.sha256(f"{login}:{date}".encode()).hexdigest()
    idx = int(digest[:8], 16) % len(_TEMPLATES)
    line = _TEMPLATES[idx]

    who = title.capitalize() if title else ""
    prefix = f"Today, {who}" if who else "Today"
    return f"_{prefix}: {line}._"
