# RLS Tests

Run the RLS pytest suite from the RLS artifact root:

```bash
cd rls
uv run python -m pytest
```

`rls/pyproject.toml` sets `testpaths = ["src/tests"]` and `pythonpath = ["src"]`,
so the command above collects the tests in this directory and imports the local
RLS modules without extra environment variables.

To run a single test file:

```bash
uv run python -m pytest src/tests/test_claims.py
```

To run one test:

```bash
uv run python -m pytest src/tests/test_claims.py::test_resolve_claim_accepts_common_aliases
```

The tests in this directory are local pytest checks. They mock external tools
where needed and should not provision VMs, call GCP, or run the long artifact
experiments.
