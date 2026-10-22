# Odile Varga

Fictional. Made for the demo on 2026-10-22; no real person behind her. Every
identifier in her life uses a reserved range (phones `555-01xx`, mail at
`example.com`/`.org`/`.net`, addresses in `192.0.2.0/24`, card and account
numbers from published test sets), so the scrub has real shapes to catch and
nothing it catches can be anyone's.

## Who she is

- **61**, piano tuner and technician, thirty-four years at it. Half-retired:
  she still keeps forty-two regular clients and the county's two school
  districts.
- Lives in **Calloway**, a made-up high-desert town of 9,000, in the house she
  grew up in.
- **Keeps bees**: six hives behind the house, a seventh at a client's orchard.
  Sells honey at the Saturday market, cash and a card reader.
- **Her father, Laszlo, is 88** and moved in fourteen months ago after a fall.
  Early heart failure, eight medications, a cardiologist two towns over.
- **Her sister, Kati**, lives 900 miles away, phones Sundays, and has opinions
  about Laszlo's care she can't act on.
- **Widowed** eleven years. Her late husband, Teo, did the books; she still
  files the way he set it up and doesn't fully understand it.
- **A water-rights letter** from the county arrived in March about the well the
  hives use. She hasn't answered it.

## How her life writes itself down

| Source | Where | Shape | Size |
|---|---|---|---|
| Tuning log | `tuning/` | typed from the notebook, a year a file: client, piano, pitch before and after (measured, in cents), what she did, `next:` | 285 home visits, 24 district rounds (180 pianos) |
| Hive notes | `hives/` | one block per inspection day: frames, eggs, brood, queen, mites per 300 | 6 seasons |
| Laszlo's care | `dad/` | daily weight, BP and pulse since he moved in; meds; the cardiologist's after-visit summary | 14 months, daily |
| Phone texts | `texts/` | Kati, and clients booking | 1,200 messages |
| Voice memos | `memos/` | dictated in the van by day, at home by night | 180 |
| Money | `money/` | her invoices, a card reader's export, Teo's old sheet she still pastes into | 6 years |
| Mail | `mail/` | scanned and OCR'd: county, insurance, the districts' purchase orders, Medicare | 29 letters |
| Calendar | `calendar.ics` | tunings, district rounds, Laszlo's appointments and refills | 6 years and 2 months ahead |

All of it is made by `make_life.py`, seeded, so the same bytes every run.
`python3 make_life.py --check` holds the files to PLANTED.md.

## What she would say she wants

"Something that tells me who's due." That's all she'd ask for.

## What she'd never write down

She's tired. She's been doing three people's jobs: her own, Teo's books, and
nurse. Nobody has asked her how she is in a while.
