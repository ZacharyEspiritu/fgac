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

from typing import Callable, Generic, List, Optional, Sequence, Tuple, TypeVar

from reconstruction.probing.parallel import ProgressTracker


V = TypeVar("V")


class InProber(Generic[V]):
    def probe(
        self,
        values: Sequence[V],
        threshold: int,
        run_probe: Callable[[Sequence[V]], int],
        on_value: Callable[[V, int, int], None],
        tracker: ProgressTracker,
        preview_fn: Callable[[Sequence[V]], str],
        on_subset_cold: Optional[Callable[[Sequence[V], int], None]] = None,
        max_subset: Optional[int] = None,
    ) -> List[V]:
        recovered: List[V] = []
        stack: List[Tuple[int, int]] = []
        if values:
            stack.append((0, len(values) - 1))
        while stack:
            lo, hi = stack.pop()
            subset = values[lo : hi + 1]
            count = len(subset)
            if count == 0:
                continue
            if max_subset is not None and count > max_subset:
                mid = (lo + hi) // 2
                stack.append((mid + 1, hi))
                stack.append((lo, mid))
                tracker.update(preview_fn(subset))
                continue
            min_rt = run_probe(subset)
            preview = preview_fn(subset)
            if count == 1:
                value = subset[0]
                guess = int(min_rt > threshold)
                on_value(value, min_rt, guess)
                if guess:
                    recovered.append(value)
                tracker.advance(1, preview)
                continue
            if min_rt <= threshold:
                if on_subset_cold:
                    on_subset_cold(subset, min_rt)
                tracker.advance(count, preview)
                continue
            mid = (lo + hi) // 2
            stack.append((mid + 1, hi))
            stack.append((lo, mid))
            tracker.update(preview)
        return recovered
