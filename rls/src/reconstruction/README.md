# Reconstruction Module

This package runs the timing-based reconstruction attack used by the artifact.
The top-level files handle CLI/config loading and run orchestration; subpackages
hold the attribute probe, tuple extension, candidate-domain, database, truth,
and reporting logic.

See each submodules's specific `README.md` for further documentation about
specific submodules.

## Contents

|      File or folder | Purpose                                                                                       |
| ------------------: | --------------------------------------------------------------------------------------------- |
|       `__main__.py` | CLI entry point for `python -m reconstruction`.                                               |
|            `cli.py` | Typed runtime options, config/CLI merge, parameter printing, and argument validation.         |
|     `cli_parser.py` | Raw `argparse` option declarations.                                                           |
|  `config_loader.py` | YAML config loading and JSON-like type normalization.                                         |
| `config_options.py` | Typed helpers for reading options from CLI namespace or YAML config.                          |
|         `runner.py` | High-level run sequence: setup, attribute reconstruction, tuple reconstruction, finalization. |
|          `types.py` | Shared type aliases and lightweight protocols for DB/csv/counter interfaces.                  |
|   `verification.py` | Attribute-level verification against admin ground truth.                                      |
|        `attribute/` | Single-attribute calibration and probing.                                                     |
|       `candidates/` | Candidate-domain parsing, value generators, and membership tests.                             |
|           `config/` | YAML profiles for the supported reconstruction modes.                                         |
|          `probing/` | Generic probing algorithms and worker/progress utilities.                                     |
|        `reporting/` | Summary, final output, and oracle-call logging.                                               |
|          `runtime/` | Run context, setup, execution object, and finalization.                                       |
|              `sql/` | SQL builders and DB timing helpers.                                                           |
|            `truth/` | Ground-truth snapshots and verification fetch/post-processing helpers.                        |
|            `tuple/` | Tuple-extension reconstruction strategies.                                                    |
