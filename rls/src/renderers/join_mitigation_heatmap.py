# Copyright 2026 MongoDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Heatmap of the join-policy mitigation sweep, in the unified Table-1 style.

Matches the format of the cross-zone / unified heatmap produced by
``src/renderers/policy_heatmap`` (e.g.
``results/postgres/c4table1/renders_v3/c4_table1_unified_both_verified_heatmap.pgf``):
a hand-written TikZ table at ``x=2.0cm,y=0.37cm`` with a ``\\useasboundingbox``,
per-cell ``\\definecolor`` fills (``RdYlGn_r`` PowerNorm over error-from-100%),
and inline ``value$\\,\\pm$ci`` cell text. It reuses the exact cell/colour
helpers from ``heatmap_table`` so the look is identical to that figure.

Rows are the mitigation settings; columns are the min-of-k probe counts; the
colour reads the same way as the Table-1 figure (green = high attacker
accuracy / channel open, red = collapsed to chance / channel sealed).

Input is the ``summary.csv`` from ``python3 -m mitigation``.

Usage:
    uv run python -m renderers.join_mitigation_heatmap \
        --summary results/table5/mit-c4-v1/summary.csv \
        --output-basename results/paper/fig_join_mitigations_heatmap
    # -> fig_join_mitigations_heatmap.{png,pdf,pgf}
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from renderers.renderer_util.io import ensure_parent_dir, write_text
from renderers.renderer_util.pgf import tikz_hrule, tikz_node, tikz_rect
# Use the shared cell renderers so the cells are byte-for-byte the unified /
# cross-zone format: a single centered node with the value and a small inline
# "+/- ci" hugging it (2 dp), e.g. {49.90{\fontsize{4.3}{4.7}\,$\pm$2.88}}.
from renderers.renderer_util.heatmap_table import (
    CELL_FONT_PGF,
    CELL_FONTSIZE_MPL,
    build_color_norm,
    ci_margin_pct,
    mpl_draw_heatmap_cell,
    pgf_draw_heatmap_cell,
)

# z multipliers (normal approx to a binomial proportion), as in the Table-1 renderers.
CI_Z = {95: 1.959963984540054, 99: 2.5758293035489004}

# PowerNorm exponent + colour floor used by the unified heatmap.
GAMMA = 0.3
LOG_FLOOR = 0.01
CMIDRULE_KERN = 0.05  # booktabs-style inset at each end of a cmidrule

# Table geometry in the same x=2.0cm / y=0.37cm units the unified heatmap uses.
# Row labels use the unified table's 6.5/7.0 row-label font; Config column is
# sized to hold "subquery + composite" at that size with right padding.
CONFIG_COL_W = 1.40
ROW_LABEL_FONT_PGF = r"\fontsize{6.5}{7.0}\selectfont"
ROW_LABEL_FONTSIZE_MPL = 6.5
# The recommended mitigation's Config cell is highlighted (light yellow).
HIGHLIGHT_CONFIG = "subq_inline"
HIGHLIGHT_RGB = "255,250,205"   # lemonchiffon
HIGHLIGHT_HEX = "#FFFACD"
# Fits the widest centered inline cell ("49.90 +/-2.88") at the unified cell
# font; this data's sealed row carries a ~2.88 CI vs the unified's near-zero CIs.
HEAT_COL_W = 0.74

# baseline ("join policy") is omitted from the heatmap (the join + single-index
# row is redundant with the other "open" rows for this table). It is still run
# and still appears in the accuracy bar chart; pass it via --configs to include.
DEFAULT_CONFIGS = ("plpgsql_composite", "subq_inline_single", "subq_inline")

# "join" is bolded to match the paper's policy naming. pgf renders real LaTeX
# (\textbf); the Agg preview uses mathtext (\mathbf) so bold survives with no
# TeX install.
CONFIG_LABELS_PGF = {
    "baseline": "join policy",
    "plpgsql_composite": "join + composite",
    "subq_inline_single": "subquery only",
    "subq_inline": "subquery + composite",
}
CONFIG_LABELS_AGG = {
    "baseline": "join policy",
    "plpgsql_composite": "join + composite",
    "subq_inline_single": "subquery only",
    "subq_inline": "subquery + composite",
}


