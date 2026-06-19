# Microbenchmarks

This package holds small standalone measurement runners that support artifact
claims but are not full multi-stage attacks. The GCP wrappers invoke these as
installed package modules.

## Contents

|                            File | Purpose                                                                                           |
| ------------------------------: | ------------------------------------------------------------------------------------------------- |
|                   `__init__.py` | Package marker.                                                                                   |
|           `measure_db_sizes.py` | C-R9 database-size measurement runner; queries PostgreSQL catalog sizes and writes optional JSON. |
| `run_existence_distribution.py` | Existence and range timing-distribution microbenchmark used to produce the KDE inputs/figures.    |
