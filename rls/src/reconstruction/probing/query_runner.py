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

from reconstruction.sql.db import run_query_min_with_match
from reconstruction.types import DbCounter, DbCursor, DbParams


@dataclass(frozen=True)
class ProbeQueryRunner:
    rounds: int
    counter: DbCounter
    fetch_one: bool

    def with_rounds(self, rounds: int) -> "ProbeQueryRunner":
        return ProbeQueryRunner(rounds, self.counter, self.fetch_one)

    def min_with_match(
        self, cur: DbCursor, query: str, params: DbParams, label: str
    ) -> tuple[int, bool]:
        return run_query_min_with_match(
            cur,
            query,
            params,
            self.rounds,
            counter=self.counter,
            label=label,
            fetch_one=self.fetch_one,
        )
