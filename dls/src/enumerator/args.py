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

import argparse
import re
import secrets
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, cast

from .constants import (
    EXACT_STRATEGIES,
    PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX,
    PREFIX_QUERY_MODES,
)
from .search_backend import BACKEND_NAMES
from .utils import JsonDict


@dataclass(frozen=True)
class BatchOptions:
    size: Optional[int]
    auto_max_content_ratio: float
    auto_max_probes: int


@dataclass(frozen=True)
class IndexOptions:
    text_field_type: str
    use_index_prefixes: bool
    index_prefix_min_chars: int
    index_prefix_max_chars: int
    search_as_you_type_max_shingle_size: int
    analyze_max_token_count: int


@dataclass(frozen=True)
class ProgressOptions:
    interval: float
    attack_log_file: Optional[str]
    progress_tty: bool
    rich_force_terminal: bool


@dataclass(frozen=True)
class NgramOptions:
    recover: bool
    size: int
    max_expansions: int


@dataclass(frozen=True)
class EnumeratorOptions:
    backend: str
    chars: str
    joiner_chars: str
    max_term_len: Optional[int]
    corpus_file: Optional[str]
    batch: BatchOptions
    exact_strategy: str
    max_expansions: int
    prefix_query_mode: str
    index: IndexOptions
    progress: ProgressOptions
    verbose_prefixes: bool
    exact_check_inferred_leaves: bool
    stats_file: str
    ngrams: NgramOptions
    random_seed: int
    random_seed_source: str
    keep_index: bool


