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
import traceback
from typing import Dict, List, Optional, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mitigation.attack import AttackResult, capture_explain, run_attack
from mitigation.configs import ALL_CONFIGS, CONFIGS_BY_NAME, MitigationConfig
from mitigation.db_setup import (
    DOCTORS_INDEX_NAME,
    apply_index_layout,
    apply_policy,
    create_doctors_index,
    drop_doctors_index,
    restore_baseline,
)
from mitigation.outputs import write_markdown_summary, write_per_config_outputs
from util.args import (
    add_db_connection_args,
    add_fast_arg,
    add_probes_arg,
    add_seed_arg,
    add_warm_cache_arg,
    parse_csv_ints,
    parse_csv_strings,
    require_positive,
)
from util.db_backend import DatabaseBackend
from util.io import write_csv, write_json
from util.sql_utils import validate_identifier
from patients.sampling import load_attribute_value_pool, load_patient_sampling_context


CONSOLE = Console(highlight=False, width=120)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m mitigation",
        description=(
            "Evaluate join-policy RLS mitigations with the full Section 3.3 "
            "attack pipeline. RLS stays active across every measurement arm."
        )
    )
    add_db_connection_args(
        parser,
        admin_help="Admin DSN for policy / index / role management.",
        attacker_help="Attacker DSN (URL-style PostgreSQL).",
        attacker_user_help="Attacker DB role name (must match doctors.user_name).",
    )
    parser.add_argument(
        "--attribute",
        default="ssn",
        choices=("ssn", "id_number", "zip_code", "name", "age"),
        help=(
            "Indexed patients attribute to probe (default: ssn). Must be one "
            "for which `unauthorized` rows exist in other sites but NOT in "
            "the attacker's site -- ssn and id_number are unique-per-patient "
            "and always work; age has cardinality 120 and the unauthorized "
            "class is structurally empty (all ages are present in all sites)."
        ),
    )
    parser.add_argument("--configs", default=",".join(cfg.name for cfg in ALL_CONFIGS),
                        help="Comma-separated list of configuration names to run.")
    add_probes_arg(parser, default=2000, help="Probes per configuration (default: 2000).")
    parser.add_argument("--k-values", default="1,2,3",
                        help="Comma-separated k values for min-of-k oracle (default: 1,2,3).")
    parser.add_argument("--samples-pool", type=int, default=1024,
                        help="Pool size of authorized / unauthorized keys to draw from.")
    add_seed_arg(parser)
    add_warm_cache_arg(parser, help="Issue a single warmup query per probe before timing.")
    add_fast_arg(parser, help="Use SELECT 1 ... LIMIT 1 instead of SELECT *.")
    parser.add_argument("--output-dir",
                        default=os.path.join("results", "join_mitigations"),
                        help="Directory for per-config outputs.")
    parser.add_argument("--skip-restore", action="store_true",
                        help="Leave the final config's state in place; do NOT restore baseline. "
                             "Useful for debugging; never use against shared deployments.")
    parser.add_argument("--doctors-index", dest="doctors_index", action="store_true", default=False,
                        help="OPT-IN: create a COLLATE \"C\" index on doctors(user_name) so the RLS "
                             "policy's `WHERE user_name = current_user` lookup uses an index scan "
                             "instead of a seq scan. Default OFF: doctors(user_name) is left "
                             "unindexed (the per-query lookup seq-scans doctors). When enabled the "
                             "index is scoped to this experiment -- created at start, dropped on "
                             "restore -- and NOT added to the shared schema.")
    parser.add_argument("--no-doctors-index", dest="doctors_index", action="store_false",
                        help="Explicitly keep doctors(user_name) unindexed (this is the default).")
    return parser.parse_args()


def selected_configs(configs_arg: str) -> List[MitigationConfig]:
    config_names = parse_csv_strings(configs_arg, "configs")
    unknown = [name for name in config_names if name not in CONFIGS_BY_NAME]
    if unknown:
        raise SystemExit(f"Unknown configs: {unknown}; valid: {list(CONFIGS_BY_NAME)}")
    return [CONFIGS_BY_NAME[name] for name in config_names]


