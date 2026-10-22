"""Odile Varga's life, as the files it leaves: python3 make_life.py [--check]

Seeded, so the same bytes every run. Writes `life/` (the folder her box is
built from) and `names.txt` (the people in it, for `--names`; kept outside
`life/`). `--check` reads the written files back and holds them to
PLANTED.md: each need's evidence is there, and each trap is too.

Fictional throughout. Phones are 555-01xx, mail is at example.com/.org/.net,
the card reader's address is in 192.0.2.0/24, and the card and bank numbers
are published test numbers.
"""

from __future__ import annotations

import csv
import io
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIFE = HERE / "life"
TODAY = date(2026, 10, 22)
START = date(2020, 11, 1)
MOVED_IN = date(2025, 8, 18)  # Laszlo, after the fall
VAN_SOLD = date(2021, 5, 14)
COUNTY_NOTICE = date(2026, 3, 20)
COUNTY_DAYS = 180  # reply window: ran out 2026-09-16
R = random.Random(1938)

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def phone(n: int) -> str:
    return f"(575) 555-01{n:02d}"


def d(x: date) -> str:
    return x.isoformat()


def add_months(x: date, n: int) -> date:
    m = x.month - 1 + n
    y = x.year + m // 12
    return date(y, m % 12 + 1, min(x.day, 28))


# --- people -----------------------------------------------------------------

FIRST = (
    "Harriet Marisol Wendell Bea Ignacio Loretta Amos Delphine Ruben Cordelia "
    "Hollis Pilar Everett Junia Matthias Nell Orrin Teodora Calvin Esme "
    "Sterling Ofelia Gus Ramona Ansel Imogen Dario Winifred Lyle Paz Thaddeus "
    "Greer Benedikt Lupe Merritt Ada Silas Renata Abel Vesna Cyrus Margit"
).split()
LAST = (
    "Weight Quintana Ashby Holloway Baca Tremaine Okafor Lindqvist Serrano "
    "Pettibone Arvizu Caldwell Montaño Fairweather Ostrowski Gallegos Thistle "
    "Bramley Esquibel Kowalczyk Vance Archuleta Whitlock Romero-Hale Duffy "
    "Sandoval Pruitt Marchetti Lucero Halvorsen Trujillo Beckwith Ybarra "
    "Strand Padilla Kerrigan Maestas Lindahl Chacon Weatherby Salcido Petrak"
).split()
PIANOS = [
    ("Steinway", "M", 1926),
    ("Baldwin", "Acrosonic spinet", 1962),
    ("Yamaha", "U1", 1988),
    ("Kawai", "K-300", 2015),
    ("Wurlitzer", "console", 1971),
    ("Mason & Hamlin", "A", 1919),
    ("Kimball", "studio upright", 1979),
    ("Story & Clark", "console", 1984),
    ("Yamaha", "C3", 2003),
    ("Chickering", "upright", 1908),
    ("Baldwin", "Hamilton studio", 1996),
    ("Everett", "studio", 1974),
    ("Samick", "console", 1999),
    ("Steinway", "K-52", 1981),
]
STREETS = "Alder Juniper Pinon Mesa Arroyo Cholla Ridge Railroad Sunset Yucca"
STREETS = STREETS.split()


def make_clients() -> list[dict]:
    out = []
    for i in range(len(FIRST)):
        make, model, year = PIANOS[i % len(PIANOS)]
        c = {
            "name": f"{FIRST[i]} {LAST[i]}",
            "piano": f"{make} {model} ({year})",
            "addr": f"{R.randint(100, 2400)} {STREETS[i % len(STREETS)]} St",
            "phone": phone(20 + i),
            "every": 6 if i % 5 == 0 else 12,
            "from": START + timedelta(days=R.randint(0, 420) if i > 30 else 0),
            "until": None,
            "cabinet": "",
        }
        if i in (12, 27, 35):  # moved away, or the piano went
            c["until"] = START + timedelta(days=R.randint(700, 1500))
        out.append(c)
    out[0]["piano"] = "Steinway M (1926)"  # T1: Mrs. Weight's Steinway
    out[0]["every"] = 6
    out[9]["cabinet"] = "Queen Anne cabinet"  # T4
    out[9]["piano"] = "Chickering upright (1908), Queen Anne cabinet"
    return out


