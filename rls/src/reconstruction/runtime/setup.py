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

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from reconstruction.candidates import CandidateSpec, LiteralValues, parse_candidate_spec
from reconstruction.cli import ReconstructionOptions
from reconstruction.reporting.console import print_info
from reconstruction.sql.db import (
    fetch_column_type,
    pick_existing_value,
    pick_missing_value,
    sample_tuples,
)
from reconstruction.truth import GroundTruth
from reconstruction.types import DbConnection, DbRow, DbValue
from util.sql_utils import validate_identifier


@dataclass
class KnownValues:
    exists: DbValue
    missing: DbValue


@dataclass
class ReconstructionSetup:
    candidates: Dict[str, CandidateSpec]
    attributes: List[str]
    known_values: Dict[str, KnownValues]


def prepare_reconstruction_setup(
    args: ReconstructionOptions,
    admin: Optional[DbConnection],
) -> ReconstructionSetup:
    candidates: Dict[str, CandidateSpec] = {}
    for key, value in args.candidates.items():
        candidates[key] = parse_candidate_spec(value)

    attributes: List[str]
    if args.attributes:
        attributes = [
            attr.strip() for attr in args.attributes.split(",") if attr.strip()
        ]
    else:
        attributes = list(candidates.keys())

    if not attributes:
        raise RuntimeError("No attributes specified")

    for attr in attributes:
        validate_identifier(attr)

    known_values: Dict[str, KnownValues] = {}

    if not args.skip_attr_probe and args.sample_tuples <= 0:
        missing_candidates = [attr for attr in attributes if attr not in candidates]
        if missing_candidates:
            raise RuntimeError(
                f"Missing candidates for: {', '.join(missing_candidates)}"
            )

    if args.skip_attr_probe:
        if not admin:
            raise RuntimeError("--skip-attr-probe requires --admin-dsn")
        if args.sample_tuples <= 0:
            raise RuntimeError("--skip-attr-probe requires --sample-tuples > 0")
        with admin.cursor() as cur:
            tuple_rows = sample_tuples(cur, args.table, attributes, args.sample_tuples)
            _merge_aligned_tuple_candidates(candidates, attributes, tuple_rows)

    elif args.sample_tuples > 0:
        missing_candidates = [attr for attr in attributes if attr not in candidates]
        if missing_candidates:
            if not admin:
                raise RuntimeError("--sample-tuples requires --admin-dsn")
            with admin.cursor() as cur:
                tuple_rows = sample_tuples(
                    cur, args.table, attributes, args.sample_tuples
                )
            for idx, attr in enumerate(attributes):
                if attr in candidates:
                    continue
                candidates[attr] = CandidateSpec(
                    values=LiteralValues(_distinct_column_values(tuple_rows, idx))
                )

    if admin:
        rng = random.Random(1)
        with admin.cursor() as cur:
            for attr in attributes:
                column_type = fetch_column_type(cur, args.table, attr)
                known_values[attr] = KnownValues(
                    exists=pick_existing_value(cur, args.table, attr),
                    missing=pick_missing_value(cur, args.table, attr, column_type, rng),
                )

    if any(attr not in known_values for attr in attributes):
        raise RuntimeError("Missing known values for some attributes; use --admin-dsn")

    return ReconstructionSetup(
        candidates=candidates,
        attributes=attributes,
        known_values=known_values,
    )


def load_ground_truth_if_needed(
    admin: Optional[DbConnection],
    table: str,
    attributes: Sequence[str],
    verify: bool,
    no_progress_output: bool,
    stage_times: Dict[str, float],
) -> Optional[GroundTruth]:
    if admin is None or not verify:
        return None

    start = time.perf_counter()
    ground_truth = GroundTruth.load(admin, table, attributes)
    stage_times["ground_truth_load"] = time.perf_counter() - start
    if not no_progress_output:
        print_info(
            f"Loaded ground truth: {ground_truth.row_count} rows "
            f"in {stage_times['ground_truth_load']:.2f}s"
        )
    return ground_truth


def resolve_known_tuple(
    args: ReconstructionOptions,
    admin: Optional[DbConnection],
    tuple_attrs: Sequence[str],
) -> Dict[str, DbValue]:
    if admin:
        validate_identifier(args.table)
        for attr in tuple_attrs:
            validate_identifier(attr)
        with admin.cursor() as cur:
            cur.execute(f"SELECT {', '.join(tuple_attrs)} FROM {args.table} LIMIT 1")
            row = cur.fetchone()
            if row:
                return {attr: row[idx] for idx, attr in enumerate(tuple_attrs)}

    raise RuntimeError("Unable to determine known existing tuple; provide --admin-dsn")


def _merge_aligned_tuple_candidates(
    candidates: Dict[str, CandidateSpec],
    attributes: List[str],
    tuple_rows: List[DbRow],
) -> None:
    for idx, attr in enumerate(attributes):
        aligned = _distinct_column_values(tuple_rows, idx)
        spec = candidates.get(attr)
        if spec is None:
            candidates[attr] = CandidateSpec(
                values=LiteralValues(aligned),
                skip_probe=True,
            )
        else:
            spec.values = LiteralValues(aligned)
            spec.skip_probe = True


def _distinct_column_values(rows: List[DbRow], idx: int) -> List[DbValue]:
    seen = set()
    values: List[DbValue] = []
    for row in rows:
        value = row[idx]
        if value not in seen:
            seen.add(value)
            values.append(value)
    return values
