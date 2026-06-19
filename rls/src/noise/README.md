# Noise Workload

This package implements the external background-load generator used by the Table
1 oracle-accuracy experiments. It is run on the noise VM as `python -m noise`
and communicates only with the database.

## Contents

|            File | Purpose                                                                                                    |
| --------------: | ---------------------------------------------------------------------------------------------------------- |
|   `__init__.py` | Package marker.                                                                                            |
|   `__main__.py` | CLI entry point for `python -m noise`; includes multiprocessing freeze support.                            |
| `controller.py` | Multiprocess noise controller, worker process loop, QPS updates, and summary aggregation.                  |
|     `runner.py` | CLI parsing and long-running remote noise process lifecycle.                                               |
|   `workload.py` | Noise-client credential selection, DSN rewriting, query mix ratios, key sampling, and summary dataclasses. |
