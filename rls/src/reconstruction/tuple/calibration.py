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

import operator
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Sequence, Tuple, cast

from reconstruction.probing.query_runner import ProbeQueryRunner
from reconstruction.sql.queries import build_query, build_tuple_between_query
from reconstruction.types import ComparableValue, DbConnection, DbCursor, DbValue

if TYPE_CHECKING:
    from reconstruction.runtime.execution import ReconstructionExecution


@dataclass
class TupleCalibrationState:
    buffer: Dict[int, int] = field(default_factory=dict)
    query: Dict[int, str] = field(default_factory=dict)
    missing_params: Dict[int, Tuple[DbValue, ...]] = field(default_factory=dict)
    exists_params: Dict[int, Tuple[DbValue, ...]] = field(default_factory=dict)

    def calibrate_threshold(
        self,
        execution: ReconstructionExecution,
        cur: DbCursor,
        admin: DbConnection | None,
        attrs: Sequence[str],
        step: int,
        known_tuple: Dict[str, DbValue],
    ) -> Tuple[str, int]:
        warmup_for_step(execution, admin, attrs, known_tuple)
        tuple_mode = execution.args.tuple_extension_mode
        if tuple_mode == "between":
            query = build_tuple_between_query(
                execution.args.table,
                attrs[:-1],
                attrs[-1],
                execution.backend.param,
                "1",
                " LIMIT 1",
            )
            base_known = [known_tuple[attr] for attr in attrs[:-1]]
            missing_target = execution.known_values[attrs[-1]].missing
            known_target = known_tuple[attrs[-1]]
            missing_params = base_known + [missing_target, missing_target]
            exists_params = base_known + [known_target, known_target]
        else:
            query = build_query(execution.args.table, attrs, "1", " LIMIT 1")
            missing_params = [known_tuple[attr] for attr in attrs]
            missing_params[-1] = execution.known_values[attrs[-1]].missing
            exists_params = [known_tuple[attr] for attr in attrs]
        cal_start = time.perf_counter()
        runner = ProbeQueryRunner(
            execution.args.num_queries_for_initial_calibration,
            execution.state.query_counts,
            True,
        )
        rt_missing, _ = runner.min_with_match(
            cur,
            query,
            missing_params,
            label=f"tuple_cal_len:{step}",
        )
        rt_exists, _ = runner.min_with_match(
            cur,
            query,
            exists_params,
            label=f"tuple_cal_len:{step}",
        )
        threshold = int(rt_missing + (rt_exists - rt_missing) / 2)
        self.buffer[step] = max(int(rt_exists) - int(rt_missing), 0) // 2
        self.query[step] = query
        self.missing_params[step] = tuple(missing_params)
        self.exists_params[step] = tuple(exists_params)
        execution.state.stage_times[f"tuple_cal_len:{step}"] = (
            execution.state.stage_times.get(f"tuple_cal_len:{step}", 0.0)
            + (time.perf_counter() - cal_start)
        )
        return query, threshold


def warmup_for_step(
    execution: ReconstructionExecution,
    admin: DbConnection | None,
    attrs: Sequence[str],
    known_tuple: Dict[str, DbValue],
) -> None:
    if admin is None:
        return
    prefix_attrs = list(attrs[:-1])
    next_attr = attrs[-1]
    next_spec = execution.candidates.get(next_attr)
    if (
        next_spec is None
        or next_spec.search_values is None
        or len(next_spec.search_values) == 0
    ):
        return
    lo_bound = next_spec.search_values.value_at(0)
    hi_bound = next_spec.search_values.value_at(len(next_spec.search_values) - 1)
    lo_cmp = cast(ComparableValue, lo_bound)
    hi_cmp = cast(ComparableValue, hi_bound)
    if operator.gt(lo_cmp, hi_cmp):
        lo_bound, hi_bound = hi_bound, lo_bound
    where_parts = [f"{attr} = %s" for attr in prefix_attrs] + [
        f"{next_attr} BETWEEN %s AND %s"
    ]
    params = [known_tuple[attr] for attr in prefix_attrs] + [lo_bound, hi_bound]
    query = (
        f"SELECT count(*) FROM (SELECT 1 FROM {execution.args.table} "
        f"WHERE {' AND '.join(where_parts)}) t"
    )
    warm_start = time.perf_counter()
    with admin.cursor() as admin_cur:
        admin_cur.execute(query, params)
        admin_cur.fetchone()
    execution.state.stage_times[f"tuple_warmup_len:{len(attrs)}"] = (
        execution.state.stage_times.get(f"tuple_warmup_len:{len(attrs)}", 0.0)
        + (time.perf_counter() - warm_start)
    )
