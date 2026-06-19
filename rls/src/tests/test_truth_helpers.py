from __future__ import annotations

from reconstruction.candidates import CandidateSpec, LiteralValues, RangeValues
from reconstruction.truth.ground_truth import GroundTruth
from reconstruction.truth.models import CorrectnessStats, compute_correctness
from reconstruction.verification import verify_recovered_attribute


def test_compute_correctness_counts_true_false_positives_and_false_negatives() -> None:
    assert compute_correctness([1, 2, 3], [2, 3, 4]) == (2, 1, 1)
    assert compute_correctness(["a", "a"], ["a"]) == (1, 0, 0)


def test_correctness_stats_update_and_merge() -> None:
    stats = CorrectnessStats()
    for guess, truth in [(1, 1), (1, 0), (0, 1), (0, 0), (1, None)]:
        stats.update(guess, truth)

    other = CorrectnessStats(tp=2, fp=1, fn=0, total=3)
    stats.merge(other)

    assert stats.tp == 3
    assert stats.fp == 2
    assert stats.fn == 1
    assert stats.total == 8


def test_ground_truth_indexes_values_tuples_and_prefix_matches() -> None:
    truth = GroundTruth(
        ["age", "zip_code", "site_id"],
        [(30, "10001", 1), (30, "10002", 2), (40, "10001", 1)],
    )

    assert truth.row_count == 3
    assert truth.values_for("age") == {30, 40}
    assert truth.tuple_set(["age", "zip_code"]) == {
        (30, "10001"),
        (30, "10002"),
        (40, "10001"),
    }
    assert truth.matching_values_for_prefix(["age"], (30,), "zip_code") == {"10001", "10002"}
    assert truth.matching_values_for_prefix(["age"], (99,), "zip_code") == set()


def test_verify_recovered_attribute_uses_ground_truth_for_binary_and_skip_probe() -> None:
    truth = GroundTruth(["age"], [(20,), (30,), (40,)])
    spec = CandidateSpec(
        values=RangeValues(20, 50, 10),
        binary_search=True,
        search_values=RangeValues(20, 50, 10),
        skip_probe=True,
    )
    binary_counts: dict[str, tuple[int, int, int]] = {}
    sampled_counts: dict[str, tuple[int, int, int]] = {}

    verify_recovered_attribute(
        True,
        truth,
        admin=None,
        backend=object(),
        table="patients",
        attr="age",
        spec=spec,
        recovered_values=[20, 50],
        binary_verify_counts=binary_counts,
        sampled_verify_counts=sampled_counts,
    )

    assert binary_counts == {"age": (1, 1, 2)}
    assert sampled_counts == {"age": (1, 1, 2)}


def test_verify_recovered_attribute_does_nothing_when_disabled() -> None:
    counts: dict[str, tuple[int, int, int]] = {}

    verify_recovered_attribute(
        False,
        GroundTruth(["name"], [("alice",)]),
        admin=None,
        backend=object(),
        table="patients",
        attr="name",
        spec=CandidateSpec(values=LiteralValues(["alice"]), skip_probe=True),
        recovered_values=[],
        binary_verify_counts=counts,
        sampled_verify_counts=counts,
    )

    assert counts == {}
