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

from typing import List, Optional, Tuple

from reconstruction.candidates.specs import CandidateConfig, CandidateSpec
from reconstruction.candidates.values import (
    CandidateValues,
    CompositeValues,
    LiteralValues,
    PartsValues,
    RangeValues,
)
from reconstruction.types import DbValue, JsonObject, JsonValue, SupportsValueAt


def _validate_range_spec(spec: JsonObject) -> Tuple[int, int, int, Optional[str]]:
    start = spec.get("start")
    end = spec.get("end")
    if start is None or end is None:
        raise ValueError("Range spec requires 'start' and 'end'")
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("Range spec 'start' and 'end' must be integers")
    step_raw = spec.get("step")
    if step_raw is None:
        step = 1 if end >= start else -1
    elif isinstance(step_raw, int):
        step = step_raw
    else:
        raise ValueError("Range spec 'start', 'end', and 'step' must be integers")
    if step == 0:
        raise ValueError("Range spec 'step' cannot be 0")
    fmt_raw = spec.get("format")
    fmt = fmt_raw if isinstance(fmt_raw, str) else None
    return int(start), int(end), int(step), fmt


def expand_range_spec(spec: JsonObject) -> RangeValues:
    start, end, step, fmt = _validate_range_spec(spec)
    return RangeValues(start=start, end=end, step=step, fmt=fmt)


def expand_parts_spec(spec: JsonObject) -> PartsValues:
    parts = spec.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Parts spec requires a non-empty 'parts' list")
    separator_raw = spec.get("separator", "-")
    if not isinstance(separator_raw, str):
        raise ValueError("Parts spec 'separator' must be a string")
    separator = separator_raw
    part_defs: List[Tuple[int, int, int, int]] = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("Each part must be a dict with start/end/width")
        start = part.get("start")
        end = part.get("end")
        width = part.get("width")
        if start is None or end is None or width is None:
            raise ValueError("Each part requires 'start', 'end', and 'width'")
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError("Part 'start' and 'end' must be integers")
        if not isinstance(width, int) or width <= 0:
            raise ValueError("Part 'width' must be a positive integer")
        step_raw = part.get("step")
        if step_raw is None:
            step = 1 if end >= start else -1
        elif isinstance(step_raw, int):
            step = step_raw
        else:
            raise ValueError("Part 'step' must be an integer")
        if step == 0:
            raise ValueError("Part 'step' cannot be 0")
        part_defs.append((int(start), int(end), int(step), int(width)))

    return PartsValues(parts=part_defs, separator=separator)


def normalize_candidates(raw: CandidateConfig) -> CandidateValues:
    if isinstance(raw, dict):
        if "parts" in raw:
            return expand_parts_spec(raw)
        spec = raw.get("range", raw)
        if not isinstance(spec, dict):
            raise ValueError("Range spec must be a dict")
        return expand_range_spec(spec)
    if isinstance(raw, list):
        segments: List[CandidateValues] = []
        literal_values: List[DbValue] = []
        for item in raw:
            if isinstance(item, dict):
                if "parts" in item:
                    segments.append(expand_parts_spec(item))
                else:
                    spec = item.get("range", item)
                    if not isinstance(spec, dict):
                        raise ValueError("Range segment must be a dict")
                    segments.append(expand_range_spec(spec))
            else:
                literal_values.append(_coerce_literal_value(item))
        if literal_values:
            segments.append(LiteralValues(literal_values))
        if len(segments) == 1:
            return segments[0]
        return CompositeValues(segments)
    return LiteralValues([_coerce_literal_value(raw)])


def parse_candidate_spec(raw: CandidateConfig) -> CandidateSpec:
    binary = False
    tuple_in = False
    skip_probe = False
    values_raw: CandidateConfig = raw
    values: CandidateValues
    if isinstance(raw, dict):
        strategy = raw.get("strategy")
        if strategy:
            if strategy == "binary":
                binary = True
            elif strategy == "tuple_in":
                tuple_in = True
            else:
                raise ValueError(f"Unsupported strategy: {strategy}")
        if raw.get("binary_search") is True:
            binary = True
        if raw.get("tuple_in") is True:
            tuple_in = True
        if raw.get("skip_probe") is True:
            skip_probe = True
        if "values" in raw:
            values_raw = raw["values"]
        elif "range" in raw or "parts" in raw:
            values_raw = {
                key: value
                for key, value in raw.items()
                if key not in ("binary_search", "strategy")
            }

        values = normalize_candidates(values_raw)
        search_values = _as_indexed_values(values)
    else:
        values = normalize_candidates(values_raw)
        search_values = _as_indexed_values(values)
    if binary and search_values is None:
        raise ValueError("binary_search requires a range or parts spec")
    return CandidateSpec(
        values=values,
        binary_search=binary,
        search_values=search_values,
        tuple_in=tuple_in,
        skip_probe=skip_probe,
    )


def _coerce_literal_value(value: JsonValue) -> DbValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError("Candidate literal values must be JSON scalar values")


def _as_indexed_values(values: CandidateValues) -> Optional[SupportsValueAt]:
    if isinstance(values, (RangeValues, PartsValues)):
        return values
    return None
