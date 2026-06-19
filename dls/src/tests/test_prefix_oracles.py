from __future__ import annotations

import pytest

from enumerator.constants import (
    PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX,
    PREFIX_QUERY_MODE_SPAN_PREFIX,
)
from enumerator.prefix_oracles import (
    MatchPhrasePrefixOracle,
    PrefixProbe,
    PrefixScorePolicy,
    SpanPrefixOracle,
    match_phrase_prefix_bodies,
    prefix_oracle_for_name,
    span_prefix_bodies,
    span_prefix_query,
)


def test_prefix_oracle_factory_supports_scoring_oracles_only() -> None:
    assert isinstance(
        prefix_oracle_for_name(PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX),
        MatchPhrasePrefixOracle,
    )
    assert isinstance(prefix_oracle_for_name(PREFIX_QUERY_MODE_SPAN_PREFIX), SpanPrefixOracle)

    with pytest.raises(ValueError, match="unknown prefix query mode"):
        prefix_oracle_for_name("match_phrase_prefix_crowdout")


def test_span_prefix_query_uses_context_span_near_and_scoring_prefix() -> None:
    query = span_prefix_query("ctx abc")

    assert query == {
        "span_near": {
            "clauses": [
                {"span_term": {"text": "ctx"}},
                {
                    "span_multi": {
                        "match": {
                            "prefix": {
                                "text": {
                                    "value": "abc",
                                    "rewrite": "scoring_boolean",
                                }
                            }
                        }
                    }
                },
            ],
            "slop": 0,
            "in_order": True,
        }
    }


def test_span_prefix_query_single_token_uses_span_multi_only() -> None:
    assert span_prefix_query("abc") == {
        "span_multi": {
            "match": {
                "prefix": {
                    "text": {
                        "value": "abc",
                        "rewrite": "scoring_boolean",
                    }
                }
            }
        }
    }


def test_span_prefix_query_rejects_empty_query() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        span_prefix_query("")


def test_match_phrase_prefix_bodies_honor_field_and_max_expansions() -> None:
    bodies = match_phrase_prefix_bodies(
        ["ctx abc"],
        max_expansions=7,
        field="text._4gram",
    )

    assert bodies == [
        {
            "size": 1,
            "_source": False,
            "query": {
                "match_phrase_prefix": {
                    "text._4gram": {
                        "query": "ctx abc",
                        "max_expansions": 7,
                    }
                }
            },
        }
    ]


def test_span_prefix_bodies_wrap_queries_for_msearch() -> None:
    bodies = span_prefix_bodies(["abc", "ctx abc"])

    assert bodies[0]["size"] == 1
    assert bodies[0]["_source"] is False
    assert bodies[0]["query"] == span_prefix_query("abc")
    assert bodies[1]["query"] == span_prefix_query("ctx abc")


@pytest.mark.parametrize(
    ("prefix", "candidate_score", "control_score", "expected"),
    [
        ("abc", 1.0, 2.0, True),
        ("abc", 2.0, 1.0, False),
        ("abcdefghijklmnopqrst", 1.0, 2.0, True),
        ("abcdefghijklmnopqrstu", 2.0, 1.0, True),
        ("abcdefghijklmnopqrstu", 1.0, 2.0, False),
    ],
)
def test_search_as_you_type_span_policy_switches_direction_after_index_prefix_limit(
    prefix: str,
    candidate_score: float,
    control_score: float,
    expected: bool,
) -> None:
    policy = PrefixScorePolicy.search_as_you_type_span_prefix()

    assert policy.indicates_match(prefix, candidate_score, control_score) is expected


def test_plain_text_policy_uses_candidate_gt_control() -> None:
    policy = PrefixScorePolicy.plain_text()

    assert policy.direction_for_prefix("abc") == "candidate_gt_control"
    assert policy.indicates_match("abc", 2.0, 1.0)
    assert not policy.indicates_match("abc", 1.0, 2.0)


def test_scoring_oracle_evaluate_pairs_candidate_and_control_scores() -> None:
    class FakeRunner:
        max_expansions = 5
        prefix_score_policy = PrefixScorePolicy.plain_text()

        def search_scores(self, bodies, labels, *, query_kind):
            assert query_kind == PREFIX_QUERY_MODE_SPAN_PREFIX
            assert len(bodies) == 4
            assert labels[0] == "span_prefix='ctx ab'"
            return [2.0, 1.0, 0.5, 1.5]

    probes = [
        PrefixProbe(prefix="ab", control="xy", context="ctx"),
        PrefixProbe(prefix="cd", control="zz", context="ctx2"),
    ]

    assert SpanPrefixOracle().evaluate(FakeRunner(), probes) == {
        "ab": True,
        "cd": False,
    }
