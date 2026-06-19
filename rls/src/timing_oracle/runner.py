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
import csv
import math
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from util.args import (
    add_db_connection_args,
    add_fast_arg,
    add_probes_arg,
    add_seed_arg,
    add_warm_cache_arg,
    parse_csv_ints,
    parse_csv_strings,
    parse_optional_csv_ints,
    require_positive,
)
from util.db_backend import DatabaseBackend
from util.io import ensure_parent_dir, write_csv, write_json
from patients.sampling import load_patient_sampling_context, load_site_key_pool
from patients.queries import build_patient_point_query
from timing_oracle.core import (
    CalibrationEntry,
    OracleProbeStats,
    OracleProbeTrial,
    add_calibration_stats,
    calibration_entry,
    empty_calibration_stats,
    run_alternating_probe_trials,
    sample_calibration,
    summarize_calibration_stats,
)
from util.postgres_metrics import build_metrics_event, snapshot_postgres_metrics


@dataclass(frozen=True)
class AccuracySummaryRow:
    policy: str
    k: int
    tp_rate_pct: float
    tn_rate_pct: float
    accuracy_pct: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m timing_oracle",
        description=(
            "Generate the Table 1 oracle-calibration summary on the real patients table, "
            "optionally while controlled background client load is running."
        )
    )
    add_db_connection_args(
        parser,
        admin_help="Admin DSN for sampling keys and policy changes.",
        attacker_help="Attacker DSN used for the oracle measurements.",
        attacker_user_help="Attacker user name.",
    )
    parser.add_argument(
        "--policies",
        default="join,inline",
        help="Comma-separated RLS policies to compare (default: join,inline).",
    )
    parser.add_argument(
        "--k-values",
        default="1,2,3,4,5,6,7,8,9,10",
        help="Comma-separated probe counts k (minimum of k timings per oracle query).",
    )
    add_probes_arg(parser, default=10000, help="Total probe attempts per policy and k.")
    add_seed_arg(parser)
    parser.add_argument(
        "--nonexistent-offset",
        type=int,
        default=1000,
        help="Offset above max(id_number) for nonexistent probe keys.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join("results", "table1_summary.csv"),
        help="Summary CSV output path.",
    )
    parser.add_argument(
        "--table-output",
        default=os.path.join("results", "table1_summary.md"),
        help="Markdown table output path.",
    )
    parser.add_argument(
        "--noise-output",
        default=os.path.join("results", "table1_noise.json"),
        help="Background-load summary JSON output path.",
    )
    parser.add_argument(
        "--metrics-output",
        default=os.path.join("results", "table1_metrics.json"),
        help="PostgreSQL cache/statistics snapshots captured around each measured probe batch.",
    )
    add_fast_arg(parser, help="Use SELECT 1 with LIMIT/TOP 1 for both the attack and background load.")
    add_warm_cache_arg(parser, help="Run each attack query once before timing.")
    parser.add_argument(
        "--noise-clients",
        type=int,
        default=0,
        help="Number of external background clients used for this scenario.",
    )
    parser.add_argument(
        "--noise-total-qps",
        type=float,
        default=0.0,
        help="Target aggregate QPS for the external background load.",
    )
    parser.add_argument(
        "--noise-pool-size",
        type=int,
        default=256,
        help="Minimum number of authorized/unauthorized keys to cache for attack probes.",
    )
    parser.add_argument(
        "--thresholds-from",
        default="",
        help=(
            "Optional Table 1 summary CSV to reuse threshold/auth/nonexistent calibration values "
            "instead of recalibrating in this run."
        ),
    )
    parser.add_argument(
        "--calibration-mode",
        choices=("trial", "scenario"),
        default="trial",
        help=(
            "Use fresh calibration probes for every trial, or one fixed calibration "
            "per policy/scenario (default: trial)."
        ),
    )
    parser.add_argument(
        "--calibration-cadences",
        default="",
        help=(
            "Comma-separated list of calibration cadences to evaluate in parallel "
            "in a single probe stream. When set, the script ignores --calibration-mode "
            "and emits one summary row per (policy, k, cadence). Cadence C means "
            "'recalibrate every C probes' (C=1 ≡ trial mode; C=<probes> ≡ scenario "
            "mode). Cadences with the same probe index share the same calibration "
            "measurements, so total cost is bounded by the smallest cadence."
        ),
    )
    return parser.parse_args()


