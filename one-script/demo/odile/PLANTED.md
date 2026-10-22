# What the author planted: the answer key

**Never boxed.** The strip-down runs blind; this file is how we check it
afterwards. A strip that finds none of these has failed, and one that finds
something not on this list has found something to look at.

Each need is planted in the data as evidence, never stated outright.

| # | Need | Where the evidence will sit | Kind |
|---|---|---|---|
| N1 | Who is due for a tuning | tuning log's "next" lines plus the calendar's gaps | the one she'd ask for |
| N2 | Laszlo's weight is climbing: 71.2 kg on Oct 8 to 75.1 kg on Oct 21, 2.2 kg in the worst week (Oct 13 to 20), a heart-failure warning sign nobody has joined up | `dad/care-log.csv`, with puffy ankles and a night in the recliner in its notes; `dad/cardiology-2026-07-30.txt` says call if more than 2 kg in a week | urgent, unasked |
| N3 | The county notice of 2026-03-20 gave 180 days to respond; that ran out on 2026-09-16, and no reply exists | the scanned letter's date and its deadline sentence | urgent, unasked |
| N4 | The school districts haven't paid two purchase orders: Calloway Unified's of February 2026 and Dry Creek's of August 2025 | POs in mail, no matching line in the card export or Teo's sheet | money |
| N5 | Hive 4 is queenless | `hives/2026.txt`: its last three inspections, no eggs, no brood | her craft |
| N6 | She's paying for an insurance rider on Teo's van she sold in 2021 | insurance letter plus a voice memo about selling the van | money, small |
| N7 | She is worn out | voice memos at night, getting shorter; texts to Kati that start "fine" | the one nobody asks |

## Traps, so the strip can't get it right by accident

- **T1:** a client named Weight, so "weight" isn't a clean signal for N2.
- **T2:** an old county letter (2024), already answered, next to the new one.
- **T5:** a February 2026 weight blip that went away by itself, so a rise alone isn't the signal.
- **T3:** Dry Creek's February 2023 purchase order was paid in cash; only a voice memo says so, so the books show three unpaid and one of them isn't.
- **T4:** "queen" in a client's piano note: a Queen Anne cabinet.

## PII she carries, by rung

Laszlo's health data (L4, medical record), the card reader's export (L4,
card), Kati's phone and address (L3), clients' names (`--names`), and a bank
account in Teo's sheet (L4). All shapes are from reserved or test ranges.

## What the scrub already misses (found while building her)

At ceiling 0 with her names list, every phone, email, card, bank number, DOB,
MRN and the reader's IP is withheld, and no full name survives. Two things do:

- **Street addresses.** Kati's "4410 Larkspur Ct, Eugene OR 97401" goes through
  whole; the scrub has no address kind.
- **First names alone.** "Kati:" in her texts and "Harriet" in the clients'
  thread; `names_rule` matches the names it's given, and it's given full names.
