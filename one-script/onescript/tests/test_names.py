"""The names a person gives, withheld from everything served (IDEAS 119):
`Config.names`, read by `opening.read_names` from a file outside the box, and
applied last by serve and by the prompt hook."""

# ruff: noqa: F811
from __future__ import annotations

import dataclasses
import re

from onescript import api, chunk, draw, opening
from onescript.tests.test_draw import R, served_pair
from onescript.tests.test_seed import box, cfg  # noqa: F401


def test_a_given_name_never_reaches_the_served_file(box):
    # Served tables come in served-id order, keyed fresh each check-in, so the
    # two serves are compared as a whole, never table by table.
    draw.draw(box["cfg"], R)
    _, plain = served_pair(box["cfg"])
    text = "\n".join(t["rows"][0]["text"] for t in plain)
    word = max(sorted(re.findall(r"[A-Za-z]{4,}", text)), key=len)  # served now

    c = dataclasses.replace(box["cfg"], names=(word,))
    record = c.box / "record.jsonl"
    draw.draw(c, R)
    _, tables = served_pair(c)
    served = "\n".join(t["rows"][0]["text"] for t in tables)
    assert "[withheld: name, L3]" in served
    assert not re.search(rf"(?i)(?<!\w){word}(?!\w)", served)
    on_disk = (c.box / "served.json").read_text(encoding="utf-8")
    assert not re.search(rf"(?i)(?<!\w){word}(?!\w)", on_disk)
    # the record holds places and hashes, never the served text it withheld from
    assert text not in record.read_text()


def test_no_names_changes_nothing():
    doc = {"tables": [{"rows": [{"text": "Nieves Aguirre"}]}]}
    assert chunk.withhold_names(doc, ()) is doc


def test_the_names_file_is_the_persons_own(tmp_path):
    box = tmp_path / "box"
    box.mkdir()
    f = tmp_path / "names.txt"
    f.write_text("Nieves Aguirre\n# a comment\n\nBig Jim  # the seller\n")
    f.chmod(0o600)
    assert opening.read_names(f, box) == (("Nieves Aguirre", "Big Jim"), None)
    assert opening.read_names(None, box) == ((), None)
    f.chmod(0o644)
    assert "chmod 600" in opening.read_names(f, box)[1]
    inside = box / "names.txt"
    inside.write_text("x\n")
    inside.chmod(0o600)
    assert "inside the box" in opening.read_names(inside, box)[1]
    assert api.Config is opening.Config and "names" in {
        f.name for f in dataclasses.fields(opening.Config)
    }
