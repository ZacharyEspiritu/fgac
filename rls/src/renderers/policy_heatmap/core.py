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

from collections import OrderedDict
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .data import PolicyHeatmapRow, build_rows, load_scenarios


LoadKey = Tuple[str, Optional[float]]
PolicyRows = List[PolicyHeatmapRow]

CMIDRULE_KERN = 0.05
INTER_POLICY_GAP = 0.1
FRONTIER_COLOR = "#1565C0"
ACCURACY_TARGET_PCT = 99.9
ACC_LABEL = f"{ACCURACY_TARGET_PCT:g}"


@dataclass
class PolicySpec:
    name: str
    label: str
    rows: PolicyRows
    k_values: List[int]
    show_summary: bool
    show_qps: bool = True
    show_cost: bool = True
    missing_message_lines: Optional[List[str]] = None
    qps_col: int = -1
    k_start: int = 0
    k_end: int = 0
    min_k_col: int = -1
    cost_col: int = -1
    block_left: float = 0.0
    block_right: float = 0.0
    qps_left: float = 0.0
    qps_right: float = 0.0
    k_left: float = 0.0
    k_right: float = 0.0
    cost_left: float = 0.0
    cost_right: float = 0.0


@dataclass(frozen=True)
class HeatmapLayout:
    col_widths: List[float]
    col_lefts: List[float]
    total_width: float


def load_policy_data(
    policy_specs: Sequence[str],
    k_values: Sequence[int],
    queries_per_timetest: int,
) -> "OrderedDict[str, PolicyRows]":
    policy_data: "OrderedDict[str, PolicyRows]" = OrderedDict()
    for spec in policy_specs:
        name, sep, base_dir = spec.partition("=")
        if not sep or not name or not base_dir:
            raise SystemExit(f"--policy-dir expects NAME=PATH, got {spec!r}")
        scenarios = load_scenarios(base_dir, "", "")
        rows = build_rows(
            scenarios,
            name,
            k_values,
            queries_per_timetest,
            accuracy_target_pct=ACCURACY_TARGET_PCT,
            accuracy_within_ci=True,
            ci_margin_key="ci99_margin_pct",
        )
        policy_data[name] = rows
    return policy_data


def row_load_key(row: PolicyHeatmapRow) -> LoadKey:
    return (row.scenario, row.target_cpu_pct)


def union_policy_load_keys(policy_specs: Sequence[PolicySpec]) -> List[LoadKey]:
    seen: List[LoadKey] = []
    seen_set = set()
    for spec in policy_specs:
        for row in spec.rows:
            key = row_load_key(row)
            if key not in seen_set:
                seen.append(key)
                seen_set.add(key)
    return seen


def row_for_load(rows: Sequence[PolicyHeatmapRow], load_key: LoadKey) -> Optional[PolicyHeatmapRow]:
    for row in rows:
        if row_load_key(row) == load_key:
            return row
    return None


def filter_load_keys(load_keys: Sequence[LoadKey], cpu_loads_filter: Optional[Sequence[str]]) -> List[LoadKey]:
    if not cpu_loads_filter:
        return list(load_keys)

    def _strip(label: str) -> str:
        return label.strip().lower().rstrip("%").removeprefix("cpu")

    wanted: List[LoadKey] = []
    for raw in cpu_loads_filter:
        token = raw.strip()
        if not token:
            continue
        tok_norm = _strip(token)
        if tok_norm in ("base", "baseline", ""):
            match = next(
                (
                    key
                    for key in load_keys
                    if _strip(key[0]) in ("base", "baseline")
                    or (key[1] is not None and key[1] == 0.0)
                ),
                None,
            )
        else:
            try:
                target = float(tok_norm)
            except ValueError:
                raise SystemExit(
                    f"--cpu-loads token {token!r} is not 'Base' or a number"
                )
            match = next(
                (
                    key
                    for key in load_keys
                    if (key[1] is not None and abs(key[1] - target) < 1e-6)
                    or _strip(key[0]) == tok_norm
                ),
                None,
            )
        if match is None:
            raise SystemExit(
                f"--cpu-loads {token!r} is not present in any policy "
                f"(available: {[key[0] for key in load_keys]})"
            )
        if match not in wanted:
            wanted.append(match)
    return wanted


def build_heatmap_layout(policy_specs: Sequence[PolicySpec]) -> HeatmapLayout:
    load_col_width = 0.46
    qps_col_width = 0.56
    heat_col_width = 0.48
    min_k_col_width = 0.30
    cost_col_width = 0.72
    min_k_only_col_width = 0.52

    col_widths: List[float] = [load_col_width]
    next_col = 1
    for spec_idx, spec in enumerate(policy_specs):
        if spec_idx > 0:
            col_widths.append(INTER_POLICY_GAP)
            next_col += 1
        if spec.show_qps:
            spec.qps_col = next_col
            col_widths.append(qps_col_width)
            next_col += 1
        else:
            spec.qps_col = -1
        spec.k_start = next_col
        for _ in spec.k_values:
            col_widths.append(heat_col_width)
        spec.k_end = spec.k_start + len(spec.k_values)
        next_col = spec.k_end
        if spec.show_summary:
            spec.min_k_col = next_col
            col_widths.append(min_k_col_width if spec.show_cost else min_k_only_col_width)
            next_col += 1
            if spec.show_cost:
                spec.cost_col = next_col
                col_widths.append(cost_col_width)
                next_col += 1
            else:
                spec.cost_col = -1
        else:
            spec.min_k_col = -1
            spec.cost_col = -1

    col_lefts = [0.0]
    for width in col_widths[:-1]:
        col_lefts.append(col_lefts[-1] + width)
    total_width = sum(col_widths)

    for spec in policy_specs:
        spec.k_left = col_lefts[spec.k_start]
        spec.k_right = col_lefts[spec.k_end - 1] + col_widths[spec.k_end - 1]
        if spec.show_qps:
            spec.qps_left = col_lefts[spec.qps_col]
            spec.qps_right = col_lefts[spec.qps_col] + col_widths[spec.qps_col]
        else:
            spec.qps_left = spec.k_left
            spec.qps_right = spec.k_left
        if spec.show_summary:
            spec.cost_left = col_lefts[spec.min_k_col]
            if spec.show_cost:
                spec.cost_right = col_lefts[spec.cost_col] + col_widths[spec.cost_col]
            else:
                spec.cost_right = col_lefts[spec.min_k_col] + col_widths[spec.min_k_col]
        else:
            spec.cost_left = spec.k_right
            spec.cost_right = spec.k_right
        spec.block_left = spec.qps_left if spec.show_qps else spec.k_left
        spec.block_right = spec.cost_right if spec.show_summary else spec.k_right

    return HeatmapLayout(col_widths=col_widths, col_lefts=col_lefts, total_width=total_width)
