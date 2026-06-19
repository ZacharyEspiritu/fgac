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

from typing import TYPE_CHECKING, Optional, Sequence, Tuple

from reconstruction.types import DbCursor, DbValue

if TYPE_CHECKING:
    from reconstruction.runtime.execution import ReconstructionExecution


def matching_values_for_prefix(
    execution: ReconstructionExecution,
    attrs: Sequence[str],
    prefix: Tuple[DbValue, ...],
    next_attr: str,
) -> Optional[set[DbValue]]:
    if execution.ground_truth is None:
        return None
    return execution.ground_truth.matching_values_for_prefix(
        attrs[:-1], prefix, next_attr
    )


def truth_for_next_value(
    execution: ReconstructionExecution,
    attrs: Sequence[str],
    prefix: Tuple[DbValue, ...],
    next_attr: str,
    value: DbValue,
    combo: Tuple[DbValue, ...],
    verify_cur: Optional[DbCursor],
    verify_query: Optional[str],
    verify_tuple_query: str,
    is_final_step: bool,
) -> Optional[int]:
    truth_values = matching_values_for_prefix(execution, attrs, prefix, next_attr)
    if truth_values is not None:
        return 1 if value in truth_values else 0
    return truth_from_verification(
        execution,
        combo,
        verify_cur,
        verify_query,
        verify_tuple_query,
        is_final_step,
    )


def truth_for_combo(
    execution: ReconstructionExecution,
    attrs: Sequence[str],
    combo: Tuple[DbValue, ...],
    verify_cur: Optional[DbCursor],
    verify_query: Optional[str],
    verify_tuple_query: str,
    is_final_step: bool,
) -> Optional[int]:
    if execution.ground_truth is not None:
        return int(combo in execution.ground_truth.tuple_set(attrs))
    return truth_from_verification(
        execution,
        combo,
        verify_cur,
        verify_query,
        verify_tuple_query,
        is_final_step,
    )


def truth_from_verification(
    execution: ReconstructionExecution,
    combo: Tuple[DbValue, ...],
    verify_cur: Optional[DbCursor],
    verify_query: Optional[str],
    verify_tuple_query: str,
    is_final_step: bool,
) -> Optional[int]:
    if not (execution.verify and verify_cur):
        return None
    if is_final_step:
        verify_cur.execute(verify_tuple_query, combo)
    else:
        if verify_query is None:
            raise RuntimeError("verification query was not initialized")
        verify_cur.execute(verify_query, combo)
    return int(verify_cur.fetchone() is not None)
