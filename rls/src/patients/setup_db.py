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

import argparse
import os
import random
import re
from typing import Iterable, Tuple

from util.db_admin import (
    copy_rows,
    create_role,
    ensure_database,
    execute_sql_file,
    extract_dbname,
    derive_admin_dsn,
    psql,
)
from util.io import write_csv
from util.progress import ProgressBar
from util.sql_utils import (
    validate_identifier,
)
from util.db_backend import DatabaseBackend, connect


CREDS_OUT = os.path.join("data", "doctors.csv")
COPY_BATCH_SIZE = 10000
FAKER_LOCALE = "en_US"
ID_START = 1_000_000_000
PATIENTS_DIR = os.path.dirname(os.path.abspath(__file__))
SQL_DIR = os.path.join(PATIENTS_DIR, "sql")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m patients.setup_db",
        description="Set up patients/doctors schema and load data.",
    )
    parser.add_argument(
        "--dsn",
        required=True,
        help="PostgreSQL DSN with privileges to create roles.",
    )
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="Create the target database before loading data; dbname is inferred from --dsn.",
    )
    parser.add_argument("--patients", type=int, default=2_000_000, help="Number of patient rows.")
    parser.add_argument("--doctors", type=int, default=10_000, help="Number of doctor rows.")
    parser.add_argument("--sites", type=int, default=5, help="Number of sites.")
    parser.add_argument("--seed", type=int, default=1, help="Random seed.")
    parser.add_argument("--reset", action="store_true", help="Drop existing tables/functions first.")
    parser.add_argument(
        "--rls-policy",
        choices=("join", "inline"),
        default="join",
        help="RLS policy variant to install.",
    )
    parser.add_argument("--analyze", action="store_true", help="Run ANALYZE after loading.")
    return parser.parse_args()


NAME_RE = re.compile(r"[^A-Za-z ]+")


def sanitize_name(raw: str) -> str:
    cleaned = NAME_RE.sub("", raw)
    cleaned = " ".join(cleaned.split())
    return cleaned


def reset_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DROP POLICY IF EXISTS doctor_read ON patients;")
        cur.execute("DROP TABLE IF EXISTS patients CASCADE;")
        cur.execute("DROP TABLE IF EXISTS doctors CASCADE;")
        cur.execute("DROP TABLE IF EXISTS existence_success_experiment CASCADE;")
        cur.execute("DROP TABLE IF EXISTS range_experiment CASCADE;")
        cur.execute("DROP TABLE IF EXISTS prefix_experiment CASCADE;")
        cur.execute("DROP TABLE IF EXISTS binary_search_experiment CASCADE;")
        cur.execute("DROP FUNCTION IF EXISTS site_policy_join(BIGINT, TEXT) CASCADE;")
        cur.execute("DROP FUNCTION IF EXISTS site_policy_inline(BIGINT, TEXT) CASCADE;")


def doctor_rows(doctors: int, sites: int) -> Iterable[Tuple[str, int]]:
    for idx in range(doctors):
        site_id = (idx % sites) + 1
        user_name = f"doctor_s{site_id}_{idx:05d}"
        yield user_name, site_id


def iter_with_progress(
    rows: Iterable[Tuple],
    total: int,
    label: str,
    preview_func=None,
) -> Iterable[Tuple]:
    progress = ProgressBar(total, label)
    for idx, row in enumerate(rows, start=1):
        yield row
        preview = ""
        if preview_func:
            preview = str(preview_func(row))
        progress.update(idx, preview=preview)


def make_zip_code(rng: random.Random) -> str:
    return f"{rng.randint(0, 99999):05d}"


def make_ssn(rng: random.Random) -> str:
    return f"{rng.randint(0, 999):03d}-{rng.randint(0, 99):02d}-{rng.randint(0, 9999):04d}"


def patient_rows(
    patients: int,
    sites: int,
    rng: random.Random,
    faker,
) -> Iterable[Tuple[int, str, int, int, str, str]]:
    def make_name() -> str:
        for _ in range(10):
            candidate = sanitize_name(faker.name())
            if candidate:
                return candidate
        return "Patient"

    for row_idx in range(patients):
        id_number = ID_START + row_idx
        site_id = (row_idx % sites) + 1
        name = make_name()
        age = rng.randint(1, 120)
        zip_code = make_zip_code(rng)
        ssn = make_ssn(rng)
        yield id_number, name, age, site_id, zip_code, ssn


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    backend = DatabaseBackend.from_dsn(args.dsn)
    if backend.name != "postgres":
        raise RuntimeError("patients.setup_db only supports PostgreSQL.")

    if args.create_db:
        db_name = extract_dbname(args.dsn)
        if not db_name:
            raise RuntimeError("Unable to infer dbname from --dsn")
        admin_dsn = derive_admin_dsn(args.dsn, admin_db="postgres")
        ensure_database(admin_dsn, db_name)

    conn = connect(args.dsn, autocommit=True)

    if args.reset:
        reset_schema(conn)

    execute_sql_file(conn, os.path.join(SQL_DIR, "patients_schema.sql"))
    execute_sql_file(conn, os.path.join(SQL_DIR, "patients_rls.sql"))

    with conn.cursor() as cur:
        backend.apply_rls_policy(cur, args.rls_policy, ["patients"])
        cur.execute("GRANT SELECT ON patients TO PUBLIC;")
        cur.execute("GRANT SELECT ON doctors TO PUBLIC;")

    doctors = []
    doctor_progress = ProgressBar(args.doctors, "doctors")
    for idx, row in enumerate(doctor_rows(args.doctors, args.sites), start=1):
        doctors.append(row)
        doctor_progress.update(idx, preview=row[0])
    copy_rows(conn, "doctors", ("user_name", "site_id"), doctors, COPY_BATCH_SIZE)

    with conn.cursor() as cur:
        for user_name, site_id in doctors:
            create_role(cur, user_name, user_name)
            validate_identifier(user_name)
            if psql is None:
                raise RuntimeError("psycopg is required for inline role configuration")
            cur.execute(
                psql.SQL("ALTER ROLE {} SET app.site_id = {}").format(
                    psql.Identifier(user_name),
                    psql.Literal(str(site_id)),
                )
            )

    write_csv(
        CREDS_OUT,
        [(user_name, user_name, site_id) for user_name, site_id in doctors],
        header=("user_name", "password", "site_id"),
    )

    try:
        from faker import Faker  # type: ignore
    except ImportError as exc:
        raise RuntimeError("faker is required. Install with: pip install faker") from exc
    faker = Faker(FAKER_LOCALE)
    faker.seed_instance(args.seed)

    patients_iter = patient_rows(
        args.patients,
        args.sites,
        rng,
        faker,
    )
    patients_iter = iter_with_progress(
        patients_iter,
        args.patients,
        "patients",
        preview_func=lambda row: row[0],
    )
    copy_rows(
        conn,
        "patients",
        ("id_number", "name", "age", "site_id", "zip_code", "ssn"),
        patients_iter,
        COPY_BATCH_SIZE,
    )

    if args.analyze:
        with conn.cursor() as cur:
            cur.execute("ANALYZE patients;")

    conn.close()


if __name__ == "__main__":
    main()
