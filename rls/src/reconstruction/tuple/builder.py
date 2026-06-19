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

import csv
import os
import sys
import time
from contextlib import ExitStack
from typing import Dict, List, Tuple

from reconstruction.probing.parallel import make_probe_step
from reconstruction.probing.query_runner import ProbeQueryRunner
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.runtime.setup import resolve_known_tuple
from reconstruction.sql.queries import build_query
from reconstruction.truth import CorrectnessStats, TupleBuildResult
from reconstruction.tuple.binary_extension import run_binary_extension
from reconstruction.tuple.calibration import TupleCalibrationState
from reconstruction.tuple.linear_extension import run_linear_extension
from reconstruction.tuple.planning import TuplePlanningContext, make_tuple_step_plan
from reconstruction.tuple.subset_extension import run_subset_extension
from reconstruction.tuple.worker_context import TupleOutputBuffer, TupleStepRuntime
from reconstruction.types import DbValue


def build_tuples(
    runtime: ReconstructionExecution,
    tuple_attrs: List[str],
) -> TupleBuildResult:
    admin = runtime.admin
    known_tuple = resolve_known_tuple(runtime.args, admin, tuple_attrs)
    verify_tuple_query = build_query(
        runtime.args.table, tuple_attrs, select_expr="1", limit_clause=" LIMIT 1"
    )
    tuple_header = (
        [*tuple_attrs, "min_elapsed_ns", "threshold_ns", "exists_guess", "exists_truth"]
    )
    tuple_tested_count = 0
    tuple_stats = CorrectnessStats()
    tuple_step_stats: Dict[int, CorrectnessStats] = {}
    tuple_threshold = 0
    tuple_csv_path = os.path.join(runtime.args.output_dir, "reconstruction_tuples.csv")

    with runtime.attacker.cursor() as cur, ExitStack() as stack:
        verify_cur = (
            stack.enter_context(admin.cursor()) if runtime.verify and admin else None
        )
        tuple_handle = stack.enter_context(
            open(tuple_csv_path, "w", encoding="utf-8", newline="")
        )
        tuple_writer = csv.writer(tuple_handle)
        tuple_writer.writerow(tuple_header)
        tuple_output = TupleOutputBuffer(tuple_writer)
        calibration = TupleCalibrationState()

        current: List[Tuple[DbValue, ...]] = [
            (value,) for value in runtime.state.recovered.get(tuple_attrs[0], [])
        ]
        if not runtime.args.no_progress_output:
            print("Calibrating tuple thresholds (stepwise)...", file=sys.stderr)

        planning_context = TuplePlanningContext(
            execution=runtime,
            cur=cur,
            admin=admin,
            calibration=calibration,
            tuple_attrs=tuple_attrs,
            known_tuple=known_tuple,
            verify_cur=verify_cur,
            tuple_step_stats=tuple_step_stats,
            verify_tuple_query=verify_tuple_query,
        )
        for step in range(2, len(tuple_attrs) + 1):
            plan = make_tuple_step_plan(
                planning_context,
                current,
                step,
            )
            if plan.is_final_step:
                tuple_threshold = plan.threshold

            if not runtime.args.no_progress_output:
                if plan.total_candidates == 0:
                    print(
                        f"No tuple combinations to probe at length {step}.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"Probing tuples of length {step} "
                        f"({plan.total_candidates} combinations)",
                        file=sys.stderr,
                    )

            probe_step = make_probe_step(
                plan.total_candidates,
                f"tuples_{step}",
                runtime.args.no_progress_output,
                runtime.args.workers,
                runtime.state.query_counts,
                enable_workers=len(current) > 1,
                create_progress=plan.total_candidates > 0,
            )
            probe_start = time.perf_counter()
            step_runtime = TupleStepRuntime(
                execution=runtime,
                admin_present=admin is not None,
                current=current,
                cur=cur,
                verify_cur=verify_cur,
                use_workers=probe_step.use_workers,
                plan=plan,
                tracker=probe_step.tracker,
                query_runner=ProbeQueryRunner(
                    runtime.args.num_queries_per_probe,
                    probe_step.counter,
                    True,
                ),
                calibration=calibration,
                tuple_step_stats=tuple_step_stats,
                tuple_stats=tuple_stats,
                output=tuple_output,
            )

            if plan.strategy == "binary":
                run_binary_extension(step_runtime)
            elif plan.strategy == "in":
                run_subset_extension(step_runtime)
            else:
                run_linear_extension(step_runtime)

            runtime.state.stage_times[f"tuple_probe_len:{step}"] = (
                runtime.state.stage_times.get(f"tuple_probe_len:{step}", 0.0)
                + (time.perf_counter() - probe_start)
            )
            tuple_tested_count += step_runtime.tested_count
            current = step_runtime.next_current
            if not runtime.args.no_progress_output:
                print(
                    f"Recovered {len(current)} tuple prefixes of length {step}",
                    file=sys.stderr,
                )

        tuple_output.flush()

    if len(tuple_attrs) == 1:
        tuple_threshold = 0

    return TupleBuildResult(
        tuple_threshold=tuple_threshold,
        tuple_tested_count=tuple_tested_count,
        tuple_stats=tuple_stats,
        tuple_step_stats=tuple_step_stats,
    )
