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
import sys
from typing import Dict, Sequence, cast

from reconstruction.truth import CorrectnessStats
from reconstruction.types import Summary
from util.io import write_json


def write_summary(output_dir: str, summary: Summary) -> None:
    write_json(os.path.join(output_dir, "reconstruction_summary.json"), summary)


def print_final_report(
    summary: Summary,
    tuple_attrs: Sequence[str],
    tuple_step_stats: Dict[int, CorrectnessStats],
    query_counts: Dict[str, int],
    stage_times: Dict[str, float],
    verify: bool,
) -> None:
    if verify:
        value_fp = summary.get("value_false_positives", 0)
        value_fn = summary.get("value_false_negatives", 0)
        tuple_fp = summary.get("tuple_false_positives", 0)
        tuple_fn = summary.get("tuple_false_negatives", 0)
        value_tp = summary.get("value_true_positives", 0)
        tuple_tp = summary.get("tuple_true_positives", 0)
        print(
            f"Value FP={value_fp} FN={value_fn} TP={value_tp} | "
            f"Tuple FP={tuple_fp} FN={tuple_fn} TP={tuple_tp}",
            file=sys.stderr,
        )
        per_attr_fp = _summary_int_map(summary, "value_false_positives_per_attr")
        per_attr_fn = _summary_int_map(summary, "value_false_negatives_per_attr")
        per_attr_tp = _summary_int_map(summary, "value_true_positives_per_attr")
        for attr in tuple_attrs:
            fp = per_attr_fp.get(attr, 0)
            fn = per_attr_fn.get(attr, 0)
            print(
                f"{attr}: FP={fp} FN={fn} TP={per_attr_tp.get(attr, 0)}",
                file=sys.stderr,
            )
        for step in sorted(tuple_step_stats.keys()):
            stats = tuple_step_stats[step]
            print(
                f"tuple_len={step}: TP={stats.tp} FP={stats.fp} "
                f"FN={stats.fn} total={stats.total}",
                file=sys.stderr,
            )

    if query_counts:
        print("Attacker query counts by stage:", file=sys.stderr)
        for key in sorted(query_counts.keys()):
            print(f"{key}={query_counts[key]}", file=sys.stderr)
        print(f"attacker_query_total={sum(query_counts.values())}", file=sys.stderr)
    if stage_times:
        print("Stage times (s):", file=sys.stderr)
        for key in sorted(stage_times.keys()):
            print(f"{key}={stage_times[key]:.3f}", file=sys.stderr)


def _summary_int_map(summary: Summary, key: str) -> Dict[str, int]:
    value = summary.get(key)
    if isinstance(value, dict):
        return cast(Dict[str, int], value)
    return {}
