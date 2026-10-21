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
21. [ripgrep: a faster `--exact` seed](#21-ripgrep-a-faster-exact-seed)
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