DISTRICTS = {
    "Calloway Unified": {
        "short": "CUSD",
        "pianos": [
            "CHS choir room Yamaha U1",
            "CHS auditorium Kawai RX-2 grand",
            "CHS band room Baldwin studio",
            "CMS music room Kimball console",
            "CMS cafeteria Wurlitzer spinet",
            "Alder Elem. Yamaha P22",
            "Alder Elem. Baldwin Hamilton",
            "Juniper Elem. Everett studio",
            "Juniper Elem. Samick console",
        ],
        "rate": 135,
    },
    "Dry Creek SD": {
        "short": "DCSD",
        "pianos": [
            "DCHS choir Yamaha U3",
            "DCHS stage Young Chang grand",
            "DCMS music Baldwin Hamilton",
            "Dry Creek Elem. Kimball studio",
            "Mesa Elem. Yamaha P22",
            "Mesa Elem. Story & Clark console",
        ],
        "rate": 125,
    },
}
UNPAID = {("CUSD", "2026-02"), ("DCSD", "2025-08")}  # N4
CASH_PO = ("DCSD", "2023-02")  # T3: paid in cash, only a memo says so


# --- the tuning log ------------------------------------------------------------

WORK = [
    "pins tight",
    "pins a little loose in the tenor, held",
    "replaced a broken bass string (A1)",
    "eased two sticking keys",
    "damper felt worn, told them",
    "regulated the una corda",
    "voiced down the top octave, bright",
    "humidity 18%, recommended a Dampp-Chaser",
    "cracked soundboard, old, not getting worse",
    "glued a loose key top",
    "pedal squeak, lubed the trapwork",
    "hammers grooved, quoted a reshape",
    "mouse nest under the keybed, cleaned",
    "cat hair everywhere",
]


def visits_for(c: dict, overdue: bool) -> list[date]:
    out, at = [], c["from"] + timedelta(days=R.randint(0, 120))
    while at < TODAY:
        if c["until"] and at > c["until"]:
            break
        if overdue and at > date(2026, 6, 15):
            break
        out.append(at)
        at = add_months(at, c["every"]) + timedelta(days=R.randint(-20, 25))
    return out


def tuning(clients: list[dict]) -> tuple[list[dict], list[dict]]:
    """Every visit, and the visits not yet made (for the calendar)."""
    visits, booked = [], []
    overdue = {i for i in range(len(clients)) if i % 4 == 1}  # N1: due and not booked
    for i, c in enumerate(clients):
        days = visits_for(c, i in overdue)
        for at in days:
            before = -R.randint(2, 14) if c["every"] == 6 else -R.randint(4, 38)
            note = R.sample(WORK, R.choice((1, 1, 2)))
            if c["cabinet"] and R.random() < 0.5:
                note.append("the Queen Anne cabinet's veneer lifting on the left")
            nxt = add_months(at, c["every"])
            visits.append(
                {
                    "date": at,
                    "who": c["name"],
                    "where": c["addr"],
                    "piano": c["piano"],
                    "before": before,
                    "after": 0 if before > -25 else -3,
                    "note": note,
                    "next": f"{MONTHS[nxt.month - 1]} {nxt.year}",
                    "client": c,
                }
            )
        if days and i not in overdue and not c["until"]:
            nxt = add_months(days[-1], c["every"])
            if TODAY <= nxt <= TODAY + timedelta(days=40) and R.random() < 0.6:
                booked.append({"date": nxt, "who": c["name"], "where": c["addr"]})
    return visits, booked


