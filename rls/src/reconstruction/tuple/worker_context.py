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

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol, Sequence, Tuple, cast

from reconstruction.probing.parallel import (
    ProgressTracker,
    chunk_list,
    execute_db_workers,
)
from reconstruction.probing.query_runner import ProbeQueryRunner
from reconstruction.truth import CorrectnessStats, TupleWorkerResult
from reconstruction.tuple.calibration import TupleCalibrationState
from reconstruction.tuple.planning import TupleStepPlan
from reconstruction.types import CsvWriter, DbCursor, DbValue


class TupleWorkerBody(Protocol):
    def __call__(
        self,
        prefixes: Sequence[Tuple[DbValue, ...]],
        worker_cur: DbCursor,
        verify_cur: Optional[DbCursor],
        state: TupleWorkerResult,
    ) -> None:
        ...


if TYPE_CHECKING:
    from reconstruction.runtime.execution import ReconstructionExecution


def should_write_tuple(guess: int, truth: Optional[int]) -> bool:
    return truth is not None and (guess == 1 or truth == 1)


class TupleOutputBuffer:
    def __init__(self, writer: CsvWriter, flush_size: int = 1000) -> None:
        self._writer = writer
        self._flush_size = flush_size
        self._buffer: list[Sequence[DbValue]] = []

    def write_row(
        self,
        row: Sequence[DbValue],
        guess: int,
        truth: Optional[int],
    ) -> None:
        if not should_write_tuple(guess, truth):
            return
        self._buffer.append(row)
        if len(self._buffer) >= self._flush_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        self._writer.writerows(self._buffer)
        self._buffer.clear()


@dataclass
class TupleStepRuntime:
    execution: ReconstructionExecution
    admin_present: bool
    current: List[Tuple[DbValue, ...]]
    cur: DbCursor
    verify_cur: Optional[DbCursor]
    use_workers: bool
    plan: TupleStepPlan
    tracker: ProgressTracker
    query_runner: ProbeQueryRunner
    calibration: TupleCalibrationState
    tuple_step_stats: Dict[int, CorrectnessStats]
    tuple_stats: CorrectnessStats
    output: TupleOutputBuffer
    next_current: List[Tuple[DbValue, ...]] = field(default_factory=list)
    tested_count: int = 0

    def record_local(
        self,
        state: TupleWorkerResult,
        combo: Tuple[DbValue, ...],
        min_rt: int,
        guess: int,
        truth: Optional[int],
    ) -> None:
        if truth is not None:
            state.step_stats.update(guess, truth)
            if self.plan.is_final_step:
                state.tuple_stats.update(guess, truth)
        if guess:
            state.next_prefixes.append(combo)
        if self.plan.is_final_step:
            state.tested_count += 1
            if should_write_tuple(guess, truth):
                state.rows.append((*combo, min_rt, self.plan.threshold, guess, truth))

    def merge_result(self, result: TupleWorkerResult) -> None:
        self.next_current.extend(result.next_prefixes)
        self.tested_count += result.tested_count
        if self.execution.verify:
            self.tuple_step_stats[self.plan.step].merge(result.step_stats)
            if self.plan.is_final_step:
                self.tuple_stats.merge(result.tuple_stats)
        for row in result.rows:
            self.output.write_row(
                row,
                cast(int, row[-2]),
                cast(Optional[int], row[-1]),
            )

    def record_cold_truth_values(
        self,
        state: TupleWorkerResult,
        prefix: Tuple[DbValue, ...],
        cold_count: int,
        fn_values: set[DbValue],
        min_rt: int,
    ) -> None:
        fn_count = len(fn_values)
        tn_count = cold_count - fn_count
        if fn_count > 0:
            state.step_stats.fn += fn_count
            state.step_stats.total += fn_count
            if self.plan.is_final_step:
                state.tuple_stats.fn += fn_count
                state.tested_count += fn_count
        if tn_count > 0:
            state.step_stats.total += tn_count
            if self.plan.is_final_step:
                state.tested_count += tn_count
        if self.plan.is_final_step and should_write_tuple(0, 1):
            for value in fn_values:
                combo = prefix + (value,)
                state.rows.append((*combo, min_rt, self.plan.threshold, 0, 1))

    def execute(self, worker_body: TupleWorkerBody) -> None:
        if not self.current:
            return
        work_items = (
            chunk_list(self.current, min(self.execution.args.workers, len(self.current)))
            if self.use_workers
            else [self.current]
        )

        def run_worker_body(
            prefixes: Sequence[Tuple[DbValue, ...]],
            worker_cur: DbCursor,
            verify_cur: Optional[DbCursor],
        ) -> TupleWorkerResult:
            state = TupleWorkerResult([], [], 0, CorrectnessStats(), CorrectnessStats())
            worker_body(prefixes, worker_cur, verify_cur, state)
            return state

        execute_db_workers(
            use_workers=self.use_workers,
            work_items=work_items,
            attacker_dsn=self.execution.args.attacker_dsn,
            admin_dsn=self.execution.args.admin_dsn,
            admin_enabled=self.admin_present,
            verify=self.execution.verify,
            inline_attacker_cur=self.cur,
            inline_verify_cur=self.verify_cur,
            worker_body=run_worker_body,
            merge_fn=self.merge_result,
        )
