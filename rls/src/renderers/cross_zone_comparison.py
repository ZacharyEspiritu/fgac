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

"""Render a Table 1-style comparison of cross-region vs same-zone results.

Reads:
  - results/table2/${RUN_ID}/comparison_input/<scenario>/tab1cross_<scenario>_summary.csv
    (assembled from a cross-region run produced by orchestration/run_crosszone_experiment.sh)
  - results/table1/samezone-baseline/<scenario>/table1_summary.csv
    (same-zone C-R3 join-policy Base + cpu50 cells published by orchestration/run_samezone_exps.sh)

Writes Markdown + CSV + matplotlib PNG/PDF + a TikZ/PGF heatmap that matches
the style of the unified Table 1 heatmap (RdYlGn_r colormap, power-norm of
error rate, per-cell CI annotations, header bands). The PGF output is the same
one a USENIX paper would `\\input{}`, sized to fit one
text column via `\\noindent\\resizebox`.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from renderers.renderer_util.io import open_output_text, write_text
from renderers.renderer_util.pgf import tikz_hrule, tikz_node, tikz_rect
from renderers.renderer_util.heatmap_table import (
    CELL_FONT_PGF,
    CELL_FONTSIZE_MPL,
    CI_FONTSIZE_MPL,
    build_color_norm,
    fmt_acc,
    fmt_ci,
    latex_escape,
    mpl_draw_heatmap_cell,
    parse_float,
    pgf_draw_heatmap_cell,
    wilson_half_width,
)


# ---- Loaders ---------------------------------------------------------------


@dataclass(frozen=True)
class SummaryRecord:
    accuracy_pct: float
    ci99_margin_pct: Optional[float]


@dataclass(frozen=True)
class ComparisonCell:
    accuracy_pct: float
    ci99_margin_pct: Optional[float]


@dataclass(frozen=True)
class ComparisonRow:
    zone: str
    scenario: str
    actual_total_qps: Optional[float]
    by_k: Dict[int, ComparisonCell]
    min_k_99_accuracy: Optional[int]
    is_diff_row: bool = False


def read_summary(csv_path: str) -> Dict[int, SummaryRecord]:
    out: Dict[int, SummaryRecord] = {}
    if not os.path.exists(csv_path):
        return out
    with open(csv_path, newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            try:
                k = int(row["k"])
                accuracy_pct = float(row["accuracy_pct"])
            except (KeyError, ValueError):
                continue
            ci99_margin_pct = None
            try:
                tp = int(row["true_positive"])
                fp = int(row["false_positive"])
                tn = int(row["true_negative"])
                fn = int(row["false_negative"])
            except (KeyError, ValueError):
                pass
            else:
                ci99_margin_pct = wilson_half_width(tp + tn, tp + fp + tn + fn)
            out[k] = SummaryRecord(
                accuracy_pct=accuracy_pct,
                ci99_margin_pct=ci99_margin_pct,
            )
    return out


def actual_noise_qps(scenario_dir: str, name: str) -> Optional[float]:
    """Return actual aggregate noise QPS, or None if not available."""
    candidates = [
        os.path.join(scenario_dir, f"tab1cross_noise_{name}.json"),
        os.path.join(scenario_dir, "table1_noise.json"),
    ]
    for c in candidates:
        if not os.path.exists(c):
            continue
        try:
            with open(c, encoding="utf-8") as h:
                d = json.load(h)
        except Exception:
            continue
        if isinstance(d, list) and d:
            first = d[0]
            if isinstance(first, dict):
                v = first.get("actual_total_qps")
                if v is not None:
                    return parse_float(v)
        elif isinstance(d, dict):
            v = d.get("actual_total_qps")
            if v is not None:
                return parse_float(v)
    return None


def build_row(zone_label: str, scenario: str, summary: Dict[int, SummaryRecord],
              actual_qps: Optional[float], k_values: List[int]) -> ComparisonRow:
    by_k: Dict[int, ComparisonCell] = {}
    min_k_99: Optional[int] = None
    for k in k_values:
        rec = summary.get(k)
        if not rec:
            continue
        by_k[k] = ComparisonCell(
            accuracy_pct=rec.accuracy_pct,
            ci99_margin_pct=rec.ci99_margin_pct,
        )
        if min_k_99 is None and rec.accuracy_pct >= 99.0:
            min_k_99 = k
    return ComparisonRow(
        zone=zone_label,
        scenario=scenario,
        actual_total_qps=actual_qps,
        by_k=by_k,
        min_k_99_accuracy=min_k_99,
    )


def collect_cross_region(directory: str, scenarios: List[str],
                         k_values: List[int]) -> List[ComparisonRow]:
    rows: List[ComparisonRow] = []
    for scen in scenarios:
        sdir = os.path.join(directory, scen)
        summary = read_summary(os.path.join(sdir, f"tab1cross_{scen}_summary.csv"))
        if not summary:
            continue
        rows.append(build_row(
            zone_label="cross zone",
            scenario=scen,
            summary=summary,
            actual_qps=actual_noise_qps(sdir, scen),
            k_values=k_values,
        ))
    return rows


def collect_same_zone(directory: str, scenarios: List[str],
                      k_values: List[int]) -> List[ComparisonRow]:
    rows: List[ComparisonRow] = []
    for scen in scenarios:
        sdir = os.path.join(directory, scen)
        summary = read_summary(os.path.join(sdir, "table1_summary.csv"))
        if not summary:
            continue
        rows.append(build_row(
            zone_label="same zone",
            scenario=scen,
            summary=summary,
            actual_qps=actual_noise_qps(sdir, scen),
            k_values=k_values,
        ))
    return rows


def scenario_display(name: str) -> str:
    _MAP = {"baseline": "Base", "cpu50": "50%"}
    return _MAP.get(name, name)


# Zone-column display. The cross-zone row reads as the region pair rather than
# the literal "cross zone": the PGF form uses \texttt + a math arrow; the plain
# form (markdown / matplotlib preview) uses a unicode arrow.
CROSS_ZONE_PGF = r"\texttt{us-west3} $\leftrightarrow$ \texttt{us-east1}"
CROSS_ZONE_PLAIN = "us-west3 ↔ us-east1"


def zone_display(zone: str, *, pgf: bool = False) -> str:
    if zone == "cross zone":
        return CROSS_ZONE_PGF if pgf else CROSS_ZONE_PLAIN
    return latex_escape(zone) if pgf else zone


# ---- Markdown + CSV outputs ------------------------------------------------

def render_markdown(rows: Sequence[ComparisonRow],
                    k_values: Sequence[int]) -> str:
    header = ["CPU Load", "Zone"] + [f"acc_k{k}" for k in k_values]
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(["---"] * len(header)) + " |"]
    for row in rows:
        cells = [scenario_display(row.scenario), zone_display(row.zone)]
        for k in k_values:
            cell = row.by_k.get(k)
            if cell is None:
                cells.append("-")
                continue
            if cell.ci99_margin_pct is None:
                cells.append(f"{fmt_acc(cell.accuracy_pct)}%")
            else:
                cells.append(
                    f"{fmt_acc(cell.accuracy_pct)}% ± {fmt_ci(cell.ci99_margin_pct)}"
                )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_csv(path: str, rows: Sequence[ComparisonRow],
               k_values: Sequence[int]) -> None:
    # CSV keeps the extra fields (actual_qps, min_k) for downstream
    # analysis even though the rendered table omits them — strip nothing.
    fields = ["zone", "scenario", "actual_total_qps"] + \
             [f"k{k}_accuracy_pct" for k in k_values] + \
             [f"k{k}_ci99_margin_pct" for k in k_values] + \
             ["min_k_99_accuracy"]
    with open_output_text(path, newline="") as h:
        w = csv.writer(h)
        w.writerow(fields)
        for row in rows:
            w.writerow(
                [row.zone, row.scenario, row.actual_total_qps]
                + [
                    row.by_k[k].accuracy_pct if k in row.by_k else None
                    for k in k_values
                ]
                + [
                    row.by_k[k].ci99_margin_pct if k in row.by_k else None
                    for k in k_values
                ]
                + [csv_min_k(row)]
            )


def csv_min_k(row: ComparisonRow) -> object:
    value = row.min_k_99_accuracy
    if value is None:
        return None
    if row.is_diff_row:
        return ("+" if value > 0 else "") + str(value) if value != 0 else "0"
    return value


# ---- PGF / TikZ output (modeled on the unified Table 1 renderer) ----------

def write_pgf(path: str,
              rows: Sequence[ComparisonRow],
              k_values: Sequence[int],
              divider_after,  # int or List[int]; -1 disables
              row_groups: Optional[Sequence[Tuple[int, int, str]]] = None,
              log_floor: float = 0.01,
              ci_show: bool = True) -> Tuple[object, object, List[float], List[float], float]:
    """Emit a TikZ/PGF heatmap and return the (cmap, norm, col_widths, col_lefts,
    total_width) so PNG/PDF can be rendered with the same geometry."""
    finite_errors: List[float] = []
    for row in rows:
        for k in k_values:
            cell = row.by_k.get(k)
            if cell is None:
                continue
            finite_errors.append(max(log_floor, 100.0 - cell.accuracy_pct))
    cmap, norm = build_color_norm(finite_errors, log_floor, gamma=0.5)

    # Column geometry: zone + scenario + one cell per k. The original
    # "Conc. Reads Per Second" and "@ ≥99% acc"/min_k columns are
    # intentionally dropped — this comparison table focuses on accuracy.
    # col 0 = CPU Load, col 1 = Zone, then one cell per k.
    # load_col_w matches the unified Table 1's CPU Load column (load_col_width
    # = 0.46 in the policy accuracy heatmap); both PGFs use x=2.0cm, so equal
    # x-units == equal physical width. The narrower column needs a two-line
    # "CPU / Load" header (below) to fit.
    load_col_w = 0.46
    # Wide enough to hold the cross-zone label "us-west3 <-> us-east1" on one
    # line at 6.5pt (a two-line label would overflow the fixed row height).
    zone_col_w = 1.30
    heat_col_w = 0.48
    col_widths = [load_col_w, zone_col_w] + [heat_col_w] * len(k_values)
    col_lefts = [0.0]
    for w in col_widths[:-1]:
        col_lefts.append(col_lefts[-1] + w)
    total_width = sum(col_widths)

    # PGF assembly.
    lines = [
        "% Auto-generated by src/renderers/cross_zone_comparison.py.",
        "% Requires \\usepackage{tikz}. Include with \\input{...} or",
        "% \\noindent\\resizebox{\\linewidth}{!}{\\input{...}}.",
        r"\begin{tikzpicture}[x=2.0cm,y=0.37cm]",
        r"\definecolor{rlsOuter}{RGB}{34,34,34}",
        r"\definecolor{rlsText}{RGB}{17,17,17}",
        r"\definecolor{rlsHeaderText}{RGB}{34,34,34}",
        r"\definecolor{rlsWhite}{RGB}{255,255,255}",
    ]

    # Top supra-header band over the k cells.
    k_col_left = col_lefts[2]
    k_col_right = col_lefts[1 + len(k_values)] + col_widths[1 + len(k_values)]
    tikz_node(lines, (k_col_left + k_col_right) / 2.0, -0.5,
              r"\textbf{$k$ (Queries Per Probe Type)}",
              r"\fontsize{6.5}{6.8}\selectfont", "rlsHeaderText")
    # Side header cells (CPU Load, Zone).
    tikz_node(lines, col_lefts[0] + col_widths[0] / 2.0, 0.0,
              r"\textbf{CPU}\\\textbf{Load}",
              r"\fontsize{6.5}{6.8}\selectfont", "rlsHeaderText")
    tikz_node(lines, col_lefts[1] + col_widths[1] / 2.0, 0.0,
              r"\textbf{Zone}",
              r"\fontsize{6.5}{6.8}\selectfont", "rlsHeaderText")
    # k sub-headers.
    for i, k in enumerate(k_values):
        left = col_lefts[i + 2]
        w = col_widths[i + 2]
        tikz_node(lines, left + w / 2.0, 0.5, latex_escape(str(k)),
                  r"\fontsize{5.6}{6.0}\selectfont", "rlsHeaderText")

    # CPU Load column (merged across each row group, centered vertically).
    # If row_groups is None, fall back to per-row rendering (one label per row).
    rows_in_groups: set = set()
    if row_groups:
        for start_idx, end_idx, label in row_groups:
            y_center = float(start_idx + 1) + (end_idx - start_idx + 1) / 2.0
            tikz_node(lines, col_lefts[0] + col_widths[0] / 2.0, y_center,
                      latex_escape(label),
                      r"\fontsize{6.5}{7.0}\selectfont", "rlsText")
            for i in range(start_idx, end_idx + 1):
                rows_in_groups.add(i)

    # Data rows.
    color_idx = 0
    for row_idx, row in enumerate(rows):
        y = float(row_idx + 1)
        is_diff = row.is_diff_row
        zone_font = (r"\fontsize{6.5}{7.0}\bfseries\selectfont"
                     if is_diff else r"\fontsize{6.5}{7.0}\selectfont")
        if row_idx not in rows_in_groups:
            # Not in any merged group; render the per-row CPU Load label.
            tikz_node(lines, col_lefts[0] + col_widths[0] / 2.0, y + 0.5,
                      latex_escape(scenario_display(row.scenario)),
                      r"\fontsize{6.5}{7.0}\selectfont", "rlsText")
        tikz_node(lines, col_lefts[1] + col_widths[1] / 2.0, y + 0.5,
                  zone_display(row.zone, pgf=True),
                  zone_font, "rlsText")
        for i, k in enumerate(k_values):
            left = col_lefts[i + 2]
            w = col_widths[i + 2]
            cell = row.by_k.get(k)
            if cell is None:
                tikz_rect(lines, left, y, w, 1.0, "rlsWhite")
                tikz_node(lines, left + w / 2.0, y + 0.5, "-", CELL_FONT_PGF, "rlsText")
                continue
            if is_diff:
                # Δ row: no fill, signed number, combined ± CI underneath.
                tikz_rect(lines, left, y, w, 1.0, "rlsWhite")
                delta = cell.accuracy_pct
                sign = "$-$" if delta < 0 else (r"$+$" if delta > 0 else r"$\pm$")
                if ci_show and cell.ci99_margin_pct is not None:
                    cell_text = (
                        rf"{sign}{abs(delta):.2f}"
                        rf"{{\fontsize{{4.3}}{{4.7}}\selectfont\,$\pm${fmt_ci(cell.ci99_margin_pct)}}}"
                    )
                else:
                    cell_text = rf"{sign}{abs(delta):.2f}"
                tikz_node(lines, left + w / 2.0, y + 0.5, cell_text, CELL_FONT_PGF, "rlsText")
                continue
            color_idx = pgf_draw_heatmap_cell(
                lines, left, y, w, cell.accuracy_pct, cell.ci99_margin_pct,
                cmap, norm, color_idx, log_floor, ci_show,
            )

    # Horizontal rules.
    tikz_hrule(lines, -1.0, 0.0, total_width, "0.7pt")
    tikz_hrule(lines, 0.0, k_col_left, k_col_right, "0.4pt")
    tikz_hrule(lines, 1.0, 0.0, total_width, "0.4pt")
    _divs = divider_after if isinstance(divider_after, (list, tuple)) else [divider_after]
    for d in _divs:
        if isinstance(d, int) and 0 < d < len(rows):
            tikz_hrule(lines, float(d + 1), 0.0, total_width, "0.5pt")
    tikz_hrule(lines, float(len(rows) + 1), 0.0, total_width, "0.7pt")

    lines.append(r"\end{tikzpicture}")
    write_text(path, "\n".join(lines) + "\n")
    return cmap, norm, col_widths, col_lefts, total_width


def write_matplotlib(path_prefix: str,
                     rows: Sequence[ComparisonRow],
                     k_values: Sequence[int],
                     divider_after,  # int or List[int]
                     cmap, norm,
                     col_widths: Sequence[float],
                     col_lefts: Sequence[float],
                     total_width: float,
                     row_groups: Optional[Sequence[Tuple[int, int, str]]] = None,
                     log_floor: float = 0.01) -> None:
    """Render PNG/PDF that visually matches the PGF layout."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    row_height = 0.27
    fig_height = max(4.0, 0.72 + (len(rows) + 2) * row_height)
    fig_width = max(6.5, total_width * 1.05)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.set_xlim(0, total_width)
    ax.set_ylim(len(rows) + 1, -1)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Top supra-header over the k cells (no right-side "@ ≥99% acc" band).
    k_col_left = col_lefts[2]
    k_col_right = col_lefts[1 + len(k_values)] + col_widths[1 + len(k_values)]
    ax.text((k_col_left + k_col_right) / 2.0, -0.5,
            "$k$ (Queries Per Probe Type)",
            ha="center", va="center", fontsize=8, weight="bold")
    ax.text(col_lefts[0] + col_widths[0] / 2.0, 0.0, "CPU\nLoad",
            ha="center", va="center", fontsize=8, weight="bold")
    ax.text(col_lefts[1] + col_widths[1] / 2.0, 0.0, "Zone",
            ha="center", va="center", fontsize=8, weight="bold")
    for i, k in enumerate(k_values):
        left = col_lefts[i + 2]
        w = col_widths[i + 2]
        ax.text(left + w / 2.0, 0.5, str(k),
                ha="center", va="center", fontsize=8, weight="bold")

    # CPU Load column merged across each row group; render once, centered.
    rows_in_groups: set = set()
    if row_groups:
        for start_idx, end_idx, label in row_groups:
            y_center = float(start_idx + 1) + (end_idx - start_idx + 1) / 2.0
            ax.text(col_lefts[0] + col_widths[0] / 2.0, y_center,
                    label, ha="center", va="center", fontsize=8)
            for i in range(start_idx, end_idx + 1):
                rows_in_groups.add(i)

    for row_idx, row in enumerate(rows):
        y = float(row_idx + 1)
        is_diff = row.is_diff_row
        zone_weight = "bold" if is_diff else "normal"
        if row_idx not in rows_in_groups:
            ax.text(col_lefts[0] + col_widths[0] / 2.0, y + 0.5,
                    scenario_display(row.scenario),
                    ha="center", va="center", fontsize=7)
        ax.text(col_lefts[1] + col_widths[1] / 2.0, y + 0.5,
                zone_display(row.zone), ha="center", va="center",
                fontsize=8 if is_diff else 7, weight=zone_weight)
        for i, k in enumerate(k_values):
            left = col_lefts[i + 2]
            w = col_widths[i + 2]
            cell = row.by_k.get(k)
            if cell is None:
                ax.add_patch(Rectangle((left, y), w, 1.0,
                                       facecolor="white", edgecolor="none"))
                ax.text(left + w / 2.0, y + 0.5, "-",
                        ha="center", va="center", fontsize=7)
                continue
            if is_diff:
                ax.add_patch(Rectangle((left, y), w, 1.0,
                                       facecolor="white", edgecolor="none"))
                delta = cell.accuracy_pct
                sign = "−" if delta < 0 else ("+" if delta > 0 else "±")
                ax.text(left + w / 2.0, y + 0.5,
                        f"{sign}{abs(delta):.2f}",
                        ha="right", va="center", fontsize=CELL_FONTSIZE_MPL,
                        color="#111111", weight="bold")
                if cell.ci99_margin_pct is not None:
                    ax.text(left + w / 2.0 + w * 0.02, y + 0.56,
                            f"±{fmt_ci(cell.ci99_margin_pct)}",
                            ha="left", va="center", fontsize=CI_FONTSIZE_MPL,
                            color="#444444")
                continue
            mpl_draw_heatmap_cell(
                ax, left, y, w, cell.accuracy_pct, cell.ci99_margin_pct, cmap, norm, log_floor
            )

    # Rules.
    rule_color = "#222222"
    ax.plot([0, total_width], [-1.0, -1.0], color=rule_color, lw=1.1)
    ax.plot([k_col_left, k_col_right], [0.0, 0.0], color=rule_color, lw=0.7)
    ax.plot([0, total_width], [1.0, 1.0], color=rule_color, lw=0.7)
    _divs = divider_after if isinstance(divider_after, (list, tuple)) else [divider_after]
    for d in _divs:
        if isinstance(d, int) and 0 < d < len(rows):
            ax.plot([0, total_width], [float(d + 1)] * 2,
                    color=rule_color, lw=0.7, linestyle="-")
    ax.plot([0, total_width], [float(len(rows) + 1)] * 2,
            color=rule_color, lw=1.1)

    fig.tight_layout(pad=0.3)
    fig.savefig(f"{path_prefix}.png", dpi=220, bbox_inches="tight")
    fig.savefig(f"{path_prefix}.pdf", bbox_inches="tight")
    plt.close(fig)


