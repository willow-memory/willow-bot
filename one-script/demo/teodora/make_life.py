#!/usr/bin/env python3
"""Write the files of Teodora Marinescu's life into ./life/ (deterministic).

Usage:
    python3 make_life.py          # (re)write life/
    python3 make_life.py --check  # verify planted evidence is present on disk
"""

import argparse
import csv
import io
import random
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 4471
TODAY = date(2026, 10, 22)
HERE = Path(__file__).resolve().parent
LIFE = HERE / "life"

rng = random.Random(SEED)
FILES = {}

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]
LONG_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def put(path, text):
    if not text.endswith("\n"):
        text += "\n"
    if path in FILES:
        raise SystemExit("duplicate path: " + path)
    FILES[path] = text


def D(s):
    return date.fromisoformat(s)


def month_end(y, m):
    if m == 12:
        return date(y, 12, 31)
    return date(y, m + 1, 1) - timedelta(days=1)


def months(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def uk(d):
    return d.strftime("%d/%m/%Y")


def long_date(d):
    return "%d %s %d" % (d.day, LONG_MONTHS[d.month - 1], d.year)


def rfc_date(d, hh=9, mm=0):
    off = "+0100" if 4 <= d.month <= 10 else "+0000"
    return "%s, %02d %s %d %02d:%02d:00 %s" % (
        DAYS[d.weekday()],
        d.day,
        MONTHS[d.month - 1],
        d.year,
        hh,
        mm,
        off,
    )


def to_csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def money(x):
    return "%.2f" % x


TEO_EMAIL = "teo@marinescu-books.example.com"


def eml(d, frm, to, subject, body, hh=9, mm=0, slug=None):
    slug = slug or "".join(c if c.isalnum() else "-" for c in subject.lower()).strip(
        "-"
    )
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug[:48].strip("-")
    text = (
        "From: %s\nTo: %s\nDate: %s\nSubject: %s\nContent-Type: text/plain; charset=utf-8\n\n%s"
        % (
            frm,
            to,
            rfc_date(d, hh, mm),
            subject,
            body.strip("\n"),
        )
    )
    put("email/%s_%s.eml" % (d.isoformat(), slug), text)


# ---------------------------------------------------------------------------
# Work: clients, invoices, ledger
# ---------------------------------------------------------------------------

CLIENTS = [
    # key, name, payer string, start, end, lo, hi, lag range
    (
        "hargreaves",
        "Hargreaves Plumbing & Heating Ltd",
        "HPS LTD BACS",
        (2022, 1),
        (2026, 9),
        470,
        560,
        (8, 20),
    ),
    (
        "bramble",
        "Bramble & Bean Ltd",
        "BRAMBLE AND BEAN LTD",
        (2022, 1),
        (2026, 8),
        600,
        700,
        (10, 20),
    ),
    (
        "petalrow",
        "Petal Row Flowers",
        "PETAL ROW FLOWERS",
        (2022, 1),
        (2026, 9),
        290,
        360,
        (5, 15),
    ),
    (
        "okonjo",
        "Okonjo Electrical Ltd",
        "OKONJO ELEC LTD",
        (2022, 4),
        (2025, 6),
        380,
        450,
        (6, 18),
    ),
    (
        "fernside",
        "Fernside Dog Grooming",
        "FERNSIDE GROOMING",
        (2024, 3),
        (2026, 9),
        160,
        200,
        (3, 12),
    ),
]
CLIENT_BY_KEY = {c[0]: c for c in CLIENTS}

BB_2026 = {  # Bramble & Bean 2026: amount override, lag (None = unpaid)
    1: (655.0, 28),
    2: (640.0, 33),
    3: (660.0, 38),
    4: (670.0, 41),
    5: (690.0, 44),
    6: (680.0, None),
    7: (720.0, None),
    8: (740.0, None),
}
LEDGER_UPDATED = D("2026-10-08")

invoices = []  # dicts
inv_no = 201
for y, m in months((2022, 1), (2026, 9)):
    for key, name, payer, start, end, lo, hi, lag in CLIENTS:
        if not (start <= (y, m) <= end):
            continue
        idate = month_end(y, m)
        amt = float(rng.randint(lo * 4, hi * 4)) / 4
        paid = idate + timedelta(days=rng.randint(*lag))
        ref = "INV%04d" % inv_no
        if key == "bramble" and y == 2026:
            amt, lg = BB_2026[m]
            paid = None if lg is None else idate + timedelta(days=lg)
        if key == "hargreaves":
            ref = "HPS-%s%s" % (MONTHS[m - 1].upper(), str(y)[2:])
            if (y, m) == (2026, 8):
                amt, paid = 520.0, D("2026-09-02")
            if (y, m) == (2026, 9):
                paid = D("2026-10-12")
        if key == "petalrow" and (y, m) == (2026, 9):
            paid = D("2026-10-06")
        if key == "fernside" and (y, m) == (2026, 9):
            paid = None
        if paid is not None and paid > D("2026-10-21"):
            paid = None
        status = "paid"
        if paid is None or paid > LEDGER_UPDATED:
            status = "sent"
        if key == "hargreaves" and (y, m) == (2026, 8):
            status = "sent"  # paid, but the ledger was never updated
        invoices.append(
            dict(
                no="INV-%04d" % inv_no,
                key=key,
                client=name,
                payer=payer,
                date=idate,
                due=idate + timedelta(days=30),
                period="%s %d" % (LONG_MONTHS[m - 1], y),
                amount=amt,
                paid=paid,
                status=status,
                ref=ref,
            )
        )
        inv_no += 1

ledger_rows = []
for inv in invoices:
    ledger_rows.append(
        [
            inv["no"],
            inv["date"].isoformat(),
            inv["client"],
            inv["period"],
            money(inv["amount"]),
            inv["due"].isoformat(),
            inv["status"],
            inv["paid"].isoformat() if inv["status"] == "paid" and inv["paid"] else "",
        ]
    )
put(
    "work/invoices/invoice_ledger.csv",
    to_csv(
        [
            "invoice",
            "date",
            "client",
            "period",
            "amount_gbp",
            "due",
            "status",
            "paid_on",
        ],
        ledger_rows,
    ),
)

SERVICES = {
    "hargreaves": [
        "Monthly bookkeeping and bank reconciliation",
        "Supplier invoice processing (Plumb Centre, merchants)",
        "CIS deductions summary",
    ],
    "bramble": [
        "Monthly bookkeeping and bank reconciliation",
        "Till Z-report reconciliation",
        "Payroll journal (4 staff)",
    ],
    "petalrow": [
        "Monthly bookkeeping",
        "Card terminal settlement reconciliation",
    ],
    "okonjo": [
        "Monthly bookkeeping",
        "VAT return preparation",
    ],
    "fernside": [
        "Monthly bookkeeping (cash basis)",
    ],
}

for inv in invoices:
    if inv["date"] < D("2025-07-01"):
        continue
    lines = SERVICES[inv["key"]]
    body = [
        "INVOICE %s" % inv["no"],
        "",
        "Teodora Marinescu Bookkeeping",
        "Flat 2, 9 Example Row, Leeds LS99 9ZZ",
        TEO_EMAIL + "  |  0113 555 0147",
        "",
        "Bill to: %s" % inv["client"],
        "Invoice date: %s" % uk(inv["date"]),
        "Payment due: %s (30 days)" % uk(inv["due"]),
        "",
        "Services for %s:" % inv["period"],
    ]
    for ln in lines:
        body.append("  - " + ln)
    body += [
        "",
        "Total due: £%s" % money(inv["amount"]),
        "(Not VAT registered.)",
        "",
        "Pay by bank transfer to: T MARINESCU",
        "Northbank  sort code 00-00-00  account 00012345",
        "Please quote %s as reference." % inv["no"],
    ]
    put("work/invoices/%s_%s.txt" % (inv["no"], inv["key"]), "\n".join(body))


def find_inv(key, y, m):
    for inv in invoices:
        if inv["key"] == key and (inv["date"].year, inv["date"].month) == (y, m):
            return inv
    raise KeyError((key, y, m))


BB_JUN = find_inv("bramble", 2026, 6)
BB_JUL = find_inv("bramble", 2026, 7)
BB_AUG = find_inv("bramble", 2026, 8)
HPS_AUG = find_inv("hargreaves", 2026, 8)
BB_UNPAID_TOTAL = BB_JUN["amount"] + BB_JUL["amount"] + BB_AUG["amount"]

# ---------------------------------------------------------------------------
# Bank statements
# ---------------------------------------------------------------------------

tx = []  # (date, type, description, reference, amount)


def add(d, typ, desc, ref, amt):
    tx.append((d, typ, desc, ref, round(amt, 2)))


for inv in invoices:
    if inv["paid"] is not None:
        ref = inv["ref"] if inv["key"] == "hargreaves" else inv["no"].replace("-", "")
        add(inv["paid"], "FPI", inv["payer"], ref, inv["amount"])

for y, m in months((2022, 1), (2026, 10)):
    rent = 540.0 if y < 2024 else 575.0
    add(date(y, m, 1), "SO", "HOLMWOOD LETTINGS", "RENT FLAT 2 HALF", -rent)
    add(date(y, m, 2), "SO", "S OKAFOR", "JOINT POT", -300.0)
    energy = 112.0 if m in (11, 12, 1, 2, 3) else 68.0
    add(date(y, m, 8), "DD", "NORTHERN ENERGY", "ACC 7700-00-123", -energy)
    add(date(y, m, 12), "DD", "VELA MOBILE", "07700900147", -14.0)
    add(date(y, m, 15), "DD", "HARBOUR PENSIONS", "SIPP 000123", -150.0)
    add(date(y, m, 28), "SO", "T MARINESCU", "TO SAVINGS 00098765", -160.0)
    add(date(y, m, 22), "DD", "STREAMBOX", "MEMBERSHIP", -10.99)
    if m in (1, 7):
        add(
            date(y, m, 30),
            "BP",
            "HMRC SELF ASSESSMENT",
            "0000000000K",
            -float(rng.randint(900, 1600)),
        )
    last = month_end(y, m).day
    if (y, m) == (2026, 10):
        last = 21
    for _ in range(rng.randint(5, 8)):
        shop = rng.choice(
            [
                "ALDI STORES",
                "TESCO STORES",
                "CO-OP FOOD",
                "KIRKSTALL BAKERY",
                "CORNER CAFE",
                "BOOTS",
                "WEST YORKS BUS",
                "PETROL STN",
                "CHARITY SHOP",
            ]
        )
        dd = date(y, m, rng.randint(1, last))
        add(dd, "DEB", shop, "CARD", -rng.randint(250, 7800) / 100.0)

SPECIAL_TX = [
    ("2023-09-15", "DEB", "NORTHGATE ACC COLLEGE", "CARD", -1195.00),
    ("2023-11-04", "DEB", "LARCHMONT MUTUAL", "PI 2023-24", -164.50),
    ("2024-11-04", "DEB", "LARCHMONT MUTUAL", "PI 2024-25", -171.20),
    ("2025-11-04", "DEB", "LARCHMONT MUTUAL", "PI 2025-26", -179.80),
    ("2024-05-14", "DEB", "KINETIC PHYSIO LEEDS", "CARD", -45.00),
    ("2024-05-28", "DEB", "KINETIC PHYSIO LEEDS", "CARD", -45.00),
    ("2024-06-11", "DEB", "KINETIC PHYSIO LEEDS", "CARD", -45.00),
    ("2025-03-04", "DEB", "KINETIC PHYSIO LEEDS", "CARD", -45.00),
    ("2025-03-18", "DEB", "KINETIC PHYSIO LEEDS", "CARD", -45.00),
    ("2024-12-04", "FPI", "D RAMAN", "BOILER", 600.00),
    ("2024-12-06", "DEB", "WARMLINE HEATING", "CARD", -1840.00),
    ("2025-02-28", "FPO", "D RAMAN", "BOILER 1", -100.00),
    ("2025-05-30", "FPO", "D RAMAN", "BOILER PT2", -150.00),
    ("2025-08-10", "FPO", "ANA MARINESCU", "ROCHIE", -170.00),
    ("2025-09-02", "FPI", "ANA MARINESCU", "INAPOI MERSI", 170.00),
    ("2024-11-08", "DEB", "KIRKGATE AUTOS", "CARD", -214.60),
    ("2025-11-10", "DEB", "KIRKGATE AUTOS", "CARD", -54.85),
    ("2026-03-14", "DEB", "KIRKGATE AUTOS", "CARD", -96.40),
    ("2026-08-14", "DEB", "ONLINE MARKETPLACE", "CARD", -18.99),
    ("2025-07-12", "DEB", "LEMONGRASS AND LIME", "CARD", -47.30),
    ("2026-05-30", "DEB", "INKWELL PRINT", "SAMPLE PAGES", -12.00),
]
for ds, typ, desc, ref, amt in SPECIAL_TX:
    add(D(ds), typ, desc, ref, amt)

tx = [t for t in tx if t[0] <= D("2026-10-21")]  # statement exported 22 Oct
tx.sort(key=lambda t: (t[0], -t[4] if t[4] > 0 else 0))
bal = 4710.55
by_month = {}
for d, typ, desc, ref, amt in tx:
    bal = round(bal + amt, 2)
    by_month.setdefault((d.year, d.month), []).append(
        [d.isoformat(), typ, desc, ref, money(amt), money(bal)]
    )
for (y, m), rows in sorted(by_month.items()):
    put(
        "bank/northbank_current_%d-%02d.csv" % (y, m),
        to_csv(["date", "type", "description", "reference", "amount", "balance"], rows),
    )

# ---------------------------------------------------------------------------
# Client notes and engagement letters
# ---------------------------------------------------------------------------

PI_CLAUSE = (
    "7. Insurance. The bookkeeper confirms that she holds professional\n"
    "indemnity insurance of not less than £100,000 and will maintain it for\n"
    "the duration of this engagement. Lapse of cover is grounds for the\n"
    "client to suspend the engagement without notice."
)
for key, name, payer, start, end, lo, hi, lag in CLIENTS:
    sd = date(start[0], start[1], 1) - timedelta(days=12)
    put(
        "work/clients/%s/engagement_letter_%d.txt" % (key, sd.year),
        'LETTER OF ENGAGEMENT\n\nBetween Teodora Marinescu ("the bookkeeper") and %s ("the client").\n'
        "Dated %s.\n\n1. Scope. Monthly bookkeeping as agreed by email.\n"
        "2. Records. The client will supply records by the 5th of each month.\n"
        "3. Fees. Invoiced monthly in arrears, payable within 30 days.\n"
        "4. Late payment. Statutory interest may be charged on overdue sums.\n"
        "5. Confidentiality. Standard.\n6. Termination. One month's notice either side.\n%s\n\n"
        "Signed: T. Marinescu      Signed for the client: ____________\n"
        % (name, long_date(sd), PI_CLAUSE),
    )

put(
    "work/clients/hargreaves/notes.md",
    """# Hargreaves Plumbing & Heating

- Contact: Gary Hargreaves, gary@hargreaves-plumbing.example.com, 0113 555 0122
- Office manager does the paperwork: Lynn. Drops a folder on the 3rd.
- CIS subcontractors: 3 regulars. Watch the CIS statements every month.
- They pay by BACS and use their own reference (HPS-MONYY), NOT my invoice number.
  Sometimes they lump two months together. Match on amount.
- Year end 31 March. Accountant: Fold & Co (they do the accounts, I do the books).
""",
)
put(
    "work/clients/bramble/notes.md",
    """# Bramble & Bean (café)

- Contact: Kirsty Pell, kirsty@brambleandbean.example.com, 0113 555 0168
- Receipts come in a shoebox, literally. Box usually arrives by the 6th.
- Till: Z-reports daily, photo them if the printer jams (it always jams).
- 4 staff on payroll, part-time students mostly.
- 2023: second site idea, dropped.
- 2025-11: rent review on the unit, landlord wants +18%. Kirsty upset.
- 2026-03: Kirsty asked about moving to quarterly invoicing to "smooth cashflow". Said I'd think about it.
""",
)
put(
    "work/clients/petalrow/notes.md",
    """# Petal Row Flowers

- Contact: Hannah Bryce, hannah@petalrow.example.org
- Card terminal settlements land 2 days late; reconcile by settlement date.
- Peak: Valentine's, Mother's Day, Christmas. Everything else quiet.
- Year end 31 Dec.
""",
)
put(
    "work/clients/okonjo/notes.md",
    """# Okonjo Electrical

- Contact: Chidi Okonjo, accounts@okonjo-electrical.example.net
- VAT quarterly (Mar/Jun/Sep/Dec).
- 2025-05: Chidi selling up, retiring to Lagos. Final month June 2025.
- Sent final handover pack to the buyer's accountant 2025-07-04. Done.
""",
)
put(
    "work/clients/fernside/notes.md",
    """# Fernside Dog Grooming

- Contact: Mel Rook, mel@fernside-grooming.example.com
- Tiny. Cash basis. Mostly a spreadsheet and a jar.
- Referred by Hannah (Petal Row) Feb 2024.
""",
)

# ---------------------------------------------------------------------------
# Insurance, tax
# ---------------------------------------------------------------------------

for start, prem in (("2024-11-04", 171.20), ("2025-11-04", 179.80)):
    s = D(start)
    e = date(s.year + 1, 11, 3)
    put(
        "work/insurance/PI_schedule_%d-%s.txt" % (s.year, str(e.year)[2:]),
        """LARCHMONT MUTUAL — PROFESSIONAL INDEMNITY SCHEDULE

Policyholder:        Teodora Marinescu t/a Teodora Marinescu Bookkeeping
Policy number:       LM-PI-000-4471
Business activity:   Bookkeeping services (not audit, not tax advice)
Limit of indemnity:  £100,000 any one claim
Excess:              £250
Period of insurance: %s to %s (both dates inclusive)
Premium:             £%s inc. IPT
Payment method:      Debit card ending 1111 (exp 09/26)
Renewal:             Continuous payment authority — the policy will renew
                     automatically using the card held on file unless you
                     tell us otherwise.

Data protection extension: included
Run-off cover: not included
"""
        % (uk(s), uk(e), money(prem)),
    )

put(
    "work/insurance/PI_schedule_2023-24.txt",
    """LARCHMONT MUTUAL — PROFESSIONAL INDEMNITY SCHEDULE

Policyholder:        Teodora Marinescu
Policy number:       LM-PI-000-4471
Limit of indemnity:  £100,000 any one claim
Period of insurance: 04/11/2023 to 03/11/2024
Premium:             £164.50 inc. IPT
Payment method:      Debit card ending 4242 (exp 03/24)
""",
)


def tax_year_turnover(start_year):
    lo, hi = date(start_year, 4, 6), date(start_year + 1, 4, 5)
    return sum(i["amount"] for i in invoices if lo <= i["date"] <= hi)


for ty, exp in ((2023, 1312.40), (2024, 1420.15)):
    t = tax_year_turnover(ty)
    put(
        "work/tax/self_assessment_%d-%s_summary.txt" % (ty, str(ty + 1)[2:]),
        """Self assessment — tax year 6 April %d to 5 April %d
Self-employment: bookkeeping (cash basis)

Turnover (invoiced):            £%s
Allowable expenses:             £%s
  software subs, PI insurance, phone (50%%), mileage, stationery
Net profit:                     £%s

Filed online %s. Payments on account set up for 31 Jan / 31 Jul.
Notes: AAT course fee NOT claimed (new qualification, not allowable — checked).
"""
        % (
            ty,
            ty + 1,
            money(t),
            money(exp),
            money(t - exp),
            "2025-01-19" if ty == 2023 else "2026-01-24",
        ),
    )

put(
    "work/tax/expenses_2026-27.csv",
    to_csv(
        ["date", "item", "category", "amount"],
        [
            ["2026-04-09", "Bookkeeping software (annual)", "software", "216.00"],
            ["2026-05-02", "Printer ink", "stationery", "24.99"],
            ["2026-06-11", "Mileage — Hargreaves site visit 18mi", "travel", "8.10"],
            ["2026-07-20", "Lever arch files x6", "stationery", "17.40"],
            ["2026-08-14", "Wrist support brace", "?", "18.99"],
            ["2026-09-03", "Phone bill 50%", "phone", "7.00"],
            ["2026-10-03", "Phone bill 50%", "phone", "7.00"],
        ],
    ),
)

# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------

KIRSTY = "Kirsty Pell <kirsty@brambleandbean.example.com>"
TEO = "Teodora Marinescu <%s>" % TEO_EMAIL

eml(
    D("2026-07-29"),
    TEO,
    KIRSTY,
    "%s — June books" % BB_JUN["no"],
    """Hi Kirsty,

Just a nudge on %s for June (£%s), due tomorrow. Shoebox for July
whenever you're ready — no rush on that one.

Thanks!
Teo"""
    % (BB_JUN["no"], money(BB_JUN["amount"])),
    hh=10,
    mm=12,
)
eml(
    D("2026-08-31"),
    TEO,
    KIRSTY,
    "%s and %s" % (BB_JUN["no"], BB_JUL["no"]),
    """Hi Kirsty,

Hope the summer's been busy in a good way. %s (June) is now a month
overdue and %s (July, £%s) falls due tomorrow. Could you let me know
when to expect them? August invoice to follow today.

Teo"""
    % (BB_JUN["no"], BB_JUL["no"], money(BB_JUL["amount"])),
    hh=16,
    mm=40,
)
eml(
    D("2026-09-12"),
    KIRSTY,
    TEO,
    "Re: %s and %s" % (BB_JUN["no"], BB_JUL["no"]),
    """Teo I'm so sorry, I know. Cashflow has been mad this summer — the
landlord thing and the coffee price thing at the same time. Bear with me,
I'll sort all of it at the end of the month, promise.

Box for August is in the back, I'll drop it round.

K x

Sent from my phone""",
    hh=22,
    mm=51,
)
eml(
    D("2026-10-06"),
    TEO,
    KIRSTY,
    "Outstanding invoices",
    """Hi Kirsty,

Following up — I haven't seen anything come through yet for:

  %s  June 2026    £%s
  %s  July 2026    £%s
  %s  August 2026  £%s

That's £%s in total. I also haven't had the September box so I've held
off invoicing September. Can we have a quick call this week?

Teo"""
    % (
        BB_JUN["no"],
        money(BB_JUN["amount"]),
        BB_JUL["no"],
        money(BB_JUL["amount"]),
        BB_AUG["no"],
        money(BB_AUG["amount"]),
        money(BB_UNPAID_TOTAL),
    ),
    hh=9,
    mm=5,
)
eml(
    D("2026-09-02"),
    "Lynn at Hargreaves <accounts@hargreaves-plumbing.example.com>",
    TEO,
    "Remittance advice",
    """REMITTANCE ADVICE

Hargreaves Plumbing & Heating Ltd has made the following payment:

  Payee:      T MARINESCU
  Amount:     £520.00
  Method:     BACS
  Reference:  HPS-AUG26
  Date:       02/09/2026

This is an automated message.""",
    hh=7,
    mm=30,
)
eml(
    D("2026-09-20"),
    "Northbank Alerts <alerts@northbank.example.com>",
    TEO,
    "Your replacement debit card is on its way",
    """Hello Teodora,

Your debit card ending 1111 expires at the end of September 2026. Your
new card, ending 5556, has been posted to your home address and should
arrive within 5 working days.

Remember: your card number is changing. Regular payments set up with
your old card number (subscriptions, insurance, online shops) will need
to be updated with the new card details. Direct Debits and standing
orders are not affected.

Northbank""",
    hh=6,
    mm=2,
)
eml(
    D("2026-10-06"),
    "Larchmont Mutual <renewals@larchmont-mutual.example.com>",
    TEO,
    "Your professional indemnity policy LM-PI-000-4471 — renewal",
    """Dear Ms Marinescu,

Thank you for insuring with Larchmont Mutual. Your professional
indemnity policy LM-PI-000-4471 is due for renewal.

  Renewal date:   04/11/2026
  New premium:    £186.40 inc. IPT
  Payment:        Continuous payment authority, card ending 1111

We will collect the premium on or after 27/10/2026. If your payment
details have changed, please log in to your account or call us on
0800 555 0190. If we are unable to collect payment your cover will
end on 03/11/2026.

Kind regards,
Renewals Team""",
    hh=8,
    mm=0,
    slug="pi-renewal-2026",
)
eml(
    D("2025-10-06"),
    "Larchmont Mutual <renewals@larchmont-mutual.example.com>",
    TEO,
    "Your professional indemnity policy LM-PI-000-4471 — renewal",
    """Dear Ms Marinescu,

Your professional indemnity policy LM-PI-000-4471 is due for renewal.

  Renewal date:   04/11/2025
  New premium:    £179.80 inc. IPT
  Payment:        Continuous payment authority, card ending 1111

No action is needed if your details are unchanged.

Renewals Team""",
    hh=8,
    mm=0,
    slug="pi-renewal-2025",
)
eml(
    D("2025-11-04"),
    "Larchmont Mutual <renewals@larchmont-mutual.example.com>",
    TEO,
    "Payment received — thank you",
    "We have received £179.80. Your policy runs 04/11/2025 to 03/11/2026.\nYour schedule is attached.",
    hh=11,
    mm=20,
)
eml(
    D("2026-10-14"),
    "HM Revenue Refunds <refunds@hmrc-taxrefund.example.net>",
    TEO,
    "URGENT: You are eligible for a tax refund of £412.67",
    """Dear Taxpayer,

After the last annual calculation of your fiscal activity we have
determined that you are eligible to receive a tax refund of £412.67.

You must claim within 48 HOURS or the refund will be cancelled.

Click here to verify your identity and bank card:
http://192.0.2.44/refund/claim?id=88213

HM Revenue Refunds Department""",
    hh=3,
    mm=17,
)
eml(
    D("2026-10-18"),
    "SheetSmart <noreply@sheetsmart.example.com>",
    TEO,
    "Your free trial ends in 3 days",
    """Hi Teodora,

Your SheetSmart Pro trial (Advanced Excel Formulas course) ends on
21 October. After that you'll be moved to the free plan and lose access
to Pro lessons. Upgrade now for £9.99/month to keep learning!

Unsubscribe: https://sheetsmart.example.com/u/1""",
    hh=12,
    mm=0,
)
eml(
    D("2023-09-15"),
    "Northgate Accountancy College <enrol@northgate-college.example.org>",
    TEO,
    "Welcome to AAT Level 4 Diploma in Professional Accounting",
    """Dear Teodora,

Welcome! Your enrolment on the AAT Level 4 Diploma (distance learning)
is confirmed. Fee paid: £1,195.00.

Your package includes tutor support, course materials and one sitting
of each unit assessment. Your access to course materials, tutor support
and exam booking runs until 31 March 2027. Extensions are not available
on this package.

Units:
  Mandatory: Drafting and Interpreting Financial Statements (DAIF)
             Applied Management Accounting (APMA)
             Internal Accounting Systems and Controls (INAC)
  Optional (choose 2): Business Tax (BSTX), Personal Tax (PLTX),
             Audit and Assurance (AUDT), Cash and Financial Management (CSFT)

Good luck!
Student Services""",
    hh=14,
    mm=2,
    slug="aat-welcome",
)
for d, subj, body in (
    (
        "2024-06-21",
        "Assessment result: Personal Tax (PLTX)",
        "Congratulations — you achieved a PASS (78%) in Personal Tax.",
    ),
    (
        "2024-02-09",
        "Assessment result: Applied Management Accounting (APMA)",
        "Congratulations — you achieved a PASS (71%) in Applied Management Accounting.",
    ),
    (
        "2023-12-15",
        "Assessment result: Drafting and Interpreting Financial Statements (DAIF)",
        "Congratulations — you achieved a PASS (74%) in DAIF.",
    ),
    (
        "2025-04-02",
        "We haven't seen you in a while",
        "Hi Teodora, it's been a while since you logged in. Your tutor is here when you're ready.",
    ),
    (
        "2026-08-11",
        "Keep going — you're 3/5 of the way there",
        "Hi Teodora, you have 2 units left to complete your diploma. Book your next assessment from the student portal.",
    ),
    (
        "2026-10-20",
        "Your learning account: important dates",
        """Hi Teodora,

A reminder that your learning account closes on 31 March 2027. Units
remaining: Internal Accounting Systems and Controls (INAC), Business Tax
(BSTX). Assessment slots in February and March book up quickly — we
recommend booking at least 6 weeks ahead.

Student Services""",
    ),
):
    eml(
        D(d),
        "Northgate Accountancy College <students@northgate-college.example.org>",
        TEO,
        subj,
        body,
        hh=10,
        mm=0,
    )

eml(
    D("2026-05-29"),
    "Teodora Marinescu <teo.marinescu@example.net>",
    "Inkwell Print <hello@inkwellprint.example.com>",
    "Small run of hardback books — quote?",
    """Hello,

I'm looking for a quote for a small run of hardback books, A5, about
70 pages, mostly text with maybe 20 photos (old family photos, scanned).
Around 40 copies. They'd need to be in Romania (Cluj) in the first week
of June 2027 — I could carry them over myself if that's easier.

Is there anything I should be thinking about now?

Thanks,
Teo""",
    hh=21,
    mm=33,
)
eml(
    D("2026-06-02"),
    "Inkwell Print <hello@inkwellprint.example.com>",
    "teo.marinescu@example.net",
    "Re: Small run of hardback books — quote?",
    """Hi Teo,

Lovely project. Rough quote: 40 x A5 hardback, 72pp, colour photos,
matte laminate cover — £612 + delivery.

Timings: for anything hardback we need final print-ready artwork 8 weeks
before you need the books in hand, plus a proof round (allow a week).
For delivery before you fly in early June that means files with us by
mid-April 2027 at the very latest. We get busy in spring (wedding season!)
so earlier is kinder.

I've posted you a couple of sample pages on our heavier paper (£12).

Best,
Rosa
Inkwell Print""",
    hh=11,
    mm=8,
)
eml(
    D("2025-06-03"),
    "Chidi Okonjo <accounts@okonjo-electrical.example.net>",
    TEO,
    "Last month",
    "Teo — confirming June will be the last month. Thank you for 3 good years. Chidi",
    hh=15,
    mm=0,
)
eml(
    D("2024-02-14"),
    "Mel Rook <mel@fernside-grooming.example.com>",
    TEO,
    "Hannah said you might take me on",
    "Hi! Hannah at Petal Row said you're brilliant and not scary. I have a jar of receipts. Help. Mel",
    hh=19,
    mm=45,
)
for i, (d, subj) in enumerate(
    [
        ("2026-09-04", "Welcome back! Autumn term newsletter"),
        ("2026-10-09", "Year 4 trip to the museum — consent and £6"),
        ("2026-10-16", "Half term reminder and harvest collection"),
        ("2025-12-12", "Nativity tickets and last day of term"),
    ]
):
    eml(
        D(d),
        "Elmfield Primary Office <office@elmfield-primary.example.org>",
        "parents-y4@elmfield-primary.example.org",
        subj,
        [
            "Dear parents and carers,\n\nWelcome back. Please make sure PE kits are in school on Tuesdays and Thursdays. Breakfast club is £3.50 a session.\n\nMrs Ashdown",
            "Dear parents and carers,\n\nYear 4 visit the city museum on Thursday 5 November. Please give consent and pay £6 via the parent app by 30 October.\n\nYear 4 team",
            "Dear parents and carers,\n\nSchool closes for half term on Friday 23 October at 3.15pm and reopens Monday 2 November. Harvest collection: tins and packets to the hall by Friday please.\n\nMrs Ashdown",
            "Dear parents and carers,\n\nNativity tickets (2 per family) go out on Monday. Last day of term is Friday 19 December, finishing at 2pm.\n\nMrs Ashdown",
        ][i],
        hh=15,
        mm=30,
    )
NEWS = [
    ("2025-01-14", "Weekend reads: the cash basis threshold explained"),
    ("2025-04-08", "Making Tax Digital for income tax — what bookkeepers need to know"),
    ("2025-09-23", "Five reconciliation habits worth stealing"),
    ("2026-02-17", "MTD ITSA: are your sole traders ready for April?"),
    ("2026-06-30", "Summer webinar: payroll year-end mistakes"),
    ("2026-10-13", "Autumn budget: what we're watching"),
]
for d, subj in NEWS:
    eml(
        D(d),
        "Ledger Lines Newsletter <news@ledgerlines.example.com>",
        TEO,
        subj,
        "This week's issue: %s.\n\nRead online: https://ledgerlines.example.com/issues\n\nYou're receiving this because you subscribed."
        % subj,
        hh=7,
        mm=0,
    )
eml(
    D("2026-03-02"),
    "Okafor & Marinescu Home Insurance <docs@hearthcover.example.com>",
    "sam.okafor@example.net",
    "Your contents policy has renewed",
    "Your contents insurance renewed on 01/03/2026 by Direct Debit. Premium £11.40/month. Nothing else to do.",
    hh=9,
    mm=0,
)

# ---------------------------------------------------------------------------
# Car
# ---------------------------------------------------------------------------

put(
    "car/MOT_2024-11-08.txt",
    """MOT TEST CERTIFICATE (copy) — Kirkgate Autos, test station 00412

Vehicle: SKODA FABIA 1.0 TSI, silver, 2015
Registration: YX15 ZZT
Odometer: 71,204 miles
Test date: 08/11/2024
Result: PASS
Expiry: 07/11/2025

Advisories: none

Work carried out same day (invoice K-8812): front brake pads replaced
(advised at last year's test), wiper blades.
""",
)
put(
    "car/MOT_2025-11-10.txt",
    """MOT TEST CERTIFICATE (copy) — Kirkgate Autos, test station 00412

Vehicle: SKODA FABIA 1.0 TSI, silver, 2015
Registration: YX15 ZZT
Odometer: 78,950 miles
Test date: 10/11/2025
Result: PASS WITH ADVISORIES
Expiry: 09/11/2026

Monitor and repair if necessary (advisories):
  * Nearside front tyre worn close to legal limit (1.8mm) (5.2.3 (e))
  * Offside front tyre worn close to legal limit (2.0mm) (5.2.3 (e))
  * Rear brake pipe corroded, covered in grease or other material (1.1.11 (c))

Tester: J. Mercer
""",
)
put(
    "car/kirkgate_invoice_K-8812.txt",
    "KIRKGATE AUTOS — Invoice K-8812 — 08/11/2024\nYX15 ZZT Skoda Fabia\nMOT test £54.85\nFront brake pads (pair) fitted £139.80\nWiper blades £19.95\nTOTAL £214.60 — paid card\n",
)
put(
    "car/kirkgate_invoice_K-9930.txt",
    "KIRKGATE AUTOS — Invoice K-9930 — 10/11/2025\nYX15 ZZT Skoda Fabia\nMOT test £54.85\nTOTAL £54.85 — paid card\nTyre quote given: 2x 185/60 R15 mid-range £148 fitted. Customer declined today.\n",
)
put(
    "car/kirkgate_invoice_L-0211.txt",
    "KIRKGATE AUTOS — Invoice L-0211 — 14/03/2026\nYX15 ZZT Skoda Fabia\nOil and filter change £79.00\nScreenwash £4.50\nBulb (rear number plate) £12.90\nTOTAL £96.40 — paid card\n",
)
put(
    "car/car_insurance_2026.txt",
    "Car insurance — policy holder S. Okafor, named driver T. Marinescu.\nRenewed 22/04/2026 by Direct Debit, £38.20/month. Insurer: Tollgate Motor (example).\nNo claims: 6 years.\n",
)

# ---------------------------------------------------------------------------
# Home, Maisie, misc notes
# ---------------------------------------------------------------------------

put(
    "home/wifi.txt",
    "Router: NetBox-7F2A\nWiFi password: plum-otter-4471\nAdmin page: http://192.0.2.1  (admin / on the sticker under the router)\nGuest network: NetBox-Guest / maisie-loves-cats\n",
)
put(
    "home/council_tax_2026-27.txt",
    "COUNCIL TAX BILL 2026/27\nProperty: Flat 2, 9 Example Row, Leeds LS99 9ZZ\nBand: B\nAnnual charge: £1,764.30\nPayment: 10 monthly instalments of £176.43 by Direct Debit from 1 April (account: S OKAFOR)\nCouncil tax reference: 000-4471-00\n",
)
put(
    "home/boiler_warranty.txt",
    """WARMLINE HEATING — installation certificate and warranty

Address: Flat 2, 9 Example Row, Leeds LS99 9ZZ
Installed: 06/12/2024 (emergency replacement, old boiler condemned 03/12/2024)
Boiler: combi, 25kW
Warranty: 7 years parts and labour, conditional on annual service.
Next service due: by 06/12/2025 (done 21/11/2025) then by 06/12/2026.

Total cost £3,680.00. Paid: £1,840.00 card (T. Marinescu), £1,840.00 card (S. Okafor).
Landlord contribution: refused (tenant-installed upgrade, see email from Holmwood).
""",
)
put(
    "home/todo.txt",
    """- maisie PE shoes size 1
- library books back (3)
- grace bday card + parcel (14 mar)
- pay on account jan
- boiler service before dec
- ask hannah re valentines rush staffing
- descale kettle
- new glasses?
""",
)
put(
    "maisie/term_dates_2026-27.txt",
    """Elmfield Primary — term dates 2026/27

Autumn 1: Thu 3 Sep 2026 – Fri 23 Oct 2026
Half term: Mon 26 Oct – Fri 30 Oct 2026
Autumn 2: Mon 2 Nov – Fri 18 Dec 2026  (INSET day: Mon 2 Nov — school closed to pupils)
Spring 1: Tue 5 Jan – Fri 12 Feb 2027
Half term: Mon 15 Feb – Fri 19 Feb 2027
Spring 2: Mon 22 Feb – Thu 25 Mar 2027
Summer 1: Mon 12 Apr – Fri 28 May 2027
Summer 2: Mon 7 Jun – Wed 21 Jul 2027
""",
)
put(
    "maisie/schedule_with_jo.md",
    """# Maisie's week (agreed with Jo, Sept 2025)

- Mon/Tue: Jo
- Wed/Thu: us (Sam does Wed pickup, I do Thu)
- Fri–Sun: alternate weekends, handover Friday at school gate
- Holidays: split down the middle, swap Christmas years.
- Jo: 0113 555 0175, jo.whitlam@example.org
- Allergies: none. Hates: mushrooms, "itchy" jumpers.
""",
)
put(
    "maisie/party_invites_2026.txt",
    "Ruby P — Sat 7 Nov, soft play, 2-4pm\nAyaan — Sun 22 Nov, football party, 10am\nMaisie's own (9!) — Sat 16 Jan 2027?? ask Jo re venue\n",
)
put(
    "notes/chair_options.md",
    """# desk chair options (2025-02)

- https://officeseat.example.com/ergo-mesh-200  £179 — adjustable arms, lumbar
- https://officeseat.example.com/task-basic  £89 — no arms
- https://secondhand.example.org/listing/55102  £60 used, collection Headingley
- vertical mouse? https://gadgetshop.example.com/vmouse-2  £24.99

Didn't buy any. Still on the kitchen chair + cushion.
""",
)
put(
    "notes/gift_ideas.md",
    """# gifts

## Sam
- proper chef's knife
- tickets for the comedy thing
## Grace (Sam's mum) — birthday 14 March
- the orchid she liked at Petal Row (ask Hannah for trade price)
- photo calendar of Maisie
## Maisie (Jan)
- art set, the big one
- roller skates?
## Ana & Radu
- (sorted. sort of.)
## Dev & Priya
- something for the baby when it's time
""",
)
put(
    "notes/books_2026.md",
    "- The Overstory (abandoned p.200)\n- Normal People (reread)\n- something Romanian for book club??\n- Atomic Habits (Sam's, ironic)\n",
)
put(
    "notes/packing_cluj.md",
    "# packing — Cluj Christmas 2025\n\n- presents for Mama/Tata, Ana, Radu\n- adapter x2\n- Maisie's colouring\n- Sam's thermals (he will complain anyway)\n- passports!! (Sam's expires 2029, mine 2031)\n",
)
put(
    "notes/thai_takeaway.md",
    "Lemongrass & Lime — Sam's favourite. Order: jungle curry (hot), pad see ew, morning glory.\nPhone 0113 555 0193. They close Mondays.\n",
)
put(
    "notes/physio_exercises.md",
    """# wrist exercises (Kinetic Physio, 4 Mar 2025)

Right wrist — extensor tendons irritated. "Not serious yet."

1. Wrist extension stretch — 30s x3
2. Wrist flexion stretch — 30s x3
3. Eccentric wrist extension with tin of beans — 3 x 15, slow down
4. Tendon glides — 10 each
5. Grip squeeze (sock ball) — 10 x 5s

3 times a day. Breaks from the mouse every 30 min.
Desk: elbow at 90°, wrist neutral, screen at eye height.
Review in 2 weeks, then 4.
""",
)
put(
    "receipts/kinetic_physio_2024-05-14_sam.txt",
    "KINETIC PHYSIO LEEDS — Receipt\nPatient: Sam Okafor\nAssessment — left knee (5-a-side)\n£45.00 paid card (T MARINESCU)\n",
)
put(
    "receipts/kinetic_physio_2024-05-28_sam.txt",
    "KINETIC PHYSIO LEEDS — Receipt\nPatient: Sam Okafor\nFollow-up — left knee\n£45.00 paid card (T MARINESCU)\n",
)
put(
    "receipts/kinetic_physio_2024-06-11_sam.txt",
    "KINETIC PHYSIO LEEDS — Receipt\nPatient: Sam Okafor\nFollow-up — left knee. Discharged. Return to football.\n£45.00 paid card (T MARINESCU)\n",
)
put(
    "receipts/kinetic_physio_2025-03-04.txt",
    "KINETIC PHYSIO LEEDS — Receipt\nPatient: Teodora Marinescu\nAssessment — right wrist/forearm\n£45.00 paid card\nNext appt: 18/03/2025 10:00\n",
)
put(
    "receipts/kinetic_physio_2025-03-18.txt",
    "KINETIC PHYSIO LEEDS — Receipt\nPatient: Teodora Marinescu\nFollow-up — right wrist\n£45.00 paid card\nNext appt: 01/04/2025 10:00\n",
)
put(
    "receipts/order_2026-08-14_wrist_brace.txt",
    "Order confirmation #EX-55-0814\nWrist support brace, right hand, size S/M, with splint — £18.99\nDelivered 16/08/2026\n",
)
put(
    "receipts/lemongrass_and_lime_2025-07-12.txt",
    "LEMONGRASS & LIME\nJungle curry (hot) 13.50\nPad see ew 11.90\nMorning glory 7.50\nSticky rice 3.40\nBeers x2 11.00\nTOTAL 47.30 (card)\n",
)
put(
    "receipts/warmline_2024-12-06.txt",
    "WARMLINE HEATING — Receipt\nBoiler replacement, Flat 2\nPart payment £1,840.00 card (T MARINESCU)\nBalance £1,840.00 card (S OKAFOR)\n",
)
put(
    "receipts/inkwell_samples_2026-05-30.txt",
    "INKWELL PRINT — Receipt\nSample pages, 170gsm silk + 120gsm uncoated, posted\n£12.00 card\n",
)

# ---------------------------------------------------------------------------
# AAT study
# ---------------------------------------------------------------------------

put(
    "study/aat/progress.md",
    """# AAT Level 4

| unit | status | date | mark |
|------|--------|------|------|
| DAIF | passed | 2023-12-15 | 74% |
| APMA | passed | 2024-02-09 | 71% |
| PLTX | passed | 2024-06-21 | 78% |
| BSTX | started | 2024-09 | |
| INAC | | | |
""",
)
put(
    "study/aat/DAIF_notes.md",
    "# DAIF\n\n- IAS 1 formats, SOFP / SPL\n- consolidation: goodwill = consideration + NCI - net assets at acquisition\n- ratios: ROCE, gearing, current, quick, inventory days\n",
)
put(
    "study/aat/APMA_notes.md",
    "# APMA\n\n- variance analysis: material price/usage, labour rate/efficiency\n- lifecycle costing, target costing\n- NPV / IRR quick method\n",
)
put(
    "study/aat/PLTX_notes.md",
    "# PLTX\n\n- income tax computation layout\n- benefits in kind: cars (CO2 %), living accommodation\n- CGT: annual exempt amount, PRR\n- IHT basics, 7-year rule\n",
)
put(
    "study/aat/BSTX_notes.md",
    "# BSTX (started Sept 2024)\n\n- adjustment of profits — add back disallowables\n- capital allowances: AIA, main pool 18%, special rate 6%\n- \n",
)

# ---------------------------------------------------------------------------
# Bunica's recipes
# ---------------------------------------------------------------------------

ALL_RECIPES = [
    ("ciorba-de-perisoare", "Ciorbă de perișoare", 1, "02:10"),
    ("galuste-cu-prune", "Găluște cu prune", 1, "06:45"),
    ("cozonac", "Cozonac", 2, "14:20"),
    ("sarmale", "Sarmale", 1, "09:30"),
    ("mamaliga-cu-branza", "Mămăligă cu brânză și smântână", 1, "13:05"),
    ("zacusca", "Zacuscă", 2, "01:40"),
    ("salata-de-vinete", "Salată de vinete", 2, "05:55"),
    ("placinta-cu-mere", "Plăcintă cu mere", 3, "00:30"),
    ("papanasi", "Papanași", 3, "04:15"),
    ("cornulete", "Cornulețe", 3, "08:00"),
    ("supa-de-gaina-cu-taitei", "Supă de găină cu tăiței de casă", 1, "16:20"),
    ("ardei-umpluti", "Ardei umpluți", 2, "09:10"),
    ("chiftele", "Chiftele", 1, "19:00"),
    ("drob", "Drob de miel", 4, "02:00"),
    ("tochitura", "Tochitură", 4, "05:30"),
    ("varza-calita", "Varză călită", 2, "12:45"),
    ("fasole-batuta", "Fasole bătută", 4, "08:20"),
    ("coltunasi", "Colțunași cu brânză", 3, "11:10"),
    ("pasca", "Pască", 2, "18:30"),
    ("gogosi", "Gogoși", 3, "13:40"),
    ("compot-de-visine", "Compot de vișine", 4, "10:05"),
    ("dulceata-de-caise", "Dulceață de caise", 4, "11:50"),
    ("muraturi", "Murături asortate", 4, "14:00"),
    ("tocanita", "Tocăniță de cartofi", 1, "21:30"),
    ("salata-de-boeuf", "Salată de boeuf", 2, "21:00"),
    ("prajitura-cu-visine", "Prăjitură cu vișine", 3, "15:20"),
    ("salam-de-biscuiti", "Salam de biscuiți", 3, "17:45"),
    ("parjoale", "Pârjoale", 1, "23:10"),
    ("iahnie-de-fasole", "Iahnie de fasole", 4, "16:30"),
    ("placinta-cu-branza", "Plăcintă cu brânză (a lui Tataie)", 4, "19:55"),
]
DONE = {
    "ciorba-de-perisoare": (
        "2026-05-10",
        [
            "500g minced pork and beef",
            "1 onion",
            "2 carrots",
            "1 parsnip",
            "half a cup of rice",
            "1 egg",
            "borș (or lemon)",
            "lovage, a big handful",
        ],
        "Lovage at the end, never at the start, or it goes bitter. And you don't stir after the meatballs go in, you shake the pot.",
    ),
    "galuste-cu-prune": (
        "2026-05-10",
        [
            "1kg floury potatoes",
            "250g flour",
            "1 egg",
            "12 small plums (Romanian ones if you can)",
            "sugar and cinnamon",
            "breadcrumbs fried in butter",
        ],
        "Put a bit of sugar where the stone was. When they float, count to sixty.",
    ),
    "sarmale": (
        "2026-06-07",
        [
            "1 sour cabbage (varză murată)",
            "1kg mince, pork mostly",
            "1 cup rice",
            "2 onions",
            "smoked ribs",
            "tomato juice",
            "dill, thyme",
            "bay leaves",
        ],
        "Small. Smaller than you think. Your mother makes them like fists. Low oven, three hours, don't look at them.",
    ),
    "mamaliga-cu-branza": (
        "2026-06-07",
        [
            "300g coarse cornmeal",
            "1.2l water",
            "salt",
            "telemea or feta",
            "smântână (sour cream)",
            "a fried egg on top for Ana",
        ],
        "Rain the flour in with one hand, stir with the other, and don't answer the phone.",
    ),
    "zacusca": (
        "2026-06-21",
        [
            "3kg red peppers, roasted",
            "2kg aubergines, roasted",
            "1kg onions",
            "1kg tomatoes",
            "500ml sunflower oil",
            "bay, peppercorns, salt",
        ],
        "This is a whole day. Do it with someone, it's for gossip as much as for eating.",
    ),
    "salata-de-vinete": (
        "2026-06-21",
        [
            "3 aubergines, charred on the flame",
            "1 small onion, grated",
            "sunflower oil",
            "salt",
            "a wooden knife — not metal",
        ],
        "Not metal. I don't care what anyone says. It goes black.",
    ),
    "placinta-cu-mere": (
        "2026-07-19",
        [
            "500g flour",
            "250g lard (or butter)",
            "3 tbsp sour cream",
            "1.5kg sour apples",
            "sugar, cinnamon",
            "semolina for the bottom",
        ],
        "Grate the apples, squeeze them, keep the juice for the children to drink.",
    ),
    "papanasi": (
        "2026-08-02",
        [
            "500g fresh cow's cheese (urdă or ricotta)",
            "2 eggs",
            "3 tbsp semolina",
            "flour as needed",
            "lemon zest",
            "sour cherry jam",
            "smântână",
        ],
        "One big one with a little hat. Teo always ate the hat first.",
    ),
    "cornulete": (
        "2026-08-30",
        [
            "500g flour",
            "250g butter",
            "200ml sour cream",
            "1 sachet yeast",
            "Turkish delight or plum jam",
            "vanilla sugar",
        ],
        "Roll from the wide end. If they open in the oven it's because you were greedy with the filling.",
    ),
    "supa-de-gaina-cu-taitei": (
        "2026-09-27",
        [
            "1 boiling hen",
            "carrots, parsnip, celeriac",
            "1 onion with the skin on",
            "for noodles: 2 eggs and flour, nothing else",
            "parsley",
        ],
        "The onion skin is for the colour. The noodles cut thin as hair or don't bother.",
    ),
    "cozonac": (
        "2026-10-11",
        [
            "1kg flour",
            "6 egg yolks",
            "350ml warm milk",
            "200g butter",
            "250g sugar",
            "40g fresh yeast",
            "walnuts, cocoa, rum essence, raisins",
            "lemon and orange zest",
        ],
        "You knead until your arms complain and then another quarter hour. And you close the windows, it doesn't like a draught. Like your grandfather.",
    ),
}
idx = [
    "# Rețetele bunicii Floarea",
    "",
    "From the four tapes Mama sent (Feb 2022). Typed up as I go.",
    "Tape / time = where she starts talking about it.",
    "",
]
for slug, title, tape, ts in ALL_RECIPES:
    mark = "x" if slug in DONE else " "
    idx.append("- [%s] %s — tape %d, %s" % (mark, title, tape, ts))
put("recipes/bunica/index.md", "\n".join(idx))
for slug, title, tape, ts in ALL_RECIPES:
    if slug not in DONE:
        continue
    typed, ingr, quote = DONE[slug]
    body = [
        "# " + title,
        "",
        "_Tape %d, from %s. Typed up %s._" % (tape, ts, typed),
        "",
        "## Ingredients",
        "",
    ]
    body += ["- " + i for i in ingr]
    body += [
        "",
        "## How she does it",
        "",
        "She doesn't measure, so these are my guesses from the tape and from watching Mama.",
        "Prep everything first. Cook slowly. Taste as you go.",
        "",
        "## On the tape",
        "",
        "> " + quote,
    ]
    put("recipes/bunica/%s.md" % slug, "\n".join(body))
put(
    "recipes/bunica/tapes.txt",
    "Tape 1 — 11:48 (kitchen, Christmas 2019, radio in background)\nTape 2 — 22:30 (mostly Easter baking)\nTape 3 — 18:05 (desserts, interrupted by neighbour Doina)\nTape 4 — 24:10 (summer preserves; last 5 min is about Tataie)\nFiles: bunica_tape1.m4a ... bunica_tape4.m4a (on the old laptop + Mama's phone)\n",
)
put(
    "recipes/jollof_sams_dad.md",
    "# Jollof (Sam's dad's way)\n\n- blend: 4 plum tomatoes, 2 red peppers, 1 scotch bonnet, 1 onion\n- fry the paste down till it splits, 20 min min\n- long grain parboiled rice, stock, bay, thyme, curry powder\n- foil under the lid. Party jollof = let the bottom catch.\n",
)
put(
    "recipes/thai_green_curry.md",
    "# green curry (weeknight)\n\n- 2 tbsp paste, fry in coconut cream\n- chicken thighs\n- aubergine, green beans\n- fish sauce, palm sugar, lime leaves, basil\n",
)
put(
    "recipes/maisie_pancakes.md",
    "# Maisie pancakes\n\n1 cup flour, 1 cup milk, 1 egg, pinch salt. Maisie flips. Expect losses.\n",
)

# ---------------------------------------------------------------------------
# Chats (WhatsApp-style exports)
# ---------------------------------------------------------------------------


def stamp(d, t):
    return "[%s, %s]" % (uk(d), t)


def chat_file(path, header, entries):
    entries = sorted(entries, key=lambda e: (e[0], e[1]))
    lines = [header]
    for d, t, who, msg in entries:
        lines.append("%s %s: %s" % (stamp(d, t), who, msg))
    put(path, "\n".join(lines))


def rand_time(lo=7, hi=23):
    return "%02d:%02d:%02d" % (
        rng.randint(lo, hi - 1),
        rng.randint(0, 59),
        rng.randint(0, 59),
    )


SAM_FILLER = [
    ("Sam", "can you grab milk on the way back", "Teo", "yep"),
    ("Teo", "what do you want for tea", "Sam", "anything that isn't pasta again"),
    ("Sam", "marking till late, don't wait up", "Teo", "ok x"),
    ("Teo", "Maisie left her book bag here", "Sam", "I'll drop it at Jo's"),
    ("Sam", "bins tonight", "Teo", "done already"),
    ("Teo", "landlord coming thurs re the damp", "Sam", "finally"),
    ("Sam", "parents evening 6:10 if you want to come", "Teo", "I'll be there"),
    ("Teo", "running the canal loop, back by 7", "Sam", "show off"),
    ("Sam", "there's a parcel for you at no. 7", "Teo", "ta"),
    ("Teo", "can we do Saturday at your mum's instead of Sunday", "Sam", "I'll ask"),
    (
        "Sam",
        "Maisie wants to know if the cat can come to ours",
        "Teo",
        "there is no cat",
    ),
    ("Teo", "month end hell. eat without me", "Sam", "saved you a plate"),
    ("Sam", "date night friday? Jo has her", "Teo", "yes please"),
    ("Teo", "the washing machine is making The Noise again", "Sam", "I'll look at it"),
    ("Sam", "forgot my lanyard, any chance", "Teo", "on my way"),
    ("Teo", "heating on or off", "Sam", "on, it's freezing"),
    ("Sam", "Mum says thank you for the flowers", "Teo", "Hannah did me a deal"),
    ("Teo", "football tonight?", "Sam", "yeah back 9ish"),
    ("Sam", "did you pay the energy", "Teo", "it's a DD love"),
    ("Teo", "Maisie's swimming moved to 5", "Sam", "noted"),
]
sam = []
d = D("2021-10-03")
while d < TODAY:
    a, am, b, bm = rng.choice(SAM_FILLER)
    t = rand_time(7, 21)
    sam.append((d, t, "Sam Okafor" if a == "Sam" else "Teo", am))
    t2 = t[:3] + "%02d" % min(59, int(t[3:5]) + rng.randint(1, 9)) + t[5:]
    sam.append((d, t2, "Sam Okafor" if b == "Sam" else "Teo", bm))
    d += timedelta(days=rng.randint(3, 9))
SAM_PLANTED = [
    (
        "2024-05-14",
        "18:02:11",
        "Sam Okafor",
        "physio says it's just a strain, couple of sessions and I'm back",
    ),
    ("2024-06-11", "17:40:00", "Sam Okafor", "discharged!! knee is officially fine"),
    (
        "2024-12-03",
        "07:15:20",
        "Teo",
        "boiler's dead. like properly dead. engineer says condemned",
    ),
    ("2024-12-03", "07:16:02", "Sam Okafor", "oh no. how much"),
    ("2024-12-03", "07:20:45", "Teo", "3.6k. Dev says he can lend us some till Jan"),
    ("2025-02-11", "14:22:31", "Teo", "my wrist is doing the clicky thing again"),
    ("2025-03-04", "12:10:05", "Teo", "physio says tendons. exercises 3x a day lol"),
    ("2025-03-04", "12:11:30", "Sam Okafor", "do them!! I'll nag"),
    (
        "2025-04-01",
        "08:30:12",
        "Teo",
        "cancelled physio, Petal Row year end is on fire",
    ),
    ("2025-04-01", "08:31:00", "Sam Okafor", "rebook though"),
    (
        "2025-07-12",
        "23:05:18",
        "Sam Okafor",
        "that jungle curry at Lemongrass & Lime. I think about it constantly",
    ),
    (
        "2025-11-10",
        "13:44:09",
        "Teo",
        "MOT passed!! but advisories - both front tyres nearly bald and a brake pipe thing",
    ),
    ("2025-11-10", "13:46:51", "Sam Okafor", "I'll sort the tyres before winter x"),
    ("2026-01-30", "19:12:40", "Teo", "tyres?"),
    ("2026-01-30", "19:20:02", "Sam Okafor", "next payday promise. after the tax bill"),
    ("2026-04-18", "21:55:00", "Teo", "ANA'S ENGAGED"),
    ("2026-04-18", "21:55:40", "Sam Okafor", "!!!! to Radu?? finally"),
    ("2026-08-14", "16:01:15", "Teo", "ordered a wrist brace, don't laugh"),
    (
        "2026-10-19",
        "18:33:47",
        "Sam Okafor",
        "walked past bramble & bean on the way back - windows papered over and a sign saying closed for refurbishment?? did you know they were doing that",
    ),
    ("2026-10-19", "18:40:12", "Teo", "no"),
    ("2026-10-19", "18:40:30", "Teo", "no I did not"),
    (
        "2026-10-21",
        "07:02:00",
        "Sam Okafor",
        "you ok? you were shaking your hand out in your sleep again",
    ),
]
for ds, t, who, msg in SAM_PLANTED:
    sam.append((D(ds), t, who, msg))
chat_file(
    "chats/WhatsApp Chat with Sam.txt",
    "Messages and calls are end-to-end encrypted.",
    sam,
)

ana = [
    (
        D("2022-01-20"),
        "04:58:10",
        "Ana Marinescu",
        "Teo. Bunica. Sună-mă când te trezești",
    ),
    (D("2022-01-20"), "06:12:00", "Teo", "am văzut. I'm calling now"),
    (
        D("2022-02-14"),
        "18:20:00",
        "Ana Marinescu",
        "Mama put bunica's tapes on the drive. 4 of them. I can't listen yet",
    ),
    (D("2022-02-14"), "18:45:00", "Teo", "me neither. one day"),
    (
        D("2023-06-02"),
        "20:10:00",
        "Ana Marinescu",
        "Radu is cooking sarmale and they are TERRIBLE",
    ),
    (D("2023-06-02"), "20:11:00", "Teo", "too big?"),
    (D("2023-06-02"), "20:11:40", "Ana Marinescu", "like fists"),
    (
        D("2024-12-24"),
        "11:00:00",
        "Ana Marinescu",
        "Crăciun fericit to all three of you!! Maisie's drawing is on Mama's fridge",
    ),
    (
        D("2025-08-09"),
        "19:30:00",
        "Ana Marinescu",
        "îmi împrumuți 200 lei? no wait. 200 euro. for the dress for Ioana's wedding. I pay back in Sept I swear",
    ),
    (D("2025-08-10"), "08:05:00", "Teo", "trimis. £170, that's about 200"),
    (D("2025-09-02"), "12:00:00", "Ana Marinescu", "ți-am trimis înapoi, mersi!! ❤"),
    (D("2026-04-18"), "21:40:00", "Ana Marinescu", "TEO. RADU. INEL. 💍"),
    (D("2026-04-18"), "21:41:00", "Teo", "AAAAAA"),
    (D("2026-04-19"), "10:15:00", "Teo", "I wish bunica could have seen it"),
    (
        D("2026-04-19"),
        "10:16:30",
        "Ana Marinescu",
        "me too. she'd have made a hundred cornulețe",
    ),
    (
        D("2026-05-03"),
        "17:00:00",
        "Ana Marinescu",
        "you don't have to give us anything ok, just come",
    ),
    (
        D("2026-05-03"),
        "17:04:00",
        "Teo",
        "I'm doing something. you'll see. don't ask Mama, she doesn't know either",
    ),
    (
        D("2026-07-11"),
        "13:00:00",
        "Ana Marinescu",
        "Mama asked if you still have bunica's tapes 😂 she can't find the drive",
    ),
    (D("2026-07-11"), "13:05:00", "Teo", "I have them, tell her not to worry"),
    (
        D("2026-09-08"),
        "09:00:00",
        "Ana Marinescu",
        "DATE CONFIRMED: 12 iunie 2027, Cluj. church 2pm then restaurant at Radu's uncle's place",
    ),
    (
        D("2026-09-08"),
        "09:01:00",
        "Ana Marinescu",
        "you, Sam and Maisie. Maisie can do flowers if Jo says ok?",
    ),
    (D("2026-09-08"), "09:10:00", "Teo", "she will lose her mind. I'll ask Jo"),
    (
        D("2026-10-12"),
        "20:30:00",
        "Ana Marinescu",
        "are you flying in the week before? Mama wants everyone home by the 5th",
    ),
    (D("2026-10-12"), "20:42:00", "Teo", "probably 3rd or 4th. haven't booked"),
]
for ds in [
    "2022-05-08",
    "2022-11-30",
    "2023-03-08",
    "2023-10-01",
    "2024-04-15",
    "2024-08-20",
    "2025-01-06",
    "2025-12-01",
    "2026-03-08",
    "2026-06-15",
]:
    ana.append(
        (
            D(ds),
            rand_time(),
            "Ana Marinescu",
            rng.choice(
                [
                    "ce faci?",
                    "pozele de la Mama 📷",
                    "sună-o pe Mama, e supărată că nu suni",
                    "La mulți ani de 1 Decembrie lol",
                    "look at this dog",
                ]
            ),
        )
    )
    ana.append(
        (
            D(ds),
            rand_time(),
            "Teo",
            rng.choice(
                [
                    "bine, obosită",
                    "ce frumos",
                    "ok ok o sun diseară",
                    "😂",
                    "work work work",
                ]
            ),
        )
    )
chat_file(
    "chats/WhatsApp Chat with Ana.txt",
    "Messages and calls are end-to-end encrypted.",
    ana,
)

dev = [
    (
        D("2023-03-11"),
        "20:00:00",
        "Dev Raman",
        "pub quiz thursday? we need your brain for the money round",
    ),
    (D("2023-03-11"), "20:05:00", "Teo", "obviously"),
    (
        D("2024-07-02"),
        "18:00:00",
        "Dev Raman",
        "bbq at ours sat, bring Sam and the small one",
    ),
    (
        D("2024-12-03"),
        "08:02:00",
        "Teo",
        "Dev our boiler has died. condemned. 3.6k for a new one and we're short till the Jan clients pay",
    ),
    (
        D("2024-12-03"),
        "08:10:00",
        "Dev Raman",
        "I can do 600 tonight if that helps. pay back whenever, honestly no rush",
    ),
    (
        D("2024-12-03"),
        "08:11:00",
        "Teo",
        "you're a lifesaver. I'll pay it back in bits",
    ),
    (D("2024-12-04"), "19:00:00", "Dev Raman", "sent 👍"),
    (D("2025-02-28"), "17:30:00", "Teo", "first £100 back, thank you again x"),
    (D("2025-02-28"), "17:45:00", "Dev Raman", "didn't need to! cheers"),
    (D("2025-05-30"), "17:35:00", "Teo", "another 150 sent"),
    (D("2025-05-30"), "17:50:00", "Dev Raman", "👌"),
    (D("2025-09-14"), "21:00:00", "Dev Raman", "quiz is back on after summer, you in"),
    (D("2026-02-10"), "12:00:00", "Dev Raman", "PRIYA'S PREGNANT"),
    (D("2026-02-10"), "12:01:00", "Teo", "!!!!!!! congratulations both of you"),
    (
        D("2026-09-12"),
        "22:15:00",
        "Dev Raman",
        "30 weeks. just priced up a pram, buggy, cot, car seat... how do people afford children",
    ),
    (
        D("2026-09-12"),
        "22:16:00",
        "Dev Raman",
        "Priya's going down to statutory pay in Nov too. tight few months lol",
    ),
    (
        D("2026-09-12"),
        "22:30:00",
        "Teo",
        "ask me about Maisie's pram, it's in Jo's loft, might be free",
    ),
    (
        D("2026-10-17"),
        "10:00:00",
        "Dev Raman",
        "quiz thurs? last one before the baby probably",
    ),
]
chat_file(
    "chats/WhatsApp Chat with Dev.txt",
    "Messages and calls are end-to-end encrypted.",
    dev,
)

jo = [
    (
        D("2025-09-01"),
        "09:00:00",
        "Jo Whitlam",
        "new schedule from this week as agreed: Mon/Tue me, Wed/Thu you, alt weekends",
    ),
    (D("2025-09-01"), "09:05:00", "Teo", "perfect, Sam's put it in the calendar"),
    (
        D("2025-12-15"),
        "18:00:00",
        "Jo Whitlam",
        "have a lovely time in Romania, send pics of her in the snow",
    ),
    (
        D("2026-03-20"),
        "16:00:00",
        "Jo Whitlam",
        "Maisie's got nits. sorry. treating tonight, suggest you do too",
    ),
    (D("2026-03-20"), "16:02:00", "Teo", "the glamour"),
    (
        D("2026-09-09"),
        "20:00:00",
        "Teo",
        "Ana's asked if Maisie can be a flower girl, 12 June 2027 in Cluj. totally your call",
    ),
    (
        D("2026-09-09"),
        "20:40:00",
        "Jo Whitlam",
        "aw!! yes of course, we'll need to swap that week, sort nearer the time",
    ),
    (
        D("2026-10-15"),
        "19:30:00",
        "Jo Whitlam",
        "Christmas: she's with us Christmas Eve to Boxing Day, then you have her 27th to 2nd. handover at mine 10am on the 27th?",
    ),
    (
        D("2026-10-15"),
        "19:45:00",
        "Teo",
        "works for us. and half term she's with you Mon-Wed then us Thu-Sun",
    ),
    (D("2026-10-15"), "19:46:00", "Jo Whitlam", "yep 👍"),
]
for ds in [
    "2025-10-02",
    "2025-11-13",
    "2026-01-21",
    "2026-05-06",
    "2026-06-24",
    "2026-10-01",
]:
    jo.append(
        (
            D(ds),
            rand_time(15, 20),
            "Jo Whitlam",
            rng.choice(
                [
                    "she's got her reading book in her bag, page 40",
                    "PE kit is at yours I think?",
                    "running 10 min late sorry",
                    "can you do pickup thursday, I'm stuck at work",
                ]
            ),
        )
    )
    jo.append(
        (
            D(ds),
            rand_time(15, 20),
            "Teo",
            rng.choice(["got it", "no worries", "yep it's here", "sure"]),
        )
    )
chat_file(
    "chats/WhatsApp Chat with Jo.txt",
    "Messages and calls are end-to-end encrypted.",
    jo,
)

# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

events = []


def ev(start, summary, end=None, allday=True, time=None, status=None, desc=None):
    events.append((start, summary, end, allday, time, status, desc))


ev("2025-03-04", "Physio — wrist", allday=False, time="1000")
ev("2025-03-18", "Physio — wrist", allday=False, time="1000")
ev("2025-04-01", "Physio — wrist", allday=False, time="1000", status="CANCELLED")
ev("2024-05-14", "Sam physio (knee)", allday=False, time="1730")
ev("2024-11-08", "MOT Fabia — Kirkgate", allday=False, time="0830")
ev("2025-11-10", "MOT Fabia — Kirkgate", allday=False, time="0830")
ev("2026-10-26", "Half term", end="2026-10-31")
ev("2027-02-15", "Half term", end="2027-02-20")
ev("2026-12-27", "Maisie handover — Jo's, 10am", allday=False, time="1000")
ev("2027-06-12", "Ana & Radu wedding — Cluj")
ev("2026-10-22", "Pub quiz w Dev", allday=False, time="1930")
ev("2026-11-05", "Maisie museum trip (Y4)")
ev("2026-11-07", "Ruby's party, soft play", allday=False, time="1400")
ev("2023-12-15", "AAT exam DAIF", allday=False, time="0930")
ev("2024-02-09", "AAT exam APMA", allday=False, time="0930")
ev("2024-06-21", "AAT exam PLTX", allday=False, time="0930")
ev("2025-11-21", "Boiler service", allday=False, time="0800")
for y in range(2022, 2027):
    ev("%d-03-14" % y, "Grace's birthday")
    ev("%d-08-19" % y, "Ana's birthday")
    ev("%d-01-16" % y, "Maisie's birthday")
for y, m in months((2025, 1), (2026, 12)):
    ev(month_end(y, m).isoformat(), "Month end — invoices out")
cal = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Teo//Calendar//EN"]
for i, (start, summary, end, allday, time, status, desc) in enumerate(
    sorted(events, key=lambda e: (e[0], e[1]))
):
    s = start.replace("-", "")
    cal.append("BEGIN:VEVENT")
    cal.append("UID:teo-%04d@marinescu-books.example.com" % i)
    if allday:
        cal.append("DTSTART;VALUE=DATE:%s" % s)
        e = D(end) if end else D(start) + timedelta(days=1)
        cal.append("DTEND;VALUE=DATE:%s" % e.isoformat().replace("-", ""))
    else:
        cal.append("DTSTART;TZID=Europe/London:%sT%s00" % (s, time))
    cal.append("SUMMARY:%s" % summary)
    if status:
        cal.append("STATUS:%s" % status)
    cal.append("END:VEVENT")
cal.append("END:VCALENDAR")
put("calendar/teo.ics", "\n".join(cal))

# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

JOURNAL_PLANTED = {
    "2022-01-20": "Bunica died last night. Ana rang at five. I booked the flight before I'd even cried. Sam held the fort with Maisie.",
    "2022-02-14": "Mama sent the tapes. Four of them, forty-odd minutes of bunica in the kitchen, talking about food and the neighbours in the same breath. Couldn't listen past the first minute.",
    "2023-09-16": "Enrolled on AAT Level 4 with Northgate. £1,195. Sam says it's the right call and that I'm wasted doing shoeboxes. He's right and also rude.",
    "2023-12-15": "Passed DAIF! 74%. Celebrated with chips.",
    "2024-06-21": "Third one done. PLTX 78%. Two to go. Taking the summer off from it.",
    "2024-09-22": "Opened the BSTX book. Capital allowances. Closed the BSTX book.",
    "2024-12-03": "Boiler condemned. £3,680. Dev offered £600 before I'd even finished asking. Cold showers at the gym till Friday.",
    "2025-02-11": "Right wrist clicking every time I use the mouse. Tried left-handed for an afternoon. Useless.",
    "2025-03-04": "First physio. She pressed one spot and I nearly hit her. Tendons, she says. Exercises three times a day, breaks, and 'sort your desk out'. I am at the kitchen table on a dining chair.",
    "2025-03-30": "Did the exercises twice this week. Twice total.",
    "2025-04-01": "Cancelled physio. Petal Row year end. Will rebook.",
    "2025-06-03": "Okonjo leaving — Chidi selling up. Fair enough. That's £400 a month gone though.",
    "2025-11-10": "MOT pass with advisories. Tyres both nearly bald plus some brake pipe thing. Kirkgate quoted £148 for tyres. Sam said he'd sort it.",
    "2026-02-10": "Dev and Priya are having a baby!",
    "2026-03-19": "Kirsty asked if she could go quarterly. Said I'd think about it. Bramble & Bean was always the easy one.",
    "2026-04-18": "Ana's engaged. Radu, obviously, after nine years. Rang Mama, who cried. I want to do something proper for them.",
    "2026-05-10": "Listened to tape 1 all the way through tonight. Her voice. Typed up ciorbă de perișoare and the plum dumplings. Two.",
    "2026-06-21": "Zacuscă and salată de vinete. Six now. Tape 2 has the radio so loud I'm guessing at half of it.",
    "2026-08-02": "Papanași. Eight. Bunica on the tape: 'Teo always ate the hat first.' Had to stop for a bit.",
    "2026-08-14": "Wrist bad all week. Bought a brace. Typing in it like a robot.",
    "2026-08-31": "Sent Kirsty a chaser for June and July. Felt awkward. She's been a client for nearly five years.",
    "2026-09-30": "No box from Bramble & Bean this month. Didn't invoice. Not sure what's going on there.",
    "2026-10-06": "Chased Kirsty again by email. Nothing back. Sent her a WhatsApp too — two grey ticks.",
    "2026-10-11": "Cozonac tonight, typed and actually baked. Eleven. The kitchen smelled like her flat.",
    "2026-10-15": "Woke at three with pins and needles in my right hand. Shook it out. Again at five.",
}
FILLER = [
    "Rain all day.",
    "Maisie with us. She has decided she's vegetarian except for sausages.",
    "Ran the canal loop, 6k. Legs like wood.",
    "Sam marking till midnight.",
    "Month end for Hargreaves took forever — Lynn's folder was half the size it should be.",
    "Made sarmale, froze half.",
    "Called Mama. Forty minutes, mostly about the neighbour's roof.",
    "Petal Row reconciled first try. Small joys.",
    "Grace came for Sunday lunch, brought far too much food as usual.",
    "Couldn't sleep, reorganised the receipts drawer.",
    "Book club. Nobody had finished the book.",
    "Pub quiz with Dev. Came third. Lost on the music round, obviously.",
    "Maisie read me a whole chapter of her book. Proud.",
    "Kitchen tap dripping again.",
    "Mel's jar of receipts has become two jars.",
    "Sunny. Walked to Kirkstall Abbey with Sam.",
    "Washing machine making The Noise.",
    "Tired. Bed by ten.",
    "Watched something awful with Sam and loved it.",
    "Did the weekly shop, forgot the one thing I went for.",
    "Jo dropped Maisie early, we had pancakes.",
    "Spent the evening on VAT for Okonjo. Thrilling.",
    "Thought about getting a dog. Sam said no. Maisie said yes.",
    "Long call with Ana about nothing.",
    "Hannah sent me home with a bunch of tulips.",
]
taken = set(JOURNAL_PLANTED)
start_ord, end_ord = D("2021-11-01").toordinal(), D("2026-10-21").toordinal()
journal_dates = set()
while len(journal_dates) < 62:
    dd = date.fromordinal(rng.randint(start_ord, end_ord))
    if dd.isoformat() in taken:
        continue
    if dd.year < 2025 and dd.year != 2022 and len(journal_dates) % 3 == 0:
        continue
    journal_dates.add(dd)
for dd in sorted(journal_dates):
    pick = rng.sample(FILLER, rng.randint(1, 3))
    if dd < D("2022-04-01") or (D("2025-06-30") < dd):
        pick = [p for p in pick if "Okonjo" not in p] or ["Quiet day."]
    if dd < D("2024-03-01"):
        pick = [p for p in pick if "Mel" not in p] or ["Quiet day."]
    put(
        "notes/journal/%s.md" % dd.isoformat(),
        "# %s\n\n%s" % (dd.isoformat(), " ".join(pick)),
    )
for ds, txt in JOURNAL_PLANTED.items():
    put("notes/journal/%s.md" % ds, "# %s\n\n%s" % (ds, txt))

# ---------------------------------------------------------------------------
# Planted needs: evidence for --check
# ---------------------------------------------------------------------------

NEEDS = [
    (
        "N1",
        [
            ("work/invoices/invoice_ledger.csv", BB_JUN["no"]),
            (
                "work/invoices/%s_bramble.txt" % BB_AUG["no"],
                "Total due: £%s" % money(BB_AUG["amount"]),
            ),
            (
                "email/2026-09-12_re-%s-and-%s.eml"
                % (BB_JUN["no"].lower(), BB_JUL["no"].lower()),
                "Bear with me",
            ),
            ("email/2026-10-06_outstanding-invoices.eml", money(BB_UNPAID_TOTAL)),
            ("chats/WhatsApp Chat with Sam.txt", "closed for refurbishment"),
            ("bank/northbank_current_2026-06.csv", "BRAMBLE AND BEAN LTD"),
            ("notes/journal/2026-10-06.md", "two grey ticks"),
        ],
    ),
    (
        "N2",
        [
            ("work/insurance/PI_schedule_2025-26.txt", "03/11/2026"),
            (
                "email/2026-09-20_your-replacement-debit-card-is-on-its-way.eml",
                "card ending 1111 expires",
            ),
            (
                "email/2026-10-06_pi-renewal-2026.eml",
                "card ending 1111",
            ),
            (
                "work/clients/hargreaves/engagement_letter_2021.txt",
                "professional\nindemnity insurance",
            ),
        ],
    ),
    (
        "N3",
        [
            ("car/MOT_2025-11-10.txt", "Expiry: 09/11/2026"),
            ("car/MOT_2025-11-10.txt", "Rear brake pipe corroded"),
            ("car/kirkgate_invoice_K-9930.txt", "Customer declined today"),
            ("chats/WhatsApp Chat with Sam.txt", "next payday promise"),
        ],
    ),
    (
        "N4",
        [
            ("notes/journal/2025-02-11.md", "Right wrist clicking"),
            ("receipts/kinetic_physio_2025-03-18.txt", "Next appt: 01/04/2025"),
            ("calendar/teo.ics", "STATUS:CANCELLED"),
            ("notes/journal/2026-10-15.md", "pins and needles"),
            ("receipts/order_2026-08-14_wrist_brace.txt", "Wrist support brace"),
            ("notes/chair_options.md", "Didn't buy any"),
        ],
    ),
    (
        "N5",
        [
            (
                "email/2023-09-15_aat-welcome.eml",
                "until 31 March 2027",
            ),
            ("study/aat/progress.md", "| INAC | | | |"),
            (
                "email/2026-10-20_your-learning-account-important-dates.eml",
                "closes on 31 March 2027",
            ),
            ("notes/journal/2024-09-22.md", "Closed the BSTX book"),
        ],
    ),
    (
        "N6",
        [
            ("recipes/bunica/index.md", "- [ ] Drob de miel"),
            ("recipes/bunica/cozonac.md", "Typed up 2026-10-11"),
            ("chats/WhatsApp Chat with Ana.txt", "12 iunie 2027"),
            (
                "email/2026-06-02_re-small-run-of-hardback-books-quote.eml",
                "mid-April 2027",
            ),
            ("notes/journal/2026-10-11.md", "Eleven"),
        ],
    ),
    (
        "N7",
        [
            ("bank/northbank_current_2024-12.csv", "D RAMAN,BOILER,600.00"),
            ("bank/northbank_current_2025-02.csv", "D RAMAN,BOILER 1,-100.00"),
            ("bank/northbank_current_2025-05.csv", "D RAMAN,BOILER PT2,-150.00"),
            ("chats/WhatsApp Chat with Dev.txt", "statutory pay"),
        ],
    ),
]


def write_all():
    if LIFE.exists():
        shutil.rmtree(LIFE)
    for rel in sorted(FILES):
        p = LIFE / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(FILES[rel])


def check():
    bad = 0
    for need, evidence in NEEDS:
        missing = []
        for rel, needle in evidence:
            p = LIFE / rel
            if not p.is_file():
                missing.append("%s (no file)" % rel)
                continue
            if needle not in p.read_text(encoding="utf-8"):
                missing.append("%s (lacks %r)" % (rel, needle))
        if missing:
            bad += 1
            print("%s MISSING: %s" % (need, "; ".join(missing)))
        else:
            print(
                "%s ok (%d pieces of evidence in %d files)"
                % (need, len(evidence), len({r for r, _ in evidence}))
            )
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true", help="verify planted evidence on disk"
    )
    args = ap.parse_args()
    if args.check:
        sys.exit(check())
    write_all()
    total = sum(len(v.encode("utf-8")) for v in FILES.values())
    print("wrote %d files, %d bytes, to %s" % (len(FILES), total, LIFE))


if __name__ == "__main__":
    main()
