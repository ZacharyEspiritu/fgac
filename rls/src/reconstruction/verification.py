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

from typing import Dict, Optional, Sequence

from reconstruction.candidates.domains import values_in_candidate_domain
from reconstruction.candidates import CandidateSpec
from reconstruction.truth import (
    BackendLike,
    GroundTruth,
    compute_correctness,
    fetch_truth_values_for_binary,
    fetch_truth_values_for_in,
)
from reconstruction.types import DbConnection, DbValue


VerifyCounts = Dict[str, tuple[int, int, int]]


def verify_recovered_attribute(
    verify: bool,
    ground_truth: Optional[GroundTruth],
    admin: Optional[DbConnection],
    backend: BackendLike,
    table: str,
    attr: str,
    spec: CandidateSpec,
    recovered_values: Sequence[DbValue],
    binary_verify_counts: VerifyCounts,
    sampled_verify_counts: VerifyCounts,
) -> None:
    if not verify:
        return

    if ground_truth is not None and spec.binary_search:
        truth_values = values_in_candidate_domain(
            spec.values,
            ground_truth.values_for(attr),
        )
        binary_verify_counts[attr] = compute_correctness(truth_values, recovered_values)
    elif admin and spec.binary_search:
        with admin.cursor() as verify_cur:
            truth_values = fetch_truth_values_for_binary(verify_cur, table, attr, spec)
        binary_verify_counts[attr] = compute_correctness(truth_values, recovered_values)

    if ground_truth is not None and spec.skip_probe:
        truth_values = values_in_candidate_domain(
            spec.values,
            ground_truth.values_for(attr),
        )
        sampled_verify_counts[attr] = compute_correctness(truth_values, recovered_values)
    elif admin and spec.skip_probe:
        with admin.cursor() as verify_cur:
            truth_values = fetch_truth_values_for_in(
                verify_cur, table, attr, list(spec.values), backend
            )
        sampled_verify_counts[attr] = compute_correctness(truth_values, recovered_values)
