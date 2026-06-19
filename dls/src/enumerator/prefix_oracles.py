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

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence

from .constants import (
    PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX,
    PREFIX_QUERY_MODE_SPAN_PREFIX,
    SHARED_CONTEXT_CHARS,
    SPAN_CONTEXT_CHARS,
)
from .utils import JsonDict


@dataclass(frozen=True)
class PrefixProbe:
    prefix: str
    control: str
    context: str


@dataclass(frozen=True)
class PrefixScorePolicy:
    kind: str
    fixed_direction: Optional[str] = None
    index_prefix_min_chars: Optional[int] = None
    index_prefix_max_chars: Optional[int] = None
    policy_field: Optional[str] = None

    @classmethod
    def plain_text(cls) -> "PrefixScorePolicy":
        return cls(kind="plain_text", fixed_direction="candidate_gt_control")

    @classmethod
    def root_text_span_prefix(cls) -> "PrefixScorePolicy":
        return cls(kind="root_text_span_prefix", fixed_direction="candidate_gt_control")

    @classmethod
    def search_as_you_type_span_prefix(cls) -> "PrefixScorePolicy":
        return cls(
            kind="search_as_you_type_span_prefix",
            index_prefix_min_chars=1,
            index_prefix_max_chars=20,
        )

    @classmethod
    def prefix_field(cls, kind: str) -> "PrefixScorePolicy":
        return cls(kind=kind, fixed_direction="control_gt_candidate")

    @classmethod
    def search_as_you_type_shingle_prefixes(
        cls,
        field: str,
        *,
        min_chars: int = 1,
        max_chars: int = 20,
    ) -> "PrefixScorePolicy":
        return cls(
            kind="search_as_you_type_shingle_prefixes",
            index_prefix_min_chars=min_chars,
            index_prefix_max_chars=max_chars,
            policy_field=field,
        )

    @classmethod
    def index_prefixes(
        cls,
        min_chars: int,
        max_chars: int,
    ) -> "PrefixScorePolicy":
        return cls(
            kind="index_prefixes",
            index_prefix_min_chars=min_chars,
            index_prefix_max_chars=max_chars,
        )

    @property
    def summary_direction(self) -> str:
        return self.fixed_direction or "per_prefix"

    def direction_for_prefix(self, prefix: str) -> str:
        if self.fixed_direction is not None:
            return self.fixed_direction

        index_prefix_policies = (
            "index_prefixes",
            "search_as_you_type_span_prefix",
            "search_as_you_type_shingle_prefixes",
        )
        if self.kind not in index_prefix_policies:
            raise ValueError(f"unknown prefix score policy: {self.kind}")
        if self.index_prefix_min_chars is None or self.index_prefix_max_chars is None:
            raise ValueError("index-prefix policy requires min/max chars")

        if self.index_prefix_min_chars <= len(prefix) <= self.index_prefix_max_chars:
            return "control_gt_candidate"
        return "candidate_gt_control"

    def indicates_match(
        self,
        prefix: str,
        candidate_score: float,
        control_score: float,
    ) -> bool:
        direction = self.direction_for_prefix(prefix)
        if direction == "candidate_gt_control":
            return candidate_score > control_score
        if direction == "control_gt_candidate":
            return control_score > candidate_score
        raise ValueError(f"unknown prefix score direction: {direction}")

    def description(self) -> str:
        if self.kind == "index_prefixes":
            return (
                "index_prefixes "
                f"[{self.index_prefix_min_chars}, {self.index_prefix_max_chars}] "
                "uses control_gt_candidate; outside uses candidate_gt_control"
            )
        if self.kind == "search_as_you_type_span_prefix":
            return (
                "search_as_you_type span_prefix "
                f"[{self.index_prefix_min_chars}, {self.index_prefix_max_chars}] "
                "uses control_gt_candidate; outside uses candidate_gt_control"
            )
        if self.kind == "search_as_you_type_shingle_prefixes":
            return (
                f"{self.policy_field} search_as_you_type shingle _index_prefix "
                f"[{self.index_prefix_min_chars}, {self.index_prefix_max_chars}] "
                "uses control_gt_candidate; outside uses candidate_gt_control"
            )
        return f"{self.kind}: {self.summary_direction}"

    def to_json(self) -> JsonDict:
        return {
            "kind": self.kind,
            "summary_direction": self.summary_direction,
            "index_prefix_min_chars": self.index_prefix_min_chars,
            "index_prefix_max_chars": self.index_prefix_max_chars,
            "policy_field": self.policy_field,
        }


