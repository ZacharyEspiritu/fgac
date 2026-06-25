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

import bisect
import csv
import os
from typing import Callable, Dict, List, Optional, cast

from reconstruction.candidates import CandidateSpec
from reconstruction.truth.fetch import fetch_lex_range_values_for_binary
from reconstruction.types import ComparableValue, DbConnection


def post_process_oracle_calls(
    raw_path: str,
    output_dir: str,
    admin: Optional[DbConnection],
    candidates: Dict[str, CandidateSpec],
    table: str,
) -> Dict[str, Dict[str, int]]:
    """Augment raw binary-search oracle calls with admin-derived truth labels."""
    truth_sorted: Dict[str, List[ComparableValue]] = {}
    if admin is not None:
        with admin.cursor() as cur:
            for attr, spec in candidates.items():
                if not spec.binary_search:
                    continue
                values = fetch_lex_range_values_for_binary(cur, table, attr, spec)
                truth_sorted[attr] = sorted(cast(List[ComparableValue], values))

    coercers: Dict[str, Callable[[str], ComparableValue]] = {}
    for attr, ts in truth_sorted.items():
        if ts and isinstance(ts[0], int):
            coercers[attr] = int
        elif ts and isinstance(ts[0], float):
            coercers[attr] = float
        else:
            coercers[attr] = str

    final_path = os.path.join(output_dir, "reconstruction_oracle_calls.csv")
    per_attr: Dict[str, Dict[str, int]] = {}
    with (
        open(raw_path, "r", newline="", encoding="utf-8") as raw_f,
        open(final_path, "w", newline="", encoding="utf-8") as final_f,
    ):
        reader = csv.reader(raw_f)
        writer = csv.writer(final_f)
        header = next(reader)
        guess_idx = header.index("guess")
        writer.writerow(header + ["truth", "correct"])
        for row in reader:
            attr = row[0]
            low_raw = row[1]
            high_raw = row[2]
            guess = int(row[guess_idx])
            truth: Optional[int] = None
            if attr in truth_sorted:
                ts = truth_sorted[attr]
                coerce = coercers[attr]
                try:
                    low = coerce(low_raw)
                    high = coerce(high_raw)
                except (TypeError, ValueError):
                    low, high = low_raw, high_raw
                lo_idx = bisect.bisect_left(ts, low)
                hi_idx = bisect.bisect_right(ts, high)
                truth = int(hi_idx > lo_idx)
            correct = int(guess == truth) if truth is not None else ""
            writer.writerow(row + [truth if truth is not None else "", correct])
            if truth is None:
                continue
            stats = per_attr.setdefault(attr, {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
            if guess == 1 and truth == 1:
                stats["tp"] += 1
            elif guess == 1 and truth == 0:
                stats["fp"] += 1
            elif guess == 0 and truth == 0:
                stats["tn"] += 1
            elif guess == 0 and truth == 1:
                stats["fn"] += 1
    try:
        os.remove(raw_path)
    except OSError:
        pass
    return per_attr
