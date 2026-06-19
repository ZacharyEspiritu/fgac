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

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from .analyzer import AnalyzerTrieTraversal
from .constants import EXACT_CONTROL_CHARS, PREFIX_CONTROL_CHARS
from .enumerator import ExactProbe, MatchPhrasePrefixEnumerator
from .prefix_oracles import MatchPhrasePrefixOracle, PrefixProbe, PrefixScorePolicy
from .search_backend import SearchBackend
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict


@dataclass
class TermTrie:
    terminals: Set[str]
    children: Dict[str, Set[str]] = field(default_factory=dict)

    @classmethod
    def from_terms(
        cls,
        terms: Iterable[str],
        traversal: AnalyzerTrieTraversal,
    ) -> "TermTrie":
        terminals = {term for term in terms if term}
        raw_prefixes = {
            term[:end]
            for term in terminals
            for end in range(1, len(term) + 1)
        }
        children: Dict[str, Set[str]] = {}

        # Reuse the analyzer-derived traversal so extension prefixes follow the
        # same token/joiner rules as unigram enumeration.
        frontier = [""]
        seen = {""}
        while frontier:
            parent = frontier.pop()
            for child in traversal.child_prefixes(parent, max_term_len=None):
                if child not in raw_prefixes:
                    continue
                children.setdefault(parent, set()).add(child)
                if child not in seen:
                    seen.add(child)
                    frontier.append(child)
        return cls(terminals=terminals, children=children)

    def initial_prefixes(self) -> List[str]:
        return sorted(self.children.get("", set()))

    def child_prefixes(self, prefix: str) -> List[str]:
        return sorted(self.children.get(prefix, set()))

    def is_terminal(self, prefix: str) -> bool:
        return prefix in self.terminals


