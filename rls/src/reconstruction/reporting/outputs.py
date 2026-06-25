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
from typing import Dict, Sequence

from reconstruction.reporting.console import print_final_report as print_report_panel
from reconstruction.truth import CorrectnessStats
from reconstruction.types import Summary
from util.io import write_json


def write_summary(output_dir: str, summary: Summary) -> None:
    write_json(os.path.join(output_dir, "reconstruction_summary.json"), summary)


def print_final_report(
    summary: Summary,
    tuple_attrs: Sequence[str],
    tuple_step_stats: Dict[int, CorrectnessStats],
    query_counts: Dict[str, int],
    stage_times: Dict[str, float],
    verify: bool,
) -> None:
    print_report_panel(
        summary,
        tuple_attrs,
        tuple_step_stats,
        query_counts,
        stage_times,
        verify,
    )
