# Tuple Reconstruction

This folder extends recovered single-attribute values into recovered tuples. The
builder plans each tuple length, then dispatches to one of the tuple extension
strategies.

## Contents

|                  File | Purpose                                                                             |
| --------------------: | ----------------------------------------------------------------------------------- |
|         `__init__.py` | Package marker.                                                                     |
|   `reconstruction.py` | Tuple reconstruction entry point and result object.                                 |
|          `builder.py` | Tuple CSV setup, per-step loop, progress setup, strategy dispatch.                  |
|         `planning.py` | Per-step calibration, verify-query setup, strategy selection, and candidate counts. |
|   `worker_context.py` | Tuple step runtime, worker execution, result merging, and buffered CSV output.      |
|      `calibration.py` | Tuple threshold calibration and admin warmup.                                       |
| `binary_extension.py` | Tuple extension over indexed binary-search domains.                                 |
| `subset_extension.py` | Tuple extension using `ANY` or `BETWEEN` subset probes.                             |
| `linear_extension.py` | Tuple extension by linear scan over recovered values.                               |
|     `truth_lookup.py` | Ground-truth and admin verification helpers for tuple probes.                       |