def label_for(config: str, backend: str) -> str:
    table = CONFIG_LABELS_PGF if backend == "pgf" else CONFIG_LABELS_AGG
    if config in table:
        return table[config]
    return config.replace("_", r"\_") if backend == "pgf" else config


def load_summary(
    path: str, configs: Sequence[str]
) -> Tuple[Dict[str, Dict[int, Tuple[float, int]]], List[int]]:
    by_config: Dict[str, Dict[int, Tuple[float, int]]] = defaultdict(dict)
    ks: set = set()
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            config = row["config"]
            if config not in configs:
                continue
            k = int(row["k"])
            by_config[config][k] = (float(row["accuracy_pct"]), int(row["probes"]))
            ks.add(k)
    return by_config, sorted(ks)


def _columns(n_k: int) -> Tuple[List[float], List[float], float]:
    col_widths = [CONFIG_COL_W] + [HEAT_COL_W for _ in range(n_k)]
    col_lefts = [0.0]
    for w in col_widths[:-1]:
        col_lefts.append(col_lefts[-1] + w)
    return col_widths, col_lefts, sum(col_widths)


def _finite_errors(by_config, configs, k_values) -> List[float]:
    return [
        max(LOG_FLOOR, 100.0 - by_config[c][k][0])
        for c in configs
        for k in k_values
        if k in by_config.get(c, {})
    ]


def _cmap_norm(by_config, configs, k_values):
    """(cmap, norm) with the colour direction REVERSED vs the Table-1 heatmaps.

    build_color_norm colours by error = 100 - accuracy through RdYlGn_r, i.e.
    high attacker accuracy => green. For a *mitigation* figure we want the
    opposite reading: accuracy collapsing toward chance (~50%, channel sealed)
    should be green ("good"), and high accuracy (~100%, channel open) red
    ("bad"). Keeping the same error-based norm and swapping RdYlGn_r -> the
    non-reversed RdYlGn flips exactly that axis.
    """
    cmap, norm = build_color_norm(_finite_errors(by_config, configs, k_values), LOG_FLOOR, GAMMA)
    import matplotlib.pyplot as plt

    cmap = plt.get_cmap("RdYlGn").copy()
    cmap.set_bad("white")
    return cmap, norm


