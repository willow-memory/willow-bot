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
        yield {"kind": "said", "date": date, "where": f"{repo}:{path}",
               "quote_hash": sha256(quote)}  # the record keeps the hash
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
        for k in r["missing"]:       # BLAKE2b-128, never a word
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
    table.verdict(cfg, {"what": "ci_red_streak", "repo": repo,
                        "pr": pr, "value": sigh.get(repo, pr)})
```

## New scripts to add

### 5. Bet on your own CI

`market.py` keeps forecasts nobody can edit or drop. Point the same ledger at
your PRs instead of SPY, then score the forecasts with `calibration.py`. Who
forecasts better, you or the model?

```python
api.forecast(cfg, "pr-160-green-first-push",
             [{"path": "yes", "p": 0.7}, {"path": "no", "p": 0.3}], by="human")
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
print(rng.seed_id, TEMPLATES[rng.randrange(len(TEMPLATES))])
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
db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, chunk_id UNINDEXED)")
hits = db.execute("SELECT chunk_id, bm25(fts) FROM fts WHERE fts MATCH ? "
                  "ORDER BY bm25(fts) LIMIT 100", (query,)).fetchall()
```

Watch: the store already holds hashed postings; an FTS table holds the words,
so it lives with the chunks, never in the record.

### 19. Model2Vec: a second static embedder

Same idea as WordLlama (static vectors, no GPU, milliseconds), from
MinishLab. Two embedders give a cheap second opinion: where they disagree on
the top chunk is worth a look.

```python
from model2vec import StaticModel
m = StaticModel.from_pretrained("/models/potion-base-8M")   # a local dir
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
process.extract(word, vocab, scorer=distance.Levenshtein.distance,
                score_cutoff=2, limit=5)
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

The rules for outside data:
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
f = get("https://api.open-meteo.com/v1/forecast", latitude=lat, longitude=lon,
        daily="precipitation_sum", timezone="auto")
api.forecast(cfg, f"rain-{day}", [{"path": "yes", "p": 0.3}, {"path": "no", "p": 0.7}])
# settle: archive-api.open-meteo.com/v1/archive?...&start_date=day&end_date=day
```

#### 34. USGS: The earthquake base-rate game

"Will there be an M6+ somewhere this week?" The USGS feeds settle it, and
its catalogue gives the base rate, so you find out whether you beat the
base rate or just repeat it.

```python
n = get("https://earthquake.usgs.gov/fdsnws/event/1/count",
        starttime=monday, endtime=sunday, minmagnitude=6)
```

#### 35. Wikipedia pageviews: Which article wins tomorrow

Draw two Wikipedia articles from the stream and guess which gets more views
tomorrow. Twelve of them make a `deck.py` round. A good way to find out how
little you know about what the world reads.

```python
url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
       f"en.wikipedia/all-access/user/{title}/daily/{day}/{day}")
```

Watch: Wikimedia asks for a User-Agent that names you and how to reach you.

#### 36. Manifold: You against the crowd

Copy a public prediction-market question into your own ledger with your own
probability, write the crowd's probability beside it, and when it resolves,
score both. Over a season you learn where you beat the crowd.

```python
m = get(f"https://api.manifold.markets/v0/market/{market_id}")
api.forecast(cfg, f"manifold-{market_id}", mine, by="human")
api.forecast(cfg, f"manifold-{market_id}-crowd", crowd(m["probability"]), by="crowd")
```

#### 37. Hacker News: Will it hit the front page

For a handful of new posts, wager on which make the front page. Algolia's
HN search API settles it.

```python
front = get("https://hn.algolia.com/api/v1/search", tags="front_page")
```

#### 38. NOAA tides: The tide is a sure thing

A control for the whole forecasting game: NOAA publishes tide predictions
that are nearly always right. If your Brier score on tides isn't close to
zero, the plumbing is broken, not your judgement.

```python
get("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
    product="predictions", station=station, begin_date=day, range=24,
    datum="MLLW", interval="hilo", units="metric", time_zone="lst_ldt", format="json")
```

### Things to read

#### 39. Open English WordNet: A dictionary rung

The checkout problem (16) is a word with two senses. WordNet knows "seal"
the stamp, "seal" the animal and "seal" the gasket. Expanding a bite's words
with the right synonyms (or flagging when a word has several senses) is a
cheap fix before any embedder. CC BY 4.0, offline once downloaded.

```python
import wn
wn.download("oewn:2024")               # once, off the box
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
book = get(f"https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt")
```

Watch: Gutenberg asks that bulk downloads come from a mirror, not the site.

### Things to draw from

#### 43. Skyfield: A horoscope with real planets

`horoscope.py` is made up. Skyfield computes where the planets really are,
offline, from a JPL ephemeris file. Each sign is a real 30-degree slice of
the ecliptic, and the drand round picks the reading for the sign.

```python
from skyfield.api import load
ts, eph = load.timescale(), load("de421.bsp")   # the file, fetched once
lat, lon, _ = eph["earth"].at(ts.now()).observe(eph["mars"]).apparent().ecliptic_latlon()
sign = SIGNS[int(lon.degrees // 30)]
```

#### 44. Natural Earth: Today's place

Natural Earth's populated places are public domain and small. A drand round
picks one, and it becomes the day's place: where the oracle "is", where the
weather bet (33) is made, where the-table's next scene is set.

```python
place = rng.choice(places)    # rng = StreamRandom.from_seed(randomness)
```

#### 45. The Met Open Access: Art of the day

Hundreds of thousands of the Met's public-domain works, CC0, by API. A drand
draw picks one for each sealed session, beside its rune (15).

```python
ids = get("https://collectionapi.metmuseum.org/public/collection/v1/objects")["objectIDs"]
art = get(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{rng.choice(ids)}")
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
rows = get(f"https://ourworldindata.org/grapher/{slug}.csv")
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
req = TimestampRequestBuilder().data(bundle_bytes).build()   # sent to a TSA
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
    continue   # leave it a miss; a miss reaches the map, a flood doesn't
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
cid = "b" + base32(bytes([1, 0x55, 0x12, 0x20]) + bytes.fromhex(chunk_sha256)).lower().rstrip("=")
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
t = genesis_time + period * (round - 1)   # from /{chain_hash}/info
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
h3.latlng_to_cell(lat, lng, 5)   # a cell ID, a 15-digit hex string
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
| `xref` (`dupes`) | IDs defined twice in one repo | Only in the xref report |
| willows-grove `templates/cross-table/cross_table.py` | Boxes on a grid, each filled by copying a passage. `measure --against` says which boxes changed since a snapshot; `link` writes a crosswalk between two grids | No. It lives in the grove, and the one script doesn't call it |

So exact sameness (same hash) and "comes from the same place" are covered.
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
    rel = ("same" if a.sha256 == b.sha256 else
           "near" if jaccard(shingles(a), shingles(b)) >= NEAR else    # difflib / MinHash
           "close" if cosine(embed(a), embed(b)) >= CLOSE else None)    # rung 3, when on
    if rel:
        rows.append({"what": "compare", "a": name(a), "b": name(b),
                     "rel": rel, "diff": diff_hunks(a, b) if rel == "near" else None})
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
