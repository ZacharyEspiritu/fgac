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

import re
from typing import Iterable, Optional, Sequence, TypedDict

from .constants import INDEX_NAME
from .search_backend import SearchBackend, response_body
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict


DEFAULT_HTTP_MAX_CONTENT_LENGTH_BYTES = 100 * 1024 * 1024
BYTE_UNITS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}


class AnalyzerSample(TypedDict, total=False):
    body: dict[str, object]
    tokens: list[dict[str, object]]


def parse_byte_size(value: object) -> int:
    if isinstance(value, int):
        return value

    text = str(value).strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?b?)", text)
    if not match:
        raise ValueError(f"unable to parse byte size: {value!r}")

    number, unit = match.groups()
    multiplier = BYTE_UNITS[unit]
    return int(float(number) * multiplier)


def find_setting_values(settings: object, key: str) -> Iterable[object]:
    if isinstance(settings, dict):
        for setting_key, value in settings.items():
            if setting_key == key:
                yield value
            yield from find_setting_values(value, key)
    elif isinstance(settings, list):
        for value in settings:
            yield from find_setting_values(value, key)


def http_max_content_length_bytes(
    client: SearchClient,
    telemetry: Optional[TimingTelemetry] = None,
) -> int:
    values: list[object] = []
    try:
        if telemetry is None:
            response = client.nodes.info(metric="settings")
        else:
            with telemetry.opensearch("nodes.info.settings"):
                response = client.nodes.info(metric="settings")
        response = response_body(response)
        values.extend(find_setting_values(response, "max_content_length"))
    except Exception:
        values = []

    parsed_values: list[int] = []
    for value in values:
        try:
            parsed_values.append(parse_byte_size(value))
        except ValueError:
            continue

    if parsed_values:
        return min(parsed_values)
    return DEFAULT_HTTP_MAX_CONTENT_LENGTH_BYTES


def auto_batch_byte_budget(
    client: SearchClient,
    *,
    max_content_ratio: float,
    telemetry: Optional[TimingTelemetry] = None,
) -> tuple[int, int]:
    max_content_length = http_max_content_length_bytes(client, telemetry=telemetry)
    return max(1, int(max_content_length * max_content_ratio)), max_content_length


def recreate_text_index(
    client: SearchClient,
    *,
    text_field_type: str,
    use_index_prefixes: bool,
    index_prefix_min_chars: int,
    index_prefix_max_chars: int,
    search_as_you_type_max_shingle_size: int,
    analyze_max_token_count: Optional[int],
    telemetry: Optional[TimingTelemetry] = None,
) -> None:
    if telemetry is None:
        index_exists = client.indices.exists(index=INDEX_NAME)
    else:
        with telemetry.opensearch("indices.exists"):
            index_exists = client.indices.exists(index=INDEX_NAME)
    if index_exists:
        if telemetry is None:
            client.indices.delete(index=INDEX_NAME)
        else:
            with telemetry.opensearch("indices.delete"):
                client.indices.delete(index=INDEX_NAME)

    if text_field_type == "search_as_you_type":
        text_mapping = {
            "type": "search_as_you_type",
            "max_shingle_size": search_as_you_type_max_shingle_size,
        }
    else:
        text_mapping = {"type": "text"}

    if use_index_prefixes:
        text_mapping["index_prefixes"] = {
            "min_chars": index_prefix_min_chars,
            "max_chars": index_prefix_max_chars,
        }

    create_body = {
        "mappings": {
            "properties": {
                "text": text_mapping,
                "public": {"type": "boolean"},
            }
        }
    }
    if analyze_max_token_count is not None and analyze_max_token_count > 0:
        create_body["settings"] = {
            "index": {
                "analyze": {
                    "max_token_count": analyze_max_token_count,
                },
            },
        }
    if telemetry is None:
        client.indices.create(index=INDEX_NAME, body=create_body)
    else:
        with telemetry.opensearch("indices.create"):
            client.indices.create(index=INDEX_NAME, body=create_body)


def load_hidden_docs(
    backend: SearchBackend,
    client: SearchClient,
    texts: Sequence[str],
    *,
    id_prefix: str,
    telemetry: Optional[TimingTelemetry] = None,
) -> int:
    actions = (
        {
            "_index": INDEX_NAME,
            "_id": f"{id_prefix}{i}",
            "_source": {"text": text, "public": False},
        }
        for i, text in enumerate(texts)
    )
    if telemetry is None:
        backend.helpers_bulk(client, actions, chunk_size=1000, request_timeout=120)
        client.indices.refresh(index=INDEX_NAME)
    else:
        with telemetry.opensearch("hidden_docs.bulk"):
            backend.helpers_bulk(
                client,
                actions,
                chunk_size=1000,
                request_timeout=120,
            )
        with telemetry.opensearch("indices.refresh.hidden_docs"):
            client.indices.refresh(index=INDEX_NAME)
    return len(texts)


def fetch_search_config(
    backend: SearchBackend,
    client: SearchClient,
    *,
    telemetry: Optional[TimingTelemetry] = None,
) -> JsonDict:
    if telemetry is None:
        info = client.info()
        mappings = client.indices.get_mapping(index=INDEX_NAME)
        settings = client.indices.get_settings(index=INDEX_NAME, include_defaults=True)
    else:
        with telemetry.opensearch("cluster.info"):
            info = client.info()
        with telemetry.opensearch("indices.get_mapping"):
            mappings = client.indices.get_mapping(index=INDEX_NAME)
        with telemetry.opensearch("indices.get_settings"):
            settings = client.indices.get_settings(
                index=INDEX_NAME,
                include_defaults=True,
            )

    metadata: JsonDict = {
        **backend.connection_config(),
        "cluster_info": response_body(info),
        "index_mapping": response_body(mappings),
        "index_settings": response_body(settings),
    }
    if backend.name == "elasticsearch":
        try:
            if telemetry is None:
                license_response = client.license.get()
            else:
                with telemetry.opensearch("license.get"):
                    license_response = client.license.get()
            metadata["license"] = response_body(license_response)
        except Exception as e:
            metadata["license"] = {"error": str(e)}
    return metadata


def fetch_analyzer_samples(
    client: SearchClient,
    *,
    telemetry: Optional[TimingTelemetry] = None,
) -> dict[str, AnalyzerSample]:
    samples: dict[str, AnalyzerSample] = {
        "text_field": {
            "body": {
                "field": "text",
                "text": "This apple can't split a.b and 123",
            }
        },
        "raw_source": {
            "body": {
                "tokenizer": "standard",
                "filter": ["lowercase"],
                "text": "This apple can't split a.b and 123",
            }
        },
    }

    for sample in samples.values():
        body = sample["body"]
        if telemetry is None:
            response = client.indices.analyze(index=INDEX_NAME, body=body)
        else:
            with telemetry.opensearch("indices.analyze.metadata"):
                response = client.indices.analyze(index=INDEX_NAME, body=body)
        response = response_body(response)
        sample["tokens"] = [
            {
                "token": token.get("token"),
                "type": token.get("type"),
                "position": token.get("position"),
                "start_offset": token.get("start_offset"),
                "end_offset": token.get("end_offset"),
            }
            for token in response.get("tokens", [])
        ]
    return samples
