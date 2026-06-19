# Document-Level Security (DLS)

This directory contains the artifact for the Document-Level Security section of
the paper.

> **Note:** As a reminder, if possible, we encourage evaluators to run this on
> their own infrastructure, not the Google Compute Engine VM infrastructure we
> provided to you.

## Quick Install

The quickest setup path is:

```bash
./setup.sh
```

This installs the system tools used by the reproduce scripts:

- `uv`, which installs Python 3.10 and syncs `.venv` with the packages in
  `pyproject.toml` (also mirrored in `requirements.txt`).
- `yq`, for reading YAML experiment configuration files.
- Docker with Docker Compose v2, for local OpenSearch/Elasticsearch runs.
- Google Cloud CLI (`gcloud`), for launching GCP experiments.
- Standard Unix shell tools available on macOS/Linux: `bash`, `tar`, `ssh`, and
  `scp`.

Use `./setup.sh --help` for options such as `--check-only`, `--skip-gcloud`, or
a custom virtualenv path. By default, `setup.sh` also starts the local
OpenSearch and Elasticsearch Docker containers; pass `--skip-docker-start` if
you only want dependency setup.

## CLI

After setup, use `unfilter-dls` as the stable command-line entrypoint for the DLS
tools:

```bash
unfilter-dls --help
unfilter-dls doctor --help       # to check installation
unfilter-dls enumerate --help       # for term recovery
unfilter-dls debruijn --help        # for reconstruction of n-grams
unfilter-dls table --help           # for LaTeX table rendering
```

## Run all Experiments

After running "Quick Install", to reproduce all the table and reconstruction
experiments locally, run:

```bash
./run.sh
```

`run.sh` invokes Python through uv internally and checks that the Python project
environment, Python packages, and deterministic Enron JSONL files are available.
By default, it runs the selected datasets against both OpenSearch and
Elasticsearch. These backend runs are sequential; if the wrapper needs to start
a backend, it first stops the inactive backend container to reduce local memory
pressure. For trials that need a fresh attack run, it checks that the required
Docker backend is running, then runs the attack. It then renders the LaTeX table
and runs greedy de Bruijn reconstruction for every result.

While each trial runs, the wrapper shows the rich live progress display in the
terminal and writes the complete attack output to the per-trial log file.
Backend Docker service names and default local connection settings live in
`config/config.yml`; pass `--config PATH` to use a different config. The fixed
reviewer attack settings and greedy reconstruction settings used by `run.sh`
also live in the `attack` and `reconstruction` sections of that file. See
`config/README.md` for the full option reference.

The default outputs are:

```text
results/reviewer/stats/*_stats.json
results/reviewer/logs/*.log
results/reviewer/reconstructions/*_debruijn_greedy.txt
results/reviewer/reconstructions/*_debruijn_greedy.json
results/reviewer/opensearch_table.tex
results/reviewer/elasticsearch_table.tex
```

If `--backend` selects a single backend, the wrapper still uses the
backend-specific table name, e.g. `results/reviewer/opensearch_table.tex`.

For dataset/trial pairs listed in `config/config.yml`, `run.sh` uses the
documented seed from that file. For any other trial, it generates a fresh random
seed and records it in the attack stats file.

`run.sh` skips an existing trial when both of these files already exist, where
`<backend>` is `opensearch` or `elasticsearch`:

```text
results/reviewer/stats/<backend>-d<D>-r<R>_stats.json
results/reviewer/reconstructions/<backend>-d<D>-r<R>_debruijn_greedy.json
```

If the stats JSON exists but the reconstruction JSON is missing, the wrapper
regenerates only the reconstruction. To force an attack rerun and overwrite
existing result files, pass `--destructive-rerun-existing-results`.

### (Optional) Additional `run.sh` Options

To run multiple trials per corpus size and average them in the rendered table:

```bash
./run.sh --trials 3
```

To run only a subset of corpus sizes, pass a comma-separated dataset list. The
`d` prefix is optional:

```bash
./run.sh --datasets d1
./run.sh --datasets d1,d10
./run.sh --datasets 1,10,100 --trials 3
```

To run only one backend, pass `--backend`:

```bash
./run.sh --backend opensearch
./run.sh --backend elastic
```

To stop the local backend containers after a run:

```bash
./teardown.sh
```

This preserves Docker volumes by default. To remove indexed data as well, run
`./teardown.sh --volumes`.

To write to a separate output directory:

```bash
./run.sh --trials 3 --output-dir results/reviewer-3x
```

Use `./run.sh --help` for the full option list.

To check a machine before launching an experiment, run:

```bash
unfilter-dls doctor --config config/config.yml
```

For the same checks with the exact dataset/backend/output selection that
`run.sh` will use, run:

```bash
./run.sh --doctor-only
```

The doctor checks validate required files, selected corpus JSONL files, Python
packages, `uv`, `yq`, output-directory writability, reviewer config
consistency, Docker/Compose readiness when a backend must be started, or backend
reachability when `--skip-docker-start` is used.

## Run on GCP (Not Recommended)

If you would like to run the code on GCP, see `gcp/README.md`. We recommend
running these experiments locally if you have enough memory to reduce our
billing costs and also to demonstrate the portability of our experiments (the
networking time is not the main claim for the results).

## Development Notes

### Tests

The focused automated test suite uses `pytest`:

```bash
uv run python -m pytest -q
```

The tests live under `src/tests`; see `src/tests/README.md` for examples of
running a single file or individual test. They cover argument/config behavior,
prefix oracle construction, the 1-gram `span_prefix` plus 2/3/4-gram MPP split,
stats JSON schema, command generation, path discovery, and doctor validation
helpers.

### Type-Checking

Our code type-checks with `mypy`:

```bash
uv run python -m mypy --strict src/cli.py src/enumerator src/debruijn src/latex src/dataset src/util
```
