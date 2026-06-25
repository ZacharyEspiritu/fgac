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

from typing import Optional, Sequence, Tuple

from reconstruction.probing.binary import BinaryProber
from reconstruction.probing.progress import make_range_preview
from reconstruction.sql.queries import build_tuple_range_query
from reconstruction.truth import TupleWorkerResult
from reconstruction.tuple.truth_lookup import (
    matching_values_for_prefix,
    truth_for_next_value,
)
from reconstruction.tuple.worker_context import TupleStepRuntime
from reconstruction.types import DbCursor, DbValue, SupportsValueAt


def run_binary_extension(step_runtime: TupleStepRuntime) -> None:
    execution = step_runtime.execution
    plan = step_runtime.plan
    attrs = plan.attrs
    next_attr = plan.next_attr
    maybe_search_values = plan.search_values
    if maybe_search_values is None:
        raise RuntimeError(
            f"binary_search enabled but no range/parts spec for {next_attr}"
        )
    search_values: SupportsValueAt = maybe_search_values

    range_query = build_tuple_range_query(
        execution.args.table,
        plan.attrs[:-1],
        next_attr,
        "1",
        " LIMIT 1",
    )

    def worker_body(
        prefixes: Sequence[Tuple[DbValue, ...]],
        worker_cur: DbCursor,
        verify_cur: Optional[DbCursor],
        state: TupleWorkerResult,
    ) -> None:
        verify_range_query: Optional[str] = None
        for prefix in prefixes:
            prefix_params = tuple(prefix)
            slice_values = search_values
            slice_total = len(slice_values)
            if slice_total == 0:
                continue

            def run_range(
                low: DbValue,
                high: DbValue,
                _lo: int,
                _hi: int,
            ) -> int:
                rt, observed = step_runtime.query_runner.min_with_match(
                    worker_cur,
                    range_query,
                    prefix_params + (low, high),
                    label=f"tuple_probe_len:{plan.step}",
                )
                if observed:
                    return 10**12
                return rt

            def on_value(
                value: DbValue,
                min_rt: int,
                guess: int,
            ) -> None:
                combo = prefix + (value,)
                truth = truth_for_next_value(
                    execution,
                    attrs,
                    prefix,
                    next_attr,
                    value,
                    combo,
                    verify_cur,
                    plan.verify_query,
                    plan.verify_tuple_query,
                    plan.is_final_step,
                )
                step_runtime.record_local(state, combo, min_rt, guess, truth)

            def on_range_cold(
                lo: int,
                hi: int,
                low: DbValue,
                high: DbValue,
                min_rt: int,
            ) -> None:
                truth_values = matching_values_for_prefix(
                    execution, attrs, prefix, next_attr
                )
                if truth_values is not None:
                    range_size = hi - lo + 1
                    if truth_values:
                        range_values = {
                            slice_values.value_at(idx) for idx in range(lo, hi + 1)
                        }
                        fn_values = truth_values & range_values
                    else:
                        fn_values = set()
                    step_runtime.record_cold_truth_values(
                        state, prefix, range_size, fn_values, min_rt
                    )
                    return

                if not (execution.verify and verify_cur):
                    return
                nonlocal verify_range_query
                if verify_range_query is None:
                    verify_range_query = build_tuple_range_query(
                        execution.args.table,
                        attrs[:-1],
                        next_attr,
                        f"DISTINCT {next_attr}",
                        "",
                    )
                verify_cur.execute(verify_range_query, prefix_params + (low, high))
                truth_values = {row[0] for row in verify_cur.fetchall()}
                for idx in range(lo, hi + 1):
                    value = slice_values.value_at(idx)
                    combo = prefix + (value,)
                    truth = 1 if value in truth_values else 0
                    step_runtime.record_local(state, combo, min_rt, 0, truth)

            preview_fn = make_range_preview(slice_total)

            BinaryProber().probe(
                slice_values,
                plan.threshold,
                run_range,
                on_value,
                step_runtime.tracker,
                preview_fn,
                on_range_cold=on_range_cold,
            )

    step_runtime.execute(worker_body)
