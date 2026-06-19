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

from typing import List, Optional, Sequence

from ..renderer_util.heatmap_table import (
    CELL_FONTSIZE_MPL,
    build_color_norm,
    fmt_cost_multiplier,
    fmt_min_k,
    fmt_qps,
    mpl_draw_heatmap_cell,
)

from .data import heatmap_ci_text, heatmap_value
from .core import (
    ACC_LABEL,
    CMIDRULE_KERN,
    FRONTIER_COLOR,
    PolicySpec,
    build_heatmap_layout,
    filter_load_keys,
    row_for_load,
    union_policy_load_keys,
)
from .pgf import write_unified_heatmap_pgf


def write_unified_heatmap(
    path_prefix: str,
    policy_specs: List[PolicySpec],
    log_floor: float,
    ci: str,
    show_99_frontier: bool = False,
    cpu_loads_filter: Optional[Sequence[str]] = None,
    heatmap_suffix: str = "_heatmap",
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    finite_errors: List[float] = []
    for spec in policy_specs:
        for row in spec.rows:
            for k in spec.k_values:
                value = heatmap_value(row, k, "accuracy")
                if value is None:
                    continue
                finite_errors.append(max(log_floor, 100.0 - float(value)))
    cmap, norm = build_color_norm(finite_errors, log_floor, gamma=0.3)

    layout = build_heatmap_layout(policy_specs)
    col_widths = layout.col_widths
    col_lefts = layout.col_lefts
    total_width = layout.total_width
    load_keys = filter_load_keys(union_policy_load_keys(policy_specs), cpu_loads_filter)
    num_loads = len(load_keys)

    row_height = 0.27 if ci != "none" else 0.22
    fig_height = max(5.0, 0.6 + ((num_loads + 3) * row_height))
    fig_width = max(8.0, total_width * 0.92)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.set_xlim(0, total_width)
    ax.set_ylim(num_loads + 1, -2)
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    rule_color = "#222222"

    ax.text(
        col_lefts[0] + (col_widths[0] / 2.0),
        -0.5,
        "CPU\nLoad",
        ha="center",
        va="center",
        fontsize=7.2,
        fontweight="semibold",
        color="#222222",
    )

    for spec in policy_specs:
        ax.text(
            (spec.block_left + spec.block_right) / 2.0,
            -1.5,
            f"{spec.label} Policy",
            ha="center",
            va="center",
            fontsize=7.2,
            fontweight="semibold",
            color="#222222",
        )

    for spec in policy_specs:
        if spec.show_qps:
            ax.text(
                (spec.qps_left + spec.qps_right) / 2.0,
                0.0,
                "Conc.\nReads\n/ Sec",
                ha="center",
                va="center",
                fontsize=5.6,
                fontweight="semibold",
                color="#222222",
            )
        ax.text(
            (spec.k_left + spec.k_right) / 2.0,
            -0.5,
            r"$k$ (Queries Per Probe Type)",
            ha="center",
            va="center",
            fontsize=7.2,
            fontweight="semibold",
            color="#222222",
        )
        if spec.show_summary and not spec.show_cost:
            ax.text(
                (spec.cost_left + spec.cost_right) / 2.0,
                0.0,
                "$k$ @ $\\geq$\n" + ACC_LABEL + "% acc\nWithin CI",
                ha="center",
                va="center",
                fontsize=6.4,
                fontweight="semibold",
                color="#222222",
            )
        elif spec.show_summary:
            ax.text(
                (spec.cost_left + spec.cost_right) / 2.0,
                -0.5,
                "@ ≥ " + ACC_LABEL + "% acc",
                ha="center",
                va="center",
                fontsize=6.4,
                fontweight="semibold",
                color="#222222",
            )

    for spec in policy_specs:
        for k_idx, k in enumerate(spec.k_values):
            col_idx = spec.k_start + k_idx
            ax.text(
                col_lefts[col_idx] + (col_widths[col_idx] / 2.0),
                0.5,
                str(k),
                ha="center",
                va="center",
                fontsize=6.0,
                color="#222222",
            )
        if spec.show_summary and spec.show_cost:
            ax.text(
                col_lefts[spec.min_k_col] + (col_widths[spec.min_k_col] / 2.0),
                0.5,
                r"$k$",
                ha="center",
                va="center",
                fontsize=6.2,
                fontweight="semibold",
                color="#222222",
            )
            ax.text(
                col_lefts[spec.cost_col] + (col_widths[spec.cost_col] / 2.0),
                0.5,
                "Queries\nPer Probe ($3k$)",
                ha="center",
                va="center",
                fontsize=5.4,
                fontweight="semibold",
                color="#222222",
            )

    for row_idx, load_key in enumerate(load_keys):
        y = row_idx + 1
        load_label: Optional[str] = None
        for spec in policy_specs:
            load_row = row_for_load(spec.rows, load_key)
            if load_row is not None:
                load_label = load_row.scenario
                break
        ax.text(
            col_lefts[0] + (col_widths[0] / 2.0),
            y + 0.5,
            load_label or load_key[0],
            ha="center",
            va="center",
            fontsize=6.0,
            color="#111111",
        )

        for spec in policy_specs:
            load_row = row_for_load(spec.rows, load_key)
            if spec.show_qps:
                qps_text = fmt_qps(load_row.actual_total_qps) if load_row is not None else "—"
                ax.text(
                    col_lefts[spec.qps_col] + (col_widths[spec.qps_col] / 2.0),
                    y + 0.5,
                    qps_text,
                    ha="center",
                    va="center",
                    fontsize=6.0,
                    color="#111111",
                )

            if load_row is None and spec.missing_message_lines:
                ax.text(
                    (spec.k_left + spec.k_right) / 2.0,
                    y + 0.5,
                    "\n".join(spec.missing_message_lines),
                    ha="center",
                    va="center",
                    fontsize=5.6,
                    color="#555555",
                    fontstyle="italic",
                )
            else:
                for k_idx, k in enumerate(spec.k_values):
                    col_idx = spec.k_start + k_idx
                    left = col_lefts[col_idx]
                    width = col_widths[col_idx]
                    value = heatmap_value(load_row, k, "accuracy") if load_row is not None else None
                    if value is None:
                        ax.add_patch(
                            Rectangle(
                                (left, y),
                                width,
                                1,
                                facecolor="white",
                                edgecolor="none",
                            )
                        )
                        ax.text(
                            left + width / 2.0,
                            y + 0.5,
                            "—",
                            ha="center",
                            va="center",
                            fontsize=CELL_FONTSIZE_MPL,
                            color="#666666",
                        )
                        continue
                    assert load_row is not None
                    ci_text = heatmap_ci_text(load_row, k, ci) if ci != "none" else ""
                    mpl_draw_heatmap_cell(
                        ax,
                        left,
                        y,
                        width,
                        float(value),
                        ci_text or None,
                        cmap,
                        norm,
                        log_floor,
                    )

            if spec.show_summary:
                min_k_label = (
                    fmt_min_k(load_row.min_k_99_accuracy)
                    if load_row is not None
                    else "—"
                )
                cost_label = (
                    fmt_cost_multiplier(load_row.query_cost_multiplier_at_min_k_99)
                    if load_row is not None
                    else "—"
                )
                ax.text(
                    col_lefts[spec.min_k_col] + (col_widths[spec.min_k_col] / 2.0),
                    y + 0.5,
                    min_k_label,
                    ha="center",
                    va="center",
                    fontsize=CELL_FONTSIZE_MPL,
                    color="#111111",
                )
                if spec.show_cost:
                    ax.text(
                        col_lefts[spec.cost_col] + (col_widths[spec.cost_col] / 2.0),
                        y + 0.5,
                        cost_label,
                        ha="center",
                        va="center",
                        fontsize=5.9,
                        color="#111111",
                    )

    if show_99_frontier:
        for spec in policy_specs:
            xs: List[float] = []
            ys: List[float] = []
            for row_idx, load_key in enumerate(load_keys):
                load_row = row_for_load(spec.rows, load_key)
                if load_row is None:
                    continue
                min_k_val = load_row.min_k_99_accuracy
                if min_k_val is None:
                    continue
                try:
                    k_idx = spec.k_values.index(min_k_val)
                except ValueError:
                    continue
                x = col_lefts[spec.k_start + k_idx]
                y_top = row_idx + 1
                y_bot = row_idx + 2
                xs.extend([x, x])
                ys.extend([y_top, y_bot])
            if xs:
                ax.plot(
                    xs,
                    ys,
                    color=FRONTIER_COLOR,
                    linewidth=2.0,
                    solid_capstyle="butt",
                    solid_joinstyle="miter",
                    zorder=5,
                )

    ax.plot([0, total_width], [-2, -2], color=rule_color, linewidth=0.9, solid_capstyle="butt")
    for spec in policy_specs:
        ax.plot(
            [spec.block_left + CMIDRULE_KERN, spec.block_right - CMIDRULE_KERN],
            [-1, -1],
            color=rule_color,
            linewidth=0.5,
            solid_capstyle="butt",
        )
    for spec in policy_specs:
        ax.plot(
            [spec.k_left + CMIDRULE_KERN, spec.k_right - CMIDRULE_KERN],
            [0, 0],
            color=rule_color,
            linewidth=0.4,
            solid_capstyle="butt",
        )
        if spec.show_summary and spec.show_cost:
            ax.plot(
                [spec.cost_left + CMIDRULE_KERN, spec.cost_right - CMIDRULE_KERN],
                [0, 0],
                color=rule_color,
                linewidth=0.4,
                solid_capstyle="butt",
            )

    last_idx = len(policy_specs) - 1
    for spec_idx, spec in enumerate(policy_specs):
        left = 0.0 if spec_idx == 0 else spec.block_left
        right = total_width if spec_idx == last_idx else spec.block_right
        ax.plot(
            [left, right],
            [1, 1],
            color=rule_color,
            linewidth=0.5,
            solid_capstyle="butt",
        )
    ax.plot(
        [0, total_width],
        [num_loads + 1, num_loads + 1],
        color=rule_color,
        linewidth=0.9,
        solid_capstyle="butt",
    )

    ax.margins(0, 0)
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    fig.savefig(f"{path_prefix}{heatmap_suffix}.png", dpi=240, bbox_inches="tight", pad_inches=0)
    fig.savefig(f"{path_prefix}{heatmap_suffix}.pdf", bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    write_unified_heatmap_pgf(
        f"{path_prefix}{heatmap_suffix}.pgf",
        policy_specs,
        load_keys,
        layout,
        log_floor,
        ci,
        cmap,
        norm,
        show_99_frontier=show_99_frontier,
    )
