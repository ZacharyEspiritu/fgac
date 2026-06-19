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

import time
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Tuple

from util.db_backend import DatabaseBackend


def _run_metric_query(conn, sql: str, params: Sequence = ()) -> Dict[str, object]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description or []]
            rows = [
                {column: value for column, value in zip(columns, row)}
                for row in cur.fetchall()
            ]
        return {"available": True, "rows": rows}
    except Exception as exc:  # pragma: no cover - depends on live PostgreSQL extensions
        try:
            conn.rollback()
        except Exception:
            pass
        return {"available": False, "error": str(exc), "rows": []}


def _create_postgres_extension(conn, extension: str) -> Dict[str, object]:
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        return {"available": True}
    except Exception as exc:  # pragma: no cover - depends on live PostgreSQL install/config
        try:
            conn.rollback()
        except Exception:
            pass
        return {"available": False, "error": str(exc)}


def snapshot_postgres_metrics(
    conn,
    backend: DatabaseBackend,
    label: str,
    policy: str,
) -> Dict[str, object]:
    snapshot: Dict[str, object] = {
        "label": label,
        "policy": policy,
        "captured_at_unix": time.time(),
        "backend": backend.name,
    }
    if backend.name != "postgres":
        snapshot["available"] = False
        snapshot["error"] = "PostgreSQL metrics are only supported for the postgres backend"
        return snapshot

    snapshot["available"] = True
    snapshot["extensions"] = {
        "pg_buffercache": _create_postgres_extension(conn, "pg_buffercache"),
        "pg_stat_statements": _create_postgres_extension(conn, "pg_stat_statements"),
    }
    snapshot["pg_stat_database"] = _run_metric_query(
        conn,
        """
        SELECT
            datname,
            blks_read,
            blks_hit,
            CASE
                WHEN blks_read + blks_hit = 0 THEN NULL
                ELSE blks_hit::float8 / (blks_read + blks_hit)
            END AS block_hit_ratio
        FROM pg_stat_database
        WHERE datname = current_database()
        """,
    )
    snapshot["pg_buffercache"] = _run_metric_query(
        conn,
        """
        WITH current_db AS (
            SELECT oid AS dbid, dattablespace
            FROM pg_database
            WHERE datname = current_database()
        ),
        rels AS (
            SELECT
                c.oid,
                c.relname,
                c.relkind,
                c.reltablespace,
                pg_relation_filenode(c.oid) AS relfilenode,
                pg_relation_size(c.oid) AS relation_bytes
            FROM pg_class c
            WHERE c.oid = 'patients'::regclass
               OR c.oid IN (
                    SELECT indexrelid
                    FROM pg_index
                    WHERE indrelid = 'patients'::regclass
               )
        )
        SELECT
            rels.relname,
            rels.relkind,
            rels.relation_bytes,
            CEIL(rels.relation_bytes / 8192.0)::bigint AS relation_pages,
            COUNT(b.bufferid)::bigint AS resident_pages,
            CASE
                WHEN rels.relation_bytes = 0 THEN NULL
                ELSE COUNT(b.bufferid)::float8 / CEIL(rels.relation_bytes / 8192.0)
            END AS resident_ratio
        FROM rels
        CROSS JOIN current_db
        LEFT JOIN pg_buffercache b
          ON b.relfilenode = rels.relfilenode
         AND b.reldatabase = current_db.dbid
         AND b.reltablespace = CASE
                WHEN rels.reltablespace = 0 THEN current_db.dattablespace
                ELSE rels.reltablespace
             END
        GROUP BY rels.relname, rels.relkind, rels.relation_bytes
        ORDER BY rels.relkind, rels.relname
        """,
    )
    snapshot["pg_stat_statements"] = _run_metric_query(
        conn,
        """
        SELECT
            r.rolname,
            s.queryid::text AS queryid,
            s.calls,
            s.total_exec_time,
            s.rows,
            s.shared_blks_hit,
            s.shared_blks_read,
            s.local_blks_hit,
            s.local_blks_read,
            s.temp_blks_read,
            s.query
        FROM pg_stat_statements s
        LEFT JOIN pg_roles r ON r.oid = s.userid
        WHERE s.dbid = (
            SELECT oid
            FROM pg_database
            WHERE datname = current_database()
        )
          AND s.query ILIKE '%FROM patients%'
          AND s.query ILIKE '%id_number%'
        ORDER BY r.rolname, s.queryid::text
        """,
    )
    return snapshot


