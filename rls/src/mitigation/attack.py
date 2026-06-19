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

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from timing_oracle.core import (
    CalibrationSample,
    OracleProbeStats,
    OracleProbeTrial,
    add_calibration_stats,
    empty_calibration_stats,
    run_alternating_probe_trials,
    sample_calibration,
)


@dataclass
class AttackResult:
    config_name: str
    probes: int
    k_values: List[int]
    tp_by_k: Dict[int, int] = field(default_factory=dict)
    fp_by_k: Dict[int, int] = field(default_factory=dict)
    tn_by_k: Dict[int, int] = field(default_factory=dict)
    fn_by_k: Dict[int, int] = field(default_factory=dict)
    total_positive: int = 0
    total_negative: int = 0
    threshold_avg_by_k: Dict[int, float] = field(default_factory=dict)
    auth_min_avg_by_k: Dict[int, float] = field(default_factory=dict)
    nonexist_min_avg_by_k: Dict[int, float] = field(default_factory=dict)
    raw_timings: List[Tuple[str, str, int, int]] = field(default_factory=list)

    def accuracy_pct(self, k: int) -> float:
        tp = self.tp_by_k.get(k, 0)
        tn = self.tn_by_k.get(k, 0)
        return 100.0 * (tp + tn) / max(self.probes, 1)

    def tp_rate_pct(self, k: int) -> float:
        return (
            100.0 * self.tp_by_k.get(k, 0) / self.total_positive
            if self.total_positive
            else 0.0
        )

    def tn_rate_pct(self, k: int) -> float:
        return (
            100.0 * self.tn_by_k.get(k, 0) / self.total_negative
            if self.total_negative
            else 0.0
        )


def run_attack(
    *,
    config_name: str,
    cur,
    query: str,
    limit_params: Sequence,
    fetch_one_only: bool,
    warm_cache: bool,
    authorized_keys: Sequence,
    unauthorized_keys: Sequence,
    nonexistent_keys: Sequence,
    probes: int,
    k_values: Sequence[int],
    rng: random.Random,
) -> AttackResult:
    max_k = max(k_values)
    result = AttackResult(
        config_name=config_name,
        probes=probes,
        k_values=list(k_values),
    )
    oracle_stats = OracleProbeStats(k_values, probes)
    calibration_stats_by_k = {k: empty_calibration_stats() for k in k_values}
    latest_calibration: Optional[CalibrationSample] = None

    def calibrate_for_trial(_idx: int) -> None:
        nonlocal latest_calibration
        auth_key = authorized_keys[rng.randrange(len(authorized_keys))]
        nonexist_key = nonexistent_keys[rng.randrange(len(nonexistent_keys))]
        latest_calibration = sample_calibration(
            cur,
            query,
            limit_params,
            auth_key=auth_key,
            nonexist_key=nonexist_key,
            max_k=max_k,
            k_values=k_values,
            warm_cache=warm_cache,
            fetch_one_only=fetch_one_only,
        )
        add_calibration_stats(
            calibration_stats_by_k,
            latest_calibration.calibration_by_k,
        )

    def record_trial(trial: OracleProbeTrial) -> None:
        if latest_calibration is None:
            raise RuntimeError("oracle probe trial ran before calibration")
        oracle_stats.record_probe(
            trial.guess_mins,
            latest_calibration.calibration_by_k,
            trial.actually_exists,
        )
        result.raw_timings.append(
            (trial.label, str(trial.key), int(trial.guess_mins[-1]), max_k)
        )
        if trial.idx < 200 or trial.idx % 50 == 0:
            result.raw_timings.append(
                (
                    "auth_cal",
                    str(latest_calibration.auth_key),
                    int(latest_calibration.auth_prefix_mins[-1]),
                    max_k,
                )
            )
            result.raw_timings.append(
                (
                    "nonexist_cal",
                    str(latest_calibration.nonexist_key),
                    int(latest_calibration.nonexist_prefix_mins[-1]),
                    max_k,
                )
            )

    run_alternating_probe_trials(
        cur=cur,
        query=query,
        limit_params=limit_params,
        max_k=max_k,
        probes=probes,
        warm_cache=warm_cache,
        fetch_one_only=fetch_one_only,
        positive_key_for_idx=lambda _idx: unauthorized_keys[
            rng.randrange(len(unauthorized_keys))
        ],
        negative_key_for_idx=lambda _idx: nonexistent_keys[
            rng.randrange(len(nonexistent_keys))
        ],
        before_trial=calibrate_for_trial,
        on_trial=record_trial,
        progress_label=f"{config_name}_attack",
    )

    result.total_positive = oracle_stats.total_positive
    result.total_negative = oracle_stats.total_negative
    for k in k_values:
        counts = oracle_stats.stats_for(k)
        result.tp_by_k[k] = counts["tp"]
        result.fp_by_k[k] = counts["fp"]
        result.tn_by_k[k] = counts["tn"]
        result.fn_by_k[k] = counts["fn"]

    for k in k_values:
        stats = calibration_stats_by_k[k]
        count = max(stats["count"], 1.0)
        result.threshold_avg_by_k[k] = stats["threshold_sum"] / count
        result.auth_min_avg_by_k[k] = stats["auth_min_sum"] / count
        result.nonexist_min_avg_by_k[k] = stats["nonexist_min_sum"] / count

    return result


def capture_explain(
    cur,
    query: str,
    limit_params: Sequence,
    *,
    authorized_key,
    unauthorized_key,
    nonexistent_key,
) -> Dict[str, List[str]]:
    plans: Dict[str, List[str]] = {}
    for label, key in (
        ("authorized", authorized_key),
        ("unauthorized", unauthorized_key),
        ("nonexistent", nonexistent_key),
    ):
        try:
            cur.execute(f"EXPLAIN (ANALYZE, BUFFERS) {query}", (key, *limit_params))
            rows = [row[0] for row in cur.fetchall()]
        except Exception as exc:  # pragma: no cover - depends on live PG
            rows = [f"EXPLAIN failed: {exc}"]
        plans[label] = rows
    return plans
