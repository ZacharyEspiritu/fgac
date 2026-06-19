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
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Optional, Sequence, cast

from .analyzer import AnalyzerTrieTraversal
from .args import EnumeratorOptions, options_to_json
from .corpus import CorpusTermStats
from .enumerator import MatchPhrasePrefixEnumerator
from .utils import JsonDict, pct


def sorted_terms(terms: Sequence[str] | set[str]) -> list[str]:
    return sorted(terms)


def corpus_stats_to_json(stats: Optional[CorpusTermStats]) -> Optional[JsonDict]:
    if stats is None:
        return None

    recovered_eligible = stats.recovered_terms & stats.eligible_terms
    missing_eligible = stats.eligible_terms - stats.recovered_terms
    extra_recovered = stats.recovered_terms - stats.analyzed_terms

    return {
        "counts": {
            "raw_source_terms": len(stats.raw_terms),
            "analyzed_indexed_terms": len(stats.analyzed_terms),
            "raw_terms_removed_or_transformed_by_analyzer": len(
                stats.analyzer_removed_terms
            ),
            "indexed_terms_in_attack_alphabet": len(stats.attack_alphabet_terms),
            "indexed_terms_outside_attack_alphabet": len(
                stats.outside_attack_alphabet_terms
            ),
            "indexed_terms_blocked_by_max_term_length": len(
                stats.length_limited_terms
            ),
            "eligible_terms": len(stats.eligible_terms),
            "recovered_eligible_terms": len(recovered_eligible),
            "missing_eligible_terms": len(missing_eligible),
            "extra_recovered_terms": len(extra_recovered),
        },
        "percentages": {
            "raw_terms_removed_or_transformed_by_analyzer_of_raw_source_terms": pct(
                len(stats.analyzer_removed_terms),
                len(stats.raw_terms),
            ),
            "indexed_terms_outside_attack_alphabet_of_analyzed_indexed_terms": pct(
                len(stats.outside_attack_alphabet_terms),
                len(stats.analyzed_terms),
            ),
            "indexed_terms_blocked_by_max_term_length_of_attack_alphabet_terms": pct(
                len(stats.length_limited_terms),
                len(stats.attack_alphabet_terms),
            ),
            "recovered_eligible_terms_of_eligible_terms": pct(
                len(recovered_eligible),
                len(stats.eligible_terms),
            ),
            "missing_eligible_terms_of_eligible_terms": pct(
                len(missing_eligible),
                len(stats.eligible_terms),
            ),
        },
        "sets": {
            "raw_source_terms": sorted_terms(stats.raw_terms),
            "analyzed_indexed_terms": sorted_terms(stats.analyzed_terms),
            "raw_terms_removed_or_transformed_by_analyzer": sorted_terms(
                stats.analyzer_removed_terms
            ),
            "indexed_terms_in_attack_alphabet": sorted_terms(
                stats.attack_alphabet_terms
            ),
            "indexed_terms_outside_attack_alphabet": sorted_terms(
                stats.outside_attack_alphabet_terms
            ),
            "attack_alphabet_missing_chars_in_indexed_terms": sorted_terms(
                stats.missing_attack_alphabet_chars
            ),
            "indexed_terms_blocked_by_max_term_length": sorted_terms(
                stats.length_limited_terms
            ),
            "eligible_terms": sorted_terms(stats.eligible_terms),
            "recovered_terms": sorted_terms(stats.recovered_terms),
            "recovered_eligible_terms": sorted_terms(recovered_eligible),
            "missing_eligible_terms": sorted_terms(missing_eligible),
            "extra_recovered_terms": sorted_terms(extra_recovered),
        },
    }


def traversal_to_json(traversal: AnalyzerTrieTraversal) -> JsonDict:
    return {
        "token_chars": traversal.token_chars,
        "joiner_chars": traversal.joiner_chars,
        "joiner_transitions": {
            joiner: {
                left: sorted(rights)
                for left, rights in sorted(transitions.items())
            }
            for joiner, transitions in sorted(traversal.joiner_transitions.items())
        },
        "probe_suffix_char": traversal.probe_suffix_char,
        "ignored_chars": traversal.ignored_chars,
    }


def build_run_statistics(
    *,
    options: EnumeratorOptions,
    enumerator: MatchPhrasePrefixEnumerator,
    recovered_terms: set[str],
    corpus_stats: Optional[CorpusTermStats],
    traversal: AnalyzerTrieTraversal,
    timing: Mapping[str, object],
    stage_seconds: dict[str, float],
    search_config: Mapping[str, object],
    analyzer_samples: Mapping[str, object],
    ngram_results: Optional[JsonDict] = None,
) -> JsonDict:
    attack_stats = cast(JsonDict, asdict(enumerator.stats))
    attack_stats["logical_score_queries"] = enumerator.stats.logical_score_queries
    attack_config = {
        "exact_strategy": enumerator.exact_strategy,
        "batch_size": enumerator.batch_size,
        "auto_batch_max_bytes": enumerator.auto_batch_max_bytes,
        "auto_batch_max_probes": enumerator.auto_batch_max_probes,
        "max_expansions": enumerator.max_expansions,
        "max_term_len": enumerator.max_term_len,
        "prefix_query_mode": enumerator.prefix_query_mode,
        "prefix_score_direction": (
            enumerator.prefix_score_policy.summary_direction
        ),
        "prefix_score_policy": enumerator.prefix_score_policy.to_json(),
        "exact_check_inferred_leaves": enumerator.exact_check_inferred_leaves,
    }
    unigram_stage: JsonDict = {
        "stage": 1,
        "stage_name": "1-gram recovery",
        "ngram_size": 1,
        "field": "text",
        "recovered_terms": sorted_terms(recovered_terms),
        "attack_stats": attack_stats,
        "configuration": attack_config,
        "corpus_term_stats": corpus_stats_to_json(corpus_stats),
        "timing": {
            "enumerate_seconds": stage_seconds.get("enumerate_terms"),
            "stats_seconds": stage_seconds.get("compute_corpus_stats"),
        },
    }
    attack_stages: list[object] = [unigram_stage]
    if ngram_results is not None:
        stages = ngram_results.get("stages", [])
        if isinstance(stages, list):
            attack_stages.extend(stages)

    result: JsonDict = {
        "configuration": {
            "script_args": options_to_json(options),
            "random_seed": options.random_seed,
            "random_seed_source": options.random_seed_source,
            "attack": attack_config,
            "traversal": traversal_to_json(traversal),
        },
        "search_backend": {
            **search_config,
            "analyzer_samples": analyzer_samples,
        },
        "timing": {
            **timing,
            "stage_seconds": dict(sorted(stage_seconds.items())),
        },
        "attack_stats": attack_stats,
        "attack_stages": attack_stages,
        "corpus_term_stats": corpus_stats_to_json(corpus_stats),
        "recovered_terms": sorted_terms(recovered_terms),
    }
    if ngram_results is not None:
        result["ngram_recovery"] = ngram_results
    return result


def write_run_statistics(path: str, stats: Mapping[str, object]) -> None:
    stats_path = Path(path)
    if stats_path.parent != Path("."):
        stats_path.parent.mkdir(parents=True, exist_ok=True)
    with stats_path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write("\n")
