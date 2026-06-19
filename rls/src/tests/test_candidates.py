from __future__ import annotations

import pytest

from reconstruction.candidates import (
    CompositeValues,
    LiteralValues,
    PartsValues,
    RangeSlice,
    RangeValues,
    normalize_candidates,
    parse_candidate_spec,
)
from reconstruction.candidates.domains import (
    candidate_domain_contains,
    values_in_candidate_domain,
)


def test_range_values_iterate_ascending_descending_and_formatted_values() -> None:
    assert list(RangeValues(1, 5, 2)) == [1, 3, 5]
    assert list(RangeValues(5, 1, -2)) == [5, 3, 1]
    assert list(RangeValues(7, 9, 1, "%03d")) == ["007", "008", "009"]
    assert RangeValues(7, 9, 1, "{:02d}").value_at(1) == "08"
    assert RangeValues(7, 9, 1, "04d").value_at(0) == "0007"
    assert len(RangeValues(5, 1, 1)) == 0


def test_parts_values_iterate_with_and_without_separators() -> None:
    values = PartsValues(parts=[(1, 2, 1, 2), (9, 8, -1, 1)], separator="-")

    assert len(values) == 4
    assert list(values) == ["01-9", "01-8", "02-9", "02-8"]
    assert values.value_at(2) == "02-9"

    compact = PartsValues(parts=[(1, 1, 1, 2), (2, 3, 1, 2)], separator="")
    assert list(compact) == ["0102", "0103"]


def test_parts_values_reject_empty_ranges_and_out_of_range_indexes() -> None:
    with pytest.raises(ValueError, match="Parts range must be non-empty"):
        PartsValues(parts=[(1, 0, 1, 1)])

    values = PartsValues(parts=[(1, 1, 1, 1)])
    with pytest.raises(IndexError, match="Parts index out of range"):
        values.value_at(1)


def test_range_slice_delegates_value_lookup() -> None:
    sliced = RangeSlice(RangeValues(10, 20, 2), start=1, end=3)

    assert len(sliced) == 3
    assert sliced.value_at(0) == 12
    assert sliced.value_at(2) == 16


def test_normalize_candidates_accepts_ranges_parts_literals_and_composites() -> None:
    assert list(normalize_candidates({"start": 1, "end": 3})) == [1, 2, 3]
    assert list(normalize_candidates({"range": {"start": 3, "end": 1}})) == [3, 2, 1]
    assert list(
        normalize_candidates({"parts": [{"start": 1, "end": 2, "width": 2}], "separator": ""})
    ) == ["01", "02"]

    composite = normalize_candidates([{"range": {"start": 1, "end": 2}}, "x", None])
    assert isinstance(composite, CompositeValues)
    assert list(composite) == [1, 2, "x", None]


def test_normalize_candidates_rejects_malformed_specs() -> None:
    malformed_specs = [
        {"start": 1},
        {"start": 1, "end": "3"},
        {"start": 1, "end": 3, "step": 0},
        {"parts": []},
        {"parts": [{"start": 1, "end": 2, "width": 0}]},
        [{"range": "bad"}],
        [{"not": object()}],
    ]

    for raw in malformed_specs:
        with pytest.raises(ValueError):
            normalize_candidates(raw)  # type: ignore[arg-type]


def test_parse_candidate_spec_sets_strategy_flags_and_search_values() -> None:
    spec = parse_candidate_spec(
        {
            "strategy": "binary",
            "skip_probe": True,
            "values": {"range": {"start": 1, "end": 3}},
        }
    )

    assert spec.binary_search is True
    assert spec.skip_probe is True
    assert spec.tuple_in is False
    assert spec.search_values is spec.values
    assert list(spec.values) == [1, 2, 3]

    tuple_spec = parse_candidate_spec({"tuple_in": True, "values": ["a", "b"]})
    assert tuple_spec.tuple_in is True
    assert tuple_spec.binary_search is False
    assert list(tuple_spec.values) == ["a", "b"]


def test_parse_candidate_spec_rejects_unsupported_strategy_and_binary_literals() -> None:
    with pytest.raises(ValueError, match="Unsupported strategy"):
        parse_candidate_spec({"strategy": "unknown", "values": [1, 2]})

    with pytest.raises(ValueError, match="binary_search requires a range or parts spec"):
        parse_candidate_spec({"binary_search": True, "values": [1, 2]})


def test_candidate_domain_contains_ranges_parts_slices_composites_and_literals() -> None:
    range_values = RangeValues(1, 5, 2)
    assert candidate_domain_contains(range_values, 3)
    assert candidate_domain_contains(range_values, "3")
    assert not candidate_domain_contains(range_values, 4)

    formatted = RangeValues(1, 3, 1, "%03d")
    assert candidate_domain_contains(formatted, "002")
    assert not candidate_domain_contains(formatted, "2")

    parts = PartsValues(parts=[(1, 2, 1, 2), (3, 5, 2, 1)], separator="-")
    assert candidate_domain_contains(parts, "01-5")
    assert not candidate_domain_contains(parts, "1-5")
    assert not candidate_domain_contains(parts, "01-4")

    compact_parts = PartsValues(parts=[(1, 1, 1, 2), (2, 2, 1, 2)], separator="")
    assert candidate_domain_contains(compact_parts, "0102")
    assert not candidate_domain_contains(compact_parts, "01-02")

    composite = CompositeValues([LiteralValues(["x"]), RangeSlice(RangeValues(10, 20, 2), 1, 2)])
    assert candidate_domain_contains(composite, "x")
    assert candidate_domain_contains(composite, 14)
    assert not candidate_domain_contains(composite, 10)
    assert not candidate_domain_contains(None, "x")


def test_values_in_candidate_domain_filters_without_expanding_large_domains() -> None:
    domain = CompositeValues([LiteralValues(["a", "b"]), RangeValues(10, 20, 5)])

    assert values_in_candidate_domain(domain, ["a", "c", 10, 15, 16]) == ["a", 10, 15]
    assert values_in_candidate_domain(None, ["a"]) == []
