"""An add-on idea issue answered with the list entry it would become."""

from __future__ import annotations

import json

import pytest

import router
from willow_bot import idea_entry

REPO = "willow-memory/Willow"

# What GitHub writes for Willow's idea form (.github/ISSUE_TEMPLATE/idea.yml).
FORM = """### Name

bank

### What it does, in one sentence

Bank CSV and OFX statements as dated lines, with spending by month.

### Section

Reading more kinds of files

### What it writes

to (only where --to points)

### Where it goes online

none

### What it needs installed, with its licence

_No response_

### Tags

_No response_"""


def test_the_form_becomes_the_entry_in_contributings_key_order():
    got = idea_entry.entry(FORM)
    assert list(got.items()) == [
        ("name", "bank"),
        ("kind", "stdlib"),
        ("section", "Reading more kinds of files"),
        ("line", "Bank CSV and OFX statements as dated lines, with spending by month."),
        ("source", "new"),
        ("writes", "to"),
        ("network", "none"),
    ]


def test_an_install_makes_it_deps_and_names_it():
    body = FORM.replace(
        "### What it needs installed, with its licence\n\n_No response_",
        "### What it needs installed, with its licence\n\nofxparse (MIT)",
    )
    got = idea_entry.entry(body)
    assert got["kind"] == "deps" and got["needs"] == "ofxparse (MIT)"


def test_tags_are_kept_when_given():
    body = FORM.replace("### Tags\n\n_No response_", "### Tags\n\nmoney, offline")
    assert idea_entry.entry(body)["tags"] == "money, offline"


@pytest.mark.parametrize(
    "old,new",
    [
        ("### Name\n\nbank", "### Name\n\nBank Statements"),  # not a name the list takes
        ("### Name\n\nbank", "### Name\n\n_No response_"),
        ("Reading more kinds of files", ""),
        ("to (only where --to points)", "everywhere"),
    ],
)
def test_no_entry_when_a_field_cant_be_one(old, new):
    assert idea_entry.entry(FORM.replace(old, new)) is None


def test_the_comment_holds_the_entry_as_json():
    body = idea_entry.comment(idea_entry.entry(FORM))
    assert body.startswith(idea_entry.MARKER)
    block = body.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(block)["name"] == "bank"


# ── wired into the issues handler ─────────────────────────────────────────


@pytest.fixture
def posts(monkeypatch):
    monkeypatch.setattr(router.fleet_bridge, "handle", lambda *a, **k: None)
    out = []
    return out, (lambda repo, number, body: out.append((repo, number, body)))


@pytest.fixture
def profile(monkeypatch, tmp_path):
    from willow_bot import operator_profile

    def _write(data: dict):
        path = tmp_path / "operator.json"
        path.write_text(json.dumps(data))
        monkeypatch.setenv(operator_profile.PROFILE_ENV, str(path))
        operator_profile.reset()

    yield _write
    operator_profile.reset()


def _opened(labels=("idea",), assoc="CONTRIBUTOR", typ="User", body=FORM, repo=REPO):
    return {
        "action": "opened",
        "repository": {"full_name": repo},
        "issue": {
            "number": 9,
            "body": body,
            "labels": [{"name": n} for n in labels],
            "author_association": assoc,
            "user": {"login": "ada", "type": typ},
        },
    }


def test_a_new_idea_issue_gets_its_entry(posts, profile):
    out, post = posts
    profile({"idea_entries": {"repos": [REPO]}})
    router.route("issues", _opened(), post)
    assert len(out) == 1 and out[0][:2] == (REPO, 9) and idea_entry.MARKER in out[0][2]


def test_a_first_timer_gets_the_entry_and_the_welcome(posts, profile):
    out, post = posts
    profile({"idea_entries": {"repos": [REPO]}, "welcome": {"orgs": ["willow-memory"]}})
    router.route("issues", _opened(assoc="FIRST_TIMER"), post)
    bodies = [b for _, _, b in out]
    assert len(bodies) == 2 and idea_entry.MARKER in bodies[0] and "@ada" in bodies[1]


@pytest.mark.parametrize(
    "event,prof",
    [
        (_opened(), {}),  # the profile names no repo
        (_opened(repo="someone/else"), {"idea_entries": {"repos": [REPO]}}),
        (_opened(labels=("bug",)), {"idea_entries": {"repos": [REPO]}}),
        (_opened(typ="Bot"), {"idea_entries": {"repos": [REPO]}}),
        (_opened(body="I have an idea but no form"), {"idea_entries": {"repos": [REPO]}}),
    ],
)
def test_no_entry(posts, profile, event, prof):
    out, post = posts
    profile(prof)
    router.route("issues", event, post)
    assert not any(idea_entry.MARKER in b for _, _, b in out)


# ── the language form (.github/ISSUE_TEMPLATE/language.yml) ───────────────

LANG_FORM = """### Language

Welsh (Cymraeg)

### Its code

cy

### Its common words

```text
a, y, yn
mae
'r
```

### What it does that English doesn't

Mutates the first letter of a word.

### Where the list comes from, and its licence

My own knowledge"""


def test_the_language_form_becomes_a_pack_entry():
    got = idea_entry.language_entry(LANG_FORM)
    assert got == {
        "name": "words-cy",
        "kind": "stdlib",
        "section": "Your own words",
        "line": 'Welsh common words ("a", "y", "yn").',
        "source": "new",
        "writes": "record",
        "network": "none",
        "tags": "no-install, offline, records, words, welsh",
    }


def test_a_name_that_cant_be_a_tag_leaves_the_tag_out():
    got = idea_entry.language_entry(LANG_FORM.replace("Welsh (Cymraeg)", "Español"))
    assert got["line"].startswith("Español common words") and got["tags"].endswith(", words")


@pytest.mark.parametrize(
    "old,new",
    [
        ("### Its code\n\ncy", "### Its code\n\nWelsh"),  # not a code
        ("### Its code\n\ncy", "### Its code\n\nc"),
        ("My own knowledge", "_No response_"),  # the source is required
        ("Welsh (Cymraeg)", ""),
    ],
)
def test_no_pack_entry_when_a_field_cant_be_one(old, new):
    assert idea_entry.language_entry(LANG_FORM.replace(old, new)) is None


def test_the_label_picks_the_form():
    assert idea_entry.entry_for({"language"}, LANG_FORM)["name"] == "words-cy"
    assert idea_entry.entry_for({"idea"}, FORM)["name"] == "bank"
    assert idea_entry.entry_for({"bug"}, FORM) is None
    assert "the source goes on its card" in idea_entry.comment(idea_entry.language_entry(LANG_FORM))
    assert "the source goes on its card" not in idea_entry.comment(idea_entry.entry(FORM))


def test_a_new_language_issue_gets_its_entry(posts, profile):
    out, post = posts
    profile({"idea_entries": {"repos": [REPO]}})
    router.route("issues", _opened(labels=("language",), body=LANG_FORM), post)
    assert len(out) == 1 and '"name": "words-cy"' in out[0][2]


def test_the_languages_tracking_issue_gets_nothing(posts, profile):
    out, post = posts
    profile({"idea_entries": {"repos": [REPO]}})
    tracking = "Willow should work for questions in your language.\n\n## Where things stand\n"
    router.route("issues", _opened(labels=("good first issue", "language"), body=tracking), post)
    assert not any(idea_entry.MARKER in b for _, _, b in out)
