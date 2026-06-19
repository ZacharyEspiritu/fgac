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

import csv
import io
import shlex
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse, urlunparse

from util.db_backend import connect
from util.sql_utils import validate_identifier

try:
    from psycopg import sql as psql  # type: ignore

    PSYCOPG_MAJOR = 3
except ImportError:  # pragma: no cover - fallback for psycopg2
    try:
        from psycopg2 import sql as psql  # type: ignore

        PSYCOPG_MAJOR = 2
    except ImportError:  # pragma: no cover
        psql = None  # type: ignore[assignment]
        PSYCOPG_MAJOR = 0


def execute_sql_file(conn, path: str) -> None:
    with open(path, "r", encoding="utf-8") as handle:
        sql_text = handle.read()
    with conn.cursor() as cur:
        cur.execute(sql_text)


def iter_batches(rows: Iterable[Sequence], batch_size: int) -> Iterable[List[Sequence]]:
    batch: List[Sequence] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def copy_rows(
    conn,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence],
    batch_size: int = 10000,
) -> None:
    col_list = ", ".join(columns)
    if PSYCOPG_MAJOR == 3:
        copy_sql = f"COPY {table} ({col_list}) FROM STDIN"
        with conn.cursor() as cur:
            with cur.copy(copy_sql) as copy:
                for row in rows:
                    copy.write_row(row)
        return

    copy_sql = f"COPY {table} ({col_list}) FROM STDIN WITH (FORMAT csv)"
    with conn.cursor() as cur:  # type: ignore[assignment]
        for batch in iter_batches(rows, batch_size):
            sio = io.StringIO()
            writer = csv.writer(sio)
            writer.writerows(batch)
            sio.seek(0)
            cur.copy_expert(copy_sql, sio)


def create_role(cur, name: str, password: str) -> None:
    if PSYCOPG_MAJOR == 0 or psql is None:
        raise RuntimeError("psycopg is required for role management")
    validate_identifier(name)
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if cur.fetchone():
        return
    cur.execute(
        psql.SQL("CREATE ROLE {} LOGIN PASSWORD {}")
        .format(psql.Identifier(name), psql.Literal(password))
    )


def _parse_kv_dsn(dsn: str) -> Dict[str, str]:
    parts: Dict[str, str] = {}
    for token in shlex.split(dsn):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        parts[key] = value
    return parts


def _is_url_dsn(dsn: str) -> bool:
    parsed = urlparse(dsn)
    return bool(parsed.scheme and parsed.netloc)


def extract_dbname(dsn: str) -> Optional[str]:
    if _is_url_dsn(dsn):
        parsed = urlparse(dsn)
        if parsed.path and parsed.path != "/":
            return parsed.path.lstrip("/")
        query = parse_qs(parsed.query)
        for key in ("dbname", "database"):
            if key in query and query[key]:
                return query[key][0]
        return None
    parts = _parse_kv_dsn(dsn)
    return parts.get("dbname") or parts.get("database")


def derive_admin_dsn(dsn: str, admin_db: str = "postgres") -> str:
    if _is_url_dsn(dsn):
        parsed = urlparse(dsn)
        new_path = f"/{admin_db}"
        return urlunparse(parsed._replace(path=new_path))
    parts = _parse_kv_dsn(dsn)
    parts["dbname"] = admin_db
    if "database" in parts:
        parts.pop("database")
    return " ".join(f"{key}={shlex.quote(value)}" for key, value in parts.items())


def ensure_database(admin_dsn: str, dbname: str) -> None:
    if not dbname:
        raise ValueError("Database name is required")
    if psql is None:
        raise RuntimeError("psycopg is required for database creation")
    conn = connect(admin_dsn, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        exists = cur.fetchone() is not None
        if not exists:
            cur.execute(psql.SQL("CREATE DATABASE {}").format(psql.Identifier(dbname)))
    conn.close()
