# DLS Enumerator Attack Driver

`enumerator` is the main driver for the OpenSearch/Elasticsearch scoring
side-channel experiments. It creates or reuses a protected test index, loads a
hidden corpus, configures a DLS-filtered low-privilege user, and attempts to
recover the analyzed terms in the hidden documents. When the target field is
`search_as_you_type`, it can also recover 2-, 3-, and 4-gram shingle terms from
the generated subfields.

The implementation is a research artifact driver for a controlled local or VM
test deployment where the attacker can write probe documents and issue queries,
while DLS hides the corpus documents from the attacker user.

For the formal attack, correctness argument, and scoring derivation, see the
paper/writeup. This README is only an operator guide for running the script and
understanding its inputs and outputs.

## High-Level Attack

The script uses scoring differences on attacker-visible probe documents to
answer two oracle questions:

1. Does an analyzed term exist in the hidden corpus?
2. Does any analyzed term start with a chosen prefix?

The prefix oracle is based on `match_phrase_prefix`-style prefix expansion, or
one of the compatible variants implemented in this package. The enumerator then
walks the analyzer-aware trie induced by the configured attack alphabet. A
prefix-positive branch is extended with each possible next character; terminal
prefixes are either exact-checked or accepted as inferred leaves, depending on
the chosen strategy.

For `search_as_you_type` fields, the script first recovers 1-grams from the root
`text` field. It can then use the recovered lower-order grams as seeds to
enumerate the `_2gram`, `_3gram`, and `_4gram` subfields.

## Implementation Layout

- `__main__.py`: compatibility module entrypoint.
- `cli.py`: compatibility wrapper for the runner entrypoint.
- `args.py`: command-line parsing and typed option dataclasses.
- `runner.py`: top-level attack orchestration.
- `setup_workflow.py`: index setup, corpus loading, batch sizing, analyzer
  traversal setup, and prefix scoring-policy selection.
- `recovery_stats.py`: recovery summaries, n-gram coverage statistics, and
  n-gram stage JSON assembly.
- `enumerator.py`: core 1-gram trie traversal, probe batching, scoring queries,
  and exact checks.
- `ngram_enumerator.py`: SAYT 2-, 3-, and 4-gram traversal built from recovered
  lower-order grams.
- `prefix_oracles.py`: pluggable prefix oracle implementations and score
  direction policies.
- `analyzer.py`: analyzer-aware trie traversal construction.
- `search_backend.py`: OpenSearch/Elasticsearch connection and backend-specific
  API adapters.
- `search_env.py`: index setup, corpus loading, backend metadata, and analyzer
  samples.
- `stats_report.py`: JSON statistics assembly and writing.
- `progress.py`: Rich setup and live attack progress displays.
- `types.py`: protocol types for the small subset of search-client APIs used by
  the attack.

## Prerequisites

Install the pinned repository dependencies:

```bash
uv python install 3.10
uv venv --python 3.10 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

At minimum, install the Python client for the backend you are using and `rich`
for the live progress display:

```bash
uv pip install --python .venv/bin/python opensearch-py rich
# or
uv pip install --python .venv/bin/python elasticsearch rich
```

Start a backend. For the local OpenSearch setup in this repository:

```bash
docker compose -f config/docker-compose.opensearch.yml up -d opensearch
```

The default OpenSearch connection is:

```bash
export OPENSEARCH_HOST=localhost
export OPENSEARCH_PORT=9200
export OPENSEARCH_SCHEME=https
export OPENSEARCH_VERIFY_CERTS=false
export OPENSEARCH_ADMIN_PASSWORD='@!A134Kwjdoiwna!'
export OPENSEARCH_USER_PASSWORD='KTrdMBtPB6NmUXP'
```

Elasticsearch uses the same suffixes with the `ELASTICSEARCH_` prefix. The
defaults are `localhost:9201`, scheme `http`, admin username `elastic`, and user
username `user`.

The script also accepts generic `SEARCH_*` environment variables as fallbacks,
for example `SEARCH_HOST` or `SEARCH_VERIFY_CERTS`.

## Basic Usage

Run a deterministic local JSONL corpus against a normal `text` field:

```bash
unfilter-dls enumerate \
  --backend opensearch \
  --corpus-file dataset/enron_d1.jsonl \
  --chars abcdefghijklmnopqrstuvwxyz \
  --stats-file results/opensearch/enron_d1_stats.json
```

Run a deterministic local JSONL corpus against a `search_as_you_type` field and
recover up to 4-grams:

```bash
unfilter-dls enumerate \
  --backend opensearch \
  --text-field-type search_as_you_type \
  --search-as-you-type-max-shingle-size 4 \
  --prefix-query-mode span_prefix \
  --corpus-file dataset/enron_d10.jsonl \
  --chars "abcdefghijklmnopqrstuvwxyz0123456789'.,:_" \
  --exact-strategy optimized \
  --batch-size 2048 \
  --progress-interval 30 \
  --recover-ngrams \
  --ngram-size 4 \
  --stats-file results/opensearch/enron_d10_stats.json
