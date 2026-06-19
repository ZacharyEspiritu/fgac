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

from reconstruction.config_loader import DEFAULT_RECONSTRUCTION_CONFIG


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reconstruction",
        description=(
            "Attempt to reconstruct tuples of chosen attributes using timing side-channels."
        ),
    )
    parser.add_argument("--attacker-dsn", required=True, help="Attacker DSN (RLS applies).")
    parser.add_argument(
        "--admin-dsn",
        required=True,
        help="Admin DSN for calibration, candidate sampling, and verification.",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_RECONSTRUCTION_CONFIG,
        help="Reconstruction config file.",
    )
    parser.add_argument(
        "--rls-policy",
        choices=("join", "inline"),
        default=None,
        help="RLS policy variant to apply before running (requires --admin-dsn).",
    )
    parser.add_argument("--table", default=None, help="Target table.")
    parser.add_argument(
        "--attributes",
        default=None,
        help="Comma-separated attribute list (defaults to keys in the config).",
    )
    parser.add_argument(
        "--skip-attr-probe",
        action="store_true",
        default=None,
        help="Skip attribute probing and instead sample candidates from the dataset (requires --admin-dsn).",
    )
    parser.add_argument(
        "--sample-tuples",
        type=int,
        default=None,
        help="Sample N existing tuples from the database (requires --admin-dsn).",
    )
    parser.add_argument(
        "--num-queries-for-initial-calibration",
        type=int,
        default=None,
        help="Rounds to calibrate missing vs existing threshold.",
    )
    parser.add_argument(
        "--num-queries-per-probe",
        type=int,
        default=None,
        help="Rounds per candidate/tuple (min runtime used).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Worker threads for probing (each uses its own DB connection).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=None,
        help="Verify guesses using admin queries (requires --admin-dsn).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for CSV/JSON outputs.",
    )
    parser.add_argument(
        "--no-progress-output",
        action="store_true",
        default=None,
        help="Disable progress output.",
    )
    parser.add_argument(
        "--log-oracle-calls",
        action="store_true",
        default=None,
        help=(
            "Record every binary-search oracle call (low, high, span, is_leaf, "
            "min_elapsed_ns, threshold_ns, guess) and post-process with admin "
            "ground truth to emit reconstruction_oracle_calls.csv plus aggregate "
            "per-call TP/FP/TN/FN/accuracy in the summary."
        ),
    )
    parser.add_argument(
        "--tuple-recompute-threshold",
        action="store_true",
        default=None,
        help=(
            "Recalibrate the tuple-extension threshold on every probe. For each "
            "candidate probe, run two extra queries at the KNOWN tuple "
            "(rt_missing_cal with known prefix + missing target, rt_exists_cal "
            "with known prefix + known target), compute fresh threshold = "
            "(rt_missing_cal + rt_exists_cal) / 2, then issue the candidate "
            "probe and decide hot via cand_rt > threshold. 3x query cost. "
            "Tracks per-probe system noise (jitter, GC, CPU freq) better than "
            "the static per-step threshold, but still anchored to the known "
            "tuple's plan choice."
        ),
    )
    parser.add_argument(
        "--tuple-recompute-cal-rounds",
        type=int,
        default=None,
        help=(
            "Number of rounds to use for each calibration query in "
            "--tuple-recompute-threshold mode (default 1; total per-probe DB "
            "queries = 1 candidate + 2 * this rounds). Higher = more stable "
            "fresh threshold, lower = cheaper."
        ),
    )
    parser.add_argument(
        "--tuple-extension-mode",
        choices=["any", "between"],
        default=None,
        help=(
            "Tuple-extension query shape. `any` (default) issues `next_attr = "
            "ANY($N)` with the bisection subset as an array - the original "
            "InProber behaviour. `between` issues `next_attr BETWEEN $N AND "
            "$N+1` using the subset's min/max from the sorted recovered list. "
            "BETWEEN avoids the per-call array-serialization overhead (sending "
            "a 100k-element string array per probe is expensive) and can be 5-"
            "10x cheaper per query when the recovered candidate set is large. "
            "BETWEEN is approximate: it may falsely report 'hot' for subsets "
            "whose lex span contains non-candidate DB values (matches a real "
            "row that isn't in our candidate set), causing wasted recursion. "
            "Bisection still converges to the correct leaf set because leaves "
            "narrow to single candidates and lex range degenerates to equality. "
            "Recommended for large recovered sets with mostly-contiguous gaps."
        ),
    )
    return parser
