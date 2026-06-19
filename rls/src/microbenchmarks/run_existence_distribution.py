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
from typing import List, Sequence, Tuple

from renderers.renderer_util.timing_distribution import (
    print_latency_summary,
    render_latency_distribution_plot,
)
from util.args import (
    add_admin_dsn_arg,
    add_attacker_dsn_arg,
    add_attacker_user_arg,
    add_fast_arg,
    add_seed_arg,
    add_warm_cache_arg,
)
from util.db_utils import fetch_one
from util.db_backend import DatabaseBackend
from util.io import write_csv
from util.timing import timed_query
from patients.queries import build_patient_point_query, build_patient_range_query


DEFAULT_OUTPUT = {
    "equality": os.path.join("results", "existence_latency.csv"),
    "range": os.path.join("results", "existence_range_latency.csv"),
}
DEFAULT_PLOT_OUTPUT = {
    "equality": os.path.join("results", "existence_kde.png"),
    "range": os.path.join("results", "existence_range_kde.png"),
}

ProbeRow = Tuple[str, Tuple[int, ...], Tuple[int, ...]]


def parse_args(default_query: str = "equality") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m microbenchmarks.run_existence_distribution",
        description="Measure existence-privacy timing gaps for equality or range queries.",
    )
    parser.add_argument(
        "--query",
        choices=("equality", "range"),
        default=default_query,
        help="Query shape to measure.",
    )
    add_admin_dsn_arg(parser, help="Admin DSN for sampling keys.")
    add_attacker_dsn_arg(parser, help="Attacker DSN (RLS applies).")
    add_attacker_user_arg(parser, help="Attacker user name.")
    parser.add_argument("--samples", type=int, default=500, help="Samples per query type.")
    parser.add_argument(
        "--authorized-cardinality",
        type=int,
        default=1,
        help="Equality query only: row count per authorized key in the base table.",
    )
    parser.add_argument(
        "--unauthorized-cardinality",
        type=int,
        default=1,
        help="Equality query only: row count per unauthorized key in the base table.",
    )
    parser.add_argument("--range-width", type=int, default=10, help="Range query only: range width in ids.")
    parser.add_argument(
        "--max-tries",
        type=int,
        default=50000,
        help="Range query only: max attempts to find authorized/unauthorized ranges.",
    )
    parser.add_argument("--nonexistent-offset", type=int, default=1000, help="Offset above max id.")
    add_seed_arg(parser)
    parser.add_argument(
        "--output",
        default="",
        help="CSV output path. Defaults to the historical filename for the selected query.",
    )
    add_fast_arg(parser, help="Use SELECT 1 with LIMIT 1 for faster probing.")
    add_warm_cache_arg(parser, help="Run each query once before timing to warm index pages.")
    parser.add_argument(
        "--plot-output",
        default="",
        help="Histogram plot output path. Defaults to the historical filename for the selected query.",
    )
    parser.add_argument(
        "--plot-format",
        choices=("png", "pdf", "pgf"),
        default="png",
        help="Plot output format (png, pdf, pgf).",
    )
    parser.add_argument("--explain", action="store_true", help="Print EXPLAIN ANALYZE once.")
    parser.add_argument(
        "--rls-policy",
        choices=("join", "inline"),
        default="join",
        help="RLS policy variant to apply before running.",
    )
    args = parser.parse_args()
    if not args.output:
        args.output = DEFAULT_OUTPUT[args.query]
    if not args.plot_output:
        args.plot_output = DEFAULT_PLOT_OUTPUT[args.query]
    return args


