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
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Set

from util.paths import resolve_artifact_path

from .analyzer import AnalyzerTrieTraversal, TokenAnalyzer
from .utils import format_chars, pct


def load_texts_from_jsonl(path: str) -> List[str]:
    docs_path = resolve_artifact_path(path)
    if not docs_path.exists():
        raise RuntimeError(f"Corpus file does not exist: {docs_path}")

    texts: List[str] = []
    bad_lines: List[int] = []
    with docs_path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Invalid JSON on line {line_number}: {e}") from e
            text = record.get("text") if isinstance(record, dict) else None
            if not isinstance(text, str) or not text.strip():
                bad_lines.append(line_number)
                continue
            texts.append(text)

    if bad_lines:
        raise RuntimeError(
            "Corpus file has empty/non-string text at lines: "
            + ", ".join(str(line) for line in bad_lines[:10])
        )
    if not texts:
        raise RuntimeError(f"Corpus file contains no documents: {docs_path}")
    return texts


@dataclass
class CorpusTermStats:
    raw_terms: Set[str]
    analyzed_terms: Set[str]
    analyzer_removed_terms: Set[str]
    attack_alphabet_terms: Set[str]
    outside_attack_alphabet_terms: Set[str]
    missing_attack_alphabet_chars: Set[str]
    length_limited_terms: Set[str]
    eligible_terms: Set[str]
    recovered_terms: Set[str]


def terms_from_texts(
    texts: Sequence[str],
    *,
    analyzer: TokenAnalyzer,
) -> Set[str]:
    return analyzer.terms_from_texts(texts)


def compute_corpus_term_stats(
    texts: Sequence[str],
    recovered_terms: Set[str],
    *,
    raw_analyzer: TokenAnalyzer,
    indexed_analyzer: TokenAnalyzer,
    traversal: AnalyzerTrieTraversal,
    max_term_len: Optional[int],
) -> CorpusTermStats:
    raw_terms = terms_from_texts(texts, analyzer=raw_analyzer)
    analyzed_terms = terms_from_texts(texts, analyzer=indexed_analyzer)
    attack_alphabet_terms = {
        term for term in analyzed_terms if traversal.contains_only_attack_chars(term)
    }
    outside_attack_alphabet_terms = analyzed_terms - attack_alphabet_terms
    missing_attack_alphabet_chars = traversal.missing_chars(
        outside_attack_alphabet_terms
    )
    if max_term_len is None:
        length_limited_terms: Set[str] = set()
        eligible_terms = set(attack_alphabet_terms)
    else:
        length_limited_terms = {
            term for term in attack_alphabet_terms if len(term) > max_term_len
        }
        eligible_terms = attack_alphabet_terms - length_limited_terms

    return CorpusTermStats(
        raw_terms=raw_terms,
        analyzed_terms=analyzed_terms,
        analyzer_removed_terms=raw_terms - analyzed_terms,
        attack_alphabet_terms=attack_alphabet_terms,
        outside_attack_alphabet_terms=outside_attack_alphabet_terms,
        missing_attack_alphabet_chars=missing_attack_alphabet_chars,
        length_limited_terms=length_limited_terms,
        eligible_terms=eligible_terms,
        recovered_terms=set(recovered_terms),
    )


def print_corpus_term_stats(
    stats: CorpusTermStats,
    *,
    max_term_len: Optional[int],
) -> None:
    recovered_eligible = stats.recovered_terms & stats.eligible_terms
    missing_eligible = stats.eligible_terms - stats.recovered_terms
    extra_recovered = stats.recovered_terms - stats.analyzed_terms

    print("corpus term stats:")
    print(f"  raw source terms: {len(stats.raw_terms)}")
    print(f"  analyzed/indexed terms: {len(stats.analyzed_terms)}")
    print(
        "  raw terms removed or transformed by analyzer: "
        f"{len(stats.analyzer_removed_terms)} "
        f"({pct(len(stats.analyzer_removed_terms), len(stats.raw_terms))} "
        "of raw source terms)"
    )
    print(
        "  indexed terms outside attack alphabet: "
        f"{len(stats.outside_attack_alphabet_terms)} "
        f"({pct(len(stats.outside_attack_alphabet_terms), len(stats.analyzed_terms))} "
        "of analyzed/indexed terms)"
    )
    print(
        "  attack alphabet missing chars in indexed terms: "
        f"{format_chars(stats.missing_attack_alphabet_chars)}"
    )
    if max_term_len is None:
        print("  indexed terms blocked by max term length: 0 (no max-term-len bound)")
    else:
        print(
            "  indexed terms blocked by max term length: "
            f"{len(stats.length_limited_terms)} "
            f"({pct(len(stats.length_limited_terms), len(stats.attack_alphabet_terms))} "
            "of indexed terms in attack alphabet)"
        )
    print(
        "  indexed terms eligible under alphabet/length limits: "
        f"{len(stats.eligible_terms)}"
    )
    print(
        "  recovered eligible indexed terms: "
        f"{len(recovered_eligible)}/{len(stats.eligible_terms)} "
        f"({pct(len(recovered_eligible), len(stats.eligible_terms))})"
    )
    print(
        "  missing eligible indexed terms: "
        f"{len(missing_eligible)} "
        f"({pct(len(missing_eligible), len(stats.eligible_terms))})"
    )
    if extra_recovered:
        print(
            "  recovered terms not present after analysis: "
            f"{len(extra_recovered)} ({sorted(extra_recovered)[:20]})"
        )
