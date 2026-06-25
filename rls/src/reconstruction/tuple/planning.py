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

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Sequence, Tuple, cast

from reconstruction.candidates import CandidateSpec, RangeValues
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.sql.queries import build_query
from reconstruction.truth import CorrectnessStats
from reconstruction.tuple.calibration import TupleCalibrationState
from reconstruction.types import (
    ComparableValue,
    DbConnection,
    DbCursor,
    DbValue,
    SupportsValueAt,
)


TupleStrategy = Literal["binary", "in", "linear"]


@dataclass(frozen=True)
class TuplePlanningContext:
    execution: ReconstructionExecution
    cur: DbCursor
    admin: Optional[DbConnection]
    calibration: TupleCalibrationState
    tuple_attrs: Sequence[str]
    known_tuple: Dict[str, DbValue]
    verify_cur: Optional[DbCursor]
    tuple_step_stats: Dict[int, CorrectnessStats]
    verify_tuple_query: str


@dataclass(frozen=True)
class TupleStepPlan:
    step: int
    attrs: Sequence[str]
    next_attr: str
    strategy: TupleStrategy
    query: str
    threshold: int
    verify_query: Optional[str]
    verify_tuple_query: str
    total_candidates: int
    is_final_step: bool
    next_values: Sequence[DbValue] = ()
    search_values: Optional[SupportsValueAt] = None


def make_tuple_step_plan(
    context: TuplePlanningContext,
    current: Sequence[Tuple[DbValue, ...]],
    step: int,
) -> TupleStepPlan:
    execution = context.execution
    tuple_attrs = context.tuple_attrs
    attrs = tuple_attrs[:step]
    query, threshold = context.calibration.calibrate_threshold(
        execution, context.cur, context.admin, attrs, step, context.known_tuple
    )
    verify_query: Optional[str] = None
    if execution.verify and context.verify_cur:
        context.tuple_step_stats.setdefault(step, CorrectnessStats())
        verify_query = build_query(
            execution.args.table, attrs, select_expr="1", limit_clause=" LIMIT 1"
        )

    next_attr = attrs[-1]
    next_spec = execution.candidates.get(next_attr)
    if _uses_binary_strategy(next_spec):
        search_values = _require_search_values(next_attr, next_spec)
        value_count = len(search_values)
        return TupleStepPlan(
            step=step,
            attrs=attrs,
            next_attr=next_attr,
            strategy="binary",
            query=query,
            threshold=threshold,
            verify_query=verify_query,
            verify_tuple_query=context.verify_tuple_query,
            total_candidates=len(current) * value_count,
            is_final_step=step == len(tuple_attrs),
            search_values=search_values,
        )

    next_values = sorted_recovered_values(execution, next_attr)
    strategy: TupleStrategy = (
        "in" if next_spec is not None and next_spec.tuple_in else "linear"
    )
    return TupleStepPlan(
        step=step,
        attrs=attrs,
        next_attr=next_attr,
        strategy=strategy,
        query=query,
        threshold=threshold,
        verify_query=verify_query,
        verify_tuple_query=context.verify_tuple_query,
        total_candidates=len(current) * len(next_values),
        is_final_step=step == len(tuple_attrs),
        next_values=next_values,
    )


def sorted_recovered_values(
    execution: ReconstructionExecution, attr: str
) -> Sequence[DbValue]:
    values = execution.state.recovered.get(attr, [])
    return cast(
        List[DbValue],
        sorted(cast(List[ComparableValue], list(values))),
    )


def _uses_binary_strategy(next_spec: Optional[CandidateSpec]) -> bool:
    return next_spec is not None and next_spec.skip_probe and next_spec.binary_search


def _require_search_values(
    next_attr: str, next_spec: Optional[CandidateSpec]
) -> SupportsValueAt:
    if next_spec is None or next_spec.search_values is None:
        raise RuntimeError(
            f"binary_search enabled but no range/parts spec for {next_attr}"
        )
    search_values = next_spec.search_values
    if isinstance(search_values, RangeValues) and abs(search_values.step) != 1:
        raise RuntimeError(f"binary_search for {next_attr} requires step of 1 or -1")
    return search_values
