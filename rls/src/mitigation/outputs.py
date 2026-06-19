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

import os
from typing import Dict, List, Optional, Sequence, Tuple

from mitigation.attack import AttackResult
from mitigation.configs import MitigationConfig
from util.io import write_csv, write_text


def write_markdown_summary(
    path: str,
    *,
    attribute: str,
    probes: int,
    k_values: Sequence[int],
    results: Sequence[Tuple[MitigationConfig, Optional[AttackResult], Optional[str]]],
) -> None:
    lines: List[str] = [
        f"# Join-Policy RLS Mitigation Sweep (attribute = `{attribute}`)",
        "",
        f"- probes per config: {probes}",
        f"- k values: {', '.join(str(k) for k in k_values)}",
        "",
        "Configurations:",
        "",
    ]
    for cfg in (entry[0] for entry in results):
        lines.append(f"- **{cfg.name}** -- {cfg.description}")
        lines.append(f"  - expected: {cfg.expected_outcome}")
    lines.append("")
    header = ["config"] + [f"k={k} Acc" for k in k_values] + [
        f"k={k} TP" for k in k_values
    ] + [f"k={k} TN" for k in k_values] + ["status"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for cfg, result, error in results:
        cells = [cfg.name]
        if result is None:
            cells.extend(["--"] * (3 * len(k_values)))
            cells.append(f"error: {error or 'unknown'}")
        else:
            for k in k_values:
                cells.append(f"{result.accuracy_pct(k):.2f}")
            for k in k_values:
                cells.append(f"{result.tp_rate_pct(k):.2f}")
            for k in k_values:
                cells.append(f"{result.tn_rate_pct(k):.2f}")
            cells.append("ok")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Plan-shape evidence (whether the policy lands as `Index Cond` vs "
        "`Filter`) is in each config's `explain.txt`. A mitigation has "
        "*closed* the channel when (a) accuracy collapses to ~50% and (b) "
        "the policy predicate appears inside the Index Cond, not as a "
        "separate Filter line."
    )
    write_text(path, "\n".join(lines) + "\n")


def write_per_config_outputs(
    config_dir: str,
    *,
    config: MitigationConfig,
    result: AttackResult,
    explain_plans: Dict[str, List[str]],
) -> None:
    os.makedirs(config_dir, exist_ok=True)

    accuracy_rows = []
    for k in result.k_values:
        accuracy_rows.append((
            config.name,
            k,
            result.probes,
            result.tp_by_k.get(k, 0),
            result.fp_by_k.get(k, 0),
            result.tn_by_k.get(k, 0),
            result.fn_by_k.get(k, 0),
            f"{result.tp_rate_pct(k):.4f}",
            f"{result.tn_rate_pct(k):.4f}",
            f"{result.accuracy_pct(k):.4f}",
            int(round(result.threshold_avg_by_k[k])),
            int(round(result.auth_min_avg_by_k[k])),
            int(round(result.nonexist_min_avg_by_k[k])),
        ))
    write_csv(
        os.path.join(config_dir, "accuracy.csv"),
        accuracy_rows,
        header=(
            "config",
            "k",
            "probes",
            "true_positive",
            "false_positive",
            "true_negative",
            "false_negative",
            "tp_rate_pct",
            "tn_rate_pct",
            "accuracy_pct",
            "threshold_ns_avg",
            "auth_min_ns_avg",
            "nonexist_min_ns_avg",
        ),
    )

    write_csv(
        os.path.join(config_dir, "timings.csv"),
        result.raw_timings,
        header=("class", "key", "min_of_k_ns", "k"),
    )

    explain_lines: List[str] = []
    for label, rows in explain_plans.items():
        explain_lines.append(f"=== {label} ===")
        explain_lines.extend(rows)
        explain_lines.append("")
    write_text(os.path.join(config_dir, "explain.txt"), "\n".join(explain_lines) + "\n")
