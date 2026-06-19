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

from argparse import Namespace
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from rls_artifact.paths import find_project_root
from rls_artifact.yaml_io import dump_yaml, load_yaml


def run_results_command(args: Namespace) -> int:
    if args.results_command == "list":
        return list_results()
    if args.results_command == "inspect":
        return inspect_result(args.path, raw=args.raw)
    raise AssertionError(f"unhandled results command: {args.results_command}")


def list_results() -> int:
    root = find_project_root()
    if root is None:
        Console().print("[red]Could not find the RLS project root.[/red]")
        return 2

    manifests = sorted((root / "results" / "runs").glob("*/manifest.yml"))
    if not manifests:
        Console().print("No run manifests found under results/runs/.")
        return 0

    table = Table(title="RLS Artifact Runs", box=box.ROUNDED, show_lines=False)
    table.add_column("Run ID", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Claims")
    table.add_column("Started")
    table.add_column("Finished")
    table.add_column("Manifest", overflow="fold")

    for manifest in manifests:
        data = _manifest_dict(manifest)
        table.add_row(
            _string_field(data, "run_id", manifest.parent.name),
            _string_field(data, "status", "unknown"),
            ", ".join(_string_list_field(data, "claims")),
            _string_field(data, "started_at", ""),
            _string_field(data, "finished_at", ""),
            manifest.relative_to(root).as_posix(),
        )
    Console(highlight=False, width=140).print(table)
    return 0


def inspect_result(path_arg: str, *, raw: bool) -> int:
    root = find_project_root()
    if root is None:
        Console().print("[red]Could not find the RLS project root.[/red]")
        return 2

    manifest = _resolve_manifest(root, Path(path_arg))
    if manifest is None:
        Console().print(f"[red]Could not find a manifest for:[/red] {path_arg}")
        return 2

    loaded = load_yaml(manifest)
    data = loaded if isinstance(loaded, dict) else {}
    if raw:
        return _print_raw_manifest(root, manifest, loaded)
    return _print_manifest_summary(root, manifest, data)


def _print_raw_manifest(root: Path, manifest: Path, data: object) -> int:
    text = dump_yaml(data)
    syntax = Syntax(text, "yaml", word_wrap=True)
    title = _display_path(root, manifest)
    Console(highlight=False).print(Panel(syntax, title=title, box=box.ROUNDED))
    return 0


def _print_manifest_summary(root: Path, manifest: Path, data: dict[str, object]) -> int:
    outputs = _string_list_field(data, "outputs")
    published_outputs = _string_list_field(data, "published_outputs")
    notes = _string_list_field(data, "notes")

    rows = Table.grid(padding=(0, 1))
    rows.add_column(style="bold")
    rows.add_column()
    rows.add_row("Run ID", _string_field(data, "run_id", manifest.parent.name))
    rows.add_row("Status", _string_field(data, "status", "unknown"))
    rows.add_row("Claims", ", ".join(_string_list_field(data, "claims")) or "<none>")
    rows.add_row("Runner", _string_field(data, "runner", ""))
    rows.add_row("Command", _string_field(data, "command", ""))
    rows.add_row("Config", _string_field(data, "config", ""))
    rows.add_row("Started", _string_field(data, "started_at", ""))
    rows.add_row("Finished", _string_field(data, "finished_at", ""))
    rows.add_row("Git", _git_summary(data))
    rows.add_row("Manifest", _display_path(root, manifest))

    console = Console(highlight=False, width=120)
    console.print(Panel(rows, title="Run Summary", box=box.ROUNDED))
    console.print(_paths_panel("Published Outputs", published_outputs))
    console.print(_paths_panel("Run Outputs", outputs))
    if notes:
        console.print(_paths_panel("Notes", notes))
    return 0


def _paths_panel(title: str, paths: list[str]) -> Panel:
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column(title, overflow="fold")
    if paths:
        for path in paths:
            table.add_row(path)
    else:
        table.add_row("<none>")
    return Panel(table, title=title, box=box.ROUNDED)


def _git_summary(data: dict[str, object]) -> str:
    commit = _string_field(data, "git_commit", "")
    dirty = data.get("git_dirty")
    if isinstance(dirty, bool):
        dirty_text = "dirty" if dirty else "clean"
    elif isinstance(dirty, str) and dirty:
        dirty_text = dirty
    else:
        dirty_text = "unknown"
    if not commit:
        return dirty_text
    return f"{commit} ({dirty_text})"


def _resolve_manifest(root: Path, path: Path) -> Path | None:
    candidates: list[Path] = []
    if path.is_absolute():
        candidates.append(path)
    else:
        candidates.extend((Path.cwd() / path, root / path))

    for candidate in candidates:
        if candidate.is_file() and candidate.suffix in (".yml", ".yaml"):
            return candidate
        if candidate.is_dir() and (candidate / "manifest.yml").is_file():
            return candidate / "manifest.yml"

    run_ids = _possible_run_ids(path)
    for run_id in run_ids:
        manifest = root / "results" / "runs" / run_id / "manifest.yml"
        if manifest.is_file():
            return manifest
    return None


def _possible_run_ids(path: Path) -> list[str]:
    names = [part for part in path.parts if part not in ("", ".")]
    candidates: list[str] = []
    if names:
        candidates.append(names[-1])
    for name in names:
        if name.startswith(("join-", "inline-")) and len(name) > len("join-"):
            candidates.append(name.split("-", 1)[1])
    return list(dict.fromkeys(candidates))


def _manifest_dict(path: Path) -> dict[str, object]:
    raw = load_yaml(path)
    return raw if isinstance(raw, dict) else {}


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _string_field(data: dict[str, object], key: str, default: str) -> str:
    value = data.get(key, default)
    return value if isinstance(value, str) else default


def _string_list_field(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