# ---- Main ------------------------------------------------------------------

def build_diff_row(sz: ComparisonRow, cr: ComparisonRow,
                   k_values: Sequence[int]) -> ComparisonRow:
    """Synthesize a Δ (CR − SZ) row. Accuracy cells store the pp delta and
    the per-k ci99 fields hold the *combined* CI half-width (root-sum-square)
    so the table cell can show the appropriate uncertainty for the delta."""
    by_k: Dict[int, ComparisonCell] = {}
    for k in k_values:
        sz_cell = sz.by_k.get(k)
        cr_cell = cr.by_k.get(k)
        if sz_cell is None or cr_cell is None:
            continue
        sz_ci = sz_cell.ci99_margin_pct or 0.0
        cr_ci = cr_cell.ci99_margin_pct or 0.0
        by_k[k] = ComparisonCell(
            accuracy_pct=cr_cell.accuracy_pct - sz_cell.accuracy_pct,
            ci99_margin_pct=math.sqrt(sz_ci ** 2 + cr_ci ** 2),
        )
    min_k_delta = None
    if sz.min_k_99_accuracy is not None and cr.min_k_99_accuracy is not None:
        min_k_delta = cr.min_k_99_accuracy - sz.min_k_99_accuracy
    return ComparisonRow(
        zone="Δ",
        scenario="cross − same",
        actual_total_qps=None,
        by_k=by_k,
        min_k_99_accuracy=min_k_delta,
        is_diff_row=True,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cross-region-dir", required=True)
    p.add_argument("--same-zone-dir",
                   default="results/table1/samezone-baseline")
    p.add_argument("--k-values", default="1,2,4,8")
    p.add_argument("--same-zone-scenarios",
                   default="baseline,cpu50")
    p.add_argument("--cross-region-scenarios",
                   default="baseline,cpu50",
                   help="Cross-zone scenarios to read from --cross-region-dir.")
    p.add_argument("--output-prefix", default=None)
    p.add_argument("--mode", choices=("full", "baseline-diff", "paired-diff"),
                   default="paired-diff",
                   help="`full` = all scenarios from both zones, with one divider "
                        "between the same-zone block and the cross-zone block. "
                        "`baseline-diff` = three rows (SZ baseline, CR baseline, "
                        "Δ). `paired-diff` = three rows per matched scenario "
                        "(SZ scen, CR scen, Δ), in the order given by "
                        "--cross-region-scenarios.")
    p.add_argument("--no-ci-in-pgf", action="store_true",
                   help="Hide ± CI margins in the heatmap cells")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    k_values = [int(x) for x in args.k_values.split(",") if x.strip()]
    sz_scens = [s.strip() for s in args.same_zone_scenarios.split(",") if s.strip()]
    cr_scens = [s.strip() for s in args.cross_region_scenarios.split(",") if s.strip()]

    same_zone = collect_same_zone(args.same_zone_dir, sz_scens, k_values)
    cross_region = collect_cross_region(args.cross_region_dir, cr_scens, k_values)
    if not cross_region:
        print(f"ERROR: no cross-region scenarios found in {args.cross_region_dir}",
              file=sys.stderr)
        sys.exit(1)

    row_groups: List[Tuple[int, int, str]] = []
    divider_after: int | List[int]
    if args.mode == "baseline-diff":
        sz_base = next((r for r in same_zone if r.scenario == "baseline"), None)
        cr_base = next((r for r in cross_region if r.scenario == "baseline"), None)
        if sz_base is None or cr_base is None:
            print("ERROR: --mode baseline-diff requires a 'baseline' scenario in "
                  "both zones.", file=sys.stderr)
            sys.exit(1)
        all_rows = [sz_base, cr_base, build_diff_row(sz_base, cr_base, k_values)]
        row_groups = [(0, 2, scenario_display("baseline"))]
        divider_after = -1  # single triplet, no inter-block rule needed
    elif args.mode == "paired-diff":
        sz_by_scen = {r.scenario: r for r in same_zone}
        cr_by_scen = {r.scenario: r for r in cross_region}
        matched = [s for s in cr_scens if s in sz_by_scen and s in cr_by_scen]
        if not matched:
            print("ERROR: --mode paired-diff requires at least one scenario "
                  "present in both --same-zone-scenarios and "
                  "--cross-region-scenarios.", file=sys.stderr)
            sys.exit(1)
        all_rows = []
        divider_rows: List[int] = []
        for scen in matched:
            start_idx = len(all_rows)
            all_rows.append(sz_by_scen[scen])
            all_rows.append(cr_by_scen[scen])
            all_rows.append(build_diff_row(sz_by_scen[scen], cr_by_scen[scen], k_values))
            end_idx = len(all_rows) - 1
            row_groups.append((start_idx, end_idx, scenario_display(scen)))
            divider_rows.append(len(all_rows))
        # Last divider would double the bottom rule, so drop it.
        divider_after = divider_rows[:-1] if len(divider_rows) > 1 else -1
    else:
        all_rows = list(same_zone) + list(cross_region)
        divider_after = len(same_zone)

    prefix = args.output_prefix or os.path.join(args.cross_region_dir,
                                                "comparison_table")
    md_path = f"{prefix}.md"
    csv_path = f"{prefix}.csv"
    pgf_path = f"{prefix}.pgf"

    md = render_markdown(all_rows, k_values)
    md_header = [
        "# Cross-region vs same-zone Table 1 comparison",
        "",
        f"Mode: `{args.mode}`",
        f"Cross-region run: `{args.cross_region_dir}`",
        f"Same-zone run: `{args.same_zone_dir}`",
    ]
    if args.mode == "full":
        md_header.append(f"Same-zone scenarios: {', '.join(sz_scens)}")
    md_header.extend([
        "",
        "Single-zone scenarios used 100,000 probes per row; cross-region "
        "used 1,000. 99% Wilson CI half-widths are ~0.06% and ~0.6% "
        "respectively. The Δ row's CI half-width is the root-sum-square "
        "of the two source rows' CIs.",
        "",
        md,
        "",
    ])
    write_text(md_path, "\n".join(md_header))
    render_csv(csv_path, all_rows, k_values)

    cmap, norm, col_widths, col_lefts, total_width = write_pgf(
        pgf_path, all_rows, k_values, divider_after,
        row_groups=row_groups or None,
        ci_show=not args.no_ci_in_pgf,
    )
    write_matplotlib(prefix, all_rows, k_values, divider_after,
                     cmap, norm, col_widths, col_lefts, total_width,
                     row_groups=row_groups or None)

    print(f"wrote {md_path}", file=sys.stderr)
    print(f"wrote {csv_path}", file=sys.stderr)
    print(f"wrote {pgf_path}", file=sys.stderr)
    print(f"wrote {prefix}.png, {prefix}.pdf", file=sys.stderr)
    print()
    print(md)


if __name__ == "__main__":
    main()
