# Ground Truth

This folder supports verification. It can load an in-memory admin snapshot,
fetch truth sets directly from the DB, and post-process raw oracle-call logs.

## Contents

|                    File | Purpose                                                        |
| ----------------------: | -------------------------------------------------------------- |
|           `__init__.py` | Public exports for truth models and helpers.                   |
|             `models.py` | Verification stats and tuple worker/result dataclasses.        |
|       `ground_truth.py` | In-memory ground-truth snapshot and prefix indexes.            |
|              `fetch.py` | Admin DB truth queries for binary and `IN` candidate domains.  |
| `oracle_postprocess.py` | Converts raw oracle-call logs into verified TP/FP/TN/FN stats. |
