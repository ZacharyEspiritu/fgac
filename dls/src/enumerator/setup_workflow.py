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
from typing import List, Optional, cast

from .analyzer import (
    AnalyzerTrieTraversal,
    build_analyzer_trie_traversal,
    text_field_analyzer,
)
from .args import EnumeratorOptions
from .constants import (
    INDEX_NAME,
    PREFIX_QUERY_MODE_SPAN_PREFIX,
)
from .corpus import load_texts_from_jsonl
from .prefix_oracles import PrefixScorePolicy
from .search_backend import SearchBackend, response_body
from .search_env import auto_batch_byte_budget, load_hidden_docs, recreate_text_index
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict, format_char_sequence


def load_selected_corpus(options: EnumeratorOptions) -> List[str]:
    if options.corpus_file is None:
        raise RuntimeError("--corpus-file is required unless --keep-index is set")
    return load_texts_from_jsonl(options.corpus_file)


def print_loaded_corpus(options: EnumeratorOptions, loaded: int) -> None:
    print(f"Loaded {loaded} hidden docs from local corpus file {options.corpus_file}")


def load_corpus_into_index(
    options: EnumeratorOptions,
    backend: SearchBackend,
    admin_client: SearchClient,
    telemetry: TimingTelemetry,
) -> Optional[List[str]]:
    if options.keep_index:
        return None

    recreate_text_index(
        admin_client,
        text_field_type=options.index.text_field_type,
        use_index_prefixes=options.index.use_index_prefixes,
        index_prefix_min_chars=options.index.index_prefix_min_chars,
        index_prefix_max_chars=options.index.index_prefix_max_chars,
        search_as_you_type_max_shingle_size=(
            options.index.search_as_you_type_max_shingle_size
        ),
        analyze_max_token_count=options.index.analyze_max_token_count,
        telemetry=telemetry,
    )
    if options.index.text_field_type == "search_as_you_type":
        print(
            "Created search_as_you_type text index "
            f"(max_shingle_size={options.index.search_as_you_type_max_shingle_size})"
        )
    else:
        print(
            "Created text index without search_as_you_type"
            + (" using index_prefixes" if options.index.use_index_prefixes else "")
        )

    loaded_texts = load_selected_corpus(options)
    loaded = load_hidden_docs(
        backend,
        admin_client,
        loaded_texts,
        id_prefix="hidden-file-",
        telemetry=telemetry,
    )
    print_loaded_corpus(options, loaded)
    return loaded_texts


def print_index_mapping(
    admin_client: SearchClient,
    telemetry: TimingTelemetry,
) -> JsonDict:
    with telemetry.opensearch("indices.get_mapping.print"):
        mapping = response_body(admin_client.indices.get_mapping(index=INDEX_NAME))
    index_mapping = mapping.get(INDEX_NAME, mapping)
    normalized_mapping = index_mapping.get("mappings", index_mapping)
    print("index mapping:")
    print(json.dumps(normalized_mapping, indent=2, sort_keys=True))
    return cast(JsonDict, normalized_mapping)


def build_traversal(
    options: EnumeratorOptions,
    admin_client: SearchClient,
    telemetry: TimingTelemetry,
) -> AnalyzerTrieTraversal:
    traversal = build_analyzer_trie_traversal(
        text_field_analyzer(admin_client, telemetry=telemetry),
        options.chars,
        options.joiner_chars,
    )
    if not traversal.token_chars:
        raise SystemExit("No analyzer-stable token chars remain in --chars")

    print(
        f"Attack token chars ({len(traversal.token_chars)}): "
        f"{format_char_sequence(traversal.token_chars)}"
    )
    print(
        f"Attack joiner chars ({len(traversal.joiner_chars)}): "
        f"{format_char_sequence(traversal.joiner_chars)}"
    )
    print(f"Probe suffix char: {traversal.probe_suffix_char!r}")
    if traversal.ignored_chars:
        print(
            "Ignored analyzer-unstable chars: "
            f"{format_char_sequence(traversal.ignored_chars)}"
        )
    return traversal


def resolve_batch_config(
    options: EnumeratorOptions,
    admin_client: SearchClient,
    telemetry: TimingTelemetry,
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    if options.batch.size is not None:
        return options.batch.size, None, None

    budget, max_content_length = auto_batch_byte_budget(
        admin_client,
        max_content_ratio=options.batch.auto_max_content_ratio,
        telemetry=telemetry,
    )
    print(
        "Auto batch size: "
        f"budget={budget} bytes "
        f"({options.batch.auto_max_content_ratio:.3%} of "
        f"http.max_content_length={max_content_length} bytes), "
        f"max_probes={options.batch.auto_max_probes}"
    )
    return None, budget, options.batch.auto_max_probes


def prefix_score_policy_for_mapping(options: EnumeratorOptions) -> PrefixScorePolicy:
    if options.prefix_query_mode == PREFIX_QUERY_MODE_SPAN_PREFIX:
        if options.index.text_field_type == "search_as_you_type":
            return PrefixScorePolicy.search_as_you_type_span_prefix()
        return PrefixScorePolicy.root_text_span_prefix()
    if options.index.text_field_type == "search_as_you_type":
        return PrefixScorePolicy.prefix_field("search_as_you_type")
    if options.index.use_index_prefixes:
        return PrefixScorePolicy.index_prefixes(
            options.index.index_prefix_min_chars,
            options.index.index_prefix_max_chars,
        )
    return PrefixScorePolicy.plain_text()
