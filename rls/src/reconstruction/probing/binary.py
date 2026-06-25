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

import operator
from typing import Callable, List, Optional, Tuple, cast

from reconstruction.probing.parallel import ProgressTracker
from reconstruction.types import ComparableValue, DbValue, SupportsValueAt


class BinaryProber:
    def probe(
        self,
        values: SupportsValueAt,
        threshold: int,
        run_probe: Callable[[DbValue, DbValue, int, int], int],
        on_value: Callable[[DbValue, int, int], None],
        tracker: ProgressTracker,
        preview_fn: Callable[[DbValue, DbValue, int, int, int], str],
        on_range_cold: Optional[
            Callable[[int, int, DbValue, DbValue, int], None]
        ] = None,
    ) -> List[DbValue]:
        recovered: List[DbValue] = []
        total = len(values)
        stack: List[Tuple[int, int]] = []
        if total > 0:
            stack.append((0, total - 1))
        while stack:
            lo, hi = stack.pop()
            lo_val = values.value_at(lo)
            hi_val = values.value_at(hi)
            lo_cmp = cast(ComparableValue, lo_val)
            hi_cmp = cast(ComparableValue, hi_val)
            low = lo_val if operator.le(lo_cmp, hi_cmp) else hi_val
            high = hi_val if operator.le(lo_cmp, hi_cmp) else lo_val
            min_rt = run_probe(low, high, lo, hi)
            preview = preview_fn(low, high, lo, hi, total)
            if min_rt <= threshold:
                if on_range_cold:
                    on_range_cold(lo, hi, low, high, min_rt)
                tracker.advance(hi - lo + 1, preview)
                continue
            if lo == hi:
                on_value(lo_val, min_rt, 1)
                recovered.append(lo_val)
                tracker.advance(1, str(lo_val))
                continue
            mid = (lo + hi) // 2
            stack.append((mid + 1, hi))
            stack.append((lo, mid))
            tracker.update(preview)
        return recovered
