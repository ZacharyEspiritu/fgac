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

from reconstruction.candidates import CandidateSpec
from reconstruction.reporting.outputs import print_final_report, write_summary
from reconstruction.runtime.context import ReconstructionContext
from reconstruction.reporting.summary import build_reconstruction_summary
from reconstruction.tuple.reconstruction import TupleReconstructionResult


def finalize_reconstruction_run(
    ctx: ReconstructionContext,
    candidates: dict[str, CandidateSpec],
    tuple_result: TupleReconstructionResult,
) -> None:
    args = ctx.args
    summary = build_reconstruction_summary(
        tuple_result.tuple_attrs,
        candidates,
        ctx.state.recovered,
        ctx.state.skipped_probe,
        tuple_result.tuple_threshold,
        tuple_result.tuple_tested_count,
        ctx.state.query_counts,
        ctx.state.stage_times,
        args.verify,
        ctx.state.binary_verify_counts,
        ctx.state.sampled_verify_counts,
        ctx.state.values_rows,
        tuple_result.tuple_stats,
        tuple_result.tuple_step_stats,
    )

    ctx.oracle_logs.add_summary_stats(
        summary,
        args.output_dir,
        ctx.admin,
        candidates,
        args.table,
        args.no_progress_output,
    )
    write_summary(args.output_dir, summary)

    print_final_report(
        summary,
        tuple_result.tuple_attrs,
        tuple_result.tuple_step_stats,
        ctx.state.query_counts,
        ctx.state.stage_times,
        args.verify,
    )
