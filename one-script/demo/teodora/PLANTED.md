# Answer key — planted needs and traps

Today in Teo's world is **2026-10-22**. All paths are relative to `life/`.
`python3 make_life.py --check` verifies the evidence for N1–N7 is on disk.

## Needs

### N1 — A client owes her £2,140 and may be going under (urgent, practical)
Bramble & Bean has not paid June, July or August 2026 (INV-0427 £680,
INV-0431 £720, INV-0435 £740 = £2,140). Payments had been arriving later and
later all year; the owner promised to pay "end of the month" and went silent;
the café now has papered-over windows. She needs to escalate (call, letter
before action, small claim) before there is nothing left to claim from.
- `work/invoices/invoice_ledger.csv` — three B&B rows `sent`, no `paid_on`; 2026 Jan–May `paid_on` lags drifting from 28 to 44 days
- `work/invoices/INV-0427_bramble.txt`, `INV-0431_bramble.txt`, `INV-0435_bramble.txt`
- `bank/northbank_current_2026-06.csv` … `2026-10.csv` — last `BRAMBLE AND BEAN LTD` credit is 2026-07-14 (for May); none after
- `email/2026-07-29_inv-0427-june-books.eml`, `email/2026-08-31_inv-0427-and-inv-0431.eml`, `email/2026-09-12_re-inv-0427-and-inv-0431.eml` ("Bear with me"), `email/2026-10-06_outstanding-invoices.eml` (total £2,140, no reply)
- `chats/WhatsApp Chat with Sam.txt` 2026-10-19 — "windows papered over … closed for refurbishment"
- `notes/journal/2026-03-19.md` (asked to go quarterly), `2026-08-31.md`, `2026-09-30.md` (no box, didn't invoice), `2026-10-06.md` ("two grey ticks")
- `work/clients/bramble/notes.md` — rent review +18%, quarterly-invoicing request
- `work/clients/bramble/engagement_letter_2021.txt` — late-payment interest clause

### N2 — Her professional indemnity cover will lapse on 3 Nov (urgent, practical)
The PI policy renews on continuous payment authority against the debit card
ending 1111, which expired at the end of September; the replacement card ends
5556. The insurer will try the old card on/after 27 Oct and cover ends
03/11/2026 if collection fails. Every client engagement letter makes PI cover
a condition of the engagement. She needs to update the card with the insurer
this week.
- `work/insurance/PI_schedule_2025-26.txt` — period to 03/11/2026, card ending 1111 (exp 09/26), auto-renew
- `email/2026-09-20_your-replacement-debit-card-is-on-its-way.eml` — card 1111 expiring, new card 5556, "update regular payments"
- `email/2026-10-06_pi-renewal-2026.eml` — collection from card 1111 on/after 27/10, cover ends 03/11 if unpaid
- `work/clients/*/engagement_letter_*.txt` — clause 7 (PI required; lapse = grounds to suspend)
- Contrast: `work/insurance/PI_schedule_2023-24.txt` shows a previous card change went fine

### N3 — The car's MOT runs out 9 Nov and last year's advisories were never fixed (urgent-ish, practical)
MOT expires 09/11/2026; nothing is booked (calendar has 2024 and 2025 MOTs,
none for 2026). At the last test both front tyres were near the legal limit and
a rear brake pipe was corroded; the tyre quote was declined and Sam's "next
payday" promise never shows up as a garage bill (only an oil change in March).
It will likely fail without work, and they share the one car.
- `car/MOT_2025-11-10.txt` — expiry 09/11/2026, three advisories
- `car/kirkgate_invoice_K-9930.txt` — tyre quote £148, "Customer declined today"
- `car/kirkgate_invoice_L-0211.txt` — March 2026 service, no tyres or brakes
- `chats/WhatsApp Chat with Sam.txt` 2025-11-10 and 2026-01-30 — "I'll sort the tyres before winter", "next payday promise"
- `notes/journal/2025-11-10.md`
- `calendar/teo.ics` — absence of a 2026 MOT entry

### N4 — Her wrist is getting worse and she abandoned treatment (slow, health)
Right-wrist tendon trouble since Feb 2025. Two physio sessions, third
cancelled for work and never rebooked; exercises dropped; desk never fixed;
now wearing a brace and waking with pins and needles (a change in symptoms).
For someone who types for a living this is a slow-moving threat to her income.
- `notes/journal/2025-02-11.md`, `2025-03-04.md`, `2025-03-30.md`, `2025-04-01.md`, `2026-08-14.md`, `2026-10-15.md`
- `receipts/kinetic_physio_2025-03-04.txt`, `kinetic_physio_2025-03-18.txt` ("Next appt: 01/04/2025")
- `calendar/teo.ics` — 2025-04-01 physio `STATUS:CANCELLED`, nothing after
- `notes/physio_exercises.md`, `notes/chair_options.md` ("Didn't buy any")
- `receipts/order_2026-08-14_wrist_brace.txt`; `work/tax/expenses_2026-27.csv` (brace, category "?")
- `chats/WhatsApp Chat with Sam.txt` 2025-02-11, 2025-03-04, 2025-04-01 ("rebook though"), 2026-08-14, 2026-10-21

### N5 — Her AAT course access ends 31 March 2027 with two units left (slow, career)
Paid £1,195 in 2023; passed three units by June 2024; opened Business Tax in
Sept 2024 and stopped. Access to materials and exam booking ends 31/03/2027,
no extensions, and exam slots need ~6 weeks' notice — so realistically she
must restart now to sit INAC and BSTX by February.
- `email/2023-09-15_aat-welcome.eml` — "runs until 31 March 2027. Extensions are not available"
- `email/2023-12-15_…`, `2024-02-09_…`, `2024-06-21_…` (passes), `2025-04-02_…`, `2026-08-11_…`, `email/2026-10-20_your-learning-account-important-dates.eml`
- `study/aat/progress.md` (INAC row blank), `study/aat/BSTX_notes.md` (stops mid-list)
- `notes/journal/2023-09-16.md`, `2023-12-15.md`, `2024-06-21.md`, `2024-09-22.md`
- `bank/northbank_current_2023-09.csv` — NORTHGATE ACC COLLEGE −1195.00

### N6 — The grandmother's recipe book for Ana's wedding is 11 of 30 done (slow, personal)
A secret wedding present: Teo is typing up bunica's taped recipes into a
printed hardback for Ana and Radu's wedding on 12 June 2027. 11 of 30 are done
at roughly two a month; the printer needs final artwork by mid-April 2027, so
19 recipes plus photos and layout in under six months — at the current pace
she won't make it.
- `recipes/bunica/index.md` — 30 entries, 11 ticked
- `recipes/bunica/*.md` — the 11 typed-up recipes with "Typed up" dates May–Oct 2026; `recipes/bunica/tapes.txt`
- `notes/journal/2022-02-14.md`, `2026-04-18.md` ("something proper for them"), `2026-05-10.md`, `2026-06-21.md`, `2026-08-02.md`, `2026-10-11.md` ("Eleven")
- `chats/WhatsApp Chat with Ana.txt` — 2026-05-03 "I'm doing something. you'll see", 2026-09-08 date confirmed 12 iunie 2027
- `email/2026-05-29_small-run-of-hardback-books-quote.eml`, `email/2026-06-02_re-small-run-of-hardback-books-quote.eml` — artwork by mid-April 2027
- `receipts/inkwell_samples_2026-05-30.txt`, `bank/northbank_current_2026-05.csv` (INKWELL PRINT)
- `notes/gift_ideas.md` — "Ana & Radu — (sorted. sort of.)"

### N7 — She still owes Dev £350, and he's about to need it (slow, social)
Dev lent £600 on 2024-12-04 for the boiler; she repaid £100 (Feb 2025) and
£150 (May 2025), then nothing for 17 months. Dev never asks, but he's now
pricing prams, and Priya drops to statutory pay in November.
- `bank/northbank_current_2024-12.csv` (D RAMAN +600.00), `2025-02.csv` (−100.00), `2025-05.csv` (−150.00); nothing to D RAMAN since
- `chats/WhatsApp Chat with Dev.txt` — "pay back whenever", "first £100 back", "another 150 sent", 2026-09-12 pram prices, "statutory pay in Nov"
- `notes/journal/2024-12-03.md`; `home/boiler_warranty.txt`; `receipts/warmline_2024-12-06.txt`
- `chats/WhatsApp Chat with Sam.txt` 2024-12-03

## Traps

### T1 — "URGENT" HMRC refund email (phishing, not a need)
`email/2026-10-14_urgent-you-are-eligible-for-a-tax-refund-of-412.eml` — sender
domain `hmrc-taxrefund.example.net`, link to a bare IP, 48-hour pressure.
Looks like money owed to her; it is a scam. Her real tax position is in
`work/tax/`.

### T2 — Hargreaves' August invoice looks unpaid but was paid
`work/invoices/invoice_ledger.csv` shows INV-0434 (Hargreaves, August 2026,
£520) as `sent` with no `paid_on`, beside the genuinely unpaid B&B rows. It was
paid on 2026-09-02: `bank/northbank_current_2026-09.csv` (`HPS LTD BACS`,
ref `HPS-AUG26`, 520.00) and `email/2026-09-02_remittance-advice.eml`.
Hargreaves never quote her invoice number (`work/clients/hargreaves/notes.md`);
she just didn't update the ledger. (INV-0438, September, is likewise paid
2026-10-12 in the October CSV but still `sent`; INV-0440 Fernside September
is genuinely open but not yet due.)

