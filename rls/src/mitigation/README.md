# Mitigation Experiments

This package runs the join-policy mitigation sweep used by the artifact. It owns
the mitigation-specific policy/index setup, attack execution, and output
generation; shared timing-oracle mechanics live in `timing_oracle`.

## Contents

|          File | Purpose                                                                                    |
| ------------: | ------------------------------------------------------------------------------------------ |
| `__init__.py` | Package marker.                                                                            |
| `__main__.py` | CLI entry point for `python -m mitigation`.                                                |
|   `attack.py` | Min-of-k timing attack loop and per-config accuracy/result models.                         |
|  `configs.py` | Supported mitigation configurations and their metadata.                                    |
| `db_setup.py` | Helpers for applying policy forms, index layouts, and restoring the baseline schema state. |
|  `outputs.py` | Markdown, CSV, and per-config artifact writers.                                            |
|   `runner.py` | CLI parsing and high-level orchestration for the mitigation sweep.                         |