def write_summary_outputs(
    output_dir: str,
    *,
    attribute: str,
    attacker_user: str,
    attacker_site: int,
    probes: int,
    k_values: List[int],
    seed: int,
    results: List[Tuple[MitigationConfig, Optional[AttackResult], Optional[str]]],
    config_metadata: List[Dict[str, object]],
    explain_records: Dict[str, Dict[str, List[str]]],
) -> str:
    summary_csv_path = os.path.join(output_dir, "summary.csv")
    summary_rows = []
    for config, result, _error in results:
        if result is None:
            continue
        for k in k_values:
            summary_rows.append((
                config.name,
                config.policy_form,
                config.index_layout,
                k,
                result.probes,
                f"{result.tp_rate_pct(k):.4f}",
                f"{result.tn_rate_pct(k):.4f}",
                f"{result.accuracy_pct(k):.4f}",
                int(round(result.threshold_avg_by_k[k])),
                int(round(result.auth_min_avg_by_k[k])),
                int(round(result.nonexist_min_avg_by_k[k])),
            ))
    write_csv(
        summary_csv_path,
        summary_rows,
        header=(
            "config",
            "policy_form",
            "index_layout",
            "k",
            "probes",
            "tp_rate_pct",
            "tn_rate_pct",
            "accuracy_pct",
            "threshold_ns_avg",
            "auth_min_ns_avg",
            "nonexist_min_ns_avg",
        ),
    )
    write_markdown_summary(
        os.path.join(output_dir, "summary.md"),
        attribute=attribute,
        probes=probes,
        k_values=k_values,
        results=results,
    )
    write_json(
        os.path.join(output_dir, "configs.json"),
        {
            "attribute": attribute,
            "attacker_user": attacker_user,
            "attacker_site": attacker_site,
            "probes": probes,
            "k_values": k_values,
            "seed": seed,
            "configurations": config_metadata,
        },
    )
    write_json(os.path.join(output_dir, "explain_plans.json"), explain_records)
    return summary_csv_path