class PrefixOracleRunner(Protocol):
    max_expansions: int
    prefix_score_policy: PrefixScorePolicy

    def search_scores(
        self,
        bodies: Sequence[JsonDict],
        labels: Sequence[str],
        *,
        query_kind: str,
    ) -> List[float]:
        ...

class PrefixOracle(Protocol):
    name: str
    context_chars: str

    def request_bodies(
        self,
        runner: PrefixOracleRunner,
        probes: Sequence[PrefixProbe],
    ) -> List[JsonDict]:
        ...

    def evaluate(
        self,
        runner: PrefixOracleRunner,
        probes: Sequence[PrefixProbe],
    ) -> Dict[str, bool]:
        ...


def context_queries(probes: Sequence[PrefixProbe]) -> List[str]:
    queries: List[str] = []
    for probe in probes:
        queries.extend(
            [
                f"{probe.context} {probe.prefix}",
                f"{probe.context} {probe.control}",
            ]
        )
    return queries


def match_phrase_prefix_bodies(
    queries: Sequence[str],
    *,
    max_expansions: int,
    field: str = "text",
) -> List[JsonDict]:
    return [
        {
            "size": 1,
            "_source": False,
            "query": {
                "match_phrase_prefix": {
                    field: {
                        "query": query,
                        "max_expansions": max_expansions,
                    }
                }
            },
        }
        for query in queries
    ]


def span_prefix_bodies(queries: Sequence[str]) -> List[JsonDict]:
    return [
        {
            "size": 1,
            "_source": False,
            "query": span_prefix_query(query),
        }
        for query in queries
    ]


def span_prefix_query(query: str) -> JsonDict:
    terms = query.split()
    if not terms:
        raise ValueError("span_prefix query cannot be empty")

    clauses: List[JsonDict] = [
        {"span_term": {"text": term}} for term in terms[:-1]
    ]
    clauses.append(
        {
            "span_multi": {
                "match": {
                    "prefix": {
                        "text": {
                            "value": terms[-1],
                            "rewrite": "scoring_boolean",
                        }
                    }
                }
            }
        }
    )
    if len(clauses) == 1:
        return clauses[0]
    return {
        "span_near": {
            "clauses": clauses,
            "slop": 0,
            "in_order": True,
        }
    }


class ScoringPrefixOracle:
    name: str
    query_kind: str
    context_chars = SHARED_CONTEXT_CHARS

    def request_bodies(
        self,
        runner: PrefixOracleRunner,
        probes: Sequence[PrefixProbe],
    ) -> List[JsonDict]:
        return self.query_bodies(runner, context_queries(probes))

    def evaluate(
        self,
        runner: PrefixOracleRunner,
        probes: Sequence[PrefixProbe],
    ) -> Dict[str, bool]:
        queries = context_queries(probes)
        scores = runner.search_scores(
            self.query_bodies(runner, queries),
            [f"{self.query_kind}={query!r}" for query in queries],
            query_kind=self.query_kind,
        )

        results: Dict[str, bool] = {}
        for i, probe in enumerate(probes):
            candidate_score = scores[2 * i]
            control_score = scores[2 * i + 1]
            results[probe.prefix] = runner.prefix_score_policy.indicates_match(
                probe.prefix,
                candidate_score,
                control_score,
            )
        return results

    def query_bodies(
        self,
        runner: PrefixOracleRunner,
        queries: Sequence[str],
    ) -> List[JsonDict]:
        raise NotImplementedError


class MatchPhrasePrefixOracle(ScoringPrefixOracle):
    name = PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX
    query_kind = PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX

    def __init__(self, *, field: str = "text") -> None:
        self.field = field

    def query_bodies(
        self,
        runner: PrefixOracleRunner,
        queries: Sequence[str],
    ) -> List[JsonDict]:
        return match_phrase_prefix_bodies(
            queries,
            max_expansions=runner.max_expansions,
            field=self.field,
        )


class SpanPrefixOracle(ScoringPrefixOracle):
    name = PREFIX_QUERY_MODE_SPAN_PREFIX
    query_kind = PREFIX_QUERY_MODE_SPAN_PREFIX
    context_chars = SPAN_CONTEXT_CHARS

    def query_bodies(
        self,
        runner: PrefixOracleRunner,
        queries: Sequence[str],
    ) -> List[JsonDict]:
        return span_prefix_bodies(queries)


def prefix_oracle_for_name(name: str) -> PrefixOracle:
    if name == PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX:
        return MatchPhrasePrefixOracle()
    if name == PREFIX_QUERY_MODE_SPAN_PREFIX:
        return SpanPrefixOracle()
    raise ValueError(f"unknown prefix query mode: {name}")