def district_jobs() -> list[dict]:
    jobs, n = [], {"CUSD": 30, "DCSD": 11}
    for year in range(2021, 2027):
        for month in (2, 8):
            if date(year, month, 1) > TODAY:
                continue
            for name, dist in DISTRICTS.items():
                s = dist["short"]
                n[s] += R.randint(3, 9)
                po = f"{s} PO {year}-{n[s]:03d}"
                day = date(year, month, R.randint(3, 20))
                jobs.append(
                    {
                        "district": name,
                        "short": s,
                        "po": po,
                        "key": (s, f"{year}-{month:02d}"),
                        "date": day,
                        "pianos": dist["pianos"],
                        "rate": dist["rate"],
                        "amount": dist["rate"] * len(dist["pianos"]),
                    }
                )
    return jobs


def write_tuning(visits: list[dict], jobs: list[dict]) -> None:
    by_year: dict[int, list[str]] = {}
    rows = [(v["date"], "client", v) for v in visits] + [
        (j["date"], "district", j) for j in jobs
    ]
    for at, kind, x in sorted(rows, key=lambda r: (r[0], r[1])):
        if kind == "client":
            lines = [
                f"{d(at)}  {x['who']} - {x['piano']}, {x['where']}",
                f"  pitch before {x['before']:+d}c at A4, after {x['after']:+d}c",
                "  " + "; ".join(x["note"]),
                f"  next: {x['next']}",
            ]
        else:
            lines = [f"{d(at)}  {x['district']}, {x['po']} ({len(x['pianos'])})"]
            for p in x["pianos"]:
                lines.append(f"  {p}: {-R.randint(3, 22):+d}c -> 0")
            lines.append("  invoice sent")
        by_year.setdefault(at.year, []).append("\n".join(lines))
    for year, entries in by_year.items():
        head = f"TUNING LOG {year} (typed from the notebook)\n\n"
        write(f"tuning/{year}.txt", head + "\n\n".join(entries) + "\n")


# --- the hives ---------------------------------------------------------------


def write_hives() -> None:
    for year in range(2021, 2027):
        at, out = date(year, 3, 12), []
        while at <= date(year, 10, 20) and at < TODAY:
            out.append(f"== {d(at)}  {R.choice(['sunny', 'windy', 'hot', 'cool'])}")
            for h in range(1, 8):
                if h == 7 and at.toordinal() % 2:
                    continue  # the orchard hive, every other visit
                out.append("  " + hive_line(h, at))
            at += timedelta(days=R.randint(9, 15))
        write(f"hives/{year}.txt", f"HIVE NOTES {year}\n\n" + "\n".join(out) + "\n")


def hive_line(h: int, at: date) -> str:
    where = " (Petrak orchard)" if h == 7 else ""
    if h == 4 and at >= date(2026, 9, 14):  # N5: three in a row, queenless
        return (
            f"H4{where}: 7 fr bees, eggs none, brood none (all emerged), "
            "queen not seen, mites 0/300, loud when opened"
        )
    frames = R.randint(5, 10)
    eggs = "yes" if R.random() < 0.92 else "not seen"
    queen = "seen" if R.random() < 0.4 else "not seen"
    mites = R.choice((0, 1, 1, 2, 2, 3, 4, 6))
    extra = R.choice(("", "", "", "fed 1:1", "added a super", "swarm cells, cut"))
    line = (
        f"H{h}{where}: {frames} fr bees, eggs {eggs}, brood {R.randint(2, 7)} fr, "
        f"queen {queen}, mites {mites}/300"
    )
    return line + (f", {extra}" if extra else "")


# --- Laszlo ------------------------------------------------------------------