def build_parser(prog: str = "python3 -m enumerator") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Enumerate hidden terms using prefix scoring oracles on a text or "
            "search_as_you_type field."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=BACKEND_NAMES,
        default="opensearch",
        help=(
            "Search engine backend to attack. opensearch preserves the existing "
            "local setup; elasticsearch uses the Elastic security APIs."
        ),
    )
    parser.add_argument(
        "--chars",
        default=string.ascii_lowercase,
        help=(
            "Characters to use as ordinary token characters. Analyzer-internal "
            "joiners passed here are automatically moved to the joiner alphabet."
        ),
    )
    parser.add_argument(
        "--joiner-chars",
        default="",
        help=(
            "Analyzer-internal token joiners, such as apostrophe or dot for the "
            "standard text analyzer. These are only explored between token chars."
        ),
    )
    parser.add_argument(
        "--max-term-len",
        type=int,
        default=None,
        help="Optional maximum term length to explore. Omit for unbounded exploration.",
    )
    parser.add_argument(
        "--corpus-file",
        default=None,
        help="Local JSONL corpus file to load. Required unless --keep-index is set.",
    )
    parser.add_argument(
        "--batch-size",
        default="1000",
        help=(
            "Number of same-length prefixes/exact candidates to include in each "
            "batched probe, or 'auto' to split by request byte size."
        ),
    )
    parser.add_argument(
        "--auto-batch-max-content-ratio",
        type=float,
        default=0.5,
        help=(
            "When --batch-size auto is used, cap each bulk/msearch request at "
            "this fraction of the search backend http.max_content_length."
        ),
    )
    parser.add_argument(
        "--auto-batch-max-probes",
        type=int,
        default=1000,
        help=(
            "When --batch-size auto is used, also cap each prefix/exact probe "
            "batch at this many candidates to avoid huge _msearch executions."
        ),
    )
    parser.add_argument(
        "--exact-strategy",
        choices=EXACT_STRATEGIES,
        default="eager",
        help=(
            "Exact-check strategy. eager is the baseline that exact-checks every "
            "prefix candidate. optimized combines prefix-negative pruning, "
            "level-wide exact batching, and leaf inference."
        ),
    )
    parser.add_argument(
        "--max-expansions",
        type=int,
        default=10000,
        help=(
            "match_phrase_prefix max_expansions value for 1-gram probe score "
            "queries when --prefix-query-mode match_phrase_prefix is selected."
        ),
    )
    parser.add_argument(
        "--prefix-query-mode",
        choices=PREFIX_QUERY_MODES,
        default=PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX,
        help=(
            "1-gram prefix oracle query to use. match_phrase_prefix preserves "
            "the old oracle behavior. span_prefix uses root-field "
            "span_near/span_multi prefix probes, which avoids search_as_you_type "
            "shingle subfields while preserving score-comparison recovery. "
            "--recover-ngrams still uses match_phrase_prefix against the SAYT "
            "_2gram, _3gram, and _4gram fields."
        ),
    )
    parser.add_argument(
        "--text-field-type",
        choices=["text", "search_as_you_type"],
        default="text",
        help=(
            "Mapping type for the attacked text field when recreating the index. "
            "Use search_as_you_type to run the match_phrase_prefix oracle against "
            "a field that has search-as-you-type subfields."
        ),
    )
    parser.add_argument(
        "--search-as-you-type-max-shingle-size",
        type=int,
        default=4,
        help=(
            "max_shingle_size for --text-field-type search_as_you_type when "
            "recreating the index."
        ),
    )
    parser.add_argument(
        "--analyze-max-token-count",
        type=int,
        default=1_000_000,
        help=(
            "index.analyze.max_token_count to set when recreating the index. "
            "This only affects _analyze calls used for ground-truth statistics; "
            "set to 0 to leave the backend default."
        ),
    )
    parser.add_argument(
        "--keep-index",
        action="store_true",
        help="Use the existing test index instead of recreating it and loading a corpus file.",
    )
    parser.add_argument(
        "--use-index-prefixes",
        action="store_true",
        help=(
            "Also enable text.index_prefixes. The old match_phrase_prefix oracle "
            "is known to work with this script's default plain text mapping."
        ),
    )
    parser.add_argument("--index-prefix-min-chars", type=int, default=1)
    parser.add_argument("--index-prefix-max-chars", type=int, default=19)
    parser.add_argument(
        "--verbose-prefixes",
        action="store_true",
        help="Print every tested prefix and oracle outcome.",
    )
    parser.add_argument(
        "--progress-interval",
        type=float,
        default=10.0,
        help=(
            "Enable live stderr progress when positive. The rich display "
            "refreshes continuously; this value controls the plain-text "
            "fallback cadence. Set to 0 to disable."
        ),
    )
    parser.add_argument(
        "--attack-log-file",
        default=None,
        help=(
            "Redirect normal stdout/stderr output to this log file. Intended "
            "for wrapper scripts that show Rich progress separately."
        ),
    )
    parser.add_argument(
        "--attack-progress-tty",
        action="store_true",
        help="Render Rich progress directly to /dev/tty when available.",
    )
    parser.add_argument(
        "--rich-force-terminal",
        action="store_true",
        help="Force Rich terminal rendering for wrapper-managed progress output.",
    )
    parser.add_argument(
        "--exact-check-inferred-leaves",
        action="store_true",
        help=(
            "Exact-check terminal prefixes that optimized traversal would "
            "otherwise accept as inferred leaves."
        ),
    )
    parser.add_argument(
        "--stats-file",
        default="enumerator_stats.json",
        help="Path to write JSON run statistics. Set to an empty string to disable.",
    )
    parser.add_argument(
        "--recover-ngrams",
        action="store_true",
        help=(
            "After recovering 1-grams on a search_as_you_type field, recover "
            "2-, 3-, or 4-gram shingles by extending recovered lower-order "
            "n-grams with the recovered 1-gram trie."
        ),
    )
    parser.add_argument(
        "--ngram-size",
        type=int,
        default=2,
        choices=(2, 3, 4),
        help="Largest shingle size to recover with --recover-ngrams.",
    )
    parser.add_argument(
        "--ngram-max-expansions",
        type=int,
        default=10000,
        help="match_phrase_prefix max_expansions for shingle-field n-gram probes.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help=(
            "Seed for randomized controls and contexts. If omitted, the script "
            "generates a fresh 64-bit seed at startup and records it in the "
            "statistics file."
        ),
    )
    return parser


