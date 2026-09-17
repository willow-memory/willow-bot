"""
runes.py — rune casting for willow-bot.
"""

_RUNES = [
    ("ᚠ", "Fehu", "wealth. Wealth is a lie you tell yourself in changelogs."),
    ("ᚢ", "Uruz", "strength. It will not fix your test coverage."),
    ("ᚦ", "Thurisaz", "a thorn. Something in this diff will draw blood, eventually."),
    ("ᚨ", "Ansuz", "a message from the gods. Read the error log anyway."),
    ("ᚱ", "Raidho", "a journey. The road is CI, and it is long."),
    ("ᚲ", "Kenaz", "a torch. It illuminates exactly one bug, not all of them."),
    ("ᚷ", "Gebo", "a gift. Gifts come with dependencies."),
    ("ᚹ", "Wunjo", "joy, but check your imports."),
    ("ᚺ", "Hagalaz", "hail. Something upstream is about to break."),
    ("ᚾ", "Nauthiz", "need. You needed a review. You did not request one."),
    ("ᛁ", "Isa", "ice. Nothing moves until someone approves this."),
    ("ᛃ", "Jera", "harvest. Reap what last quarter's tech debt sowed."),
    ("ᛇ", "Eihwaz", "the yew. Old code endures. Do not disturb its roots."),
    ("ᛈ", "Perthro", "the lot cup. The outcome was decided before you opened this PR."),
    ("ᛉ", "Algiz", "protection. Also: your linter is right."),
    ("ᛋ", "Sowilo", "the sun. Everything looks fine in daylight. Check the logs at night."),
    ("ᛏ", "Tiwaz", "justice. Someone sacrifices a hand. Probably to a merge conflict."),
    ("ᛒ", "Berkano", "new growth. It will need pruning by Friday."),
    ("ᛖ", "Ehwaz", "the horse. Progress, but only if you trust what's pulling it."),
    ("ᛗ", "Mannaz", "mankind. You wrote this. You will maintain this."),
    ("ᛚ", "Laguz", "water. It flows around obstacles instead of fixing them."),
    ("ᛜ", "Ingwaz", "fertility. This PR will spawn three more."),
    ("ᛞ", "Dagaz", "dawn. A breakthrough, or just Tuesday."),
    ("ᛟ", "Othala", "inheritance. You have inherited this codebase. Condolences."),
]


def cast(sha: str) -> str:
    try:
        idx = int(sha[:8], 16) % len(_RUNES)
    except (ValueError, TypeError):
        idx = 0
    glyph, name, reading = _RUNES[idx]
    return f"> {glyph} **{name}** — {reading}"