def write_dad() -> list[tuple[date, float]]:
    head = "Laszlo Varga  DOB 03/02/1938  MRN CV-4471902\n"
    meds = head + (
        "\nMEDS (from the discharge list, kept up)\n"
        "  furosemide 40 mg - morning\n"
        "  metoprolol succinate 50 mg - morning\n"
        "  lisinopril 10 mg - morning\n"
        "  spironolactone 25 mg - morning\n"
        "  atorvastatin 40 mg - night\n"
        "  aspirin 81 mg - morning\n"
        "  tamsulosin 0.4 mg - night\n"
        "  vitamin D 1000 IU - morning\n"
        "Pharmacy: Calloway Drug " + phone(90) + "\n"
    )
    write("dad/meds.txt", meds)
    write(
        "dad/cardiology-2026-07-30.txt",
        "MESA HEART ASSOCIATES - AFTER VISIT SUMMARY\n"
        + head
        + "Visit 2026-07-30, Dr. A. Kerrigan\n\n"
        "Heart failure with reduced ejection fraction, stable today.\n"
        "Weight 71.4 kg. BP 122/70. Continue current medicines.\n\n"
        "WEIGH EVERY MORNING after the bathroom, before breakfast.\n"
        "Call the office if weight goes up more than 2 kg (about 4-5 lb)\n"
        "in a week, or 1 kg overnight, or if ankles swell or he has to\n"
        "sleep sitting up.\n\n"
        "Office " + phone(91) + ". Next visit: January 2027.\n",
    )
    weights, rows, w, at = [], [], 70.6, MOVED_IN
    while at < TODAY:
        if date(2026, 10, 10) <= at:  # N2: the climb
            w = 72.0 + 0.29 * (at - date(2026, 10, 10)).days + R.uniform(-0.1, 0.1)
        elif date(2026, 2, 3) <= at <= date(2026, 2, 9):  # a blip, gone by itself
            w = 71.3 + 0.17 * (at - date(2026, 2, 3)).days
        else:
            w = 71.2 + 0.4 * ((at.toordinal() % 37) / 37) + R.uniform(-0.3, 0.3)
        w = round(w, 1)
        note = {
            date(2026, 10, 16): "ankles puffy, socks leave marks",
            date(2026, 10, 18): "slept in the recliner, said bed was too flat",
            date(2026, 10, 20): "didn't want lunch",
            date(2025, 12, 24): "Kati called him, good day",
            date(2026, 2, 6): "salty soup at the Petraks'",
        }.get(at, "")
        sys_, dia = R.randint(108, 134), R.randint(62, 78)
        rows.append([d(at), f"{w:.1f}", f"{sys_}/{dia}", R.randint(58, 72), note])
        weights.append((at, w))
        at += timedelta(days=1)
    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(["date", "weight_kg", "bp", "pulse", "notes"])
    out.writerows(rows)
    write("dad/care-log.csv", buf.getvalue())
    return weights


# --- money -------------------------------------------------------------------


