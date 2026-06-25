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

from collections.abc import Callable, Sequence
from typing import TypeVar

from reconstruction.types import DbValue


T = TypeVar("T")


def render_range_window(lo: int, hi: int, total: int, width: int = 20) -> str:
    if total <= 0 or width <= 0:
        return "[]"
    clamped_lo = max(0, min(lo, total - 1))
    clamped_hi = max(0, min(hi, total - 1))
    start = int(clamped_lo / total * width)
    end = int((clamped_hi + 1) / total * width) - 1
    start = max(0, min(start, width - 1))
    end = max(start, min(end, width - 1))
    chars = ["-"] * width
    for idx in range(start, end + 1):
        chars[idx] = "="
    return f"[{''.join(chars)}]"


def preview_subset(subset: Sequence[T]) -> str:
    if len(subset) == 1:
        return str(subset[0])
    return f"{subset[0]}..{subset[-1]} ({len(subset)})"


def make_range_preview(
    total: int,
    offset: int = 0,
) -> Callable[[DbValue, DbValue, int, int, int], str]:
    def preview(low: DbValue, high: DbValue, lo: int, hi: int, _total: int) -> str:
        global_lo = offset + lo
        global_hi = offset + hi
        window = render_range_window(global_lo, global_hi, total)
        coverage = 0.0
        if total > 0:
            coverage = ((global_hi - global_lo + 1) / total) * 100.0
        if 0 < coverage < 0.01:
            cover_text = "<0.01%"
        else:
            cover_text = f"{coverage:.2f}%"
        return f"{low}..{high} cover={cover_text} {window}"

    return preview
