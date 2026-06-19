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

import random
from typing import List, Optional, Sequence, Tuple, cast

from reconstruction.sql.queries import build_query
from reconstruction.types import DbCounter, DbCursor, DbParams, DbRow, DbValue
from util.sql_utils import validate_identifier
from util.timing import timed_query


NUMERIC_TYPES = {
    "smallint",
    "integer",
    "bigint",
    "numeric",
    "real",
    "double precision",
}


def run_query_min_with_match(
    cur: DbCursor,
    query: str,
    params: DbParams,
    rounds: int,
    counter: Optional[DbCounter] = None,
    label: Optional[str] = None,
    fetch_one: bool = False,
) -> Tuple[int, bool]:
    best: Optional[int] = None
    any_match = False
    for _ in range(rounds):
        if counter is not None and label:
            if hasattr(counter, "add"):
                counter.add(label, 1)
            else:
                counter[label] = counter.get(label, 0) + 1
        elapsed_ns, rowcount = timed_query(cur, query, params, fetch_one=fetch_one)
        if rowcount > 0:
            any_match = True
        if best is None or elapsed_ns < best:
            best = elapsed_ns
    return int(best or 0), any_match


def fetch_column_type(cur: DbCursor, table: str, column: str) -> str:
    cur.execute(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Unknown column {table}.{column}")
    return str(row[0]).lower()


def value_exists(cur: DbCursor, table: str, column: str, value: DbValue) -> bool:
    query = build_query(table, [column], select_expr="1", limit_clause=" LIMIT 1")
    cur.execute(query, (value,))
    return cur.fetchone() is not None


def pick_existing_value(cur: DbCursor, table: str, column: str) -> DbValue:
    validate_identifier(table)
    validate_identifier(column)
    query = f"SELECT {column} FROM {table} LIMIT 1"
    cur.execute(query)
    row = cur.fetchone()
    if not row:
        raise RuntimeError(f"No rows found in {table}")
    return cast(DbValue, row[0])


def pick_missing_value(
    cur: DbCursor, table: str, column: str, data_type: str, rng: random.Random
) -> DbValue:
    validate_identifier(table)
    validate_identifier(column)
    if data_type in NUMERIC_TYPES:
        cur.execute(f"SELECT max({column}) FROM {table}")
        row = cur.fetchone()
        max_val = row[0] if row and row[0] is not None else 0
        candidate = int(max_val) + 1000
        while value_exists(cur, table, column, candidate):
            candidate += 1000
        return candidate
    for _ in range(1000):
        missing_candidate = f"missing_{column}_{rng.randint(0, 1_000_000)}"
        if not value_exists(cur, table, column, missing_candidate):
            return missing_candidate
    raise RuntimeError(f"Unable to find missing value for {column}")


def sample_tuples(
    cur: DbCursor, table: str, attributes: Sequence[str], limit: int
) -> List[DbRow]:
    validate_identifier(table)
    for attr in attributes:
        validate_identifier(attr)
    cur.execute(
        f"SELECT {', '.join(attributes)} FROM {table} LIMIT %s",
        (limit,),
    )
    return [tuple(row) for row in cur.fetchall()]
