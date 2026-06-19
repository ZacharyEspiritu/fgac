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

"""
microbenchmarks.measure_db_sizes — report exact physical sizes for the rls database.

Run after loading the dataset:

    uv run python -m microbenchmarks.measure_db_sizes \
        --dsn "postgresql://postgres:<pw>@localhost/rls"

Output is printed to stdout in human-readable form and optionally written to
--output as JSON for use in the artifact appendix.
"""

import argparse
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from util.db_backend import DatabaseBackend
from util.db_utils import fetch_all, fetch_optional_value
from util.io import write_json
from util.sql_utils import validate_identifier


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python3 -m microbenchmarks.measure_db_sizes",
        description=__doc__,
    )
    p.add_argument(
        "--dsn",
        required=True,
        help="Admin PostgreSQL DSN (e.g. postgresql://postgres:pw@host/rls)",
    )
    p.add_argument("--patients-table", default="patients")
    p.add_argument("--doctors-table", default="doctors")
    p.add_argument("--output", default="", help="Optional path to write JSON output.")
    return p.parse_args()


@dataclass(frozen=True)
class IndexMetric:
    name: str
    size_bytes: int
    size_pretty: str
    is_primary: bool
    columns: str


def require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"Expected integer for {label}, got {value!r}")
    return value


def require_str(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"Expected string for {label}, got {value!r}")
    return value


def require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"Expected boolean for {label}, got {value!r}")
    return value


def fetch_int(cur, sql: str, params: Sequence[object] = ()) -> int:
    value = fetch_optional_value(cur, sql, params)
    return require_int(value, sql)


def fetch_optional_int(cur, sql: str, params: Sequence[object] = ()) -> Optional[int]:
    value = fetch_optional_value(cur, sql, params)
    if value is None:
        return None
    return require_int(value, sql)


