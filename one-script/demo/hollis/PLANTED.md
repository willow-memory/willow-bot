# PLANTED: answer key (testers: do not read until after scoring)

All paths are relative to `life/`. `python3 make_life.py --check` verifies the core evidence for each need.

## Needs

### N1: His hearing is going, and it threatens his living (urgent, personal and practical)
None of his files say so. The evidence:
- `work/tuning_log_2021.csv` … `tuning_log_2026.csv`: callbacks (the `callback=Y` flag, with `return visit` rows
  noting sour or beating treble) run at about 1–2% a year through 2024, rise in the second half of 2025, and reach
  about 21% in 2026. Minutes per job rise in 2026. Notes such as "ETD above C6" and "strobe on treble" start
  appearing in mid-2025.
- `notes/2021-03-12_civic_hall_by_ear.txt` (proud aural tuning) contrasts with `notes/2026-03-11_strobe.txt`
  ("rooms with carpet are hard to hear in") and `notes/2025-10-30_top_octave.txt` ("twice as long up top").
- `mail/inbox/2026-02-10_your_referral_from_dr_lindqvist.eml`: GP referral for audiometric evaluation, valid
  through 2026-11-10.
- `calendar/hollis_calendar.ics`: two Larkfield Hearing Center evaluations, 2026-04-14 and 2026-07-09, both
  `STATUS:CANCELLED`. The matching emails are `mail/inbox/2026-04-13_appointment_cancelled.eml`,
  `2026-07-08_appointment_cancelled.eml` and `2026-07-20_we_d_like_to_reschedule_you.eml`, which says the
  referral expires 10 November.
- `messages/sms_nell_2026.txt` (2026-03-07): "you said 'what' like six times on the phone".
- `receipts/2026-01-18_larkfield_drug.txt` and `2026-02-20_larkfield_drug.txt`: ear-wax drops twice, as if he
  is trying the easy explanation.
- `notes/todo_2026-07-24.txt`: "call back re appt".

### N2: The third-quarter estimated tax payment was never made (urgent, practical)
- `money/bank/checking_2022-09.csv` … `checking_2025-09.csv` each have a `TREAS EST TAX PMT 1040-ES` line on
  Sep 15. `checking_2026-09.csv` has none. The 2026 Apr and Jun payments are present.
- `money/taxes/estimates_2026.txt`: Q3 has no paid line. Earlier years' ledgers are complete.
- `mail/inbox/2026-09-02_september_estimate.eml`: Dana's routine reminder, identical in form to every year's.
- `notes/todo_2024-09-09.txt` and `todo_2025-09-08.txt` list the Q3 estimate. `notes/todo_2026-09-10.txt` doesn't.

### N3: His father can no longer safely live alone or drive, and Hollis hasn't faced it (slow-urgent, personal)
- `van/van_log.csv`: "Brookhollow (Dad)" days go from 12 a year (2021–23) to 55 in Jan–Oct 2026, now every Sunday
  plus Wednesdays.
- `messages/sms_dad_2026.txt`: the plumber question asked three times in one morning (2026-02-03) and again in
  October, "Who is this" (2026-08-20), the missing checkbook, "they changed the road".
- `mail/inbox/2026-08-19_your_dad.eml` (Ruth): the car clipped the mailbox post twice, and he sat lost in a
  parking lot. `mail/inbox/2026-10-02_re_your_dad.eml`.
- `notes/2026-06-21_dads.txt` (expired milk, stove left on, bills behind, pills untaken) and
  `notes/2026-08-20_dad.txt` (fresh bumper scrape).
- `receipts/*_brookhollow_pharmacy.txt`: from 2026-03 the prescription includes donepezil, picked up by Hollis.
  `receipts/2026-06-22_brookhollow_municipal_electric.txt` and the bank line on 2026-06-22: Hollis paid his
  father's overdue electric bill.
- `mail/sent/2026-09-08_imre_varga_appointment_9_24.eml` and the reply (`mail/inbox/2026-09-09_…`): a consent form
  is needed before the doctor can talk to Hollis. Also `calendar` 2026-09-24 and `notes/todo_2026-09-10.txt`.

### N4: The van's timing belt is due now (urgent, practical; his whole business rides on the van)
- `van/service_history.txt`: the 2022-04-11 belt and water-pump job says the next one is due at 158,000 mi. The
  2026-08-03 entry says "Advised: timing belt interval coming up (158k)".
- `van/van_log.csv`: the last odometer reading (2026-10-21) is 157,641, and he drives roughly 1,000 miles a month.
- `notes/2026-10-14_van_ticking.txt`: a ticking at idle, "busy through Nov".

