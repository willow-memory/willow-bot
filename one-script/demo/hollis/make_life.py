#!/usr/bin/env python3
"""Write the files Hollis Varga's life has left on his computer into ./life/.

Deterministic: fixed seed, fixed dates, no clock reads. Today is 2026-10-22.

    python3 make_life.py          # (re)write life/
    python3 make_life.py --check  # confirm each planted need's evidence is present
"""
import csv
import io
import math
import os
import random
import re
import shutil
import sys
from datetime import date, timedelta

SEED = 19720614
TODAY = date(2026, 10, 22)
START = date(2021, 3, 1)
END = date(2026, 10, 21)
HERE = os.path.dirname(os.path.abspath(__file__))
LIFE = os.path.join(HERE, "life")
R = random.Random(SEED)
FILES = {}

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ME = "Hollis Varga <hollis.varga@example.com>"


def W(path, text):
    assert path not in FILES, path
    if not text.endswith("\n"):
        text += "\n"
    FILES[path] = text


def D(s):
    y, m, d = map(int, s.split("-"))
    return date(y, m, d)


def days(a, b):
    x = a
    while x <= b:
        yield x
        x += timedelta(days=1)


def money(c):
    return f"{c / 100:.2f}"


def csv_text(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def rfc(dt, hh, mm):
    tz = "-0400" if 4 <= dt.month <= 10 else "-0500"
    return f"{WD[dt.weekday()]}, {dt.day:02d} {MONTHS[dt.month - 1]} {dt.year} {hh:02d}:{mm:02d}:00 {tz}"


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s[:40]


MAIL = []


def mail(dt, hh, mm, frm, to, subj, body, folder="inbox", name=None):
    if isinstance(dt, str):
        dt = D(dt)
    MAIL.append((dt, hh, mm, frm, to, subj, body.strip("\n"), folder, name))


def weekday_on_or_after(dt, allow_sat=False):
    while dt.weekday() == 6 or (dt.weekday() == 5 and not allow_sat):
        dt += timedelta(days=1)
    return dt


# ---------------------------------------------------------------- places, people
TOWNS = {"Larkfield": 3, "Coldbrook": 6, "Harmon Ridge": 9, "Pine Hatch": 11,
         "Ashby Mills": 14, "Wrenhaven": 18, "Saltmarsh": 22}
TOWN_W = ["Larkfield"] * 6 + ["Coldbrook"] * 3 + ["Harmon Ridge"] * 3 + ["Pine Hatch"] * 2 + \
         ["Ashby Mills"] * 2 + ["Wrenhaven"] + ["Saltmarsh"]
DAD_MILES = 76
NELL_MILES = 342

FIRST = ["Agnes", "Barnaby", "Cormac", "Delphine", "Ezra", "Fenella", "Gideon", "Hester", "Ivo",
         "Juniper", "Lorcan", "Mabel", "Niall", "Odalys", "Percival", "Rosalind", "Silas", "Thea",
         "Ulric", "Wilhelmina", "Yvette", "Zebulon", "Marisol", "Tobin", "Greer", "Anouk", "Bram",
         "Clemency", "Dorian", "Edda", "Fitz", "Golda", "Hamish", "Ines", "Jory", "Kesia"]
LAST = ["Ashgrove", "Brecken", "Calloway", "Dunmore", "Elsworth", "Fairbairn", "Grizzard",
        "Hollowell", "Inchley", "Jessop", "Kettering", "Loveridge", "Merriweather", "Nethercott",
        "Ottley", "Pendry", "Quarles", "Rookwood", "Strand", "Thistlewood", "Upham", "Vane",
        "Wexley", "Yarrow", "Bellweather", "Corrigan", "Dabney", "Feeney", "Gorst", "Halloran"]
INSTR = ["Yamaha U1 upright", "Yamaha U3 upright", "Kawai K-300 upright", "Baldwin Acrosonic spinet",
         "Baldwin Hamilton studio", "Wurlitzer console", "Kimball console", "Young Chang G-157 grand",
         "Steinway M grand", "Mason & Hamlin A grand", "Story & Clark console", "Boston UP-118",
         "Kohler & Campbell spinet", "Samick SU-118", "Everett studio", "Yamaha GB1 grand",
         "Hamilton upright (1920s)", "Chickering upright (1910s)", "Estonia 168 grand"]

CLIENTS = []
_seen = []
while len(CLIENTS) < 140:
    n = f"{R.choice(FIRST)} {R.choice(LAST)}"
    if n in _seen:
        continue
    _seen.append(n)
    freq = 2 if R.random() < 0.4 else 1
    m1 = R.randint(1, 12)
    months = [m1] if freq == 1 else sorted([m1, (m1 + 5) % 12 + 1])
    a = 2021 if R.random() < 0.75 else R.randint(2022, 2025)
    b = 2026 if R.random() < 0.85 else R.randint(max(a, 2022), 2025)
    CLIENTS.append({"name": n, "town": R.choice(TOWN_W), "instr": R.choice(INSTR),
                    "months": months, "from": a, "to": b})
CLIENTS.append({"name": "Aldous Pell", "town": "Ashby Mills", "instr": "Steinway M grand (1962)",
                "months": [4, 10], "from": 2021, "to": 2026})

INST = {
    "stb": dict(name="St. Brendan's Church", town="Larkfield", months=[2, 5, 9, 12], base=26000,
                instr="Baldwin SD-10 (sanctuary) + Kimball console (parish hall)",
                bill="St. Brendan's Church, Parish Office, attn: Fr. Colm Ardagh", code="ST BRENDANS PARISH"),
    "hrs": dict(name="Harmon Ridge School", town="Harmon Ridge", months=[1, 8], base=39000,
                instr="Yamaha U1 x2 (music rm, gym) + Kawai GE-30 (auditorium)",
                bill="Harmon Ridge School District, Accounts Payable", code="HARMON RIDGE SD"),
    "lma": dict(name="Larkfield Music Academy", town="Larkfield", months=[1, 6, 9], base=96000,
                instr="Studios 1-8 (Yamaha U1/U3, Boston UP-118)",
                bill="Larkfield Music Academy, attn: Bettina Saar", code="LARKFIELD MUSIC ACAD"),
    "lct": dict(name="Larkfield Community Theatre", town="Larkfield", months=[3, 10], base=28000,
                instr="Wurlitzer studio upright (pit) + Yamaha C3 (rehearsal)",
                bill="Larkfield Community Theatre, attn: Gil Marchetti, Treasurer", code="LARKFIELD COMM THEATRE"),
    "civ": dict(name="Larkfield Civic Hall", town="Larkfield", months=[1, 3, 4, 10, 11, 12], base=22000,
                instr="Steinway D (stage)", bill="Larkfield Civic Hall, Events Office, attn: Marta Quill",
                code="LARKFIELD CIVIC HALL"),
    "bk": dict(name="The Blue Kettle", town="Coldbrook", months=[4, 11], base=15000,
               instr="Kawai K-300 upright (bar)", bill="The Blue Kettle, attn: Rory Feeney", code="BLUE KETTLE LLC"),
}
THEATRE_SPECIAL = D("2026-05-28")
THEATRE_INV_DATE = D("2026-05-30")
CIVIC_RECITAL = D("2026-05-16")


def fee_for(year):
    return {2021: 12500, 2022: 13000, 2023: 13500, 2024: 14000, 2025: 14500, 2026: 15000}[year]


def callback_p(dt):
    if dt.year <= 2022:
        return 0.015
    if dt.year == 2023:
        return 0.02
    if dt.year == 2024:
        return 0.025
    if dt.year == 2025:
        return 0.03 if dt.month <= 6 else 0.08
    return 0.22


NOTES_EARLY = ["", "", "", "", "", "aural", "pins firm", "freed sticky key {k}", "replaced broken string {k}",
               "room humidity high, suggested a dehumidifier", "mouse nest in bottom board, cleaned out",
               "damper buzz on {k} fixed", "new owner, inherited it from an aunt", "cat sat on the lid the whole time",
               "wants a quote for regulation", "kids practicing a lot, says mom", "pitch held well since last time",
               "loose hinge pin on fallboard, tightened", "tea and shortbread", "piano against outside wall, moved 6in"]
NOTES_LATE = ["ETD above C6", "checked top octave w/ ETD", "strobe on treble", "rechecked treble unisons twice",
              "top section took longer than it should"]
RETURN_EARLY = ["string slipped after pitch raise, retouched", "client moved piano, retuned unisons",
                "new string settled flat, pulled up"]
RETURN_LATE = ["client: high notes sound sour", "top octave beating, per client", "C7-C8 redone",
               'client said treble sounds "wobbly"', "redid unisons above C6",
               "teacher noticed treble out within days", "retouched treble unisons, no charge"]
KEYS = ["A0", "C2", "F#2", "Bb2", "D3", "E3", "G#3", "C4", "F4", "A4", "D5", "G5", "B5", "E6"]

# ---------------------------------------------------------------- jobs
JOBS = []


def add_job(dt, client, town, instr, kind, inst=None, fee=None, note=""):
    JOBS.append(dict(date=dt, client=client, town=town, instr=instr, kind=kind, inst=inst, fee=fee, note=note))


for y in range(2021, 2027):
    for c in CLIENTS:
        if not (c["from"] <= y <= c["to"]):
            continue
        for m in c["months"]:
            dt = weekday_on_or_after(date(y, m, R.randint(1, 26)), allow_sat=R.random() < 0.3)
            if START <= dt <= END:
                add_job(dt, c["name"], c["town"], c["instr"], "private")
    for key in sorted(INST):
        inf = INST[key]
        months = list(inf["months"])
        if key == "civ" and y == 2026:
            months = [1, 3, 4, 10]
        for m in months:
            if key == "lct" and y == 2026 and m == 10:
                continue  # not booked this autumn
            dt = weekday_on_or_after(date(y, m, R.randint(2, 24)))
            if START <= dt <= END:
                add_job(dt, inf["name"], inf["town"], inf["instr"], "inst", inst=key)
        if key == "civ":
            extra = [date(y, 5, 14)] if y < 2026 else [CIVIC_RECITAL]
            for dt in extra:
                if START <= dt <= END:
                    add_job(dt, inf["name"], inf["town"], inf["instr"], "inst", inst=key)
add_job(THEATRE_SPECIAL, INST["lct"]["name"], "Larkfield", INST["lct"]["instr"], "inst", inst="lct",
        fee=64000, note="pit flooded in April; pitch raise + partial regulation on Wurlitzer, 2 bass strings")

# one-off new clients
for i in range(60):
    dt = weekday_on_or_after(START + timedelta(days=R.randint(0, (END - START).days - 5)))
    n = f"{R.choice(FIRST)} {R.choice(LAST)}"
    add_job(dt, n, R.choice(TOWN_W), R.choice(INSTR), "private", note="new client, one-off")

JOBS.sort(key=lambda j: (j["date"], j["kind"], j["client"]))

# callbacks / returns
returns = []
for j in JOBS:
    j["callback"] = ""
    if j["kind"] == "return":
        continue
    if R.random() < callback_p(j["date"]):
        rd = weekday_on_or_after(j["date"] + timedelta(days=R.randint(3, 12)))
        if rd > END:
            continue
        j["callback"] = "Y"
        late = j["date"] >= date(2025, 7, 1)
        returns.append(dict(date=rd, client=j["client"], town=j["town"], instr=j["instr"], kind="return",
                            inst=j["inst"], fee=0, note=R.choice(RETURN_LATE if late else RETURN_EARLY), callback=""))
JOBS.extend(returns)
JOBS.sort(key=lambda j: (j["date"], j["kind"], j["client"]))

# fill in fees, notes, payment
for j in JOBS:
    y = j["date"].year
    j["cents"] = R.choice([-2, -3, -4, -5, -6, -8, -10, -12, -14, -18, -25, -35, 1, 2])
    late = j["date"] >= date(2025, 7, 1)
    j["minutes"] = R.randint(65, 95) + (14 if y == 2026 else 0) + (5 if late and y == 2025 else 0)
    if j["kind"] == "return":
        j["work"] = "return visit"
        j["fee"] = 0
        j["pay"] = "n/c"
        j["minutes"] = R.randint(25, 55)
        j["cents"] = ""
        continue
    if j["kind"] == "inst":
        inf = INST[j["inst"]]
        j["work"] = "service visit"
        if j["fee"] is None:
            j["fee"] = int(round(inf["base"] * (1 + 0.03 * (y - 2021)) / 500.0)) * 500
        j["minutes"] = {"stb": 150, "hrs": 200, "lma": 420, "lct": 160, "civ": 120, "bk": 80}[j["inst"]] + \
            R.randint(-10, 20) + (25 if y == 2026 else 0)
        j["pay"] = "invoice"
        if j["inst"] == "civ" and j["date"] == CIVIC_RECITAL:
            j["note"] = "Rennick recital 2pm. In at 8, D ready 11:40, stayed for interval touch-up"
            j["minutes"] = 330
        if j["date"] == THEATRE_SPECIAL:
            j["minutes"] = 360
            j["cents"] = -38
        continue
    big = abs(j["cents"]) >= 15
    j["work"] = "pitch raise + tuning" if big else "tuning"
    j["fee"] = fee_for(y) + (6000 if big else 0)
    if not j["note"]:
        if j["client"] == "Aldous Pell":
            j["note"] = "bright, as he likes it"
        else:
            pool = NOTES_EARLY + (NOTES_LATE * 2 if late else [])
            j["note"] = R.choice(pool).format(k=R.choice(KEYS))
            if "string" in j["note"] or "buzz" in j["note"] or "sticky" in j["note"]:
                j["fee"] += R.choice([2000, 2500, 3500])
    j["pay"] = R.choices(["check", "cash", "p2p"], [6, 3, 1])[0]

# invoices
INVOICES = []
seq = {}
for j in JOBS:
    if j["kind"] != "inst":
        continue
    y = j["date"].year
    seq[y] = seq.get(y, 0) + 1
    num = f"INV-{y}-{seq[y]:03d}"
    j["pay"] = num
    idate = THEATRE_INV_DATE if j["date"] == THEATRE_SPECIAL else j["date"] + timedelta(days=R.randint(0, 2))
    INVOICES.append(dict(num=num, job=j, date=idate))
THEATRE_INV = [i for i in INVOICES if i["job"]["date"] == THEATRE_SPECIAL][0]["num"]

for inv in INVOICES:
    j = inv["job"]
    inf = INST[j["inst"]]
    lines = []
    if j["date"] == THEATRE_SPECIAL:
        lines = [("Pitch raise, Wurlitzer studio upright (pit)", 14000),
                 ("Partial regulation after water damage (Wurlitzer): let-off, repetition, 14 swollen keys eased", 26000),
                 ("Tuning, Yamaha C3 (rehearsal room)", 15000),
                 ("Replace 2 bass strings (Wurlitzer, A1, Bb1), incl. strings", 9000)]
    else:
        lines = [(f"Tuning and service: {inf['instr']}", j["fee"])]
    total = sum(a for _, a in lines)
    j["fee"] = total
    body = ["VARGA PIANO SERVICE", "18 Cobble Lane, Larkfield", "555-0142  hollis.varga@example.com", "",
            f"INVOICE {inv['num']}", f"Date: {inv['date'].isoformat()}", f"Service date: {j['date'].isoformat()}",
            "", "Bill to:", f"  {inf['bill']}", ""]
    for desc, amt in lines:
        body.append(f"  {desc:<92} ${money(amt):>8}")
    body += ["", f"  {'TOTAL DUE':<92} ${money(total):>8}", "",
             "Terms: Net 30. Checks payable to Hollis Varga.", "Thank you!"]
    W(f"work/invoices/{inv['num']}.txt", "\n".join(body))

# tuning logs
for y in range(2021, 2027):
    rows = [["date", "client", "town", "instrument", "pitch_found_cents", "work", "minutes", "fee", "paid",
             "callback", "notes"]]
    for j in JOBS:
        if j["date"].year != y:
            continue
        rows.append([j["date"].isoformat(), j["client"], j["town"], j["instr"], j["cents"], j["work"],
                     j["minutes"], money(j["fee"]), j["pay"], j["callback"], j["note"]])
    W(f"work/tuning_log_{y}.csv", csv_text(rows))

# client notes (incl. trap: Pell)
cn = ["CLIENT NOTES (the ones worth remembering)", "Last tidied: Feb 2025", ""]
picks = R.sample(range(140), 22)
quirks = ["Dog is loud but harmless. Park on the street, not the drive.",
          "Pays in cash, exact, in an envelope with my name on it.",
          "Wants a call the day before, not a text.", "Piano is in the basement. Damp. Keep pushing the dehumidifier.",
          "Daughter takes lessons at the Academy; teacher is fussy about the A.",
          "Gate code 4471. Do not let the cat out.", "Tea, too strong. Accept it anyway.",
          "Asked about selling it twice. Doesn't really want to.",
          "Hammers grooved, quoted reshaping 2023, declined.", "Retired organist. Will tell you which unisons are off.",
          "Keeps the heat at 58 in winter. Pitch drops every Jan.", "Lovely people. Always offers lunch."]
for i in sorted(picks):
    c = CLIENTS[i]
    cn.append(f"{c['name']} ({c['town']}) - {c['instr']}. {R.choice(quirks)}")
cn.append("Aldous Pell (Ashby Mills) - 1962 Steinway M. Deaf in his left ear since the navy (his words) and "
          "hears the top through his right only; likes it bright, a hair sharp in the treble. Don't argue. Tea, always.")
cn.append("Fr. Colm (St. Brendan's) - sanctuary Baldwin before Easter and Christmas without fail; hall Kimball whenever.")
cn.append("Marta Quill (Civic Hall) - books late, pays fast. Visiting pianists get the D the way they ask, no debate.")
cn.append("Gil Marchetti (Community Theatre) - treasurer, volunteer. Usually pays within the month.")
W("work/client_notes.txt", "\n".join(cn))

# ---------------------------------------------------------------- daily driving (van log)
DAD_DAYS = []
for dt in days(START, END):
    y = dt.year
    if dt.weekday() == 6:
        first_sun = dt.day <= 7
        if y <= 2023 and first_sun:
            DAD_DAYS.append(dt)
        elif y == 2024 and (first_sun or (15 <= dt.day <= 21 and dt.month % 2 == 0)):
            DAD_DAYS.append(dt)
        elif y == 2025 and (dt.day <= 7 or 15 <= dt.day <= 21 or (dt.month >= 9 and 22 <= dt.day <= 28)):
            DAD_DAYS.append(dt)
        elif y == 2026:
            DAD_DAYS.append(dt)
    if y == 2026 and dt.weekday() == 2 and dt.month >= 6 and R.random() < 0.5:
        DAD_DAYS.append(dt)
for s in ["2026-02-03", "2026-06-21", "2026-08-20", "2026-09-24"]:
    if D(s) not in DAD_DAYS:
        DAD_DAYS.append(D(s))
DAD_DAYS.sort()
NELL_DAYS = [D(s) for s in ["2021-11-25", "2022-05-14", "2022-08-20", "2023-12-23", "2024-11-28",
                            "2025-06-07", "2025-11-27"]]
SERVICE_DAY = D("2022-04-11")
SERVICE_ODO = 98062
FINAL_ODO = 157641

day_rows = []
for dt in days(START, END):
    jobs = [j for j in JOBS if j["date"] == dt]
    fixed = sum(2 * TOWNS[j["town"]] for j in jobs)
    parts = []
    if jobs:
        towns = []
        for j in jobs:
            if j["town"] not in towns:
                towns.append(j["town"])
        parts.append(f"{len(jobs)} job{'s' if len(jobs) > 1 else ''}: " + ", ".join(towns))
    if dt in DAD_DAYS:
        fixed += DAD_MILES
        parts.append("Brookhollow (Dad)")
    if dt in NELL_DAYS:
        fixed += NELL_MILES
        parts.append("Rivermouth (Nell)")
    err = 0
    if R.random() < 0.55:
        err = R.randint(4, 30)
        parts.append(R.choice(["errands", "errands", "supply run", "hardware store", "groceries", "post office"]))
    if fixed or err:
        day_rows.append([dt, fixed, err, "; ".join(parts)])

pre = [r for r in day_rows if r[0] < SERVICE_DAY]
post = [r for r in day_rows if r[0] >= SERVICE_DAY]
start_odo = SERVICE_ODO - sum(r[1] + r[2] for r in pre)
need = (FINAL_ODO - SERVICE_ODO) - sum(r[1] for r in post)
have = sum(r[2] for r in post)
f = need / have
acc = 0.0
tot = 0
for r in post:
    acc += r[2] * f
    v = int(round(acc)) - tot
    tot += v
    r[2] = max(v, 0)
diff = need - sum(r[2] for r in post)
post[-1][2] += diff

odo = start_odo
ODO_AT = {}
vrows = [["date", "odo_start", "odo_end", "miles", "where / why"]]
for dt, fixed, err, why in day_rows:
    m = fixed + err
    vrows.append([dt.isoformat(), odo, odo + m, m, why])
    ODO_AT[dt] = odo
    odo += m
assert odo == FINAL_ODO, odo
W("van/van_log.csv", csv_text(vrows))


def odo_on(dt):
    best = start_odo
    for k in sorted(ODO_AT):
        if k <= dt:
            best = ODO_AT[k]
    return best


SERVICES = [("2021-06-14", "Oil + filter, rotate tires", 8940),
            ("2021-11-29", "Oil + filter. Winter wipers.", 9620),
            ("2022-04-11", "TIMING BELT + WATER PUMP + tensioner replaced. Coolant flush. "
                           "Next timing belt/water pump due at 158,000 mi (or Apr 2029, whichever first).", 118400),
            ("2022-09-06", "Oil + filter", 7210),
            ("2023-02-20", "Oil + filter, battery (old one 6 yrs)", 26450),
            ("2023-08-09", "Check engine light, code P0134. Upstream O2 sensor replaced, code cleared. Road test ok.", 31275),
            ("2023-10-02", "Oil + filter", 7480),
            ("2024-04-15", "4 tires, alignment. Oil + filter", 81390),
            ("2024-11-11", "Oil + filter. Rear wiper motor.", 21860),
            ("2025-05-19", "Front brakes, pads + rotors. Oil + filter", 49720),
            ("2025-12-08", "Oil + filter", 8150),
            ("2026-08-03", "Oil + filter. Advised: timing belt interval coming up (158k).", 8590)]
sv = ["DUNMORE AUTO & DIESEL - service history", "Vehicle: 2014 Ford Transit Connect (white), customer: Hollis Varga",
      "Printed for customer", ""]
for ds, what, cost in SERVICES:
    sv.append(f"{ds}  odo {odo_on(D(ds)):,}  ${money(cost):>8}  {what}")
W("van/service_history.txt", "\n".join(sv))

# ---------------------------------------------------------------- bank
TX = []


def tx(dt, desc, cents):
    if isinstance(dt, str):
        dt = D(dt)
    TX.append((dt, len(TX), desc, cents))


for inv in INVOICES:
    if inv["num"] == THEATRE_INV:
        continue
    pd = weekday_on_or_after(inv["date"] + timedelta(days=R.randint(12, 38)))
    tx(pd, f"DEPOSIT {INST[inv['job']['inst']]['code']} REF {inv['num']}", inv["job"]["fee"])

checks = {}
cash_month = {}
for j in JOBS:
    if j["kind"] != "private":
        continue
    if j["pay"] == "check":
        fd = j["date"] + timedelta(days=(4 - j["date"].weekday()) % 7)
        checks.setdefault(fd, []).append(j["fee"])
    elif j["pay"] == "p2p":
        ini = "".join(p[0] for p in j["client"].split())
        tx(j["date"], f"P2P XFER IN {ini}", j["fee"])
    else:
        k = (j["date"].year, j["date"].month)
        cash_month[k] = cash_month.get(k, 0) + j["fee"]
for fd in sorted(checks):
    tx(fd, f"MOBILE DEPOSIT {len(checks[fd])} CHECK{'S' if len(checks[fd]) > 1 else ''}", sum(checks[fd]))
for (y, m) in sorted(cash_month):
    dd = date(y, m, 27)
    tx(weekday_on_or_after(dd), "ATM DEPOSIT CASH LARKFIELD SVGS", (cash_month[(y, m)] * 9 // 10) // 2000 * 2000)

EST = {2022: 118000, 2023: 126000, 2024: 134000, 2025: 145000, 2026: 152000}
EST_LEDGER = {}
for y in range(2022, 2027):
    for (mo, dd, amt_year) in [(1, 15, y - 1), (4, 15, y), (6, 15, y), (9, 15, y)]:
        dt = date(y, mo, dd)
        if dt > END:
            continue
        if y == 2026 and mo == 9:
            continue
        amt = EST.get(amt_year, 110000)
        tx(dt, "TREAS EST TAX PMT 1040-ES", -amt)
        EST_LEDGER[(amt_year, mo)] = (dt, amt)
tx("2024-06-03", "TREAS TAX PMT 1040 BAL DUE 2023", -21240)

for dt in days(date(2022, 1, 1), END):
    y, m, dd = dt.year, dt.month, dt.day
    if dd == 1:
        tx(dt, "MORTGAGE PMT LARKFIELD SAVINGS", -114218)
    if dd == 5:
        tx(dt, "NORTHLINE WIRELESS AUTOPAY", -6840 if y < 2024 else -7215)
    if dd == 12:
        seas = 22000 if m in (1, 2, 12) else (16000 if m in (3, 11) else 9500)
        tx(dt, "LARKFIELD ELEC & GAS", -(seas + R.randint(-1500, 2500)))
    if dd == 20:
        tx(dt, "PEMBERTON MUTUAL AUTO INS", -9650 if y < 2025 else -10420)
    if dd == 25 and m in (1, 4, 7, 10):
        tx(dt, "LARKFIELD WATER DEPT", -R.randint(5800, 8200))
    if dt.weekday() == 5:
        tx(dt, "HARVEST MARKET #3", -R.randint(6200, 13800))
    if dt.weekday() in (1, 4) and R.random() < 0.7:
        tx(dt, R.choice(["FUEL STOP #12 LARKFIELD", "FUEL STOP #12 LARKFIELD", "RIDGE GAS & GO"]),
           -R.randint(4200, 8400))
    if dd == 8 and R.random() < 0.6:
        tx(dt, "KEYBOARD SUPPLY CO", -R.randint(2800, 21000))
    if R.random() < 0.06:
        tx(dt, R.choice(["BLUE KETTLE COLDBROOK", "PINE HATCH DINER", "SALTMARSH FISH SHACK", "LARKFIELD HARDWARE",
                         "DUNNE'S BOOKS", "LARKFIELD DRUG"]), -R.randint(900, 5200))
    if m == 2 and dd == 3:
        tx(dt, "NE KEYBOARD GUILD DUES", -9500)
    if (y == 2025 and m >= 11 or y == 2026) and dd == 18:
        tx(dt, "BROOKHOLLOW PHARMACY", -R.randint(2800, 4900))
for ds, what, cost in SERVICES:
    if D(ds) >= date(2022, 1, 1):
        tx(ds, "DUNMORE AUTO & DIESEL", -cost)
tx("2022-03-08", "MERIDIAN PIANO SUPPLY", -48600)
tx("2022-09-14", "MERIDIAN PIANO SUPPLY", -13200)
tx("2026-01-18", "LARKFIELD DRUG", -1149)
tx("2026-02-20", "LARKFIELD DRUG", -1149)
tx("2026-03-10", "P2P XFER OUT DORIAN WEXLEY", -21000)
tx("2026-06-22", "BROOKHOLLOW MUNICIPAL ELEC - VARGA I", -18422)
tx("2022-05-13", "RIVERMOUTH INN", -14200)
tx("2025-11-26", "P2P XFER OUT NELL VARGA", -10000)

TX.sort(key=lambda t: (t[0], t[1]))
bal = 1421437
months_seen = []
by_month = {}
for dt, _, desc, cents in TX:
    if dt < date(2022, 1, 1) or dt > END:
        continue
    bal += cents
    k = f"{dt.year}-{dt.month:02d}"
    by_month.setdefault(k, []).append([dt.isoformat(), desc, money(cents), money(bal)])
    if k not in months_seen:
        months_seen.append(k)
MIN_BAL = None
for k in months_seen:
    W(f"money/bank/checking_{k}.csv", csv_text([["Date", "Description", "Amount", "Balance"]] + by_month[k]))
    for r in by_month[k]:
        v = float(r[3])
        MIN_BAL = v if MIN_BAL is None else min(MIN_BAL, v)

# estimate ledgers (his own tracking)
for y in range(2022, 2027):
    L = [f"{y} ESTIMATED TAX (1040-ES)  - per Dana", f"Amount each quarter: ${money(EST[y])}", ""]
    for q, (mo, lab) in enumerate([(4, "Apr 15"), (6, "Jun 15"), (9, "Sep 15"), (1, "Jan 15 next yr")], 1):
        got = EST_LEDGER.get((y, mo))
        if got:
            L.append(f"Q{q}  {lab:<15} paid {got[0].isoformat()}  ${money(got[1])}  conf {R.randint(10**7, 10**8 - 1)}")
        else:
            L.append(f"Q{q}  {lab:<15}")
    W(f"money/taxes/estimates_{y}.txt", "\n".join(L))

W("money/taxes/2024-05-20_notice_balance_due.txt", """\
[typed from the letter so I can toss the paper]
Department of the Treasury - Notice
Date of notice: May 20, 2024
Taxpayer: HOLLIS VARGA   SSN: XXX-XX-0000 (fake for my files)
Tax year: 2023   Form 1040

We changed your 2023 return to correct your estimated tax payments.
Amount due: $212.40 (tax $198.00, penalty $11.10, interest $3.30)
Pay by June 10, 2024 to avoid additional interest.

my note: Dana says the Jan 15 payment posted to the wrong year. Just pay it.""")
W("money/taxes/2024-07-15_notice_paid_zero.txt", """\
[typed from the letter]
Department of the Treasury - Notice
Date of notice: July 15, 2024
Tax year: 2023
Your account balance is $0.00. Thank you for your payment of $212.40 received June 3, 2024.
No further action is needed.

-- done. filed.""")

# ---------------------------------------------------------------- mail: handcrafted
DANA = "Dana Ostrowski CPA <dana@ostrowskicpa.example.net>"
for y in range(2022, 2027):
    mail(f"{y}-01-06", 9, 12, DANA, ME, f"{y} estimated tax vouchers",
         f"""Hi Hollis,

Attached are your {y} 1040-ES vouchers. Based on last year I have you at ${money(EST[y])} per quarter:

  Q1  April 15
  Q2  June 15
  Q3  September 15
  Q4  January 15, {y + 1}

Same as always: pay online and keep the confirmation numbers. Send me your mileage log and the 1099s when you have them.

Dana

Dana Ostrowski, CPA
Ostrowski Tax & Bookkeeping, Larkfield
555-0129""")
    mail(f"{y}-09-02", 8, 14, DANA, ME, "September estimate",
         f"""Hi Hollis,

Usual note: third-quarter estimate is due September 15 - ${money(EST[y])}, same as June.
If your income has changed a lot this year let me know and we'll adjust Q4.

Dana""")
    mail(f"{y}-03-03", 10, 40, DANA, ME, f"{y - 1} return - what I still need",
         """Hi Hollis,

Still need from you: mileage log totals, the 1099-NEC from the school district, and your supply receipts total.
Your appointment is on the calendar for later this month.

Dana""")
mail("2024-05-22", 19, 3, ME, DANA, "Fwd: letter about 2023", """Dana - got a letter saying I owe $212.40 for 2023, something about the estimates. Typed it into my files. Is it right?
Hollis""", folder="sent")
mail("2024-05-23", 8, 50, DANA, ME, "Re: Fwd: letter about 2023", """It's right - your January payment got applied to the wrong year. Annoying but small. Just pay it and keep the letter. I've noted it so it doesn't happen again.
Dana""")

CIVIC = "Marta Quill <events@larkfieldcivichall.example.org>"
for y in range(2021, 2026):
    mail(f"{y}-04-20", 11, 2, CIVIC, ME, "May 14 - spring gala", f"""Hollis,

Spring gala is May 14 as usual. D ready by 5pm please, and if you can stay for the first half that's ideal.

Marta
Larkfield Civic Hall - 555-0150""")
mail("2026-03-18", 14, 22, CIVIC, ME, "Rennick recital - Sat May 16", """Hollis,

Big one for us: Ilse Rennick, Saturday May 16, 2pm. Her tech can't travel after all. She's particular - wants the D voiced a touch warmer in the middle and the tuning settled.
Could you be in from 8am, have it ready by noon, and stay through the interval for a touch-up?
I know it's a Saturday. We'll pay the weekend rate.

Marta""")
mail("2026-03-18", 21, 47, ME, CIVIC, "Re: Rennick recital - Sat May 16", """Marta,
Yes. I'll be in at 8 and stay through the interval.
Hollis""", folder="sent")
mail("2026-05-17", 10, 5, CIVIC, ME, "Thank you", """Hollis - she said it was the best D she's played this tour. Thank you for giving up your Saturday.
Marta""")

NELLM = "Nell Varga <nell.varga@example.net>"
mail("2022-04-02", 16, 30, NELLM, ME, "graduation!", """Dad!
It's May 14, 11am, the field house. You don't need a ticket for undergrad. Bring a chair cushion, the bleachers are awful.
Love you
N""")
mail("2026-02-08", 20, 11, NELLM, ME, "Graduation details!!", """Dad,

OK official: Saturday May 16, 10am, Rivermouth Arena. Reception after at the OT building, around 1. I got you two tickets (bring someone or don't lol). Mom's coming, Greg too. Please please be there on time, they close the doors at 9:50.

I can't believe I'm actually done.

Love,
Nell""")
mail("2026-06-03", 7, 58, NELLM, ME, "Re: (no subject)", """Got your email. I read it. I'm not ready to talk about it yet.
N""")

COR = "Corinne Haldane <corinne.haldane@example.net>"
mail("2021-04-10", 21, 15, COR, ME, "the house", """Hollis,
I'm not signing anything until the appraisal is redone. The first one was done in February with snow on the roof, you know that.
C""")
mail("2021-04-11", 9, 2, ME, COR, "Re: the house", """Fine. Get it redone. I'll split the cost.
H""", folder="sent")
mail("2021-06-30", 13, 40, COR, ME, "Re: the house", """New appraisal came in at 236. Wendy at the title office will draw up the quitclaim. Can you get me the refinance letter from Larkfield Savings?
C""")
mail("2021-09-14", 18, 20, COR, ME, "we need to finish this", """It's been five months. Nell is asking me about it. Please just send the letter.
C""")
mail("2022-02-25", 15, 3, COR, ME, "Recorded", """It's recorded. Quitclaim's filed, refinance closed. We're done with the house, Hol. Thank you for not making it worse than it had to be.
C""")
mail("2026-04-28", 12, 9, COR, ME, "May 16", """Hollis - I'll sit wherever. Greg will too. Let's just be decent for her, it's her day.
C""")
mail("2026-05-16", 19, 31, COR, ME, "today", """She waited at the doors until they closed them. I'm not going to say anything else, it's not my place. Here are some pictures.
C""")

LCT = "Gil Marchetti <gil.marchetti@example.org>"
mail("2023-04-02", 10, 0, LCT, ME, "payment sent", """Hollis, check is in the mail for March. Thanks as ever - the pit piano has never sounded better.
Gil""")
mail("2026-07-01", 9, 30, ME, LCT, f"Invoice {THEATRE_INV} - reminder", f"""Hi Gil,
Friendly reminder on {THEATRE_INV} ($640.00) for the pit piano work after the flood, dated May 30.
Thanks,
Hollis""", folder="sent")
mail("2026-08-05", 8, 15, ME, LCT, f"Invoice {THEATRE_INV} - second reminder", f"""Gil,
Second note on {THEATRE_INV}, $640.00, now over 60 days. Can you check where it is?
Hollis""", folder="sent")
mail("2026-08-06", 22, 40, LCT, ME, f"Re: Invoice {THEATRE_INV} - second reminder", """Hollis, so sorry. I stepped down as treasurer in June and things are a mess with the handover. I've passed it to the board, they'll get it to you.
Gil""")
mail("2026-09-15", 7, 45, ME, "Larkfield Community Theatre <boxoffice@larkfieldtheatre.example.org>",
     f"Invoice {THEATRE_INV} - 108 days", f"""Hello,
I'm writing about invoice {THEATRE_INV} for $640.00 (pit piano pitch raise, regulation and strings after the April flood). Gil Marchetti said it was passed to the board in August. It's now 108 days. Could someone let me know who to talk to?
Thanks,
Hollis Varga
555-0142""", folder="sent")
mail("2026-09-15", 7, 46, "Larkfield Community Theatre <boxoffice@larkfieldtheatre.example.org>", ME,
     "Auto-reply: Box office", """Thanks for your email! The box office is open Thu-Sat 12-6 during the season. For ticketing questions please see our website.""")

HC = "Larkfield Hearing Center <frontdesk@larkfieldhearing.example.org>"
mail("2026-02-10", 16, 2, "Lindqvist Family Practice <portal@lindqvistfp.example.org>", ME,
     "Your referral from Dr. Lindqvist", """Hello Hollis,

Following your annual visit on February 9, Dr. Priya Lindqvist has sent a referral on your behalf:

  Larkfield Hearing Center - audiometric evaluation
  Phone: 555-0131
  Referral valid for 9 months (through 2026-11-10)

Please call to schedule.

Lindqvist Family Practice""")
mail("2026-03-30", 11, 30, HC, ME, "Appointment confirmed: Tue Apr 14, 9:30am", """Hi Hollis,
You're booked for a comprehensive evaluation on Tuesday, April 14 at 9:30am (about 60 minutes). Please avoid loud noise for 24 hours before your visit.
Larkfield Hearing Center - 555-0131""")
mail("2026-04-13", 17, 2, HC, ME, "Appointment cancelled", """Your appointment on Tue Apr 14 at 9:30am has been cancelled. Call 555-0131 to rebook.""")
mail("2026-06-25", 10, 14, HC, ME, "Appointment confirmed: Thu Jul 9, 2:00pm", """Hi Hollis,
You're rebooked for Thursday, July 9 at 2:00pm.
Larkfield Hearing Center - 555-0131""")
mail("2026-07-08", 20, 55, HC, ME, "Appointment cancelled", """Your appointment on Thu Jul 9 at 2:00pm has been cancelled. Call 555-0131 to rebook.""")
mail("2026-07-20", 9, 5, HC, ME, "We'd like to reschedule you", """Hi Hollis,
We've tried you by phone twice. Your referral from Dr. Lindqvist is valid through November 10, 2026; after that a new referral is needed. We have openings most Tuesday mornings.
Larkfield Hearing Center - 555-0131""")

RUTH = "Ruth Tamsin <ruth.tamsin@example.org>"
mail("2024-07-30", 14, 12, RUTH, ME, "zucchini", """Hollis, your father has given me eleven zucchini this week. Please tell him I love him but I'm going to start leaving them on your porch instead.
Ruth""")
mail("2026-08-19", 20, 31, RUTH, ME, "your dad", """Hollis, it's Ruth next door to your dad. Please don't tell him I wrote, he'd be mortified.

That's the second time this summer his car has clipped the mailbox post backing out. I don't mind about the post at all. And Thursday I saw him sitting in his car in the Grange Market lot for a long time, a good half hour. When I knocked on the window he laughed and said he was resting, but I don't think he could think where he was headed.

He's lovely as ever. I just thought you'd want to know.

Ruth
555-0187""")
mail("2026-10-02", 9, 44, RUTH, ME, "Re: your dad", """He's had a good week. Waved me in for coffee Tuesday and told me all about your mother's piano. The car hasn't moved since Sunday.
R""")
mail("2026-09-08", 21, 10, ME, "Brookhollow Family Medicine <frontdesk@brookhollowfm.example.org>",
     "Imre Varga - appointment 9/24", """Hello,
I'm Imre Varga's son. I'd like to come in with my father to his appointment on September 24, and if possible have a few minutes with Dr. Huang beforehand.
Thank you,
Hollis Varga  555-0142""", folder="sent")
mail("2026-09-09", 10, 3, "Brookhollow Family Medicine <frontdesk@brookhollowfm.example.org>", ME,
     "Re: Imre Varga - appointment 9/24", """Hi Mr. Varga, that's fine. We'll need your father's signed consent on file for Dr. Huang to discuss his care with you; we can do that at check-in. Please arrive 15 minutes early.""")

mail("2026-10-03", 6, 2, "Account Services <billing@secure-acct-verify.example.net>", ME,
     "URGENT: Your account is past due - action required",
     """Dear Customer,

Your account has an outstanding balance and will be suspended within 24 hours.
To avoid interruption please verify your payment details here:
http://192.0.2.44/verify

Reference: 00-AX-7731
Account Services""", name="urgent_your_account_is_past_due")

MER = "Meridian Piano Supply <orders@meridianpiano.example.com>"
mail("2021-06-02", 13, 0, MER, ME, "Order #M21-4471 confirmed", """Order #M21-4471
  Hammer set, 88, custom bore/weights from your sample (Weiler & Sons 1911 upright)   $612.00
Lead time 4-6 weeks. Ships to: 18 Cobble Lane, Larkfield.
Paid: card ending 1111""")
mail("2022-03-08", 13, 0, MER, ME, "Order #M22-1180 confirmed", """Order #M22-1180
  Bass string set, custom wound from samples (Weiler & Sons 1911 upright, 20 strings)   $486.00
Lead time 3 weeks.""")
mail("2022-09-14", 13, 0, MER, ME, "Order #M22-3392 confirmed", """Order #M22-3392
  Keytops, 52 naturals, ivory-look acrylic, with fronts   $132.00""")
for i in range(10):
    dt = START + timedelta(days=R.randint(10, (END - START).days - 10))
    items = R.sample(["Tuning pins 2/0, box of 100", "Music wire #16, 1lb coil", "Music wire #17, 1lb coil",
                      "Hammer shank set", "Center pins assorted", "Key bushing cloth", "Damper felt strip",
                      "Mute strips, felt (10)", "Bridle straps (100)", "Regulating tools: let-off button set",
                      "Lubricant, Protek CLP"], 3)
    total = 0
    lines = []
    for it in items:
        a = R.randint(900, 6800)
        total += a
        lines.append(f"  {it:<44} ${money(a)}")
    mail(dt, 13, 0, MER, ME, f"Order #M{dt.year % 100}-{R.randint(1000, 9999)} confirmed",
         "\n".join(lines) + f"\nTotal ${money(total)}")

GUILD = "NE Keyboard Guild <bulletin@nekeyboardguild.example.org>"
TOPICS = ["Humidity control systems: a year of data from member installs", "Pricing your time: the 2-hour tuning",
          "Pin block cracks and when to walk away", "Grand regulation workshop recap",
          "Hammer voicing: needles vs. steam", "Insurance for independent techs",
          "Spring chapter meeting minutes", "Tuning stability after a pitch raise",
          "Member spotlight: forty years on the road", "Hearing protection on the job - a reminder",
          "New members", "Electronic tuning devices: do you still need your ears?", "Estate-sale pianos: just say no?",
          "Bass string replacement from samples"]
for y in range(2021, 2027):
    for mo in (2, 5, 8, 11):
        dt = date(y, mo, 10)
        if not (START <= dt <= END):
            continue
        ts = R.sample(TOPICS, 3)
        mail(dt, 6, 30, GUILD, ME, f"Guild Bulletin - {MONTHS[mo - 1]} {y}",
             "In this issue:\n" + "\n".join(f"  * {t}" for t in ts) +
             "\n\nChapter meeting second Tuesday, 7pm, Academy hall.\nDues reminder: annual dues are due February 1.")
for i in range(8):
    dt = START + timedelta(days=R.randint(0, (END - START).days))
    mail(dt, 5, 0, "Harvest Market <deals@harvestmarket.example.com>", ME, "This week's specials",
         "Chicken thighs $1.99/lb, sweet corn 6 for $3, store-brand coffee 2 for $9.\nHarvest Market #3, Larkfield")
for i in range(12):
    dt = START + timedelta(days=R.randint(0, (END - START).days))
    who = f"{R.choice(FIRST)} {R.choice(LAST)}"
    em = slug(who).replace("_", ".") + "@example.com"
    mail(dt, R.randint(8, 21), R.randint(0, 59), f"{who} <{em}>", ME, R.choice(
        ["Piano tuning?", "Tuning quote", "got your name from the Academy", "old upright - worth fixing?"]),
         f"""Hi, I got your name from {R.choice(['a neighbor', 'the Music Academy', 'the church', 'a friend'])}.
We have a {R.choice(INSTR)} that hasn't been tuned in {R.randint(2, 15)} years. Do you have anything in the next few weeks? What do you charge?
Thanks,
{who.split()[0]}""")

mail("2021-03-15", 10, 0, ME, "Wendy Corrigan <wendy@larkfieldtitle.example.com>", "Refinance letter", """Wendy, attached is the letter from Larkfield Savings. Let me know if Corinne needs anything else from me.
Hollis""", folder="sent")

# write mail
used = []
for (dt, hh, mm, frm, to, subj, body, folder, name) in sorted(MAIL, key=lambda x: (x[0], x[1], x[2], x[5])):
    base = f"mail/{folder}/{dt.isoformat()}_{name or slug(subj)}"
    p = base + ".eml"
    k = 2
    while p in used:
        p = f"{base}_{k}.eml"
        k += 1
    used.append(p)
    W(p, f"From: {frm}\nTo: {to}\nDate: {rfc(dt, hh, mm)}\nSubject: {subj}\n"
         f"Content-Type: text/plain; charset=utf-8\n\n{body}")

# ---------------------------------------------------------------- messages
NELL_SAYS = [("lol dad", None), ("how's grandpa", "Good. Grumpy. Asks about you."),
             ("midterm went ok i think", "Proud of you"), ("send me grandma's goulash thing again?", "Lard not oil. Sending."),
             ("can you look at my car when I'm home", "Yes, bring it"),
             ("saw a piano at the coffee place and thought of you", "Out of tune?"),
             ("it was SO out of tune btw", "Ha"), ("I'm so tired", "Sleep. Call Sunday?"),
             ("placement's at a rehab hospital and I love it", "That's great Nell"),
             ("Mom says hi (sort of)", "Hi back (sort of)"), ("did you watch the thing I sent", "Not yet. This weekend."),
             ("what are you doing for your birthday", "Nothing much. Dinner with Grandpa."),
             ("ok call me sunday", "Will do"), ("I passed!!!", "Of course you did. Proud of you."),
             ("can I borrow the van in August to move", "I'll drive it up and help"),
             ("my roommate's cat hates me", None), ("did grandpa get the card", "He did. It's on the fridge."),
             ("it snowed here, did it snow there", "Six inches. Cancelled 3 jobs."),
             ("made the goulash, it was ok, not grandma's", "Nobody's is"),
             ("do you still have grandma's piano in the garage?", "Still there. One of these winters."),
             ("how's the van", "Still going"), ("one of my patients is a retired music teacher, she'd love you", "Ha. Tell her hi."),
             ("I'm thinking about specializing in hand therapy", "You'd be good at that"), ("😂", None),
             ("love you", "Love you too"), ("that's so you", None)]
IMRE_SAYS = [("Did you see the game", "Saw the end. Hopeless."), ("Tomatoes are coming in", "Save me some"),
             ("Ferris came by, we played cards", "Who won?"), ("Call when you can", "Calling tonight"),
             ("Sunday still good?", "Sunday, around 11"), ("Bring the good coffee", "Will do"),
             ("The gutters again", "I'll do them Sunday"), ("Your mother would have liked that", "She would"),
             ("I found your old report cards", "Burn them"), ("Ok", None), ("Thank you son", "Love you Dad"),
             ("The Hawks are hopeless", "Always")]
IMRE_LATE = [("What day are you coming?", "Sunday Dad"), ("Is it Sunday you come", "Yes, Sunday")]


def chat(y, pairs, n, who, start=None, end=None):
    out = []
    a = start or max(date(y, 1, 1), START)
    b = end or min(date(y, 12, 31), END)
    span = (b - a).days
    for _ in range(n):
        dt = a + timedelta(days=R.randint(0, span))
        hh, mm = R.randint(8, 21), R.randint(0, 40)
        say, reply = R.choice(pairs)
        out.append((dt, hh, mm, who, say))
        if reply and R.random() < 0.85:
            out.append((dt, hh, mm + R.randint(1, 19), "Me", reply))
    return out


NELL_SCRIPT = [
    ("2026-01-04", 18, 5, "Nell", "SIX MORE MONTHS"),
    ("2026-02-08", 20, 15, "Nell", "check your email!! graduation stuff"),
    ("2026-02-08", 20, 31, "Me", "Got it. Wouldn't miss it."),
    ("2026-03-07", 19, 2, "Nell", "dad you said 'what' like six times on the phone lol is the connection bad on your end"),
    ("2026-03-07", 19, 9, "Me", "Must be. I'll get a new phone."),
    ("2026-03-21", 12, 40, "Nell", "you're coming right?? mom's coming. she'll behave I promise"),
    ("2026-03-21", 13, 2, "Me", "Yes. I'll be there."),
    ("2026-04-30", 21, 18, "Nell", "2 weeks!!!!"),
    ("2026-05-12", 8, 3, "Nell", "doors close 9:50 dad. 9:50"),
    ("2026-05-12", 8, 30, "Me", "9:50."),
    ("2026-05-15", 22, 41, "Me", "Nell I'm so sorry. The Civic Hall pianist's tech fell through and I said yes weeks ago before I put it together. I can't get there till evening. Dinner tomorrow night? I'll drive up after."),
    ("2026-05-15", 23, 58, "Nell", "don't bother"),
    ("2026-05-16", 18, 12, "Me", "Proud of you. I mean it."),
    ("2026-05-18", 10, 4, "Nell", "mom sent you pics I guess"),
    ("2026-06-02", 21, 50, "Me", "Sent you an email. Read it when you want."),
    ("2026-06-19", 14, 2, "Nell", "new address for the card or whatever: 41 Wren St Apt 3, Rivermouth"),
    ("2026-06-19", 14, 5, "Me", "Got it"),
    ("2026-07-14", 19, 30, "Me", "How's the new job?"),
    ("2026-07-15", 8, 2, "Nell", "good"),
    ("2026-08-23", 17, 44, "Me", "Grandpa asked about you today"),
    ("2026-08-23", 20, 10, "Nell", "tell him hi"),
    ("2026-09-20", 11, 12, "Nell", "mom's doing thanksgiving. you can come if you want I guess"),
    ("2026-09-20", 11, 14, "Me", "I'd like that."),
    ("2026-10-04", 16, 0, "Me", "Want to call this weekend?"),
    ("2026-10-15", 18, 22, "Me", "Saw a Weiler upright at an estate sale today. Made me think of Grandma's."),
]
DAD_SCRIPT = [
    ("2026-02-03", 9, 2, "Dad", "Is the plumber coming Tuesday"),
    ("2026-02-03", 9, 5, "Me", "Yes Dad, today, Tuesday at 10. I'm coming too."),
    ("2026-02-03", 9, 40, "Dad", "Is the plumber Tuesday?"),
    ("2026-02-03", 9, 41, "Me", "Today. 10am. I wrote it on the fridge."),
    ("2026-02-03", 11, 15, "Dad", "Hollis is the plumber coming Tuesday"),
    ("2026-03-14", 15, 30, "Dad", "Where is my checkbook did you take it"),
    ("2026-03-14", 15, 44, "Me", "It's in the desk drawer, left side. I didn't take it."),
    ("2026-04-02", 17, 5, "Dad", "I went to Ferris's but they changed the road"),
    ("2026-04-02", 17, 9, "Me", "Which road? Are you home now?"),
    ("2026-04-02", 18, 30, "Dad", "Home. Its fine dont fuss"),
    ("2026-06-21", 20, 15, "Dad", "Did you move my pills"),
    ("2026-06-21", 20, 20, "Me", "They're in the organizer by the kettle. Today's is Sunday."),
    ("2026-07-30", 8, 12, "Dad", "The car is fine dont fuss"),
    ("2026-08-20", 7, 50, "Me", "Coming by this afternoon, ok?"),
    ("2026-08-20", 12, 2, "Dad", "Who is this"),
    ("2026-08-20", 12, 4, "Me", "It's Hollis, Dad. Coming at 3."),
    ("2026-08-20", 12, 30, "Dad", "Ok son"),
    ("2026-09-24", 7, 30, "Me", "Picking you up at 10 for Dr. Huang"),
    ("2026-09-24", 9, 58, "Dad", "Is it today the doctor"),
    ("2026-10-11", 19, 5, "Dad", "Did you say the plumber was Tuesday"),
    ("2026-10-11", 19, 9, "Me", "No plumber this week Dad. That was February."),
]
for y in range(2021, 2027):
    for who, phone, script, mk in [("Nell", "555-0163", NELL_SCRIPT, "nell"), ("Dad", "555-0118", DAD_SCRIPT, "dad")]:
        if who == "Nell":
            if y < 2026:
                msgs = chat(y, NELL_SAYS, 115 if y > 2021 else 90, "Nell")
            else:
                msgs = chat(y, NELL_SAYS, 42, "Nell", end=date(2026, 5, 10))
        else:
            n = {2021: 50, 2022: 55, 2023: 55, 2024: 60, 2025: 70, 2026: 40}[y]
            msgs = chat(y, IMRE_SAYS + (IMRE_LATE if y >= 2025 else []), n, "Dad")
        for s in script:
            if D(s[0]).year == y:
                msgs.append((D(s[0]), s[1], s[2], s[3], s[4]))
        msgs.sort(key=lambda m: (m[0], m[1], m[2]))
        body = [f"Conversation with {who} ({phone})", "Exported 2026-10-22 from phone backup", "---"]
        for dt, hh, mm, w, t in msgs:
            body.append(f"[{dt.isoformat()} {hh:02d}:{mm:02d}] {w}: {t}")
        W(f"messages/sms_{mk}_{y}.txt", "\n".join(body))

# ---------------------------------------------------------------- notes
NOTES = {
    "2021-03-12_civic_hall_by_ear.txt": "Did the Civic Hall D by ear tonight, start to finish, no machine. Two hours. Unisons in the top octave like glass. Still got it.",
    "2021-05-02_goulash.txt": "MOM'S GOULASH (as close as I can get)\n2 lb chuck, cubed\n3 onions, a lot of sweet paprika (Szeged tin), 1 tsp hot\ncaraway, 2 cloves garlic, 1 tomato, 1 green pepper\npotatoes last 40 min\nShe never measured. Lard, not oil. Csipetke if you're brave.",
    "2021-07-19_weiler_hammers_arrived.txt": "Hammers came for Mom's Weiler. Beautiful. Hung the first ten tonight in the garage.",
    "2021-11-26_thanksgiving.txt": "Thanksgiving at Nell's apartment. She made the turkey. Dad told the story about the piano coming over on the boat (wrong, it was bought in Cleveland, but let him).",
    "2022-02-26_house_done.txt": "House is done. Corinne sent the email. Strange to feel relieved about something so sad.",
    "2022-05-15_nell_graduation.txt": "Nell graduated. Sat on a terrible bleacher in the sun for three hours and would do it again. She waved at me from the line. Took her and Dad to the inn for dinner.",
    "2023-01-08_resolutions.txt": "- finish the Weiler by summer\n- fewer Saturdays\n- call Dad more, not just Sundays\n- teeth",
    "2023-08-02_van_light.txt": "Check engine light on again, van hesitating on the Ridge Rd hill. Booked Dunmore for next week.",
    "2023-08-20_van_ok.txt": "Light stayed off since Dunmore did the sensor. Pulls fine on Ridge Rd.",
    "2024-03-10_guild_talk_notes.txt": "Guild talk - pricing.\nMost people charge for 2 hrs now. I'm under. Raise to $140 in Jan.\nNobody under 40 in the room. Who's going to do this after us?",
    "2024-11-29_thanksgiving.txt": "Nell asked about Grandma's piano again, whether I'd finished it. Told her one of these winters.",
    "2025-02-14_slow_day.txt": "Snow. Cancelled 3. Re-read Mom's letters from the garage box. The Weiler receipt from 1953 is in there, she paid it off over two years.",
    "2025-09-21_garage_roof.txt": "Patched the flashing on the back corner of the garage roof where it was getting in. Should hold. Moved the box of Mom's letters inside.",
    "2025-10-30_top_octave.txt": "Pell's Steinway today. He says I've got it lovely and bright. Took me twice as long as it used to up top.",
    "2026-03-11_strobe.txt": "Bought a used strobe tuner off Dorian Wexley from the Guild, $210. Using it above C6 for now, the rooms with carpet are hard to hear in. Civic Hall is fine, it's the houses.",
    "2026-05-16_recital.txt": "Ilse Rennick played the Schubert B-flat. Second movement. I stood in the wing.",
    "2026-06-02_draft_nell.txt": "Nell -\n\nI've started this four times.\n\nI could tell you about Marta and the tech and the date, but you don't care about that and you shouldn't. I put a job ahead of you on the one day I should not have, and I knew the date, I had it on the calendar since February.\n\nI don't know how to\n\n[sent a shorter one instead]",
    "2026-06-21_dads.txt": "At Dad's.\nFridge: three cartons of milk, two expired.\nStove: back burner on low when I came in. Off now.\nElectric bill 2 months behind - paid it from my account.\nPill organizer: Tue/Wed/Thu still full from this week.\nHe was cheerful the whole time. Asked about Nell's graduation, twice.",
    "2026-07-23_garage.txt": "Leak again over the back corner of the garage. Water down the wall onto the drop cloth. Put a tarp over the Weiler and two buckets. Need a roofer, not me.",
    "2026-08-20_dad.txt": "Dad's car: fresh scrape on the rear bumper, white paint. He said someone hit it at the market.",
    "2026-10-14_van_ticking.txt": "Van has a ticking at idle, goes away when warm. Starting to sound like the old belt did. Busy through Nov.",
    "2026-09-29_ideas.txt": "Things I keep meaning to:\n- ask Marta about a winter maintenance contract for the D\n- new business cards\n- the Weiler\n- teach someone? Guild has a mentorship list",
}
for k in sorted(NOTES):
    W(f"notes/{k}", NOTES[k])

TODO_POOL = ["oil change", "Guild dues", "buy music wire #17", "return library books", "call Dad", "gutters",
             "replace tuning hammer tip", "Harvest Mkt: coffee, eggs, onions", "invoice the Academy", "dentist",
             "mow", "fix porch step", "bank deposit", "Dunmore - wipers", "renew Guild membership", "birthday card Nell",
             "order felt", "back up phone", "shirt for Civic Hall", "pay electric", "mileage log totals for Dana"]
for i in range(20):
    dt = START + timedelta(days=R.randint(0, (END - START).days))
    items = R.sample(TODO_POOL, R.randint(3, 6))
    W(f"notes/todo_{dt.isoformat()}.txt", "TODO\n" + "\n".join(f"- {x}" for x in items))
W("notes/todo_2024-09-09.txt", "TODO\n- Q3 estimate (Dana) by 15th\n- St Brendan's Thurs\n- call Dad\n- mow")
W("notes/todo_2025-09-08.txt", "TODO\n- send Q3 estimate\n- Academy fall visit - book\n- garage roof corner\n- Dad Sunday")
W("notes/todo_2026-09-10.txt", "TODO\n- Dad - Dr Huang 24th, ask about consent form\n- Academy fall visit\n- call theatre again\n- roofer??\n- coffee")
W("notes/todo_2026-07-24.txt", "TODO\n- call back re appt\n- roofer\n- invoice theatre AGAIN\n- Dad Weds")

# ---------------------------------------------------------------- weiler
WL = ["WEILER & SONS 1911 UPRIGHT (Mom's) - restoration log", "Serial 41187. Bought by Mom 1953, used, Cleveland.", ""]
WL += ["2021-04-03  Moved it into the garage from Dad's on Paulo's trailer. Case ok, veneer lifting on the left cheek.",
       "2021-04-10  Pulled the action. Hammers grooved to the wood. Measured and sent samples to Meridian.",
       "2021-05-15  Stripped the old strings in the treble. Pin block looks sound - tight pins, no cracks seen.",
       "2021-07-19  Hammers arrived. Hung C4-A4.",
       "2021-08-28  All hammers hung. Traveled. Spaced.",
       "2021-10-09  Cleaned and polished plate. Gold paint touch-up.",
       "2022-01-22  Too cold to glue. Stopped for winter.",
       "2022-03-26  Bass string samples back from Meridian; set ordered.",
       "2022-04-30  Treble restrung C5-C8. New pins, oversize.",
       "2022-06-18  Tenor restrung.",
       "2022-10-01  Keytops (new) glued on 30 of 52.",
       "2022-11-12  Keytops done. Rebushed keys.",
       "2023-02-11  Bass strings on through E1. Stopping till spring, hands too cold in the garage.",
       "", "Still to do: rest of bass, chip up, regulation, damper felts, 3-4 tunings to settle, case veneer."]
W("weiler/restoration_log.txt", "\n".join(WL))
hy = [["date", "temp_f", "rh_pct", "note"]]
dt = date(2021, 3, 7)
while dt <= END:
    doy = dt.timetuple().tm_yday
    s = max(0.0, math.cos(2 * math.pi * (doy - 200) / 365.0))
    temp = 50 + 30 * math.cos(2 * math.pi * (doy - 200) / 365.0) + R.uniform(-5, 5)
    rh = 42 + 22 * s + R.uniform(-4, 4)
    if date(2025, 6, 1) <= dt < date(2025, 9, 21) or date(2026, 5, 15) <= dt:
        rh += 15 * s + 2
    hy.append([dt.isoformat(), f"{temp:.0f}", f"{min(rh, 96):.0f}", ""])
    dt += timedelta(days=7)
W("weiler/garage_hygrometer.csv", csv_text(hy))

# ---------------------------------------------------------------- receipts
REC = []


def receipt(ds, vendor, lines, pay="VISA ****1111"):
    tot = sum(a for _, a in lines)
    t = [vendor.upper(), ds, ""] + [f"{n:<34}{money(a):>9}" for n, a in lines] + \
        ["", f"{'TOTAL':<34}{money(tot):>9}", pay, "", "Thank you!"]
    W(f"receipts/{ds}_{slug(vendor)}.txt", "\n".join(t))


receipt("2021-06-02", "Meridian Piano Supply", [("Hammer set 88 custom (Weiler)", 61200)])
receipt("2022-03-08", "Meridian Piano Supply", [("Bass string set custom (Weiler)", 48600)])
receipt("2022-09-14", "Meridian Piano Supply", [("Keytops 52 naturals w/ fronts", 13200)])
receipt("2022-04-11", "Dunmore Auto & Diesel", [("Timing belt kit", 38400), ("Water pump", 21600),
                                                ("Labor 5.4 hr", 54000), ("Coolant", 4400)])
receipt("2023-08-09", "Dunmore Auto & Diesel", [("O2 sensor upstream", 14275), ("Labor 1.0 hr", 10000),
                                                ("Diagnostic", 7000)])
receipt("2026-01-18", "Larkfield Drug", [("Ear wax removal drops 15ml", 899), ("Cough drops", 250)])
receipt("2026-02-20", "Larkfield Drug", [("Ear wax removal drops 15ml", 899), ("Cough drops", 250)])
receipt("2026-06-22", "Brookhollow Municipal Electric", [("Acct VARGA I - past due Apr+May", 18422)])
for i, dt in enumerate([date(2025, 11, 18) + timedelta(days=30 * k) for k in range(12)]):
    if dt > END:
        break
    receipt(dt.isoformat(), "Brookhollow Pharmacy", [("Rx VARGA, IMRE - donepezil 5mg x30" if dt >= date(2026, 3, 1)
                                                      else "Rx VARGA, IMRE - amlodipine 5mg x30", R.randint(800, 1500)),
                                                     ("Rx VARGA, IMRE - atorvastatin 20mg x30", R.randint(900, 1600))],
            pay="VISA ****1111  picked up by: HOLLIS")
for i in range(18):
    dt = START + timedelta(days=R.randint(0, (END - START).days))
    v = R.choice(["Larkfield Hardware", "Harvest Market", "Dunne's Books", "Keyboard Supply Co", "Fuel Stop 12"])
    pool = {"Larkfield Hardware": [("Wood glue", 699), ("Sandpaper 220", 549), ("Shop rags", 899), ("Light bulbs", 1199), ("Tarp 10x12", 2499)],
            "Harvest Market": [("Coffee", 899), ("Eggs", 429), ("Onions 3lb", 349), ("Chuck roast", 1876), ("Paprika", 499)],
            "Dunne's Books": [("Paperback", 1799), ("Crossword book", 999)],
            "Keyboard Supply Co": [("Tuning mutes", 1250), ("Music wire #16", 2400), ("Key easing pliers", 3800)],
            "Fuel Stop 12": [("Unleaded 14.2 gal", 5112), ("Coffee", 189)]}[v]
    p = f"receipts/{dt.isoformat()}_{slug(v)}.txt"
    if p in FILES:
        continue
    receipt(dt.isoformat(), v, R.sample(pool, min(len(pool), R.randint(1, 3))))

# ---------------------------------------------------------------- house papers (trap)
W("money/house/2022-02-24_recording_confirmation.txt", """LARKFIELD TITLE & ESCROW
Recording confirmation
Property: 22 Alder Way, Larkfield (former marital residence)
Instrument: Quitclaim deed, Varga H. to Haldane C., recorded 2022-02-24, Book 0000 Page 000 (file copy)
Refinance to sole name closed 2022-02-23.
This file is closed.""")
W("money/house/2021-06-28_appraisal_summary.txt", """Appraisal summary - 22 Alder Way, Larkfield
Effective date: 2021-06-24
Opinion of value: $236,000
(Second appraisal; first dated 2021-02-11 at $219,000.)""")

# ---------------------------------------------------------------- calendar
EV = []


def ev(ds, hh, mm, dur_min, summary, loc="", status="CONFIRMED", allday=False):
    EV.append((ds, hh, mm, dur_min, summary, loc, status, allday))


for j in JOBS:
    if j["kind"] == "inst" and j["date"] != CIVIC_RECITAL:
        ev(j["date"].isoformat(), 8 if j["inst"] != "civ" else 13, 30, j["minutes"], f"Tune - {j['client']}", j["town"])
ev("2026-05-16", 8, 0, 360, "Civic Hall - Rennick recital, D prep + interval", "Larkfield Civic Hall")
ev("2026-05-16", 10, 0, 180, "NELL GRADUATION 10am Rivermouth Arena (doors close 9:50)", "Rivermouth Arena")
ev("2022-05-14", 11, 0, 180, "Nell graduation - field house", "Rivermouth")
ev("2026-04-14", 9, 30, 60, "Larkfield Hearing Center - evaluation", "Larkfield Hearing Center", status="CANCELLED")
ev("2026-07-09", 14, 0, 60, "Larkfield Hearing Center - evaluation", "Larkfield Hearing Center", status="CANCELLED")
ev("2026-09-24", 10, 40, 60, "Dad - Dr. Huang (drive him)", "Brookhollow Family Medicine")
ev("2026-02-09", 9, 0, 45, "Annual physical - Dr. Lindqvist", "Lindqvist Family Practice")
ev("2025-02-10", 9, 0, 45, "Annual physical - Dr. Lindqvist", "Lindqvist Family Practice")
ev("2026-11-26", 0, 0, 0, "Thanksgiving - Corinne's?", "", allday=True)
for ds, what, cost in SERVICES:
    ev(ds, 8, 0, 240, "Van - Dunmore", "Dunmore Auto & Diesel")
for y in range(2021, 2027):
    for mo in range(1, 13):
        first = date(y, mo, 1)
        tue = first + timedelta(days=(1 - first.weekday()) % 7 + 7)
        if START <= tue <= END and mo not in (7, 8):
            ev(tue.isoformat(), 19, 0, 120, "Guild chapter meeting", "Larkfield Music Academy hall")
    for mo in (3, 9):
        dd = weekday_on_or_after(date(y, mo, 8))
        if START <= dd <= END:
            ev(dd.isoformat(), 15, 0, 45, "Dentist - cleaning", "Larkfield Dental")
    dd = weekday_on_or_after(date(y, 3, 20))
    if START <= dd <= END:
        ev(dd.isoformat(), 16, 0, 60, "Dana - taxes", "Ostrowski Tax & Bookkeeping")
    for mo, dd, lab in [(6, 14, "Dad's birthday"), (11, 2, "Nell's birthday"), (9, 30, "Mom - anniversary")]:
        if START <= date(y, mo, dd) <= TODAY + timedelta(days=60):
            ev(date(y, mo, dd).isoformat(), 0, 0, 0, lab, "", allday=True)
EV.sort(key=lambda e: (e[0], e[1], e[2], e[4]))
ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hollis//Calendar export//EN", "CALSCALE:GREGORIAN"]
for i, (ds, hh, mm, dur, summ, loc, status, allday) in enumerate(EV):
    dt = D(ds)
    ics.append("BEGIN:VEVENT")
    ics.append(f"UID:hv-{dt.strftime('%Y%m%d')}-{i:04d}@example.com")
    ics.append("DTSTAMP:20261022T000000Z")
    if allday:
        ics.append(f"DTSTART;VALUE=DATE:{dt.strftime('%Y%m%d')}")
        ics.append(f"DTEND;VALUE=DATE:{(dt + timedelta(days=1)).strftime('%Y%m%d')}")
    else:
        e = hh * 60 + mm + dur
        ics.append(f"DTSTART:{dt.strftime('%Y%m%d')}T{hh:02d}{mm:02d}00")
        ics.append(f"DTEND:{dt.strftime('%Y%m%d')}T{min(e // 60, 23):02d}{e % 60:02d}00")
    ics.append(f"SUMMARY:{summ}")
    if loc:
        ics.append(f"LOCATION:{loc}")
    ics.append(f"STATUS:{status}")
    ics.append("END:VEVENT")
ics.append("END:VCALENDAR")
W("calendar/hollis_calendar.ics", "\r\n".join(ics))


# ---------------------------------------------------------------- write / check
def write_all():
    if os.path.isdir(LIFE):
        shutil.rmtree(LIFE)
    for p in sorted(FILES):
        full = os.path.join(LIFE, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(FILES[p].encode("utf-8"))


def rd(p):
    with open(os.path.join(LIFE, p), encoding="utf-8") as fh:
        return fh.read()


def rows(p):
    return list(csv.DictReader(io.StringIO(rd(p))))


def check():
    res = []

    def ok(name, cond, why):
        res.append((name, bool(cond), why))

    # N1
    def cb_rate(y):
        rs = [r for r in rows(f"work/tuning_log_{y}.csv") if r["work"] != "return visit"]
        return sum(1 for r in rs if r["callback"] == "Y") / len(rs)
    ics_txt = rd("calendar/hollis_calendar.ics")
    blocks = ics_txt.split("BEGIN:VEVENT")
    canc = [b for b in blocks if "Hearing Center" in b and "STATUS:CANCELLED" in b]
    hc = [p for p in os.listdir(os.path.join(LIFE, "mail/inbox")) if "reschedule" in p]
    r23, r26 = cb_rate(2023), cb_rate(2026)
    ok("N1", r26 >= 0.12 and r23 <= 0.05 and len(canc) >= 2 and hc,
       f"callback rate 2023={r23:.3f} 2026={r26:.3f}; cancelled hearing appts={len(canc)}; reschedule mail={len(hc)}")
    # N2
    s25 = rd("money/bank/checking_2025-09.csv").count("EST TAX")
    s26 = rd("money/bank/checking_2026-09.csv").count("EST TAX")
    led = rd("money/taxes/estimates_2026.txt")
    q3_blank = re.search(r"^Q3  Sep 15\s*$", led, re.M) is not None
    rem = os.path.exists(os.path.join(LIFE, "mail/inbox/2026-09-02_september_estimate.eml"))
    ok("N2", s25 == 1 and s26 == 0 and q3_blank and rem,
       f"Sep est-tax lines 2025={s25} 2026={s26}; 2026 ledger Q3 blank={q3_blank}; reminder mail={rem}")
    # N3
    vl = rows("van/van_log.csv")
    d23 = sum(1 for r in vl if r["date"].startswith("2023") and "Dad" in r["where / why"])
    d26 = sum(1 for r in vl if r["date"].startswith("2026") and "Dad" in r["where / why"])
    ruth = rd("mail/inbox/2026-08-19_your_dad.eml")
    rep = sum(1 for ln in rd("messages/sms_dad_2026.txt").splitlines() if "] Dad:" in ln and "plumber" in ln)
    ok("N3", d26 >= 2.5 * d23 and "mailbox" in ruth and rep >= 3,
       f"Dad trips 2023={d23} 2026={d26}; neighbour mail mentions mailbox; repeated question x{rep}")
    # N4
    sh = rd("van/service_history.txt")
    last = int(vl[-1]["odo_end"])
    ok("N4", "158,000" in sh and 157000 <= last < 158000,
       f"belt due 158,000 in service history={'158,000' in sh}; current odometer={last:,}")
    # N5
    grad = [b for b in blocks if "20260516T" in b]
    nl = [ln for ln in rd("messages/sms_nell_2025.txt").splitlines() if "] Nell:" in ln]
    nl26 = [ln for ln in rd("messages/sms_nell_2026.txt").splitlines()
            if "] Nell:" in ln and ln[1:11] > "2026-05-16"]
    per25, per26 = len(nl) / 12, len(nl26) / (157 / 30.4)
    draft = os.path.exists(os.path.join(LIFE, "notes/2026-06-02_draft_nell.txt"))
    ok("N5", len(grad) >= 2 and per26 < 0.4 * per25 and draft,
       f"events on 2026-05-16={len(grad)}; Nell msgs/month 2025={per25:.1f} after 5/16={per26:.1f}; draft={draft}")
    # N6
    wl = rd("weiler/restoration_log.txt")
    dates = re.findall(r"^(\d{4})-\d\d-\d\d", wl, re.M)
    hyg = rows("weiler/garage_hygrometer.csv")
    mx26 = max(int(r["rh_pct"]) for r in hyg if r["date"].startswith("2026"))
    mx22 = max(int(r["rh_pct"]) for r in hyg if r["date"].startswith("2022"))
    tarp = "tarp" in rd("notes/2026-07-23_garage.txt")
    ok("N6", max(dates) <= "2023" and mx26 >= 75 and mx22 < 70 and tarp,
       f"last restoration entry {max(dates)}; garage max RH 2022={mx22} 2026={mx26}; tarp note={tarp}")
    # N7
    inv = rd(f"work/invoices/{THEATRE_INV}.txt")
    paid = any(THEATRE_INV in rd(f"money/bank/{p}") for p in os.listdir(os.path.join(LIFE, "money/bank")))
    sent = [p for p in os.listdir(os.path.join(LIFE, "mail/sent")) if THEATRE_INV.lower().replace("-", "_") in p]
    ok("N7", "640.00" in inv and not paid and len(sent) >= 3,
       f"{THEATRE_INV} $640.00 exists; deposit found={paid}; reminders sent={len(sent)}")
    bad = 0
    for name, good, why in res:
        print(f"{name} {'ok' if good else 'MISSING'}: {why}")
        bad += (not good)
    return bad


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(1 if check() else 0)
    write_all()
    total = sum(len(v.encode("utf-8")) for v in FILES.values())
    print(f"wrote {len(FILES)} files, {total:,} bytes to {LIFE}  (min bank balance {MIN_BAL:,.2f})")
