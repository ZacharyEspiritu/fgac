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

from dataclasses import asdict
from typing import List, Optional, cast

from .analyzer import AnalyzerTrieTraversal, SearchAnalyzer, raw_source_analyzer, text_field_analyzer
from .corpus import CorpusTermStats, compute_corpus_term_stats, print_corpus_term_stats
from .ngram_enumerator import SearchAsYouTypeNgramEnumerator
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict


def print_recovery_stats(
    admin_client: SearchClient,
    loaded_texts: Optional[List[str]],
    recovered_terms: set[str],
    traversal: AnalyzerTrieTraversal,
    max_term_len: Optional[int],
    telemetry: TimingTelemetry,
) -> Optional[CorpusTermStats]:
    if loaded_texts is None:
        print(
            "corpus term stats: unavailable with --keep-index because source texts "
            "were not loaded"
        )
        return None

    corpus_stats = compute_corpus_term_stats(
        loaded_texts,
        recovered_terms,
        raw_analyzer=raw_source_analyzer(admin_client, telemetry=telemetry),
        indexed_analyzer=text_field_analyzer(admin_client, telemetry=telemetry),
        traversal=traversal,
        max_term_len=max_term_len,
    )
    print_corpus_term_stats(corpus_stats, max_term_len=max_term_len)
    return corpus_stats


def shingle_field_for_size(size: int) -> str:
    return f"text._{size}gram"


def corpus_shingle_terms(
    admin_client: SearchClient,
    loaded_texts: Optional[List[str]],
    *,
    field: str,
    telemetry: TimingTelemetry,
) -> Optional[set[str]]:
    if loaded_texts is None:
        return None
    analyzer = SearchAnalyzer(admin_client, {"field": field}, telemetry=telemetry)
    return analyzer.terms_from_texts(loaded_texts)


def print_ngram_recovery_stats(
    *,
    field: str,
    recovered_ngrams: set[str],
    indexed_ngrams: Optional[set[str]],
) -> None:
    print(f"{field} n-gram stats:")
    print(f"  recovered n-grams: {len(recovered_ngrams)}")
    if indexed_ngrams is None:
        print("  indexed n-gram stats: unavailable with --keep-index")
        return

    missing = indexed_ngrams - recovered_ngrams
    extra = recovered_ngrams - indexed_ngrams
    print(f"  indexed n-grams: {len(indexed_ngrams)}")
    print(
        "  recovered indexed n-grams: "
        f"{len(recovered_ngrams & indexed_ngrams)}/{len(indexed_ngrams)}"
    )
    print(f"  missing indexed n-grams: {len(missing)}")
    if missing:
        print(f"  missing indexed n-grams sample: {sorted(missing)[:20]}")
    if extra:
        print(f"  recovered n-grams not present after analysis: {sorted(extra)[:20]}")


def ngram_results_to_json(
    *,
    ngram_size: int,
    field: str,
    seed_terms: set[str],
    seed_prefix_ngrams: set[str],
    indexed_seed_terms: Optional[set[str]],
    indexed_prefix_ngrams: Optional[set[str]],
    recovered_ngrams: set[str],
    indexed_ngrams: Optional[set[str]],
    enumerator: SearchAsYouTypeNgramEnumerator,
    stage_seconds: dict[str, float],
) -> JsonDict:
    attack_stats = cast(JsonDict, asdict(enumerator.stats))
    attack_stats["logical_score_queries"] = enumerator.stats.logical_score_queries
    result: JsonDict = {
        "stage": ngram_size,
        "stage_name": f"{ngram_size}-gram recovery",
        "ngram_size": ngram_size,
        "field": field,
        "seed_terms": sorted(seed_terms),
        "seed_prefix_ngrams": sorted(seed_prefix_ngrams),
        "recovered_ngrams": sorted(recovered_ngrams),
        "attack_stats": attack_stats,
        "configuration": {
            "max_expansions": enumerator.max_expansions,
            "prefix_query_mode": enumerator.prefix_query_mode,
            "prefix_score_policy": enumerator.prefix_score_policy.to_json(),
            "batch_size": enumerator.batch_size,
            "auto_batch_max_bytes": enumerator.auto_batch_max_bytes,
            "auto_batch_max_probes": enumerator.auto_batch_max_probes,
            "exact_check_inferred_leaves": enumerator.exact_check_inferred_leaves,
        },
        "timing": {
            "enumerate_seconds": stage_seconds.get(f"enumerate_{ngram_size}grams"),
            "stats_seconds": stage_seconds.get(f"compute_{ngram_size}gram_stats"),
        },
    }
    if indexed_ngrams is not None:
        missing = indexed_ngrams - recovered_ngrams
        extra = recovered_ngrams - indexed_ngrams
        result["corpus_ngram_stats"] = {
            "counts": {
                "indexed_ngrams": len(indexed_ngrams),
                "recovered_indexed_ngrams": len(recovered_ngrams & indexed_ngrams),
                "missing_indexed_ngrams": len(missing),
                "extra_recovered_ngrams": len(extra),
            },
            "sets": {
                "indexed_ngrams": sorted(indexed_ngrams),
                "missing_indexed_ngrams": sorted(missing),
                "extra_recovered_ngrams": sorted(extra),
            },
        }
        result["seed_coverage"] = ngram_seed_coverage_to_json(
            ngram_size=ngram_size,
            indexed_ngrams=indexed_ngrams,
            missing_ngrams=missing,
            extra_ngrams=extra,
            seed_terms=seed_terms,
            seed_prefix_ngrams=seed_prefix_ngrams,
            indexed_seed_terms=indexed_seed_terms,
            indexed_prefix_ngrams=indexed_prefix_ngrams,
        )
    return result


