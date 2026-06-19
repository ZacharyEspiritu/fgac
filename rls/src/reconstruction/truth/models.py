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
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from reconstruction.types import CsvRow, DbRow, DbValue


class BackendLike(Protocol):
    name: str
    param: str


def compute_correctness(
    truth_values: Sequence[DbValue], guessed_values: Sequence[DbValue]
) -> Tuple[int, int, int]:
    truth_set = set(truth_values)
    guessed_set = set(guessed_values)
    tp = len(truth_set & guessed_set)
    fp = len(guessed_set - truth_set)
    fn = len(truth_set - guessed_set)
    return tp, fp, fn


@dataclass
class CorrectnessStats:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    total: int = 0

    def update(self, guess: int, truth: Optional[int]) -> None:
        self.total += 1
        if truth is None:
            return
        if guess == 1 and truth == 1:
            self.tp += 1
        elif guess == 1 and truth == 0:
            self.fp += 1
        elif guess == 0 and truth == 1:
            self.fn += 1

    def merge(self, other: "CorrectnessStats") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.total += other.total


@dataclass
class TupleWorkerResult:
    next_prefixes: List[DbRow]
    rows: List[CsvRow]
    tested_count: int
    step_stats: CorrectnessStats
    tuple_stats: CorrectnessStats


@dataclass
class TupleBuildResult:
    tuple_threshold: int
    tuple_tested_count: int
    tuple_stats: CorrectnessStats
    tuple_step_stats: Dict[int, CorrectnessStats]
