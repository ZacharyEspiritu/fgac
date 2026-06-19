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

from typing import Dict, List, Sequence, Tuple

from reconstruction.types import DbConnection, DbRow, DbValue
from util.sql_utils import validate_identifier


class GroundTruth:
    """Snapshot of the target table projected to the attack attributes."""

    def __init__(self, attributes: Sequence[str], rows: List[DbRow]):
        self._attributes = tuple(attributes)
        self._rows = rows
        self._values_for: Dict[str, set[DbValue]] = {
            attr: {row[i] for row in rows} for i, attr in enumerate(self._attributes)
        }
        self._tuple_set_cache: Dict[Tuple[str, ...], set[DbRow]] = {}
        self._prefix_index_cache: Dict[
            Tuple[Tuple[str, ...], str], Dict[DbRow, set[DbValue]]
        ] = {}

    @classmethod
    def load(cls, admin: DbConnection, table: str, attributes: Sequence[str]) -> "GroundTruth":
        validate_identifier(table)
        for attr in attributes:
            validate_identifier(attr)
        cols = ", ".join(attributes)
        with admin.cursor() as cur:
            cur.execute(f"SELECT {cols} FROM {table}")
            rows = [tuple(row) for row in cur.fetchall()]
        return cls(attributes, rows)

    def values_for(self, attr: str) -> set[DbValue]:
        return self._values_for[attr]

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def tuple_set(self, attrs: Sequence[str]) -> set[DbRow]:
        key = tuple(attrs)
        cached = self._tuple_set_cache.get(key)
        if cached is None:
            indices = [self._attributes.index(a) for a in key]
            cached = {tuple(row[i] for i in indices) for row in self._rows}
            self._tuple_set_cache[key] = cached
        return cached

    def _prefix_index(
        self, prefix_attrs: Sequence[str], next_attr: str
    ) -> Dict[DbRow, set[DbValue]]:
        cache_key = (tuple(prefix_attrs), next_attr)
        cached_index = self._prefix_index_cache.get(cache_key)
        if cached_index is None:
            prefix_indices = [self._attributes.index(a) for a in prefix_attrs]
            next_index = self._attributes.index(next_attr)
            cached_index = {}
            for row in self._rows:
                prefix_tuple = tuple(row[i] for i in prefix_indices)
                cached_index.setdefault(prefix_tuple, set()).add(row[next_index])
            self._prefix_index_cache[cache_key] = cached_index
        return cached_index

    def matching_values_for_prefix(
        self,
        prefix_attrs: Sequence[str],
        prefix_values: DbRow,
        next_attr: str,
    ) -> set[DbValue]:
        index = self._prefix_index(prefix_attrs, next_attr)
        return index.get(tuple(prefix_values), set())
