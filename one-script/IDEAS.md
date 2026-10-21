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

11. [How sessions end (`pools.py`)](#11-how-sessions-end)
12. [Twelve questions about your repos (`deck.py`)](#12-twelve-questions-about-your-repos)
13. [Where the two copies split (`diverge`)](#13-where-the-two-copies-split)
14. [A daily lore row from commits](#14-a-daily-lore-row-from-commits)
15. [A rune for each sealed session](#15-a-rune-for-each-sealed-session)

**Found while trying it**

16. [Turn on the embedder](#16-turn-on-the-embedder)
17. [Check the README's prompt timing](#17-check-the-readmes-prompt-timing)

**Outside projects that could attach cleanly**

18. [SQLite FTS5: BM25 as a rung (already in Python)](#18-sqlite-fts5-bm25-as-a-rung)
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

---

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
WordLlama rung does.

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

### 25. OpenTimestamps: an anchor for the record

Every check-in says *anchor: never sealed; a cut or a rewrite of the record
can't be seen*. OpenTimestamps sends only a SHA-256 to its public calendars
and returns a proof that the hash existed by a Bitcoin block's time. Stamp
each session bundle or the record's tip, and a rewrite after the fact shows.

```sh
ots stamp sessions/2026-10-21.json      # writes sessions/2026-10-21.json.ots
ots verify sessions/2026-10-21.json.ots
```

Watch: this is the one item that goes online. Only the hash leaves the box,
it's off by default, and no network is `unreachable`, not a failed seal. It
needs no key, so it never comes near the vault.

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

Watch: the content is CC BY-SA and the bindings are GPL, so keep it a
separate process and cite the article in every served row.

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

```sh
mutmut run   # with paths_to_mutate = onescript/gate.py in setup.cfg
mutmut results
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
