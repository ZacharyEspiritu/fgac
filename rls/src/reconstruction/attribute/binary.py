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

import sys
from typing import Optional, Tuple

from reconstruction.attribute.workers import (
    AttributeProbePlan,
    AttributeProbeResult,
    AttributeProbeRuntime,
)
from reconstruction.candidates import RangeSlice, RangeValues
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.probing.progress import make_range_preview
from reconstruction.probing.binary import BinaryProber
from reconstruction.probing.parallel import chunk_indices
from reconstruction.sql.queries import build_range_query
from reconstruction.types import DbCursor, DbValue, SupportsValueAt


def run_binary_probe(
    runtime: ReconstructionExecution,
    cur: DbCursor,
    attr: str,
    search_values: SupportsValueAt,
    cal_buffer: int,
) -> AttributeProbeResult:
    args = runtime.args
    if isinstance(search_values, RangeValues) and abs(search_values.step) != 1:
        raise RuntimeError(f"binary_search for {attr} requires step of 1 or -1")

    total_values = len(search_values)
    if not args.no_progress_output:
        print(f"Binary search probing {attr} ({total_values} values)", file=sys.stderr)
    probe_runtime = AttributeProbeRuntime.create(
        runtime,
        cur,
        AttributeProbePlan(
            attr=attr,
            total_values=total_values,
            progress_label=f"{attr}_bin",
        ),
    )
    if probe_runtime.use_workers and total_values > 0:
        ranges = chunk_indices(total_values, min(args.workers, total_values))
    elif total_values > 0:
        ranges = [(0, total_values - 1)]
    else:
        ranges = []

    def worker_body(
        span: Tuple[int, int],
        worker_cur: DbCursor,
        worker_verify_cur: Optional[DbCursor],
    ) -> AttributeProbeResult:
        result = AttributeProbeResult()
        start, end = span
        range_query = build_range_query(args.table, attr, "1", " LIMIT 1")
        slice_values = RangeSlice(search_values, start, end)
        missing_value = runtime.known_values[attr].missing
        last_ts = {"candidate": 0, "baseline": 0}

        def run_range(low: DbValue, high: DbValue, lo_idx: int, hi_idx: int) -> int:
            candidate_rt, _ = probe_runtime.query_runner.min_with_match(
                worker_cur,
                range_query,
                (low, high),
                label=f"attr_bin:{attr}",
            )
            baseline_rt, _ = probe_runtime.query_runner.min_with_match(
                worker_cur,
                range_query,
                (missing_value, missing_value),
                label=f"attr_bin_baseline:{attr}",
            )
            hot = int((candidate_rt - baseline_rt) > cal_buffer)
            last_ts["candidate"] = candidate_rt
            last_ts["baseline"] = baseline_rt
            runtime.oracle_logs.write_attribute_call(
                attr,
                low,
                high,
                hi_idx - lo_idx + 1,
                lo_idx == hi_idx,
                candidate_rt,
                baseline_rt,
                cal_buffer,
                hot,
            )
            return 1 if hot else -1

        def on_value(value: DbValue, _min_rt: int, guess: int) -> None:
            probe_runtime.record_value(
                result,
                worker_verify_cur,
                value,
                last_ts["candidate"],
                last_ts["baseline"] + cal_buffer,
                guess,
            )

        preview_fn = make_range_preview(total_values, offset=start)
        BinaryProber().probe(
            slice_values,
            0,
            run_range,
            on_value,
            probe_runtime.tracker,
            preview_fn,
        )
        return result

    return probe_runtime.run_workers(ranges, worker_body)
