# Policy Heatmap Renderer

This package renders policy-accuracy heatmaps from oracle-summary scenario
outputs. It is used for the Table 1-style policy accuracy figures, but the
implementation is kept separate from `renderer_util` because it owns a complete
renderer workflow rather than low-level shared helpers.

## Contents

|          File | Purpose                                                                          |
| ------------: | -------------------------------------------------------------------------------- |
| `__init__.py` | Package marker.                                                                  |
| `__main__.py` | Module entry point for `python -m renderers.policy_heatmap`.                     |
|     `core.py` | Policy data loading, CPU-load row alignment, and heatmap layout calculation.     |
|     `data.py` | Scenario manifest loading, summary/noise/metrics parsing, and row normalization. |
|      `mpl.py` | Matplotlib output backend for policy accuracy heatmaps.                          |
|      `pgf.py` | PGF/TikZ output backend for policy accuracy heatmaps.                            |
|   `runner.py` | CLI parsing and high-level heatmap orchestration.                                |
