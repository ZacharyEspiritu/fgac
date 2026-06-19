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

from typing import Callable, Generic, Iterable, List, Optional, TypeVar

from reconstruction.probing.parallel import ProgressTracker


V = TypeVar("V")


class LinearProber(Generic[V]):
    def probe(
        self,
        values: Iterable[V],
        threshold: int,
        run_probe: Callable[[V], int],
        on_value: Callable[[V, int, int], None],
        tracker: ProgressTracker,
        preview_fn: Callable[[V], str],
        threshold_fn: Optional[Callable[[], int]] = None,
    ) -> List[V]:
        recovered: List[V] = []
        for value in values:
            th = threshold_fn() if threshold_fn is not None else threshold
            min_rt = run_probe(value)
            guess = int(min_rt > th)
            on_value(value, min_rt, guess)
            if guess:
                recovered.append(value)
            tracker.advance(1, preview_fn(value))
        return recovered
