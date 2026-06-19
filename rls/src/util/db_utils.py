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

from typing import Optional, Sequence


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Expected {label} to be an integer, got {type(value).__name__}")
    return value


def fetch_optional_value(cur, sql: str, params: Sequence = ()) -> Optional[object]:
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return row[0]


def fetch_optional_scalar(cur, sql: str, params: Sequence = ()) -> Optional[int]:
    value = fetch_optional_value(cur, sql, params)
    if value is None:
        return None
    return _require_int(value, "scalar value")


def fetch_one(cur, sql: str, params: Sequence = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Expected a row but query returned none")
    if row[0] is None:
        raise RuntimeError("Expected a scalar value but query returned NULL")
    return _require_int(row[0], "scalar value")


def fetch_all(cur, sql: str, params: Sequence = ()):
    cur.execute(sql, params)
    return cur.fetchall()
