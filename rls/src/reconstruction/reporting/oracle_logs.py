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
import threading
from dataclasses import dataclass
from types import TracebackType
from typing import Dict, Optional, Sequence, TextIO, cast

from reconstruction.candidates import CandidateSpec
from reconstruction.reporting.console import print_info
from reconstruction.truth import post_process_oracle_calls
from reconstruction.types import CsvWriter, DbConnection, DbValue, JsonValue, Summary


@dataclass
class LockedCsvLog:
    handle: TextIO
    writer: CsvWriter
    lock: Optional[threading.Lock]

    @classmethod
    def create(
        cls,
        path: str,
        header: Sequence[str],
        use_lock: bool,
    ) -> "LockedCsvLog":
        handle = open(path, "w", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        writer.writerow(header)
        return cls(handle, writer, threading.Lock() if use_lock else None)

    def writerow(self, row: Sequence[object]) -> None:
        if self.lock is not None:
            with self.lock:
                self.writer.writerow(row)
        else:
            self.writer.writerow(row)

    def close(self) -> None:
        self.handle.close()


@dataclass
class OracleLogs:
    oracle_log_path: Optional[str] = None
    attribute_log: Optional[LockedCsvLog] = None
    tuple_log: Optional[LockedCsvLog] = None

    @classmethod
    def create(cls, output_dir: str, enabled: bool, workers: int) -> "OracleLogs":
        if not enabled:
            return cls()

        oracle_log_path = os.path.join(
            output_dir, "reconstruction_oracle_calls_raw.csv"
        )
        use_lock = workers > 1
        attribute_log = LockedCsvLog.create(
            oracle_log_path,
            (
                "attribute",
                "low",
                "high",
                "span",
                "is_leaf",
                "candidate_ns",
                "baseline_ns",
                "buffer_ns",
                "guess",
            ),
            use_lock,
        )

        tuple_oracle_log_path = os.path.join(
            output_dir, "reconstruction_tuple_oracle_calls_raw.csv"
        )
        tuple_log = LockedCsvLog.create(
            tuple_oracle_log_path,
            (
                "step",
                "prefix",
                "subset_low",
                "subset_high",
                "subset_size",
                "candidate_ns",
                "baseline_ns",
                "buffer_ns",
                "observed_row",
                "hot",
            ),
            use_lock,
        )

        return cls(
            oracle_log_path=oracle_log_path,
            attribute_log=attribute_log,
            tuple_log=tuple_log,
        )

    def __enter__(self) -> "OracleLogs":
        return self

    def __exit__(
        self,
        _exc_type: Optional[type[BaseException]],
        _exc: Optional[BaseException],
        _tb: Optional[TracebackType],
    ) -> None:
        self.close()

    def close(self) -> None:
        if self.attribute_log is not None:
            self.attribute_log.close()
            self.attribute_log = None
        if self.tuple_log is not None:
            self.tuple_log.close()
            self.tuple_log = None

    def write_attribute_call(
        self,
        attr: str,
        low: DbValue,
        high: DbValue,
        span: int,
        is_leaf: bool,
        candidate_ns: int,
        baseline_ns: int,
        buffer_ns: int,
        guess: int,
    ) -> None:
        if self.attribute_log is None:
            return
        self.attribute_log.writerow(
            (
                attr,
                low,
                high,
                span,
                int(is_leaf),
                candidate_ns,
                baseline_ns,
                buffer_ns,
                guess,
            )
        )

    def write_tuple_call(
        self,
        step: int,
        prefix: Sequence[DbValue],
        subset_low: DbValue,
        subset_high: DbValue,
        subset_size: int,
        candidate_ns: int,
        baseline_ns: int,
        buffer_ns: int,
        observed_row: bool,
        hot: bool,
    ) -> None:
        if self.tuple_log is None:
            return
        self.tuple_log.writerow(
            (
                step,
                "|".join(str(part) for part in prefix),
                subset_low,
                subset_high,
                subset_size,
                candidate_ns,
                baseline_ns,
                buffer_ns,
                int(observed_row),
                int(hot),
            )
        )

    def add_summary_stats(
        self,
        summary: Summary,
        output_dir: str,
        admin: Optional[DbConnection],
        candidates: Dict[str, CandidateSpec],
        table: str,
        no_progress_output: bool,
    ) -> None:
        if self.oracle_log_path is None:
            return
        self.close()
        if not no_progress_output:
            print_info("Post-processing oracle calls against admin ground truth...")
        oracle_per_attr = post_process_oracle_calls(
            self.oracle_log_path, output_dir, admin, candidates, table
        )
        totals = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
        for stats in oracle_per_attr.values():
            for key in totals:
                totals[key] += stats[key]
        total_calls = totals["tp"] + totals["fp"] + totals["tn"] + totals["fn"]
        accuracy = (totals["tp"] + totals["tn"]) / total_calls if total_calls else None
        tpr = (
            totals["tp"] / (totals["tp"] + totals["fn"])
            if (totals["tp"] + totals["fn"])
            else None
        )
        tnr = (
            totals["tn"] / (totals["tn"] + totals["fp"])
            if (totals["tn"] + totals["fp"])
            else None
        )
        summary["oracle_call_stats_per_attr"] = cast(JsonValue, oracle_per_attr)
        summary["oracle_call_stats_total"] = cast(JsonValue, totals)
        summary["oracle_call_total"] = total_calls
        summary["oracle_call_accuracy"] = accuracy
        summary["oracle_call_tpr"] = tpr
        summary["oracle_call_tnr"] = tnr
