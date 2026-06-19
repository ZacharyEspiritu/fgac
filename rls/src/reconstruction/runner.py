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

from reconstruction.attribute.reconstruction import run_attribute_reconstruction
from reconstruction.runtime.finalization import finalize_reconstruction_run
from reconstruction.runtime.context import ReconstructionContext
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.runtime.setup import (
    load_ground_truth_if_needed,
    prepare_reconstruction_setup,
)
from reconstruction.tuple.reconstruction import run_tuple_reconstruction


def run_reconstruction(ctx: ReconstructionContext) -> None:
    args = ctx.args
    setup = prepare_reconstruction_setup(args, ctx.admin)
    candidates = setup.candidates
    attributes = setup.attributes
    known_values = setup.known_values

    ground_truth = load_ground_truth_if_needed(
        ctx.admin,
        args.table,
        attributes,
        args.verify,
        args.no_progress_output,
        ctx.state.stage_times,
    )

    execution = ReconstructionExecution(
        args=args,
        backend=ctx.backend,
        attacker=ctx.attacker,
        admin=ctx.admin,
        candidates=candidates,
        attributes=attributes,
        known_values=known_values,
        ground_truth=ground_truth,
        oracle_logs=ctx.oracle_logs,
        state=ctx.state,
    )
    run_attribute_reconstruction(execution)
    tuple_result = run_tuple_reconstruction(execution)

    finalize_reconstruction_run(ctx, candidates, tuple_result)
