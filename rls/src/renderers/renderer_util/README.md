# Renderer Utilities

This package contains low-level helper code for renderer entry points in
`rls/src/renderers`. Keep generic IO, TikZ/PGF, formatting, and statistics
helpers here; complete renderer workflows belong in their own package, such as
`renderers/policy_heatmap`.

## Contents

|                      File | Purpose                                                                                            |
| ------------------------: | -------------------------------------------------------------------------------------------------- |
|             `__init__.py` | Package marker.                                                                                    |
|                   `io.py` | Renderer IO helpers.                                                                               |
|                  `pgf.py` | Low-level TikZ/PGF command helpers.                                                                |
| `reconstruction_table.py` | Shared statistics, formatting, and heat-color helpers for reconstruction result tables.            |
|        `heatmap_table.py` | Shared heatmap/table formatting helpers used by policy/comparison heatmap renderers.               |
|  `timing_distribution.py` | Timing-distribution CSV loading, summary statistics, KDE plotting, and compact LaTeX stats tables. |
