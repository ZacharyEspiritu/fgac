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
from typing import Optional, Sequence

from reconstruction.attribute.workers import (
    AttributeProbePlan,
    AttributeProbeResult,
    AttributeProbeRuntime,
)
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.probing.linear import LinearProber
from reconstruction.probing.parallel import chunk_list
from reconstruction.types import DbCursor, DbValue


def run_linear_probe(
    runtime: ReconstructionExecution,
    cur: DbCursor,
    attr: str,
    values: Sequence[DbValue],
    query: str,
    cal_buffer: int,
) -> AttributeProbeResult:
    args = runtime.args
    total_values = len(values)
    if not args.no_progress_output:
        print(f"Probing attribute {attr} ({total_values} candidates)", file=sys.stderr)
    probe_runtime = AttributeProbeRuntime.create(
        runtime,
        cur,
        AttributeProbePlan(
            attr=attr,
            total_values=total_values,
            progress_label=attr,
        ),
    )
    values_list = list(values)
    work_items = (
        chunk_list(values_list, min(args.workers, total_values))
        if probe_runtime.use_workers
        else [values_list]
    )

    def worker_body(
        chunk: Sequence[DbValue],
        worker_cur: DbCursor,
        worker_verify_cur: Optional[DbCursor],
    ) -> AttributeProbeResult:
        result = AttributeProbeResult()
        missing_value = runtime.known_values[attr].missing
        last_ts = {"candidate": 0, "baseline": 0}

        def run_value(value: DbValue) -> int:
            cand, _ = probe_runtime.query_runner.min_with_match(
                worker_cur,
                query,
                (value,),
                label=f"attr_probe:{attr}",
            )
            base, _ = probe_runtime.query_runner.min_with_match(
                worker_cur,
                query,
                (missing_value,),
                label=f"attr_probe_baseline:{attr}",
            )
            last_ts["candidate"] = cand
            last_ts["baseline"] = base
            return cand - base

        def on_value(value: DbValue, _min_rt: int, guess: int) -> None:
            probe_runtime.record_value(
                result,
                worker_verify_cur,
                value,
                last_ts["candidate"],
                last_ts["baseline"] + cal_buffer,
                guess,
            )

        LinearProber[DbValue]().probe(
            chunk,
            cal_buffer,
            run_value,
            on_value,
            probe_runtime.tracker,
            lambda value: str(value),
        )
        return result

    return probe_runtime.run_workers(work_items, worker_body)
