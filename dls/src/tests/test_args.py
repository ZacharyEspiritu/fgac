from __future__ import annotations

import pytest

from enumerator.args import build_parser, parse_options, progress_dataset_label, validate_options
from enumerator.constants import PREFIX_QUERY_MODES


def test_prefix_query_modes_exclude_removed_crowdout_modes() -> None:
    assert PREFIX_QUERY_MODES == ("match_phrase_prefix", "span_prefix")


@pytest.mark.parametrize(
    "removed_mode",
    [
        "match_phrase_prefix_crowdout",
        "match_phrase_prefix_index_prefix_then_crowdout",
    ],
)
def test_removed_prefix_query_modes_are_rejected(removed_mode: str) -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--prefix-query-mode", removed_mode])


def test_span_prefix_is_accepted_for_unigram_recovery() -> None:
    parser = build_parser()
    namespace = parser.parse_args(["--prefix-query-mode", "span_prefix"])

    assert namespace.prefix_query_mode == "span_prefix"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            [
                "--corpus-file",
                "dataset/enron_d1.jsonl",
                "--recover-ngrams",
            ],
            "--recover-ngrams requires --text-field-type search_as_you_type",
        ),
        (
            [
                "--corpus-file",
                "dataset/enron_d1.jsonl",
                "--text-field-type",
                "search_as_you_type",
                "--search-as-you-type-max-shingle-size",
                "3",
                "--recover-ngrams",
                "--ngram-size",
                "4",
            ],
            "--search-as-you-type-max-shingle-size must be >= --ngram-size",
        ),
        (
            [
                "--corpus-file",
                "dataset/enron_d1.jsonl",
                "--chars",
                "a b",
            ],
            "--chars and --joiner-chars must not contain whitespace",
        ),
    ],
)
def test_validate_options_rejects_common_bad_configs(
    argv: list[str],
    message: str,
) -> None:
    options = parse_options(argv)

    with pytest.raises(SystemExit, match=message):
        validate_options(options)


def test_parse_options_supports_auto_batching_and_keep_index() -> None:
    options = parse_options(
        [
            "--keep-index",
            "--batch-size",
            "auto",
            "--auto-batch-max-probes",
            "17",
            "--prefix-query-mode",
            "span_prefix",
        ]
    )

    validate_options(options)
    assert options.batch.size is None
    assert options.batch.auto_max_probes == 17
    assert options.keep_index


def test_progress_dataset_label_uses_enron_size_or_path_stem() -> None:
    enron_options = parse_options(["--corpus-file", "dataset/enron_d100.jsonl"])
    custom_options = parse_options(["--corpus-file", "/tmp/custom-corpus.jsonl"])
    keep_index_options = parse_options(["--keep-index"])

    assert progress_dataset_label(enron_options) == "D100"
    assert progress_dataset_label(custom_options) == "custom-corpus"
    assert progress_dataset_label(keep_index_options) == "existing-index"
