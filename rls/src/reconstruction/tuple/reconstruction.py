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

import os
from dataclasses import dataclass
from typing import Dict

from reconstruction.runtime.execution import ReconstructionExecution
from reconstruction.truth import CorrectnessStats
from reconstruction.tuple.builder import build_tuples
from util.io import write_csv


@dataclass(frozen=True)
class TupleReconstructionResult:
    tuple_attrs: list[str]
    tuple_threshold: int
    tuple_tested_count: int
    tuple_stats: CorrectnessStats
    tuple_step_stats: Dict[int, CorrectnessStats]


def run_tuple_reconstruction(
    runtime: ReconstructionExecution,
) -> TupleReconstructionResult:
    args = runtime.args
    attributes = runtime.attributes
    state = runtime.state

    values_csv_path = os.path.join(args.output_dir, "reconstruction_values.csv")
    values_header = (
        "attribute",
        "value",
        "min_elapsed_ns",
        "threshold_ns",
        "exists_guess",
        "exists_truth",
    )
    write_csv(values_csv_path, state.values_rows, header=values_header)

    tuple_attrs = list(attributes)
    build_result = build_tuples(
        runtime,
        tuple_attrs,
    )
    tuple_threshold = build_result.tuple_threshold
    if len(tuple_attrs) == 1 and state.single_attr_threshold is not None:
        tuple_threshold = state.single_attr_threshold

    tuple_stats = build_result.tuple_stats

    return TupleReconstructionResult(
        tuple_attrs=tuple_attrs,
        tuple_threshold=tuple_threshold,
        tuple_tested_count=build_result.tuple_tested_count,
        tuple_stats=tuple_stats,
        tuple_step_stats=build_result.tuple_step_stats,
    )
