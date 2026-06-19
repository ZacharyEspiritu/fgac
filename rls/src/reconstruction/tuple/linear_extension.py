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

from typing import Iterator, Optional, Sequence, Tuple

from reconstruction.probing.linear import LinearProber
from reconstruction.truth import TupleWorkerResult
from reconstruction.tuple.truth_lookup import truth_for_combo
from reconstruction.tuple.worker_context import TupleStepRuntime
from reconstruction.types import DbCursor, DbValue


def run_linear_extension(step_runtime: TupleStepRuntime) -> None:
    execution = step_runtime.execution
    plan = step_runtime.plan

    def worker_body(
        prefixes: Sequence[Tuple[DbValue, ...]],
        worker_cur: DbCursor,
        verify_cur: Optional[DbCursor],
        state: TupleWorkerResult,
    ) -> None:
        for prefix in prefixes:
            values = plan.next_values
            if not values:
                continue

            def iter_combos() -> Iterator[Tuple[DbValue, ...]]:
                for value in values:
                    yield prefix + (value,)

            def run_combo(combo: Tuple[DbValue, ...]) -> int:
                rt, observed = step_runtime.query_runner.min_with_match(
                    worker_cur,
                    plan.query,
                    combo,
                    label=f"tuple_probe_len:{plan.step}",
                )
                if observed:
                    return 10**12
                return rt

            def on_value(
                combo: Tuple[DbValue, ...],
                min_rt: int,
                guess: int,
            ) -> None:
                truth = truth_for_combo(
                    execution,
                    plan.attrs,
                    combo,
                    verify_cur,
                    plan.verify_query,
                    plan.verify_tuple_query,
                    plan.is_final_step,
                )
                step_runtime.record_local(state, combo, min_rt, guess, truth)

            LinearProber[Tuple[DbValue, ...]]().probe(
                iter_combos(),
                plan.threshold,
                run_combo,
                on_value,
                step_runtime.tracker,
                lambda combo: ", ".join(str(item) for item in combo),
            )

    step_runtime.execute(worker_body)
