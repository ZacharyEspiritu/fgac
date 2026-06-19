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
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from rls_artifact import __version__
from rls_artifact.paths import find_project_root
from rls_artifact.yaml_io import dump_yaml, load_yaml


RESULT_ROOTS = (
    "existence",
    "range",
    "table1",
    "table2",
    "table3",
    "table4",
    "table5",
    "dbsize",
)
PUBLISHED_OUTPUTS_BY_CLAIM = {
    "C-R1": ("results/existence/existence_kde_figure.tex", "results/existence/existence_kde.pgf"),
    "C-R2": (
        "results/range/existence_range_kde_figure.tex",
        "results/range/existence_range_kde.pgf",
    ),
    "C-R3": ("results/table1/table1.png", "results/table1/table1.pdf", "results/table1/table1.pgf"),
    "C-R4": ("results/table2/table2.png", "results/table2/table2.pdf", "results/table2/table2.pgf"),
    "C-R5": (
        "results/existence/existence_tde_figure.tex",
        "results/existence/existence_tde_figure.pgf",
        "results/range/existence_range_tde_figure.tex",
        "results/range/existence_range_tde_figure.pgf",
    ),
    "C-R6": ("results/table3/table3.tex",),
    "C-R7": ("results/table4/table4.tex",),
    "C-R8": ("results/table5/table5.png", "results/table5/table5.pdf", "results/table5/table5.pgf"),
    "C-R9": ("results/dbsize/summary.txt", "results/dbsize/db_sizes.json"),
}
INCLUDED_SUFFIXES = {".csv", ".json", ".md", ".pdf", ".pgf", ".png", ".tex", ".txt", ".yml"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Internal manifest helper for RLS run scripts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create a running manifest.")
    start.add_argument("--path", required=True, type=Path)
    start.add_argument("--run-id", required=True)
    start.add_argument("--runner", required=True)
    start.add_argument("--config", required=True)
    start.add_argument("--command-line", required=True)
    start.add_argument("--claim", action="append", default=[])
    start.add_argument("--env", action="append", default=[])

    finish = subparsers.add_parser("finish", help="Mark a manifest success/failed.")
    finish.add_argument("--path", required=True, type=Path)
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", required=True, choices=("success", "failed"))
    finish.add_argument("--note", action="append", default=[])

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "start":
        write_start_manifest(
            path=args.path,
            run_id=args.run_id,
            runner=args.runner,
            config=args.config,
            command_line=args.command_line,
            claims=tuple(args.claim),
            env_entries=tuple(args.env),
        )
        return 0
    if args.command == "finish":
        finish_manifest(
            path=args.path,
            run_id=args.run_id,
            status=args.status,
            notes=tuple(args.note),
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


def write_start_manifest(
    *,
    path: Path,
    run_id: str,
    runner: str,
    config: str,
    command_line: str,
    claims: Sequence[str],
    env_entries: Sequence[str],
) -> None:
    root = find_project_root() or Path.cwd()
    data: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "claims": list(claims),
        "runner": runner,
        "command": command_line,
        "config": config,
        "project_version": __version__,
        "project_root": str(root),
        "git_commit": _git_commit(root),
        "git_dirty": _git_dirty(root),
        "started_at": _now(),
        "finished_at": None,
        "environment": _parse_env_entries(env_entries),
        "outputs": [],
        "published_outputs": [],
        "notes": [],
    }
    _write_manifest(path, data)


def finish_manifest(*, path: Path, run_id: str, status: str, notes: Sequence[str]) -> None:
    raw = load_yaml(path) if path.exists() else {}
    data = raw if isinstance(raw, dict) else {}
    root = find_project_root() or Path.cwd()
    existing_notes = data.get("notes", [])
    if not isinstance(existing_notes, list):
        existing_notes = []
    claims = [claim for claim in data.get("claims", []) if isinstance(claim, str)]
    data.update(
        {
            "status": status,
            "finished_at": _now(),
            "outputs": _discover_run_outputs(root, run_id),
            "published_outputs": _existing_paths(root, _published_outputs_for_claims(claims)),
            "notes": [*existing_notes, *notes],
        }
    )
    _write_manifest(path, data)


def _write_manifest(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def _discover_run_outputs(root: Path, run_id: str) -> list[str]:
    candidates: list[Path] = []
    for result_root in RESULT_ROOTS:
        base = root / "results" / result_root
        candidates.extend(
            path
            for path in (
                base / run_id,
                base / f"join-{run_id}",
                base / f"inline-{run_id}",
            )
            if path.exists()
        )

    outputs: list[str] = []
    for candidate in candidates:
        if candidate.is_file():
            outputs.append(_relative_to_root(root, candidate))
            continue
        for path in sorted(candidate.rglob("*")):
            if path.is_file() and path.suffix in INCLUDED_SUFFIXES:
                outputs.append(_relative_to_root(root, path))
    return sorted(dict.fromkeys(outputs))


def _existing_paths(root: Path, paths: Sequence[str]) -> list[str]:
    return [path for path in paths if (root / path).is_file()]


def _published_outputs_for_claims(claims: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for claim in claims:
        paths.extend(PUBLISHED_OUTPUTS_BY_CLAIM.get(claim, ()))
    return list(dict.fromkeys(paths))


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _parse_env_entries(entries: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        key, sep, value = entry.partition("=")
        if sep and key:
            result[key] = value
    return result


def _git_commit(root: Path) -> str:
    return _run_git(root, "rev-parse", "HEAD")


def _git_dirty(root: Path) -> bool | str:
    status = _run_git(root, "status", "--porcelain")
    if status == "":
        return False
    if status == "unknown":
        return "unknown"
    return True


def _run_git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
