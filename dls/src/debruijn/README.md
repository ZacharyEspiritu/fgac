# De Bruijn Reconstruction Helper

`reconstruct_debruijn.py` builds a de Bruijn graph from recovered k-grams and
traverses it to produce approximate document text. It is intended to consume the
JSON statistics produced by `unfilter-dls enumerate`, especially runs with
`--recover-ngrams --ngram-size 4`.

This script is a reconstruction aid, not the scoring attack itself. It does not
query OpenSearch or Elasticsearch. It only reads recovered or ground-truth
k-gram sets from an existing result file and assembles overlapping k-grams into
longer contigs.

For the paper-level discussion of why recovered k-grams can support approximate
text reconstruction, see the paper/writeup. This README documents how to run the
helper and how to interpret its inputs and outputs.

## Implementation Layout

- `reconstruct_debruijn.py`: CLI entrypoint and top-level orchestration.
- `models.py`: shared dataclasses for graph edges, components, contigs, and
  loaded k-gram sets.
- `loaders.py`: stats JSON and text-log parsing.
- `graph.py`: de Bruijn graph construction, weak components, and graph summary
  metrics.
- `traversal.py`: unitig, Euler, greedy traversal, contig filtering, and
  reconstruction assembly.
- `render.py`: text/JSON rendering and output writing.

## High-Level Method

Each k-gram `(t_1, ..., t_k)` is treated as a directed edge from the
`(k-1)`-gram prefix `(t_1, ..., t_{k-1})` to the `(k-1)`-gram suffix
`(t_2, ..., t_k)`. Paths in this graph correspond to sequences of overlapping
k-grams, so traversing the graph gives candidate plaintext fragments.

The graph can be ambiguous when different document fragments share the same
prefix or suffix. In those cases, the output is an approximate reconstruction:
it lists contigs that are consistent with the recovered k-gram set, but it does
not recover document boundaries or guarantee a unique original ordering.

## Usage

Reconstruct from the largest k available in an attack stats file:

```bash
unfilter-dls debruijn \
  results/opensearch/enron_d10_stats.json
```

Use recovered 4-grams and write the text output to a file:

```bash
unfilter-dls debruijn \
  results/opensearch/enron_d10_stats.json \
  --k 4 \
  --source recovered \
  --traversal euler \
  --output results/opensearch/enron_d10_debruijn.txt
```

Write text and JSON outputs from the same graph construction and traversal:

```bash
unfilter-dls debruijn \
  results/opensearch/enron_d10_stats.json \
  --k 4 \
  --source recovered \
  --traversal euler \
  --output results/opensearch/enron_d10_debruijn.txt \
  --json-output results/opensearch/enron_d10_debruijn.json
```

Emit JSON instead of text:

```bash
unfilter-dls debruijn \
  results/opensearch/enron_d10_stats.json \
  --k 4 \
  --format json \
  --output results/opensearch/enron_d10_debruijn.json
```

Only show the ten longest contigs with at least five k-gram edges:

```bash
unfilter-dls debruijn \
  results/opensearch/enron_d100_stats.json \
  --k 4 \
  --min-edges 5 \
  --max-contigs 10
```

## Input Formats

The preferred input is an `enumerator` stats JSON file. The script reads
`attack_stages` and chooses the requested `--k`, or the largest available k if
`--k` is omitted. For JSON inputs, `--source` selects which set of k-grams to
assemble:

- `recovered`: use the recovered k-grams for that stage.
- `indexed`: use the ground-truth indexed k-grams recorded in the stats file.
- `recovered-indexed`: use the intersection of recovered and indexed k-grams. If
  indexed ground truth is unavailable, this falls back to recovered k-grams.
- `missing`: use indexed k-grams that were not recovered.
- `extra`: use recovered k-grams not present in the indexed ground truth.

The `indexed`, `missing`, and `extra` sources require stats files produced from
a run where source texts were available. They are unavailable with
`--keep-index` unless the stats file already includes ground truth.

The script can also read a plain log file containing lines such as:

```text
recovered 4-gram: alpha beta gamma delta
recovered 4-grams: ['alpha beta gamma delta', 'beta gamma delta epsilon']
```

For non-JSON logs, only `--source recovered` is supported.

K-grams are whitespace-tokenized. The script ignores any k-gram whose token
count does not match the selected k.

## Traversal Modes

`--traversal unitigs` emits maximal non-branching paths. This is the default and
is useful for seeing unambiguous graph fragments.

`--traversal euler` emits one Euler trail for each Eulerian component. For
non-Eulerian components, it falls back to deterministic greedy trails that walk
unused outgoing edges until stuck. This often produces longer document-like
fragments, but ambiguous branches are resolved by the script's deterministic
edge ordering rather than by a language model or external corpus.

## Options

- `result_file`: required path to an `enumerator` stats JSON file or supported
  text log.
- `--k K`: k-gram size to reconstruct. Defaults to the largest available k.
- `--source {recovered,indexed,recovered-indexed,missing,extra}`: which k-gram
  set to use from stats JSON. Default: `recovered`.
- `--traversal {unitigs,euler}`: graph traversal mode. Default: `unitigs`.
- `--min-edges N`: only emit contigs with at least `N` k-gram edges. Default:
  `1`.
- `--max-contigs N`: after sorting by edge count, emit at most `N` contigs. `0`
  means no limit.
- `--output PATH`: write output to a file instead of stdout. Parent directories
  are created automatically.
- `--format {text,json}`: output format. Default: `text`.
- `--json-output PATH`: also write JSON output to this path after the same graph
  construction and traversal used for the primary output.

## Text Output Format

The default text output starts with comment-style metadata:

```text
# source: results/opensearch/enron_d10_stats.json
# input kind: stats
# stage: 4-gram recovery
# k: 4
# recovered/input k-grams used: 721
# nodes: 725
# weak components: 37
# branching nodes: 12
# Eulerian components: 4
# contigs emitted: 37/37
```

Each contig is then printed in a FASTA-like block:

```text
>contig_1 component=1 traversal=greedy edges=42 tokens=45
tokenized reconstructed text goes here
```

The `edges` count is the number of k-grams used in the contig. The `tokens`
count is the length of the reconstructed token sequence.

## JSON Output Format

With `--format json`, the output is a JSON object with:

- `metadata`: source path, input kind, selected stage, source set, and
  traversal.
- `graph`: k, edge count, node count, weak component count, branching node
  count, and Eulerian component count.
- `contigs_emitted`: number of contigs in the output after filtering and
  truncation.
- `contigs_total`: number of contigs before `--max-contigs` truncation.
- `contigs`: list of objects with `component_id`, `traversal`, `edge_count`,
  `token_count`, and reconstructed `text`.

The JSON format is better for downstream analysis; the text format is better for
manual inspection.