def write_money(visits: list[dict], jobs: list[dict]) -> None:
    for j in jobs:
        body = [
            "ODILE VARGA PIANO SERVICE",
            "Calloway  " + phone(10) + "  odile.varga@example.net",
            "",
            f"INVOICE  {j['po']}    {d(j['date'])}",
            f"To: {j['district']}, Business Office",
            "",
        ]
        body += [f"  tune: {p:<40} ${j['rate']}" for p in j["pianos"]]
        body += ["", f"TOTAL ${j['amount']}", "Net 30. Thank you."]
        write(f"money/invoices/{j['po'].replace(' ', '-')}.txt", "\n".join(body) + "\n")

    sheet = [
        ["# TEO'S BOOKS. Calloway Federal account no. 000123456789,"],
        ["# routing 011000015. Card on file Visa 4111 1111 1111 1111 exp 09/27"],
        ["date", "what", "in", "out"],
    ]
    for j in jobs:
        if j["key"] in UNPAID or j["key"] == CASH_PO:
            continue
        paid = j["date"] + timedelta(days=R.randint(28, 62))
        if paid < TODAY:
            sheet.append([d(paid), f"DEP {j['short']} {j['po']}", j["amount"], ""])
    for v in visits:
        if R.random() < 0.55:  # the rest paid by card, or cash she never wrote
            sheet.append([d(v["date"]), f"check {v['who']}", R.choice((110, 120)), ""])
    at = date(2020, 11, 5)
    while at < TODAY:
        premium = 148 if at < date(2023, 1, 1) else 161
        sheet.append(
            [d(at), "MESA MUTUAL AUTO (Sienna + Econoline rider)", "", premium]
        )
        sheet.append([d(at), "CALLOWAY ELECTRIC COOP", "", R.randint(60, 140)])
        at = add_months(at, 1)
    body = sheet[:3] + sorted(sheet[3:], key=lambda r: r[0])
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(body)
    write("money/teo-books.csv", buf.getvalue())

    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(["# Square-ish reader export, device 192.0.2.17"])
    out.writerow(["date", "time", "amount", "card", "item"])
    for year in range(2021, 2027):
        at = date(year, 5, 2)
        while at.month <= 10 and at < TODAY:
            if at.weekday() == 5:
                for _ in range(R.randint(3, 11)):
                    t = f"{R.randint(8, 12):02d}:{R.randint(0, 59):02d}"
                    amt = R.choice((12, 12, 18, 18, 24, 30))
                    item = "honey 1lb" if amt == 12 else "honey"
                    card = f"**** {R.randint(1000, 9999)}"
                    out.writerow([d(at), t, amt, card, item])
            at += timedelta(days=1)
    for v in visits:
        if R.random() < 0.3:
            out.writerow([d(v["date"]), "16:10", 120, "**** 4242", "tuning"])
    write("money/card-reader.csv", buf.getvalue())


# --- mail --------------------------------------------------------------------


def write_mail(jobs: list[dict]) -> None:
    county = (
        "CALLOWAY COUNTY OFFICE OF THE WATER MASTER\n"
        "{when}\n\nOdile Varga\n1180 Juniper St\nCalloway\n\n"
        "RE: Case No. {case}\n\n{body}\n\nWater Master's Office " + phone(70) + "\n"
    )
    write(
        "mail/2024-04-02-county.txt",
        county.format(
            when="April 2, 2024",
            case="WR-2024-0390",
            body=(
                "Our records show a domestic well at the above address. Please\n"
                "confirm the well's use within 60 days on the enclosed form."
            ),
        ),
    )
    write(
        "mail/2024-04-30-county-reply-copy.txt",
        "copy of what I sent back, Apr 30 2024\n"
        "Case No. WR-2024-0390. Domestic use and a garden. Six beehives. "
        "Form signed.\n",
    )
    deadline = COUNTY_NOTICE + timedelta(days=COUNTY_DAYS)
    write(
        "mail/2026-03-20-county.txt",
        county.format(
            when="March 20, 2026",
            case="WR-2026-0117",
            body=(
                "NOTICE OF PROPOSED DETERMINATION OF WATER RIGHT\n\n"
                "The proposed determination limits the above well to in-house\n"
                "domestic use. Livestock, irrigation and apiary watering would\n"
                "need a separate permit. You may file a response or an\n"
                f"objection within {COUNTY_DAYS} days of the date of this notice.\n"
                "If no response is received, the proposed determination becomes\n"
                "final without further notice."
            ),
        ),
    )
    assert deadline == date(2026, 9, 16)
    write(
        "mail/2026-06-01-mesa-mutual-renewal.txt",
        "MESA MUTUAL INSURANCE - AUTO POLICY RENEWAL\n"
        "Policyholder: Odile Varga   Policy MM-88130-2\n\n"
        "Vehicles\n"
        "  2014 Toyota Sienna        liability, comprehensive   $112/mo\n"
        "  1998 Ford E-150 Econoline tools & equipment rider    $49/mo\n\n"
        "Total $161/mo, drafted from account no. 000123456789.\n",
    )
    for j in jobs:
        write(
            f"mail/{d(j['date'] - timedelta(days=12))}-{j['short'].lower()}-po.txt",
            f"{j['district'].upper()} - PURCHASE ORDER\n"
            f"{j['po']}\nVendor: Odile Varga Piano Service\n"
            f"Tune {len(j['pianos'])} pianos @ ${j['rate']}  = ${j['amount']}\n"
            "Remit invoice to the Business Office, ap@"
            f"{j['short'].lower()}.example.org\n",
        )
    write(
        "mail/2025-08-29-medicare-summary.txt",
        "MEDICARE SUMMARY NOTICE\nLaszlo Varga  DOB 03/02/1938\n"
        "Calloway Regional, inpatient 08/09/2025-08/15/2025, approved.\n",
    )


