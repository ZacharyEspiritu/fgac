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

from typing import Iterable, List, Sequence, Set, TypeVar


JsonDict = dict[str, object]
T = TypeVar("T")


def chunks(items: Sequence[T], chunk_size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


def pct(part: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{100.0 * part / total:.2f}%"


def format_chars(chars: Set[str]) -> str:
    if not chars:
        return "<none>"
    return ", ".join(repr(ch) for ch in sorted(chars, key=ord))


def format_char_sequence(chars: Sequence[str]) -> str:
    if not chars:
        return "<none>"
    return ", ".join(repr(ch) for ch in chars)


def unique_chars(chars: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(chars))
