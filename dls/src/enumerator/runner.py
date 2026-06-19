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
import os
import random
import sys
import time
from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence, TextIO

from .analyzer import AnalyzerTrieTraversal
from .args import (
    EnumeratorOptions,
    parse_options,
    progress_dataset_label,
    validate_options,
)
from .enumerator import MatchPhrasePrefixEnumerator
from .ngram_enumerator import SearchAsYouTypeNgramEnumerator
from .prefix_oracles import prefix_oracle_for_name
from .progress import SetupProgressReporter, rich_available
from .recovery_stats import (
    corpus_shingle_terms,
    ngram_results_to_json,
    ngram_stage_results_to_json,
    print_ngram_recovery_stats,
    print_recovery_stats,
    shingle_field_for_size,
)
from .search_backend import SearchBackend, backend_from_name, response_body
from .search_env import fetch_analyzer_samples, fetch_search_config
from .setup_workflow import (
    build_traversal,
    load_corpus_into_index,
    prefix_score_policy_for_mapping,
    print_index_mapping,
    resolve_batch_config,
)
from .stats_report import build_run_statistics, write_run_statistics
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict


_ATTACK_LOG_HANDLE: Optional[TextIO] = None


@contextmanager
def record_stage(stage_seconds: dict[str, float], name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        stage_seconds[name] = stage_seconds.get(name, 0.0) + (
            time.perf_counter() - started
        )


def initialize_random_seed(options: EnumeratorOptions) -> None:
    random.seed(options.random_seed)
    print(f"Random seed ({options.random_seed_source}): {options.random_seed}")


def redirect_output_to_attack_log_if_requested(options: EnumeratorOptions) -> None:
    global _ATTACK_LOG_HANDLE
    log_file = options.progress.attack_log_file
    if not log_file:
        return

    sys.stdout.flush()
    sys.stderr.flush()
    _ATTACK_LOG_HANDLE = open(log_file, "a", buffering=1)
    os.dup2(_ATTACK_LOG_HANDLE.fileno(), sys.stdout.fileno())
    os.dup2(_ATTACK_LOG_HANDLE.fileno(), sys.stderr.fileno())


def rich_progress_enabled(options: EnumeratorOptions) -> bool:
    return options.progress.interval > 0 and rich_available()


def run(options: EnumeratorOptions) -> int:
    initialize_random_seed(options)
    backend = backend_from_name(options.backend)
    telemetry = TimingTelemetry()
    stage_seconds: dict[str, float] = {}
    dataset_label = progress_dataset_label(options)
    suppress_recovered_terms = rich_progress_enabled(options)
    setup_progress = SetupProgressReporter(
        enabled=options.progress.interval > 0,
        dataset=dataset_label,
        attack_chars=options.chars,
        ngram_size=options.ngrams.size if options.ngrams.recover else None,
        total_steps=6,
        progress_tty=options.progress.progress_tty,
        force_terminal=options.progress.rich_force_terminal,
    )

    setup_progress.start()
    try:
        setup_progress.update("connecting admin client", completed_steps=0)
        with record_stage(stage_seconds, "connect_clients"):
            try:
                admin_client = backend.connect_admin()
            except RuntimeError as e:
                raise SystemExit(str(e)) from e
            with telemetry.opensearch("cluster.info.initial"):
                cluster_info = response_body(admin_client.info())
            search_system = (
                f"{backend.product_label} {cluster_info['version']['number']}"
            )
            setup_progress.set_search_system(search_system)
            print(f"Connected admin client: {search_system}")
        setup_progress.advance("creating index and loading corpus")

        with record_stage(stage_seconds, "load_corpus_into_index"):
            loaded_texts = load_corpus_into_index(
                options,
                backend,
                admin_client,
                telemetry,
            )
        setup_progress.advance("reading index mapping")

        with record_stage(stage_seconds, "print_index_mapping"):
            index_mapping = print_index_mapping(admin_client, telemetry)
        setup_progress.set_index_mapping(json.dumps(index_mapping, indent=2, sort_keys=True))
        setup_progress.advance("building analyzer-aware traversal")

        with record_stage(stage_seconds, "build_analyzer_traversal"):
            traversal = build_traversal(options, admin_client, telemetry)
        setup_progress.advance("resolving batch configuration")

        with record_stage(stage_seconds, "resolve_batch_config"):
            (
                batch_size,
                auto_batch_max_bytes,
                auto_batch_max_probes,
            ) = resolve_batch_config(options, admin_client, telemetry)
        setup_progress.advance("configuring DLS user")

        with record_stage(stage_seconds, "setup_dls_user"):
            backend.ensure_dls_user(admin_client, telemetry=telemetry)
            try:
                user_client = backend.connect_user()
            except RuntimeError as e:
                raise SystemExit(str(e)) from e
        setup_progress.finish()
    finally:
        setup_progress.stop()

    prefix_score_policy = prefix_score_policy_for_mapping(options)
    prefix_oracle = prefix_oracle_for_name(options.prefix_query_mode)

    enumerator = MatchPhrasePrefixEnumerator(
        admin_client,
        user_client,
        backend,
        traversal,
        max_term_len=options.max_term_len,
        max_expansions=options.max_expansions,
        batch_size=batch_size,
        auto_batch_max_bytes=auto_batch_max_bytes,
        auto_batch_max_probes=auto_batch_max_probes,
        exact_strategy=options.exact_strategy,
        prefix_oracle=prefix_oracle,
        prefix_score_policy=prefix_score_policy,
        verbose_prefixes=options.verbose_prefixes,
        progress_interval=options.progress.interval,
        progress_dataset=dataset_label,
        progress_tty=options.progress.progress_tty,
        rich_force_terminal=options.progress.rich_force_terminal,
        exact_check_inferred_leaves=options.exact_check_inferred_leaves,
        telemetry=telemetry,
    )
    with record_stage(stage_seconds, "enumerate_terms"):
        terms = enumerator.enumerate_terms()
    if suppress_recovered_terms:
        print(
            f"recovered words: {len(terms)} terms "
            "(suppressed while rich progress is enabled)"
        )
    else:
        print("recovered words:", sorted(terms))

    ngram_results = recover_ngrams_if_requested(
        options=options,
        admin_client=admin_client,
        user_client=user_client,
        backend=backend,
        traversal=traversal,
        loaded_texts=loaded_texts,
        terms=terms,
        batch_size=batch_size,
        auto_batch_max_bytes=auto_batch_max_bytes,
        auto_batch_max_probes=auto_batch_max_probes,
        dataset_label=dataset_label,
        suppress_recovered_terms=suppress_recovered_terms,
        telemetry=telemetry,
        stage_seconds=stage_seconds,
    )

    with record_stage(stage_seconds, "compute_corpus_stats"):
        corpus_stats = print_recovery_stats(
            admin_client,
            loaded_texts,
            terms,
            traversal,
            options.max_term_len,
            telemetry,
        )
    enumerator.print_stats()
    if options.stats_file:
        with record_stage(stage_seconds, "collect_search_metadata"):
            search_config = fetch_search_config(
                backend,
                admin_client,
                telemetry=telemetry,
            )
            analyzer_samples = fetch_analyzer_samples(
                admin_client,
                telemetry=telemetry,
            )
        telemetry.finish()
        with record_stage(stage_seconds, "write_stats_file"):
            run_stats = build_run_statistics(
                options=options,
                enumerator=enumerator,
                recovered_terms=terms,
                corpus_stats=corpus_stats,
                traversal=traversal,
                timing=telemetry.to_json(),
                stage_seconds=stage_seconds,
                search_config=search_config,
                analyzer_samples=analyzer_samples,
                ngram_results=ngram_results,
            )
            write_run_statistics(options.stats_file, run_stats)
        print(f"Wrote run statistics to {options.stats_file}")
    else:
        telemetry.finish()
    return 0


def recover_ngrams_if_requested(
    *,
    options: EnumeratorOptions,
    admin_client: SearchClient,
    user_client: SearchClient,
    backend: SearchBackend,
    traversal: AnalyzerTrieTraversal,
    loaded_texts: Optional[List[str]],
    terms: set[str],
    batch_size: Optional[int],
    auto_batch_max_bytes: Optional[int],
    auto_batch_max_probes: Optional[int],
    dataset_label: str,
    suppress_recovered_terms: bool,
    telemetry: TimingTelemetry,
    stage_seconds: dict[str, float],
) -> Optional[JsonDict]:
    if not options.ngrams.recover:
        return None

    seed_terms = set(terms)
    seed_prefix_ngrams = set(terms)
    indexed_seed_terms = None
    indexed_prefix_ngrams = None
    if loaded_texts is not None:
        with record_stage(stage_seconds, "compute_ngram_seed_stats"):
            indexed_seed_terms = corpus_shingle_terms(
                admin_client,
                loaded_texts,
                field="text",
                telemetry=telemetry,
            )
            indexed_prefix_ngrams = indexed_seed_terms
    ngram_stage_results: List[JsonDict] = []

    for ngram_size in range(2, options.ngrams.size + 1):
        shingle_field = shingle_field_for_size(ngram_size)
        ngram_enumerator = SearchAsYouTypeNgramEnumerator(
            admin_client,
            user_client,
            backend,
            traversal,
            ngram_size=ngram_size,
            shingle_field=shingle_field,
            max_term_len=options.max_term_len,
            max_expansions=options.ngrams.max_expansions,
            batch_size=batch_size,
            auto_batch_max_bytes=auto_batch_max_bytes,
            auto_batch_max_probes=auto_batch_max_probes,
            exact_strategy=options.exact_strategy,
            verbose_prefixes=options.verbose_prefixes,
            progress_interval=options.progress.interval,
            progress_dataset=dataset_label,
            progress_tty=options.progress.progress_tty,
            rich_force_terminal=options.progress.rich_force_terminal,
            exact_check_inferred_leaves=options.exact_check_inferred_leaves,
            telemetry=telemetry,
        )
        with record_stage(stage_seconds, f"enumerate_{ngram_size}grams"):
            recovered_ngrams = ngram_enumerator.enumerate_ngrams(
                sorted(seed_prefix_ngrams),
                sorted(seed_terms),
            )
        if suppress_recovered_terms:
            print(
                f"recovered {ngram_size}-grams: {len(recovered_ngrams)} "
                "(suppressed while rich progress is enabled)"
            )
        else:
            print(f"recovered {ngram_size}-grams:", sorted(recovered_ngrams))
        with record_stage(stage_seconds, f"compute_{ngram_size}gram_stats"):
            indexed_ngrams = corpus_shingle_terms(
                admin_client,
                loaded_texts,
                field=shingle_field,
                telemetry=telemetry,
            )
            print_ngram_recovery_stats(
                field=shingle_field,
                recovered_ngrams=recovered_ngrams,
                indexed_ngrams=indexed_ngrams,
            )
        print(f"{shingle_field} attack stats:")
        ngram_enumerator.print_stats()
        ngram_stage_results.append(
            ngram_results_to_json(
                ngram_size=ngram_size,
                field=shingle_field,
                seed_terms=seed_terms,
                seed_prefix_ngrams=seed_prefix_ngrams,
                indexed_seed_terms=indexed_seed_terms,
                indexed_prefix_ngrams=indexed_prefix_ngrams,
                recovered_ngrams=recovered_ngrams,
                indexed_ngrams=indexed_ngrams,
                enumerator=ngram_enumerator,
                stage_seconds=stage_seconds,
            )
        )
        seed_prefix_ngrams = recovered_ngrams
        indexed_prefix_ngrams = indexed_ngrams

    return ngram_stage_results_to_json(
        requested_ngram_size=options.ngrams.size,
        stages=ngram_stage_results,
    )


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    prog: str = "python3 -m enumerator",
) -> int:
    options = parse_options(argv, prog=prog)
    redirect_output_to_attack_log_if_requested(options)
    validate_options(options)
    return run(options)