# --- texts and memos ---------------------------------------------------------


def write_texts(visits: list[dict]) -> int:
    lines, n = [], 0
    at = START
    while at < TODAY:
        if at.weekday() == 6:
            later = at >= MOVED_IN
            k = R.choice(
                ("how's Dad?", "how are you holding up", "call at 7?", "you ok?")
                if later
                else ("call at 7?", "saw the photo of the bees!", "how's work")
            )
            reply = (
                R.choice(("fine", "fine, tired", "fine. long week", "fine"))
                if later and at >= date(2026, 4, 1)
                else R.choice(("good, call at 7", "busy but good", "yes 7 works"))
            )
            lines += [f"{d(at)} 16:{R.randint(0, 59):02d}  Kati: {k}"]
            lines += [f"{d(at)} 17:{R.randint(0, 59):02d}  me: {reply}"]
            if later and R.random() < 0.3:
                lines += [
                    f"{d(at)} 19:{R.randint(0, 59):02d}  Kati: "
                    + R.choice(
                        (
                            "I still think a place would be better for both of you",
                            "did the cardiologist say anything new",
                            "you sound tired",
                            "I'll send money for the pharmacy",
                        )
                    )
                ]
        at += timedelta(days=1)
    lines.insert(
        5,
        "2020-11-29 19:40  Kati: new address! 4410 Larkspur Ct, Eugene OR 97401,"
        f" cell still {phone(92)}, kati.varga@example.com",
    )
    write("texts/kati.txt", "Messages with Kati (export)\n\n" + "\n".join(lines) + "\n")
    n += len(lines)
    lines = []
    for v in visits:
        if R.random() < 0.6:
            ask = v["date"] - timedelta(days=R.randint(3, 12))
            who = v["who"].split()[0]
            lines += [
                f"{d(ask)} 09:{R.randint(10, 59):02d}  {who} {v['client']['phone']}: "
                + R.choice(
                    (
                        "Hi Odile, is it time for the piano again?",
                        "Can you come next week? Any day but Weds",
                        "Piano's sounding sour, can you fit us in",
                    )
                ),
                f"{d(ask)} 12:{R.randint(10, 59):02d}  me: "
                + f"{MONTHS[v['date'].month - 1]} {v['date'].day} in the afternoon?",
                f"{d(ask)} 12:{R.randint(30, 59):02d}  {who}: perfect, thank you",
            ]
    write("texts/clients.txt", "Messages (export)\n\n" + "\n".join(lines) + "\n")
    return n + len(lines)


DAY_MEMOS = (
    "Note for the {w} piano, bring the long tuning hammer, the pins are tight.",
    "Order more bass strings, the A1s keep going.",
    "Market Saturday, need forty one-pound jars and labels.",
    "Remember to check mites on the home hives this week.",
    "Call the district about the fall schedule.",
    "Felt for the {w} dampers, Pianotek.",
)
NIGHT = (
    (
        "Dad's asleep. Did half the PO paperwork. Kati called again about a "
        "place, I said not yet. Hive four's been off, I need to look properly. "
        "And the county letter. I know. Tomorrow."
    ),
    "Can't sleep. He coughs in the night now. Did the weights. Did the pills.",
    "Long day. Two schools and Dad's pharmacy run.",
    "Tired.",
    "Okay.",
    "Can't sleep.",
)


