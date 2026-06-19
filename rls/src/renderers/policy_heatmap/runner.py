"""Render a unified policy-accuracy heatmap with multiple RLS policies side by side.

Each row corresponds to a single DB-CPU load level. The leftmost column is the
shared "CPU Load" header; for every policy we render a block of k=1..10 heat
cells plus the "min k for 99% accuracy" and "queries per probe" summary
columns. Cells that exist for one policy but not another (e.g. an inline run
that timed out trying to reach 95% DB CPU because inline noise queries are too
cheap) are drawn as empty so the load alignment stays correct.

Example usage:

    uv run python -m renderers.policy_heatmap \\
        --policy-dir join=results/postgres/table1_5pct_k1_10_100k_pertrial \\
        --policy-dir inline=results/postgres/table1_5pct_k1_10_100k_pertrial_inline \\
        --output-prefix results/postgres/table1_5pct_k1_10_100k_pertrial/policy_accuracy
"""

import argparse
from typing import Dict, List

from .data import DEFAULT_K_VALUES
from .core import PolicySpec, load_policy_data
from .mpl import write_unified_heatmap
from util.args import parse_csv_ints


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m renderers.policy_heatmap",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--policy-dir",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help=(
            "Policy block to include, in display order. PATH is the per-policy "
            "base directory containing table1_noise_sweep.csv. Pass once per policy."
        ),
    )
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument(
        "--heatmap-suffix",
        default="_heatmap",
        help="Token appended to --output-prefix for the heatmap files "
        "(default '_heatmap', i.e. <prefix>_heatmap.{png,pdf,pgf}). Pass an empty "
        "string to write <prefix>.{png,pdf,pgf}.",
    )
    parser.add_argument(
        "--k-values",
        default=",".join(str(k) for k in DEFAULT_K_VALUES),
    )
    parser.add_argument(
        "--queries-per-timetest",
        type=int,
        default=3,
        help="Query multiplier for one TIMETEST at k=1 (3 for per-probe calibration).",
    )
    parser.add_argument(
        "--heatmap-log-floor",
        type=float,
        default=0.01,
        help="Smallest non-zero error percentage point used for the color norm.",
    )
    parser.add_argument(
        "--heatmap-ci",
        choices=("none", "99"),
        default="99",
        help="Confidence-interval margin to show under each heatmap value.",
    )
    parser.add_argument(
        "--policy-labels",
        default="",
        help=(
            "Optional comma-separated display labels for the policy supraheaders, "
            "in the same order as --policy-dir. Defaults to the policy name with "
            "its first letter capitalised."
        ),
    )
    parser.add_argument(
        "--policy-k-values",
        action="append",
        default=[],
        metavar="NAME=k1,k2,...",
        help=(
            "Per-policy override of --k-values for that policy's k columns. "
            "Pass once per policy; unspecified policies use --k-values."
        ),
    )
    parser.add_argument(
        "--omit-summary",
        default="",
        help=(
            "Comma-separated policy NAMEs whose '@ ≥ 99%% acc' (min-k + cost) "
            "summary columns should be omitted."
        ),
    )
    parser.add_argument(
        "--omit-qps",
        default="",
        help=(
            "Comma-separated policy NAMEs whose 'Conc. Reads Per Second' column "
            "should be omitted. By default every policy block has the column."
        ),
    )
    parser.add_argument(
        "--omit-cost",
        default="",
        help=(
            "Comma-separated policy NAMEs whose 'Queries Per Probe (3k)' cost "
            "sub-column (under '@ >= 99%% acc') should be omitted, leaving the "
            "min-k column. Independent of --omit-summary (which drops both)."
        ),
    )
    parser.add_argument(
        "--missing-message",
        action="append",
        default=[],
        metavar="NAME=text",
        help=(
            "If a CPU-load row exists for some other policy but the named policy "
            "has no data, replace the row's k-cells with a single merged italic "
            "cell containing this text. Repeatable, one entry per policy."
        ),
    )
    parser.add_argument(
        "--show-99-frontier",
        action="store_true",
        help=(
            "Draw a blue staircase frontier marking each row's minimum k for "
            "99%% accuracy. Off by default."
        ),
    )
    parser.add_argument(
        "--cpu-loads",
        default="",
        help=(
            "Optional comma-separated subset of CPU loads to render, in display "
            "order. Use 'Base' (or 'baseline') for the no-noise row; numeric "
            "values match target_cpu_pct (e.g. '15' picks the cpu15 cell). "
            "If empty (default), every load present in the inputs is shown. "
            "Example for a compact 10-row figure: "
            "--cpu-loads Base,15,25,35,45,55,65,75,85,95"
        ),
    )
    return parser.parse_args()


