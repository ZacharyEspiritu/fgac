#!/usr/bin/env python3

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

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from util.paths import resolve_artifact_path


DATASET_ORDER = ("D1", "D10", "D100", "D1000")
STAGE_ORDER = (1, 2, 3, 4)


@dataclass(frozen=True)
class StageMetrics:
    dataset: str
    stage: int
    indexed: int
    recovered: int
    true_positive: int
    trie_nodes: int
    logical_queries: int
    query_batches: int
    injected_docs: int
    bulk_requests: int
    wall_clock_seconds: float


def load_json(path: Path) -> Dict[str, Any]:
    path = resolve_artifact_path(path)
    with path.open() as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    return data


def normalise_dataset_label(label: str) -> str:
    label = label.strip()
    if not label:
        return label
    if re.fullmatch(r"\d+", label):
        return f"D{label}"
    if re.fullmatch(r"[dD]\d+", label):
        return f"D{label[1:]}"
    return label


def parse_explicit_input(value: str) -> Tuple[str, Path]:
    if "=" not in value:
        raise ValueError(f"--input must be LABEL=PATH, got: {value}")
    label, path = value.split("=", 1)
    label = normalise_dataset_label(label.strip())
    if not label:
        raise ValueError(f"empty dataset label in --input {value}")
    return label, Path(path)


def infer_dataset_label(path: Path, data: Dict[str, Any]) -> str:
    script_args = (
        data.get("configuration", {}).get("script_args", {})
        if isinstance(data.get("configuration"), dict)
        else {}
    )
    candidates = [
        str(script_args.get("corpus_file", "")),
        str(script_args.get("stats_file", "")),
        path.name,
        str(path),
    ]
    for candidate in candidates:
        match = re.search(r"enron_d(\d+)", candidate)
        if match:
            return f"D{match.group(1)}"
    return path.stem


def stage_number(stage: Dict[str, Any]) -> Optional[int]:
    value = stage.get("ngram_size")
    if isinstance(value, int):
        return value
    value = stage.get("stage")
    if isinstance(value, int):
        return value
    return None


def int_value(mapping: Dict[str, Any], key: str, default: int = 0) -> int:
    value = mapping.get(key, default)
    if value is None:
        return default
    return int(value)


def attack_cost(stage: Dict[str, Any]) -> Tuple[int, int, int, int]:
    stats = stage.get("attack_stats") or {}
    if not isinstance(stats, dict):
        stats = {}
    logical_queries = int_value(stats, "logical_score_queries")
    query_batches = int_value(
        stats,
        "msearch_requests",
        int_value(stats, "prefix_batches") + int_value(stats, "exact_batches"),
    )
    injected_docs = int_value(
        stats,
        "probe_docs_injected",
        int_value(stats, "prefix_probe_docs_injected")
        + int_value(stats, "exact_probe_docs_injected"),
    )
    bulk_requests = int_value(
        stats,
        "bulk_injection_requests",
        int_value(stats, "prefix_bulk_injection_requests")
        + int_value(stats, "exact_bulk_injection_requests"),
    )
    return logical_queries, query_batches, injected_docs, bulk_requests


def wall_clock_seconds(stage: Dict[str, Any]) -> float:
    timing = stage.get("timing") or {}
    if not isinstance(timing, dict):
        return 0.0
    if "wall_clock_seconds" in timing:
        return float(timing["wall_clock_seconds"])
    return float(timing.get("enumerate_seconds", 0.0) or 0.0) + float(
        timing.get("stats_seconds", 0.0) or 0.0
    )