def sample_ids_by_cardinality(
    cur,
    backend: DatabaseBackend,
    site_id: int,
    samples: int,
    authorized_cardinality: int,
    unauthorized_cardinality: int,
) -> Tuple[List[int], List[int]]:
    if authorized_cardinality < 0 or unauthorized_cardinality < 0:
        raise ValueError("Cardinality values must be >= 0")
    p = backend.param
    count_site = backend.count_filter(f"site_id = {p}")
    count_other = backend.count_filter(f"site_id <> {p}")
    base_auth = (
        f"SELECT id_number FROM patients GROUP BY id_number "
        f"HAVING {count_site} = {p} AND {count_other} = 0"
    )
    auth_query, auth_limit_params = backend.add_limit(base_auth, samples)
    cur.execute(auth_query, (site_id, authorized_cardinality, site_id, *auth_limit_params))
    authorized = [int(row[0]) for row in cur.fetchall()]

    base_unauth = (
        f"SELECT id_number FROM patients GROUP BY id_number "
        f"HAVING {count_site} = 0 AND {count_other} = {p}"
    )
    unauth_query, unauth_limit_params = backend.add_limit(base_unauth, samples)
    cur.execute(
        unauth_query,
        (site_id, site_id, unauthorized_cardinality, *unauth_limit_params),
    )
    unauthorized = [int(row[0]) for row in cur.fetchall()]

    if len(authorized) < samples:
        raise RuntimeError(
            "Not enough authorized keys for requested cardinality; "
            "try lowering --authorized-cardinality or --samples."
        )
    if len(unauthorized) < samples:
        raise RuntimeError(
            "Not enough unauthorized keys for requested cardinality; "
            "try lowering --unauthorized-cardinality or --samples."
        )
    return authorized, unauthorized


def choose_ranges(
    cur,
    backend: DatabaseBackend,
    site_id: int,
    min_id: int,
    max_id: int,
    width: int,
    samples: int,
    max_tries: int,
    rng: random.Random,
) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    if width < 1:
        raise ValueError("range-width must be >= 1")
    if max_id - min_id + 1 < width:
        raise ValueError("range-width larger than id range")

    authorized: List[Tuple[int, int]] = []
    unauthorized: List[Tuple[int, int]] = []
    tries = 0
    p = backend.param
    count_auth = backend.count_filter(f"site_id = {p}")
    count_unauth = backend.count_filter(f"site_id <> {p}")
    while (len(authorized) < samples or len(unauthorized) < samples) and tries < max_tries:
        start = rng.randint(min_id, max_id - width + 1)
        end = start + width - 1
        cur.execute(
            f"""
            SELECT
              {count_auth} AS authorized_count,
              {count_unauth} AS unauthorized_count
            FROM patients
            WHERE id_number BETWEEN {p} AND {p}
            """,
            (site_id, site_id, start, end),
        )
        auth_count, unauth_count = cur.fetchone()
        if auth_count > 0 and len(authorized) < samples:
            authorized.append((start, end))
        if auth_count == 0 and unauth_count > 0 and len(unauthorized) < samples:
            unauthorized.append((start, end))
        tries += 1

    if len(authorized) < samples or len(unauthorized) < samples:
        raise RuntimeError(
            "Unable to sample enough ranges; try reducing --range-width or --samples."
        )

    return authorized, unauthorized


def time_probe_rows(
    cur,
    query: str,
    probe_rows: Sequence[ProbeRow],
    *,
    warm_cache: bool,
    fetch_one_only: bool,
) -> List[Tuple]:
    rows = []
    for label, output_values, params in probe_rows:
        if warm_cache:
            cur.execute(query, params)
            if fetch_one_only:
                cur.fetchone()
            else:
                cur.fetchall()
        elapsed_ns, rowcount = timed_query(
            cur,
            query,
            params,
            fetch_one=fetch_one_only,
        )
        rows.append((label, *output_values, elapsed_ns, rowcount))
    return rows


def run_equality(
    args: argparse.Namespace,
    backend: DatabaseBackend,
    admin,
    attacker,
) -> List[Tuple[str, int, int, int]]:
    with admin.cursor() as cur:
        backend.apply_rls_policy(cur, args.rls_policy, ["patients"])
        site_id = fetch_one(
            cur,
            f"SELECT site_id FROM doctors WHERE user_name = {backend.param}",
            (args.attacker_user,),
        )
        max_id = fetch_one(cur, "SELECT max(id_number) FROM patients", ())
        authorized, unauthorized = sample_ids_by_cardinality(
            cur,
            backend,
            site_id,
            args.samples,
            args.authorized_cardinality,
            args.unauthorized_cardinality,
        )

    nonexistent = [max_id + args.nonexistent_offset + i for i in range(args.samples)]

    patient_query = build_patient_point_query(backend, args.fast)
    query = patient_query.sql
    fetch_one_only = patient_query.fetch_one_only

    if args.explain:
        with attacker.cursor() as cur:
            output = backend.explain(cur, query, patient_query.params_for_key(authorized[0]))
            if output is None:
                print("EXPLAIN not supported for this backend.")
            else:
                print("\n".join(output))

    probe_rows: List[ProbeRow] = []
    for label, keys in (
        ("nonexistent", nonexistent),
        ("authorized", authorized),
        ("unauthorized", unauthorized),
    ):
        for key in keys:
            probe_rows.append((label, (key,), patient_query.params_for_key(key)))

    with attacker.cursor() as cur:
        return time_probe_rows(
            cur,
            query,
            probe_rows,
            warm_cache=args.warm_cache,
            fetch_one_only=fetch_one_only,
        )