def parse_policy_k_values(raw_specs: List[str]) -> Dict[str, List[int]]:
    per_policy_k: Dict[str, List[int]] = {}
    for spec in raw_specs:
        name, sep, raw = spec.partition("=")
        if not sep:
            raise SystemExit(f"--policy-k-values expects NAME=k1,k2,..., got {spec!r}")
        per_policy_k[name.strip()] = parse_csv_ints(raw, "--policy-k-values")
    return per_policy_k


def parse_missing_messages(raw_specs: List[str]) -> Dict[str, List[str]]:
    messages: Dict[str, List[str]] = {}
    for spec in raw_specs:
        name, sep, text = spec.partition("=")
        if not sep:
            raise SystemExit(f"--missing-message expects NAME=text, got {spec!r}")
        normalised = text.replace("\\\\", "\n").replace("\\n", "\n").replace("\\", "\n")
        lines = [line.strip() for line in normalised.split("\n") if line.strip()]
        messages[name.strip()] = lines
    return messages


def parse_name_set(raw: str) -> set:
    return {name.strip() for name in raw.split(",") if name.strip()}


def validate_policy_names(known_names: set, flag_name: str, names) -> None:
    for name in names:
        if name not in known_names:
            raise SystemExit(f"{flag_name} references unknown policy {name!r}")


def main() -> None:
    args = parse_args()
    default_k_values = parse_csv_ints(args.k_values, "--k-values")
    full_policy_data = load_policy_data(
        args.policy_dir,
        default_k_values,
        args.queries_per_timetest,
    )

    per_policy_k = parse_policy_k_values(args.policy_k_values)
    omit_summary = parse_name_set(args.omit_summary)
    omit_qps = parse_name_set(args.omit_qps)
    omit_cost = parse_name_set(args.omit_cost)
    missing_messages = parse_missing_messages(args.missing_message)

    labels_by_name: Dict[str, str] = {}
    if args.policy_labels.strip():
        labels = [label.strip() for label in args.policy_labels.split(",")]
        if len(labels) != len(full_policy_data):
            raise SystemExit(
                f"--policy-labels has {len(labels)} entries but --policy-dir has "
                f"{len(full_policy_data)} policies."
            )
        for name, label in zip(full_policy_data.keys(), labels):
            labels_by_name[name] = label

    known_names = set(full_policy_data.keys())
    validate_policy_names(known_names, "--policy-k-values", per_policy_k)
    validate_policy_names(known_names, "--omit-summary", omit_summary)
    validate_policy_names(known_names, "--omit-qps", omit_qps)
    validate_policy_names(known_names, "--omit-cost", omit_cost)
    validate_policy_names(known_names, "--missing-message", missing_messages)

    policy_specs: List[PolicySpec] = []
    for name, rows in full_policy_data.items():
        policy_specs.append(
            PolicySpec(
                name=name,
                label=labels_by_name.get(name, name.capitalize()),
                rows=rows,
                k_values=per_policy_k.get(name, list(default_k_values)),
                show_summary=name not in omit_summary,
                show_qps=name not in omit_qps,
                show_cost=name not in omit_cost,
                missing_message_lines=missing_messages.get(name),
            )
        )

    cpu_loads_filter = [item.strip() for item in args.cpu_loads.split(",") if item.strip()] or None
    write_unified_heatmap(
        args.output_prefix,
        policy_specs,
        args.heatmap_log_floor,
        args.heatmap_ci,
        show_99_frontier=args.show_99_frontier,
        cpu_loads_filter=cpu_loads_filter,
        heatmap_suffix=args.heatmap_suffix,
    )
    print(f"Wrote {args.output_prefix}{args.heatmap_suffix}.png")
    print(f"Wrote {args.output_prefix}{args.heatmap_suffix}.pdf")
    print(f"Wrote {args.output_prefix}{args.heatmap_suffix}.pgf")


if __name__ == "__main__":
    main()
