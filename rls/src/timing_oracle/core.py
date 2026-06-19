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

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from util.progress import ProgressBar
from util.timing import timed_query


def sample_prefix_mins(
    cur,
    query: str,
    params: Sequence,
    probes: int,
    warm_cache: bool,
    fetch_one_only: bool,
) -> List[int]:
    """Return running best-of-k timing minima for repeated executions."""
    if warm_cache:
        cur.execute(query, params)
        if fetch_one_only:
            cur.fetchone()
        else:
            cur.fetchall()
    best: Optional[int] = None
    mins: List[int] = []
    for _ in range(probes):
        elapsed_ns, _ = timed_query(cur, query, params, fetch_one=fetch_one_only)
        if best is None or elapsed_ns < best:
            best = elapsed_ns
        mins.append(int(best or 0))
    return mins


def measure_calibration_prefix_mins(
    cur,
    query: str,
    limit_params: Sequence,
    auth_key: object,
    nonexist_key: object,
    max_k: int,
    warm_cache: bool,
    fetch_one_only: bool,
) -> Tuple[List[int], List[int]]:
    """Sample authorized and nonexistent best-of-k prefix minima."""
    auth_prefix_mins = sample_prefix_mins(
        cur,
        query,
        (auth_key, *limit_params),
        max_k,
        warm_cache,
        fetch_one_only,
    )
    nonexist_prefix_mins = sample_prefix_mins(
        cur,
        query,
        (nonexist_key, *limit_params),
        max_k,
        warm_cache,
        fetch_one_only,
    )
    return auth_prefix_mins, nonexist_prefix_mins


@dataclass(frozen=True)
class CalibrationEntry:
    threshold: int
    auth_min: int
    nonexist_min: int
    nonexistent_slower: bool


@dataclass(frozen=True)
class CalibrationSample:
    auth_key: object
    nonexist_key: object
    auth_prefix_mins: List[int]
    nonexist_prefix_mins: List[int]
    calibration_by_k: Dict[int, CalibrationEntry]


@dataclass(frozen=True)
class OracleProbeTrial:
    idx: int
    label: str
    key: object
    actually_exists: bool
    guess_mins: List[int]


def calibration_entry(threshold: int, auth_min: int, nonexist_min: int) -> CalibrationEntry:
    """Assemble one per-k timing calibration record."""
    return CalibrationEntry(
        threshold=int(threshold),
        auth_min=int(auth_min),
        nonexist_min=int(nonexist_min),
        nonexistent_slower=nonexist_min > auth_min,
    )


def build_calibration_by_k(
    auth_prefix_mins: Sequence[int],
    nonexist_prefix_mins: Sequence[int],
    k_values: Sequence[int],
) -> Dict[int, CalibrationEntry]:
    calibration_by_k: Dict[int, CalibrationEntry] = {}
    for k in k_values:
        auth_min = int(auth_prefix_mins[k - 1])
        nonexist_min = int(nonexist_prefix_mins[k - 1])
        calibration_by_k[k] = calibration_entry(
            threshold=int((auth_min + nonexist_min) / 2.0),
            auth_min=auth_min,
            nonexist_min=nonexist_min,
        )
    return calibration_by_k


def sample_calibration(
    cur,
    query: str,
    limit_params: Sequence,
    *,
    auth_key: object,
    nonexist_key: object,
    max_k: int,
    k_values: Sequence[int],
    warm_cache: bool,
    fetch_one_only: bool,
) -> CalibrationSample:
    auth_prefix_mins, nonexist_prefix_mins = measure_calibration_prefix_mins(
        cur,
        query,
        limit_params,
        auth_key=auth_key,
        nonexist_key=nonexist_key,
        max_k=max_k,
        warm_cache=warm_cache,
        fetch_one_only=fetch_one_only,
    )
    return CalibrationSample(
        auth_key=auth_key,
        nonexist_key=nonexist_key,
        auth_prefix_mins=auth_prefix_mins,
        nonexist_prefix_mins=nonexist_prefix_mins,
        calibration_by_k=build_calibration_by_k(
            auth_prefix_mins,
            nonexist_prefix_mins,
            k_values,
        ),
    )