def _first_metric_row(snapshot: Dict[str, object], key: str) -> Optional[Dict[str, object]]:
    metric = snapshot.get(key)
    if not isinstance(metric, dict) or not metric.get("available"):
        return None
    rows = metric.get("rows")
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def _numeric_value(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"Expected a numeric metric value, got {type(value).__name__}")
    return float(value)


def _numeric_delta(after: Dict[str, object], before: Dict[str, object], key: str) -> float:
    return _numeric_value(after.get(key)) - _numeric_value(before.get(key))


def _statement_key(row: Dict[str, object]) -> Tuple[str, str]:
    return (str(row.get("rolname") or ""), str(row.get("queryid") or ""))


def _statement_deltas(
    before: Dict[str, object],
    after: Dict[str, object],
) -> List[Dict[str, object]]:
    before_metric = before.get("pg_stat_statements")
    after_metric = after.get("pg_stat_statements")
    if not isinstance(before_metric, dict) or not isinstance(after_metric, dict):
        return []
    if not before_metric.get("available") or not after_metric.get("available"):
        return []
    before_rows = before_metric.get("rows") or []
    after_rows = after_metric.get("rows") or []
    before_by_key = {
        _statement_key(row): row
        for row in before_rows
        if isinstance(row, dict)
    }
    deltas = []
    for row in after_rows:
        if not isinstance(row, dict):
            continue
        previous = before_by_key.get(_statement_key(row), {})
        delta = {
            "rolname": row.get("rolname"),
            "queryid": row.get("queryid"),
            "query": row.get("query"),
        }
        for key in (
            "calls",
            "total_exec_time",
            "rows",
            "shared_blks_hit",
            "shared_blks_read",
            "local_blks_hit",
            "local_blks_read",
            "temp_blks_read",
        ):
            delta[f"{key}_delta"] = _numeric_delta(row, previous, key)
        deltas.append(delta)
    return deltas


def summarize_statement_deltas(
    deltas: Sequence[Dict[str, object]],
    attacker_user: str,
) -> Dict[str, Dict[str, float]]:
    summary = {
        "attacker": {"calls": 0.0, "shared_blks_hit": 0.0, "shared_blks_read": 0.0},
        "background": {"calls": 0.0, "shared_blks_hit": 0.0, "shared_blks_read": 0.0},
        "other": {"calls": 0.0, "shared_blks_hit": 0.0, "shared_blks_read": 0.0},
    }
    for delta in deltas:
        role = str(delta.get("rolname") or "")
        if role == attacker_user:
            group = "attacker"
        elif role.startswith("doctor_"):
            group = "background"
        else:
            group = "other"
        summary[group]["calls"] += _numeric_value(delta.get("calls_delta"))
        summary[group]["shared_blks_hit"] += _numeric_value(delta.get("shared_blks_hit_delta"))
        summary[group]["shared_blks_read"] += _numeric_value(delta.get("shared_blks_read_delta"))

    for values in summary.values():
        total_blocks = values["shared_blks_hit"] + values["shared_blks_read"]
        values["shared_block_hit_ratio"] = (
            values["shared_blks_hit"] / total_blocks if total_blocks else 0.0
        )
    return summary


def build_metrics_event(
    policy: str,
    before: Dict[str, object],
    after: Dict[str, object],
    attacker_user: str,
) -> Dict[str, object]:
    before_db = _first_metric_row(before, "pg_stat_database")
    after_db = _first_metric_row(after, "pg_stat_database")
    database_delta = None
    if before_db is not None and after_db is not None:
        blks_hit_delta = _numeric_delta(after_db, before_db, "blks_hit")
        blks_read_delta = _numeric_delta(after_db, before_db, "blks_read")
        total_blocks = blks_hit_delta + blks_read_delta
        database_delta = {
            "blks_hit_delta": blks_hit_delta,
            "blks_read_delta": blks_read_delta,
            "block_hit_ratio_delta_window": (
                blks_hit_delta / total_blocks if total_blocks else 0.0
            ),
        }

    statement_deltas = _statement_deltas(before, after)
    return {
        "policy": policy,
        "before": before,
        "after": after,
        "pg_stat_database_delta": database_delta,
        "pg_stat_statements_delta": statement_deltas,
        "pg_stat_statements_delta_summary": summarize_statement_deltas(
            statement_deltas,
            attacker_user,
        ),
    }