### T3 — Physio receipts and appointments that are about Sam's knee
`receipts/kinetic_physio_2024-05-14_sam.txt`, `…05-28_sam.txt`,
`…06-11_sam.txt` ("Discharged"), the same merchant in
`bank/northbank_current_2024-05.csv`/`2024-06.csv`, `calendar/teo.ics`
2024-05-14, and Sam's chat 2024-05-14 / 2024-06-11. A resolved injury that
belongs to someone else, paid on Teo's card.

### T4 — Money lent to Ana (repaid)
`chats/WhatsApp Chat with Ana.txt` 2025-08-09/10 and
`bank/northbank_current_2025-08.csv` (−170.00 ANA MARINESCU "ROCHIE") look like
an outstanding family loan alongside N7. Repaid in full:
`bank/northbank_current_2025-09.csv` (+170.00 "INAPOI MERSI") and chat 2025-09-02.

### T5 — A course access deadline that doesn't matter
`email/2026-10-18_your-free-trial-ends-in-3-days.eml` — a free Excel course
trial ending 21 October. Superficially the same shape as N5 (course, access,
deadline); it's marketing she never engaged with.

### Also deliberately present (minor decoys)
- `email/2026-10-06_pi-renewal-2026.eml` vs `email/2025-10-06_pi-renewal-2025.eml`: the 2025 one is the same insurer and card but was fine at the time.
- `email/2026-03-02_your-contents-policy-has-renewed.eml`, `car/car_insurance_2026.txt`: other insurance renewals, both on Direct Debit, both fine.
- `car/MOT_2024-11-08.txt`: earlier advisories that *were* fixed (invoice K-8812).
- `home/boiler_warranty.txt`: boiler service due by 06/12/2026 is real but routine.
