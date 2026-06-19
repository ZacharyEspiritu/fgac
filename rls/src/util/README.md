# Shared Utilities

This package contains small cross-experiment helpers. Keep this folder limited
to functionality used by multiple experiment packages; domain-specific helpers
belong in their owning package, such as `patients`, `noise`, or `timing_oracle`.

## Contents

|                  File | Purpose                                                                                                             |
| --------------------: | ------------------------------------------------------------------------------------------------------------------- |
|         `__init__.py` | Package marker.                                                                                                     |
|             `args.py` | Common `argparse` options and CSV/positive-value parsing helpers.                                                   |
|         `db_admin.py` | Database setup/admin helpers: SQL file execution, COPY loading, role/database management, and DSN rewriting.        |
|       `db_backend.py` | Backend abstraction and PostgreSQL implementation for connections, limit syntax, RLS policy switching, and EXPLAIN. |
|         `db_utils.py` | Small cursor fetch helpers for scalar and row-returning SQL queries.                                                |
|               `io.py` | File IO helpers for parent-directory creation, text, JSON, and CSV read/write.                                      |
| `postgres_metrics.py` | PostgreSQL cache/statistics snapshots used around timing experiments.                                               |
|         `progress.py` | Terminal progress bar with TTY/non-TTY behavior.                                                                    |
|     `random_utils.py` | Shared random-selection helpers.                                                                                    |
|        `sql_utils.py` | SQL identifier validation helpers.                                                                                  |
|           `timing.py` | High-resolution timing helpers for SQL probes.                                                                      |