def print_run_panel(
    args: argparse.Namespace,
    *,
    k_values: List[int],
    selected: List[MitigationConfig],
    attacker_site: int,
    authorized_count: int,
    unauthorized_count: int,
    nonexistent_count: int,
) -> None:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("Attribute", args.attribute)
    table.add_row("Attacker", f"{args.attacker_user} (site {attacker_site})")
    table.add_row("Configurations", ", ".join(config.name for config in selected))
    table.add_row("Probes", f"{args.probes:,}")
    table.add_row("k values", ", ".join(str(k) for k in k_values))
    table.add_row(
        "Sample pools",
        (
            f"authorized={authorized_count:,}, "
            f"unauthorized={unauthorized_count:,}, "
            f"nonexistent={nonexistent_count:,}"
        ),
    )
    table.add_row("Output", args.output_dir)
    table.add_row("Doctors index", "enabled" if args.doctors_index else "disabled")
    CONSOLE.print(
        Panel(
            table,
            title="Join-Policy Mitigation Sweep",
            title_align="left",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def print_config_panel(config: MitigationConfig, index: int, total: int) -> None:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("Configuration", config.name)
    table.add_row("Policy form", config.policy_form)
    table.add_row("Index layout", config.index_layout)
    table.add_row("Expected", config.expected_outcome)
    table.add_row("Description", config.description)
    CONSOLE.print(
        Panel(
            table,
            title=f"Configuration {index}/{total}",
            title_align="left",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def print_explain_panel(label: str, rows: List[str]) -> None:
    CONSOLE.print(
        Panel(
            Text("\n".join(rows)),
            title=f"EXPLAIN ({label})",
            title_align="left",
            border_style="bright_black",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def print_accuracy_panel(config_name: str, result: AttackResult, k_values: List[int]) -> None:
    table = Table(
        box=box.SIMPLE,
        border_style="bright_black",
        header_style="bold white",
        padding=(0, 0),
        show_lines=False,
        expand=True,
    )
    table.add_column("k", justify="right", no_wrap=True)
    table.add_column("TP", justify="right", no_wrap=True)
    table.add_column("TN", justify="right", no_wrap=True)
    table.add_column("Accuracy", justify="right", no_wrap=True)
    for k in k_values:
        table.add_row(
            str(k),
            f"{result.tp_rate_pct(k):.2f}%",
            f"{result.tn_rate_pct(k):.2f}%",
            f"{result.accuracy_pct(k):.2f}%",
        )
    CONSOLE.print(
        Panel.fit(
            table,
            title=f"Mitigation Accuracy - {config_name}",
            title_align="left",
            border_style="green",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def print_artifact_panel(summary_csv_path: str) -> None:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")
    table.add_row("Summary CSV", summary_csv_path)
    table.add_row("Summary Markdown", os.path.join(os.path.dirname(summary_csv_path), "summary.md"))
    table.add_row("Config metadata", os.path.join(os.path.dirname(summary_csv_path), "configs.json"))
    table.add_row("EXPLAIN plans", os.path.join(os.path.dirname(summary_csv_path), "explain_plans.json"))
    CONSOLE.print(
        Panel(
            table,
            title="Mitigation Artifacts Written",
            title_align="left",
            border_style="bright_blue",
            box=box.ROUNDED,
            padding=(0, 1),
        )
    )


def main() -> None:
    args = parse_args()

    k_values = parse_csv_ints(args.k_values, "k-values")
    selected = selected_configs(args.configs)
    require_positive(args.probes, "--probes")
    validate_identifier(args.attribute)
    validate_identifier(args.attacker_user)

    rng = random.Random(args.seed)
    backend = DatabaseBackend.from_dsn(args.admin_dsn)

    admin = backend.connect(args.admin_dsn)
    with admin.cursor() as cur:
        try:
            patient_context = load_patient_sampling_context(cur, backend, args.attacker_user)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
        sample_count = max(args.samples_pool, args.probes // 2 + 1)
        value_pool = load_attribute_value_pool(
            cur,
            backend,
            args.attribute,
            patient_context.attacker_site,
            sample_count,
            rng,
            patient_context.max_id,
        )
        authorized_keys = value_pool.authorized_values
        unauthorized_keys = value_pool.unauthorized_values
        nonexistent_keys = value_pool.nonexistent_values
        if len(authorized_keys) < args.probes // 2 + 1:
            raise SystemExit(
                f"Not enough authorized {args.attribute} values "
                f"({len(authorized_keys)}) for the requested probe count "
                f"({args.probes}); reduce --probes or pick a higher-cardinality attribute."
            )
        if len(unauthorized_keys) < args.probes // 2 + 1:
            raise SystemExit(
                f"Not enough unauthorized {args.attribute} values "
                f"({len(unauthorized_keys)}) for the requested probe count "
                f"({args.probes}); reduce --probes or pick a higher-cardinality attribute."
            )
        if args.doctors_index:
            create_doctors_index(cur)
            CONSOLE.print(
                f"[green]Created[/green] {DOCTORS_INDEX_NAME} on doctors(user_name COLLATE \"C\")."
            )

    select_expr = "1" if args.fast else "*"
    limit = 1 if args.fast else None
    fetch_one_only = args.fast
    base_query = f"SELECT {select_expr} FROM patients WHERE {args.attribute} = {backend.param}"
    query, limit_params = backend.add_limit(base_query, limit)

    os.makedirs(args.output_dir, exist_ok=True)
    results: List[Tuple[MitigationConfig, Optional[AttackResult], Optional[str]]] = []
    config_metadata: List[Dict[str, object]] = []
    explain_records: Dict[str, Dict[str, List[str]]] = {}

    attacker_conn = backend.connect(args.attacker_dsn)
    try:
        print_run_panel(
            args,
            k_values=k_values,
            selected=selected,
            attacker_site=patient_context.attacker_site,
            authorized_count=len(authorized_keys),
            unauthorized_count=len(unauthorized_keys),
            nonexistent_count=len(nonexistent_keys),
        )
        for config_idx, config in enumerate(selected, start=1):
            print_config_panel(config, config_idx, len(selected))
            error_message: Optional[str] = None
            result: Optional[AttackResult] = None
            explain_plans: Dict[str, List[str]] = {}

            try:
                with admin.cursor() as cur:
                    apply_index_layout(cur, args.attribute, config.index_layout)
                    apply_policy(
                        cur,
                        policy_form=config.policy_form,
                        attacker_user=args.attacker_user,
                    )

                with attacker_conn.cursor() as cur:
                    explain_plans = capture_explain(
                        cur,
                        query,
                        limit_params,
                        authorized_key=authorized_keys[0],
                        unauthorized_key=unauthorized_keys[0],
                        nonexistent_key=nonexistent_keys[0],
                    )
                    for label, rows in explain_plans.items():
                        print_explain_panel(label, rows)

                    result = run_attack(
                        config_name=config.name,
                        cur=cur,
                        query=query,
                        limit_params=limit_params,
                        fetch_one_only=fetch_one_only,
                        warm_cache=args.warm_cache,
                        authorized_keys=authorized_keys,
                        unauthorized_keys=unauthorized_keys,
                        nonexistent_keys=nonexistent_keys,
                        probes=args.probes,
                        k_values=k_values,
                        rng=rng,
                    )

                print_accuracy_panel(config.name, result, k_values)

                config_dir = os.path.join(args.output_dir, config.name)
                write_per_config_outputs(
                    config_dir,
                    config=config,
                    result=result,
                    explain_plans=explain_plans,
                )
                explain_records[config.name] = explain_plans
            except Exception as exc:  # pragma: no cover - depends on live PG
                error_message = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()

            results.append((config, result, error_message))
            config_metadata.append({
                "name": config.name,
                "description": config.description,
                "policy_form": config.policy_form,
                "index_layout": config.index_layout,
                "expected_outcome": config.expected_outcome,
                "error": error_message,
            })

        summary_csv_path = write_summary_outputs(
            args.output_dir,
            attribute=args.attribute,
            attacker_user=args.attacker_user,
            attacker_site=patient_context.attacker_site,
            probes=args.probes,
            k_values=k_values,
            seed=args.seed,
            results=results,
            config_metadata=config_metadata,
            explain_records=explain_records,
        )
        print_artifact_panel(summary_csv_path)
    finally:
        try:
            attacker_conn.close()
        except Exception:
            pass
        if args.skip_restore:
            CONSOLE.print("[yellow]--skip-restore set; leaving final config state in place.[/yellow]")
        else:
            CONSOLE.print("[cyan]Restoring baseline state[/cyan] (plpgsql policy + single-column index)...")
            try:
                with admin.cursor() as cur:
                    restore_baseline(cur, attributes=[args.attribute])
                    if args.doctors_index:
                        drop_doctors_index(cur)
                CONSOLE.print("[green]Baseline restored.[/green]")
            except Exception as exc:  # pragma: no cover - depends on live PG
                CONSOLE.print(f"[yellow]WARNING:[/yellow] baseline restore failed: {exc}")
                traceback.print_exc()
        admin.close()