def ngram_parts(ngram: str, ngram_size: int) -> tuple[str, str]:
    parts = ngram.split()
    if len(parts) != ngram_size:
        raise ValueError(f"expected {ngram_size} terms in n-gram: {ngram!r}")
    return " ".join(parts[:-1]), parts[-1]


def ngram_seed_coverage_to_json(
    *,
    ngram_size: int,
    indexed_ngrams: set[str],
    missing_ngrams: set[str],
    extra_ngrams: set[str],
    seed_terms: set[str],
    seed_prefix_ngrams: set[str],
    indexed_seed_terms: Optional[set[str]],
    indexed_prefix_ngrams: Optional[set[str]],
) -> JsonDict:
    missing_prefix_seed = {
        ngram
        for ngram in indexed_ngrams
        if ngram_parts(ngram, ngram_size)[0] not in seed_prefix_ngrams
    }
    missing_extension_seed = {
        ngram
        for ngram in indexed_ngrams
        if ngram_parts(ngram, ngram_size)[1] not in seed_terms
    }
    missing_any_seed = missing_prefix_seed | missing_extension_seed
    indexed_with_available_seeds = indexed_ngrams - missing_any_seed

    missing_with_missing_prefix_seed = missing_ngrams & missing_prefix_seed
    missing_with_missing_extension_seed = missing_ngrams & missing_extension_seed
    missing_with_any_missing_seed = missing_ngrams & missing_any_seed
    missing_with_available_seeds = missing_ngrams - missing_any_seed

    counts: dict[str, int] = {
        "indexed_ngrams_with_available_seeds": len(indexed_with_available_seeds),
        "indexed_ngrams_with_missing_prefix_seed": len(missing_prefix_seed),
        "indexed_ngrams_with_missing_extension_seed": len(missing_extension_seed),
        "indexed_ngrams_with_any_missing_seed": len(missing_any_seed),
        "missing_ngrams_with_missing_prefix_seed": len(
            missing_with_missing_prefix_seed
        ),
        "missing_ngrams_with_missing_extension_seed": len(
            missing_with_missing_extension_seed
        ),
        "missing_ngrams_with_any_missing_seed": len(missing_with_any_missing_seed),
        "missing_ngrams_with_available_seeds": len(missing_with_available_seeds),
    }
    sets: dict[str, List[str]] = {
        "indexed_ngrams_with_missing_prefix_seed": sorted(missing_prefix_seed),
        "indexed_ngrams_with_missing_extension_seed": sorted(
            missing_extension_seed
        ),
        "missing_ngrams_with_missing_prefix_seed": sorted(
            missing_with_missing_prefix_seed
        ),
        "missing_ngrams_with_missing_extension_seed": sorted(
            missing_with_missing_extension_seed
        ),
        "missing_ngrams_with_available_seeds": sorted(missing_with_available_seeds),
    }

    if indexed_seed_terms is not None:
        extra_nonindexed_extension = {
            ngram
            for ngram in extra_ngrams
            if ngram_parts(ngram, ngram_size)[1] not in indexed_seed_terms
        }
        counts["extra_ngrams_with_nonindexed_extension"] = len(
            extra_nonindexed_extension
        )
        sets["extra_ngrams_with_nonindexed_extension"] = sorted(
            extra_nonindexed_extension
        )

    if indexed_prefix_ngrams is not None:
        extra_nonindexed_prefix = {
            ngram
            for ngram in extra_ngrams
            if ngram_parts(ngram, ngram_size)[0] not in indexed_prefix_ngrams
        }
        counts["extra_ngrams_with_nonindexed_prefix"] = len(extra_nonindexed_prefix)
        sets["extra_ngrams_with_nonindexed_prefix"] = sorted(extra_nonindexed_prefix)

    return {
        "counts": counts,
        "sets": sets,
    }


def ngram_stage_results_to_json(
    *,
    requested_ngram_size: int,
    stages: List[JsonDict],
) -> Optional[JsonDict]:
    if not stages:
        return None
    result: JsonDict = dict(stages[-1])
    result["requested_ngram_size"] = requested_ngram_size
    result["stages"] = stages
    return result
