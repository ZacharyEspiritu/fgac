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

from typing import List, Sequence, Tuple

from reconstruction.candidates import PartsValues
from reconstruction.types import DbValue
from util.sql_utils import validate_identifier


def build_query(
    table: str,
    attributes: Sequence[str],
    select_expr: str = "*",
    limit_clause: str = "",
) -> str:
    validate_identifier(table)
    for attr in attributes:
        validate_identifier(attr)
    where = " AND ".join(f"{attr} = %s" for attr in attributes)
    return f"SELECT {select_expr} FROM {table} WHERE {where}{limit_clause}"


def build_range_query(
    table: str,
    column: str,
    select_expr: str = "*",
    limit_clause: str = "",
) -> str:
    validate_identifier(table)
    validate_identifier(column)
    return f"SELECT {select_expr} FROM {table} WHERE {column} BETWEEN %s AND %s{limit_clause}"


def build_tuple_range_query(
    table: str,
    prefix_attrs: Sequence[str],
    range_attr: str,
    select_expr: str = "*",
    limit_clause: str = "",
) -> str:
    validate_identifier(table)
    for attr in prefix_attrs:
        validate_identifier(attr)
    validate_identifier(range_attr)
    prefixes = [f"{attr} = %s" for attr in prefix_attrs]
    range_clause = f"{range_attr} BETWEEN %s AND %s"
    where = " AND ".join(prefixes + [range_clause]) if prefixes else range_clause
    return f"SELECT {select_expr} FROM {table} WHERE {where}{limit_clause}"


def build_in_any_query(
    table: str,
    column: str,
    param: str,
    select_expr: str = "*",
    limit_clause: str = "",
) -> str:
    validate_identifier(table)
    validate_identifier(column)
    return (
        f"SELECT {select_expr} FROM {table} WHERE {column} = ANY({param}){limit_clause}"
    )


def build_tuple_in_any_query(
    table: str,
    prefix_attrs: Sequence[str],
    in_attr: str,
    param: str,
    select_expr: str = "*",
    limit_clause: str = "",
) -> str:
    validate_identifier(table)
    for attr in prefix_attrs:
        validate_identifier(attr)
    validate_identifier(in_attr)
    prefixes = [f"{attr} = {param}" for attr in prefix_attrs]
    in_clause = f"{in_attr} = ANY({param})"
    where = " AND ".join(prefixes + [in_clause]) if prefixes else in_clause
    return f"SELECT {select_expr} FROM {table} WHERE {where}{limit_clause}"


def build_tuple_between_query(
    table: str,
    prefix_attrs: Sequence[str],
    range_attr: str,
    param: str,
    select_expr: str = "*",
    limit_clause: str = "",
) -> str:
    validate_identifier(table)
    for attr in prefix_attrs:
        validate_identifier(attr)
    validate_identifier(range_attr)
    prefixes = [f"{attr} = {param}" for attr in prefix_attrs]
    range_clause = f"{range_attr} BETWEEN {param} AND {param}"
    where = " AND ".join(prefixes + [range_clause]) if prefixes else range_clause
    return f"SELECT {select_expr} FROM {table} WHERE {where}{limit_clause}"


def build_parts_where(
    column: str, parts_values: PartsValues
) -> Tuple[str, List[DbValue]]:
    validate_identifier(column)
    clauses: List[str] = []
    params: List[DbValue] = []
    pos = 1
    sep = parts_values.separator
    sep_len = len(sep)
    for idx, (start, end, step, width) in enumerate(parts_values.parts):
        if idx > 0 and sep_len > 0:
            clauses.append(f"SUBSTRING({column} FROM {pos} FOR {sep_len}) = %s")
            params.append(sep)
            pos += sep_len
        expr = f"CAST(SUBSTRING({column} FROM {pos} FOR {width}) AS INTEGER)"
        min_val = min(start, end)
        max_val = max(start, end)
        clauses.append(f"{expr} BETWEEN %s AND %s")
        params.extend([min_val, max_val])
        step_abs = abs(step)
        if step_abs > 1:
            clauses.append(f"(({expr} - %s) % %s = 0)")
            params.extend([min_val, step_abs])
        pos += width
    return " AND ".join(clauses), params