def validate_noise_metadata_args(args: argparse.Namespace) -> None:
    if args.noise_clients < 0:
        raise ValueError("--noise-clients must be >= 0")
    if args.noise_total_qps < 0 or not math.isfinite(args.noise_total_qps):
        raise ValueError("--noise-total-qps must be finite and >= 0")
    if args.noise_pool_size <= 0:
        raise ValueError("--noise-pool-size must be positive")


def print_accuracy_panel(
    policy: str,
    cadence: Optional[int],
    rows: Sequence[Tuple[str, int, float]],
) -> None:
    if not rows:
        return

    table = Table(
        box=box.SIMPLE,
        border_style="bright_black",
        header_style="bold white",
        padding=(0, 0),
    )
    for _, k, _ in rows:
        table.add_column(f"k={k}", justify="right", no_wrap=True)
    table.add_row(*(f"{accuracy_pct:.2f}%" for _, _, accuracy_pct in rows))

    cadence_tag = "" if cadence is None else f" cadence={cadence}"
    Console(highlight=False, width=120).print(
        Panel.fit(
            table,
            title=f"RLS oracle accuracy - policy={policy}{cadence_tag}",
            title_align="left",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def load_threshold_rows(path: str) -> Dict[Tuple[str, int], Tuple[int, int, int]]:
    thresholds: Dict[Tuple[str, int], Tuple[int, int, int]] = {}
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            policy = str(row["policy"]).strip()
            k = int(row["k"])
            thresholds[(policy, k)] = (
                int(row["threshold_ns"]),
                int(row["authorized_min_ns"]),
                int(row["nonexistent_min_ns"]),
            )
    if not thresholds:
        raise RuntimeError(f"No threshold rows loaded from {path}")
    return thresholds


def write_markdown_table(
    path: str,
    rows: Sequence[AccuracySummaryRow],
    policies: Sequence[str],
    probes: int,
    noise_clients: int,
    noise_total_qps: float,
    calibration_mode: str,
) -> None:
    ensure_parent_dir(path)
    row_map = {(row.policy, row.k): row for row in rows}
    ks = sorted({row.k for row in rows})

    header = ["k"]
    for policy in policies:
        header.extend([f"{policy} TP", f"{policy} TN", f"{policy} Acc"])

    lines = [
        "# Table 1 Summary",
        "",
        f"- probes per row: {probes}",
        f"- calibration mode: {calibration_mode}",
        f"- noise clients: {noise_clients}",
        f"- noise target total qps: {noise_total_qps:.2f}",
        "",
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]

    for k in ks:
        cells = [str(k)]
        for policy in policies:
            row = row_map.get((policy, k))
            if row is None:
                cells.extend(["", "", ""])
                continue
            cells.extend(
                [
                    f"{row.tp_rate_pct:.2f}",
                    f"{row.tn_rate_pct:.2f}",
                    f"{row.accuracy_pct:.2f}",
                ]
            )
        lines.append("| " + " | ".join(cells) + " |")

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> None:
    args = parse_args()
    policies = parse_csv_strings(args.policies, "policies")
    k_values = parse_csv_ints(args.k_values, "k-values")
    require_positive(args.probes, "--probes")
    if args.noise_clients < 0:
        raise ValueError("--noise-clients must be >= 0")
    if args.thresholds_from and args.calibration_mode == "trial":
        raise ValueError("--thresholds-from requires --calibration-mode scenario")
    cadences = sorted(set(parse_optional_csv_ints(args.calibration_cadences, "calibration-cadences")))
    if cadences:
        if any(c > args.probes for c in cadences):
            # A cadence larger than the probe count means "calibrate once at idx=0"; that's fine.
            pass
        if args.thresholds_from:
            raise ValueError("--thresholds-from is incompatible with --calibration-cadences")

    validate_noise_metadata_args(args)

    rng = random.Random(args.seed)
    backend = DatabaseBackend.from_dsn(args.admin_dsn)
    thresholds_by_key: Dict[Tuple[str, int], Tuple[int, int, int]] = {}
    if args.thresholds_from:
        thresholds_by_key = load_threshold_rows(args.thresholds_from)
        missing = [
            f"{policy}/k={k}"
            for policy in policies
            for k in k_values
            if (policy, k) not in thresholds_by_key
        ]
        if missing:
            missing_preview = ", ".join(missing[:8])
            if len(missing) > 8:
                missing_preview += ", ..."
            raise RuntimeError(
                f"Threshold file {args.thresholds_from} is missing rows for: {missing_preview}"
            )

    admin = backend.connect(args.admin_dsn)
    with admin.cursor() as cur:
        patient_context = load_patient_sampling_context(cur, backend, args.attacker_user)
        sample_count = max(args.noise_pool_size, min(args.probes, 1024))
        key_pool = load_site_key_pool(
            cur, backend, patient_context.attacker_site, sample_count
        )
        authorized_keys = key_pool.authorized_keys
        unauthorized_keys = key_pool.unauthorized_keys
        max_id = patient_context.max_id

    patient_query = build_patient_point_query(backend, args.fast)
    query = patient_query.sql
    limit_params = patient_query.limit_params
    fetch_one_only = patient_query.fetch_one_only

    summary_rows: List[Tuple] = []
    markdown_rows: List[AccuracySummaryRow] = []
    noise_rows: List[Dict[str, object]] = []
    metrics_events: List[Dict[str, object]] = []

    try:
        for policy_index, policy in enumerate(policies):
            with admin.cursor() as cur:
                backend.apply_rls_policy(cur, policy, ["patients"])

            attacker_conn = backend.connect(args.attacker_dsn)

            try:
                with attacker_conn.cursor() as cur:
                    max_k = max(k_values)
                    scenario_calibration_by_k: Dict[int, CalibrationEntry] = {}
                    if args.calibration_mode == "scenario":
                        for k in k_values:
                            if not thresholds_by_key:
                                continue
                            threshold, auth_min, nonexist_min = thresholds_by_key[(policy, k)]
                            scenario_calibration_by_k[k] = calibration_entry(
                                threshold, auth_min, nonexist_min
                            )

                    if args.calibration_mode == "scenario" and not thresholds_by_key:
                        calibration_sample = sample_calibration(
                            cur,
                            query,
                            limit_params,
                            auth_key=authorized_keys[0],
                            nonexist_key=max_id + args.nonexistent_offset,
                            max_k=max_k,
                            k_values=k_values,
                            warm_cache=args.warm_cache,
                            fetch_one_only=fetch_one_only,
                        )
                        scenario_calibration_by_k = calibration_sample.calibration_by_k

                    # Cadence path: maintain parallel calibrations + stats keyed by cadence.
                    # The single-cadence path collapses to len(active_cadences) == 1.
                    active_cadences: List[int] = list(cadences) if cadences else [
                        1 if args.calibration_mode == "trial" else max(args.probes, 1)
                    ]
                    calibration_by_cadence: Dict[int, Dict[int, CalibrationEntry]] = {
                        c: scenario_calibration_by_k for c in active_cadences
                    }
                    calibration_stats_by_cadence: Dict[int, Dict[int, Dict[str, float]]] = {
                        c: {k: empty_calibration_stats() for k in k_values}
                        for c in active_cadences
                    }
                    stats_by_cadence: Dict[int, OracleProbeStats] = {
                        c: OracleProbeStats(k_values, args.probes)
                        for c in active_cadences
                    }
                    if args.calibration_mode == "scenario" and not cadences:
                        add_calibration_stats(
                            calibration_stats_by_cadence[active_cadences[0]],
                            scenario_calibration_by_k,
                        )

                    metrics_before = snapshot_postgres_metrics(
                        admin,
                        backend,
                        "before_probe_batch",
                        policy,
                    )

                    def calibrate_for_trial(idx: int) -> None:
                        if cadences:
                            firing = [c for c in active_cadences if idx % c == 0]
                        elif args.calibration_mode == "trial":
                            firing = [active_cadences[0]]
                        else:
                            firing = []
                        if not firing:
                            return

                        auth_key = authorized_keys[rng.randrange(len(authorized_keys))]
                        nonexist_key = (
                            max_id
                            + args.nonexistent_offset
                            + ((policy_index + 1) * args.probes * 2)
                            + idx
                        )
                        fresh_calibration = sample_calibration(
                            cur,
                            query,
                            limit_params,
                            auth_key=auth_key,
                            nonexist_key=nonexist_key,
                            max_k=max_k,
                            k_values=k_values,
                            warm_cache=args.warm_cache,
                            fetch_one_only=fetch_one_only,
                        )
                        for c in firing:
                            calibration_by_cadence[c] = fresh_calibration.calibration_by_k
                            add_calibration_stats(
                                calibration_stats_by_cadence[c],
                                fresh_calibration.calibration_by_k,
                            )

                    def unauthorized_key_for_idx(_idx: int) -> object:
                        return unauthorized_keys[rng.randrange(len(unauthorized_keys))]

                    def nonexistent_key_for_idx(idx: int) -> object:
                        return max_id + args.nonexistent_offset + idx + (policy_index * args.probes)

                    def record_trial(trial: OracleProbeTrial) -> None:
                        for c in active_cadences:
                            stats_by_cadence[c].record_probe(
                                trial.guess_mins,
                                calibration_by_cadence[c],
                                trial.actually_exists,
                            )

                    run_alternating_probe_trials(
                        cur=cur,
                        query=query,
                        limit_params=limit_params,
                        max_k=max_k,
                        probes=args.probes,
                        warm_cache=args.warm_cache,
                        fetch_one_only=fetch_one_only,
                        positive_key_for_idx=unauthorized_key_for_idx,
                        negative_key_for_idx=nonexistent_key_for_idx,
                        before_trial=calibrate_for_trial,
                        on_trial=record_trial,
                        progress_label=f"{policy}_k1-k{max_k}",
                        preview_for_trial=lambda trial: str(trial.key),
                    )

                    metrics_after = snapshot_postgres_metrics(
                        admin,
                        backend,
                        "after_probe_batch",
                        policy,
                    )
                    metrics_events.append(
                        build_metrics_event(
                            policy,
                            metrics_before,
                            metrics_after,
                            args.attacker_user,
                        )
                    )

                    for c in active_cadences:
                        accuracy_display_rows: List[Tuple[str, int, float]] = []
                        calibration_summary_by_k = summarize_calibration_stats(
                            calibration_stats_by_cadence[c]
                        )
                        probe_stats = stats_by_cadence[c]
                        for k in k_values:
                            stats = probe_stats.stats_for(k)
                            calibration = calibration_summary_by_k[k]
                            tp = int(stats["tp"])
                            fp = int(stats["fp"])
                            tn = int(stats["tn"])
                            fn = int(stats["fn"])
                            threshold = calibration.threshold
                            auth_min = calibration.auth_min
                            nonexist_min = calibration.nonexist_min

                            tp_rate_pct = probe_stats.tp_rate_pct(k)
                            tn_rate_pct = probe_stats.tn_rate_pct(k)
                            accuracy_pct = probe_stats.accuracy_pct(k)
                            accuracy_display_rows.append((policy, k, accuracy_pct))

                            summary_rows.append(
                                (
                                    policy,
                                    k,
                                    args.probes,
                                    tp,
                                    fp,
                                    tn,
                                    fn,
                                    tp_rate_pct,
                                    tn_rate_pct,
                                    accuracy_pct,
                                    int(threshold),
                                    auth_min,
                                    nonexist_min,
                                    args.noise_clients,
                                    args.noise_total_qps,
                                    "",
                                    "",
                                    "",
                                    "",
                                    "",
                                    args.calibration_mode,
                                    c,
                                )
                            )
                            markdown_rows.append(
                                AccuracySummaryRow(
                                    policy=policy,
                                    k=k,
                                    tp_rate_pct=tp_rate_pct,
                                    tn_rate_pct=tn_rate_pct,
                                    accuracy_pct=accuracy_pct,
                                )
                            )
                        print_accuracy_panel(
                            policy,
                            c if cadences else None,
                            accuracy_display_rows,
                        )
            finally:
                attacker_conn.close()

            noise_rows.append(
                {
                    "policy": policy,
                    "source": "external",
                    "clients": args.noise_clients,
                    "elapsed_s": None,
                    "target_total_qps": args.noise_total_qps,
                    "actual_total_qps": None,
                    "counts": None,
                    "avg_latency_ns": None,
                }
            )

        write_csv(
            args.output,
            summary_rows,
            header=(
                "policy",
                "k",
                "probes",
                "true_positive",
                "false_positive",
                "true_negative",
                "false_negative",
                "tp_rate_pct",
                "tn_rate_pct",
                "accuracy_pct",
                "threshold_ns",
                "authorized_min_ns",
                "nonexistent_min_ns",
                "noise_clients",
                "noise_target_total_qps",
                "noise_actual_total_qps",
                "noise_elapsed_s",
                "noise_authorized_queries",
                "noise_unauthorized_queries",
                "noise_nonexistent_queries",
                "calibration_mode",
                "calibration_cadence",
            ),
        )
        write_markdown_table(
            args.table_output,
            markdown_rows,
            policies,
            args.probes,
            args.noise_clients,
            args.noise_total_qps,
            args.calibration_mode,
        )
        write_json(args.noise_output, noise_rows)
        write_json(args.metrics_output, metrics_events)
    finally:
        admin.close()
