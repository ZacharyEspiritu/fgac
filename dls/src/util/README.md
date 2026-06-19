# Utility Scripts

This directory contains small helper scripts used by the local reviewer
workflow. They are not attack entry points; use `../../run.sh` for the full local
experiment pipeline.

## `preflight.py`

Implements the reviewer workflow doctor checks used by `../../run.sh` before
backend startup or attack execution. It validates required files, selected
corpus JSONL files, Python package imports, `uv`, `yq`, output-directory
writability, config consistency, Docker/Compose readiness, and backend
reachability when `--skip-docker-start` is used.

Normal reviewer usage is:

```bash
../../run.sh --doctor-only
```

Direct usage is useful when debugging a custom output directory or config:

```bash
unfilter-dls doctor \
  --root-dir . \
  --config config/config.yml \
  --venv .venv \
  --backend elasticsearch \
  --datasets d1 \
  --output-dir /tmp/dls-doctor \
  --skip-docker-start
```

The script exits with status `0` only when all required checks pass.

## `check_python_dependencies.py`

Checks that the Python modules required by the experiment scripts are
importable:

- `opensearchpy`
- `elasticsearch`
- `rich`

`setup.sh` installs these from `requirements.txt`, and the doctor command calls
this script before launching experiments.

Normal usage is through doctor:

```bash
unfilter-dls doctor --config config/config.yml
```

The script exits with status `0` when all modules are present. If any are
missing, it prints a comma-separated list and exits with status `1`.

## `wait_for_search_backend.py`

Polls a local OpenSearch or Elasticsearch endpoint until it responds to
`client.info()`. `run.sh` uses this after starting the Docker container for the
selected backend. The script accepts explicit connection flags and reuses the
same backend client adapter as `unfilter-dls enumerate`.

Example:

```bash
unfilter-dls wait-backend \
  --backend opensearch \
  --host localhost \
  --port 9200 \
  --scheme https \
  --username admin \
  --password '@!A134Kwjdoiwna!' \
  --verify-certs false

unfilter-dls wait-backend \
  --backend elasticsearch \
  --host localhost \
  --port 9201 \
  --scheme http \
  --username elastic \
  --password '@!A134Kwjdoiwna!' \
  --verify-certs false
```

Optional controls:

```bash
unfilter-dls wait-backend \
  --backend opensearch \
  --host localhost \
  --port 9200 \
  --scheme https \
  --username admin \
  --password '@!A134Kwjdoiwna!' \
  --verify-certs false \
  --attempts 120 \
  --sleep-seconds 2
```

The script does not read connection settings from environment variables. For
normal local runs, `run.sh` reads `config/config.yml` and passes those values as
command line flags.

The script exits with status `0` once the backend is ready. If all attempts
fail, it prints the last connection error and exits with status `1`.

## `build_enumerator_command.py`

Builds the reviewer `unfilter-dls enumerate` command from `config/config.yml`.
Both `run.sh` and `gcp/run_opensearch_two_vm_experiment.sh` use this helper so
the local and GCP experiments share the same attack settings.

The script prints one argument per line so Bash wrappers can safely rebuild an
array without shell parsing:

```bash
unfilter-dls build-command \
  --config config/config.yml \
  --cli-bin unfilter-dls \
  --backend opensearch \
  --corpus-file dataset/enron_d1.jsonl \
  --stats-file results/reviewer/stats/opensearch-d1-r1_stats.json \
  --random-seed 12532882246817266601
```

Use `--arguments-only` when the caller already supplies the executable and
subcommand, as the local and GCP launchers do. Use repeated `--extra-arg VALUE`
flags to append override arguments after the generated config-driven options.
