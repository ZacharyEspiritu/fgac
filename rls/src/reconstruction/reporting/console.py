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

from collections.abc import Mapping, Sequence
from typing import Optional

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from reconstruction.truth import CorrectnessStats
from reconstruction.types import Summary


def _console(*, stderr: bool) -> Console:
    return Console(highlight=False, markup=False, width=120, stderr=stderr)


def _panel(
    title: str,
    renderable: RenderableType,
    *,
    border_style: str = "bright_blue",
) -> Panel:
    return Panel(
        renderable,
        title=title,
        border_style=border_style,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def _table(*, title: Optional[str] = None, expand: bool = True) -> Table:
    return Table(
        title=title,
        box=box.SIMPLE,
        border_style="bright_black",
        header_style="bold white",
        padding=(0, 1),
        expand=expand,
    )


def _format_optional_ratio(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def print_info(message: str) -> None:
    _console(stderr=True).print(Text(message, style="dim"))


def print_success(message: str) -> None:
    _console(stderr=True).print(Text(message, style="green"))


def print_parameters(values: Mapping[str, object]) -> None:
    table = _table()
    table.add_column("Parameter", style="bold cyan", no_wrap=True)
    table.add_column("Value", overflow="fold")
    for key in sorted(values):
        table.add_row(key, str(values[key]))
    _console(stderr=False).print(_panel("Reconstruction Parameters", table))


def print_final_report(
    summary: Summary,
    tuple_attrs: Sequence[str],
    tuple_step_stats: Mapping[int, CorrectnessStats],
    query_counts: Mapping[str, int],
    stage_times: Mapping[str, float],
    verify: bool,
) -> None:
    sections: list[RenderableType] = []
    if verify:
        sections.append(_verification_summary_table(summary))
        per_attr = _verification_per_attribute_table(summary, tuple_attrs)
        if per_attr is not None:
            sections.append(per_attr)
        per_tuple_len = _tuple_step_stats_table(tuple_step_stats)
        if per_tuple_len is not None:
            sections.append(per_tuple_len)

    if query_counts:
        sections.append(_query_counts_table(query_counts))
    if stage_times:
        sections.append(_stage_times_table(stage_times))

    if not sections:
        return

    _console(stderr=True).print(_panel("Reconstruction Summary", Group(*sections)))


def print_oracle_summary(
    *,
    total_calls: int,
    totals: Mapping[str, int],
    accuracy: Optional[float],
    tpr: Optional[float],
    tnr: Optional[float],
) -> None:
    table = _table()
    table.add_column("Metric", style="bold cyan", no_wrap=True)
    table.add_column("Value", justify="right")
    table.add_row("Total calls", f"{total_calls:,}")
    table.add_row("True positives", f"{totals.get('tp', 0):,}")
    table.add_row("False positives", f"{totals.get('fp', 0):,}")
    table.add_row("True negatives", f"{totals.get('tn', 0):,}")
    table.add_row("False negatives", f"{totals.get('fn', 0):,}")
    table.add_row("Accuracy", _format_optional_ratio(accuracy))
    table.add_row("TPR", _format_optional_ratio(tpr))
    table.add_row("TNR", _format_optional_ratio(tnr))
    _console(stderr=True).print(_panel("Oracle Call Audit", table))


def _verification_summary_table(summary: Summary) -> Table:
    table = _table(title="Verification Overview")
    table.add_column("Scope", style="bold cyan", no_wrap=True)
    table.add_column("TP", justify="right")
    table.add_column("FP", justify="right")
    table.add_column("FN", justify="right")
    table.add_row(
        "Values",
        _summary_int(summary, "value_true_positives"),
        _summary_int(summary, "value_false_positives"),
        _summary_int(summary, "value_false_negatives"),
    )
    table.add_row(
        "Tuples",
        _summary_int(summary, "tuple_true_positives"),
        _summary_int(summary, "tuple_false_positives"),
        _summary_int(summary, "tuple_false_negatives"),
    )
    return table


def _verification_per_attribute_table(
    summary: Summary,
    tuple_attrs: Sequence[str],
) -> Optional[Table]:
    if not tuple_attrs:
        return None
    per_attr_fp = _summary_int_map(summary, "value_false_positives_per_attr")
    per_attr_fn = _summary_int_map(summary, "value_false_negatives_per_attr")
    per_attr_tp = _summary_int_map(summary, "value_true_positives_per_attr")
    if not per_attr_fp and not per_attr_fn and not per_attr_tp:
        return None

    table = _table(title="Value Verification by Attribute")
    table.add_column("Attribute", style="bold cyan", no_wrap=True)
    table.add_column("TP", justify="right")
    table.add_column("FP", justify="right")
    table.add_column("FN", justify="right")
    for attr in tuple_attrs:
        table.add_row(
            attr,
            f"{per_attr_tp.get(attr, 0):,}",
            f"{per_attr_fp.get(attr, 0):,}",
            f"{per_attr_fn.get(attr, 0):,}",
        )
    return table


def _tuple_step_stats_table(
    tuple_step_stats: Mapping[int, CorrectnessStats],
) -> Optional[Table]:
    if not tuple_step_stats:
        return None
    table = _table(title="Tuple Verification by Length")
    table.add_column("Tuple length", style="bold cyan", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("FP", justify="right")
    table.add_column("FN", justify="right")
    table.add_column("Total", justify="right")
    for step in sorted(tuple_step_stats):
        stats = tuple_step_stats[step]
        table.add_row(
            str(step),
            f"{stats.tp:,}",
            f"{stats.fp:,}",
            f"{stats.fn:,}",
            f"{stats.total:,}",
        )
    return table


def _query_counts_table(query_counts: Mapping[str, int]) -> Table:
    table = _table(title="Attacker Query Counts")
    table.add_column("Stage", style="bold cyan", no_wrap=True)
    table.add_column("Attacker queries", justify="right")
    for key in sorted(query_counts):
        table.add_row(key, f"{query_counts[key]:,}")
    table.add_row("TOTAL", f"{sum(query_counts.values()):,}", style="bold")
    return table


def _stage_times_table(stage_times: Mapping[str, float]) -> Table:
    table = _table(title="Stage Times")
    table.add_column("Stage", style="bold cyan", no_wrap=True)
    table.add_column("Seconds", justify="right")
    for key in sorted(stage_times):
        table.add_row(key, f"{stage_times[key]:.3f}")
    return table


def _summary_int(summary: Summary, key: str) -> str:
    value = summary.get(key, 0)
    if isinstance(value, int):
        return f"{value:,}"
    return "0"


def _summary_int_map(summary: Summary, key: str) -> dict[str, int]:
    value = summary.get(key, {})
    if not isinstance(value, dict):
        return {}
    return {
        str(map_key): map_value
        for map_key, map_value in value.items()
        if isinstance(map_value, int)
    }
