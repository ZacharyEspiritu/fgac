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
import json
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from ..renderer_util.heatmap_table import (
    ci_margin_pct,
    parse_float,
)


DEFAULT_K_VALUES = list(range(1, 11))
DEFAULT_ACCURACY_TARGET_PCT = 99.0
CI_Z = {
    95: 1.959963984540054,
    99: 2.5758293035489004,
}


@dataclass(frozen=True)
class Scenario:
    label: str
    output_dir: str
    target_cpu_pct: Optional[float]


@dataclass(frozen=True)
class PolicyKResult:
    probes: int
    tp_rate_pct: float
    tn_rate_pct: float
    accuracy_pct: float
    error_pct: float
    ci95_margin_pct: float
    ci99_margin_pct: float

    def ci_margin(self, key: str) -> float:
        if key == "ci95_margin_pct":
            return self.ci95_margin_pct
        if key == "ci99_margin_pct":
            return self.ci99_margin_pct
        raise ValueError(f"unknown CI margin key: {key}")


@dataclass(frozen=True)
class PolicyHeatmapRow:
    scenario: str
    target_cpu_pct: Optional[float]
    actual_total_qps: Optional[float]
    by_k: Dict[int, PolicyKResult]
    min_k_99_accuracy: Optional[int]
    query_cost_multiplier_at_min_k_99: Optional[int]


def load_scenarios(base_dir: str, manifest: str, scenario_labels: str) -> List[Scenario]:
    manifest_path = manifest or os.path.join(base_dir, "table1_noise_sweep.csv")
    scenarios: List[Scenario] = []
    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                label = row["label"].strip()
                output_dir = row.get("output_dir", "").strip() or os.path.join(base_dir, label)
                target_cpu_pct = parse_float(row.get("db_cpu_target_pct"))
                if target_cpu_pct == 0:
                    target_cpu_pct = None
                scenarios.append(
                    Scenario(
                        label=label,
                        output_dir=output_dir,
                        target_cpu_pct=target_cpu_pct,
                    )
                )
        return scenarios

    for label in [raw.strip() for raw in scenario_labels.split(",") if raw.strip()]:
        target_cpu_pct = None
        if label.startswith("cpu"):
            target_cpu_pct = parse_float(label.removeprefix("cpu"))
        scenarios.append(
            Scenario(
                label=label,
                output_dir=os.path.join(base_dir, label),
                target_cpu_pct=target_cpu_pct,
            )
        )
    return scenarios


def load_summary(path: str, policy: str) -> Dict[int, PolicyKResult]:
    by_k: Dict[int, PolicyKResult] = {}
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["policy"] != policy:
                continue
            k = int(row["k"])
            probes = int(row["probes"])
            accuracy_pct = float(row["accuracy_pct"])
            by_k[k] = PolicyKResult(
                probes=probes,
                tp_rate_pct=float(row["tp_rate_pct"]),
                tn_rate_pct=float(row["tn_rate_pct"]),
                accuracy_pct=accuracy_pct,
                error_pct=100.0 - accuracy_pct,
                ci95_margin_pct=ci_margin_pct(accuracy_pct, probes, CI_Z[95]),
                ci99_margin_pct=ci_margin_pct(accuracy_pct, probes, CI_Z[99]),
            )
    if not by_k:
        raise RuntimeError(f"No rows for policy={policy!r} in {path}")
    return by_k


def load_actual_total_qps(path: str) -> Optional[float]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return None
    return parse_float(payload.get("actual_total_qps"))


def fmt_label(label: str, target_cpu_pct: Optional[float]) -> str:
    if label == "baseline":
        return "Base"
    if target_cpu_pct is not None:
        return f"{target_cpu_pct:.0f}%"
    return label


def heatmap_value(row: PolicyHeatmapRow, k: int, metric: str) -> Optional[float]:
    result = row.by_k.get(k)
    if result is None:
        return None
    if metric == "error":
        return result.error_pct
    return result.accuracy_pct


def heatmap_ci_text(row: PolicyHeatmapRow, k: int, ci: str) -> str:
    if ci == "none":
        return ""
    result = row.by_k.get(k)
    return "" if result is None else f"± {result.ci99_margin_pct:.2f}"


def build_rows(
    scenarios: Iterable[Scenario],
    policy: str,
    k_values: Sequence[int],
    queries_per_timetest: int,
    accuracy_target_pct: float = DEFAULT_ACCURACY_TARGET_PCT,
    accuracy_within_ci: bool = False,
    ci_margin_key: str = "ci99_margin_pct",
) -> List[PolicyHeatmapRow]:
    rows: List[PolicyHeatmapRow] = []
    for scenario in scenarios:
        summary_path = os.path.join(scenario.output_dir, "table1_summary.csv")
        noise_path = os.path.join(scenario.output_dir, "table1_noise.json")
        if not os.path.exists(summary_path):
            raise RuntimeError(f"Missing summary file for {scenario.label}: {summary_path}")

        summary = load_summary(summary_path, policy)
        actual_total_qps = load_actual_total_qps(noise_path)
        min_k = None
        for k in k_values:
            result = summary.get(k)
            if result is None:
                continue
            reach = result.accuracy_pct
            if accuracy_within_ci:
                # Count k as reaching the target when the upper end of the CI does.
                reach += result.ci_margin(ci_margin_key)
            if reach >= accuracy_target_pct:
                min_k = k
                break
        rows.append(
            PolicyHeatmapRow(
                scenario=fmt_label(scenario.label, scenario.target_cpu_pct),
                target_cpu_pct=scenario.target_cpu_pct,
                actual_total_qps=actual_total_qps,
                by_k=summary,
                min_k_99_accuracy=min_k,
                query_cost_multiplier_at_min_k_99=(
                    None if min_k is None else min_k * queries_per_timetest
                ),
            )
        )
    return rows