def parse_options(
    argv: Optional[Sequence[str]] = None,
    *,
    prog: str = "python3 -m enumerator",
) -> EnumeratorOptions:
    namespace = build_parser(prog=prog).parse_args(argv)
    return options_from_namespace(namespace)


def options_from_namespace(namespace: argparse.Namespace) -> EnumeratorOptions:
    batch_size = parse_batch_size(cast(str, namespace.batch_size))
    random_seed = cast(Optional[int], namespace.random_seed)
    random_seed_source = "provided"
    if random_seed is None:
        random_seed = secrets.randbits(64)
        random_seed_source = "generated"

    return EnumeratorOptions(
        backend=cast(str, namespace.backend),
        chars=cast(str, namespace.chars),
        joiner_chars=cast(str, namespace.joiner_chars),
        max_term_len=cast(Optional[int], namespace.max_term_len),
        corpus_file=cast(Optional[str], namespace.corpus_file),
        batch=BatchOptions(
            size=batch_size,
            auto_max_content_ratio=cast(float, namespace.auto_batch_max_content_ratio),
            auto_max_probes=cast(int, namespace.auto_batch_max_probes),
        ),
        exact_strategy=cast(str, namespace.exact_strategy),
        max_expansions=cast(int, namespace.max_expansions),
        prefix_query_mode=cast(str, namespace.prefix_query_mode),
        index=IndexOptions(
            text_field_type=cast(str, namespace.text_field_type),
            use_index_prefixes=cast(bool, namespace.use_index_prefixes),
            index_prefix_min_chars=cast(int, namespace.index_prefix_min_chars),
            index_prefix_max_chars=cast(int, namespace.index_prefix_max_chars),
            search_as_you_type_max_shingle_size=cast(
                int, namespace.search_as_you_type_max_shingle_size
            ),
            analyze_max_token_count=cast(int, namespace.analyze_max_token_count),
        ),
        progress=ProgressOptions(
            interval=cast(float, namespace.progress_interval),
            attack_log_file=cast(Optional[str], namespace.attack_log_file),
            progress_tty=cast(bool, namespace.attack_progress_tty),
            rich_force_terminal=cast(bool, namespace.rich_force_terminal),
        ),
        verbose_prefixes=cast(bool, namespace.verbose_prefixes),
        exact_check_inferred_leaves=cast(bool, namespace.exact_check_inferred_leaves),
        stats_file=cast(str, namespace.stats_file),
        ngrams=NgramOptions(
            recover=cast(bool, namespace.recover_ngrams),
            size=cast(int, namespace.ngram_size),
            max_expansions=cast(int, namespace.ngram_max_expansions),
        ),
        random_seed=random_seed,
        random_seed_source=random_seed_source,
        keep_index=cast(bool, namespace.keep_index),
    )


def parse_batch_size(value: str) -> Optional[int]:
    if value == "auto":
        return None
    try:
        batch_size = int(value)
    except ValueError as e:
        raise SystemExit("--batch-size must be a positive integer or 'auto'") from e
    return batch_size


