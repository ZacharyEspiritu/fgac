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

from typing import Iterable, List, Optional

from reconstruction.candidates import (
    CandidateValues,
    CompositeValues,
    LiteralValues,
    PartsValues,
    RangeSlice,
    RangeValues,
)
from reconstruction.types import DbValue


def values_in_candidate_domain(
    domain: CandidateValues | None, values: Iterable[DbValue]
) -> List[DbValue]:
    """Filter real DB values to those represented by a candidate domain.

    Reconstruction configs can describe very large implicit domains, such as
    the full SSN space. This helper tests the real values instead of expanding
    the domain into a set first.
    """
    if domain is None:
        return []
    if isinstance(domain, LiteralValues):
        literal_set = set(domain.values)
        return [value for value in values if value in literal_set]
    return [value for value in values if candidate_domain_contains(domain, value)]


def candidate_domain_contains(domain: CandidateValues | None, value: DbValue) -> bool:
    if domain is None:
        return False
    if isinstance(domain, CompositeValues):
        return any(candidate_domain_contains(segment, value) for segment in domain.segments)
    if isinstance(domain, RangeSlice):
        return _range_slice_contains(domain, value)
    if isinstance(domain, PartsValues):
        return _parts_contains(domain, value)
    if isinstance(domain, RangeValues):
        return _range_contains(domain, value)
    if isinstance(domain, LiteralValues):
        return value in domain.values
    return False


def _range_slice_contains(domain: RangeSlice, value: DbValue) -> bool:
    for idx in range(domain.start, domain.end + 1):
        if domain.base.value_at(idx) == value:
            return True
    return False


def _range_contains(domain: RangeValues, value: DbValue) -> bool:
    numeric_value = _coerce_range_value(domain, value)
    if numeric_value is None:
        return False
    start = int(domain.start)
    end = int(domain.end)
    step = int(domain.step)
    if step == 0:
        return False
    if step > 0:
        if numeric_value < start or numeric_value > end:
            return False
        return (numeric_value - start) % step == 0
    if numeric_value > start or numeric_value < end:
        return False
    return (start - numeric_value) % abs(step) == 0


def _coerce_range_value(domain: RangeValues, value: DbValue) -> Optional[int]:
    fmt = domain.fmt
    if fmt is None:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None
    if not isinstance(value, str):
        return None
    try:
        numeric_value = int(value)
    except ValueError:
        return None
    try:
        formatted = _format_range_value(fmt, numeric_value)
    except (TypeError, ValueError):
        return None
    if formatted != value:
        return None
    return numeric_value


def _format_range_value(fmt: str, value: int) -> str:
    if "%" in fmt:
        return fmt % value
    if "{" in fmt:
        return fmt.format(value)
    return format(value, fmt)


def _parts_contains(domain: PartsValues, value: DbValue) -> bool:
    if not isinstance(value, str):
        return False
    separator = domain.separator
    parts = list(domain.parts)
    if separator:
        tokens = value.split(separator)
        if len(tokens) != len(parts):
            return False
    else:
        tokens = []
        pos = 0
        for _start, _end, _step, width in parts:
            tokens.append(value[pos : pos + width])
            pos += width
        if pos != len(value):
            return False
    for token, (start, end, step, width) in zip(tokens, parts):
        if len(token) != int(width):
            return False
        try:
            numeric_value = int(token)
        except ValueError:
            return False
        if not _in_stepped_interval(numeric_value, int(start), int(end), int(step)):
            return False
    return True


def _in_stepped_interval(value: int, start: int, end: int, step: int) -> bool:
    if step == 0:
        return False
    if step > 0:
        if value < start or value > end:
            return False
        return (value - start) % step == 0
    if value > start or value < end:
        return False
    return (start - value) % abs(step) == 0
