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
from types import TracebackType
from typing import Any, Iterable, Optional, Protocol, Sequence, Tuple, runtime_checkable

from util.sql_utils import validate_identifier


class Cursor(Protocol):
    description: Optional[Sequence[Sequence[Any]]]

    def __enter__(self) -> "Cursor":
        ...

    def __exit__(
        self,
        _exc_type: Optional[type[BaseException]],
        _exc: Optional[BaseException],
        _traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        ...

    def execute(self, query: object, params: object = ()) -> Any:
        ...

    def fetchone(self) -> Optional[Sequence[Any]]:
        ...

    def fetchall(self) -> Sequence[Sequence[Any]]:
        ...


@runtime_checkable
class Connection(Protocol):
    autocommit: bool

    def cursor(self, *_args: Any, **_kwargs: Any) -> Cursor:
        ...

    def close(self) -> None:
        ...


class DatabaseBackend:
    name: str = ""
    param: str = "%s"

    @classmethod
    def from_dsn(cls, dsn: str) -> "DatabaseBackend":
        return PostgresBackend()

    def connect(self, dsn: str, autocommit: bool = True) -> Connection:
        raise NotImplementedError

    def add_limit(self, sql: str, limit: Optional[int]) -> Tuple[str, Tuple]:
        raise NotImplementedError

    def count_filter(self, condition_sql: str) -> str:
        raise NotImplementedError

    def explain(
        self, cur: Cursor, query: str, params: Sequence[Any]
    ) -> Optional[Iterable[str]]:
        raise NotImplementedError

    def apply_rls_policy(
        self, cur: Cursor, policy: str, tables: Sequence[str]
    ) -> None:
        raise NotImplementedError


def connect(dsn: str, autocommit: bool = True) -> Connection:
    return DatabaseBackend.from_dsn(dsn).connect(dsn, autocommit=autocommit)


@dataclass(frozen=True)
class PostgresBackend(DatabaseBackend):
    name: str = "postgres"
    param: str = "%s"

    def connect(self, dsn: str, autocommit: bool = True) -> Connection:
        try:
            import psycopg  # type: ignore

            conn = psycopg.connect(dsn)
        except ImportError:
            try:
                import psycopg2  # type: ignore

                conn = psycopg2.connect(dsn)  # type: ignore[name-defined]
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("psycopg (v3) or psycopg2 is required") from exc
        conn.autocommit = autocommit
        if not isinstance(conn, Connection):
            raise TypeError(f"Unsupported database connection type: {type(conn).__name__}")
        return conn

    def add_limit(self, sql: str, limit: Optional[int]) -> Tuple[str, Tuple]:
        if limit is None:
            return sql, ()
        return f"{sql} LIMIT {self.param}", (limit,)

    def count_filter(self, condition_sql: str) -> str:
        return f"COUNT(*) FILTER (WHERE {condition_sql})"

    def explain(
        self, cur: Cursor, query: str, params: Sequence[Any]
    ) -> Optional[Iterable[str]]:
        cur.execute(f"EXPLAIN ANALYZE {query}", params)
        return [row[0] for row in cur.fetchall()]

    def apply_rls_policy(
        self, cur: Cursor, policy: str, tables: Sequence[str]
    ) -> None:
        policy = policy.lower()
        func_map = {
            "join": "site_policy_join",
            "inline": "site_policy_inline",
        }
        if policy not in func_map:
            raise ValueError(f"Unknown RLS policy: {policy}")
        func_name = func_map[policy]
        for table in tables:
            validate_identifier(table)
            cur.execute(f"SELECT to_regclass({self.param})", (table,))
            row = cur.fetchone()
            if row is None or row[0] is None:
                continue
            cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            cur.execute(f"DROP POLICY IF EXISTS doctor_read ON {table}")
            cur.execute(
                f"CREATE POLICY doctor_read ON {table} "
                f"FOR SELECT USING ({func_name}(site_id, current_user))"
            )