```

By default, the script deletes and recreates the test index named `test-index`.
Use `--keep-index` if you have already created an index and want to attack its
current contents.

## Input Corpus Formats

The enumerator only loads local JSONL corpus files. Pass `--corpus-file PATH`
unless `--keep-index` is set. The file must be JSON Lines encoded, with one JSON
object per line and a non-empty string `text` field:

```jsonl
{"text": "first hidden document text"}
{"text": "second hidden document text"}
```

Other fields are ignored. The loader inserts each document into `test-index` as:

```json
{ "text": "...", "public": false }
```

## Important Options

Backend and connection:

- `--backend {opensearch,elasticsearch}` selects the client and security API.
- Environment variables configure host, port, scheme, credentials, and TLS
  verification. Backend-specific variables such as `OPENSEARCH_HOST` override
  generic `SEARCH_HOST`.

Index and corpus:

- `--text-field-type {text,search_as_you_type}` controls the recreated mapping.
- `--search-as-you-type-max-shingle-size {2,3,4}` sets SAYT max shingle size.
- `--use-index-prefixes` enables `text.index_prefixes` on a normal `text` field.
- `--index-prefix-min-chars` and `--index-prefix-max-chars` set prefix-index
  bounds for normal `text` fields with `--use-index-prefixes`.
- `--analyze-max-token-count` raises the `_analyze` token cap for ground-truth
  statistics. Set it to `0` to leave the backend default.
- `--keep-index` skips index recreation and corpus loading.

Traversal alphabet:

- `--chars` is the candidate alphabet for ordinary token characters.
- `--joiner-chars` can explicitly mark analyzer-stable internal joiners. The
  analyzer pass also moves joiners found in `--chars` into the joiner alphabet.
- `--max-term-len` bounds explored term length. If omitted, term length is
  unbounded.

Oracle and enumeration:

- `--prefix-query-mode match_phrase_prefix` uses the classic MPP scoring oracle
  for 1-gram recovery.
- `--prefix-query-mode span_prefix` uses `span_near` plus `span_multi(prefix)`
  for 1-gram recovery. This is useful for the top-level SAYT unigram case
  because it keeps scoring on the root field and avoids scoring through SAYT
  shingle subfields.
- `--recover-ngrams` still recovers `_2gram`, `_3gram`, and `_4gram` stages
  with MPP probes against the SAYT shingle fields.
- `--max-expansions` sets MPP `max_expansions` for 1-gram probes when
  `--prefix-query-mode match_phrase_prefix` is selected.
- `--exact-strategy eager` is the baseline exact-check-every-prefix traversal.
- `--exact-strategy optimized` uses prefix-negative pruning, exact-check
  batching, and inferred leaves.
- `--exact-check-inferred-leaves` forces exact checks for leaves that the
  optimized strategy would otherwise infer.

Batching and progress:

- `--batch-size N` batches up to `N` same-length candidates per probe batch.
- `--batch-size auto` derives a request byte budget from
  `http.max_content_length`.
- `--auto-batch-max-content-ratio` is the fraction of `http.max_content_length`
  used by automatic batching.
- `--auto-batch-max-probes` caps the number of probes in an automatic batch.
- `--progress-interval SECONDS` enables progress output when positive. The rich
  display refreshes continuously; this value controls the plain-text fallback
  cadence. Use `0` to disable it.
- `--attack-log-file PATH` redirects ordinary stdout/stderr to a log file. This
  is mainly for wrappers that keep the Rich progress display visible while
  saving the detailed log.
- `--attack-progress-tty` renders Rich progress directly to `/dev/tty` when
  available.
- `--rich-force-terminal` forces Rich terminal output for wrapper-managed
  progress displays.
- `--verbose-prefixes` prints each tested prefix and oracle result.

N-gram recovery:

- `--recover-ngrams` enables SAYT `_2gram`, `_3gram`, and `_4gram` recovery.
  This requires `--text-field-type search_as_you_type`.
- `--ngram-size {2,3,4}` is the largest shingle size to recover.
- `--ngram-max-expansions` sets MPP `max_expansions` for shingle-field probes.

Reproducibility:

- `--random-seed SEED` fixes randomized controls and contexts. If omitted, the
  script generates a fresh 64-bit seed and records it in the stats file.
- `--stats-file PATH` writes JSON run statistics. Pass an empty string to
  disable stats output.

## Output

The script prints:

- the random seed,
- backend version,
- created index mapping,
- analyzer-derived token and joiner alphabets,
- recovered terms and optional recovered n-grams,
- corpus recovery statistics when source texts are available,
- attack counters, including logical queries, `_msearch` requests, injected
  probe documents, and bulk injection requests.

When `--stats-file` is enabled, the JSON file contains:

- `configuration`: parsed script arguments, random seed, attack settings, and
  analyzer traversal metadata.
- `search_backend`: connection metadata, cluster info, index mapping/settings,
  and analyzer samples.
- `timing`: time spent waiting for search-backend responses and local script
  stages.
- `attack_stats`: aggregate unigram attack counters.
- `attack_stages`: one entry for 1-gram recovery and, when enabled, one entry
  for each recovered n-gram stage.
- `corpus_term_stats`: counts and sets for analyzed/indexed terms, terms outside
  the attack alphabet, terms blocked by `--max-term-len`, recovered terms, and
  missing eligible terms.
- `recovered_terms`: sorted recovered 1-grams.
- `ngram_recovery`: present when `--recover-ngrams` is enabled. It includes the
  requested largest n-gram size, per-stage recovered n-grams, indexed n-gram
  ground truth when source texts are available, missing/extra sets, seed
  coverage diagnostics, per-stage attack counters, and per-stage timing.

The stats JSON is the preferred input for the table renderer and the De Bruijn
reconstruction script.
