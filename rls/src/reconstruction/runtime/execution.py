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

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from reconstruction.candidates import CandidateSpec
from reconstruction.cli import ReconstructionOptions
from reconstruction.reporting.oracle_logs import OracleLogs
from reconstruction.runtime.context import ReconstructionState
from reconstruction.runtime.setup import KnownValues
from reconstruction.truth import BackendLike, GroundTruth
from reconstruction.types import DbConnection


@dataclass(frozen=True)
class ReconstructionExecution:
    args: ReconstructionOptions
    backend: BackendLike
    attacker: DbConnection
    admin: Optional[DbConnection]
    candidates: Dict[str, CandidateSpec]
    attributes: Sequence[str]
    known_values: Dict[str, KnownValues]
    ground_truth: Optional[GroundTruth]
    oracle_logs: OracleLogs
    state: ReconstructionState

    @property
    def verify(self) -> bool:
        return bool(self.args.verify)