def write_memos(visits: list[dict]) -> int:
    n = 0
    for v in visits:
        if R.random() < 0.18:
            t = datetime.combine(v["date"], datetime.min.time()) + timedelta(
                hours=R.randint(8, 16), minutes=R.randint(0, 59)
            )
            w = v["who"].split()[-1]
            memo(t, R.choice(DAY_MEMOS).format(w=w))
            n += 1
    memo(
        datetime(2021, 5, 14, 15, 2),
        "Sold Teo's van. The Econoline. To the Ruiz kid, Danny, thirty-two "
        "hundred, he's going to use it for his landscaping. Need to call Mesa "
        "Mutual and take it off the policy. Later. It was hard to watch it go.",
    )
    memo(
        datetime(2023, 3, 9, 20, 15),
        "Dry Creek paid the February PO in cash at the spring concert, the "
        "business manager just handed me an envelope, three-forty times six. "
        "It's in the honey tin. Put it in the book. Didn't.",
    )
    at = MOVED_IN + timedelta(days=10)
    while at < TODAY:
        late = (at - MOVED_IN).days / (TODAY - MOVED_IN).days
        if R.random() < 0.15 + 0.35 * late:
            i = min(len(NIGHT) - 1, int(late * len(NIGHT) + R.uniform(-1, 1)))
            t = datetime.combine(at, datetime.min.time()) + timedelta(
                hours=22 + R.randint(0, 2), minutes=R.randint(0, 59)
            )
            memo(t, NIGHT[max(0, i)])
            n += 1
        at += timedelta(days=1)
    return n + 2


def memo(t: datetime, text: str) -> None:
    write(f"memos/{t:%Y-%m-%d_%H%M}.txt", text + "\n")


# --- calendar ----------------------------------------------------------------


def write_calendar(visits: list[dict], booked: list[dict], jobs: list[dict]) -> None:
    ev = []
    for v in visits + booked:
        ev.append((v["date"], f"Tune - {v['who']}", v["where"]))
    for j in jobs:
        ev.append((j["date"], f"{j['short']} tunings, {j['po']}", j["district"]))
    at = MOVED_IN
    while at < TODAY + timedelta(days=60):
        if at.day == 1:
            ev.append((at, "Dad - pharmacy refills", "Calloway Drug"))
        at += timedelta(days=1)
    for x in (date(2025, 10, 2), date(2026, 1, 15), date(2026, 4, 23)):
        ev.append((x, "Dad - cardiology", "Mesa Heart"))
    ev.append((date(2026, 7, 30), "Dad - cardiology", "Mesa Heart"))
    ev.append((date(2027, 1, 21), "Dad - cardiology", "Mesa Heart"))
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//odile//phone//EN"]
    for i, (at, what, where) in enumerate(sorted(ev)):
        out += [
            "BEGIN:VEVENT",
            f"UID:{i}@odile.example.net",
            f"DTSTART;VALUE=DATE:{at:%Y%m%d}",
            f"SUMMARY:{what}",
            f"LOCATION:{where}",
            "END:VEVENT",
        ]
    out.append("END:VCALENDAR")
    write("calendar.ics", "\n".join(out) + "\n")


# --- writing and checking ----------------------------------------------------


def write(rel: str, text: str) -> None:
    p = LIFE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="")