class SearchAsYouTypeNgramEnumerator(MatchPhrasePrefixEnumerator):
    def __init__(
        self,
        admin_client: SearchClient,
        user_client: SearchClient,
        backend: SearchBackend,
        traversal: AnalyzerTrieTraversal,
        *,
        ngram_size: int,
        shingle_field: Optional[str] = None,
        max_term_len: Optional[int],
        max_expansions: int,
        batch_size: Optional[int],
        auto_batch_max_bytes: Optional[int],
        auto_batch_max_probes: Optional[int],
        exact_strategy: str,
        verbose_prefixes: bool,
        progress_interval: float,
        progress_dataset: str = "unknown",
        progress_tty: bool = False,
        rich_force_terminal: bool = False,
        exact_check_inferred_leaves: bool = False,
        telemetry: Optional[TimingTelemetry] = None,
    ) -> None:
        if ngram_size < 2:
            raise ValueError("search_as_you_type n-gram recovery requires n >= 2")
        shingle_field = shingle_field or f"text._{ngram_size}gram"
        super().__init__(
            admin_client,
            user_client,
            backend,
            traversal,
            max_term_len=max_term_len,
            max_expansions=max_expansions,
            batch_size=batch_size,
            auto_batch_max_bytes=auto_batch_max_bytes,
            auto_batch_max_probes=auto_batch_max_probes,
            exact_strategy=exact_strategy,
            prefix_oracle=MatchPhrasePrefixOracle(field=shingle_field),
            prefix_score_policy=PrefixScorePolicy.search_as_you_type_shingle_prefixes(
                shingle_field
            ),
            verbose_prefixes=verbose_prefixes,
            progress_interval=progress_interval,
            progress_dataset=progress_dataset,
            progress_tty=progress_tty,
            rich_force_terminal=rich_force_terminal,
            exact_check_inferred_leaves=exact_check_inferred_leaves,
            telemetry=telemetry,
        )
        self.ngram_size = ngram_size
        self.shingle_field = shingle_field
        self.progress_title = f"{ngram_size}-gram enumeration ({shingle_field})"
        self.used_prefix_controls_by_context: Dict[str, Set[str]] = {}
        self.used_prefix_control_prefixes_by_context: Dict[str, Set[str]] = {}
        self.used_prefix_control_prefix_counts_by_context: Dict[str, Dict[int, int]] = {}
        self.used_exact_controls_by_context: Dict[str, Set[str]] = {}

    def enumerate_ngrams(
        self,
        prefix_ngrams: Sequence[str],
        seed_terms: Sequence[str],
    ) -> Set[str]:
        trie = TermTrie.from_terms(seed_terms, self.traversal)
        expected_prefix_terms = self.ngram_size - 1
        context_phrases = sorted(
            {
                phrase
                for phrase in prefix_ngrams
                if len(phrase.split()) == expected_prefix_terms
            }
        )
        recovered_ngrams: Set[str] = set()

        self.start_progress()
        try:
            for index, context_phrase in enumerate(context_phrases, start=1):
                self.set_progress(
                    f"starting {self.ngram_size}-gram traversal "
                    f"context={index}/{len(context_phrases)} "
                    f"phrase={context_phrase!r}"
                )
                self.enumerate_following_terms(
                    context_phrase,
                    trie,
                    recovered_ngrams,
                )
            return recovered_ngrams
        finally:
            self.stop_progress()

    def enumerate_following_terms(
        self,
        context_phrase: str,
        trie: TermTrie,
        recovered_ngrams: Set[str],
    ) -> None:
        current_level = trie.initial_prefixes()
        known_prefix_results: Optional[Dict[str, bool]] = None
        seen: Set[str] = set()
        level = 0

        while current_level:
            level += 1
            candidates = self.new_extension_candidates(current_level, seen)
            self.set_progress(
                f"{self.ngram_size}-gram context={context_phrase!r} "
                f"level={level} candidates={len(candidates)} "
                f"recovered={len(recovered_ngrams)}"
            )
            if not candidates:
                current_level = []
                known_prefix_results = None
                continue

            if known_prefix_results is None:
                prefix_results = self.extension_prefix_exists_many(
                    context_phrase,
                    candidates,
                )
                self.count_prefix_negative_exact_skips(
                    self.phrase_keyed_results(context_phrase, prefix_results),
                    self.phrases(context_phrase, candidates),
                )
            else:
                prefix_results = known_prefix_results

            positive_prefixes = [
                prefix for prefix in candidates if prefix_results.get(prefix, False)
            ]
            child_prefixes_by_parent, child_candidates = self.trie_children_for(
                trie,
                positive_prefixes,
                seen,
            )
            child_prefix_results = self.extension_prefix_exists_many(
                context_phrase,
                child_candidates,
            )
            self.count_prefix_negative_exact_skips(
                self.phrase_keyed_results(context_phrase, child_prefix_results),
                self.phrases(context_phrase, child_candidates),
            )

            exact_candidates, inferred_leaf_terms = self.classify_ngram_exact_terms(
                trie,
                positive_prefixes,
                child_prefixes_by_parent,
                child_prefix_results,
            )
            exact_results = self.extension_term_exists_many(
                context_phrase,
                exact_candidates,
            )
            self.record_ngram_level_results(
                context_phrase,
                recovered_ngrams,
                positive_prefixes,
                exact_results,
                inferred_leaf_terms,
            )

            current_level = [
                child
                for child in child_candidates
                if child_prefix_results.get(child, False)
            ]
            known_prefix_results = child_prefix_results

    @staticmethod
    def phrases(context_phrase: str, extension_prefixes: Sequence[str]) -> List[str]:
        return [
            f"{context_phrase} {extension_prefix}"
            for extension_prefix in extension_prefixes
        ]

    @staticmethod
    def phrase_keyed_results(
        context_phrase: str,
        results: Dict[str, bool],
    ) -> Dict[str, bool]:
        return {
            f"{context_phrase} {prefix}": value for prefix, value in results.items()
        }

    @staticmethod
    def split_ngram_phrase(phrase: str) -> tuple[str, str]:
        context, extension = phrase.rsplit(" ", 1)
        return context, extension

    @classmethod
    def extension_from_phrase(cls, phrase: str) -> str:
        _, extension = cls.split_ngram_phrase(phrase)
        return extension

    def new_extension_candidates(
        self,
        prefixes: Sequence[str],
        seen: Set[str],
    ) -> List[str]:
        candidates: List[str] = []
        for prefix in prefixes:
            if prefix in seen:
                continue
            seen.add(prefix)
            candidates.append(prefix)
        return candidates

    def trie_children_for(
        self,
        trie: TermTrie,
        prefixes: Sequence[str],
        seen: Set[str],
    ) -> tuple[Dict[str, List[str]], List[str]]:
        children_by_parent: Dict[str, List[str]] = {}
        child_candidates: List[str] = []
        for prefix in prefixes:
            children = [
                child for child in trie.child_prefixes(prefix) if child not in seen
            ]
            children_by_parent[prefix] = children
            child_candidates.extend(children)
        return children_by_parent, list(dict.fromkeys(child_candidates))

    def classify_ngram_exact_terms(
        self,
        trie: TermTrie,
        positive_prefixes: Sequence[str],
        child_prefixes_by_parent: Dict[str, List[str]],
        child_prefix_results: Dict[str, bool],
    ) -> tuple[List[str], Set[str]]:
        exact_candidates: List[str] = []
        inferred_leaf_terms: Set[str] = set()

        for prefix in positive_prefixes:
            if not trie.is_terminal(prefix):
                continue
            positive_children = [
                child
                for child in child_prefixes_by_parent.get(prefix, [])
                if child_prefix_results.get(child, False)
            ]
            if positive_children:
                exact_candidates.append(prefix)
            else:
                if self.exact_check_inferred_leaves:
                    exact_candidates.append(prefix)
                else:
                    inferred_leaf_terms.add(prefix)

        return exact_candidates, inferred_leaf_terms

    def extension_prefix_exists_many(
        self,
        context_phrase: str,
        extension_prefixes: Sequence[str],
    ) -> Dict[str, bool]:
        phrases = self.phrases(context_phrase, extension_prefixes)
        phrase_results = self.prefix_or_term_exists_many(phrases)
        return {
            self.extension_from_phrase(phrase): result
            for phrase, result in phrase_results.items()
        }

    def extension_term_exists_many(
        self,
        context_phrase: str,
        extension_terms: Sequence[str],
    ) -> Dict[str, bool]:
        phrases = self.phrases(context_phrase, extension_terms)
        phrase_results = self.exact_term_exists_many(phrases)
        return {
            self.extension_from_phrase(phrase): result
            for phrase, result in phrase_results.items()
        }

    def prefix_probe_docs(self, probes: Sequence[PrefixProbe]) -> List[JsonDict]:
        candidate_phrases = [
            f"{probe.context} {self.extend_token(probe.prefix)}"
            for probe in probes
        ]
        control_phrases = [
            f"{probe.context} {self.extend_token(probe.control)}"
            for probe in probes
        ]
        return [
            {
                "text": self.join_probe_phrases(candidate_phrases),
                "public": True,
            },
            {
                "text": self.join_probe_phrases(control_phrases),
                "public": True,
            },
        ]

    def exact_probe_docs(self, probes: Sequence[ExactProbe]) -> List[JsonDict]:
        return [
            {
                "text": self.join_probe_phrases([probe.term for probe in probes]),
                "public": True,
            },
            {
                "text": self.join_probe_phrases([probe.control for probe in probes]),
                "public": True,
            },
        ]

    def join_probe_phrases(self, phrases: Sequence[str]) -> str:
        if not phrases:
            return ""
        if len(phrases) == 1:
            return phrases[0]

        parts = [phrases[0]]
        for gap_index, phrase in enumerate(phrases[1:], start=1):
            parts.extend(self.shingle_separator_terms(gap_index))
            parts.append(phrase)
        return " ".join(parts)

    def shingle_separator_terms(self, gap_index: int) -> List[str]:
        # N - 1 separator terms guarantee that no N-token shingle can span two
        # adjacent packed probe phrases without including a separator.
        return [
            self.control_from_index(
                gap_index * self.ngram_size + offset,
                8,
                EXACT_CONTROL_CHARS,
            )
            for offset in range(max(0, self.ngram_size - 1))
        ]

    def record_ngram_level_results(
        self,
        context_phrase: str,
        recovered_ngrams: Set[str],
        positive_prefixes: Sequence[str],
        exact_results: Dict[str, bool],
        inferred_leaf_terms: Set[str],
    ) -> None:
        for extension_prefix in positive_prefixes:
            if extension_prefix in inferred_leaf_terms:
                self.stats.exact_terms_inferred_leaf += 1
                self.record_recovered_ngram(
                    recovered_ngrams,
                    context_phrase,
                    extension_prefix,
                    inferred=True,
                )
                continue

            if exact_results.get(extension_prefix, False):
                self.record_recovered_ngram(
                    recovered_ngrams,
                    context_phrase,
                    extension_prefix,
                )

    def record_recovered_ngram(
        self,
        recovered_ngrams: Set[str],
        context_phrase: str,
        extension_term: str,
        *,
        inferred: bool = False,
    ) -> None:
        ngram = f"{context_phrase} {extension_term}"
        if ngram in recovered_ngrams:
            return
        recovered_ngrams.add(ngram)
        if self.rich_progress_enabled():
            return
        if not self.verbose_prefixes:
            suffix = " (inferred leaf)" if inferred else ""
            print(f"recovered {self.ngram_size}-gram: {ngram}{suffix}")

    def prefix_control_for(self, prefix: str) -> str:
        context_phrase, extension_prefix = self.split_ngram_phrase(prefix)
        used = self.used_prefix_controls_by_context.setdefault(context_phrase, set())
        used_prefixes = self.used_prefix_control_prefixes_by_context.setdefault(
            context_phrase,
            set(),
        )
        used_prefix_counts = (
            self.used_prefix_control_prefix_counts_by_context.setdefault(
                context_phrase,
                {},
            )
        )
        control = self.random_prefix_control_like(
            extension_prefix,
            PREFIX_CONTROL_CHARS,
            used,
            used_prefixes,
            used_prefix_counts,
        )
        return f"{context_phrase} {control}"

    def exact_control_for(self, term: str) -> str:
        context_phrase, extension_term = self.split_ngram_phrase(term)
        used = self.used_exact_controls_by_context.setdefault(context_phrase, set())
        # Exact controls only need to be fresh analyzer-stable tokens in the
        # same n-gram context; unlike prefix controls, they need not match the
        # tested continuation length.
        control = self.random_control_at_least(
            10,
            EXACT_CONTROL_CHARS,
            used,
            disallow=extension_term,
        )
        return f"{context_phrase} {control}"

    def exact_query_field(self) -> str:
        return self.shingle_field
