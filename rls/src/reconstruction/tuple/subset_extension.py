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

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple, cast

from reconstruction.probing.in_probe import InProber
from reconstruction.probing.progress import preview_subset
from reconstruction.sql.queries import build_tuple_between_query, build_tuple_in_any_query
from reconstruction.truth import TupleWorkerResult
from reconstruction.tuple.truth_lookup import (
    matching_values_for_prefix,
    truth_for_next_value,
)
from reconstruction.tuple.worker_context import TupleStepRuntime
from reconstruction.types import DbCursor, DbParams, DbValue


@dataclass
class TupleSubsetProbe:
    step_runtime: TupleStepRuntime
    worker_cur: DbCursor
    prefix: Tuple[DbValue, ...]
    query_cache: dict[str, str]
    candidate_ns: int = 0
    baseline_ns: int = 0

    @property
    def use_recompute(self) -> bool:
        return bool(self.step_runtime.execution.args.tuple_recompute_threshold)

    @property
    def use_tuple_baseline(self) -> bool:
        return not self.use_recompute

    def run(self, subset: Sequence[DbValue]) -> int:
        execution = self.step_runtime.execution
        plan = self.step_runtime.plan
        query, cand_params, base_params = self._query_and_params(subset)
        fresh_threshold, missing_cal_ns = self._fresh_threshold()

        cand_rt, observed = self.step_runtime.query_runner.min_with_match(
            self.worker_cur,
            query,
            cand_params,
            label=f"tuple_probe_len:{plan.step}",
        )
        self.candidate_ns = cand_rt
        self.baseline_ns = 0
        base_rt = 0
        if observed:
            hot = True
        elif self.use_recompute:
            base_rt = missing_cal_ns
            self.baseline_ns = missing_cal_ns
            hot = cand_rt > fresh_threshold
        else:
            tuple_buffer = self.step_runtime.calibration.buffer.get(plan.step, 0)
            base_rt, _ = self.step_runtime.query_runner.min_with_match(
                self.worker_cur,
                query,
                base_params,
                label=f"tuple_probe_baseline_len:{plan.step}",
            )
            self.baseline_ns = base_rt
            hot = (cand_rt - base_rt) > tuple_buffer

        log_buffer = (
            fresh_threshold
            if self.use_recompute
            else self.step_runtime.calibration.buffer.get(plan.step, 0)
        )
        execution.oracle_logs.write_tuple_call(
            plan.step,
            self.prefix,
            subset[0],
            subset[-1],
            len(subset),
            cand_rt,
            base_rt,
            log_buffer,
            observed,
            hot,
        )
        if hot:
            return 10**12
        return 0

    def _query_and_params(
        self, subset: Sequence[DbValue]
    ) -> Tuple[str, DbParams, DbParams]:
        execution = self.step_runtime.execution
        plan = self.step_runtime.plan
        attrs = plan.attrs
        next_attr = plan.next_attr
        prefix_params = tuple(self.prefix)
        missing_target_value = execution.known_values[next_attr].missing
        cand_params: DbParams
        base_params: DbParams

        if execution.args.tuple_extension_mode == "between":
            query = self.query_cache.get("between")
            if query is None:
                query = build_tuple_between_query(
                    execution.args.table,
                    attrs[:-1],
                    next_attr,
                    execution.backend.param,
                    "1",
                    " LIMIT 1",
                )
                self.query_cache["between"] = query
            cand_params = prefix_params + (subset[0], subset[-1])
            base_params = prefix_params + (
                missing_target_value,
                missing_target_value,
            )
            return query, cand_params, base_params

        query = self.query_cache.get("any")
        if query is None:
            query = build_tuple_in_any_query(
                execution.args.table,
                attrs[:-1],
                next_attr,
                execution.backend.param,
                "1",
                " LIMIT 1",
            )
            self.query_cache["any"] = query
        subset_list = list(subset)
        cand_params = cast(DbParams, prefix_params + (subset_list,))
        base_params = cast(DbParams, prefix_params + ([missing_target_value],))
        return query, cand_params, base_params

    def _fresh_threshold(self) -> Tuple[int, int]:
        if not self.use_recompute:
            return 0, 0

        plan = self.step_runtime.plan
        recompute_cal_query = self.step_runtime.calibration.query.get(plan.step)
        recompute_cal_missing = self.step_runtime.calibration.missing_params.get(
            plan.step
        )
        recompute_cal_exists = self.step_runtime.calibration.exists_params.get(
            plan.step
        )
        if (
            recompute_cal_query is None
            or recompute_cal_missing is None
            or recompute_cal_exists is None
        ):
            return 0, 0

        recompute_runner = self.step_runtime.query_runner.with_rounds(
            self.step_runtime.execution.args.tuple_recompute_cal_rounds
        )
        missing_ns, _ = recompute_runner.min_with_match(
            self.worker_cur,
            recompute_cal_query,
            recompute_cal_missing,
            label=f"tuple_recompute_cal_missing_len:{plan.step}",
        )
        exists_ns, _ = recompute_runner.min_with_match(
            self.worker_cur,
            recompute_cal_query,
            recompute_cal_exists,
            label=f"tuple_recompute_cal_exists_len:{plan.step}",
        )
        return (missing_ns + exists_ns) // 2, missing_ns


