# Teodora, blind: the strip-down

2026-10-22. Built by an agent that read nothing outside its own folder, under
limits meant to keep it away from the first two (under 40, not living alone,
no music, not centred on a parent). It wrote her files, the answer key and
twenty questions. The testers read only `QUESTIONS.md`, ran all twenty through
the readings adapter on a copy of `life/` alone (no words list), and opened
`PLANTED.md` after.

## The needs

| Need | Her question | Served | Verdict |
|---|---|---|---|
| N1 a client owes £2,140 | Q2 "has kirsty paid me yet", Q20 "which clients are behind on paying" | nothing, twice (exit 42: no "behind", no "paying"); Q11 (about Hargreaves) served ledger rows with Bramble & Bean's three `sent` invoices | **an accident** |
| N2 her PI cover lapses 3 Nov | Q18 "does my work insurance need anything from me" | the replacement-card email: "regular payments … insurance … will need to be updated" | **evidence served**; the renewal email and the schedule didn't come up |
| N3 MOT 9 Nov, advisories unfixed | Q1 "when's the car due its mot" | `MOT_2025-11-10.txt`: "Expiry: 09/11/2026", both front tyres near the limit | **found, asked** |
| N4 her wrist, treatment dropped | Q8 "those wrist stretches the physio gave me" | the physio receipt ("Next appt: 01/04/2025") and the calendar entry | **evidence served**; the exercises and the brace didn't come up |
| N5 AAT access ends 31 Mar 2027 | Q14 "which aat exams have I passed" | `study/aat/progress.md` (three passed, INAC blank) | **evidence served**, not the deadline |
| N6 the recipe book, 11 of 30 | Q17 "what was I going to do for ana's wedding and how far along am I" | nothing (exit 42: no "along") | **missed** |
| N7 she owes Dev £350 | Q9 "how much do I still owe dev" | nothing (exit 42: no "owe") | **missed** |

Traps: T1 (the phishing refund) was what Q12 asked about, and was served
without a word on whether it's real. T2 sprang: "did hargreaves pay august"
served ledger rows showing his August invoice `sent`, not the bank line and
remittance saying it was paid. T3, T4, T5 never came up.

**One of seven found as asked; three with evidence served; one by accident; two
missed.**

## What the readings did

- **What isn't there: noise again.** Her dates are written `01/09/2025`; the
  kernel reads only ISO and compact dates, so to this reading they are ids, and
  six answers carried a line of "missing" dates.
- **Numbers over time and a date gone by: never spoke**, as with Hollis.

## What the kernel missed, and the readings can't fix

- **Her dates.** Every chat line and most of her letters are `dd/mm/yyyy`.
  Newest first can't date them, so "what did we end up paying for the new
  boiler" served a 2023 pub-quiz chat with Dev.
- **Inflections.** "Owe" against owed and owes; "paying" against paid; "gave",
  "behind", "along", "forget". Six of twenty questions came back empty on one
  missing form or one everyday word. Hollis lost the same way on "owes".

## After word forms and dd/mm/yyyy (kernel d942564)

The same twenty questions on a fresh copy. Five empty answers, against six.

- **N4, her wrist, is now better served.** "Those wrist stretches the physio
  gave me" serves `notes/physio_exercises.md` first (stretches matches
  stretch).
- **"Owe" was not a word-form gap.** No file of hers uses owe, owes, owed or
  owing: Dev "lent", she'll "pay back whenever". "How much do I still owe dev"
  is still empty. Only a words list, or a meaning match, closes that.
- **"Which clients are behind on paying"** is no longer empty, but it serves
  the wrong chats.
- Her chat lines like `[11/03/2023, …]` stay undated, since 11 and 03 could be
  either way round; lines like `[15/10/2026, …]` now date.

Needs: still one found as asked, now four with evidence served, two missed.
