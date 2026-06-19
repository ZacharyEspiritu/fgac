# Probing Utilities

This folder contains generic probing algorithms and shared mechanics for
parallel execution, progress reporting, query counting, and timed DB calls. It
should not depend on reconstruction-specific attribute or tuple semantics.

## Contents

|              File | Purpose                                                               |
| ----------------: | --------------------------------------------------------------------- |
|     `__init__.py` | Package marker.                                                       |
|       `binary.py` | Generic binary prober over indexed candidate values.                  |
|     `in_probe.py` | Generic subset-splitting prober for `IN`/array-style probes.          |
|       `linear.py` | Generic linear prober over iterable candidates.                       |
|     `parallel.py` | Worker execution, chunking, thread-safe progress/counter setup.       |
|     `progress.py` | Human-readable progress previews.                                     |
| `query_runner.py` | Wrapper for timed query helpers with fixed rounds/counter/fetch mode. |
