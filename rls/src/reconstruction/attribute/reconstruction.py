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
import time

from reconstruction.attribute.calibration import calibrate_attribute
from reconstruction.attribute.binary import run_binary_probe
from reconstruction.attribute.linear import run_linear_probe
from reconstruction.sql.queries import build_query
from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.verification import verify_recovered_attribute
from util.sql_utils import validate_identifier


def run_attribute_reconstruction(runtime: ReconstructionExecution) -> None:
    args = runtime.args
    state = runtime.state

    if runtime.admin is not None:
        validate_identifier(args.table)

    with runtime.attacker.cursor() as cur:
        for attr in runtime.attributes:
            spec = runtime.candidates.get(attr)
            calibration = calibrate_attribute(runtime, cur, attr, spec)
            threshold = calibration.threshold
            if len(runtime.attributes) == 1:
                state.single_attr_threshold = threshold
            query = build_query(args.table, [attr], "1", " LIMIT 1")

            if spec is None:
                raise RuntimeError(f"No candidate spec for {attr}")
            attr_candidates = spec.values
            probe_start = time.perf_counter()

            if spec.skip_probe:
                recovered_values = list(attr_candidates)
                state.skipped_probe[attr] = True
                if not args.no_progress_output:
                    print(
                        f"Skipping {attr} probing; using {len(recovered_values)} candidates",
                        file=sys.stderr,
                    )
            elif spec.binary_search:
                search_values = spec.search_values
                if search_values is None:
                    raise RuntimeError(
                        f"binary_search enabled but no range/parts spec for {attr}"
                    )
                probe_result = run_binary_probe(
                    runtime,
                    cur,
                    attr,
                    search_values,
                    calibration.gap // 2,
                )
                recovered_values = probe_result.recovered_values
                state.values_rows.extend(probe_result.rows)
            else:
                probe_result = run_linear_probe(
                    runtime,
                    cur,
                    attr,
                    list(attr_candidates),
                    query,
                    calibration.gap // 2,
                )
                recovered_values = probe_result.recovered_values
                state.values_rows.extend(probe_result.rows)

            state.stage_times[f"attr_probe:{attr}"] = (
                state.stage_times.get(f"attr_probe:{attr}", 0.0)
                + (time.perf_counter() - probe_start)
            )
            state.recovered[attr] = recovered_values
            verify_recovered_attribute(
                runtime.verify,
                runtime.ground_truth,
                runtime.admin,
                runtime.backend,
                args.table,
                attr,
                spec,
                recovered_values,
                state.binary_verify_counts,
                state.sampled_verify_counts,
            )
            if not args.no_progress_output:
                print(
                    f"Recovered {len(recovered_values)} values for {attr}",
                    file=sys.stderr,
                )