def run_range(
    args: argparse.Namespace,
    backend: DatabaseBackend,
    admin,
    attacker,
    rng: random.Random,
) -> List[Tuple[str, int, int, int, int]]:
    with admin.cursor() as cur:
        backend.apply_rls_policy(cur, args.rls_policy, ["patients"])
        site_id = fetch_one(
            cur,
            f"SELECT site_id FROM doctors WHERE user_name = {backend.param}",
            (args.attacker_user,),
        )
        min_id = fetch_one(cur, "SELECT min(id_number) FROM patients", ())
        max_id = fetch_one(cur, "SELECT max(id_number) FROM patients", ())
        authorized, unauthorized = choose_ranges(
            cur,
            backend,
            site_id,
            min_id,
            max_id,
            args.range_width,
            args.samples,
            args.max_tries,
            rng,
        )

    nonexistent = []
    for i in range(args.samples):
        start = max_id + args.nonexistent_offset + (i * args.range_width)
        end = start + args.range_width - 1
        nonexistent.append((start, end))

    patient_query = build_patient_range_query(backend, args.fast)
    query = patient_query.sql
    fetch_one_only = patient_query.fetch_one_only

    if args.explain:
        with attacker.cursor() as cur:
            output = backend.explain(
                cur,
                query,
                patient_query.params_for_range(*authorized[0]),
            )
            if output is None:
                print("EXPLAIN not supported for this backend.")
            else:
                print("\n".join(output))

    probe_rows: List[ProbeRow] = []
    for label, ranges in (
        ("nonexistent", nonexistent),
        ("authorized", authorized),
        ("unauthorized", unauthorized),
    ):
        for start, end in ranges:
            probe_rows.append(
                (label, (start, end), patient_query.params_for_range(start, end))
            )

    with attacker.cursor() as cur:
        return time_probe_rows(
            cur,
            query,
            probe_rows,
            warm_cache=args.warm_cache,
            fetch_one_only=fetch_one_only,
        )


def main(default_query: str = "equality") -> None:
    args = parse_args(default_query)
    rng = random.Random(args.seed)
    backend = DatabaseBackend.from_dsn(args.admin_dsn)
    admin = backend.connect(args.admin_dsn)
    attacker = backend.connect(args.attacker_dsn)

    try:
        rows: Sequence[Tuple]
        if args.query == "equality":
            equality_rows = run_equality(args, backend, admin, attacker)
            write_csv(
                args.output,
                equality_rows,
                header=("query_type", "key", "elapsed_ns", "rowcount"),
            )
            rows = equality_rows
        else:
            range_rows = run_range(args, backend, admin, attacker, rng)
            write_csv(
                args.output,
                range_rows,
                header=("query_type", "start", "end", "elapsed_ns", "rowcount"),
            )
            rows = range_rows

        elapsed_index = 2 if args.query == "equality" else 3
        print_latency_summary(rows, elapsed_index)
        plot_output = render_latency_distribution_plot(
            rows,
            elapsed_index=elapsed_index,
            plot_output=args.plot_output,
            plot_format=args.plot_format,
            pgf_figure_size=(3.4, 1.61) if args.query == "equality" else (3.4, 2.3),
        )
        print(f"Saved histogram plot to {plot_output}")
    finally:
        admin.close()
        attacker.close()


if __name__ == "__main__":
    main()
