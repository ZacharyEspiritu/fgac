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

import csv
import os
import statistics
from typing import Dict, List, Sequence, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .io import ensure_parent_dir


LABELS = ("nonexistent", "authorized", "unauthorized")
LATENCY_CLASS_SPECS = (
    ("nonexistent", r"\textit{nonexist}", r"\rowcolor{cyan!10}"),
    (
        "authorized",
        r"\textit{auth}",
        r"\rowcolor{orange!10}",
    ),
    (
        "unauthorized",
        r"\textit{unauth}",
        r"\rowcolor{green!10}",
    ),
)


def normalized_plot_output(plot_output: str, plot_format: str) -> str:
    if plot_format in ("pgf", "pdf"):
        root, _ext = os.path.splitext(plot_output)
        return f"{root}.{plot_format}"
    return plot_output


def summarize_latency_rows(
    rows: Sequence[Tuple],
    elapsed_index: int,
    labels: Sequence[str] = LABELS,
) -> dict:
    summary = {}
    for label in labels:
        values = [int(row[elapsed_index]) for row in rows if row[0] == label]
        summary[label] = (
            min(values),
            statistics.mean(values),
            statistics.pstdev(values),
        )
    return summary


def print_latency_summary(rows: Sequence[Tuple], elapsed_index: int) -> None:
    table = Table(
        box=box.SIMPLE,
        border_style="bright_black",
        header_style="bold white",
        padding=(0, 0),
        show_lines=False,
        expand=True,
    )
    table.add_column("Class", style="bold", no_wrap=True)
    table.add_column("Min (ns)", justify="right", no_wrap=True)
    table.add_column("Mean (ns)", justify="right", no_wrap=True)
    table.add_column("Std (ns)", justify="right", no_wrap=True)
    for label, (min_ns, mean_ns, std_ns) in summarize_latency_rows(rows, elapsed_index).items():
        table.add_row(label, f"{min_ns:.0f}", f"{mean_ns:.0f}", f"{std_ns:.0f}")

    Console(highlight=False, markup=False, width=120).print(
        Panel.fit(
            table,
            title="Timing Distribution Summary",
            title_align="left",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def read_latency_groups(path: str) -> Tuple[Dict[str, List[float]], List[str]]:
    by: Dict[str, List[float]] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            by.setdefault(row["query_type"], []).append(int(row["elapsed_ns"]) / 1000.0)
    return by, fieldnames


def _latency_stats(by: Dict[str, List[float]], label: str):
    values = by.get(label)
    if not values:
        return None
    return min(values), statistics.mean(values), statistics.pstdev(values)


def render_compact_latency_stats_table(by: Dict[str, List[float]]) -> str:
    rows_tex = []
    for label, tex_label, rowcolor in LATENCY_CLASS_SPECS:
        stats = _latency_stats(by, label)
        if stats is None:
            continue
        rows_tex.append(f"{rowcolor}{tex_label} & {stats[0]:.0f} & {stats[1]:.0f} & {stats[2]:.0f} \\\\")
    return (
        r"\begin{tabular}{|l rrr|}" + "\n"
        r"\toprule" + "\n"
        r"\textbf{Class} & \textbf{Min} ($\mu$s) & \textbf{Mean} ($\mu$s) & \textbf{Std} ($\mu$s) \\" + "\n"
        r"\midrule" + "\n"
        + "\n".join(rows_tex) + "\n"
        r"\bottomrule" + "\n"
        r"\end{tabular}" + "\n"
    )


def render_latency_distribution_plot(
    rows: Sequence[Tuple],
    *,
    elapsed_index: int,
    plot_output: str,
    plot_format: str,
    bins: int = 40,
    kind: str = "hist",
    pgf_figure_size=(3.4, 1.61),
    labels: Sequence[str] = LABELS,
) -> str:
    plot_output = normalized_plot_output(plot_output, plot_format)
    ensure_parent_dir(plot_output)

    import matplotlib
    import numpy as np

    matplotlib_backend = "Agg"
    if plot_format == "pgf":
        matplotlib_backend = "pgf"
    elif plot_format == "pdf":
        matplotlib_backend = "pdf"
    matplotlib.use(matplotlib_backend)
    if plot_format == "pgf":
        matplotlib.rcParams.update(
            {
                "figure.figsize": pgf_figure_size,
                "savefig.bbox": "tight",
                "font.family": "serif",
                "font.size": 9,
                "axes.labelsize": 9,
                "axes.titlesize": 9,
                "xtick.labelsize": 8,
                "ytick.labelsize": 8,
                "legend.fontsize": 8,
                "axes.spines.top": False,
                "axes.spines.right": False,
            }
        )
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    def darken(color, factor=0.6):
        r, g, b, _ = mcolors.to_rgba(color)
        return (r * factor, g * factor, b * factor, 1.0)

    fig_size = (8, 3.6225) if plot_format != "pgf" else (3.4, 1.66635)
    fig, ax = plt.subplots(figsize=fig_size)
    prop_colors = [p["color"] for p in plt.rcParams["axes.prop_cycle"]]
    per_label = {
        label: [int(row[elapsed_index]) / 1000.0 for row in rows if row[0] == label]
        for label in labels
    }
    all_values_us = [value for values in per_label.values() for value in values]
    high = float(np.percentile(all_values_us, 99)) if all_values_us else 1.0
    _, edges = np.histogram(all_values_us, bins=bins)
    kde_grid = np.linspace(0, high, 512) if kind == "kde" else None

    for i, label in enumerate(labels):
        values_us = per_label[label]
        if not values_us:
            continue
        color = prop_colors[i]
        if kind == "kde":
            if len(set(values_us)) < 2:
                continue
            from scipy.stats import gaussian_kde  # type: ignore[import-untyped]

            assert kde_grid is not None
            density = gaussian_kde(values_us)(kde_grid)
            ax.fill_between(kde_grid, density, alpha=0.45, color=color, label=label)
            ax.plot(kde_grid, density, color=darken(color), linewidth=0.9)
        else:
            counts, _ = np.histogram(values_us, bins=edges, density=True)
            ax.stairs(counts, edges, fill=True, alpha=0.45, color=color, label=label)
            ax.stairs(counts, edges, fill=False, color=darken(color), linewidth=0.9)
    if high > 0:
        ax.set_xlim(0, high)
    ax.set_xlabel(r"Client-observed RTT ($\mu$s)")
    ax.set_ylabel("Density")
    ax.yaxis.set_ticks([])
    fig.tight_layout()
    fig.savefig(plot_output, dpi=200)
    return plot_output