def validate_options(options: EnumeratorOptions) -> None:
    if not options.chars:
        raise SystemExit("--chars must be non-empty")
    if any(ch.isspace() for ch in options.chars + options.joiner_chars):
        raise SystemExit("--chars and --joiner-chars must not contain whitespace")
    if options.max_term_len is not None and options.max_term_len <= 0:
        raise SystemExit("--max-term-len must be positive")
    if not options.keep_index and not options.corpus_file:
        raise SystemExit("--corpus-file is required unless --keep-index is set")
    if options.batch.size is not None and options.batch.size <= 0:
        raise SystemExit("--batch-size must be positive")
    if not 0 < options.batch.auto_max_content_ratio <= 1:
        raise SystemExit("--auto-batch-max-content-ratio must be in (0, 1]")
    if options.batch.auto_max_probes <= 0:
        raise SystemExit("--auto-batch-max-probes must be positive")
    if options.max_expansions <= 0:
        raise SystemExit("--max-expansions must be positive")
    if options.index.search_as_you_type_max_shingle_size < 2:
        raise SystemExit("--search-as-you-type-max-shingle-size must be >= 2")
    if options.index.text_field_type == "search_as_you_type" and options.index.use_index_prefixes:
        raise SystemExit("--use-index-prefixes only applies to --text-field-type text")
    if options.index.index_prefix_min_chars <= 0:
        raise SystemExit("--index-prefix-min-chars must be positive")
    if options.index.index_prefix_max_chars < options.index.index_prefix_min_chars:
        raise SystemExit("--index-prefix-max-chars must be >= --index-prefix-min-chars")
    if options.index.use_index_prefixes and options.index.index_prefix_max_chars >= 20:
        raise SystemExit("--index-prefix-max-chars must be less than 20")
    if options.progress.interval < 0:
        raise SystemExit("--progress-interval must be non-negative")
    if options.ngrams.recover:
        if options.index.text_field_type != "search_as_you_type":
            raise SystemExit("--recover-ngrams requires --text-field-type search_as_you_type")
        if options.index.search_as_you_type_max_shingle_size < options.ngrams.size:
            raise SystemExit(
                "--search-as-you-type-max-shingle-size must be >= --ngram-size"
            )
        if options.ngrams.max_expansions <= 0:
            raise SystemExit("--ngram-max-expansions must be positive")


def progress_dataset_label(options: EnumeratorOptions) -> str:
    if options.corpus_file:
        match = re.search(r"enron_d(\d+)", options.corpus_file)
        if match:
            return f"D{match.group(1)}"
        return Path(options.corpus_file).stem
    return "existing-index"


def batch_size_for_json(options: EnumeratorOptions) -> str | int:
    return "auto" if options.batch.size is None else options.batch.size


def options_to_json(options: EnumeratorOptions) -> JsonDict:
    return {
        "backend": options.backend,
        "chars": options.chars,
        "joiner_chars": options.joiner_chars,
        "max_term_len": options.max_term_len,
        "corpus_file": options.corpus_file,
        "batch_size": batch_size_for_json(options),
        "auto_batch_max_content_ratio": options.batch.auto_max_content_ratio,
        "auto_batch_max_probes": options.batch.auto_max_probes,
        "exact_strategy": options.exact_strategy,
        "max_expansions": options.max_expansions,
        "prefix_query_mode": options.prefix_query_mode,
        "text_field_type": options.index.text_field_type,
        "search_as_you_type_max_shingle_size": (
            options.index.search_as_you_type_max_shingle_size
        ),
        "analyze_max_token_count": options.index.analyze_max_token_count,
        "keep_index": options.keep_index,
        "use_index_prefixes": options.index.use_index_prefixes,
        "index_prefix_min_chars": options.index.index_prefix_min_chars,
        "index_prefix_max_chars": options.index.index_prefix_max_chars,
        "verbose_prefixes": options.verbose_prefixes,
        "progress_interval": options.progress.interval,
        "attack_log_file": options.progress.attack_log_file,
        "attack_progress_tty": options.progress.progress_tty,
        "rich_force_terminal": options.progress.rich_force_terminal,
        "exact_check_inferred_leaves": options.exact_check_inferred_leaves,
        "stats_file": options.stats_file,
        "recover_ngrams": options.ngrams.recover,
        "ngram_size": options.ngrams.size,
        "ngram_max_expansions": options.ngrams.max_expansions,
        "random_seed": options.random_seed,
        "random_seed_source": options.random_seed_source,
    }
