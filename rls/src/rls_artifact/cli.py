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

from rls_artifact import __version__
from rls_artifact.claims import run_claims_command
from rls_artifact.doctor import run_doctor
from rls_artifact.results import run_results_command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unfilter-rls",
        description="Reviewer-facing helpers for the RLS artifact.",
    )
    parser.add_argument("--version", action="version", version=f"rls-artifact {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser(
        "doctor",
        help="Check local dependencies, package imports, GCP auth, and TeX tools.",
    )
    doctor.add_argument(
        "--skip-gcloud",
        action="store_true",
        help="Do not check Google Cloud CLI authentication/project state.",
    )
    doctor.add_argument(
        "--skip-tex",
        action="store_true",
        help="Do not check xelatex/latexmk availability.",
    )

    claims = subparsers.add_parser(
        "claims",
        help="List or run paper-claim reproduction workflows.",
    )
    claim_subparsers = claims.add_subparsers(dest="claims_command", required=True)
    claim_subparsers.add_parser("list", help="Show the artifact claim registry.")
    inspect_claim = claim_subparsers.add_parser(
        "inspect",
        help="Show focused metadata for one claim.",
    )
    inspect_claim.add_argument("claim", help="Claim id, e.g. C-R9, CR9, or 9.")
    run_claim = claim_subparsers.add_parser(
        "run",
        help="Run one claim through the existing artifact driver scripts.",
    )
    run_claim.add_argument(
        "claims",
        nargs="+",
        help="Claim id or comma-separated claim ids, e.g. C-R9, 9, or 1,2,3.",
    )
    run_claim.add_argument(
        "--config",
        help="Override CONFIG for the underlying runner.",
    )
    run_claim.add_argument(
        "--set",
        dest="config_overrides",
        action="append",
        nargs=2,
        metavar=("KEY", "VALUE"),
        help=(
            "Override a YAML config scalar for this run. Repeatable; "
            "for example: --set singleattr.reps 10."
        ),
    )
    run_claim.add_argument(
        "--run-id",
        help="Override RUN_ID for the underlying runner.",
    )
    run_claim.add_argument(
        "--machines",
        help=(
            "Use an existing machine descriptor instead of provisioning GCP VMs. "
            "The runner installs the artifact, runs in attached mode, and leaves the machines untouched."
        ),
    )
    run_claim.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the underlying command without executing it.",
    )

    results = subparsers.add_parser(
        "results",
        help="List or inspect run manifests.",
    )
    result_subparsers = results.add_subparsers(dest="results_command", required=True)
    result_subparsers.add_parser("list", help="List recorded artifact runs.")
    inspect_result = result_subparsers.add_parser(
        "inspect",
        help="Summarize a run manifest. Accepts a manifest, run directory, or result directory.",
    )
    inspect_result.add_argument("path", help="Manifest path, run id, or result directory.")
    inspect_result.add_argument(
        "--raw",
        action="store_true",
        help="Print the underlying manifest YAML.",
    )
    cleanup_vms = result_subparsers.add_parser(
        "cleanup-vms",
        help="Clean up GCP VMs left behind by an artifact run.",
    )
    cleanup_vms.add_argument(
        "run_id",
        nargs="?",
        help="Run ID to clean, e.g. all-12345678. Omit only with --all.",
    )
    cleanup_vms.add_argument(
        "--all",
        dest="all_runs",
        action="store_true",
        help="Clean every machine descriptor under this checkout's results/machines/.",
    )
    cleanup_vms.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cleanup commands without executing them.",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "doctor":
        return run_doctor(skip_gcloud=args.skip_gcloud, skip_tex=args.skip_tex)
    if args.command == "claims":
        return run_claims_command(args)
    if args.command == "results":
        return run_results_command(args)
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
