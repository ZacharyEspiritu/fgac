# Renderers

This package contains scripts that turn experiment outputs into the paper/table
artifacts checked into `results/` or consumed by LaTeX. The top-level files are
entry points; larger renderer workflows live in named submodules and low-level
shared helpers live under `renderer_util/`.

## Contents

|                            File or folder | Purpose                                                                                      |
| ----------------------------------------: | -------------------------------------------------------------------------------------------- |
|                     `existence_figure.py` | Render existence/range timing CSVs plus PGF histograms into complete LaTeX figure snippets.  |
|              `join_mitigation_heatmap.py` | Render the join-policy mitigation sweep as a policy-style heatmap.                           |
|               `single_attribute_table.py` | Render the single-attribute reconstruction results table.                                    |
|                `cross_zone_comparison.py` | Render the cross-zone vs same-zone Table 1 comparison artifacts.                             |
| `tuple_extension_table.py` | Render the tuple-extension reconstruction results table.                                     |
|                `validate_cr9_db_sizes.py` | Validate measured C-R9 database sizes against the paper claim.                               |
|                         `policy_heatmap/` | Policy accuracy heatmap workflow: CLI, data loading, layout, and output backends.            |
|                          `renderer_util/` | Low-level shared IO, PGF/TikZ, table, reconstruction-table, and timing-distribution helpers. |

## `renderer_util/`

|                      File | Purpose                                                                                            |
| ------------------------: | -------------------------------------------------------------------------------------------------- |
|             `__init__.py` | Package marker.                                                                                    |
|                   `io.py` | Renderer IO helpers.                                                                               |
|                  `pgf.py` | Low-level TikZ/PGF command helpers.                                                                |
| `reconstruction_table.py` | Shared statistics, formatting, and heat-color helpers for reconstruction tables.                   |
|        `heatmap_table.py` | Shared heatmap/table formatting helpers used by policy/comparison heatmap renderers.               |
|  `timing_distribution.py` | Timing-distribution CSV loading, summary statistics, KDE plotting, and compact LaTeX stats tables. |
