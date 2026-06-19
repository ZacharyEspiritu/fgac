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

import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, cast

from .constants import ADMIN_PASSWORD, INDEX_NAME, USER_PASSWORD
from .telemetry import TimingTelemetry
from .types import SearchClient
from .utils import JsonDict


BACKEND_NAMES = ("opensearch", "elasticsearch")


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def response_body(response: Any) -> Any:
    """Return the JSON body for both opensearch-py and elasticsearch-py responses."""

    return getattr(response, "body", response)


@dataclass(frozen=True)
class SearchBackend:
    name: str
    product_label: str
    env_prefix: str
    default_host: str
    default_port: int
    default_scheme: str
    default_admin_username: str
    default_user_username: str

    def env(self, suffix: str, default: str) -> str:
        return os.environ.get(
            f"{self.env_prefix}_{suffix}",
            os.environ.get(f"SEARCH_{suffix}", default),
        )

    @property
    def host(self) -> str:
        return self.env("HOST", self.default_host)

    @property
    def port(self) -> int:
        return int(self.env("PORT", str(self.default_port)))

    @property
    def scheme(self) -> str:
        return self.env("SCHEME", self.default_scheme)

    @property
    def verify_certs(self) -> bool:
        return env_bool(
            f"{self.env_prefix}_VERIFY_CERTS",
            env_bool("SEARCH_VERIFY_CERTS", False),
        )

    @property
    def admin_username(self) -> str:
        return self.env("ADMIN_USERNAME", self.default_admin_username)

    @property
    def admin_password(self) -> str:
        return self.env("ADMIN_PASSWORD", ADMIN_PASSWORD)

    @property
    def user_username(self) -> str:
        return self.env("USER_USERNAME", self.default_user_username)

    @property
    def user_password(self) -> str:
        return self.env("USER_PASSWORD", USER_PASSWORD)

    def connect_admin(
        self,
        *,
        timeout: int = 120,
        max_retries: int = 3,
        retry_on_timeout: bool = True,
    ) -> SearchClient:
        return self.connect(
            self.admin_username,
            self.admin_password,
            timeout=timeout,
            max_retries=max_retries,
            retry_on_timeout=retry_on_timeout,
        )

    def connect_user(
        self,
        *,
        timeout: int = 120,
        max_retries: int = 3,
        retry_on_timeout: bool = True,
    ) -> SearchClient:
        return self.connect(
            self.user_username,
            self.user_password,
            timeout=timeout,
            max_retries=max_retries,
            retry_on_timeout=retry_on_timeout,
        )

    def connect(
        self,
        username: str,
        password: str,
        *,
        timeout: int = 120,
        max_retries: int = 3,
        retry_on_timeout: bool = True,
    ) -> SearchClient:
        if self.name == "opensearch":
            try:
                from opensearchpy import OpenSearch
            except ImportError as e:
                raise RuntimeError(
                    "opensearchpy is required for --backend opensearch"
                ) from e

            return cast(
                SearchClient,
                OpenSearch(
                    hosts=[{"host": self.host, "port": self.port}],
                    http_auth=(username, password),
                    http_compress=True,
                    use_ssl=self.scheme == "https",
                    verify_certs=self.verify_certs,
                    ssl_assert_hostname=False,
                    ssl_show_warn=False,
                    timeout=timeout,
                    max_retries=max_retries,
                    retry_on_timeout=retry_on_timeout,
                ),
            )

        if self.name == "elasticsearch":
            try:
                from elasticsearch import Elasticsearch
            except ImportError as e:
                raise RuntimeError(
                    "elasticsearch is required for --backend elasticsearch; "
                    "run setup.sh or install it with "
                    "`uv pip install --python .venv/bin/python 'elasticsearch>=8,<9'`"
                ) from e

            return cast(
                SearchClient,
                Elasticsearch(
                    [f"{self.scheme}://{self.host}:{self.port}"],
                    basic_auth=(username, password),
                    http_compress=True,
                    verify_certs=self.verify_certs,
                    request_timeout=timeout,
                    max_retries=max_retries,
                    retry_on_timeout=retry_on_timeout,
                ),
            )

        raise ValueError(f"unknown search backend: {self.name}")

    def bulk(
        self,
        client: SearchClient,
        body: Sequence[JsonDict],
        *,
        refresh: bool,
    ) -> object:
        if self.name == "elasticsearch":
            return client.bulk(operations=body, refresh=refresh)
        return client.bulk(body=body, refresh=refresh)

    def msearch(
        self,
        client: SearchClient,
        *,
        index: str,
        body: Sequence[JsonDict],
    ) -> Mapping[str, Any]:
        if self.name == "elasticsearch":
            return cast(
                Mapping[str, Any],
                response_body(client.msearch(index=index, searches=body)),
            )
        return cast(Mapping[str, Any], response_body(client.msearch(index=index, body=body)))

    def helpers_bulk(
        self,
        client: SearchClient,
        actions: Iterable[Mapping[str, object]],
        *,
        chunk_size: int,
        request_timeout: int,
    ) -> object:
        if self.name == "elasticsearch":
            try:
                from elasticsearch import helpers as elasticsearch_helpers
            except ImportError as e:
                raise RuntimeError(
                    "elasticsearch is required for --backend elasticsearch"
                ) from e
            bulk: Callable[..., object] = elasticsearch_helpers.bulk
        else:
            try:
                from opensearchpy import helpers as opensearch_helpers
            except ImportError as e:
                raise RuntimeError(
                    "opensearchpy is required for --backend opensearch"
                ) from e
            bulk = opensearch_helpers.bulk

        return bulk(
            client,
            actions,
            chunk_size=chunk_size,
            request_timeout=request_timeout,
        )

    def ensure_dls_user(
        self,
        client: SearchClient,
        telemetry: Optional[TimingTelemetry] = None,
    ) -> None:
        if self.name == "elasticsearch":
            self.ensure_elasticsearch_dls_user(client, telemetry=telemetry)
            return
        self.ensure_opensearch_dls_user(client, telemetry=telemetry)

    def ensure_opensearch_dls_user(
        self,
        client: SearchClient,
        telemetry: Optional[TimingTelemetry] = None,
    ) -> None:
        try:
            if telemetry is None:
                client.security.delete_user(username=self.user_username)
            else:
                with telemetry.opensearch("security.delete_user"):
                    client.security.delete_user(username=self.user_username)
        except Exception:
            pass
        try:
            if telemetry is None:
                client.security.delete_role(role="test-role")
            else:
                with telemetry.opensearch("security.delete_role"):
                    client.security.delete_role(role="test-role")
        except Exception:
            pass

        role_body = {
            "cluster_permissions": ["cluster_monitor"],
            "index_permissions": [
                {
                    "index_patterns": [INDEX_NAME],
                    "allowed_actions": ["read"],
                    "dls": '{"term": { "public": true }}',
                }
            ],
            "tenant_permissions": [],
        }
        user_body = {
            "password": self.user_password,
            "opendistro_security_roles": ["test-role"],
        }
        if telemetry is None:
            client.security.create_role(role="test-role", body=role_body)
            client.security.create_user(username=self.user_username, body=user_body)
        else:
            with telemetry.opensearch("security.create_role"):
                client.security.create_role(role="test-role", body=role_body)
            with telemetry.opensearch("security.create_user"):
                client.security.create_user(username=self.user_username, body=user_body)

    def ensure_elasticsearch_dls_user(
        self,
        client: SearchClient,
        telemetry: Optional[TimingTelemetry] = None,
    ) -> None:
        try:
            if telemetry is None:
                client.security.delete_user(username=self.user_username)
            else:
                with telemetry.opensearch("security.delete_user"):
                    client.security.delete_user(username=self.user_username)
        except Exception:
            pass
        try:
            if telemetry is None:
                client.security.delete_role(name="test-role")
            else:
                with telemetry.opensearch("security.delete_role"):
                    client.security.delete_role(name="test-role")
        except Exception:
            pass

        role_kwargs = {
            "name": "test-role",
            "cluster": ["monitor"],
            "indices": [
                {
                    "names": [INDEX_NAME],
                    "privileges": ["read"],
                    "query": '{"term": { "public": true }}',
                }
            ],
        }
        user_kwargs = {
            "username": self.user_username,
            "password": self.user_password,
            "roles": ["test-role"],
            "enabled": True,
        }
        if telemetry is None:
            client.security.put_role(**role_kwargs)
            client.security.put_user(**user_kwargs)
        else:
            with telemetry.opensearch("security.create_role"):
                client.security.put_role(**role_kwargs)
            with telemetry.opensearch("security.create_user"):
                client.security.put_user(**user_kwargs)

    def connection_config(self) -> dict[str, object]:
        return {
            "backend": self.name,
            "product_label": self.product_label,
            "host": self.host,
            "port": self.port,
            "scheme": self.scheme,
            "verify_certs": self.verify_certs,
            "admin_username": self.admin_username,
            "user_username": self.user_username,
            "index_name": INDEX_NAME,
        }


def backend_from_name(name: str) -> SearchBackend:
    if name == "opensearch":
        return SearchBackend(
            name="opensearch",
            product_label="OpenSearch",
            env_prefix="OPENSEARCH",
            default_host="localhost",
            default_port=9200,
            default_scheme="https",
            default_admin_username="admin",
            default_user_username="user",
        )
    if name == "elasticsearch":
        return SearchBackend(
            name="elasticsearch",
            product_label="Elasticsearch",
            env_prefix="ELASTICSEARCH",
            default_host="localhost",
            default_port=9201,
            default_scheme="http",
            default_admin_username="elastic",
            default_user_username="user",
        )
    raise ValueError(f"unknown search backend: {name}")
