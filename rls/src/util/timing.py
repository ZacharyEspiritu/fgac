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

import time
from typing import Optional, Sequence, Tuple


def now_ns() -> int:
    return time.perf_counter_ns()


def timed_query(
    cur,
    query: str,
    params: Optional[Sequence] = None,
    fetch_one: bool = False,
) -> Tuple[int, int]:
    start = now_ns()
    cur.execute(query, params)
    if fetch_one:
        row = cur.fetchone()
        rowcount = 1 if row else 0
    else:
        rows = cur.fetchall()
        rowcount = len(rows)
    end = now_ns()
    return end - start, rowcount
