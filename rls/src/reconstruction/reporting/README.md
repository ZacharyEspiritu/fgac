# Reporting

This folder turns reconstruction outputs into CSV/JSON summaries and optional
oracle-call logs. Keep final user-visible report formatting here, separate from
probing and runtime orchestration.

## Contents

|             File | Purpose                                                                 |
| ---------------: | ----------------------------------------------------------------------- |
|    `__init__.py` | Package marker.                                                         |
|     `summary.py` | Builds the final JSON-compatible summary object.                        |
|     `outputs.py` | Writes summary JSON and prints the final run report.                    |
| `oracle_logs.py` | Optional raw oracle-call CSV logging and post-run summary augmentation. |
