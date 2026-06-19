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

from dataclasses import dataclass
from typing import Tuple

from util.db_backend import DatabaseBackend


@dataclass(frozen=True)
class PatientQuery:
    sql: str
    limit_params: Tuple[int, ...] = ()
    fetch_one_only: bool = False
    range_width: int = 0

    def params_for_key(self, key: int) -> Tuple[int, ...]:
        if self.range_width > 0:
            return (key, key + self.range_width)
        return (key, *self.limit_params)

    def params_for_range(self, start: int, end: int) -> Tuple[int, ...]:
        return (start, end, *self.limit_params)


def build_patient_point_query(backend: DatabaseBackend, fast: bool) -> PatientQuery:
    select_expr = "1" if fast else "*"
    limit = 1 if fast else None
    base_query = f"SELECT {select_expr} FROM patients WHERE id_number = {backend.param}"
    query, limit_params = backend.add_limit(base_query, limit)
    return PatientQuery(
        sql=query,
        limit_params=tuple(limit_params),
        fetch_one_only=fast,
    )


def build_patient_range_query(backend: DatabaseBackend, fast: bool) -> PatientQuery:
    select_expr = "1" if fast else "*"
    limit = 1 if fast else None
    base_query = (
        f"SELECT {select_expr} FROM patients "
        f"WHERE id_number BETWEEN {backend.param} AND {backend.param}"
    )
    query, limit_params = backend.add_limit(base_query, limit)
    return PatientQuery(
        sql=query,
        limit_params=tuple(limit_params),
        fetch_one_only=fast,
    )


def build_noise_query(
    backend: DatabaseBackend,
    mode: str,
    fast: bool,
    range_width: int,
) -> PatientQuery:
    if mode == "point":
        return build_patient_point_query(backend, fast)
    if mode == "range":
        if range_width <= 0:
            raise ValueError("--noise-range-width must be > 0 in range mode")
        query = (
            f"SELECT count(*) FROM patients "
            f"WHERE id_number BETWEEN {backend.param} AND {backend.param}"
        )
        return PatientQuery(sql=query, fetch_one_only=True, range_width=int(range_width))
    raise ValueError(f"Unknown noise query mode: {mode}")