def main() -> None:
    args = parse_args()
    for name in (args.patients_table, args.doctors_table):
        validate_identifier(name)

    backend = DatabaseBackend.from_dsn(args.dsn)
    conn = backend.connect(args.dsn)

    with conn.cursor() as cur:
        # ── PostgreSQL server settings ──────────────────────────────────────
        postgres_version = fetch_optional_value(cur, "SELECT version()")
        shared_buffers_bytes = fetch_int(
            cur,
            "SELECT setting::bigint * current_setting('block_size')::bigint "
            "FROM pg_settings WHERE name = 'shared_buffers'",
        )
        block_size_bytes = fetch_int(cur, "SELECT current_setting('block_size')::int")
        max_connections = fetch_int(
            cur, "SELECT setting::int FROM pg_settings WHERE name = 'max_connections'"
        )
        ssl_on = fetch_optional_value(cur, "SELECT setting FROM pg_settings WHERE name = 'ssl'")

        # ── Row counts ──────────────────────────────────────────────────────
        patients_row_count = fetch_int(cur, f"SELECT count(*) FROM {args.patients_table}")
        doctors_row_count = fetch_int(cur, f"SELECT count(*) FROM {args.doctors_table}")
        sites_count = fetch_int(
            cur, f"SELECT count(DISTINCT site_id) FROM {args.patients_table}"
        )

        # ── patients physical sizes ─────────────────────────────────────────
        patients_heap_bytes = fetch_int(
            cur, f"SELECT pg_relation_size('{args.patients_table}'::regclass, 'main')"
        )
        patients_vm_bytes = fetch_int(
            cur, f"SELECT pg_relation_size('{args.patients_table}'::regclass, 'vm')"
        )
        patients_fsm_bytes = fetch_int(
            cur, f"SELECT pg_relation_size('{args.patients_table}'::regclass, 'fsm')"
        )
        patients_toast_bytes = fetch_int(
            cur, f"SELECT COALESCE(pg_total_relation_size(c.reltoastrelid), 0) "
                 f"FROM pg_class c WHERE c.relname = '{args.patients_table}'"
        )
        patients_indexes_total_bytes = fetch_int(
            cur, f"SELECT pg_indexes_size('{args.patients_table}'::regclass)"
        )
        patients_total_bytes = fetch_int(
            cur, f"SELECT pg_total_relation_size('{args.patients_table}'::regclass)"
        )

        # ── per-index sizes on patients ─────────────────────────────────────
        index_rows = fetch_all(
            cur,
            """
            SELECT i.relname, pg_relation_size(i.oid) AS index_bytes,
                   pg_size_pretty(pg_relation_size(i.oid)) AS size_pretty,
                   ix.indisprimary AS is_primary,
                   array_to_string(
                       array(SELECT a.attname
                             FROM pg_attribute a
                             WHERE a.attrelid = t.oid
                               AND a.attnum = ANY(ix.indkey)
                             ORDER BY array_position(ix.indkey, a.attnum)),
                       ', '
                   ) AS columns
            FROM pg_class t
            JOIN pg_index ix ON ix.indrelid = t.oid
            JOIN pg_class i  ON i.oid = ix.indexrelid
            WHERE t.relname = %s
            ORDER BY pg_relation_size(i.oid) DESC
            """,
            (args.patients_table,),
        )
        patients_indexes: List[IndexMetric] = [
            IndexMetric(
                name=require_str(row[0], "index name"),
                size_bytes=require_int(row[1], "index bytes"),
                size_pretty=require_str(row[2], "index size_pretty"),
                is_primary=require_bool(row[3], "index is_primary"),
                columns=require_str(row[4], "index columns"),
            )
            for row in index_rows
        ]

        # ── average row width ───────────────────────────────────────────────
        patients_avg_row_width_bytes = fetch_optional_int(
            cur,
            "SELECT avg_width FROM pg_stats "
            "WHERE tablename = %s AND attname = 'ssn'",
            (args.patients_table,),
        )
        # Aggregate avg_width across all columns as a proxy for full tuple width
        full_width = fetch_all(
            cur,
            "SELECT sum(avg_width) FROM pg_stats WHERE tablename = %s",
            (args.patients_table,),
        )
        patients_avg_tuple_width_bytes = (
            require_int(full_width[0][0], "patients avg tuple width")
            if full_width[0][0]
            else None
        )

        # ── doctors physical sizes ──────────────────────────────────────────
        doctors_heap_bytes = fetch_int(
            cur, f"SELECT pg_relation_size('{args.doctors_table}'::regclass, 'main')"
        )
        doctors_indexes_total_bytes = fetch_int(
            cur, f"SELECT pg_indexes_size('{args.doctors_table}'::regclass)"
        )
        doctors_total_bytes = fetch_int(
            cur, f"SELECT pg_total_relation_size('{args.doctors_table}'::regclass)"
        )

        # ── whole database ──────────────────────────────────────────────────
        database_name = fetch_optional_value(cur, "SELECT current_database()")
        database_total_bytes = fetch_int(
            cur, "SELECT pg_database_size(current_database())"
        )

    conn.close()

    # ── pretty-print ────────────────────────────────────────────────────────
    def mb(b: Optional[int]) -> str:
        return f"{b / 1024 / 1024:.1f} MB" if b is not None else "n/a"

    metrics: Dict[str, object] = {
        "postgres_version": postgres_version,
        "shared_buffers_bytes": shared_buffers_bytes,
        "block_size_bytes": block_size_bytes,
        "max_connections": max_connections,
        "ssl_on": ssl_on,
        "patients_row_count": patients_row_count,
        "doctors_row_count": doctors_row_count,
        "sites_count": sites_count,
        "patients_heap_bytes": patients_heap_bytes,
        "patients_vm_bytes": patients_vm_bytes,
        "patients_fsm_bytes": patients_fsm_bytes,
        "patients_toast_bytes": patients_toast_bytes,
        "patients_indexes_total_bytes": patients_indexes_total_bytes,
        "patients_total_bytes": patients_total_bytes,
        "patients_indexes": [
            {
                "name": index.name,
                "bytes": index.size_bytes,
                "size_pretty": index.size_pretty,
                "is_primary": index.is_primary,
                "columns": index.columns,
            }
            for index in patients_indexes
        ],
        "patients_avg_row_width_bytes": patients_avg_row_width_bytes,
        "patients_avg_tuple_width_bytes": patients_avg_tuple_width_bytes,
        "doctors_heap_bytes": doctors_heap_bytes,
        "doctors_indexes_total_bytes": doctors_indexes_total_bytes,
        "doctors_total_bytes": doctors_total_bytes,
        "database_name": database_name,
        "database_total_bytes": database_total_bytes,
    }

    print(f"PostgreSQL:           {postgres_version}")
    print(f"Block size:           {block_size_bytes} bytes")
    print(f"shared_buffers:       {mb(shared_buffers_bytes)} ({shared_buffers_bytes} bytes)")
    print(f"max_connections:      {max_connections}")
    print(f"ssl:                  {ssl_on}")
    print()
    print("Dataset")
    print(f"  patients rows:      {patients_row_count:,}")
    print(f"  doctors rows:       {doctors_row_count:,}")
    print(f"  distinct sites:     {sites_count}")
    print()
    print("patients physical sizes")
    print(f"  heap (main fork):   {mb(patients_heap_bytes)} ({patients_heap_bytes:,} bytes)")
    print(f"  TOAST:              {mb(patients_toast_bytes)}")
    print(f"  all indexes:        {mb(patients_indexes_total_bytes)}")
    print(f"  TOTAL (incl. idx):  {mb(patients_total_bytes)}")
    print(f"  avg tuple width:    {patients_avg_tuple_width_bytes} bytes (sum of pg_stats.avg_width)")
    print()
    print("  per-index breakdown:")
    for idx in patients_indexes:
        pk = " [PK]" if idx.is_primary else ""
        print(f"    {idx.name:<38}  {idx.size_pretty:>10}   cols: ({idx.columns}){pk}")
    print()
    print("doctors physical sizes")
    print(f"  heap:               {mb(doctors_heap_bytes)}")
    print(f"  all indexes:        {mb(doctors_indexes_total_bytes)}")
    print(f"  TOTAL:              {mb(doctors_total_bytes)}")
    print()
    print(f"Database '{database_name}' total: {mb(database_total_bytes)}")
    print()
    fits_shared_buffers = patients_total_bytes <= shared_buffers_bytes
    fits_text = (
        "YES"
        if fits_shared_buffers
        else f"NO — OS page cache covers the rest (total DB = {mb(database_total_bytes)} << 64 GB RAM)"
    )
    print(f"Fits in shared_buffers ({mb(shared_buffers_bytes)})?  {fits_text}")

    if args.output:
        write_json(args.output, metrics, sort_keys=True)
        print(f"\nJSON metrics written to {args.output}")


if __name__ == "__main__":
    main()
