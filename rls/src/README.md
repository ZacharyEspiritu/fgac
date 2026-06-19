# Source Package

This directory contains the Python implementation used by the RLS artifact. The
top-level experiment drivers in `rls/orchestration/` invoke these modules from the
installed `rls-artifact` package; runnable packages use `python -m <module>`.

Most experiment-specific code lives in a named submodule. Shared helpers live in
`util`, and paper/table/figure rendering lives in `renderers`.

## Submodules

|             Folder | Purpose                                                                                                                             |
| -----------------: | ----------------------------------------------------------------------------------------------------------------------------------- |
| `microbenchmarks/` | Small standalone measurement runners for existence timing distributions and C-R9 database-size measurements.                        |
|      `mitigation/` | Join-policy mitigation sweep: configuration metadata, policy/index setup, timing attack execution, and outputs.                     |
|           `noise/` | External background-load generator used by Table 1 oracle-accuracy experiments; runnable as `python -m noise`.                      |
|        `patients/` | Patients/doctors benchmark-domain code: dataset setup, SQL query builders, credential parsing, and sampling helpers.                |
|  `reconstruction/` | Timing-based reconstruction attack implementation, including attribute, tuple, candidate, truth, SQL, runtime, and reporting logic. |
|       `renderers/` | Figure/table rendering scripts and renderer-specific helpers for LaTeX/PGF/Markdown outputs.                                        |
|    `rls_artifact/` | Reviewer-facing CLI helpers exposed by the `unfilter-rls` console script.                                                                    |
|   `timing_oracle/` | Table 1 oracle-accuracy runner and shared min-of-k calibration/probing primitives.                                                  |
|            `util/` | Shared utility code used across experiments: args, DB backends/admin, IO, progress, random helpers, SQL validation, and timing.     |

## Common Entry Points

|                                                Command | Purpose                                                         |
| -----------------------------------------------------: | --------------------------------------------------------------- |
|           `python -m microbenchmarks.measure_db_sizes` | Measure physical database/table/index sizes for C-R9.           |
| `python -m microbenchmarks.run_existence_distribution` | Run equality/range existence timing microbenchmarks.            |
|                                 `python -m mitigation` | Run the join-policy mitigation sweep.                           |
|                                      `python -m noise` | Run the remote background-noise workload.                       |
|                          `python -m patients.setup_db` | Create/load the patients/doctors dataset and roles.             |
|                             `python -m reconstruction` | Run the reconstruction attack from a YAML config profile.       |
|                   `python -m renderers.policy_heatmap` | Render policy accuracy heatmaps from oracle-summary outputs.    |
|                               `unfilter-rls claims list` | Show the paper-claim registry.                                  |
|                         `unfilter-rls claims inspect C-R9` | Show focused metadata for one claim.                          |
|                            `unfilter-rls claims run C-R9` | Run C-R9 through the claim-oriented CLI.                        |
|                                    `unfilter-rls doctor` | Check the local package, system tools, GCP auth, and TeX tools. |
|                              `unfilter-rls results list` | List recorded run manifests.                                    |
|                    `unfilter-rls results inspect <RUN_ID>` | Summarize one run manifest.                                   |
|                              `python -m timing_oracle` | Run the Table 1 oracle-accuracy experiment.                     |

See the `README.md` files inside the submodules for file-level notes.

## Quality Checks

Run these from the `rls/` directory after running `./setup.sh`.

Automated tests:

```bash
uv run python -m pytest -q
```

The pytest suite lives in `src/tests`; see `src/tests/README.md` for examples of
running a single file or individual test.

Python syntax/import smoke check:

```bash
uv run python -m compileall -q src
```

Python lint:

```bash
uv run python -m ruff check src
```

Typecheck all Python code in `src/`:

```bash
uv run python -m mypy src
```

Dead-code lint:

```bash
uv run python -m vulture src
```

Bash syntax check:

```bash
find . -path './.venv' -prune -o -path './results' -prune -o \
  -type f -name '*.sh' -print0 | xargs -0 bash -n
```

Shell lint:

```bash
find . -path './.venv' -prune -o -path './results' -prune -o \
  -type f -name '*.sh' -print0 | xargs -0 shellcheck -x -S warning -e SC2034
```

The shellcheck command excludes `SC2034` because the GCP shell layer uses
sourced config loaders and machine-descriptor loaders that intentionally assign
globals for their callers.
