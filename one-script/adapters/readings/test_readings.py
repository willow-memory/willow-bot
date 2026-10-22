"""The readings adapter: three hints beside a kernel bite, never in the kernel.

Its readings are held to the Odile demo's shapes (demo/odile/STRIP.md); the
kernel's own tests and vectors know nothing of them, and the last test here
holds the arrow one way: the kernel imports no adapter, and the adapter's bite
serves exactly what the kernel's does.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KERNEL = HERE.parents[1] / "kernel.py"
spec = importlib.util.spec_from_file_location("readings", HERE / "readings.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
k = r.kernel


def test_numbers_over_time_say_what_moved_and_never_judge(tmp_path):
    """Laszlo's care log (demo/odile): the latest week can sit under the
    cardiologist's 2 kg line while an earlier week crossed it, so both are
    said. A ledger repeats its dates and is never read as a series."""
    days = [("2026-10-0" + str(d) if d < 10 else f"2026-10-{d}") for d in range(1, 22)]
    kg = [71.2] * 8 + [71.3, 71.9, 72.3, 72.5, 72.8, 73.2, 73.4, 73.8, 74.0]
    kg += [74.3, 74.6, 75.0, 75.1]
    note = {"2026-10-16": '"ankles, puffy"'}
    log = "date,weight_kg,notes\n" + "".join(
        f"{d},{w:.1f},{note.get(d, '')}\n" for d, w in zip(days, kg)
    )
    assert r.series("dad/care-log.csv", log) == [
        {
            "why": "weight_kg: 75.1 on 2026-10-21, up 1.9 from 73.2 on 2026-10-14;"
            " the most in one week of the last 4: up 2.5, 2026-10-09 to 2026-10-16",
            "where": "dad/care-log.csv: 21 dated rows, 2026-10-01 to 2026-10-21",
        }
    ]
    ledger = "date,what,amount\n2026-10-01,a,5\n2026-10-01,b,6\n2026-10-09,c,7\n"
    assert r.series("books.csv", ledger) == []
    assert r.number("-0.30") == (-30, 2) and r.number("1.") is None
    assert r.day("20240307") - r.day("20240229") == 7


def test_a_date_gone_by_with_nothing_since_is_said(tmp_path):
    """Who's due (demo/odile, N1): the latest entry under each heading whose
    next date has passed, with nothing written under it since. A log kept a
    file a year is read as one."""
    (tmp_path / "tuning").mkdir()
    (tmp_path / "tuning" / "2025.txt").write_text(
        "2025-05-02  Ann - upright\n  next: May 2026\n\n"
        "2025-06-01  Bo - grand\n  next: Jan 2026\n"
    )
    (tmp_path / "tuning" / "2026.txt").write_text(
        "2026-02-10  Bo - grand\n  next: Feb 2027\n\n"
        "2026-03-01  Cy - console\n  next: Dec. 1, 2026\n"
    )
    pile = k.chunks(tmp_path)
    served = [c for c in pile if c["path"] == "tuning/2026.txt"]
    assert r.gone_by(pile, served, "20261022", {"next"}, {}) == [
        {
            "why": "gone by, nothing written since: Ann - upright (May 2026)",
            "where": "tuning/: 1 of 3 headings, as of 2026-10-22",
        }
    ]
    assert r.gone_by(pile, served, "20260430", {"next"}, {}) == []
    # said only where a date's own line holds the bite's words (or her list's)
    assert r.gone_by(pile, served, "20261022", {"tired"}, {}) == []
    assert r.gone_by(pile, served, "20261022", {"due"}, {"due": ["next"]})
    assert r.written("next: Oct 2027; March 20, 2026; Octo 2026") == [
        ("20271001", "Oct 2027"),
        ("20260320", "March 20, 2026"),
    ]


def test_the_arrow_points_one_way(tmp_path):
    """The kernel never names an adapter; with the readings on top, the same
    chunks are served and the kernel's own hints come first, unchanged."""
    assert "adapters" not in KERNEL.read_text().replace("adapters/readings", "")
    pile, box, mine = tmp_path / "pile", tmp_path / "box", tmp_path / "mine"
    (pile / "dad").mkdir(parents=True)
    (pile / "dad" / "care-log.csv").write_text(
        "date,weight_kg\n"
        + "".join(f"2026-10-{d:02d},{70 + d / 10:.1f}\n" for d in range(1, 22))
    )
    mine.mkdir(mode=0o700)
    b = ["--box", str(box), "--now", "2026-10-22T00:00:00Z"]
    init = [sys.executable, str(KERNEL), "init", *b, "--pile", str(pile)]
    assert subprocess.run([*init, "--key", str(mine / "k")]).returncode == 0
    plain = subprocess.run(
        [sys.executable, str(KERNEL), "bite", *b, "dad weight"],
        capture_output=True, text=True,
    )  # fmt: skip
    more = subprocess.run(
        [sys.executable, str(HERE / "readings.py"), "bite", *b, "dad weight"],
        capture_output=True, text=True,
    )  # fmt: skip
    a, z = json.loads(plain.stdout), json.loads(more.stdout)
    assert plain.returncode == more.returncode == 0
    assert a["served"] == z["served"]
    assert z["look"][: len(a.get("look", []))] == a.get("look", [])
    assert any(h["why"].startswith("weight_kg: 72.1 on 2026-10-21") for h in z["look"])
    assert not any("weight_kg" in h["why"] for h in a.get("look", []))


def test_hints_gathers_both_readings_and_never_what_isnt_there(tmp_path):
    """Numbers over time and a date gone by, each from what was served, all
    through `hints`, the one entry the bite uses. What isn't there was taken
    out after the blind runs, and stays out: an order the books don't name is
    never said."""
    (tmp_path / "log").mkdir()
    (tmp_path / "log" / "2026.txt").write_text("2026-01-02  Ann\n  next: May 2026\n")
    (tmp_path / "books.csv").write_text("".join(f"PO 2025-0{n}\n" for n in (10, 11)))
    (tmp_path / "cal.txt").write_text("PO 2025-010 PO 2025-011 PO 2025-012\n")
    (tmp_path / "po.txt").write_text("PO 2025-012 order\n")
    (tmp_path / "w.csv").write_text(
        "date,kg\n2026-10-01,70\n2026-10-08,71\n2026-10-15,72\n"
    )
    pile = k.chunks(tmp_path)
    served = [c for c in pile if c["path"] in ("po.txt", "log/2026.txt", "w.csv")]
    got = r.hints(pile, served, "next order kg", {}, "20261022")
    whys = " | ".join(h["why"] for h in got)
    assert "not in" not in whys
    assert "gone by, nothing written since: Ann (May 2026)" in whys
    assert "kg: 72 on 2026-10-15" in whys
