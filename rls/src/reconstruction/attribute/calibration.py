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

import operator
import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple, cast

from reconstruction.candidates import CandidateSpec
from reconstruction.probing.query_runner import ProbeQueryRunner
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.sql.queries import build_query, build_range_query
from reconstruction.types import ComparableValue, DbCursor, DbValue
from util.sql_utils import validate_identifier


@dataclass(frozen=True)
class AttributeCalibration:
    threshold: int
    gap: int


def calibrate_attribute(
    runtime: ReconstructionExecution,
    cur: DbCursor,
    attr: str,
    spec: Optional[CandidateSpec],
) -> AttributeCalibration:
    args = runtime.args
    known_values = runtime.known_values
    state = runtime.state
    warm_attribute_index(runtime, attr, spec)

    # Match the calibration query shape to the probe query shape so the
    # cold/hot reference timings predict probe-time behavior. Binary-search
    # attributes use a point BETWEEN; the other strategies use equality.
    missing_params: Tuple[DbValue, ...]
    exists_params: Tuple[DbValue, ...]
    if spec is not None and spec.binary_search:
        query = build_range_query(
            args.table, attr, "1", " LIMIT 1"
        )
        missing_params = (
            known_values[attr].missing,
            known_values[attr].missing,
        )
        exists_params = (
            known_values[attr].exists,
            known_values[attr].exists,
        )
    else:
        query = build_query(args.table, [attr], "1", " LIMIT 1")
        missing_params = (known_values[attr].missing,)
        exists_params = (known_values[attr].exists,)

    if not args.no_progress_output:
        print(f"Calibrating {attr} threshold...", file=sys.stderr)
    start = time.perf_counter()
    runner = ProbeQueryRunner(
        args.num_queries_for_initial_calibration,
        state.query_counts,
        True,
    )
    rt_missing, _ = runner.min_with_match(
        cur,
        query,
        missing_params,
        label=f"attr_cal:{attr}",
    )
    rt_exists, _ = runner.min_with_match(
        cur,
        query,
        exists_params,
        label=f"attr_cal:{attr}",
    )
    state.stage_times[f"attr_cal:{attr}"] = (
        state.stage_times.get(f"attr_cal:{attr}", 0.0) + (time.perf_counter() - start)
    )
    return AttributeCalibration(
        threshold=int(rt_missing + (rt_exists - rt_missing) / 2),
        gap=max(int(rt_exists) - int(rt_missing), 0),
    )


def warm_attribute_index(
    runtime: ReconstructionExecution,
    attr: str,
    spec: Optional[CandidateSpec],
) -> None:
    args = runtime.args
    admin = runtime.admin
    if (
        admin is None
        or spec is None
        or spec.search_values is None
        or len(spec.search_values) == 0
    ):
        return

    validate_identifier(attr)
    lo_bound = spec.search_values.value_at(0)
    hi_bound = spec.search_values.value_at(len(spec.search_values) - 1)
    lo_cmp = cast(ComparableValue, lo_bound)
    hi_cmp = cast(ComparableValue, hi_bound)
    if operator.gt(lo_cmp, hi_cmp):
        lo_bound, hi_bound = hi_bound, lo_bound

    if not args.no_progress_output:
        print(f"Warming index for {attr} (admin)...", file=sys.stderr)
    start = time.perf_counter()
    with admin.cursor() as admin_cur:
        admin_cur.execute(
            f"SELECT count(*) FROM (SELECT 1 FROM {args.table} "
            f"WHERE {attr} BETWEEN %s AND %s) t",
            (lo_bound, hi_bound),
        )
        admin_cur.fetchone()
    runtime.state.stage_times[f"warmup:{attr}"] = (
        runtime.state.stage_times.get(f"warmup:{attr}", 0.0)
        + (time.perf_counter() - start)
    )