def write_pgf_tikz(by_config, configs, k_values, *, ci_level, path) -> None:
    """Hand-written TikZ .pgf mirroring the policy accuracy heatmap exactly."""
    cmap, norm = _cmap_norm(by_config, configs, k_values)
    col_widths, col_lefts, total_width = _columns(len(k_values))
    k_col_left = col_lefts[1]
    k_col_right = col_lefts[len(k_values)] + col_widths[len(k_values)]
    z = CI_Z[ci_level]
    n_rows = len(configs)

    lines = [
        "% Auto-generated by src/renderers/join_mitigation_heatmap.py.",
        "% Requires \\usepackage{tikz}. Include with \\input{...} or wrap in \\noindent\\resizebox.",
        r"\begin{tikzpicture}[x=2.0cm,y=0.37cm]",
        r"\definecolor{rlsOuter}{RGB}{34,34,34}",
        r"\definecolor{rlsText}{RGB}{17,17,17}",
        r"\definecolor{rlsHeaderText}{RGB}{34,34,34}",
        r"\definecolor{rlsWhite}{RGB}{255,255,255}",
        rf"\definecolor{{rlsHighlight}}{{RGB}}{{{HIGHLIGHT_RGB}}}",
        rf"\useasboundingbox (0.000,1.000) rectangle ({total_width:.3f},{-(n_rows + 1):.3f});",
    ]

    # Header band: "Config" multirow-centered across both header rows (Y=0.0),
    # the "k (Queries Per Probe Type)" supraheader at Y=-0.5, then k labels at Y=0.5.
    tikz_node(lines, col_lefts[0] + (col_widths[0] / 2.0), 0.0, r"\textbf{Config}",
              r"\fontsize{6.5}{6.8}\selectfont", "rlsHeaderText")
    tikz_node(lines, (k_col_left + k_col_right) / 2.0, -0.5,
              r"\textbf{$k$ (Queries Per Probe Type)}",
              r"\fontsize{6.5}{6.8}\selectfont", "rlsHeaderText")
    for k_idx, k in enumerate(k_values):
        left = col_lefts[k_idx + 1]
        tikz_node(lines, left + col_widths[k_idx + 1] / 2.0, 0.5, str(k),
                  r"\fontsize{5.6}{6.0}\selectfont", "rlsHeaderText")

    color_idx = 0
    for row_idx, config in enumerate(configs):
        y = float(row_idx + 1)
        # Highlight the recommended mitigation's Config cell (light yellow).
        if config == HIGHLIGHT_CONFIG:
            tikz_rect(lines, 0.0, y, CONFIG_COL_W, 1.0, "rlsHighlight")
        tikz_node(lines, col_lefts[0] + 0.06, y + 0.5, label_for(config, "pgf"),
                  ROW_LABEL_FONT_PGF, "rlsText", extra="anchor=west")
        for k_idx, k in enumerate(k_values):
            left = col_lefts[k_idx + 1]
            width = col_widths[k_idx + 1]
            cell = by_config.get(config, {}).get(k)
            if cell is None:
                tikz_node(lines, left + width / 2.0, y + 0.5, "---",
                          CELL_FONT_PGF, "rlsText")
                continue
            acc, probes = cell
            # Exact unified centered-inline cell (fill + value + small inline CI),
            # via the shared renderer. cmap is the REVERSED RdYlGn (low acc=green).
            color_idx = pgf_draw_heatmap_cell(
                lines, left, y, width, acc,
                ci_margin_pct(acc, probes, z) if ci_level else None,
                cmap, norm, color_idx, LOG_FLOOR, ci_show=bool(ci_level),
            )

    # Rules — cmidrules inset by CMIDRULE_KERN, matching the unified table.
    tikz_hrule(lines, -1.0, 0.0, total_width, "0.7pt")
    tikz_hrule(lines, 0.0, k_col_left + CMIDRULE_KERN, k_col_right - CMIDRULE_KERN, "0.4pt")
    tikz_hrule(lines, 1.0, 0.0, total_width, "0.4pt")
    tikz_hrule(lines, float(n_rows + 1), 0.0, total_width, "0.7pt")

    lines.append(r"\end{tikzpicture}")
    write_text(path, "\n".join(lines) + "\n")


