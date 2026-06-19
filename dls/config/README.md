# DLS Configuration

`config.yml` contains the fixed reviewer workflow settings used by `setup.sh`,
`run.sh`, and the GCP launcher. Most users should edit this file only when they
need a different backend endpoint, attack alphabet, recovery mode, reconstruction
mode, or deterministic trial seed.

Run this after changing the file:

```bash
unfilter-dls doctor --config config/config.yml
```

For full local experiments, `./run.sh --doctor-only --config config/config.yml`
checks the same config together with the selected datasets and output directory.

## `backends`

The `backends` section defines local Docker services and default connection
environment variables for `opensearch` and `elasticsearch`.

- `label`: Human-readable backend name printed by `setup.sh` and `run.sh`.
- `compose_file`: Docker Compose file, relative to the `dls` directory unless
  an absolute path is used.
- `service`: Docker Compose service name to start, stop, and wait for.
- `env`: Default environment variables exported by `run.sh` before connecting
  to the backend.

OpenSearch requires:

- `OPENSEARCH_HOST`
- `OPENSEARCH_PORT`
- `OPENSEARCH_SCHEME`: `http` or `https`
- `OPENSEARCH_VERIFY_CERTS`: boolean
- `OPENSEARCH_ADMIN_USERNAME`
- `OPENSEARCH_ADMIN_PASSWORD`
- `OPENSEARCH_USER_PASSWORD`

Elasticsearch requires:

- `ELASTICSEARCH_HOST`
- `ELASTICSEARCH_PORT`
- `ELASTICSEARCH_SCHEME`: `http` or `https`
- `ELASTICSEARCH_VERIFY_CERTS`: boolean
- `ELASTICSEARCH_ADMIN_USERNAME`
- `ELASTICSEARCH_ADMIN_PASSWORD`
- `ELASTICSEARCH_USER_USERNAME`
- `ELASTICSEARCH_USER_PASSWORD`

Existing environment variables override these defaults for local runs. For
example, setting `OPENSEARCH_HOST` changes the OpenSearch host without editing
`config.yml`.

## `attack`

The `attack` section is translated by `unfilter-dls build-command` into
`unfilter-dls enumerate` flags for each reviewer trial.

- `text_field_type`: Index mapping for the attacked `text` field.
  Valid values: `text`, `search_as_you_type`.
- `search_as_you_type_max_shingle_size`: Maximum SAYT shingle size to create.
  Must be at least `2`, and must be at least `ngram_size` when
  `recover_ngrams` is enabled.
- `analyze_max_token_count`: Value for `index.analyze.max_token_count` when
  recreating the index. Use `0` to keep the backend default.
- `prefix_query_mode`: Prefix oracle used for 1-gram recovery.
  Valid values: `match_phrase_prefix`, `span_prefix`.
- `chars`: Attack alphabet for terms. It must be non-empty and contain no
  whitespace. Analyzer joiners such as apostrophe, period, comma, colon, and
  underscore can be included here.
- `exact_strategy`: Exact-check strategy.
  Valid values: `eager`, `optimized`.
- `batch_size`: Prefix/exact probe batch size, or `auto`.
- `recover_ngrams`: Boolean. When true, run 2-, 3-, and 4-gram recovery after
  1-gram recovery.
- `ngram_size`: Largest shingle size to recover. Valid values: `2`, `3`, `4`.

The reviewer configuration should keep:

```yaml
text_field_type: search_as_you_type
prefix_query_mode: span_prefix
recover_ngrams: true
ngram_size: 4
```

With this setup, the attack uses `span_prefix` for 1-gram recovery and
`match_phrase_prefix` on the SAYT `_2gram`, `_3gram`, and `_4gram` fields for
the higher-order stages.

## `reconstruction`

The `reconstruction` section controls the greedy de Bruijn reconstruction that
`run.sh` performs after each attack stats file is produced.

- `k`: K-gram size to assemble. Must be at least `2`; reviewer runs use `4`.
- `source`: K-gram set to assemble from the stats JSON.
  Valid values: `recovered`, `indexed`, `recovered-indexed`, `missing`, `extra`.
- `traversal`: Graph traversal strategy.
  Valid values: `unitigs`, `euler`.

`recovered` uses only recovered k-grams. `indexed`, `missing`, and `extra`
require stats files that include source-text ground truth.

## `seeds`

The `seeds` section maps `"<D>:<trial>"` to deterministic random seeds passed
to `unfilter-dls enumerate --random-seed`. For example:

```yaml
"100:2": 12532882246817266702
```

applies to dataset `D100`, trial `2`.

These seeds affect randomized control terms used during the attack. The
side-channel is deterministic for a fixed setup, so changing seeds should
produce essentially the same recovery results. If `run.sh` needs a dataset/trial
pair not listed here, it generates a fresh seed and records it in the stats
file.

## Docker Compose Files

- `docker-compose.opensearch.yml`: single-node OpenSearch service used by the
  `opensearch` backend config.
- `docker-compose.elastic.yml`: single-node Elasticsearch service used by the
  `elasticsearch` backend config.

Change these files only when you need a different local image, resource limit,
port mapping, or service-level setting.
