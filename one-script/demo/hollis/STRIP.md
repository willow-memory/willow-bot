# Hollis, blind: the strip-down

2026-10-22. Hollis was built by an agent that never saw the kernel, the readings
or Odile (it read nothing outside `demo/` but one `ls`). It wrote his files, his
answer key, and twenty questions in his voice. The testers read only
`QUESTIONS.md`, ran all twenty through the readings adapter on a copy of
`life/` alone (no words list: he has none), and opened `PLANTED.md` after.

One coincidence weakens the independence: the agent also made him a piano
tuner with an ageing father. It never opened Odile's folder; the overlap is
chance, but the two folders are more alike than a blind test should make them.

## The needs

| Need | His question | Served | Verdict |
|---|---|---|---|
| N1 his hearing is going | Q12 "ear doctor phone number" | nothing (exit 42: "no file has number") | **missed**; no question names it, and the referral and cancelled appointments never came up |
| N2 Q3 tax estimate not paid | Q6 "did I pay all my taxes this year" | a recital email, then the 2024 notice and its $0.00 follow-up | **missed**; the missing payment is a bank line that isn't there, with no id to match |
| N3 Dad can't live alone | Q4 "what pills is dad on now", Q19 "is it bad that dad's still driving" | Q4: texts with Dad and `notes/2026-06-21_dads.txt` (expired milk, stove on, pills untaken); Q19: old texts | **evidence served** (Q4), not named; Ruth's emails never came up |
| N4 the van's timing belt | Q5 "is the van due for anything big" | `van/service_history.txt`, the 2022 timing-belt entry | **evidence served**; the odometer (157,641 against 158,000) is in `van_log.csv`, never served |
| N5 Nell, after the graduation | Q17 "what's happening for thanksgiving" | Nell's grudging "you can come if you want I guess" | **an accident**, at best |
| N6 Mom's piano in the garage | Q10 "where did I order the bass strings" | the 2022 order | **missed**; the hygrometer and the leak notes never came up |
| N7 the theatre owes $640 | Q7 "who still owes me money" | nothing (exit 42: "no file has owes") | **missed** |

Traps: T2 (the house) came up with its own ending (`house_done.txt` served
beside the 2021 emails), and T4 (the 2024 notice) with its $0.00 follow-up,
so neither sprang. T1 and T5 never came up. T3 (the client who's deaf) was
what Q8 asked for.

**None of seven found. Two with evidence served, one by accident.** Odile's
third pass found six of seven: the readings were fitted to her folder.

## What the readings did

All three were run on every question.

- **What isn't there: 88 of 98 hint lines, all noise.** Each of his invoices
  names one client's piano model, and his tuning log names them all, so every
  answer out of the log said "GE-30: not in INV-2021-005.txt" for dozens of
  invoices (Q1, Q2, Q15). The "named beside" rule held for Odile's orders and
  fails on a per-client file. A reading that floods is worse than none.
- **Numbers over time: never spoke.** His tuning logs have several jobs a day,
  so they read as ledgers; his hygrometer and odometer tables were never served.
- **A date gone by: never spoke.** His logs are CSV rows with no later dates.

What worked was the kernel's own ranking (the tax notice served with its
resolution; the dad note; the service history), and what failed most was
vocabulary, the first reading's gap and his words list's job: owes / owed
against invoice and paid, number against `555-01xx`, miles against `odo`,
papers, recipe, happening.

## What this says

- The loop and the ranking held on a stranger's folder; the three readings
  did not. They stay out of the kernel, and "what isn't there" should not run
  as it is.
- Vocabulary is the biggest gap for a person with no list yet.
- A second blind character from a different trade would test this properly.