def draw_matplotlib(by_config, configs, k_values, *, ci_level, output_basename) -> None:
    """PNG + PDF preview (decimal-aligned, reversed colours)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    cmap, norm = _cmap_norm(by_config, configs, k_values)
    col_widths, col_lefts, total_width = _columns(len(k_values))
    k_col_left = col_lefts[1]
    k_col_right = col_lefts[len(k_values)] + col_widths[len(k_values)]
    z = CI_Z[ci_level]
    n_rows = len(configs)

    fig_w = max(3.4, total_width * 2.0)
    fig_h = (n_rows + 1.5) * 0.37 * 2.0 + 0.25
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, total_width)
    ax.set_ylim(n_rows + 1, -1)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    rule = "#222222"
    ax.text(col_lefts[0] + col_widths[0] / 2.0, 0.0, "Config", ha="center", va="center",
            fontsize=7.2, fontweight="bold", color="#222222")
    ax.text((k_col_left + k_col_right) / 2.0, -0.5, r"$k$ (Queries Per Probe Type)",
            ha="center", va="center", fontsize=7.2, fontweight="bold", color="#222222")
    for k_idx, k in enumerate(k_values):
        ax.text(col_lefts[k_idx + 1] + col_widths[k_idx + 1] / 2.0, 0.5, str(k),
                ha="center", va="center", fontsize=6.5, color="#222222")

    for row_idx, config in enumerate(configs):
        y = row_idx + 1
        if config == HIGHLIGHT_CONFIG:
            ax.add_patch(Rectangle((0, y), CONFIG_COL_W, 1.0,
                                   facecolor=HIGHLIGHT_HEX, edgecolor="none"))
        ax.text(col_lefts[0] + 0.06, y + 0.5, label_for(config, "agg"),
                ha="left", va="center", fontsize=ROW_LABEL_FONTSIZE_MPL, color="#111111")
        for k_idx, k in enumerate(k_values):
            left = col_lefts[k_idx + 1]
            width = col_widths[k_idx + 1]
            cell = by_config.get(config, {}).get(k)
            if cell is None:
                ax.text(left + width / 2.0, y + 0.5, "---", ha="center", va="center",
                        fontsize=CELL_FONTSIZE_MPL, color="#555555")
                continue
            acc, probes = cell
            # Exact unified centered-inline cell, via the shared renderer.
            mpl_draw_heatmap_cell(
                ax, left, y, width, acc,
                ci_margin_pct(acc, probes, z) if ci_level else None,
                cmap, norm, LOG_FLOOR,
            )

    ax.plot([0, total_width], [-1, -1], color=rule, linewidth=0.9, solid_capstyle="butt")
    ax.plot([k_col_left + CMIDRULE_KERN, k_col_right - CMIDRULE_KERN], [0, 0],
            color=rule, linewidth=0.5, solid_capstyle="butt")
    ax.plot([0, total_width], [1, 1], color=rule, linewidth=0.5, solid_capstyle="butt")
    ax.plot([0, total_width], [n_rows + 1, n_rows + 1], color=rule, linewidth=0.9, solid_capstyle="butt")
    ax.margins(0, 0)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(f"{output_basename}.png", dpi=240, bbox_inches="tight", pad_inches=0.02)
    fig.savefig(f"{output_basename}.pdf", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", default="results/table5/mit-c4-v1/summary.csv",
                        help="summary.csv from python3 -m mitigation.")
    parser.add_argument("--configs", default=",".join(DEFAULT_CONFIGS),
                        help="Comma-separated configs (rows), in display order.")
    parser.add_argument("--ci", type=int, choices=(95, 99), default=99,
                        help="Confidence level for the per-cell margin (default: 99).")
    parser.add_argument("--output-basename",
                        default="results/paper/fig_join_mitigations_heatmap",
                        help="Output basename (no extension); emits .png, .pdf, .pgf.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    by_config, k_values = load_summary(args.summary, configs)
    if not k_values:
        raise SystemExit(f"No rows for configs {configs} found in {args.summary}")
    present = [c for c in configs if c in by_config]
    missing = [c for c in configs if c not in by_config]
    if missing:
        print(f"Warning: configs not found in summary (skipped): {missing}")

    ensure_parent_dir(f"{args.output_basename}.png")
    draw_matplotlib(by_config, present, k_values, ci_level=args.ci,
                    output_basename=args.output_basename)
    write_pgf_tikz(by_config, present, k_values, ci_level=args.ci,
                   path=f"{args.output_basename}.pgf")

    print(f"Wrote {args.output_basename}.{{png,pdf,pgf}}")
    print(f"  rows (settings): {present}")
    print(f"  columns (k):     {k_values}   CI: {args.ci}%")
    for config in present:
        cells = "  ".join(
            f"k{k}={by_config[config][k][0]:.2f}±{ci_margin_pct(by_config[config][k][0], by_config[config][k][1], CI_Z[args.ci]):.2f}"
            for k in k_values if k in by_config[config]
        )
        print(f"  {config:<22s} {cells}")


if __name__ == "__main__":
    main()
