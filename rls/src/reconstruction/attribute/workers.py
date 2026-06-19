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
from typing import Callable, List, Optional, Sequence, TypeVar

from reconstruction.probing.parallel import (
    ProgressTracker,
    execute_db_workers,
    make_probe_step,
)
from reconstruction.probing.query_runner import ProbeQueryRunner
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.sql.db import value_exists
from reconstruction.types import CsvRow, DbCursor, DbValue


@dataclass
class AttributeProbeResult:
    recovered_values: List[DbValue] = field(default_factory=list)
    rows: List[CsvRow] = field(default_factory=list)

    def merge(self, other: "AttributeProbeResult") -> None:
        self.recovered_values.extend(other.recovered_values)
        self.rows.extend(other.rows)


WorkItem = TypeVar("WorkItem")


WorkerBody = Callable[[WorkItem, DbCursor, Optional[DbCursor]], AttributeProbeResult]


@dataclass(frozen=True)
class AttributeProbePlan:
    attr: str
    total_values: int
    progress_label: str


@dataclass(frozen=True)
class AttributeProbeRuntime:
    execution: ReconstructionExecution
    cur: DbCursor
    plan: AttributeProbePlan
    tracker: ProgressTracker
    query_runner: ProbeQueryRunner
    use_workers: bool

    @classmethod
    def create(
        cls,
        execution: ReconstructionExecution,
        cur: DbCursor,
        plan: AttributeProbePlan,
    ) -> "AttributeProbeRuntime":
        probe_step = make_probe_step(
            plan.total_values,
            plan.progress_label,
            execution.args.no_progress_output,
            execution.args.workers,
            execution.state.query_counts,
        )
        return cls(
            execution=execution,
            cur=cur,
            plan=plan,
            tracker=probe_step.tracker,
            query_runner=ProbeQueryRunner(
                execution.args.num_queries_per_probe,
                probe_step.counter,
                True,
            ),
            use_workers=probe_step.use_workers,
        )

    def run_workers(
        self,
        work_items: Sequence[WorkItem],
        worker_body: WorkerBody[WorkItem],
    ) -> AttributeProbeResult:
        args = self.execution.args
        aggregate = AttributeProbeResult()
        execute_db_workers(
            use_workers=self.use_workers,
            work_items=work_items,
            attacker_dsn=args.attacker_dsn,
            admin_dsn=args.admin_dsn,
            admin_enabled=self.execution.admin is not None,
            verify=self.execution.verify,
            inline_attacker_cur=self.cur,
            inline_admin=self.execution.admin,
            worker_body=worker_body,
            merge_fn=aggregate.merge,
        )
        return aggregate

    def record_value(
        self,
        result: AttributeProbeResult,
        verify_cur: Optional[DbCursor],
        value: DbValue,
        candidate_ns: int,
        threshold_ns: int,
        guess: int,
    ) -> None:
        truth = None
        if verify_cur:
            truth = int(
                value_exists(
                    verify_cur,
                    self.execution.args.table,
                    self.plan.attr,
                    value,
                )
            )
        result.rows.append(
            (self.plan.attr, value, candidate_ns, threshold_ns, guess, truth)
        )
        if guess:
            result.recovered_values.append(value)
