# LaTeX Table Renderer

`render_table.py` converts one or more `enumerator` statistics JSON files into a
compact `booktabs` LaTeX table. The table is intended for the DLS recovery
experiments and summarizes the cost of recovering 1-, 2-, 3-, and 4-grams.

The script does not run experiments and does not query OpenSearch or
Elasticsearch. It only reads JSON files produced by `unfilter-dls enumerate`.

## What The Table Shows

For each dataset size and each stage, the renderer emits:

- `# Docs`: the Enron corpus size, displayed once per dataset group.
- `Stage`: `1-gram`, `2-gram`, `3-gram`, `4-gram`, and `Total`.
- `Indexed Terms`: the number of indexed terms or n-grams for that stage.
- `Logical Queries`: the number of logical score queries issued by the attack.
- `Inject/Query Batches`: the number of `_msearch` query batches. The header
  keeps the table narrow; it does not include bulk injection requests.
- `Time`: wall-clock time in seconds, minutes, or hours.

The `Total` row sums logical queries and query batches across stages and sums
stage time. It intentionally leaves `Indexed Terms` blank because the stages use
different units.

When multiple stats files have the same dataset label, the renderer averages
them. Integer columns are rounded to the nearest integer; time is averaged as a
floating-point value.

## Required Input

The preferred input is an enumerator stats JSON file written with
`--stats-file`. The renderer reads:

- `attack_stages[*].corpus_term_stats.counts` for 1-gram counts.
- `attack_stages[*].corpus_ngram_stats.counts` for 2-, 3-, and 4-gram counts.
- `attack_stages[*].attack_stats.logical_score_queries`.
- `attack_stages[*].attack_stats.msearch_requests`.
- `attack_stages[*].timing.wall_clock_seconds`, or
  `enumerate_seconds + stats_seconds` if `wall_clock_seconds` is absent.

If a stats file does not use the newer `attack_stages` schema, the renderer has
a compatibility path for older top-level `attack_stats`, `corpus_term_stats`,
and `ngram_recovery` fields.

## Basic Usage

Render a table from explicit stats files and write it to a `.tex` fragment:

```bash
unfilter-dls table \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d1-r1_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d1-r2_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d1-r3_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d10-r1_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d10-r2_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d10-r3_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d100-r1_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d100-r2_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-dividers-d100-r3_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-d1000-r1_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-d1000-r2_stats.json \
  gcpresults/sasyfix-dividers/sasyfix-d1000-r3_stats.json \
  --output recovery_table.tex
```

Render a table from a glob of newly downloaded reproduce runs:

```bash
unfilter-dls table \
  gcpresults/reproduce/repro-d*-r*_stats.json \
  --output figures/dls_recovery_table.tex
```

Print to stdout instead of writing a file:

```bash
unfilter-dls table results/opensearch/*_stats.json
```

## Dataset Labels

For positional `stats_files`, the renderer tries to infer a dataset label from:

1. `configuration.script_args.corpus_file`
2. `configuration.script_args.stats_file`
3. the stats filename
4. the full stats path

It looks for substrings like `enron_d10` and normalizes them to `D10`.

If filenames do not contain enough information, pass explicit labels with
repeated `--input LABEL=PATH` arguments:

```bash
unfilter-dls table \
  --input D10=gcpresults/reproduce/run1_stats.json \
  --input D10=gcpresults/reproduce/run2_stats.json \
  --input D100=gcpresults/reproduce/d100_run1_stats.json \
  --output figures/dls_recovery_table.tex
```

Labels may be written as `1`, `D1`, or `d1`; these all normalize to `D1`.

If no inputs are provided, the script searches for a small set of historical
default result paths under `results/opensearch/`.

## Options

- `stats_files`: positional stats JSON files. Dataset labels are inferred.
- `--input LABEL=PATH`: explicit dataset label and stats file. Repeat this
  option for replicate runs with the same label.
- `--output PATH`: write the LaTeX fragment to a file instead of stdout. Parent
  directories are created automatically.
- `--time-unit {seconds,minutes,hours}`: controls the Time column unit. The
  default is `minutes`.
- `--skip-missing`: omit missing dataset/stage rows instead of emitting blank
  metric cells.
- `--width WIDTH`: deprecated and ignored. It remains only for compatibility
  with older invocations.

## Output Format

The renderer emits a LaTeX fragment, not a complete document. It starts with
comments naming the required packages and then emits:

```latex
\begin{tabular}{rrrrrr}
...
\end{tabular}
```

Include these packages in the LaTeX document that inputs the table:

```latex
\usepackage{booktabs}
\usepackage{makecell}
\usepackage{multirow}
```

The table footer currently states that each stage recovered the indexed n-gram
set exactly across the included runs. Only use that footer when the supplied
stats files actually satisfy that property.
