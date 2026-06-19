# `config`

Each YAML file in this directory configures one reconstruction run mode. Pass it
with:

```bash
python -m reconstruction --config src/reconstruction/config/<file>.yml ...
```

`--attacker-dsn` and `--admin-dsn` are intentionally CLI-only. Other CLI options
can be set in the YAML file and overridden on the command line when supplied.
Boolean CLI flags only set a value to `true`; edit the config to make one
`false`.

## Contents

| File | Purpose |
| --- | --- |
| `singleattr_binary.yml` | Single-attribute binary-probe profile. |
| `singleattr_linear.yml` | Single-attribute linear-probe profile. |
| `tuplext.yml` | Tuple-extension profile. |
| `README.md` | Config option reference and developer orientation for this folder. |

## Top-Level Options

`candidates` is required (see "Candidate Specs"). All other keys are optional.

|                                   Key |        Type        |    Default     | Purpose (see `--help` for detailed explanations)                             |
| ------------------------------------: | :----------------: | :------------: | ---------------------------------------------------------------------------- |
|                          `rls_policy` | `join` or `inline` |     `join`     | RLS policy to install before probing.                                        |
|                               `table` |       string       |   `patients`   | Target table.                                                                |
|                          `attributes` |       string       | candidate keys | Comma-separated attributes to reconstruct.                                   |
|                              `verify` |      boolean       |    `false`     | Load admin ground truth and verify guesses.                                  |
|                          `output_dir` |       string       |   `results`    | Directory for JSON/CSV outputs.                                              |
|                  `no_progress_output` |      boolean       |    `false`     | Disable progress output.                                                     |
|                    `log_oracle_calls` |      boolean       |    `false`     | Emit per-oracle-call logs and summary metrics.                               |
|                     `skip_attr_probe` |      boolean       |    `false`     | Skip attribute probing and use sampled tuples. Requires `sample_tuples > 0`. |
|                       `sample_tuples` |      integer       |      `0`       | Sample this many existing tuples from the database.                          |
| `num_queries_for_initial_calibration` |      integer       |      `3`       | Calibration queries for the initial missing/existing threshold.              |
|               `num_queries_per_probe` |      integer       |      `1`       | Queries per candidate or tuple probe.                                        |
|                             `workers` |      integer       |      `1`       | Worker threads for probing.                                                  |
|                `tuple_extension_mode` | `any` or `between` |     `any`      | Tuple-extension query shape.                                                 |
|           `tuple_recompute_threshold` |      boolean       |    `false`     | Recompute tuple-extension threshold for every probe.                         |
|          `tuple_recompute_cal_rounds` |      integer       |      `1`       | Calibration rounds when recomputing tuple thresholds.                        |

## Candidate Specs

`candidates` maps attribute names to candidate domains:

```yaml
candidates:
  age:
    range:
      start: 1
      end: 120
    strategy: binary
```

Supported domain forms:

- `range`: integer range with `start`, `end`, optional `step`, and optional
  `format` for string formatting, such as `"%05d"`.
- `parts`: Cartesian product of integer parts, each with `start`, `end`,
  optional `step`, and `width`; optional `separator` joins the formatted parts.
- `values`: explicit scalar values or a mixed list of scalar/range/parts
  segments.
- scalar or list directly: shorthand explicit values.

Supported candidate flags:

- `strategy: binary` or `binary_search: true`: use indexed binary probing.
- `strategy: tuple_in` or `tuple_in: true`: allow tuple-extension `IN` probing.
- `skip_probe: true`: treat the candidate values as already recovered.
