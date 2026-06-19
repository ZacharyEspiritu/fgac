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

"""
validate_cr9_db_sizes.py — parse a db_sizes.json and validate the C-R9 claim.

Claim (§1.1 / ARTIFACT_APPENDIX §1.4):
    The full patients and doctors database occupies 185 MB on disk
    (79 MB heap, 37 MB ssn index, 11 MB zip_code index, 7 MB age index,
    51 MB other indexes), as determined by querying PostgreSQL's statistics
    after loading the dataset.

"Other indexes" is the remainder: database_total − heap − ssn − zip_code − age.
It covers the primary-key and name indexes on patients, all of doctors, and
PostgreSQL system-catalog pages — everything not called out individually.

Usage:
    # Validate a run's db_sizes.json against the paper claim. No committed
    # reference is needed — the expected values are baked in below.
    uv run python -m renderers.validate_cr9_db_sizes results/dbsize/<RUN_ID>/db_sizes.json

    # (Optional) also diff one run against another, e.g. a prior run:
    uv run python -m renderers.validate_cr9_db_sizes <reference.json> --compare <new.json>

Exit code: 0 if all checks pass, 1 otherwise.
"""

import argparse
import json
import sys
from pathlib import Path


# Paper claim values (rounded MB)
CLAIM_TOTAL_MB = 185
CLAIM_HEAP_MB = 79
CLAIM_SSN_MB = 37
CLAIM_ZIP_MB = 11
CLAIM_AGE_MB = 7
CLAIM_OTHER_MB = 51

# Tolerance for rounded claim values (±1 MB covers all rounding in the paper)
TOLERANCE_MB = 1.0


def _mb(b: int) -> float:
    return b / 1024 / 1024


def _idx_bytes(d: dict, col: str) -> int:
    for idx in d.get("patients_indexes", []):
        if idx["columns"] == col:
            return int(idx["bytes"])
    raise KeyError(f"No patients index on column '{col}' found in JSON")


def extract(d: dict) -> dict:
    """Return the six byte counts that map to the six claim components."""
    heap = int(d["patients_heap_bytes"])
    ssn = _idx_bytes(d, "ssn")
    zip_code = _idx_bytes(d, "zip_code")
    age = _idx_bytes(d, "age")
    db_total = int(d["database_total_bytes"])
    # "other" = everything in the DB total not called out individually
    other = db_total - heap - ssn - zip_code - age
    return {
        "database_total_bytes": db_total,
        "heap_bytes": heap,
        "ssn_bytes": ssn,
        "zip_code_bytes": zip_code,
        "age_bytes": age,
        "other_bytes": other,
    }


def _check(label: str, actual_mb: float, expected_mb: float, tol: float = TOLERANCE_MB) -> bool:
    diff = actual_mb - expected_mb
    ok = abs(diff) <= tol
    status = "OK  " if ok else "FAIL"
    print(f"  [{status}]  {label:<40}  actual: {actual_mb:6.1f} MB   claim: ~{expected_mb} MB   diff: {diff:+.1f} MB")
    return ok


def validate_against_claim(d: dict, source_label: str = "") -> bool:
    """Print a per-component claim check and return True iff all pass."""
    c = extract(d)
    if source_label:
        print(f"Source: {source_label}")
    print()
    print("C-R9 claim: ~185 MB total  (79 MB heap · 37 MB ssn · 11 MB zip_code · 7 MB age · 51 MB other)")
    print()
    results = [
        _check("database total",               _mb(c["database_total_bytes"]), CLAIM_TOTAL_MB),
        _check("patients heap",                _mb(c["heap_bytes"]),           CLAIM_HEAP_MB),
        _check("patients_ssn_idx",             _mb(c["ssn_bytes"]),            CLAIM_SSN_MB),
        _check("patients_zip_code_idx",        _mb(c["zip_code_bytes"]),       CLAIM_ZIP_MB),
        _check("patients_age_idx",             _mb(c["age_bytes"]),            CLAIM_AGE_MB),
        _check("other (pk+name+doctors+sys)",  _mb(c["other_bytes"]),          CLAIM_OTHER_MB),
    ]
    print()
    if all(results):
        print("All components within ±1 MB of the paper claim.  C-R9 PASS.")
    else:
        n = results.count(False)
        print(f"{n} component(s) outside ±1 MB tolerance.  C-R9 FAIL.")
    return all(results)


def compare_to_reference(new_path: Path, ref_path: Path) -> bool:
    """Compare a new run's JSON against the committed reference JSON."""
    with new_path.open() as fh:
        new_d = json.load(fh)
    with ref_path.open() as fh:
        ref_d = json.load(fh)

    new_c = extract(new_d)
    ref_c = extract(ref_d)

    print(f"  new : {new_path}")
    print(f"  ref : {ref_path}")
    print()

    keys = [
        ("database total",              "database_total_bytes"),
        ("patients heap",               "heap_bytes"),
        ("patients_ssn_idx",            "ssn_bytes"),
        ("patients_zip_code_idx",       "zip_code_bytes"),
        ("patients_age_idx",            "age_bytes"),
        ("other (pk+name+doctors+sys)", "other_bytes"),
    ]
    ok = True
    for label, key in keys:
        new_mb = _mb(new_c[key])
        ref_mb = _mb(ref_c[key])
        diff = new_mb - ref_mb
        within = abs(diff) <= TOLERANCE_MB
        status = "OK  " if within else "DIFF"
        print(f"  [{status}]  {label:<40}  new: {new_mb:6.1f} MB   ref: {ref_mb:6.1f} MB   delta: {diff:+.1f} MB")
        if not within:
            ok = False

    print()
    if ok:
        print("New run matches the reference within ±1 MB.  C-R9 PASS.")
    else:
        print("New run diverges from the reference by >1 MB on ≥1 component.  C-R9 FAIL.")
    return ok


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "json",
        help="Path to the db_sizes.json to validate against the paper claim "
        "(e.g. results/dbsize/<RUN_ID>/db_sizes.json written by run_db_size_experiment.sh).",
    )
    p.add_argument(
        "--compare",
        metavar="NEW_JSON",
        help=(
            "Optionally diff NEW_JSON against the reference JSON given by the positional "
            "arg (e.g. a prior run). Both are then checked against the paper claim."
        ),
    )
    args = p.parse_args()

    ref_path = Path(args.json)

    if args.compare:
        new_path = Path(args.compare)
        print("\nC-R9 — diff new run against the reference run")
        ok_compare = compare_to_reference(new_path, ref_path)
        print()
        print("─" * 70)
        print("Validating new run against paper claim:")
        with new_path.open() as fh:
            new_d = json.load(fh)
        ok_claim = validate_against_claim(new_d, source_label=str(new_path))
        ok = ok_compare and ok_claim
    else:
        with ref_path.open() as fh:
            d = json.load(fh)
        ok = validate_against_claim(d, source_label=str(ref_path))

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