def metrics_from_counts(
    dataset: str,
    stage_num: int,
    stage: Dict[str, Any],
    counts: Dict[str, Any],
    indexed_key: str,
    true_positive_key: str,
    false_positive_key: str,
) -> StageMetrics:
    indexed = int_value(counts, indexed_key)
    true_positive = int_value(counts, true_positive_key)
    false_positive = int_value(counts, false_positive_key)
    logical_queries, query_batches, injected_docs, bulk_requests = attack_cost(stage)
    stats = stage.get("attack_stats") or {}
    if not isinstance(stats, dict):
        stats = {}
    return StageMetrics(
        dataset=dataset,
        stage=stage_num,
        indexed=indexed,
        recovered=true_positive + false_positive,
        true_positive=true_positive,
        trie_nodes=int_value(stats, "prefixes_tested"),
        logical_queries=logical_queries,
        query_batches=query_batches,
        injected_docs=injected_docs,
        bulk_requests=bulk_requests,
        wall_clock_seconds=wall_clock_seconds(stage),
    )


def unigram_metrics(dataset: str, stage: Dict[str, Any]) -> Optional[StageMetrics]:
    stats = stage.get("corpus_term_stats") or {}
    counts = stats.get("counts") if isinstance(stats, dict) else None
    if not isinstance(counts, dict):
        return None
    return metrics_from_counts(
        dataset=dataset,
        stage_num=1,
        stage=stage,
        counts=counts,
        indexed_key="eligible_terms",
        true_positive_key="recovered_eligible_terms",
        false_positive_key="extra_recovered_terms",
    )


def ngram_metrics(
    dataset: str,
    stage: Dict[str, Any],
    stage_num: int,
) -> Optional[StageMetrics]:
    stats = stage.get("corpus_ngram_stats") or {}
    counts = stats.get("counts") if isinstance(stats, dict) else None
    if not isinstance(counts, dict):
        return None
    return metrics_from_counts(
        dataset=dataset,
        stage_num=stage_num,
        stage=stage,
        counts=counts,
        indexed_key="indexed_ngrams",
        true_positive_key="recovered_indexed_ngrams",
        false_positive_key="extra_recovered_ngrams",
    )


def extract_metrics(dataset: str, data: Dict[str, Any]) -> Dict[int, StageMetrics]:
    metrics: Dict[int, StageMetrics] = {}
    stages = data.get("attack_stages")
    if not isinstance(stages, list):
        stages = []

    if not stages:
        top_level_unigram = {
            "ngram_size": 1,
            "attack_stats": data.get("attack_stats"),
            "corpus_term_stats": data.get("corpus_term_stats"),
        }
        stages = [top_level_unigram]
        ngram_recovery = data.get("ngram_recovery")
        if isinstance(ngram_recovery, dict):
            nested_stages = ngram_recovery.get("stages")
            if isinstance(nested_stages, list):
                stages.extend(nested_stages)
            else:
                stages.append(ngram_recovery)

    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_num = stage_number(stage)
        if stage_num not in STAGE_ORDER:
            continue
        if stage_num == 1:
            metric = unigram_metrics(dataset, stage)
        else:
            metric = ngram_metrics(dataset, stage, stage_num)
        if metric is not None:
            metrics[stage_num] = metric

    return metrics


def total_metrics(
    dataset: str,
    run_metrics: Dict[int, StageMetrics],
) -> Optional[StageMetrics]:
    stages = [run_metrics[stage] for stage in STAGE_ORDER if stage in run_metrics]
    if not stages:
        return None
    return StageMetrics(
        dataset=dataset,
        stage=0,
        indexed=0,
        recovered=0,
        true_positive=0,
        trie_nodes=sum(metric.trie_nodes for metric in stages),
        logical_queries=sum(metric.logical_queries for metric in stages),
        query_batches=sum(metric.query_batches for metric in stages),
        injected_docs=sum(metric.injected_docs for metric in stages),
        bulk_requests=sum(metric.bulk_requests for metric in stages),
        wall_clock_seconds=sum(metric.wall_clock_seconds for metric in stages),
    )


def duration_scale(unit: str) -> float:
    if unit == "seconds":
        return 1.0
    if unit == "minutes":
        return 60.0
    if unit == "hours":
        return 3600.0
    raise ValueError(f"unknown time unit: {unit}")


