# Runtime Orchestration

This folder owns run-scoped objects and setup/finalization glue. It bridges the
CLI/config layer to the attribute and tuple reconstruction implementations.

## Contents

|              File | Purpose                                                                         |
| ----------------: | ------------------------------------------------------------------------------- |
|     `__init__.py` | Package marker.                                                                 |
|      `context.py` | DB connections, backend setup, oracle-log lifetime, and mutable run state.      |
|    `execution.py` | Immutable execution bundle passed through reconstruction helpers.               |
|        `setup.py` | Candidate parsing, sampled tuple setup, known values, and ground-truth loading. |
| `finalization.py` | Summary construction, oracle-log post-processing, and final report output.      |
