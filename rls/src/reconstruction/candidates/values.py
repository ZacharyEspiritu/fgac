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

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Protocol, Tuple

from reconstruction.types import DbValue, SupportsValueAt


class CandidateValues(Protocol):
    def __iter__(self) -> Iterator[DbValue]:
        ...

    def __len__(self) -> int:
        ...


def _range_length(start: int, end: int, step: int) -> int:
    if step > 0:
        if start > end:
            return 0
        return ((end - start) // step) + 1
    if start < end:
        return 0
    return ((start - end) // (-step)) + 1


def _iter_range_values(start: int, end: int, step: int) -> Iterator[int]:
    if step > 0:
        return iter(range(start, end + 1, step))
    return iter(range(start, end - 1, step))


@dataclass
class LiteralValues:
    values: List[DbValue]

    def __iter__(self) -> Iterator[DbValue]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)


@dataclass
class RangeValues:
    start: int
    end: int
    step: int
    fmt: Optional[str] = None

    def __iter__(self) -> Iterator[DbValue]:
        for value in _iter_range_values(self.start, self.end, self.step):
            if self.fmt:
                if "%" in self.fmt:
                    yield self.fmt % value
                elif "{" in self.fmt:
                    yield self.fmt.format(value)
                else:
                    yield format(value, self.fmt)
            else:
                yield value

    def value_at(self, idx: int) -> DbValue:
        value = self.start + (idx * self.step)
        if self.fmt:
            if "%" in self.fmt:
                return self.fmt % value
            if "{" in self.fmt:
                return self.fmt.format(value)
            return format(value, self.fmt)
        return value

    def __len__(self) -> int:
        return _range_length(self.start, self.end, self.step)


@dataclass
class RangeSlice:
    base: SupportsValueAt
    start: int
    end: int

    def __len__(self) -> int:
        return max(0, self.end - self.start + 1)

    def value_at(self, idx: int) -> DbValue:
        return self.base.value_at(self.start + idx)


@dataclass
class PartsValues:
    parts: List[Tuple[int, int, int, int]]
    separator: str = "-"
    part_sizes: List[int] = field(init=False)
    part_factors: List[int] = field(init=False)

    def __post_init__(self) -> None:
        sizes: List[int] = []
        for start, end, step, _width in self.parts:
            size = _range_length(start, end, step)
            if size <= 0:
                raise ValueError("Parts range must be non-empty")
            sizes.append(size)
        factors: List[int] = []
        suffix = 1
        for size in reversed(sizes):
            factors.append(suffix)
            suffix *= size
        self.part_sizes = sizes
        self.part_factors = list(reversed(factors))

    def __iter__(self) -> Iterator[str]:
        ranges = []
        widths = []
        for start, end, step, width in self.parts:
            ranges.append(range(start, end + 1, step) if step > 0 else range(start, end - 1, step))
            widths.append(width)
        for combo in itertools.product(*ranges):
            formatted = [f"{value:0{widths[idx]}d}" for idx, value in enumerate(combo)]
            yield self.separator.join(formatted)

    def __len__(self) -> int:
        total = 1
        for size in self.part_sizes:
            total *= size
        return total

    def value_at(self, idx: int) -> str:
        if idx < 0 or idx >= len(self):
            raise IndexError("Parts index out of range")
        values: List[str] = []
        for (start, _end, step, width), size, factor in zip(
            self.parts, self.part_sizes, self.part_factors
        ):
            part_index = (idx // factor) % size
            value = start + (part_index * step)
            values.append(f"{value:0{width}d}")
        return self.separator.join(values)


@dataclass
class CompositeValues:
    segments: List[CandidateValues]

    def __iter__(self) -> Iterator[DbValue]:
        for segment in self.segments:
            for item in segment:
                yield item

    def __len__(self) -> int:
        return sum(len(segment) for segment in self.segments)
