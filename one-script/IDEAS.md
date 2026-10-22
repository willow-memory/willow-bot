# Ideas: what else the one script could eat, and what it might spit out

Started 2026-10-21, for fun: new data to feed the pile, new scripts to add to
it, and new ways to see what comes out. Nothing here is sealed or built. Each
idea has a quick snippet. The snippets are sketches; the names they call are
real (checked in this tree), but the glue around them is not written yet.

Same rules as the rest of the one script: read only unless the human seals it,
three states never collapsed (`populated` / `empty` / `unreachable`), the
record holds hashes and not words, and **the vault is Sean's key**: no idea
here reads, writes or caches vault material.

## Index

**New data to feed it**

1. [Your own quotes as a corpus](#1-your-own-quotes-as-a-corpus)
2. [Jeles as a full pile repo](#2-jeles-as-a-full-pile-repo)
3. [The misses as a map](#3-the-misses-as-a-map)
4. [The bot's personality data as rows](#4-the-bots-personality-data-as-rows)

**New scripts to add**

5. [Bet on your own CI](#5-bet-on-your-own-ci)
6. [Verifiable fortunes](#6-verifiable-fortunes)
7. [Serendipity mode](#7-serendipity-mode)
8. [Code graph across repos](#8-code-graph-across-repos)

**Ways to see what comes out**

9. [A dashboard page of the record](#9-a-dashboard-page-of-the-record)
10. [Deep Thought's questions over time](#10-deep-thoughts-questions-over-time)

**Quick ones**

11. [How sessions end](#11-how-sessions-end)
12. [Twelve questions about your repos](#12-twelve-questions-about-your-repos)
13. [Where the two copies split](#13-where-the-two-copies-split)
14. [A daily lore row from commits](#14-a-daily-lore-row-from-commits)
15. [A rune for each sealed session](#15-a-rune-for-each-sealed-session)

**Found while trying it**

16. [Turn on the embedder](#16-turn-on-the-embedder)
17. [Check the README's prompt timing](#17-check-the-readmes-prompt-timing)

**Outside projects that could attach cleanly**

18. [SQLite FTS5: BM25 as a rung](#18-sqlite-fts5-bm25-as-a-rung)
19. [Model2Vec: a second static embedder](#19-model2vec-a-second-static-embedder)
20. [tree-sitter: chunk by function, not by line](#20-tree-sitter-chunk-by-function-not-by-line)
21. [ripgrep: a faster `--exact` seed](#21-ripgrep-a-faster---exact-seed)
22. [RapidFuzz: a faster typo rung](#22-rapidfuzz-a-faster-typo-rung)
23. [Datasette: browse the store, read only](#23-datasette-browse-the-store-read-only)
24. [DuckDB: SQL straight over `record.jsonl`](#24-duckdb-sql-straight-over-recordjsonl)
25. [OpenTimestamps: an anchor for the record](#25-opentimestamps-an-anchor-for-the-record)
26. [Kiwix ZIM files: an offline encyclopedia rung](#26-kiwix-zim-files-an-offline-encyclopedia-rung)
27. [Hypothesis: property tests for the byte rules](#27-hypothesis-property-tests-for-the-byte-rules)
28. [mutmut: do the gates catch a broken gate?](#28-mutmut-do-the-gates-catch-a-broken-gate)
29. [ast-grep: structural search as a seed](#29-ast-grep-structural-search-as-a-seed)
30. [scc and git-of-theseus: the repos as numbers](#30-scc-and-git-of-theseus-the-repos-as-numbers)
31. [Gource: the pile as a movie](#31-gource-the-pile-as-a-movie)
32. [Pagefind: search for the dashboard page](#32-pagefind-search-for-the-dashboard-page)

**The world's data**

*Things to bet on*

33. [Open-Meteo: Out-forecast the weather forecaster](#33-open-meteo-out-forecast-the-weather-forecaster)
34. [USGS: The earthquake base-rate game](#34-usgs-the-earthquake-base-rate-game)
35. [Wikipedia pageviews: Which article wins tomorrow](#35-wikipedia-pageviews-which-article-wins-tomorrow)
36. [Manifold: You against the crowd](#36-manifold-you-against-the-crowd)
37. [Hacker News: Will it hit the front page](#37-hacker-news-will-it-hit-the-front-page)
38. [NOAA tides: The tide is a sure thing](#38-noaa-tides-the-tide-is-a-sure-thing)

*Things to read*

39. [Open English WordNet: A dictionary rung](#39-open-english-wordnet-a-dictionary-rung)
40. [wordfreq: What's rare to you, common to the world](#40-wordfreq-whats-rare-to-you-common-to-the-world)
41. [Tatoeba: Sentences in every language](#41-tatoeba-sentences-in-every-language)
42. [Project Gutenberg: Old books for serendipity](#42-project-gutenberg-old-books-for-serendipity)

*Things to draw from*

43. [Skyfield: A horoscope with real planets](#43-skyfield-a-horoscope-with-real-planets)
44. [Natural Earth: Today's place](#44-natural-earth-todays-place)
45. [The Met Open Access: Art of the day](#45-the-met-open-access-art-of-the-day)
46. [Lichess: Puzzle of the day](#46-lichess-puzzle-of-the-day)

*Things to measure yourself against*

47. [Our World in Data: The 90 percent quiz](#47-our-world-in-data-the-90-percent-quiz)
48. [GH Archive: Is your CI normal](#48-gh-archive-is-your-ci-normal)

**More outside projects (the research pass)**

49. [bm25s: BM25 in numpy](#49-bm25s-bm25-in-numpy)
50. [tantivy-py: a full search engine](#50-tantivy-py-a-full-search-engine)
51. [FlashRank: a CPU reranker](#51-flashrank-a-cpu-reranker)
52. [sqlite-vec: vectors in the same file](#52-sqlite-vec-vectors-in-the-same-file)
53. [scip-python: precise symbols](#53-scip-python-precise-symbols)
54. [tree-sitter-language-pack: every language](#54-tree-sitter-language-pack-every-language)
55. [rfc3161-client: a second timestamp](#55-rfc3161-client-a-second-timestamp)
56. [pymerkle: proofs for the chain](#56-pymerkle-proofs-for-the-chain)
57. [Tracery: oracle text from a grammar](#57-tracery-oracle-text-from-a-grammar)
58. [ink: branching stories](#58-ink-branching-stories)

**Gaps the one script found in itself**

59. [The typo rung rewrites names it doesn't know](#59-the-typo-rung-rewrites-names-it-doesnt-know)
60. [The miss row lost two words](#60-the-miss-row-lost-two-words)
61. [The Jeles rung was never on](#61-the-jeles-rung-was-never-on)
62. [A big corpus wins a code bite on volume](#62-a-big-corpus-wins-a-code-bite-on-volume)

**What connects all the worlds**

*IDs the pile already has*

63. [Every blob is already a Software Heritage ID](#63-every-blob-is-already-a-software-heritage-id)
64. [Every chunk is already an IPFS CID](#64-every-chunk-is-already-an-ipfs-cid)
65. [The same blob in supply-chain words: gitoid and in-toto](#65-the-same-blob-in-supply-chain-words-gitoid-and-in-toto)
66. [Keep a SHA-256 git blob too](#66-keep-a-sha-256-git-blob-too)

*Hubs that join everything else*

67. [Wikidata as the crosswalk](#67-wikidata-as-the-crosswalk)
68. [OpenAlex and DOIs: papers](#68-openalex-and-dois-papers)
69. [ORCID and ROR: people and organisations](#69-orcid-and-ror-people-and-organisations)
70. [GeoNames and OpenStreetMap: places](#70-geonames-and-openstreetmap-places)
71. [Country and language codes](#71-country-and-language-codes)

*The record in the world's formats*

72. [W3C PROV: the record as provenance](#72-w3c-prov-the-record-as-provenance)
73. [RO-Crate: a snapshot as a research object](#73-ro-crate-a-snapshot-as-a-research-object)
74. [Frictionless, DCAT and an SPDX license on every row](#74-frictionless-dcat-and-an-spdx-license-on-every-row)
75. [in-toto Statements: a seal others can check](#75-in-toto-statements-a-seal-others-can-check)
76. [CloudEvents: a record row in a common envelope](#76-cloudevents-a-record-row-in-a-common-envelope)

*Pointing into text without storing it*

77. [Line and text fragments](#77-line-and-text-fragments)
78. [W3C Web Annotation: a chunk talking about the world](#78-w3c-web-annotation-a-chunk-talking-about-the-world)
79. [Memento and the Wayback Machine: what a page said then](#79-memento-and-the-wayback-machine-what-a-page-said-then)

*Protocols*

80. [MCP: the pile as resources by hash](#80-mcp-the-pile-as-resources-by-hash)
81. [A feed of sealed decisions](#81-a-feed-of-sealed-decisions)

*Time and place*

82. [The drand round on every row](#82-the-drand-round-on-every-row)
83. [Dates that admit doubt: EDTF and the tz database](#83-dates-that-admit-doubt-edtf-and-the-tz-database)
84. [Cells on the map: geohash and H3](#84-cells-on-the-map-geohash-and-h3)
85. [The snapshots' own IDs](#85-the-snapshots-own-ids)

**One from you**

86. [Compare the snippets in the boxes, and serve the comparison](#86-compare-the-snippets-in-the-boxes-and-serve-the-comparison)

**The stupid pile**

87. [Roll for merge](#87-roll-for-merge)
88. [Grade the horoscope](#88-grade-the-horoscope)
89. [The sigh is a forecaster](#89-the-sigh-is-a-forecaster)
90. [Ask Jeles whether today is the day for a rebase](#90-ask-jeles-whether-today-is-the-day-for-a-rebase)
91. [Deep Thought already answered 42](#91-deep-thought-already-answered-42)
92. [Earthquakes as dice](#92-earthquakes-as-dice)
93. [Lunatic mode](#93-lunatic-mode)
94. [Rainy-day scope](#94-rainy-day-scope)
95. [The spurious-correlation machine](#95-the-spurious-correlation-machine)
96. [Your decisions as a Gource movie](#96-your-decisions-as-a-gource-movie)
97. [The pile writes poetry](#97-the-pile-writes-poetry)
98. [Rebase shame tarot](#98-rebase-shame-tarot)
99. [Chess for merge conflicts](#99-chess-for-merge-conflicts)
100. [Ask the Met what the session looked like](#100-ask-the-met-what-the-session-looked-like)
101. [The tide checks the clock](#101-the-tide-checks-the-clock)
102. [The market bets on the bot](#102-the-market-bets-on-the-bot)

[How much is already here](#how-much-is-already-here)

**What fell out of the second run**

103. [Writing an idea down hides its gap](#103-writing-an-idea-down-hides-its-gap)
104. [NEEDS YOU doesn't list a session waiting on your seal](#104-needs-you-doesnt-list-a-session-waiting-on-your-seal)
105. [Deep Thought measures the world before D2](#105-deep-thought-measures-the-world-before-d2)
106. [A box with its own keys can't be looked into](#106-a-box-with-its-own-keys-cant-be-looked-into)
107. [This branch merges clean](#107-this-branch-merges-clean)

**What fell out of the third run**

108. [A helper that passed its tests found 10 of 68](#108-a-helper-that-passed-its-tests-found-10-of-68)
109. [The cover goes stale once the ideas are built](#109-the-cover-goes-stale-once-the-ideas-are-built)
110. [Six purposes have two homes](#110-six-purposes-have-two-homes)
111. [Every commit asks old boxes to re-check their writes](#111-every-commit-asks-old-boxes-to-re-check-their-writes)
112. ["What's blocked" doesn't find the blocked list](#112-whats-blocked-doesnt-find-the-blocked-list)

**The hidden egg**

113. [Every demo is one loop, and the kernel is it](#113-every-demo-is-one-loop-and-the-kernel-is-it)
114. [What the demos' characters needed](#114-what-the-demos-characters-needed)
115. [Crossing the streams: what already lives in the box](#115-crossing-the-streams-what-already-lives-in-the-box)
116. [One repo at a time](#116-one-repo-at-a-time)
117. [The kernel on the-box: what plopped out](#117-the-kernel-on-the-box-what-plopped-out)
118. [Asymmetric offsets: a puzzle from the Discord group](#118-asymmetric-offsets-a-puzzle-from-the-discord-group)
119. [Nothing checks for personal data before serving](#119-nothing-checks-for-personal-data-before-serving)
120. [The OAuth handoff, and the box picking its own tests](#120-the-oauth-handoff-and-the-box-picking-its-own-tests)
121. [The seed line: what the small script was before it was small](#121-the-seed-line-what-the-small-script-was-before-it-was-small)
122. [The private chunks, scanned first, and SEED10 found](#122-the-private-chunks-scanned-first-and-seed10-found)

[What the one script said about this list](#what-the-one-script-said-about-this-list)

## New data to feed it

### 1. Your own quotes as a corpus

The docstrings and READMEs are full of *the operator, 2026-10-19: "…"*. A
small `said.py` collects every one into a dated timeline of decisions, so a
bite can be run against what you actually said and show where code and words
have drifted apart.

```python
# said.py (sketch): harvest "the operator, <date>: <quote>" across the pile
SAID = re.compile(r'[Tt]he operator,? (\d{4}-\d{2}-\d{2})?:?\s*\*?"([^"]+)"')
for repo, path, text in pile_files(cfg):
    for date, quote in SAID.findall(text):
        yield {
            "kind": "said",
            "date": date,
            "where": f"{repo}:{path}",
            "quote_hash": sha256(quote),
        }  # the record keeps the hash
```

### 2. Jeles as a full pile repo

Today Jeles is the escalation rung: asked only when the pile misses. Indexed
as a normal repo, you can compare which bites Jeles wins and which your own
code wins. (It already happens by accident; see 16.)

```sh
python -m onescript --repo willow-bot=../ --repo jeles=../../Jeles checkin
python -m onescript seed "how do seals work at checkout"   # who ranks first?
```

### 3. The misses as a map

Every seed whose words the store has no posting for leaves a `miss` row:
the bite's hash and each missing word keyed with the store key. Nothing reads
them back yet (`read-misses` is a different thing: it clusters missed
*forecasts*). Group the `miss` rows by keyed word and session, and you get a
list of what the box keeps not knowing, without ever holding the words.

```python
rows = [json.loads(l) for l in open(box / "record.jsonl")]
seen = defaultdict(set)
for r in rows:
    if r["kind"] == "miss":
        for k in r["missing"]:  # BLAKE2b-128, never a word
            seen[k].add(r["checkin"])
for k, sessions in sorted(seen.items(), key=lambda kv: -len(kv[1]))[:10]:
    print(len(sessions), k[:12])
```

### 4. The bot's personality data as rows

`sigh.py` red streaks, `rebase_shame.py` force-push counts and `troll.py`'s
pools become `verdict` rows the pile can serve. A bite like "flaky CI" then
returns real numbers instead of prose.

```python
import sigh
from onescript import table

for repo, pr in open_prs:
    table.verdict(
        cfg,
        {"what": "ci_red_streak", "repo": repo, "pr": pr, "value": sigh.get(repo, pr)},
    )
```

## New scripts to add

### 5. Bet on your own CI

`market.py` keeps forecasts nobody can edit or drop. Point the same ledger at
your PRs instead of SPY, then score the forecasts with `calibration.py`. Who
forecasts better, you or the model?

```python
api.forecast(
    cfg,
    "pr-160-green-first-push",
    [{"path": "yes", "p": 0.7}, {"path": "no", "p": 0.3}],
    by="human",
)
# later, once CI answers:
from onescript.calibration import brier, bins

print(sum(brier(p, hit) for p, hit in pairs) / len(pairs), bins(pairs))
```

### 6. Verifiable fortunes

`horoscope.reading` and `runes.cast` are seeded by login + date. Seed them
from a drand round through `stream.py` instead, and every reading is provable
after the fact and impossible to pick in advance.

```python
from onescript import beacon
from onescript.stream import StreamRandom

rnd = beacon.round_for(day)
r = beacon.drand_reader(rnd)["randomness"]
rng = StreamRandom.from_seed(bytes.fromhex(r), label=login.encode())
print(rng.seed_id, rng.choice(TEMPLATES))  # StreamRandom has choice, not randrange
```

### 7. Serendipity mode

A drand round picks one random chunk from each of two repos; they are served
side by side with one question: "what joins these?" A way to find the links
between willow-bot, Jeles, the-table and the Forge that nobody planned.

```python
rng = StreamRandom.from_seed(bytes.fromhex(randomness), label=b"serendipity")
a, b = rng.sample(sorted(cfg.repos), 2)
left = rng.choice(chunks_of(store, a))
right = rng.choice(chunks_of(store, b))
serve(cfg, spec_for([left, right]), max_chars=4000)  # "what joins these?"
```

### 8. Code graph across repos

`graph.py` already wraps `cbm` (codebase-memory-mcp). Run it over every repo
and show which functions are called from somewhere else, and which copies are
held by parity tests versus quietly drifting.

```python
from onescript import graph

run = graph.cbm_runner()
for repo in cfg.repos:
    # every read is {state, why, stamp, rows, total}: never a bare []
    print(repo, graph.symbols_for(cited_text, repo, runner=run))
```

## Ways to see what comes out

### 9. A dashboard page of the record

One page: sessions sealed / waiting / denied, misses over time, forecast
calibration, and which rung (`fused` or `words`) answered each prompt. Read
only, built from `record.jsonl`.

```python
rows = [json.loads(l) for l in open(box / "record.jsonl")]
by_kind = Counter(r["kind"] for r in rows)
rungs = Counter(r.get("rung", {}).get("used") for r in rows if r["kind"] == "lookup")
```

### 10. Deep Thought's questions over time

Run `deep_thought.py` before and after each merge and diff the lines that need
your decision. The morning screen becomes a history of questions asked and
answered.

```sh
python deep_thought.py > before.txt; git merge X; python deep_thought.py > after.txt
diff <(grep '^?' before.txt) <(grep '^?' after.txt)
```

## Quick ones

### 11. How sessions end

`pools.measure` on session bundles: how sessions usually end (sealed, denied,
abandoned), how long they run, and the outliers.

```python
from onescript import pools

print(pools.measure([bundle_as_round(s) for s in sessions], seats=1))
```

### 12. Twelve questions about your repos

`deck.py` asks 12 yes/no questions about random data. Ask them about your
repos instead: "will willow-bot ship a release this week?", "will any PR go
red three times?"

```python
DECK = [("release-this-week", lambda st: st["releases"] > 0), ...]
```

### 13. Where the two copies split

`diverge` finds where two refs part, down to the smallest unit. Point it at
willow-bot's one script and the copy history in willows-grove.

```sh
python -m onescript diverge <grove-ref> <bot-ref>
```

### 14. A daily lore row from commits

One `lore` fact a day, drawn from that day's commit subjects across the
pile. `lore` only takes a sealed `world/*.md` proposal, so the day's subjects
go in as a proposal row first, and become lore once you seal it.

```sh
git log --since=midnight --format='%s' > world/today.md   # as a proposal row
python -m onescript turn --proposal today.jsonl
python -m onescript lore proposal:<sha256>                # after your seal
```

### 15. A rune for each sealed session

`runes.cast` already takes a SHA. Give every sealed session its rune, cast
from its `session:<sha256>` subject.

```python
import runes

print(runes.cast(subject.removeprefix("session:")))
```

## Found while trying it

First live run, 2026-10-21: check-in `open` over willow-bot, Jeles and
willows-grove (1,366 files), `--no-tests`, ruff 0.16.7 from a scratch venv.

### 16. Turn on the embedder

The bite *"how do seals work at checkout"* ranked Jeles entries about
supermarket barcode checkouts and light switches above the session-seal code
that runs at check-out. With no embedder, only the words match.

```sh
pip install -e ".[embed]"
ONESCRIPT_EMBED=~/.cache/wordllama python -m onescript prompt < event.json
```

### 17. Check the README's prompt timing

The README says about 11–14 s a prompt on a warm store. The first live
`prompt` came back `populated` in 0.35 s, after the lazy-store and one-git-pass
changes. Worth re-measuring and updating the line.

```sh
time (echo '{"prompt":"..."}' | python -m onescript prompt > /dev/null)
```

## Outside projects that could attach cleanly

Not ours, and picked because they fit the one script's shape. "Cleanly" here
means: off by default, an optional extra the way `[embed]` is (the core stays
stdlib only), `unreachable` with a reason when it isn't installed, runs
offline (or, where it can't, sends only a hash and says so), and deletes
clean. Licenses are worth a check before any of these lands; the GPL ones
should stay a separate process, never an import.

### 18. SQLite FTS5: BM25 as a rung

Not even outside: FTS5 ships in most Python builds' `sqlite3`. A BM25 rank
beside the words rank, fused by the same RRF as the embedder. It would have
helped the checkout bite in 16, since BM25 weights rare words higher.

```python
db.execute(
    "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, chunk_id UNINDEXED)"
)
hits = db.execute(
    "SELECT chunk_id, bm25(fts) FROM fts WHERE fts MATCH ? "
    "ORDER BY bm25(fts) LIMIT 100",
    (query,),
).fetchall()
```

Watch: the store already holds hashed postings; an FTS table holds the words,
so it lives with the chunks, never in the record.

### 19. Model2Vec: a second static embedder

Same idea as WordLlama (static vectors, no GPU, milliseconds), from
MinishLab. Two embedders give a cheap second opinion: where they disagree on
the top chunk is worth a look.

```python
from model2vec import StaticModel

m = StaticModel.from_pretrained("/models/potion-base-8M")  # a local dir
vecs = m.encode([bite] + chunk_texts)
```

Watch: `from_pretrained` downloads from Hugging Face when given a name.
Fetch once off the box, then only ever point it at a local dir, the way the
WordLlama rung does. Its loader passes `force_download=True` on to Hugging
Face, so prove a local dir really stays offline with `HF_HUB_OFFLINE=1`
before trusting it.

### 20. tree-sitter: chunk by function, not by line

The pile chunks by lines. tree-sitter parses each file locally, so a chunk
can be one whole function or class, and a hit says `pile.score_grams`
instead of `pile.py:212`.

```python
import tree_sitter_python as tsp
from tree_sitter import Language, Parser

tree = Parser(Language(tsp.language())).parse(src)
for node in tree.root_node.children:
    if node.type in ("function_definition", "class_definition"):
        yield node.start_point[0], node.end_point[0]
```

### 21. ripgrep: a faster `--exact` seed

`seed --exact` reads every file in Python. `rg --json` returns the same
files and lines, much faster, and its JSON is easy to check against the
index's blob hashes.

```sh
rg --json -w -i vault ../willow-bot ../Jeles | jq -c 'select(.type=="match")'
```

### 22. RapidFuzz: a faster typo rung

Rung 2 (`Cached.repair`) tries every spelling one edit away. RapidFuzz
scores a word against the store's whole vocabulary in C, and can reach two
edits without blowing up.

```python
from rapidfuzz import process, distance

process.extract(
    word, vocab, scorer=distance.Levenshtein.distance, score_cutoff=2, limit=5
)
```

Watch: it runs while the store is open, so the words still never leave the
bite; only the hashed result is recorded.

### 23. Datasette: browse the store, read only

Point Datasette at the pile's SQLite file and you get a local web UI over
every table, with SQL, facets and JSON export, without writing any of it.

```sh
datasette box-store/pile-500.sqlite --immutable   # http://127.0.0.1:8001
```

### 24. DuckDB: SQL straight over `record.jsonl`

No loading step: DuckDB reads JSON lines in place. The fastest way to answer
"what's in the record?" by hand, and a good engine for the dashboard in 9.

```sh
duckdb -c "SELECT kind, count(*) FROM read_json_auto('record.jsonl')
           GROUP BY 1 ORDER BY 2 DESC"
```

Watch: DuckDB installs a known extension from the internet the first time
it's needed (`autoinstall_known_extensions` is on by default). Turn that off
and pre-install `json` to keep it offline.

### 25. OpenTimestamps: an anchor for the record

Every check-in says *anchor: never sealed; a cut or a rewrite of the record
can't be seen*. OpenTimestamps sends only a SHA-256 to its public calendars
and returns a proof that the hash existed by a Bitcoin block's time. Stamp
each session bundle or the record's tip, and a rewrite after the fact shows.

```sh
ots stamp sessions/2026-10-21.json      # writes sessions/2026-10-21.json.ots
ots verify sessions/2026-10-21.json.ots # needs a local Bitcoin Core node
```

Watch: this is the one item that goes online. Only the hash leaves the box,
it's off by default, and no network is `unreachable`, not a failed seal. It
needs no key, so it never comes near the vault. Stamping is easy; verifying
needs a Bitcoin Core node (a pruned one is fine), so until there is one a
proof is `unreachable`, not checked. The client's last release was
2024-12-31. Its license is LGPL-3.

### 26. Kiwix ZIM files: an offline encyclopedia rung

Kiwix packs Wikipedia (and Stack Exchange, Gutenberg, and more) into single
offline ZIM files with a built-in full-text index. A rung after Jeles: when
neither your pile nor Jeles holds the bite, ask a ZIM on disk. Fun for
serendipity mode (7) too.

```python
from libzim.reader import Archive
from libzim.search import Query, Searcher

zim = Archive("wikipedia_en_simple_all.zim")
hits = Searcher(zim).search(Query().set_query(bite)).getResults(0, 5)
```

Watch: the bindings (`pip install libzim`) are GPL-3.0-or-later, so keep it
a separate process. Each ZIM file carries its own content license
(Wikipedia's is CC BY-SA); cite the article in every served row.

### 27. Hypothesis: property tests for the byte rules

The pile lives on rules that must agree to the byte: `words_v1`, the
stream's draws, `bite_key`. Hypothesis throws thousands of odd inputs at
them (combining marks, empty strings, huge seeds) and shrinks any failure to
the smallest one.

```python
from hypothesis import given, strategies as st
from onescript.stream import StreamRandom


@given(st.binary(), st.integers(1, 2**32))
def test_below_stays_in_range(seed, n):
    assert 0 <= StreamRandom.from_seed(seed).randrange(n) < n
```

### 28. mutmut: do the gates catch a broken gate?

Mutation testing flips `<` to `<=`, `True` to `False`, and so on, then runs
the tests. A mutant that survives in `gate.py` or `proposals.py` is a way a
seal could go wrong without any test noticing.

```toml
# pyproject.toml
[tool.mutmut]
source_paths = ["onescript/gate.py", "onescript/proposals.py"]
```

```sh
mutmut run && mutmut results
```

### 29. ast-grep: structural search as a seed

Search code by its shape, not its words: "every `rec.append` that writes a
`kind` other than a states constant". A seed rung for bites that are about
code structure.

```sh
ast-grep run -l python -p 'run.rec.append($KIND, $$$)' onescript/
```

### 30. scc and git-of-theseus: the repos as numbers

`scc` counts lines, code and complexity per language in a blink.
git-of-theseus plots how much of each year's code still survives. Both
become `verdict` rows the pile can serve ("how much of the one script is
still from October?").

```sh
scc --format json ../willow-bot ../Jeles ../willow-memory/willows-grove
git-of-theseus-analyze ../willow-bot && git-of-theseus-stack-plot cohorts.json
```

Watch: git-of-theseus's last release was 2023 (0.3.4), so it may need
coaxing on a new Python.

### 31. Gource: the pile as a movie

Gource animates a repo's history as a growing tree. Combine the three repos'
logs and you watch the one script move from willows-grove into the bot.

```sh
gource --output-custom-log grove.txt ../willow-memory/willows-grove
gource --output-custom-log bot.txt ../willow-bot
cat grove.txt bot.txt | sort -n | gource --log-format custom -
```

### 32. Pagefind: search for the dashboard page

If the dashboard (9) or IDEAS becomes a static page, Pagefind builds a
search index for it at build time, with no server, all in the browser.

```sh
npx pagefind --site dashboard/
```

## The world's data

Everything above is about your own repos. This is about everything else: the
public data that's out there for free, and the games you can play with it.
They all use pieces the one script already has: forecasts that can't be
edited (`api.forecast`, the way `market.py` bets on SPY), Brier scores
(`calibration.py`), drand draws (`beacon`, `stream`), served chunks, and
serendipity (7).

The rules for outside data (built: `onescript/snapshot.py`, `snapshot` and
`snapshot-read` on the command line, the fetch only with `--fetch`):
- Fetch it once into a dated snapshot, with its SHA-256, source URL and
  license on the record.
- Only the human refreshes a snapshot.
- After that it reads offline.
- A forecast settled from live data names its reader, the way
  `market.stooq_reader` does. No network means `unreachable`, never a
  wrong answer.

The endpoints below are free and need no key, as far as I know. They are
sketches and haven't been run.

### Things to bet on

#### 33. Open-Meteo: Out-forecast the weather forecaster

Every morning, wager on "rain tomorrow in your town" before you look. Then
log Open-Meteo's own forecast as a second wager. Settle both from its
historical archive and compare Brier scores: you against a weather model.

```python
q = urllib.parse.urlencode(
    {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "timezone": "auto",
    }
)
f = snapshot.get(
    cfg,
    f"rain-{day}",
    f"https://api.open-meteo.com/v1/forecast?{q}",
    license="CC-BY-4.0",
)
api.forecast(cfg, f"rain-{day}", [{"path": "yes", "p": 0.3}, {"path": "no", "p": 0.7}])
# settle: archive-api.open-meteo.com/v1/archive?...&start_date=day&end_date=day
```

#### 34. USGS: The earthquake base-rate game

"Will there be an M6+ somewhere this week?" The USGS feeds settle it, and
its catalogue gives the base rate, so you find out whether you beat the
base rate or just repeat it.

```python
q = urllib.parse.urlencode({"starttime": monday, "endtime": sunday, "minmagnitude": 6})
n = snapshot.get(
    cfg,
    f"quakes-m6-{monday}",
    f"https://earthquake.usgs.gov/fdsnws/event/1/count?{q}",
    license="LicenseRef-us-public-domain",
)
```

#### 35. Wikipedia pageviews: Which article wins tomorrow

Draw two Wikipedia articles from the stream and guess which gets more views
tomorrow. Twelve of them make a `deck.py` round. A good way to find out how
little you know about what the world reads.

```python
url = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    f"en.wikipedia/all-access/user/{title}/daily/{day}/{day}"
)
```

Watch: Wikimedia asks for a User-Agent that names you and how to reach you.

#### 36. Manifold: You against the crowd

Copy a public prediction-market question into your own ledger with your own
probability, write the crowd's probability beside it, and when it resolves,
score both. Over a season you learn where you beat the crowd.

```python
m = snapshot.as_json(
    snapshot.get(
        cfg,
        f"manifold-{market_id}",
        f"https://api.manifold.markets/v0/market/{market_id}",
        license="LicenseRef-unverified",
    )
)
api.forecast(cfg, f"manifold-{market_id}", mine, by="human")
api.forecast(cfg, f"manifold-{market_id}-crowd", crowd(m["probability"]), by="crowd")
```

#### 37. Hacker News: Will it hit the front page

For a handful of new posts, wager on which make the front page. Algolia's
HN search API settles it.

```python
front = snapshot.get(
    cfg,
    f"hn-front-{day}",
    "https://hn.algolia.com/api/v1/search?tags=front_page",
    license="LicenseRef-unverified",
)
```

#### 38. NOAA tides: The tide is a sure thing

A control for the whole forecasting game: NOAA publishes tide predictions
that are nearly always right. If your Brier score on tides isn't close to
zero, the plumbing is broken, not your judgement.

```python
q = urllib.parse.urlencode(
    {
        "product": "predictions",
        "station": station,
        "begin_date": day,
        "range": 24,
        "datum": "MLLW",
        "interval": "hilo",
        "units": "metric",
        "time_zone": "lst_ldt",
        "format": "json",
    }
)
snapshot.get(
    cfg,
    f"tides-{station}-{day}",
    f"https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?{q}",
    license="LicenseRef-us-public-domain",
)
```

### Things to read

#### 39. Open English WordNet: A dictionary rung

The checkout problem (16) is a word with two senses. WordNet knows "seal"
the stamp, "seal" the animal and "seal" the gasket. Expanding a bite's words
with the right synonyms (or flagging when a word has several senses) is a
cheap fix before any embedder. CC BY 4.0, offline once downloaded.

```python
import wn

wn.download("oewn:2024")  # once, off the box
[s.definition() for s in wn.synsets("seal")]
```

#### 40. wordfreq: What's rare to you, common to the world

wordfreq gives how common a word is across the web, books, subtitles and
more. Set that against your pile's counts and you get your own dialect: the
words you use far more than the world does ("seal", "bite", "rung", "pile").
Good for ranking too: a word rare in the world but common for you is a
strong signal.

```python
from wordfreq import zipf_frequency

mine = store_count(w) / store_total
print(w, zipf_frequency(w, "en"), mine)
```

Watch: wordfreq's data stopped being updated in 2024; it's a snapshot of
the web before then.

#### 41. Tatoeba: Sentences in every language

Jeles already has `source_lang` and `target_lang`. Tatoeba is millions of
sentences with their translations, CC BY 2.0 FR. A bite in one language
could pull the same idea in another.

```sh
curl -O https://downloads.tatoeba.org/exports/sentences.tar.bz2
```

#### 42. Project Gutenberg: Old books for serendipity

Serendipity mode (7) gets stranger when one side is a paragraph from a
public-domain book: your gate code beside Moby-Dick.

```python
book = snapshot.text(
    snapshot.get(
        cfg,
        f"gutenberg-{n}",
        f"https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt",
        license="LicenseRef-gutenberg",
    )
)
```

Watch: Gutenberg asks that bulk downloads come from a mirror, not the site.

### Things to draw from

#### 43. Skyfield: A horoscope with real planets

`horoscope.py` is made up. Skyfield computes where the planets really are,
offline, from a JPL ephemeris file. Each sign is a real 30-degree slice of
the ecliptic, and the drand round picks the reading for the sign.

```python
from skyfield.api import load

ts, eph = load.timescale(), load("de421.bsp")  # the file, fetched once
lat, lon, _ = (
    eph["earth"].at(ts.now()).observe(eph["mars"]).apparent().ecliptic_latlon()
)
sign = SIGNS[int(lon.degrees // 30)]
```

#### 44. Natural Earth: Today's place

Natural Earth's populated places are public domain and small. A drand round
picks one, and it becomes the day's place: where the oracle "is", where the
weather bet (33) is made, where the-table's next scene is set.

```python
place = rng.choice(places)  # rng = StreamRandom.from_seed(randomness)
```

#### 45. The Met Open Access: Art of the day

Hundreds of thousands of the Met's public-domain works, CC0, by API. A drand
draw picks one for each sealed session, beside its rune (15).

```python
MET = "https://collectionapi.metmuseum.org/public/collection/v1/objects"
ids = snapshot.as_json(snapshot.get(cfg, "met-ids", MET, license="CC0-1.0"))[
    "objectIDs"
]
pick = rng.choice(ids)
art = snapshot.get(cfg, f"met-{pick}", f"{MET}/{pick}", license="CC0-1.0")
```

Watch: check `isPublicDomain` on each object before showing its image.

#### 46. Lichess: Puzzle of the day

Lichess publishes millions of rated chess puzzles, CC0. A drand round picks
today's, and `python-chess` (already in the table tests) checks the answer.
Predict whether you'll solve it first, and that's a wager too.

```sh
curl -O https://database.lichess.org/lichess_db_puzzle.csv.zst
```

### Things to measure yourself against

#### 47. Our World in Data: The 90 percent quiz

The classic calibration drill: "Population of Nigeria in 2020? Give a range
you're 90% sure of." Our World in Data has thousands of such numbers as
CSV. If far fewer than 9 in 10 of your ranges hold the answer, you're
overconfident. `calibration.bins` then shows exactly where.

```python
rows = snapshot.text(
    snapshot.get(
        cfg,
        f"owid-{slug}",
        f"https://ourworldindata.org/grapher/{slug}.csv",
        license="CC-BY-4.0",
    )
)
hit = low <= answer(rows, country, year) <= high
```

#### 48. GH Archive: Is your CI normal

GH Archive records every public GitHub event, hour by hour. From it: how
often do PRs everywhere go green on the first push, how long do they stay
open, how many force-pushes? That's the base rate for betting on your own
CI (5), and for `rebase_shame.py`: are you actually worse than average?

```sh
curl -O https://data.gharchive.org/2026-10-20-15.json.gz   # one hour
```

Watch: one hour is around 100 MB unpacked. Sample a few hours, don't pull
the year.

## More outside projects (the research pass)

A second pass (2026-10-21) checked ideas 18–32 against their own repos and
PyPI, and found these. Versions are PyPI's at the time; anything the pass
couldn't confirm is said.

### 49. bm25s: BM25 in numpy

A fast BM25 over sparse matrices, MIT. A quicker way to try idea 18's BM25
rung without touching the store's schema.

```python
import bm25s

retriever = bm25s.BM25()
retriever.index(bm25s.tokenize(chunk_texts, stopwords="en"))
docs, scores = retriever.retrieve(bm25s.tokenize(bite), k=10)
```

Watch: needs numpy. `retrieve`'s exact return is from memory; the index and
tokenize lines are from its README.

### 50. tantivy-py: a full search engine

Python bindings for Tantivy (a Rust Lucene), MIT. A whole inverted index
with BM25, phrases and fields, if the pile ever outgrows SQLite.

Watch: a compiled wheel, heavier than anything else here. Usage not checked.

### 51. FlashRank: a CPU reranker

A tiny cross-encoder, Apache-2.0, that reorders the top results by reading
the bite and the chunk together. A rung after RRF: words and the embedder
find 100, FlashRank picks the 5.

Watch: it fetches its model at first use unless you give it a `cache_dir`
that already holds one. Last release 0.2.10, January 2025.

### 52. sqlite-vec: vectors in the same file

A SQLite extension for vector search, MIT/Apache-2.0. The embedder's vectors
could live in the pile's own SQLite file instead of being recomputed.

Watch: pre-1.0 (0.1.9). Usage not checked.

### 53. scip-python: precise symbols

Sourcegraph's SCIP indexer for Python, MIT: exact definitions and
references, the kind of facts `graph.py` wants, without `cbm`.

```sh
scip-python index . --project-name=willow-bot   # with the venv active
```

Watch: needs Node and npm.

### 54. tree-sitter-language-pack: every language

Prebuilt tree-sitter grammars for hundreds of languages, MIT, so idea 20
works on the Rust, JS and shell files in the pile too.

Watch: whether it downloads grammars at runtime wasn't confirmed.

### 55. rfc3161-client: a second timestamp

Trail of Bits' RFC 3161 client, Apache-2.0. A timestamp from a standard
timestamp authority beside OpenTimestamps (25), and it verifies offline
against a root certificate you keep, no Bitcoin node needed.

```python
from rfc3161_client import TimestampRequestBuilder

req = TimestampRequestBuilder().data(bundle_bytes).build()  # sent to a TSA
```

Watch: stamping is a network call to the authority; only the hash goes.

### 56. pymerkle: proofs for the chain

Merkle trees with inclusion and consistency proofs. The record is a hash
chain, which proves order but needs the whole file to check. A Merkle tree
over it proves one row with a handful of hashes.

Watch: GPL-3.0-or-later, so a separate process only. Usage not checked.

### 57. Tracery: oracle text from a grammar

Kate Compton's grammar engine for generated text, ISC. The horoscope and
rune lines become grammars that never run dry, and the drand stream picks
every branch, so a reading is still provable.

```js
const grammar = tracery.createGrammar({origin: ["#omen# #advice#"], ...});
grammar.flatten("#origin#");
```

Watch: JavaScript, and it rolls its own randomness; the one script would
have to hand it the stream's draws (a Python port is the easier route).

### 58. ink: branching stories

inkle's narrative language, MIT, compiled to JSON and run by inkjs. A story
the-table could tell, where every choice the game makes for you comes from
the drand stream.

Watch: same as Tracery: the seeding is ours to wire in.

## Gaps the one script found in itself

From the check described in [What the one script said about this
list](#what-the-one-script-said-about-this-list).

### 59. The typo rung rewrites names it doesn't know

`Cached.repair` (rung 2) tries an unseen word one edit away. On the check
bite it turned *gource* into *source* (5,850 chunks), *scip* into *skip*,
*ctags* into *tags*, *owid* into *owed* and *tracery* into *tracer*. A bite
about a tool the pile hasn't seen gets flooded by a common word one letter
away. `REPAIR_MIN` (4, the D12 baseline) only stops short words. A guard is
the operator's call; one shape it could take:

```python
# pile.py, rung 2 (sketch): don't trade a rare word for a flood
if fix_chunks > REPAIR_FLOOD * max(1, median_chunks(bite_words)):
    continue  # leave it a miss; a miss reaches the map, a flood doesn't
```

### 60. The miss row lost two words

The seed's answer listed 40 missed words; the `miss` row it wrote (row 32)
counts 38. The row keeps hashes only, so which two can't be read back. A
miss that isn't logged never reaches the misses map (3). Worth a test that
holds the answer's `missed` and the row's `count` to the same number.

```python
assert len(seed["missed"]) == miss_row["count"]
```

### 61. The Jeles rung was never on

Jeles was in the pile as a repo, but the seed's gap row said `unreachable:
Jeles isn't configured`. The CLI only turns the rung on when the `jeles`
package is importable in the run's interpreter (`jeles_store.default_store`),
so indexing Jeles (2) and Jeles as the escalation rung are two separate
settings, and the 40 misses were never asked of it.

```sh
$VENV/bin/pip install -e ../Jeles   # then check in again
```

### 62. A big corpus wins a code bite on volume

A bite that was mostly code-tool names ranked Jeles's
`core_weather-earth.json` first, and Jeles held the top of the rank. Same
story as the checkout bite (16): words alone favour whoever has the most
text. BM25 (18, 49), an embedder (16, 19) or a per-repo cap on the top-N
would each change it.

```python
# a cap (sketch): no repo takes more than half the top 10
top = cap_per_repo(rank, n=10, most=5)
```

## What connects all the worlds

A third pass (2026-10-21): the shared IDs, addresses, formats and protocols
that would let the pile, the outside snapshots (33–48) and the wider world
point at each other. The two headline joins were checked here: a file's
git blob is its Software Heritage ID (`git hash-object` on `words.py`
matched `sha1("blob N\0"+bytes)`), and a chunk's hash (`pile.read_pile`:
`sha256` over the chunk's text, no header) is already an IPFS CIDv1. The
rest is the research pass's, from primary sources where it could reach
them; what it couldn't confirm is said.

### IDs the pile already has

### 63. Every blob is already a Software Heritage ID

Software Heritage archives all public code, and its content ID (SWHID, now
ISO/IEC 18670) is git's blob hash. Every file the pile indexes already has
a world-wide name, and a chunk is the same name plus `;lines=a-b`. The
archive can be asked, hashes only, which of your blobs it holds.

```python
swhid = f"swh:1:cnt:{blob};origin={repo_url};path=/{path};lines={a}-{b}"
# optional, hashes only: POST [swhid, ...] to
# https://archive.softwareheritage.org/api/1/known/ -> {swhid: {"known": true}}
```

Watch: Software Heritage calls the git match "incidental and not
guaranteed" in future versions; SWHID v2 is still being worked out.

### 64. Every chunk is already an IPFS CID

A chunk's `sha256` wrapped in four bytes is a CIDv1 `raw` ID, the address
IPFS, IPLD and the AT Protocol use. Stdlib only, no network.

```python
cid = "b" + base32(
    bytes([1, 0x55, 0x12, 0x20]) + bytes.fromhex(chunk_sha256)
).lower().rstrip("=")
# sha256("hello world\n") -> bafkreifjjcie6lypi6ny7amxnfftagclbuxndqonfipmb64f2km2devei4
```

### 65. The same blob in supply-chain words: gitoid and in-toto

IANA's provisional `gitoid:` scheme and in-toto's `gitBlob` digest are the
same hex again. Free columns for SBOM and attestation tools.

```text
gitoid:blob:sha1:<blob>
{"digest": {"gitBlob": "<blob>", "sha256": "<chunk sha256>"}}
```

### 66. Keep a SHA-256 git blob too

Git's SHA-256 objects (and probably SWHID v2) hash `blob N\0` plus the
bytes, so a plain `sha256(chunk)` won't match them. Storing both now saves
a migration later.

```python
git256 = hashlib.sha256(b"blob %d\0" % len(data) + data).hexdigest()
```

### Hubs that join everything else

### 67. Wikidata as the crosswalk

Almost every outside source carries a Wikidata ID (QID): Wikipedia
articles, OpenStreetMap features, OpenAlex, the Met, Gutenberg, WordNet.
One `xref(qid, scheme, value, snapshot, fetched_at)` table, filled from a
dated extract, joins them all. The structured data is CC0.

```text
https://en.wikipedia.org/w/api.php?action=query&prop=pageprops&titles=Albert_Einstein&format=json
-> pageprops.wikibase_item = "Q937"
```

Watch: the query service blocks generic User-Agents and caps a query at
60 s. Most property numbers (GeoNames, OSM, the Met, Gutenberg) weren't
confirmed.

### 68. OpenAlex and DOIs: papers

OpenAlex (CC0) maps every scholarly work to its DOI, PubMed, ORCID, ROR and
Wikidata IDs in one `ids` object. When a Jeles answer cites a paper, store
its `doi` and `openalex_id`, and the citation joins the world's.

```sh
curl https://api.openalex.org/works/doi:10.7717/peerj.4375
curl -LH "Accept: application/vnd.citationstyles.csl+json" https://doi.org/10.5284/1015681
```

### 69. ORCID and ROR: people and organisations

ORCID (people) and ROR (institutions) both publish CC0 dumps. Mostly just
an ID string on a contributor, only with that person's consent. Low
priority unless Jeles grows a lot of named sources.

```text
0000-0002-7869-831X
```

### 70. GeoNames and OpenStreetMap: places

GeoNames (CC BY 4.0) gives every place an ID, coordinates and a country.
OSM features carry a `wikidata=` tag, so they join through 67. Store only
the IDs, never OSM geometry, to stay clear of its share-alike license.

```text
geonameid on each place-bearing snapshot row; osm_type/osm_id as an xref to a QID
```

### 71. Country and language codes

ISO 3166 country codes, BCP-47 language tags and Unicode CLDR (open, with
the 2-to-3-letter mappings) let OWID, USGS and Open-Meteo snapshots join by
country. OWID adds its own `OWID_*` codes, such as `OWID_WRL` for the
world.

```text
iso3166_a3 = "NGA"; lang = "en"   # CLDR as the bundled code source
```

Watch: SIL's ISO 639-3 tables restrict redistribution. Use CLDR.

### The record in the world's formats

### 72. W3C PROV: the record as provenance

PROV is the W3C standard for "this came from that, by this activity, under
this agent". The record already is that: chunks and snapshots are
entities, rank and serve are activities, the sealer is the agent. An
`export --prov` with SWHIDs and CIDs as the IRIs stays hash-only.

```turtle
<swh:1:cnt:…> a prov:Entity ; prov:wasGeneratedBy <run:…> .
```

### 73. RO-Crate: a snapshot as a research object

RO-Crate (Apache-2.0 spec) is plain JSON-LD that Zenodo and WorkflowHub
read. One `ro-crate-metadata.json` beside each dated snapshot: every file
with its `sha256`, its SWHID or CID, its SPDX license and where it came
from.

```json
{"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
 "about": {"@id": "./"}, "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"}}
```

### 74. Frictionless, DCAT and an SPDX license on every row

A `datapackage.json` (Frictionless) per tabular snapshot, a DCAT
`Distribution` with `spdx:checksum` and `previousVersion` (which chains
snapshots the way the record chains rows), and an SPDX license ID on every
snapshot row. The license column lets the script refuse to serve
share-alike text into something that can't carry it.

```json
{"path": "owid-population.csv", "hash": "sha256:<hex>", "license": "CC-BY-4.0"}
```

### 75. in-toto Statements: a seal others can check

A sealed decision exported as an in-toto Statement (the envelope SLSA
uses): `subject` lists the served chunks by `gitBlob` and `sha256`, and the
predicate holds the seal. Signing stays a separate concern; nothing here
handles a key.

```json
{"_type": "https://in-toto.io/Statement/v1",
 "subject": [{"name": "pile.py#L10-20", "digest": {"gitBlob": "…"}}],
 "predicateType": "<your uri>", "predicate": {}}
```

### 76. CloudEvents: a record row in a common envelope

CloudEvents' required fields map almost one-to-one onto a record row: `id`
is the row hash, `source` the pile, `type` the kind, `subject` the chunk's
SWHID. A cheap, standard way out for any row.

```json
{"specversion": "1.0", "id": "<row hash>", "source": "pile://willow",
 "type": "org.willow.pile.seal", "subject": "swh:1:cnt:…"}
```

### Pointing into text without storing it

### 77. Line and text fragments

RFC 5147 addresses lines in a plain-text URL (`#line=10,20`); browsers now
all support text fragments (`#:~:text=`). A chunk's locator becomes a link
anyone can click, and the record still holds no words.

```text
https://raw.githubusercontent.com/willow-memory/willow-bot/<sha>/one-script/onescript/pile.py#line=265,275
```

### 78. W3C Web Annotation: a chunk talking about the world

The standard shape for "this says something about that". A link between a
pile chunk and an outside snapshot becomes an annotation any client can
read. Use position selectors, not quote selectors, so no words go in.

```json
{"type": "Annotation", "body": "swh:1:cnt:…",
 "target": {"source": "<wayback url>", "selector": {"type": "TextPositionSelector", "start": 40, "end": 90}}}
```

### 79. Memento and the Wayback Machine: what a page said then

Each web snapshot gets the archive's closest copy beside its hash, so
anyone can check what the source looked like on the day it was fetched.

```text
GET https://archive.org/wayback/available?url=<url>&timestamp=20261021
-> archived_snapshots.closest.{url, timestamp}
```

Watch: an empty answer doesn't prove a page was never archived.

### Protocols

### 80. MCP: the pile as resources by hash

Serving chunks to a model is what MCP is for. An MCP server exposing each
chunk as a resource whose URI is its SWHID (or `pile://<sha256>`), plus a
`rank` tool that takes a bite. willow-mcp already imports the one script
(`reach.py`), so this may be a door to add there rather than a new server.

```json
{"method": "resources/read", "params": {"uri": "swh:1:cnt:…"}}
```

### 81. A feed of sealed decisions

JSON Feed (or Atom) with one item per sealed row, `id` = the row's hash.
The cheapest way for anything outside to follow what was decided, with no
server beyond a static file. ActivityPub and the AT Protocol can come
later; both need a running server.

```json
{"version": "https://jsonfeed.org/version/1.1", "items": [{"id": "<row hash>", "title": "sealed", "date_published": "2026-10-21T22:32:00Z"}]}
```

### Time and place

### 82. The drand round on every row

drand rounds are a clock everyone shares: round to time is arithmetic from
the chain's genesis and period. A `(chain_hash, round)` pair on each row
says "not before this" in the world's own terms. With OpenTimestamps (25)
for "not after", every row is pinned on both sides.

```python
t = genesis_time + period * (round - 1)  # from /{chain_hash}/info
```

### 83. Dates that admit doubt: EDTF and the tz database

UTC instants plus an IANA zone name, never local times. For fuzzy dates (a
Met object "about 1880", a forecast resolving "sometime in March") EDTF
adds `~` (approximate), `?` (uncertain) and `%` (both).

```text
1880~    2026-03?    2026-10-21T22:32:00Z America/Los_Angeles
```

### 84. Cells on the map: geohash and H3

Give every place-bearing row a cell ID, and an earthquake (34), a weather
reading (33) and today's place (44) join on cell plus time window. Geohash
is a few lines of stdlib; H3 (Apache-2.0) would be an optional extra.

```python
import h3

h3.latlng_to_cell(lat, lng, 5)  # a cell ID, a 15-digit hex string
```

Watch: avoid what3words; its terms make the words its property.

### 85. The snapshots' own IDs

Some planned sources already carry IDs that join:
- USGS event IDs (`us6000jllz`) are never reused, and each event lists every
  network's ID.
- The Met's objects link back to Wikidata (67).
- GH Archive's push events carry commit SHAs, which are git commit IDs, so
  they're SWHID `rev` IDs too (63).
- Manifold market IDs give your forecasts a crowd price to be scored
  against (36).

```text
https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us6000jllz&format=geojson
swh:1:rev:<commit sha from a GH Archive PushEvent>
```

## One from you

### 86. Compare the snippets in the boxes, and serve the comparison

The operator, 2026-10-21: *"Is there anything in the one script that
compares snippets, as in snippets in boxes, that can be served through the
system?"*

**What's built** (read in this tree, 2026-10-21). Each one compares only
part of what this idea asks for:

| Piece | What it compares | Served? |
|---|---|---|
| `serve.joins` / `serve.propose_chunks` | Which piles share a who/what/when/where value, and which served chunks come from the same file: "the mosaic" | On the card, for the human to judge before sealing. Not to the model |
| `pile.read_pile` (`copies`) | Exact copies: a chunk whose sha256 was already seen is counted and skipped | No. The count is dropped |
| `pile.merge` (`lookup`) | The same chunk across sessions' stores, merged by hash (`seen_in`, `sessions`) | `lookup` is a view; nothing is served |
| `diverge` | Two refs down to the smallest code unit (AST hash), and two record chains | No. It writes one `diverge` row |
| `scripts/scan/script_match.py` | Scripts against every earlier one: same name, renamed copy (same hash), variant (same syntax-tree shape) or similar (Jaccard over imports, defs and calls) | Into the gate: `gate.door` checks a proposed script against earlier ones. Not served to the model |
| `xref` (`dupes`) | IDs defined twice in one repo | Only in the xref report |
| willows-grove `templates/cross-table/cross_table.py` | Boxes on a grid, each filled by copying a passage. `measure --against` says which boxes changed since a snapshot; `link` writes a crosswalk between two grids | No. It lives in the grove, and the one script doesn't call it |

So exact sameness (same hash), "same shape" and "comes from the same place"
are covered, and the gate already compares scripts.
The cross table is the nearest thing to snippets in boxes, but it isn't
wired into serving.

**What isn't built:** comparing what two snippets *say*. Near-copies (one
word changed), two chunks that answer the same question differently,
or a Jeles answer against your own code. And nothing hands a comparison
to the model as a served table.

**A shape for it.** A new `compare` table kind beside chunk tables. For the
top N served chunks, code works out each pair's relation and serves it as
rows, so the model reads "these two say the same thing" or "these two
differ here". It never computes that itself.

```python
# serve.py (sketch): pairs of served chunks, related by code
for a, b in itertools.combinations(top, 2):
    rel = (
        "same"
        if a.sha256 == b.sha256
        else "near"
        if jaccard(shingles(a), shingles(b)) >= NEAR
        # difflib / MinHash
        else "close"
        if cosine(embed(a), embed(b)) >= CLOSE
        else None
    )  # rung 3, when on
    if rel:
        rows.append(
            {
                "what": "compare",
                "a": name(a),
                "b": name(b),
                "rel": rel,
                "diff": diff_hunks(a, b) if rel == "near" else None,
            }
        )
```

- Stdlib gets the first two relations: `difflib` for the diff, word
  shingles for "near".
- The embedder (16, 19) adds "close": same meaning, different words.
- The rows cite both chunks' ids, so a proposal that leans on a comparison
  goes through the same cite check as anything else.
- With 63–64 the pair is two world-wide IDs. Then "this chunk of yours is
  a near-copy of that file in Software Heritage" becomes a servable row too.
- `NEAR` and `CLOSE` are thresholds, so they're the operator's numbers,
  like every number in `deep_thought.py`.
- `script_match.jaccard` and `script_match.features` already exist; the
  first piece built on them is `snippet_cover` (below).

## The stupid pile

The last pile: things that shouldn't work, and the reason each one
probably does. Every one of them keeps the one script's rules. Nothing
merges, seals or decides on its own, the record keeps hashes, and **the
vault is Sean's key**. That's the joke: the plumbing is serious enough
that nonsense still comes out checkable.

### 87. Roll for merge

A d20 for every PR. Add the green runs, take away `sigh.py`'s red streak,
and the troll calls the DC. **Why it shouldn't work:** merges aren't
Dungeons & Dragons. **Why it does:** `dice5e.d20` takes the rng it's handed,
so a drand-seeded stream makes the roll replayable and provable after the
fact. It goes on the record like any verdict, and the human still seals.
A nat 1 is advisory. Mostly.

```python
rng = StreamRandom.from_seed(bytes.fromhex(randomness), label=f"pr-{n}".encode())
total, nat = dice5e.d20(rng, mod=green_runs - sigh.get(repo, n))
table.verdict(
    cfg,
    {"what": "roll", "session": f"merge roll {repo} {n}", "total": total, "nat": nat},
)  # `what` is one of table.WHATS
```

### 88. Grade the horoscope

`horoscope.reading` says *"your tests know something you don't"*. Log every
reading as a forecast, settle it against that day's CI, and give the stars a
Brier score. **Why it shouldn't work:** a horoscope makes no claim you can
check. **Why it does:** the market ledger doesn't care where a forecast
came from, and after a month `calibration.bins` tells you exactly how
psychic the bot is. (Probably as good as a coin. Possibly worse, which is
funnier.)

```python
api.forecast(
    cfg,
    f"horoscope-{day}",
    [{"path": "tests-red", "p": 0.5}, {"path": "tests-green", "p": 0.5}],
    by="oracle",
)
# a forecast is by a role (human, desk, oracle, coin), never "stars"
```

### 89. The sigh is a forecaster

`sigh.sigh_line` gets one more `h` for every red after the third. Read the
number of h's as a probability that the next push is red too, and score
it. **Why it shouldn't work:** it's a joke string. **Why it does:** a
streak length is a perfectly good feature, and `sighhhh` with five h's is
a confident forecast. Now it can be held to it.

```python
p_red = min(0.95, 0.5 + 0.1 * (len(sigh.sigh_line(streak) or "sig") - 3))
```

### 90. Ask Jeles whether today is the day for a rebase

Send the horoscope's line to the pile as a bite. Jeles holds no verified
fact on the subject, so the seed writes a `miss` and the gap goes on the
queue that only a human's answer closes. **Why it shouldn't work:** it's
asking a fact-checked corpus about astrology. **Why it does:** the
escalation path doesn't judge bites. You'll be asked, formally and with a
hash, whether today is the day for a rebase. (`horoscope.py` says it isn't.)

```sh
python -m onescript seed "today is not the day for a rebase"   # exits 42
```

### 91. Deep Thought already answered 42

`api.ESCALATE = 42`: since commit 907c2ac, the one script exits 42 when it
hands something to the human ("not enough information to say a human said
this was true"). `quips.py` already carries `ΔΣ=42`. Run `deep_thought.py`
and every real question it finds should end in 42. **Why it shouldn't
work:** it's a Douglas Adams joke. **Why it does:** it's already built.
The Answer was always "ask the human".

```sh
python -m onescript escalate "the question"; echo $?   # 42 when no rung answers
```

### 92. Earthquakes as dice

Use the week's USGS magnitudes as a random source, and put them through
the same tests the stream has to pass. **Why it shouldn't work:** the
earth is not a fair die (big quakes are rare; that's the whole Gutenberg-
Richter law). **Why it does:** it fails, loudly and measurably.
`pools.measure` puts the outliers in the outer layer, and the earth gets a
grade next to drand's. A lesson in why the beacon exists.

```python
digits = [int(str(q["mag"]).replace(".", "")[-1]) for q in quakes]
print(pools.hist(digits))  # spoiler: not flat
```

### 93. Lunatic mode

Skyfield's moon phase sets the typo rung's floor: at full moon `REPAIR_MIN`
drops to 3 and the pile repairs everything; at new moon it's off.
**Why it shouldn't work:** the moon has nothing to do with spelling. **Why
it does:** `REPAIR_MIN` is a number the operator owns, the phase is
deterministic for a date, and the seed row records which floor ran, so
every lunatic answer can be replayed. Read 59 first: at full moon *gource*
is definitely *source*.

```python
phase = almanac.moon_phase(eph, ts.utc(y, m, d)).degrees
REPAIR_MIN = 3 if abs(phase - 180) < 20 else None if phase < 20 else 4
```

### 94. Rainy-day scope

A weather snapshot (33) becomes a W value, so `serve.choose` can pick
"only the piles from days it rained in your town". **Why it shouldn't
work:** weather isn't a property of your code. **Why it does:**
`serve.choose` matches any W a group carries, and `serve.joins` will
dutifully tell the human which piles share a drizzle before they seal.

```python
serve.propose(stack, weather="rain")  # the card lists the joins: all wet
```

### 95. The spurious-correlation machine

Join everything on time and place (82, 84): CI reds, earthquakes,
rainfall, Hacker News front pages, Wikipedia pageviews. Then let
serendipity mode (7) hunt for correlations. **Why it shouldn't work:**
almost all of them are noise. **Why it does:** that's the point. Every
"finding" is logged as a forecast that has to come true *next* month, and
the calibration curve shows how many links a big enough pile invents. A
very honest machine for being wrong.

```python
api.forecast(
    cfg,
    "ci-red-when-it-rains-nov",
    [{"path": "holds", "p": 0.5}, {"path": "noise", "p": 0.5}],
)
```

### 96. Your decisions as a Gource movie

Feed `record.jsonl` to Gource as a custom log: each session is a "user",
each row kind is a "file" that gets added, so seals, misses and serves
grow like a code tree. **Why it shouldn't work:** Gource visualises source
code, and the record is a ledger. **Why it does:** Gource's custom log is
just `timestamp|user|A|path`, and every row has a time, a who and a kind.

```python
for r in rows:
    print(f"{epoch(r['ts'])}|{r.get('who', 'run')}|A|{r['kind']}/{r['n']}")
```

### 97. The pile writes poetry

The pile already finds adjacent word pairs that belong together (NPMI over
`PAIR_MIN`). Feed the strongest pairs into a Tracery grammar (57) and the
pile writes verse in its own dialect: *sealed scope, served table, the
human's no.* **Why it shouldn't work:** it's a search index. **Why it
does:** a pair list is a phrase book, and Tracery only needs phrases. The
drand stream picks every line, so each poem is provable.

```python
grammar = {"origin": ["#pair#, #pair#. #pair#."], "pair": strongest_pairs(store, 50)}
```

### 98. Rebase shame tarot

Three cards from `rebase_shame.py`'s force-push history: past (the oldest
force-push still on record), present (today's count) and future (the
stream's draw). The runes already give a reading per SHA, so the cards
come from the same SHAs. **Why it shouldn't work:** tarot. **Why it
does:** every card is a hash, so the spread is the same every time you ask.
The cards can't be shuffled until you get a nice one.

```python
past, present, future = (runes.cast(sha) for sha in (oldest, latest, drawn))
```

### 99. Chess for merge conflicts

`diverge` finds the units both branches changed. Score each conflict like
a chess position, and pull the matching Lichess puzzle (46) by rating:
one conflict is a 1200 puzzle, five tangled ones are a 2400. Solve the
puzzle before you resolve the merge. **Why it shouldn't work:** nothing
about Python is chess. **Why it does:** both have a difficulty you can
measure. A conflict's count of `both_different` units and level-3 edges
is a rating.

```python
rating = 1000 + 200 * len(div["both_different"]) + 100 * len(div["edges"])
```

### 100. Ask the Met what the session looked like

At check-out, hash the session bundle, use it to pick an artwork from the
Met's public-domain collection (45), and file the painting beside the
sealed session. **Why it shouldn't work:** a JSON bundle has no look.
**Why it does:** a hash picks one object out of hundreds of thousands, the
same one every time. Your worst session gets a still life of a dead
pheasant forever, and you can prove it.

```python
art_id = ids[int(session_sha256[:12], 16) % len(ids)]
```

### 101. The tide checks the clock

`clock.py` and the record's timestamps are trusted to be right. NOAA's
tide predictions (38) are almost never wrong. Check one against the other:
if the record says high tide was at 3 a.m. and NOAA says 9, someone's clock
is off. **Why it shouldn't work:** tides aren't a time service. **Why it
does:** the moon keeps better time than a laptop that slept through a
DST change. An absurdly expensive NTP.

```python
assert abs(recorded_high - noaa_high) < timedelta(minutes=10)  # else: whose clock?
```

### 102. The market bets on the bot

Cross-world wagers: "willow-bot cuts a release before SPY closes up on
Friday." `market.reveal` settles the SPY half, the release tags settle
the bot half, and `calibration` scores how well you understand both at
once. **Why it shouldn't work:** the stock market doesn't know willow-bot
exists. **Why it does:** a wager is a wager. The ledger only needs two
settled facts and a date, and it will never let you drop the losers.

```python
api.forecast(
    cfg,
    "release-before-spy-up-1023",
    [{"path": "yes", "p": 0.2}, {"path": "no", "p": 0.8}],
    market={"symbol": "SPY", "kind": "up", "close_date": "2026-10-23"},
)
```

## How much is already here

The operator, 2026-10-21: *"I want to see how much of the other can really
be just compiled from what's already here, rather than hand built."*

`scripts/scan/snippet_cover.py` (the first piece of 86) answers that. It
reads every Python snippet in this file and every `.py` file committed in
willow-bot, willows-grove and Jeles, and sorts each name a snippet calls:

- **here:** exists, and the snippet says where (`dice5e.d20`).
- **loose:** a bare name that something here happens to define, such as
  `get`. It counts as missing until the snippet says which one.
- **stdlib:** the standard library or a builtin.
- **outside:** a package to install.
- **missing:** exists nowhere.

It reuses `script_match`'s `features` and `jaccard` to name the nearest
existing function. Read only, and the same files give the same bytes.

```sh
python3 scripts/scan/snippet_cover.py IDEAS.md willow-bot=.. grove=../../willows-grove jeles=../../Jeles
```

First run, 2026-10-21. 55 of the 102 ideas carry a Python snippet; the
rest are shell, JSON or prose, so they aren't counted.

| Verdict | Count | Ideas |
|---|---|---|
| compiled: only existing code plus glue | 13 | 4 5 6 8 15 87 88 89 92 94 95 98 102 |
| stdlib only: nothing to build but the glue | 14 | 3 9 12 18 35 44 60 63 66 82 93 99 100 101 |
| needs outside: one pip extra | 10 | 19 20 22 26 27 39 43 49 55 84 |
| needs building | 18 | 1 7 11 33 34 36 37 38 40 42 45 47 59 62 64 86 96 97 |

**27 of 55 (about half) need nothing new**, only wiring. Another 10 need one
package and nothing else.

**The 18 that needed building mostly needed the same few things:**
- **One snapshot reader.** `get` was missing from 8 of them: 33, 34, 36, 37,
  38, 42, 45 and 47, all the world data. **Built** (`onescript/snapshot.py`,
  2026-10-21): fetched once into the box under its SHA-256, a `snapshot`
  row of hashes, offline after that, `unreachable` with no `--fetch`.
- **A name away.** 96 calls `epoch`, which `beacon.epoch` already is. 64
  calls `base32`, which is `base64.b32encode`.
- **Small helpers, one each:** `pile_files` (1), `chunks_of` and `spec_for`
  (7), `bundle_as_round` (11), `store_count` (40), `median_chunks` (59),
  `cap_per_repo` (62), `strongest_pairs` (97).
- **86 itself:** `shingles`, `diff_hunks`, `cosine` and `embed`. `jaccard`
  is already there, in `script_match`.

**After the snapshot reader** (same day, sketches pointed at
`snapshot.get`, rerun):

| Verdict | Count | Ideas |
|---|---|---|
| compiled | 19 | 4 5 6 8 15 33 34 37 38 42 45 87 88 89 92 94 95 98 102 |
| stdlib only | 14 | 3 9 12 18 35 44 60 63 66 82 93 99 100 101 |
| needs outside | 10 | 19 20 22 26 27 39 43 49 55 84 |
| needs building | 12 | 1 7 11 36 40 47 59 62 64 86 96 97 |

Six of the eight world-data ideas now compile. 36 still needs `crowd` (the
market's probability as a two-path forecast) and 47 needs `answer` (a value
out of an OWID CSV): one small helper each.

**Compiled** (same day): `compiled/ideas.py` builds the 19 as one function
each, made only of the calls above and glue, and
`onescript/tests/test_compiled.py` runs every one against a real box with
offline stand-ins for drand, prices and the fetcher. Running them found
what matching names can't:

| Idea | What running it showed | Now |
|---|---|---|
| 4 | `table.verdict` only takes a `what` from a fixed list (`open, roll, step, decision, result, agm_head, escalate, deck, market`); a CI streak is none of them | **Blocked.** The streaks read; writing them as rows needs a new `what`, which is the operator's call |
| 6 | `StreamRandom` has `choice` and `randint`, not `randrange` | Uses `choice` |
| 87 | `merge_roll` isn't an allowed `what`, but `roll` is; and `dice5e` imports the `dice` package (the `table` extra) | A `roll` verdict; `unreachable` without `dice` |
| 88 | A forecast is by a role (human, desk, oracle, coin), never `stars` | By `oracle` |
| 102 | A market call can only say what SPY does; the bot's half has no field | The SPY call alone |

So of the 19: 18 run, one (4) waits on a decision, and three sketches had
to change to fit contracts the code already enforces. The `BLOCKED` table
in `compiled/ideas.py` carries the same notes.

So most of the list compiles from what's here: 33 of 55 now need nothing
new, and 10 more need one package.

**The loose chunks, built** (2026-10-22). The test the operator set: *"use
the new pile to fill in the gaps. This is kinda the test to see how easily
chunks can be added now."* Each missing helper was asked of a fresh box in
plain words (`lookup`) before a line was written:

| Helper | Asked the pile | Top 4 held the piece? | Built on |
|---|---|---|---|
| `epoch` (96) | "seconds since the epoch from a timestamp" | yes, #2 | `beacon.epoch` |
| `base32` (64) | "base32 encode bytes lowercase" | no (it's stdlib) | `base64.b32encode` |
| `crowd` (36) | "forecast distribution yes no probability" | partly: test usage | `api.forecast`'s two paths |
| `answer` (47) | "parse a csv row by date and read a column value" | yes, #1 | `market.parse_daily_csv`'s shape, `csv` |
| `pile_files` (1) | "every indexed file read as text at its blob" | yes, #1 | `seed.Texts` |
| `chunks_of` (7) | "the chunks the store holds for one repo" | partly | `seed.Texts` + `pile.chunks` |
| `spec_for` (7) | "the spec that names the stack, handed back to serve" | yes, #2 | `api.scope` (and it showed serving is blocked) |
| `bundle_as_round` (11) | "the session bundle actions and their states" | yes, #2 | `session.actions`, `pools.measure` |
| `store_count` (40) | "how many chunks hold a word" | yes, #1 | `Cached.df` |
| `median_chunks` (59) | "repair an unseen word one edit away" | no: README and tests above the code | the seed's `keys` and `repair` |
| `cap_per_repo` (62) | "rank the chunks and fuse by rrf" | yes, #2 | the rank's own rows |
| `strongest_pairs` (97) | "adjacent word pairs kept by npmi" | yes, #3 | the seed's `pairs` |
| `shingles` (86) | "jaccard over features of a script" | yes, #2 | `script_match.jaccard`, `seed.WORD` |
| `diff_hunks` (86) | "unified diff hunks keyed by content hash" | yes, #1 | `difflib` (diverge's rule, in miniature) |
| `embed` (86) | "wordllama embedder rank fused with the words" | partly | not built: `close` needs the embedder on |

11 of 15 straight from a plain question. All 13 helpers landed in
`compiled/ideas.py` beside the 19, with tests (32 in
`onescript/tests/test_compiled.py`). 59 and 62 are pure functions over a
seed's answer and a rank, their thresholds placeholders: wiring either into
the pile's ranking is the operator's call. 7's pick runs, but serving it
needs a new table source, so it's in `BLOCKED` beside 4 and 102. Idea 60
needed no code: the seed's `missed` checks a word as written and the miss
row every form `rank` looks for, so they can differ.

So: the gaps a sketch left were found by asking, mostly in one question,
and filled in a few lines each, on top of what was there.

## What fell out of the second run

2026-10-21, after the snapshot reader and the compile. A fresh box over
willow-bot, willows-grove and Jeles (1,373 files, today's commits in it),
the same 80-word bite as the first run, the morning screen, `needs`, a
cross-box `lookup`, `diverge` against `main`, and `deep_thought.py`.

### 103. Writing an idea down hides its gap

All 40 once-missed words are found now, and the five bad typo repairs (59)
are gone. But nothing was fixed: 37 of the 40 are found *only* in this
file. The misses map (3) goes blind to anything written down as an idea,
and the rank's top hit for the bite (score 354, next best 97) is this
file's own report listing the missing words. Only `algolia` and `tarot`
reached real code (`compiled/ideas.py`); `bm25s` sits only in a test's
example.

A shape for the fix: a hit that lands only in a proposal file isn't
knowledge. Mark this file (and anything like it) as `provenance: proposal`
in the index, and count a word as known only when it lands somewhere else
too.

```python
known = [
    k for k in seed["keys"] if any(not a[1].endswith("IDEAS.md") for a in k["anchors"])
]
```

### 104. NEEDS YOU doesn't list a session waiting on your seal

Check-in says *"sessions: 0 sealed, 1 waiting on your seal"*; the morning
screen's NEEDS YOU says *"nothing"*, and `needs` returns 0. `view.needs`
builds its lines from turns, proposals, offers, claims and acts, but not
from sessions. Here neither session could even be proposed (no Nestor
store), so two sessions sit unsealed and nothing on the screen says so.

```python
# view.needs (sketch)
needs += [
    f"session {s['subject']} · waiting on your seal{s.get('why', '')}"
    for s in report.get("sessions_waiting", [])
]
```

### 105. Deep Thought measures the world before D2

`deep_thought.py` reports 8 `[failing] … nothing in the bot`. Two reasons,
both stale:
- Its default root is `~/github/willow-memory`. Pointed at the real repos
  (`ONESCRIPT_ROOT`), 5 of the 8 find a file.
- It counts `one-script/onescript/` as "skeleton" (elsewhere) and only
  `willow_bot/deterministic/` as "the bot". Since D2 moved the one script
  into willow-bot, the skeleton *is* in the bot: `predict.py`, `view.py`
  and `reverse.py` are all there.

So its "Move one in, merge them, or write it new in the bot?" questions are
already answered by the move. What counts as "in the bot" is the operator's
to say; the measure just needs to be told.

### 106. A box with its own keys can't be looked into

`lookup --other <box>` said the other box was `unreachable`: *"no store key
in the operator's keys dir"*. The cross-session lookup reads the other
store's key from the operator's keys dir, so a box made with its own
`--keys` file can't be merged in. Said, not hidden; worth knowing before
relying on cross-box lookups.

### 107. This branch merges clean

`diverge origin/main HEAD`: 9 files and 203 units changed on this branch
only, none on `main`, 0 divergence points. Nothing to reconcile.

## What fell out of the third run

2026-10-22, after 103, 104 and the loose chunks. A fresh box at `a9c9b76`,
a seed, `needs` on a box with two checked-out sessions, the new helpers run
on the real pile (not fixtures), `snippet_cover`, `deep_thought.py` pointed
at the real repos, and `diverge` against `main`.

Working, live:
- **104:** both unsealed sessions are in NEEDS YOU now.
- **11:** those two sessions as rounds: both end `waiting`, 6 and 9 steps.
- **7:** a real pair from a drand-style draw: a Jeles climate answer beside
  a willow-bot steward test. *What joins these?*
- **40:** the pile's own shares: *the* sits in 55% of chunks, *box* 7.7%,
  *seal* 4.2%, *bite* 1.1%. The world's half needs `wordfreq`.
- **96:** a box's record as 27 Gource lines.
- **The branch** still merges clean: 18 files and 279 units on this side,
  nothing on `main`, 0 divergence points.

### 108. A helper that passed its tests found 10 of 68

`said` (1) passed its fixture and found 10 operator quotes on the real
pile. The fixture was shaped like the sketch: a dated quote. The pile holds
at least six shapes, and most carry no date (`The operator: "…"` alone 79
times, `**The operator:** "…"`, `the operator's word: "…"`, `*"…"*`).
Widened the same day: 68 quotes, 13 dated, one said in 13 places. The
lesson for every compiled helper: run it on the pile, not only on a
fixture.

### 109. The cover goes stale once the ideas are built

`snippet_cover` still says 12 ideas need building, though every one is in
`compiled/ideas.py` now: it reads the sketches here, and they still call
the names that were missing. It measures the list, not the code. A shape
for it: read `compiled/ideas.py`'s `COMPILED` table and mark those ideas
`built`, or point each sketch at the function that now exists.

### 110. Six purposes have two homes

Pointed at the real repos (`ONESCRIPT_ROOT`), Deep Thought's DISAGREEMENTS
go from 0 to 14:
- boot, record, gate, resolve and run each live in both
  `willow_bot/deterministic/` and `one-script/onescript/`. D2 says one
  home: which survives?
- Seven `willow_bot/deterministic/` files can't be called on their own (no
  `__main__`, no script entry), which D2 asks for.
- willows-grove's `incoming/haiku-2026-10-02/PROPOSALS-SUMMARY.md:167` still
  says D1 is open; D1 was sealed (`02050acf`).
- **Chain dialects** (found 2026-10-22 crossing willow-data-vault, 116).
  The hash chain has three homes in the fleet, and no verifier can check
  another's chain:

  | | First `prev` | What is hashed |
  |---|---|---|
  | Nestor's `nestor/ledger.py` | the string `genesis` | the previous line's raw bytes, carried as the next row's `prev`; a row has no hash of its own |
  | willow-data-vault's receipts ("pattern-ported" from Nestor) | the string `genesis` | each row's own `hash`, over `json.dumps` with default separators (`", "`, `": "`) and a fixed key order |
  | the one script's `record.py`; `kernel.py` | 64 zeros | each row's own `hash`, over sorted, compact JSON (`canon` family A) |

  The vault's port kept Nestor's sentinel and changed what is hashed, so even
  the two that look alike don't agree. Either one dialect, or each verifier
  names the others and says it can't check them, so "this chain doesn't
  verify" never reads as tampering.

All four are the operator's calls.

### 111. Every commit asks old boxes to re-check their writes

A box checked in before today's commits lists four `re-check row N (write)
· made by run version …, now …` lines. Reverse does what it should: the
code that made those rows changed. Worth knowing: every commit puts a line
on every open box's screen until it's re-checked.

### 112. "What's blocked" doesn't find the blocked list

A bite about what is blocked or waiting on the operator's call ranks old
governance and design docs first; `compiled/ideas.py`'s `BLOCKED` (4, 7,
102) isn't in the top 8. The list of calls waiting on the operator lives in
a Python dict, where NEEDS YOU can't see it. A shape: each `BLOCKED` entry
becomes a NEEDS YOU line, since that's what it is.

## The hidden egg

### 113. Every demo is one loop, and the kernel is it

2026-10-22. The operator: *"put all that data in one box, and see what comes
out the other end. Think of it as merging the demos, to find the hidden
egg."* Every demo file from nine repos (Nestor 48, the Grove 29,
safe-app-store 9, corpus-lens 15, Forge 3, and four more; 109 files, 4,201
chunks) went into one box, read with the box's own tools:

- **Little text is shared:** two real near-copies (Nestor's `shoebox.py`
  and the Grove's `shoebox-stream` HTML, 0.43; one `demo.sh` byte for
  byte in two repos).
- **One shape is shared.** Of 24 Python demos, 12 make a temp box, work,
  and remove it; 11 import `nestor.memory`. The calls that do the work:
  `add_pair` (7 demos), `SqliteStore` (7), `lookup` (6), `best_sealed`
  (6), `set_ledger_path` (6). Something is said, kept, looked up, served
  only when a human sealed it, and every step chained.
- **Nestor's `demo/sixty_seconds.py`** tells that loop in eight beats; the
  Forge's `demo/the_first_bite.py` says "the box has never been
  demonstrated as one thing".

The eight beats are the one script's loop, and genesis's record: 42 when
nothing is verified, a seal, 0 once a human said it, a near miss refused, a
forged seal refused, the chain verified, an edit seen. So the smallest
script for anyone is that loop on their own folder.

**Built: `one-script/kernel.py`.** One stdlib file, Python 3.9+:
`init`, `bite`, `propose`, `seal`, `verify`, `out`. Its pile is the person's
own folder, its key theirs, nothing of the operator's. Its rows, seal and
bite key are the one script's byte for byte (`test_kernel.py` holds it), so
`Record.verify_chain` checks a kernel box. `kernel.py test` runs the eight
beats. Run on Nestor's demos as a stranger's pile, a first bite about the
shoebox found the shoebox story first.

Run on the whole merged demo box as a newcomer's pile: "how do I bake
sourdough bread" was served Nestor tables, held on *how*, *do* and *I*.
Fixed: `FUNCTION` words never count toward holding a bite, so bread is 42,
"what does this do" is 42 (say more), and "who is big jim" goes straight to
`big_jim.py`. Open: `HOLD` (0.5) and `FUNCTION` (English) are placeholders,
the operator's to set.

### 114. What the demos' characters needed

2026-10-22. The operator: *"Keep pushing on what that characters would need.
There's more than big jim in that pile."* Each character in the merged demo
box got a kernel box with only their own story as the pile, and asked the
questions their demo asks (the string arguments of every `lookup` and `ask`
call), plus a few their story implies. The eight writing styles in
corpus-lens's `styles-corpus` asked their openers against the whole box.

| Who | Asked | What they needed | Now |
|---|---|---|---|
| Big Jim | "that one ending 672841" | a serial matched by its ending (the VIN `1HGEJ6672841` is one token) | **Built**: `holds`, a word with a digit matches a token that starts or ends with it |
| Nieves | "mi terremoto", "my little earthquake" | her story is a web page; she was served `<button>` bars | **Built**: `html_text`, pages read as the text they show. "my little earthquake" now finds the ruling `seal("mi terremoto", "my earthquake")` |
| June | "what is june growing" | the same: her farm is a page | Built (same fix); her page is mostly interface, so there is little farm in it |
| The Dispatches author | "has the essays folder been audited for contradictions?" | the demo's trap: "a question nobody audited must NOT be answered", and the kernel served the question back | **Built**: `echo`, a chunk holding the bite's own words in order is served marked and answers nothing; only echoes is 42 |
| Attercliffe Medical | the pump CH-4471, three ways | the near miss to refuse | Holds: its only chunk is the fixture's copy of the near miss, an echo, so 42 |
| The eight styles | their openers | every opener was "found" in `build.py`, the script that wrote them | **Built**: `ECHO_RUN`, 8 words in a row in the bite's order make an echo; all eight are 42 now (the questioner still gets two real chunks) |
| Rosalind | "a tool for my scooter rally photographs" | served her story; what she wants is something built, which is the Forge's tier, not the kernel's | Open: a 42 that names the next tier |
| A Law Gazelle client | "what is my next court deadline" | 42, rightly: the deadlines are generated into SQLite at run time, never text | Open, the operator's call: whether the kernel reads generated data |

Open, the operator's call: whether a hit in a newcomer's *own unsorted
files* should be 0 at all. F-43 makes the operator's pile a yes because it
is things already said yes to; a newcomer's folder isn't that yet. The
echo rule keeps the worst case (a question served as its own answer) at
42; the rest is a ruling.

### 115. Crossing the streams: what already lives in the box

2026-10-22. The operator: *"lets cross the streams. lets see how much of
what else they need already lives in the box"*, then *"record it and keep
crossing"*. One box over both worlds: the one script's pile (willow-bot,
willows-grove, Jeles) and the fleet the demos came from (Nestor, Forge,
willow-mcp, ratatosk, safe-app-store's code, corpus-lens and seven more; 17
repos, the store built in 30 s; no vault repo). Each open need asked in
plain words; idea files left out of the answers (103).

**The egg's other half: Rosalind is real.** The Forge's first-bite demo
character wants "a tool for my scooter rally photographs". safe-app-store's
`nasa-archive` is the North America Scootering Archive: scoot.net's rally
galleries from 1997 to 2013, kept before they disappear, with an EXIF reader
and a downloader. The demo's fiction asks for what the fleet already built.

**What the characters need (114), crossed:**

| Who | Need | Lives in the box |
|---|---|---|
| Rosalind | something built | `nasa-archive`; and `api.forge_bite` hands a bite to the Forge |
| Rosalind | photographs | `nasa-archive/downloader`: `extract_exif`, the rally date, the photographer |
| Law Gazelle | deadlines from today | `law-gazelle/case_store.urgent_queue()` |
| Law Gazelle | reading SQLite | corpus-lens `ingest/sqlite.py` |
| Nieves | a ruling across two languages | Nestor itself: translation-memory pairs, `source_lang`/`target_lang`, one seal per pair |
| Attercliffe | CH-4471 and CH4471 as one | Nestor's matcher `normalize` (`source_norm`) |
| Attercliffe | the near miss, refused for good | Nestor's `suppressed_by_rejection` |
| everyone | a seal served past louder drafts | Nestor's prefilter: "a seal ranked past the top five is still served" |
| everyone | a second desk | Nestor `demo/desks.py`, `test_two_desks.py` |
| everyone | a keyring per verifier | Nestor's verifiers keyring (the one script's `nestor_seal` reads it) |
| everyone | word order not changing a bite | Nestor `bench/token_matchers.py` (sorted token set): a benchmark, not production |
| June | the farm's own data | not found: only pages |
| the newcomer | their folder isn't a yes yet | the Forge's design ("first run is onboarding: scripted questions"); not built |

10 of 13 live. The kernel is Nestor's loop at its smallest, and Nestor is
the kernel grown: what a newcomer outgrows in the kernel is mostly already
in Nestor.

**What the one script itself needs, crossed:**

| Gap | Lives in the box |
|---|---|
| 86 "close": same meaning, other words | Nestor `semantic_matcher.py`: cosine on embeddings, an optional extra |
| handoff C: an over-cap prompt | ratatosk `onescript.py`: `served_budget`, "too large to serve; narrow the stack" |
| O-6: how 42 is stamped on documents | design: willows-grove `incoming/haiku-2026-10-02/proposal-packet-stamp-chain.md` |
| the anchor: a cut can't be seen | design: willows-grove box-build `SPEC-rival.md` |
| handoff E: record compaction | not found (Nestor's locked `ledger_append` keeps the chain, doesn't compact it) |
| 62 per-repo cap | Nestor `corpus.py`: two rows per repository on an unscoped search, lifted when scoped to one; decision 0220 makes it "a guarantee, not an ordering" and documents `limit` as a ceiling |
| 59 flood guard, handoff G | not found (Nestor's triage thresholds keep a report from flooding with false positives; not a repair guard) |

Second pass, the misses in other words: 62 was there all along, under
"one repo does not crowd the rest". Nestor wrote the rule `cap_per_repo`
sketches, and the reason to keep it: a short result is the guarantee
working, so say so where the caller reads, don't fill it. Compaction, the
flood guard and June's farm records stayed missing (a food-systems seed in
Jeles is text, not a farm's own rows).

**Name-matching doesn't cross; meaning does.** `snippet_cover` with all
17 repos as "here" changed no verdict: the sketches are written in the one
script's own words. Running it found a bug: a variable named like some
repo's module (`db.execute`, `rng.choice`, `report.get` beside a `db.py`,
`rng.py`, `report.py`) read as a missing function. Fixed: such a call on a
standard value type's method is a value (`VALUE_METHODS`).

**A shape for the kernel's hand-offs.** The kernel stays small; when a
newcomer outgrows a beat, its 42 says which part of the fleet does it, the
way `forge_bite` hands a build to the Forge:

| A 42 because | Names |
|---|---|
| they want something made | the Forge (`forge_bite`) |
| two words should be one thing, or a near miss must stay refused | Nestor (`normalize`, `suppressed_by_rejection`) |
| the words differ but the meaning is the same | Nestor's `semantic_matcher` |
| a second person should review | Nestor's desks |
| the answer is data, not text | the app that holds it (corpus-lens's SQLite reader, law-gazelle's queue) |
| their prompt is over the cap | Rat |

### 116. One repo at a time

2026-10-22. The operator: *"Start bringing in other repos that have not been
included. One at a time."* Each one joins the crossed box (115), the same open
needs are asked again, and the row says what the new repo answered that the
box couldn't before. Vault repos stay out.

| Repo | Size | What it answered | What it brings to the one script |
|---|---|---|---|
| agriculture-almanac | 34 files; 24 the template's, unchanged | none of the open needs; "is the source still there", "a catalog of public datasets" | Each catalog entry names the canonical URL and an SPDX license (FAOSTAT: CC-BY-4.0), which is exactly what `snapshot take` asks for; and an `observed` block (checked, reachable, status) that is the three states for a URL. June's farm data isn't here, but the road to it is: catalog says where, snapshot keeps it. |
| homestead-ledger | 102 files | none of the open needs asked in the box's words; its own words answer older ones | The same split as the one script: the household's books are canonical and read-only, the overlay is where the app writes (the pile and the box). "What's due" and cadence roll-forward are Law Gazelle's deadlines from today, built for money. Its no-egress guard (I-17, an AST scan for network imports and `eval`) now guards `kernel.py` too: the kernel promised a newcomer's folder never leaves the machine, and nothing checked it. |
| homestead-law | 88 files | June's two hits are noise (a franchise-tax date, a custody move date) | Law Gazelle is a real module, not only the demo's character (114 found her needs in safe-app-store's `law-gazelle`). Its deadlines are the one script's loop in court dates: `rules.compute()` stores nothing and answers the same twice (a proposal); `accept()` is the only write, and only of what a `preview_token`, a hash of exactly the fields the person saw, proves was shown (a seal over the proposal's hash). "Jurisdiction absent → refuse" (I-42) is a 42 with a reason. |
| homestead-health | 69 files | none | Its reference lane is the snapshot reader, arrived at separately: a pinned, versioned public corpus served by an injected reader, "the bytes are here, frozen, and no link is resolved at runtime" (`snapshot read` offline, `Config.fetch` injected). Its wall is the one script's too: the reader takes a question, never a person, so public text can't be joined to someone's record. And its audit lesson, "do not keep two validators for the same rule", is 110's two homes said as a rule. |
| awesome-sovereign-software | 11 files | none | A five-point test for software you own, and `kernel.py` passes it: no account, no server (now tested), no subscription, survives the vendor (stdlib, one file), and the data reads without the app: the record is JSONL and a sealed answer lands as a plain file under `written/`. The list's other rule fits too: "every entry documents its exit plan". The kernel's exit is that its box is a folder; a newcomer can say so in their own README. |
| the other eleven almanacs (climate, health, civic, economy, environment, and six with one entry each; almanac-data) | 26 to 48 files each; 58 catalog entries in all | June's weather: "daily rainfall and temperature at a station" finds NOAA's GHCN-Daily and NCEI's US climate normals; "drought this week" the US Drought Monitor. "Frost dates" finds nothing, though the climate normals hold them: the catalog never says "frost" (86 again). "Crop production by county" finds nothing either: agriculture-almanac has no USDA NASS entry yet, a good first issue for it | A bug in this PR's own snapshot reader: it promised an SPDX license and checked only the shape, so 50 of the 58 entries' licenses passed (`public-domain`, `Open`, `non-commercial`, `Copernicus`). Now an SPDX id carries its version or is one of the few that don't, and anything else is SPDX's own `LicenseRef-<words>`; 8 of the 58 take as they are, and a US agency's data goes in as `LicenseRef-public-domain`. |
| terpsi-music | 312 files; design plus one library | none of the open needs | It has already done this crossing, by hand. Its `ARCHITECTURE.md` §14 maps the fleet's components ("Fleet components this maps onto") onto a music program's needs, and each row that claims something exists names the commit someone read it at ("VERIFIED 2026-08-01 at `c872d4c`"), with a test that fails an unpinned claim. Re-checked against the box's clones 12 weeks on: every file and symbol it names in a repo the box holds is still there (65 of 65 references, in 42 verified rows). The other 7 are in repos the box doesn't hold yet (safe-app-common-package, SAFE, willow-config, willow-2.0), and the vault row was left unread. Its rule is the one the crossing tables above need: a "lives in the box" with no commit is a claim, not a check. |

**The shallow read.** The operator: *"Do a shallow read on the rest, to see if
they possibly bring in something new."* README and tree only, nothing built:

| Repo | Something new? |
|---|---|
| DispatchesFromReality (in the box) | The outside view: Case Study 4 is the one script told to an employer (three random streams, two chains, three seal paths made one). Lesson 03, "The Consent Ledger", is the record explained to a ninth grader: newcomer material for 114's onboarding gap |
| willow-seed (private) | **Yes.** `seed.py` (244 lines) is an earlier kernel: check Python, find or clone Willow, hand off. Its README's session consent ("May I access your journal today?", gone when the app closes) is a first-bite question the kernel doesn't ask |
| quick-stupids (private) | **Yes.** `the-box/` is a made-up legacy intranet: wrong READMEs kept wrong on purpose, `DO_NOT_DELETE_2.txt`, seven `EXCAVATION` notes. A stranger's messy folder, ready made, for the kernel's first bite |
| SAFE (private) | `governance/HARD_STOPS.md` (the file terpsi's §14 couldn't reach), `SESSION_CONSENT.md`, `DUAL_COMMIT.md`; older than the one script, same ideas |
| safe-app-common-package | `no_egress.py`, the fleet's canonical copy of the scan the kernel's test now carries; the test could import it instead, if the kernel may have a dependency (it may not) |
| openclaw-sap-gate | SAP/1.0, fail-closed authorization for MCP tool calls; nothing the one script lacks |
| quiet-corner | `session_scope`: a per-field sensitivity vocabulary (terpsi: "worth taking, enforced nowhere") |
| safe-design | One token set, three renderers held to parity by a test; nothing for the one script |
| the org `.github` repos (8) | Boilerplate plus each face's one-paragraph profile: six faces, one sentence each, the map a newcomer would want |
| rudi193-cmd profile and site | The fleet's one-line pitch; the site copies the profile |
| rudi193-cmd/Forge | An older copy of forge-play/Forge (116 files differ); no |
| willow-tech-manual | A Mintlify book of the July platform; docs only |
| tui-scaffold, aionic-claude-skills | A TUI starter; a skill anthology of metaphors. No |
| willow-memory/Willow | Empty: no commits |
| willow-compose (private) | Left unread past its README: "a self-portrait", a database dump of the operator and the collaboration (123 MB). Treat as vault: never in a box |
| willow-config, yggdrasil-training-data | Not opened: config with token files, and training data; the operator's call |
| glapagos (castroquiles) | Not the operator's; for the contributed-to pass |

**The forks and the contributed-to** (the operator: *"a lot is going to be
duplicate, because i gave and took a lot from a lot of them"*). Exact
duplicates, measured as paragraphs hashed the kernel's way: almost none
beyond licence text and codes of conduct, because giving and taking moved
ideas, not text. The real traffic:

| Repo | Gave, took, or new |
|---|---|
| community (Emerging-Rule) | **Gave**: 949 paragraphs shared with DispatchesFromReality, the AI-literacy and calibration lessons; nine of the operator's commits, merged by the maintainer who also owns glapagos |
| redential-cli | **Gave**: the MCP taxonomy slug, a detection fix, RFC #13 drafts (5 of the last 50 commits) |
| hermes-agent | **Gave**: a security audit and a fix. Its 2,000 files of skills crowd every lookup they touch: 62's per-repo cap, wanted in the one script's own lookup |
| codebase-memory-mcp | **Took**, and the answer to 117's egg 5: "dead code detection: functions with zero callers". The tool the kernel's `unused` pointer means. Left out of the box (1.3 GB) |
| mex, basic-memory | Took: persistent memory for coding agents, the same idea from outside |
| sigmap | Took: "deterministic, verifiable grounding", byte-stable output, no model |
| DigUp | Took: search your files by what's in them, the kernel's own first bite |
| socratic, AHS-cortex, gov-transparency-mcp | A self-questioning skill; agent orchestration; US government data, the almanacs' ground |
| CSL, Multi-News, NaturalConv, sentence-similarity | Datasets; nothing shared. sentence-similarity is 86's oldest root |
| cpython | History only; none of the last 50 commits the operator's |

**willow-2.0 (archived, public).** terpsi-music's §14 claims about it hold:
the bitemporal migration, `TRUST.md`, `SECURITY_AUDIT.md` and
`core/inference_router.py` are where it says. And it closes two old gaps:
handoff G and the O-6 stamp are `willow/fylgja/boot_digest.py` ("latest
handoff + claim verification ... every action-driving line carries a
verification stamp; nothing is copied forward without one"), and
willow-seed's session consent is built in `willow/fylgja/safety/session.py`
(streams authorized per session, `"expires": "session"`, the consent record
written and the authorizations dropped at close). rudi193-cmd/Willow could
not be attached here: a session can't hold two repos named `willow`.

**willow-data-vault (the template).** Checked first: 20 files, schema,
bootstrap and docs, no data and nothing key-shaped; "the blueprint ... never
data". Then boxed, with the operator's word. It holds the kernel's rules
from the other side: its `vault.key` is 0600, made locally and never in the
repo (the kernel's key lives outside the box, 0600); its receipts verifier
refuses a broken chain and tells "never chained" (exit 2) from "tampered"
(exit 1), absence as a recorded value; and its covenant is the one script's
own: "a clean chain says the rows were not altered after being written, not
that anything in them was approved". What it adds is a fact about the
fleet: there are chain dialects. The vault's receipts start from the
string `genesis` and hash `json.dumps` with default separators in a fixed
key order; Nestor's ledger, which they were ported from, keeps `genesis` but
hashes the previous line's raw bytes; the one script and the kernel start
from 64 zeros and hash sorted, compact JSON (family A). No verifier can
check another's chain, and nothing says so yet (now on 110's list).

### 117. The kernel on the-box: what plopped out

2026-10-22. The operator: *"run the kernel on the-box, and see what golden
eggs plop out"*. quick-stupids' `the-box/` is a made-up legacy intranet, a
stranger's folder at its worst. The kernel got the exhibit only: the
curator's note, the excavation logs and `tests/THE_VERDICT.txt` (an earlier
dig's answer) were left out, so nothing in the pile had been explained.
Twelve questions a new hire would ask.

**Egg 1, a defect: the kernel read only my kind of file.** It took a list of
suffixes (`.md .txt .py .json` ...), the fleet's shape, so 5 of the 12
questions came back 42 while their answers sat in `TODO`, `login.php`,
`dates.pl`, `deploy.bat`, `error_log`, `data.mdb.ldb`, and a
`TEST_PROCEDURE.doc` that is plain text. That is the first bite filled with
mine, the thing the kernel exists to avoid. Now a file is text by what it
holds (UTF-8, no NUL byte in its first 8 KB), whatever its name; `Thumbs.db`
and `login.swf` stay out because they're binary. After the fix, 11 of 12
answered.

**Egg 2: the contradiction is served, not settled.** "Which folder is the
real intranet" serves `README.txt` (final_v2, wrong on purpose) and the svn
migration notes (final_v2_REAL_USE_THIS_ONE, "unless it is a northeast
region issue") side by side. The kernel doesn't pick; the human seals the
one that's true, and the other stays a chunk.

**Egg 3: the database's answer came under another question.** "Where is the
database" is a 42: no file says "database". "Who is dave" answers it: the
lock file `data.mdb.ldb` says `DAVE-DESKTOP Admin`, so the 1.4 GB database
is open on Dave's desktop. Same meaning, other words (86): the fleet's
`semantic_matcher` is the hand-off (115).

**Egg 4: what DO_NOT_DELETE.txt does is split across three files.** It is
empty. `TODO` says "NEVER DO THAT AGAIN" (Dave, 1 April 2006),
`DO_NOT_DELETE_2.txt` says "testing if the other one is really load
bearing (it was)", and `error_log` shows machines on 10.0.0.x requesting it
from final_v2 every few weeks ("WHO IS REQUESTING THIS"). Something outside
polls it. The kernel served the pieces; the join is a proposal for a human
to seal.

**Egg 5, a limit: it can't see what's missing.** "Does the login check the
password" serves `check_login()` and the hard-coded `admin`/`password`. The
true answer, that nothing calls `check_login()` so anyone walks into
`payments.php` (the earlier dig's verdict), is about an absence. Text
retrieval answers "populated"; whether the code is used needs a code tool
(`script_match`, a call graph). A newcomer asking "is this used?" should be
told where to look, not shown the definition as if it settled it.

**Then the kernel was taught where to look** (the operator: *"teach the
kernel's 42 where to look"*). A bite's output gains `look`, printed for the
person and never written to the record: the bite's words no file in their
folder has ("no file in your folder has database: it may be called something
else there"); each function a served chunk defines and whether anything else
in the folder names it ("check_login is defined at login.php:12; nothing else
in your folder names it", which is egg 5's verdict, found by counting); a
bite of common words only ("name the thing you mean"); and a request to
make something. Facts about their folder come first; only then, under
`next`, the kind of tool that does the rest, with the fleet's name for it
(115's hand-off table), so a newcomer can ignore it.

### 118. Asymmetric offsets: a puzzle from the Discord group

2026-10-22. A problem from the operator's Discord group, no names: find every
`(b - k) b^i = (c - m) c^j` with `b > k`, `c > m`, `b < c`, `i, j >= 1`; the
group found one solution each for `(k, m) = (3, 2)`, `3 * 6^9 = 16 * 18^5 =
30,233,088`, and `(8, 5)`, `12 * 20^5 = 75 * 80^3 = 38,400,000`, and said "only
one*". Each solution is one number written as a single digit and zeros in two
bases (`3000000000` in base 6, `(16)00000` in base 18; the message's `75000₈`
is base 80).

**What the box said:** nothing in the fleet does this; the lookups found
noise. It is new, so it was built: `compiled/offsets.py`, stdlib.

**What came out:**

- Both claims hold as far as anyone looked: one solution each for every
  `b <= 100,000` with the value below 10^40, and for every `b <= 600` with
  the value below 10^400. That is the honest form of "only one*": the claim
  with its bound attached, which a search can give and can't go past.
- The asterisk is earned. Offsets `(9, 2)` have a small solution `(12, 3, 18,
  2)` and then a 40-digit one, `(54, 22, 162, 17)`; a search stopping at 10^39
  would have said "only one" there too.
- Why they are rare: for a prime p dividing c but not m, p doesn't divide
  `c - m`, so p's exponent in the value is exactly `j * v_p(c)`, and the same
  on b's side. So b and c share their primes, each offset is built from
  them, and every prime gives one linear equation in i and j, two unknowns.
  In both solutions every prime lines up: `2^9 3^10` and `2^12 3^1 5^5`.
- Across all offsets 1 to 10 (b <= 1000, value below 10^40), 14 pairs have
  exactly one solution, 7 have none (`(6, 1)` to `(10, 1)`, `(6, 7)`, `(10, 3)`), and some have
  infinite families from shared powers (`(1, 2)`: `2^(2j+1) = 2 * 4^j`).

### 119. Nothing checks for personal data before serving

2026-10-22. The operator: *"see if the system pre scans for allowed pii
before serving ... there is a pile of pii checks out there"*.

**What the one script does now.** Every served chunk passes `chunk._scrub`,
which applies `scrub.py`'s `hash64` and `credentials` sets: a full-width
hash and the credential shapes (the union of eleven copies, F12). That is
secrets, not people. No set looks for an SSN, a card, a birth date, a phone,
an email or a home path; nothing in `onescript/` knows a sensitivity rung
or a consent; and `kernel.py` serves a newcomer's folder with no scrub at
all (fair while it is their folder read by them; not once a model reads it).

**What the fleet already has** (read in the crossed box):

| Where | What it checks | Its rule worth keeping |
|---|---|---|
| homestead `keep/advise.py` | SSN (L5); card, bank number, birth date (L4); phone, email, EIN (L3), each mapped to a rung | A date counts as a birth date only next to `DOB:` and a number as a bank number only next to `routing`/`account`: flag every date and the signal drowns |
| homestead `keep/patterns.py` (and homestead-law's copy) | Extraction patterns | I-18: any pattern that could match PII is anchored and shipped with the PII it must not match (`1420 Maple 87501`, the address that got through) |
| homestead `keep` serve/export | A rung per field; L5 never crosses; unclassified reads as L5 | "Allowed" is a ceiling per surface, not a yes/no: fail closed |
| terpsi-music `records/classify.py` | A field's rung from a table of decided cases | It refuses to guess: a field the table doesn't decide needs a human |
| corpus-lens `egress_shapes.py` | A home path (the owner's username), an email, a calendar date, a weekday | A structural scan for values never on a literal list: "a value that was never quarantined cannot be in the literal list" |
| willow-2.0 `core/pii_detect.py`, `scripts/pii_check.py`, nest `pipeline/scrub.py` | SSN, card, email, phone (phase 1); phases 2-3 planned | "This module detects; callers act"; nest's scrub flags and never edits the file |
| willow-mcp `model_egress.py`, nest | The "local PII zone": no document body leaves for an off-box model without consent | Consent is checked where the bytes would leave, not where they are read |
| hermes-agent `agent/redact.py` | Secrets, plus phone numbers and Discord mentions in logs | |
| subject-consent (UTETY, corpus-lens, safe-app-store) | Consent per subject | |

**A shape, for the operator's call.** A `pii` named set in `scrub.py`, the
union of these the way `credentials` is the union of eleven secret
scanners, under the same rule (it may never catch less than any copy did,
each copy's positives kept as fixtures), plus homestead's two that make it
usable: each kind carries its rung, and the context-only kinds (birth date,
bank number) match only beside their label. "Allowed" is then a ceiling
passed by the caller: a chunk served to the person's own screen may show up
to L4, one bound for a model up to L2, and anything over the ceiling is
withheld with its kind named (`[withheld: ssn, L5]`), never silently. The
kernel takes the same set when its `look` and `served` text go anywhere but
its owner's terminal.

**Built** (the operator: *"build the pii set"*). `scrub.py` gains the `pii`
set and `withhold(text, ceiling)`; each kind's rung is in `RUNGS`. Every
copy's positives are its fixtures in `test_scrub.py` (homestead, willow-2.0's
detector and nest scrub, corpus-lens, hermes) and homestead's negatives must
stay untouched (the addresses, citations, hearing dates, ZIP+4, "into
account"). Two choices, both from the copies' own reasons: a bare nine-digit
SSN counts only beside its label (`ssn: 123456789`), and a bare 13-19 digit
run is a card only when its Luhn check holds. Serve now applies it:
`chunk._scrub` withholds anything over `SERVE_CEILING = 2` (a placeholder,
the operator's number), so a chunk reaches the model with
`[withheld: ssn, L5]` or `[withheld: email, L3]` in place of the value,
and a case number still readable. The full suite held (1865 passed).
`kernel.py` doesn't take it yet: it is one stdlib file that imports nothing,
so it would carry its own copy, a second home (110).

**Run on the-box** (the operator: *"run the kernel on the-box again with
the pii set"*). The twelve new-hire bites' served chunks, each passed
through serve's own scrub (`chunk._scrub`): nothing withheld, and the
whole pile scanned with every set agrees. That is right for what the set
knows and shows what it doesn't:

- **What's personal in the-box isn't a shape.** First names and initials
  (Dave, Gary, Karen, Steve, "rm", "th"), a machine named `DAVE-DESKTOP`,
  and 10.0.0.x client addresses in `error_log`. No check in the fleet
  detects a name or an IP address; willow-2.0 only plans a model for names
  (its phase 3).
- **Serve lets both planted passwords through.** Serve scrubs hashes and
  credential *shapes*, which are API keys. `login.php`'s `$password =
  "password"` is caught only by `journal_kv` and `generic`, which serve
  doesn't run, and `deploy.bat`'s `user: cwcftp pw: widgets1998` is caught by
  no set at all: no rule knows `pw`. A password a person wrote reaches the
  model.
- **Why serve can't just add `generic`:** its base64 rule takes
  `/var/www/final_v2_REAL_USE_THIS_ONE/payments` for base64, more than 300
  times in `error_log`. A scrubber that cries wolf on every path gets
  turned off.

**Then the gaps, closed** (the operator: *"add the password rule and IPs,
and any other PII set that isn't already covered at least at baseline"*):

- `typed_password`, a new set serve runs: a label (`password`, `passwd`,
  `passphrase`, `pwd`, `pw`, `$password`) then `:` or `=` then a value; the
  label stays and the value goes. It leaves a comparison (`$p == $password`),
  a label with no value, a source (`env:`, `None`) and a placeholder alone;
  `pass` alone is too common to be a label.
- `pii` gains the baseline kinds of the usual detector catalogues that no
  copy had: IP (v4 and v6, never loopback or unspecified, and a v6 address
  needs three groups so `a::b` stays C++), MAC, ITIN (L5, before the SSN
  shape), IBAN (only when mod 97 holds), passport, driver's licence and
  medical record number (each only beside its label), a crypto wallet
  (bech32 or `0x` and 40 hex, never a bare git hash) and a latitude and
  longitude pair. Each with its rung; each with positives and negatives.
- the-box again: both planted passwords go (`pw: [REDACTED:password]`,
  `$password = [REDACTED:password];`), every client address in `error_log`
  is withheld at serve's ceiling, and nothing else in the folder is touched.

**The cross-check with the demos' names** (the operator: *"We have lists of
people's names in the demos. Maybe it's time to do a cross check again."*):

- The demos' cast (the Aguirres, the Acorns, Fern Nutkin, Willow Burr,
  James Beauregard, Rosalind Ffoulkes, June Akiyama, Pastor Linda, Big Jim),
  looked for across every repo crossed so far: it stays where it was made.
  Each name lives in its own repo's demos, docs and tests; June Akiyama and
  Pastor Linda also sit in Nestor's and the Grove's data bundles. None
  reaches homestead, the almanacs or the forks.
- The real find was not the cast. Nestor's `docs/agent-log.md` and its
  copy in `demo/ideas-store/nestor.bundle.json` (a public repo) carry a
  git-author table with 16 email addresses, several of them other people
  who share the operator's first name, marked "somebody else". The pii set
  withholds 15 of the 16 at serve's ceiling (the sixteenth is a local
  `user@host` with no domain); the names beside them pass. Those two files
  are the operator's to clean.
- So names get a door: `withhold(..., names=[...])` takes a list the person
  gives (their household, their clients) and withholds each whole, in any
  case, longest first, as `name` (L3). It is willow-2.0's `pii_check`
  configured-names idea without its literals: the list is the caller's and
  never kept in the module. The cast is its test fixture.

**Then wired into serve** (the operator: *"wire the names list into
serve"*): `Config.names`, read by `opening.read_names` from a file outside
the box and readable only by its owner (a list of the people who matter is
their data, so it is kept like the human key), set by `--names FILE` or
`$ONESCRIPT_NAMES`; a file inside the box or open to others refuses the run
rather than serving without it. Serve withholds the names last, after every
other scrub, then checks the cap again before the file is written; the
prompt hook does the same to its chunks. The record keeps places and
hashes, never the names or the text they were withheld from.

Still not covered by any pattern: a person's name nobody listed, and a
street address. Both need a model or a list of the person's own names,
which is willow-2.0's planned phase 3, and the pii set's limit is said here
rather than implied.

Found while building: willow-2.0's `scripts/pii_check.py` (a public,
archived repo) is written as one person's literal details rather than
shapes. It is left out of the set, and is the operator's to look at.

### 120. The OAuth handoff, and the box picking its own tests

2026-10-22. The operator brought a packaged handoff from another cloud
session: `oauth_probe` (deterministic OAuth as forecast wagers and pins) and
`oauth_resource` (every code and token stamped with the server it was
issued for), plus wiring diffs for willows-grove and willow-mcp. The hole it
closes: Grove accepted an access token minted for another server.

- **Landed here:** the three willow-bot commits, applied as they were
  (`git am`), onto this branch; lint clean; with the `mcp` SDK installed the
  binding and probe tests are 30 of 30, as the handoff said. Without it the
  18 binding tests skip, and the one-script CI job doesn't install it, so in
  CI they skip: a test that doesn't run in CI is not a test yet.
- **Not landed:** the Grove and willow-mcp wiring. Both diffs still apply to
  today's heads, but Grove commits need a persona and the operator's
  ratifying words, and both wait on G1 (refuse pre-binding tokens now, or
  honour them for a window).
- **Verified live anyway**, in throwaway clones on throwaway homes: both
  servers wired, the probe run five times. 7 wagers and 19 of 20 pins hold;
  the one that moves is `g.audience.foreign_resource`, `accepted` to
  `http_401`, the fix and nothing else.

**The box picks the tests** (the operator: *"the one script could do a
similar job as the tests are doing, as long as the piles of tests are in
the box"*). Asked with the identifiers the diffs touch
(`GroveOAuthProvider`, `validate_token_resource`, `exchange_refresh_token`,
`_build_serve_provider`, ...), the crossed box names the OAuth tests in both
servers: seven Grove files (74 tests, about a second) and four willow-mcp
files (56 tests, about two), against full suites of minutes. It also showed
what no suite can: none of those tests runs the wired lines, because both
servers decide serve mode at import and their tests keep it off. The box
found the right tests and, by their silence, the gap the handoff names
("a Grove-side test that serve mode's provider is bound"), true of
willow-mcp as well. Only the live probe reaches the wiring.

A shape: a `tests-for` verb, a diff in, the box's test files out, ranked,
so a change runs its own tests first and the full suite after, and a change
whose identifiers no test names says so.

### 121. The seed line: what the small script was before it was small

2026-10-22. The operator, on pulling the small script out: *"the seed.py
and other related metaphor. It's willow, it's baked in."* Every repo the
box can reach, read for the planting sense of seed (the first run that
everything grows from), not the data sense (`seed_kb`, `seed_policy`,
`seed_demo`, which load starter rows into a store):

| Generation | Where | What it does |
|---|---|---|
| the first seed | willow-seed `seed.py` | "Plant this. Everything grows from here." Checks Python, finds or clones Willow, hands off. Its README's session consent ("May I access your journal today?") |
| the replant | willow-seed `REPLANT.md` | Marked historical: superseded 2026-07-22 by the charter's seed |
| SEED9 | willow-2.0 `seed.py` (and `seed-windows.py`, `willow-launcher.py`) | "One file to rule them all": first run installs, holds the onboarding conversation, makes the card, opens the dashboard; a return run is splash, auth, dashboard |
| SEED10 | the charter, `Willow/seed/` (`seed.py`, the handoff letter, the canon, ONBOARDING.md) | Not reachable from this session (rudi193-cmd/Willow can't be attached beside willow-memory/willow) |
| the canon | willows-grove `governance/seed/canon/` (six movements) | Moved here 2026-08-29, byte-identical to the charter's; `grove/seed_reader.py` walks them at first boot (D16: "the six-part canon *is* the human onboarding") |
| the onboarding | willows-grove `docs/TESTER_ONBOARDING.md` | Clone, venv, Postgres: a setup page, not a seed |
| the reference | willow-bot `claude/genesis`: `genesis.py` | "The smallest one script": run checks, append a row, verify. Unmerged |
| the kernel | willow-bot `one-script/kernel.py` (PR #160) | The newcomer's loop on their own folder |
| the bite | willow-bot `onescript/seed.py` | Not a planting: "2 predict, the seed": the first bite's words scanned against the box, once per check-in |

What the line shows: the small script is the seed again. Each generation
was "the one file a person runs first", and the canon moved to the Grove
while `seed.py` itself did not; SEED10 sits only in the charter. genesis
and the kernel rebuilt the seed's job under new names, and `onescript/seed.py`
reuses the word for a different job (the first bite). Three names for one
role, one word for two roles.

**How much of the seed the one script already holds** (the operator: *"use
the one script again, a few more times with different sets of questions"*).
Willow-seed joined the box; 42 questions in eight sets (the seed's steps,
onboarding, consent, the first bite, the return run, the seed's own words,
the one script's start-up in its own words, a stranger's first ten
minutes). The first answer came from the wider fleet 28 times, the old seed
line 9, the one script 5; asked in its own words, the one script answers
for its own start-up (check-in, the morning screen, check-out). Lined up
job by job:

| The seed's job | In the one script or the kernel? | Where else |
|---|---|---|
| the person's own key, outside the box | yes: kernel `init`, the human key | |
| their folder is not a yes yet | yes: kernel | |
| refuse a second planting | yes: kernel `init` ("this box already has a record") | |
| the first bite | yes: kernel `bite`; `onescript/seed.py` | the Forge's first bite |
| the return run | yes: check-in, NEEDS YOU, check-out | SEED9's splash, auth, dashboard |
| check the tools | partly: the toolchain gate checks the bot's venv, not a newcomer's | willow-seed, SEED9 |
| choose where to plant | partly: kernel `init --box --pile` | willow-seed `--target` |
| the onboarding conversation | no | SEED9; the Forge's design |
| walk the canon's six movements | no | the Grove's `seed_reader` |
| make the person's card | no | SEED9 |
| ask consent for the session | no | willow-seed's README; willow-2.0's fylgja `session.py` |
| the handoff letter | no | willow-2.0 `fylgja/handoff_write.py`; the charter |
| a first screen that isn't empty | no: an empty pile is a 42 | Nestor's demo `seed.py` |
| the first five minutes, written down | no | willow-2.0 `docs/FIRST_5_MINUTES.md` |
| a database, models | not its job: the record is a file, the embedder optional | SEED9 |

Seven of the fourteen jobs that are its to do are held, five of them whole.
What it holds is the mechanics (key, refusal, bite, return); what it lacks
is the person-facing half: the conversation, the canon, the card, the
consent, the letter, the full first screen. Those all exist, in the seed
line and willow-2.0, just not in the one script.

Asked this way, hermes-agent's skill pages took the first answer for six
questions about installing and data: 62's per-repo cap again.

**Pulled in** (the operator: *"pull the missing seed pieces into the one
script"*): `onescript/plant.py`, the person-facing half in the one script's
own terms, with its rules in a leaf, `plant_rule.py`, so the prompt hook reads
them without loading the rest.

- `plant`, once per box: the canon's six movements from where the Grove reads
  them (`$WILLOW_HOME/seed/canon`, then `governance/seed/canon`), handed back to
  read, each one's hash on a `plant` row and never its words. Read live from
  the Grove: "0 · The Covenant" through "5 · The World". No canon is
  `unreachable`, said, and nothing is planted: where the Grove serves a stub,
  the one script says so.
- `consent`, once per check-in (willow-seed's "May I access your journal
  today?", willow-2.0's fylgja session record): the repos this session may
  read, training off unless given, expiring at check-out. On a planted box
  serve refuses, and the prompt hook serves nothing, from a repo the session
  didn't consent to; with no consent, from none. The hook reads it from the
  record's sealed tip (`has_kind`, `since_last`), never a whole parse. NEEDS
  YOU says when a session hasn't consented. An unplanted box serves as before.
- `letter`, the handoff letter (willow-2.0's `handoff_write`, the charter's
  letter): `letters/<day><a-z>.md` in the box, always lettered so the newest
  sorts last, written through the record; NEEDS YOU names the latest.
- Each gate was cut and its test failed: serve's consent check, the hook's
  filter.

Left where they are, on purpose: SEED9's dashboard card and Nestor's seeded
demo store (their own surfaces). Of the fourteen jobs, the one script now
holds ten, plus the first-run docs as a README section ("Planting").

### 122. The private chunks, scanned first, and SEED10 found

2026-10-22. The operator: *"since the pii is now in, we can bring those
private chunks in, scan first, and see what they add."* Eight private repos
(quick-stupids, SAFE, willow-seed, safe-design, tui-scaffold,
aionic-claude-skills, willow-config, yggdrasil-training-data), every tracked
file scanned with the credential, typed-password, secret-path and pii sets,
kinds and counts only, no value printed or kept. A file with any credential
shape or any L4-L5 kind stayed out of the box, as did anything over 5 MB;
L3 and below and bare secret-path mentions went in (serve withholds L3 at
its ceiling). 409 files boxed, 33 held back. willow-compose stayed out
whole.

- What was held back, by kind: the-box's two planted passwords; SAFE's
  opauth and a few `labelled_secret_kv` pages; aionic-claude-skills' hooks
  (`password_kv`); willow-config's sandbox and MCP project files and SEED10's
  `seed.py` (a password field); and yggdrasil's training files, the serious
  ones: Postgres connection strings with passwords in the preference pairs,
  card-number and birth-date shapes in the persona corpus. `secret_path`
  fires on nearly every willow-config file because it names where secrets
  live (`WILLOW_HOME`, a vault path); it flags a pointer, not a secret.
- What they add (20 questions, a private repo answered first 4 times):
  SAFE's `HARD_STOPS` and its session consent ("expires when the app
  closes"), aionic-claude-skills' dual-commit skill, tui-scaffold's layout,
  quick-stupids' prior-art survey.
- **SEED10 was in willow-config all along**: `seed/seed.py` (b17 SEED10,
  "Plant this, everything grows from here"), the ratified design
  `seed/ONBOARDING.md` (the six movements are the onboarding, each one a
  canon chapter, a SEED9 page, and the install chain running underneath),
  the canon (byte-identical to the Grove's), and the coda,
  `seed/handoff/seed.py`, "the first honest handoff, read at first boot", a
  letter to the next instance. 121's "SEED10 unreachable" is answered.

`plant` against SEED10's design: the canon walk and fail-closed consent are
there; the coda (showing the seed's own first letter at planting) and
movement 3 (the person says who they are, held only with consent) are not;
movement 4 (Gerald) stays the vault's, and movement 5's features and the
install chain are not the one script's job.

**Then both brought in** (the operator: *"keep going with what we were
on"*). Planting now shows the coda, the seed's own first letter, read from
the first seed dir that holds one (`$WILLOW_HOME/seed`, `$ONESCRIPT_SEED`, the
Grove's copy, then `willow-config/seed` beside the bot), its hash on the
`plant` row; live, that is willow-config's `seed/handoff/seed.py`, while the
canon still comes from the Grove. And `--who` is movement 3: the person's
words kept in the box's `person.md` only with `--keep-who`, otherwise read
once and not kept, the row saying which, never the words. Gerald showed up
in the box along the way, in the Grove's design notes and the store's
gerald-bot; his story stays in the vault.

## What the one script said about this list

2026-10-21. One check-in over willow-bot, Jeles and willows-grove (1,366
files; the index was built before this file existed, so the list can't find
itself). One `seed` bite holding every idea's key words.

**40 words the pile holds nowhere: real gaps.**
algolia, bm25s, ctags, datasette, duckdb, ephemeris, flashrank, fts5,
gharchive, gource, hexagram, inkle, kiwix, levenshtein, libzim, lichess,
manifold, model2vec, mutmut, opentimestamps, owid, pagefind, pageviews,
pymerkle, rapidfuzz, rfc3161, ripgrep, scc, scip, serendipity, skyfield,
tantivy, tarot, tatoeba, theseus, tracery, vec, wordfreq, wordnet, zim.
They were written as one `miss` row of keyed hashes (row 32), so they will
show up in the misses map (3) once it's built. That row counts 38, not 40:
two of the words the seed called missed weren't logged as misses. The row
holds hashes only, so which two can't be read back; worth a look, since a
miss that isn't logged never reaches the map.

**Words that hit, but the idea isn't there:**
- *earthquake*: only the shoebox demo's "mi terremoto". Nothing about USGS.
- *tides*, *planets*, *usgs*, *noaa*, *museum*, *wikimedia*, *meteo*,
  *reranker*, *bm25*: Jeles corpus facts *about* these, not code that uses
  them.
- *serendipity*: only Jeles prose.
- *mutation*: never mutation testing.

**Prior art: the idea is already started.**
- *Hypothesis* (27): installed in the box-build venv
  (`seat/willow/experiments/box-build-2026-10-17/BRIEF.md`) and imported
  nowhere. The cheapest gap to close.
- *Serendipity* (7): `docs/design/one-box/random-pull-2026-10-06.md` already
  did "pull some randomness from your corpus", from the model's training,
  every box `unattested`. Idea 7 is the same game made checkable: drand picks
  the pull and the chunks come from the pile.
- *Merkle* (56): `hashing-session-handoff-2026-10-05.md` already weighs
  "hash chain or Merkle tree" for tamper-evident logs, and the grove's
  one-script README lists Merkle beside the record chain.
- *Zipf* (40): `next-pile.md` already fits the pile's own words (slope
  −0.907, R² 0.993). The wordfreq dialect report is that fit against the
  world's.
- *Horoscope* (6, 15): willow-bot's `docs/IDEAS.md` 35 (voice preview) and
  71 (a daily note with the day's horoscope) are the delivery for it.
- *drand*, *Brier*, *calibration*: 100+ chunks each. The ledger, beacon and
  scoring ideas (5, 33–38, 47) stand on built ground.

**Gaps in the one script itself** are items 59–62 above.