### N5: Repairing things with Nell after he missed her graduation (slow, personal)
- `calendar/hollis_calendar.ics`: on 2026-05-16, "NELL GRADUATION 10am" and "Civic Hall - Rennick recital"
  overlap.
- `mail/inbox/2026-02-08_graduation_details.eml` (he knew the date in February) and
  `mail/inbox/2026-03-18_rennick_recital_sat_may_16.eml` with `mail/sent/2026-03-18_re_rennick_…` (he said yes
  anyway).
- `mail/inbox/2026-05-16_today.eml` (Corinne: "She waited at the doors until they closed them"),
  `2026-04-28_may_16.eml` and `2026-06-03_re_no_subject.eml` ("I'm not ready to talk about it yet").
- `messages/sms_nell_2026.txt`: about 10 messages a month from Nell before May and about 1 a month after. They
  become one-word replies. Messages on 10-04 and 10-15 get no answer. Thanksgiving is offered grudgingly
  (09-20).
- `notes/2026-06-02_draft_nell.txt`: an unfinished apology.
- Contrast: `notes/2022-05-15_nell_graduation.txt` and `mail/inbox/2022-04-02_graduation.eml`.

### N6: His mother's piano is being ruined in the garage (slow, personal)
- `weiler/restoration_log.txt`: steady work from 2021-04 to 2023-02-11, then nothing.
- `weiler/garage_hygrometer.csv`: summer RH peaks at about 65–67% in 2021–24, then reaches the low 80s in summer
  2025 and again from mid-2026.
- `notes/2025-09-21_garage_roof.txt` (a patch that didn't hold) and `notes/2026-07-23_garage.txt` (leak again, a
  tarp over the Weiler, "need a roofer").
- Parts already sunk into it: `receipts/2021-06-02_…`, `2022-03-08_…` and `2022-09-14_meridian_piano_supply.txt`,
  and the matching order emails.
- Signs that it matters: `notes/2024-11-29_thanksgiving.txt`, `notes/2025-02-14_slow_day.txt`,
  `notes/2023-01-08_resolutions.txt`, and his last text to Nell (2026-10-15).

### N7: The community theatre owes him $640 and the invoice is drifting (medium, practical)
- `work/invoices/INV-2026-011.txt`: $640.00, dated 2026-05-30.
- No `REF INV-2026-011` deposit in any `money/bank/*.csv`, while every theatre invoice from 2022 onward was paid.
- `mail/sent/2026-07-01_…`, `2026-08-05_…` and `2026-09-15_invoice_inv_2026_011_108_days.eml`. Gil's reply
  (`mail/inbox/2026-08-06_…`) says he stepped down. The last chase got only a box-office auto-reply
  (`mail/inbox/2026-09-15_auto_reply_box_office.eml`).
- Side effect: no theatre visit was booked for October 2026 (`work/tuning_log_2026.csv`). Also
  `notes/todo_2026-07-24.txt` and `todo_2026-09-10.txt`.

## Traps (look like needs, aren't)

- **T1: "URGENT … past due" email.** `mail/inbox/2026-10-03_urgent_your_account_is_past_due.eml` is phishing
  (unknown sender, IP-address link), not a real bill.
- **T2: The house dispute with Corinne.** `mail/inbox/2021-04-10_the_house.eml`, `2021-06-30_re_the_house.eml`,
  `2021-09-14_we_need_to_finish_this.eml` and `mail/sent/2021-04-11_re_the_house.eml` read as unresolved. It ended:
  `mail/inbox/2022-02-25_recorded.eml`, `money/house/2022-02-24_recording_confirmation.txt` ("This file is closed")
  and `notes/2022-02-26_house_done.txt`.
- **T3: Aldous Pell's deafness.** `work/client_notes.txt` ("Deaf in his left ear…") and his "bright, as he likes
  it" rows in the tuning logs describe a client, not Hollis.
- **T4: The 2024 tax underpayment notice.** `money/taxes/2024-05-20_notice_balance_due.txt` and
  `mail/sent/2024-05-22_fwd_letter_about_2023.eml` look like trouble, but it was paid (bank 2024-06-03) and closed
  (`money/taxes/2024-07-15_notice_paid_zero.txt`, balance $0.00).
- **T5: The van's check-engine light.** `notes/2023-08-02_van_light.txt` was fixed by an O2 sensor
  (`receipts/2023-08-09_dunmore_auto_diesel.txt` and the 2023-08-09 line in `van/service_history.txt`) and
  confirmed in `notes/2023-08-20_van_ok.txt`. It is not the belt.
