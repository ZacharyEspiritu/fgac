from __future__ import annotations

from types import SimpleNamespace

from enumerator.args import parse_options
from enumerator.corpus import CorpusTermStats
from enumerator.enumerator import AttackStats
from enumerator.prefix_oracles import PrefixScorePolicy
from enumerator.stats_report import build_run_statistics, corpus_stats_to_json


def test_attack_stats_schema_has_no_crowdout_counters() -> None:
    stats = AttackStats(
        logical_span_prefix_queries=12,
        logical_term_queries=4,
    )
    options = parse_options(
        [
            "--corpus-file",
            "dataset/enron_d1.jsonl",
            "--prefix-query-mode",
            "span_prefix",
            "--random-seed",
            "1",
        ]
    )
    enumerator = SimpleNamespace(
        stats=stats,
        exact_strategy="optimized",
        batch_size=128,
        auto_batch_max_bytes=None,
        auto_batch_max_probes=None,
        max_expansions=10000,
        max_term_len=None,
        prefix_query_mode="span_prefix",
        prefix_score_policy=PrefixScorePolicy.search_as_you_type_span_prefix(),
        exact_check_inferred_leaves=False,
    )
    traversal = SimpleNamespace(
        token_chars=["a"],
        joiner_chars=[],
        joiner_transitions={},
        probe_suffix_char="x",
        ignored_chars=[],
    )

    result = build_run_statistics(
        options=options,
        enumerator=enumerator,
        recovered_terms={"alpha"},
        corpus_stats=None,
        traversal=traversal,
        timing={},
        stage_seconds={},
        search_config={},
        analyzer_samples={},
    )

    attack_stats = result["attack_stats"]
    assert attack_stats["logical_score_queries"] == 16
    assert "logical_match_phrase_prefix_crowdout_queries" not in attack_stats
    assert "msearch_match_phrase_prefix_crowdout_requests" not in attack_stats
    assert result["attack_stages"][0]["configuration"]["prefix_query_mode"] == "span_prefix"


def test_corpus_stats_to_json_reports_missing_and_extra_terms() -> None:
    stats = CorpusTermStats(
        raw_terms={"alpha", "beta", "gamma"},
        analyzed_terms={"alpha", "beta"},
        analyzer_removed_terms={"gamma"},
        attack_alphabet_terms={"alpha", "beta"},
        outside_attack_alphabet_terms=set(),
        missing_attack_alphabet_chars=set(),
        length_limited_terms={"beta"},
        eligible_terms={"alpha"},
        recovered_terms={"alpha", "extra"},
    )

    result = corpus_stats_to_json(stats)
    assert result is not None
    assert result["counts"]["eligible_terms"] == 1
    assert result["counts"]["recovered_eligible_terms"] == 1
    assert result["counts"]["missing_eligible_terms"] == 0
    assert result["counts"]["extra_recovered_terms"] == 1
    assert result["sets"]["raw_terms_removed_or_transformed_by_analyzer"] == ["gamma"]
    assert result["sets"]["indexed_terms_blocked_by_max_term_length"] == ["beta"]
    assert result["sets"]["extra_recovered_terms"] == ["extra"]


def test_build_run_statistics_appends_ngram_stages() -> None:
    stats = AttackStats(logical_span_prefix_queries=2)
    options = parse_options(
        [
            "--corpus-file",
            "dataset/enron_d1.jsonl",
            "--prefix-query-mode",
            "span_prefix",
            "--random-seed",
            "1",
        ]
    )
    enumerator = SimpleNamespace(
        stats=stats,
        exact_strategy="optimized",
        batch_size=128,
        auto_batch_max_bytes=None,
        auto_batch_max_probes=None,
        max_expansions=10000,
        max_term_len=None,
        prefix_query_mode="span_prefix",
        prefix_score_policy=PrefixScorePolicy.search_as_you_type_span_prefix(),
        exact_check_inferred_leaves=False,
    )
    traversal = SimpleNamespace(
        token_chars=["a"],
        joiner_chars=[],
        joiner_transitions={},
        probe_suffix_char="x",
        ignored_chars=[],
    )

    result = build_run_statistics(
        options=options,
        enumerator=enumerator,
        recovered_terms={"alpha"},
        corpus_stats=None,
        traversal=traversal,
        timing={},
        stage_seconds={},
        search_config={},
        analyzer_samples={},
        ngram_results={"stages": [{"stage_name": "2-gram recovery"}]},
    )

    assert [stage["stage_name"] for stage in result["attack_stages"]] == [
        "1-gram recovery",
        "2-gram recovery",
    ]
    assert result["ngram_recovery"] == {"stages": [{"stage_name": "2-gram recovery"}]}
