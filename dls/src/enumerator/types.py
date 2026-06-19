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

from typing import Mapping, Protocol, Sequence


class InfoClient(Protocol):
    def info(self) -> object: ...


class IndicesClient(Protocol):
    def exists(self, *, index: str) -> bool: ...

    def delete(self, *, index: str) -> object: ...

    def create(self, *, index: str, body: Mapping[str, object]) -> object: ...

    def refresh(self, *, index: str) -> object: ...

    def get_mapping(self, *, index: str) -> object: ...

    def get_settings(self, *, index: str, include_defaults: bool) -> object:
        _ = index, include_defaults
        raise NotImplementedError

    def analyze(self, *, index: str, body: Mapping[str, object]) -> object: ...


class NodesClient(Protocol):
    def info(self, *, metric: str) -> object: ...


class LicenseClient(Protocol):
    def get(self) -> object: ...


class SecurityClient(Protocol):
    def delete_user(self, *, username: str) -> object: ...

    def delete_role(self, *, role: str = "", name: str = "") -> object:
        _ = role, name
        raise NotImplementedError

    def create_role(self, *, role: str, body: Mapping[str, object]) -> object:
        _ = role, body
        raise NotImplementedError

    def create_user(self, *, username: str, body: Mapping[str, object]) -> object: ...

    def put_role(self, **kwargs: object) -> object: ...

    def put_user(self, **kwargs: object) -> object: ...


class SearchClient(InfoClient, Protocol):
    indices: IndicesClient
    nodes: NodesClient
    security: SecurityClient
    license: LicenseClient

    def bulk(
        self,
        *,
        body: Sequence[Mapping[str, object]] = (),
        operations: Sequence[Mapping[str, object]] = (),
        refresh: bool,
    ) -> object:
        _ = body, operations, refresh
        raise NotImplementedError

    def msearch(
        self,
        *,
        index: str,
        body: Sequence[Mapping[str, object]] = (),
        searches: Sequence[Mapping[str, object]] = (),
    ) -> object:
        _ = index, body, searches
        raise NotImplementedError
