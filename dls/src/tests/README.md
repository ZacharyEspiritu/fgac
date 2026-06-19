# DLS Tests

Run the DLS pytest suite from the DLS artifact root:

```bash
cd dls
uv run python -m pytest
```

`dls/pyproject.toml` sets `testpaths = ["src/tests"]` and `pythonpath = ["src"]`,
so the command above collects the tests in this directory and imports the local
DLS modules without extra environment variables.

To run a single test file:

```bash
uv run python -m pytest src/tests/test_ngram_enumerator.py
```

To run one test:

```bash
uv run python -m pytest src/tests/test_ngram_enumerator.py::test_sayt_ngram_recovery_uses_mpp_on_shingle_fields
```

The tests in this directory are local pytest checks. They should not require
OpenSearch, Elasticsearch, Docker, GCP, or the full Enron dataset.