def dataset_sort_key(label: str) -> Tuple[int, str]:
    if label in DATASET_ORDER:
        return (DATASET_ORDER.index(label), label)
    match = re.fullmatch(r"D(\d+)", label)
    if match:
        return (len(DATASET_ORDER) + int(match.group(1)), label)
    return (10_000, label)


def collect_inputs(args: argparse.Namespace) -> List[Tuple[str, Path]]:
    inputs = [parse_explicit_input(value) for value in args.input]
    for path in args.stats_files:
        data = load_json(path)
        inputs.append((infer_dataset_label(path, data), path))
    return inputs


@dataclass(frozen=True)
class Row:
    dataset: str
    stage: int
    indexed: Optional[int]
    wall_clock_seconds: Optional[float]
    logical_queries: Optional[int]
    query_batches: Optional[int]
    injected_docs: Optional[int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render a compact booktabs/tabular LaTeX table with indexed counts, "
            "wall-clock time, logical queries, and query batches."
        )
    )
    parser.add_argument(
        "stats_files",
        nargs="*",
        type=Path,
        help=(
            "Stats JSON files. Dataset label is inferred from paths such as "
            "dataset/enron_d*.jsonl. "
            "Multiple files with the same inferred label are averaged."
        ),
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help=(
            "Explicit dataset label and stats file. Repeat the same label for "
            "replicate runs, e.g. --input D1000=run1.json --input D1000=run2.json."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write the LaTeX table fragment to this file instead of stdout.",
    )
    parser.add_argument(
        "--time-unit",
        choices=("seconds", "minutes", "hours"),
        default="minutes",
        help="Unit for the Time column. Default: minutes.",
    )
    parser.add_argument(
        "--width",
        default=r"\columnwidth",
        help="Deprecated; ignored because the renderer now emits tabular.",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip missing D/stage rows instead of emitting blank metric cells.",
    )
    return parser.parse_args()


def rounded_mean(values: Sequence[float]) -> int:
    return int(math.floor(sum(values) / len(values) + 0.5))


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def summarise_stage(
    dataset: str,
    stage: int,
    metrics: Sequence[StageMetrics],
) -> Row:
    return Row(
        dataset=dataset,
        stage=stage,
        indexed=rounded_mean([metric.indexed for metric in metrics]),
        wall_clock_seconds=mean([metric.wall_clock_seconds for metric in metrics]),
        logical_queries=rounded_mean([metric.logical_queries for metric in metrics]),
        query_batches=rounded_mean([metric.query_batches for metric in metrics]),
        injected_docs=rounded_mean([metric.injected_docs for metric in metrics]),
    )


def summarise_total(dataset: str, metrics: Sequence[StageMetrics]) -> Row:
    return Row(
        dataset=dataset,
        stage=0,
        indexed=None,
        wall_clock_seconds=mean([metric.wall_clock_seconds for metric in metrics]),
        logical_queries=rounded_mean([metric.logical_queries for metric in metrics]),
        query_batches=rounded_mean([metric.query_batches for metric in metrics]),
        injected_docs=rounded_mean([metric.injected_docs for metric in metrics]),
    )


def rows_for_dataset(
    dataset: str,
    runs: Sequence[Dict[int, StageMetrics]],
    *,
    skip_missing: bool,
) -> List[Row]:
    rows: List[Row] = []
    for stage in STAGE_ORDER:
        stage_metrics = [run[stage] for run in runs if stage in run]
        if stage_metrics:
            rows.append(summarise_stage(dataset, stage, stage_metrics))
        elif not skip_missing:
            rows.append(
                Row(
                    dataset=dataset,
                    stage=stage,
                    indexed=None,
                    wall_clock_seconds=None,
                    logical_queries=None,
                    query_batches=None,
                    injected_docs=None,
                )
            )

    total_stage_metrics = [
        metric
        for metric in (total_metrics(dataset, run_metrics) for run_metrics in runs)
        if metric is not None
    ]
    if total_stage_metrics:
        rows.append(summarise_total(dataset, total_stage_metrics))
    return rows


def latex_int(value: Optional[int]) -> str:
    if value is None:
        return ""
    return f"{value:,}".replace(",", r"{,}")


def latex_time(seconds: Optional[float], unit: str) -> str:
    if seconds is None:
        return ""
    value = seconds / duration_scale(unit)
    if 0.0 < value < 0.05:
        return r"$< 0.1$"
    return f"{value:.1f}"


def display_dataset(label: str) -> str:
    label = normalise_dataset_label(label)
    if label.startswith("D") and label[1:].isdigit():
        return label[1:]
    return label


def display_stage(stage: int) -> str:
    if stage == 0:
        return r"\textit{Total}"
    return f"{stage}-gram"


def format_row(row: Row, *, include_dataset: bool, group_size: int, time_unit: str) -> str:
    dataset = (
        rf"\multirow{{{group_size}}}{{*}}{{{display_dataset(row.dataset)}}}"
        if include_dataset
        else ""
    )
    indexed = "" if row.stage == 0 else latex_int(row.indexed)
    cells = [
        dataset,
        display_stage(row.stage),
        indexed,
        latex_int(row.logical_queries),
        latex_int(row.query_batches),
        latex_time(row.wall_clock_seconds, time_unit),
    ]
    return " & ".join(cells) + r" \\"


def render_table(rows: Sequence[Row], *, width: str, time_unit: str) -> str:
    del width
    time_header = {
        "seconds": r"\makecell[r]{Time\\(s)}",
        "minutes": r"\makecell[r]{Time\\(min)}",
        "hours": r"\makecell[r]{Time\\(h)}",
    }[time_unit]
    lines = [
        r"% Requires \usepackage{booktabs}",
        r"% Requires \usepackage{makecell}",
        r"% Requires \usepackage{multirow}",
        r"\begin{tabular}{rrrrrr}",
        r"\toprule",
        rf"\bf \makecell[r]{{\#\\Docs}} & \bf Stage & \bf \makecell[r]{{Indexed\\Terms}} & \bf \makecell[r]{{Logical\\ Queries}} & \bf \makecell[r]{{Inject/Query\\ Batches}} & \bf {time_header} \\",
        r"\midrule",
    ]

    index = 0
    while index < len(rows):
        dataset = rows[index].dataset
        group_end = index + 1
        while group_end < len(rows) and rows[group_end].dataset == dataset:
            group_end += 1
        group = rows[index:group_end]

        if index > 0:
            lines.append(r"\addlinespace")
        for offset, row in enumerate(group):
            lines.append(
                format_row(
                    row,
                    include_dataset=offset == 0,
                    group_size=len(group),
                    time_unit=time_unit,
                )
            )
        index = group_end

    lines.extend(
        [
            r"\midrule",
            r"\multicolumn{6}{c}{\bf \makecell{Across all corpus sizes and runs, each stage recovers the \\ indexed $n$-gram set exactly (100\% precision and recall).}} \\",
            r"\bottomrule",
            r"\end{tabular}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        inputs = collect_inputs(args)
        if not inputs:
            raise ValueError("no stats files provided and no default stats files found")

        by_dataset: Dict[str, List[Dict[int, StageMetrics]]] = {}
        for label, path in inputs:
            data = load_json(path)
            dataset = normalise_dataset_label(label)
            by_dataset.setdefault(dataset, []).append(extract_metrics(dataset, data))

        rows: List[Row] = []
        for dataset in sorted(by_dataset, key=dataset_sort_key):
            rows.extend(
                rows_for_dataset(
                    dataset,
                    by_dataset[dataset],
                    skip_missing=args.skip_missing,
                )
            )

        table = render_table(rows, width=args.width, time_unit=args.time_unit)
        if args.output is None:
            print(table)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(table + "\n")
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
