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

from typing import Iterable, MutableMapping, Protocol, Sequence, TypeAlias

from util.db_backend import Connection as DbConnection
from util.db_backend import Cursor as DbCursor

__all__ = [
    "ComparableValue",
    "CounterLike",
    "CsvCell",
    "CsvRow",
    "CsvWriter",
    "DbConnection",
    "DbCounter",
    "DbCursor",
    "DbParams",
    "DbRow",
    "DbValue",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "Summary",
    "SupportsValueAt",
]

DbValue: TypeAlias = str | int | float | bool | None
ComparableValue: TypeAlias = str | int | float
DbRow: TypeAlias = tuple[DbValue, ...]
DbParams: TypeAlias = Sequence[DbValue | Sequence[DbValue]]

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
Summary: TypeAlias = dict[str, JsonValue]

CsvCell: TypeAlias = str | int | float | bool | None
CsvRow: TypeAlias = Sequence[CsvCell]


class CounterLike(Protocol):
    def add(self, label: str, delta: int = 1) -> None: ...


DbCounter: TypeAlias = CounterLike | MutableMapping[str, int]


class CsvWriter(Protocol):
    def writerow(self, row: Iterable[object]) -> object: ...

    def writerows(self, rows: Iterable[Iterable[object]]) -> object: ...


class SupportsValueAt(Protocol):
    def __len__(self) -> int: ...

    def value_at(self, idx: int) -> DbValue: ...
