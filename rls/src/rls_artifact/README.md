# Artifact CLI

This package contains reviewer-facing command-line helpers for the RLS artifact.
It is intentionally thin: experiment implementations remain in their claim
modules, while this package owns top-level diagnostics and future orchestration
entry points exposed through the `unfilter-rls` console script.

## Contents

| File | Purpose |
| ---: | ------- |
| `__init__.py` | Package metadata. |
| `claims.py` | Paper-claim registry and CLI execution helpers. |
| `cli.py` | Top-level `unfilter-rls` command parser. |
| `doctor.py` | Local dependency, package-import, GCP, and TeX health checks. |
| `manifest.py` | Internal helper used by shell runners to write run manifests. |
| `paths.py` | Project-root discovery shared by CLI helpers. |
| `results.py` | `unfilter-rls results list/inspect` implementation for recorded manifests. |
| `yaml_io.py` | Small typed wrapper around PyYAML. |
