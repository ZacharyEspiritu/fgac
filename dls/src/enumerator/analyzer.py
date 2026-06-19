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
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Set

from .constants import INDEX_NAME, PROBE_SUFFIX_CANDIDATES
from .search_backend import response_body
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import unique_chars


class TokenAnalyzer(Protocol):
    """Minimal analyzer interface needed by the trie traversal builder."""

    def tokens(self, text: str) -> List[str]:
        ...

    def terms_from_texts(self, texts: Sequence[str]) -> Set[str]:
        ...

    def emits_single_token(self, text: str) -> bool:
        ...


@dataclass
class SearchAnalyzer:
    client: SearchClient
    analyze_body: Dict[str, object]
    index: str = INDEX_NAME
    telemetry: Optional[TimingTelemetry] = None

    def tokens(self, text: str) -> List[str]:
        body = dict(self.analyze_body)
        body["text"] = text
        if self.telemetry is None:
            response = self.client.indices.analyze(index=self.index, body=body)
        else:
            with self.telemetry.opensearch("indices.analyze"):
                response = self.client.indices.analyze(index=self.index, body=body)
        response = response_body(response)
        return [token["token"] for token in response.get("tokens", [])]

    def terms_from_texts(self, texts: Sequence[str]) -> Set[str]:
        terms: Set[str] = set()
        for text in texts:
            terms.update(self.tokens(text))
        return terms

    def emits_single_token(self, text: str) -> bool:
        return self.tokens(text) == [text]


@dataclass
class AnalyzerTrieTraversal:
    """Analyzer-derived policy for which trie edges are valid indexed tokens."""

    token_chars: List[str]
    joiner_chars: List[str]
    joiner_transitions: Dict[str, Dict[str, Set[str]]]
    probe_suffix_char: str
    ignored_chars: List[str]

    @property
    def attack_chars(self) -> Set[str]:
        return set(self.token_chars) | set(self.joiner_chars)

    def initial_prefixes(self) -> List[str]:
        return list(self.token_chars)

    def contains_only_attack_chars(self, term: str) -> bool:
        attack_chars = self.attack_chars
        return all(ch in attack_chars for ch in term)

    def missing_chars(self, terms: Iterable[str]) -> Set[str]:
        attack_chars = self.attack_chars
        return {
            ch
            for term in terms
            for ch in term
            if ch not in attack_chars
        }

    def child_prefixes(
        self,
        prefix: str,
        *,
        max_term_len: Optional[int],
    ) -> List[str]:
        children: List[str] = []

        def append_if_allowed(child: str) -> None:
            if max_term_len is None or len(child) <= max_term_len:
                children.append(child)

        for ch in self.token_chars:
            append_if_allowed(prefix + ch)

        if not prefix:
            return children

        left = prefix[-1]
        for joiner in self.joiner_chars:
            allowed_next_chars = self.joiner_transitions.get(joiner, {}).get(
                left,
                set(),
            )
            for ch in self.token_chars:
                if ch in allowed_next_chars:
                    append_if_allowed(prefix + joiner + ch)
        return children

    def can_fully_expand_children(
        self,
        prefix: str,
        *,
        max_term_len: Optional[int],
    ) -> bool:
        if max_term_len is None:
            return True

        if self.token_chars and len(prefix) + 1 > max_term_len:
            return False

        if not prefix:
            return True

        left = prefix[-1]
        for joiner in self.joiner_chars:
            if self.joiner_transitions.get(joiner, {}).get(left):
                if len(prefix) + 2 > max_term_len:
                    return False
        return True


def raw_source_analyzer(
    client: SearchClient,
    telemetry: Optional[TimingTelemetry] = None,
) -> SearchAnalyzer:
    return SearchAnalyzer(
        client,
        {"tokenizer": "standard", "filter": ["lowercase"]},
        telemetry=telemetry,
    )


def text_field_analyzer(
    client: SearchClient,
    telemetry: Optional[TimingTelemetry] = None,
) -> SearchAnalyzer:
    return SearchAnalyzer(client, {"field": "text"}, telemetry=telemetry)


def is_stable_token_char(analyzer: TokenAnalyzer, ch: str) -> bool:
    return analyzer.emits_single_token(ch)


def is_stable_joiner_transition(
    analyzer: TokenAnalyzer,
    left: str,
    joiner: str,
    right: str,
) -> bool:
    return analyzer.emits_single_token(f"{left}{joiner}{right}")


def build_joiner_transitions(
    analyzer: TokenAnalyzer,
    token_chars: Sequence[str],
    joiner_chars: Sequence[str],
) -> Dict[str, Dict[str, Set[str]]]:
    transitions: Dict[str, Dict[str, Set[str]]] = {}
    for joiner in joiner_chars:
        transitions[joiner] = {}
        for left in token_chars:
            next_chars = {
                right
                for right in token_chars
                if is_stable_joiner_transition(analyzer, left, joiner, right)
            }
            if next_chars:
                transitions[joiner][left] = next_chars
    return transitions


def choose_probe_suffix_char(
    analyzer: TokenAnalyzer,
    token_chars: Sequence[str],
    joiner_chars: Sequence[str],
) -> str:
    attack_chars = set(token_chars) | set(joiner_chars)
    for ch in PROBE_SUFFIX_CANDIDATES:
        if ch in attack_chars or not is_stable_token_char(analyzer, ch):
            continue
        if all(analyzer.emits_single_token(left + ch) for left in token_chars):
            return ch
    raise RuntimeError(
        "unable to find analyzer-stable probe suffix outside attack alphabet"
    )


def build_analyzer_trie_traversal(
    analyzer: TokenAnalyzer,
    chars: Sequence[str],
    joiner_chars: Sequence[str],
) -> AnalyzerTrieTraversal:
    requested_chars = unique_chars(chars)
    token_chars = [
        ch for ch in requested_chars if is_stable_token_char(analyzer, ch)
    ]
    possible_joiner_chars = unique_chars(
        [ch for ch in requested_chars if ch not in token_chars]
        + [ch for ch in joiner_chars if ch not in token_chars]
    )
    joiner_transitions = build_joiner_transitions(
        analyzer,
        token_chars,
        possible_joiner_chars,
    )
    joiner_chars = [
        ch for ch in possible_joiner_chars if joiner_transitions.get(ch)
    ]
    ignored_chars = [
        ch for ch in possible_joiner_chars if ch not in joiner_chars
    ]

    probe_suffix_char = choose_probe_suffix_char(analyzer, token_chars, joiner_chars)
    return AnalyzerTrieTraversal(
        token_chars=token_chars,
        joiner_chars=joiner_chars,
        joiner_transitions=joiner_transitions,
        probe_suffix_char=probe_suffix_char,
        ignored_chars=unique_chars(ignored_chars),
    )
