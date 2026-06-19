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

import operator
from typing import List, Sequence, cast

from reconstruction.candidates import CandidateSpec, PartsValues, RangeValues
from reconstruction.sql.queries import build_in_any_query, build_parts_where
from reconstruction.truth.models import BackendLike
from reconstruction.types import ComparableValue, DbCursor, DbValue
from util.sql_utils import validate_identifier


def fetch_lex_range_values_for_binary(
    cur: DbCursor, table: str, column: str, spec: CandidateSpec
) -> List[DbValue]:
    """Return DISTINCT row values in the binary-search query's lexicographic span."""
    validate_identifier(table)
    validate_identifier(column)
    values = spec.search_values
    if values is None:
        return []
    total = len(values)
    if total <= 0:
        return []
    low = values.value_at(0)
    high = values.value_at(total - 1)
    low_cmp = cast(ComparableValue, low)
    high_cmp = cast(ComparableValue, high)
    if operator.gt(low_cmp, high_cmp):
        low, high = high, low
    query = f"SELECT DISTINCT {column} FROM {table} WHERE {column} BETWEEN %s AND %s"
    cur.execute(query, (low, high))
    return [cast(DbValue, row[0]) for row in cur.fetchall()]


def fetch_truth_values_for_binary(
    cur: DbCursor, table: str, column: str, spec: CandidateSpec
) -> List[DbValue]:
    validate_identifier(table)
    validate_identifier(column)
    values = spec.search_values
    if values is None:
        return []
    total = len(values)
    if total <= 0:
        return []
    if isinstance(values, RangeValues):
        low = values.value_at(0)
        high = values.value_at(total - 1)
        low_cmp = cast(ComparableValue, low)
        high_cmp = cast(ComparableValue, high)
        if operator.gt(low_cmp, high_cmp):
            low, high = high, low
        query = f"SELECT DISTINCT {column} FROM {table} WHERE {column} BETWEEN %s AND %s"
        cur.execute(query, (low, high))
        return [cast(DbValue, row[0]) for row in cur.fetchall()]
    if isinstance(values, PartsValues):
        where, params = build_parts_where(column, values)
        query = f"SELECT DISTINCT {column} FROM {table} WHERE {where}"
        cur.execute(query, params)
        return [cast(DbValue, row[0]) for row in cur.fetchall()]
    raise RuntimeError(f"binary_search verification requires range or parts spec for {column}")


def fetch_truth_values_for_in(
    cur: DbCursor,
    table: str,
    column: str,
    values: Sequence[DbValue],
    backend: BackendLike,
) -> List[DbValue]:
    if not values:
        return []
    validate_identifier(table)
    validate_identifier(column)
    if backend.name != "postgres":
        raise RuntimeError("IN strategy with ANY() requires PostgreSQL")
    query = build_in_any_query(table, column, backend.param, select_expr=f"DISTINCT {column}")
    cur.execute(query, (list(values),))
    return [cast(DbValue, row[0]) for row in cur.fetchall()]
