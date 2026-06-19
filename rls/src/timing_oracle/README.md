# Timing Oracle

This package contains the Table 1 oracle-accuracy runner and the shared min-of-k
timing-oracle primitives. Other packages, such as `mitigation`, reuse `core.py`
for calibration and alternating probe trials.

## Contents

|          File | Purpose                                                                                             |
| ------------: | --------------------------------------------------------------------------------------------------- |
| `__init__.py` | Package marker.                                                                                     |
| `__main__.py` | CLI entry point for `python -m timing_oracle`.                                                      |
|     `core.py` | Calibration samples, min-of-k probe helpers, probe stats, and alternating trial execution.          |
|   `runner.py` | Table 1 oracle-accuracy CLI, policy loop, metrics capture, rich output panel, and artifact writers. |