def build() -> dict:
    if LIFE.exists():
        for p in sorted(LIFE.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    clients = make_clients()
    visits, booked = tuning(clients)
    jobs = district_jobs()
    write_tuning(visits, jobs)
    write_hives()
    write_dad()
    write_money(visits, jobs)
    write_mail(jobs)
    texts = write_texts(visits)
    memos = write_memos(visits)
    write_calendar(visits, booked, jobs)
    names = sorted(
        {c["name"] for c in clients}
        | {
            "Odile Varga",
            "Laszlo Varga",
            "Kati Varga",
            "Teo Varga",
            "Danny Ruiz",
            "A. Kerrigan",
        }
    )
    (HERE / "names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
    return {
        "visits": len(visits) + sum(len(j["pianos"]) for j in jobs),
        "client visits": len(visits),
        "district jobs": len(jobs),
        "texts": texts,
        "memos": memos,
        "files": sum(1 for p in LIFE.rglob("*") if p.is_file()),
        "bytes": sum(p.stat().st_size for p in LIFE.rglob("*") if p.is_file()),
    }


def check() -> list[str]:
    """PLANTED.md, read back from the files. Each line that fails is returned."""
    bad = []

    def need(ok: bool, what: str) -> None:
        if not ok:
            bad.append(what)

    log = list(csv.DictReader((LIFE / "dad/care-log.csv").open()))
    w = {date.fromisoformat(r["date"]): float(r["weight_kg"]) for r in log}
    last = max(w)
    week = max(
        w[x] - w[x - timedelta(days=7)]
        for x in w
        if x - timedelta(7) in w and x > last - timedelta(days=31)
    )
    need(week > 2.0, f"N2: the last month's worst 7-day gain is {week:.1f} kg")
    need(
        "more than 2 kg" in (LIFE / "dad/cardiology-2026-07-30.txt").read_text(),
        "N2: the cardiologist's rule",
    )
    older = max(
        w[x] - w[x - timedelta(days=7)]
        for x in w
        if x - timedelta(7) in w and x <= last - timedelta(days=31)
    )
    need(older <= 2.0, f"N2: an older week already gained {older:.1f} kg")

    notice = (LIFE / "mail/2026-03-20-county.txt").read_text()
    need(f"within {COUNTY_DAYS} days" in notice, "N3: the window")
    need(
        not any("WR-2026-0117" in p.read_text() for p in LIFE.rglob("*reply*")),
        "N3: a reply to the 2026 notice exists",
    )
    need((LIFE / "mail/2024-04-30-county-reply-copy.txt").exists(), "T2")

    books = (LIFE / "money/teo-books.csv").read_text()
    pos = [p.read_text().splitlines()[1] for p in LIFE.glob("mail/*-po.txt")]
    unpaid = [po for po in pos if po not in books]
    memos = " ".join(p.read_text() for p in LIFE.glob("memos/*.txt"))
    need(len(unpaid) == 3, f"N4/T3: {len(unpaid)} POs have no deposit: {unpaid}")
    need("cash at the spring concert" in memos, "T3: the cash memo")

    h4 = [
        ln
        for ln in (LIFE / "hives/2026.txt").read_text().splitlines()
        if ln.strip().startswith("H4")
    ]
    need(all("eggs none" in x for x in h4[-3:]), "N5: last three H4 queenless")
    need(not any("eggs none" in x for x in h4[:-3]), "N5: queenless too early")
    tun = " ".join(p.read_text() for p in LIFE.glob("tuning/*.txt"))
    need("Queen Anne" in tun, "T4")
    need("Harriet Weight" in tun, "T1")

    renewal = (LIFE / "mail/2026-06-01-mesa-mutual-renewal.txt").read_text()
    need("Econoline" in renewal and "Sold Teo's van" in memos, "N6")

    night = sorted(p for p in LIFE.glob("memos/*.txt") if p.stem[-4:] >= "2200")
    half = len(night) // 2
    early = sum(len(p.read_text()) for p in night[:half]) / max(half, 1)
    late = sum(len(p.read_text()) for p in night[half:]) / max(len(night) - half, 1)
    need(late < early, f"N7: night memos aren't shorter ({early:.0f} -> {late:.0f})")
    kati = (LIFE / "texts/kati.txt").read_text()
    need(kati.count("me: fine") > 10, "N7: the 'fine's")

    due = [ln for ln in tun.splitlines() if ln.strip().startswith("next:")]
    need(len(due) > 100, "N1: next lines")
    return bad


def main(argv: list[str]) -> int:
    if "--check" not in argv:
        for k, v in build().items():
            print(f"{k}: {v}")
    bad = check()
    for b in bad:
        print("PLANTED, NOT FOUND:", b)
    print("planted: all present" if not bad else f"planted: {len(bad)} missing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