def run_subset_extension(step_runtime: TupleStepRuntime) -> None:
    execution = step_runtime.execution
    plan = step_runtime.plan
    attrs = plan.attrs
    next_attr = plan.next_attr
    if execution.backend.name != "postgres":
        raise RuntimeError("IN strategy with ANY() requires PostgreSQL")

    def worker_body(
        prefixes: Sequence[Tuple[DbValue, ...]],
        worker_cur: DbCursor,
        verify_cur: Optional[DbCursor],
        state: TupleWorkerResult,
    ) -> None:
        query_cache: dict[str, str] = {}
        verify_query_cache: dict[int, str] = {}
        for prefix in prefixes:
            values = plan.next_values
            if not values:
                continue
            prefix_params = tuple(prefix)
            subset_probe = TupleSubsetProbe(
                step_runtime,
                worker_cur,
                prefix,
                query_cache,
            )

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
                actual_rt = (
                    subset_probe.candidate_ns
                    if subset_probe.use_tuple_baseline
                    else min_rt
                )
                step_runtime.record_local(state, combo, actual_rt, guess, truth)

            def on_subset_cold(subset: Sequence[DbValue], min_rt: int) -> None:
                truth_values = matching_values_for_prefix(
                    execution, attrs, prefix, next_attr
                )
                if truth_values is not None:
                    fn_values = truth_values & set(subset) if truth_values else set()
                    step_runtime.record_cold_truth_values(
                        state, prefix, len(subset), fn_values, min_rt
                    )
                    return

                if not (execution.verify and verify_cur):
                    return
                verify_cached = verify_query_cache.get(0)
                if verify_cached is None:
                    verify_cached = build_tuple_in_any_query(
                        execution.args.table,
                        attrs[:-1],
                        next_attr,
                        execution.backend.param,
                        f"DISTINCT {next_attr}",
                        "",
                    )
                    verify_query_cache[0] = verify_cached
                verify_cur.execute(verify_cached, prefix_params + (list(subset),))
                truth_values = {row[0] for row in verify_cur.fetchall()}
                for value in subset:
                    combo = prefix + (value,)
                    truth = 1 if value in truth_values else 0
                    step_runtime.record_local(state, combo, min_rt, 0, truth)

            effective_threshold = (
                0 if subset_probe.use_tuple_baseline else plan.threshold
            )
            InProber[DbValue]().probe(
                values,
                effective_threshold,
                subset_probe.run,
                on_value,
                step_runtime.tracker,
                preview_subset,
                on_subset_cold=on_subset_cold,
                max_subset=None,
            )

    step_runtime.execute(worker_body)