def run_alternating_probe_trials(
    *,
    cur,
    query: str,
    limit_params: Sequence,
    max_k: int,
    probes: int,
    warm_cache: bool,
    fetch_one_only: bool,
    positive_key_for_idx: Callable[[int], object],
    negative_key_for_idx: Callable[[int], object],
    before_trial: Callable[[int], None],
    on_trial: Callable[[OracleProbeTrial], None],
    progress_label: str,
    preview_for_trial: Optional[Callable[[OracleProbeTrial], str]] = None,
) -> None:
    progress = ProgressBar(probes, progress_label)
    for idx in range(probes):
        before_trial(idx)
        actually_exists = idx % 2 == 0
        key = positive_key_for_idx(idx) if actually_exists else negative_key_for_idx(idx)
        label = "unauthorized" if actually_exists else "nonexistent"
        guess_mins = sample_prefix_mins(
            cur,
            query,
            (key, *limit_params),
            max_k,
            warm_cache,
            fetch_one_only,
        )
        trial = OracleProbeTrial(
            idx=idx,
            label=label,
            key=key,
            actually_exists=actually_exists,
            guess_mins=guess_mins,
        )
        on_trial(trial)
        progress.update(
            idx + 1,
            preview=preview_for_trial(trial) if preview_for_trial else trial.label,
        )


def empty_calibration_stats() -> Dict[str, float]:
    return {
        "count": 0.0,
        "threshold_sum": 0.0,
        "auth_min_sum": 0.0,
        "nonexist_min_sum": 0.0,
        "nonexistent_slower_count": 0.0,
    }


def empty_confusion_counts() -> Dict[str, int]:
    return {"tp": 0, "fp": 0, "tn": 0, "fn": 0}


def oracle_predicts_exists(calibration: CalibrationEntry, guess_min: int) -> bool:
    """Apply a calibrated timing threshold to decide if a probed key exists."""
    if calibration.nonexistent_slower:
        return guess_min <= calibration.threshold
    return guess_min >= calibration.threshold


def record_classification(
    stats: Dict[str, int],
    predicted_exists: bool,
    actually_exists: bool,
) -> None:
    if predicted_exists:
        stats["tp" if actually_exists else "fp"] += 1
    else:
        stats["fn" if actually_exists else "tn"] += 1


@dataclass
class OracleProbeStats:
    """Track min-of-k oracle confusion counts for one probe stream."""

    k_values: Sequence[int]
    probes: int
    counts_by_k: Dict[int, Dict[str, int]] = field(init=False)
    total_positive: int = 0
    total_negative: int = 0

    def __post_init__(self) -> None:
        self.counts_by_k = {k: empty_confusion_counts() for k in self.k_values}

    def record_probe(
        self,
        guess_mins: Sequence[int],
        calibration_by_k: Dict[int, CalibrationEntry],
        actually_exists: bool,
    ) -> None:
        if actually_exists:
            self.total_positive += 1
        else:
            self.total_negative += 1

        for k in self.k_values:
            predicted_exists = oracle_predicts_exists(
                calibration_by_k[k],
                int(guess_mins[k - 1]),
            )
            record_classification(
                self.counts_by_k[k],
                predicted_exists,
                actually_exists,
            )

    def stats_for(self, k: int) -> Dict[str, int]:
        return self.counts_by_k[k]

    def accuracy_pct(self, k: int) -> float:
        stats = self.stats_for(k)
        return 100.0 * (stats["tp"] + stats["tn"]) / max(self.probes, 1)

    def tp_rate_pct(self, k: int) -> float:
        return (
            100.0 * self.stats_for(k)["tp"] / self.total_positive
            if self.total_positive
            else 0.0
        )

    def tn_rate_pct(self, k: int) -> float:
        return (
            100.0 * self.stats_for(k)["tn"] / self.total_negative
            if self.total_negative
            else 0.0
        )


def add_calibration_stats(
    calibration_stats_by_k: Dict[int, Dict[str, float]],
    calibration_by_k: Dict[int, CalibrationEntry],
) -> None:
    for k, calibration in calibration_by_k.items():
        stats = calibration_stats_by_k[k]
        stats["count"] += 1.0
        stats["threshold_sum"] += float(calibration.threshold)
        stats["auth_min_sum"] += float(calibration.auth_min)
        stats["nonexist_min_sum"] += float(calibration.nonexist_min)
        if calibration.nonexistent_slower:
            stats["nonexistent_slower_count"] += 1.0


def summarize_calibration_stats(
    calibration_stats_by_k: Dict[int, Dict[str, float]],
) -> Dict[int, CalibrationEntry]:
    summary: Dict[int, CalibrationEntry] = {}
    for k, stats in calibration_stats_by_k.items():
        count = max(stats["count"], 1.0)
        summary[k] = CalibrationEntry(
            threshold=int(round(stats["threshold_sum"] / count)),
            auth_min=int(round(stats["auth_min_sum"] / count)),
            nonexist_min=int(round(stats["nonexist_min_sum"] / count)),
            nonexistent_slower=stats["nonexistent_slower_count"] > (count / 2.0),
        )
    return summary
