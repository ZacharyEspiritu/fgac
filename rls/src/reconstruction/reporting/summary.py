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

from typing import Mapping, Optional, Sequence, cast

from reconstruction.candidates import CandidateSpec
from reconstruction.truth import CorrectnessStats
from reconstruction.types import CsvRow, DbValue, Summary


def build_reconstruction_summary(
    tuple_attrs: Sequence[str],
    candidates: Mapping[str, CandidateSpec],
    recovered: Mapping[str, Sequence[DbValue]],
    skipped_probe: Mapping[str, bool],
    tuple_threshold: int,
    tuple_tested_count: int,
    query_counts: Mapping[str, int],
    stage_times: Mapping[str, float],
    verify: bool,
    binary_verify_counts: Mapping[str, tuple[int, int, int]],
    sampled_verify_counts: Mapping[str, tuple[int, int, int]],
    values_rows: Sequence[CsvRow],
    tuple_stats: CorrectnessStats,
    tuple_step_stats: Mapping[int, CorrectnessStats],
) -> Summary:
    summary: dict[str, object] = {
        "attributes": list(tuple_attrs),
        "candidates": {attr: len(candidates[attr].values) for attr in tuple_attrs},
        "recovered": {attr: len(recovered.get(attr, [])) for attr in tuple_attrs},
        "skipped_probe": dict(sorted(skipped_probe.items())),
        "tuple_threshold_ns": tuple_threshold,
        "tuples_tested": tuple_tested_count,
        "attacker_query_counts": dict(sorted(query_counts.items())),
        "attacker_query_total": sum(query_counts.values()),
        "stage_times_s": dict(sorted(stage_times.items())),
    }
    if not verify:
        return cast(Summary, summary)

    per_attr_stats = {attr: CorrectnessStats() for attr in tuple_attrs}
    special_verify_counts = dict(binary_verify_counts)
    special_verify_counts.update(sampled_verify_counts)
    special_attrs = {attr for attr in tuple_attrs if attr in special_verify_counts}
    for attr in tuple_attrs:
        if attr in special_verify_counts:
            tp, fp, fn = special_verify_counts[attr]
            stats = per_attr_stats[attr]
            stats.tp = tp
            stats.fp = fp
            stats.fn = fn
            stats.total = tp + fp + fn
    for row in values_rows:
        attr = cast(str, row[0])
        if attr in special_attrs:
            continue
        guess = cast(int, row[4])
        truth = cast(Optional[int], row[5])
        if truth is None:
            continue
        per_attr_stats[attr].update(guess, truth)

    summary.update(
        {
            "value_false_positives": sum(stats.fp for stats in per_attr_stats.values()),
            "value_false_negatives": sum(stats.fn for stats in per_attr_stats.values()),
            "value_true_positives": sum(stats.tp for stats in per_attr_stats.values()),
            "value_false_positives_per_attr": {
                attr: stats.fp for attr, stats in per_attr_stats.items()
            },
            "value_false_negatives_per_attr": {
                attr: stats.fn for attr, stats in per_attr_stats.items()
            },
            "value_true_positives_per_attr": {
                attr: stats.tp for attr, stats in per_attr_stats.items()
            },
            "tuple_false_positives": tuple_stats.fp,
            "tuple_false_negatives": tuple_stats.fn,
            "tuple_true_positives": tuple_stats.tp,
            "tuple_step_stats": {
                str(step): {
                    "tp": stats.tp,
                    "fp": stats.fp,
                    "fn": stats.fn,
                    "total": stats.total,
                }
                for step, stats in sorted(tuple_step_stats.items())
            },
        }
    )
    return cast(Summary, summary)
