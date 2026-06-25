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
import shlex
import subprocess

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from rls_artifact.paths import find_project_root
from rls_artifact.yaml_io import dump_yaml, load_yaml


def run_results_command(args: Namespace) -> int:
    if args.results_command == "list":
        return list_results()
    if args.results_command == "inspect":
        return inspect_result(args.path, raw=args.raw)
    if args.results_command == "cleanup-vms":
        return cleanup_vms(
            args.run_id,
            all_runs=args.all_runs,
            dry_run=args.dry_run,
        )
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

    table = Table(
        title=Text("RLS Artifact Runs", style="bold bright_cyan"),
        box=box.ROUNDED,
        border_style="bright_cyan",
        header_style="bold bright_cyan",
        show_lines=False,
    )
    table.add_column("Run ID", style="bold bright_green", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Claims", style="white", no_wrap=True)
    table.add_column("Machines", justify="center", no_wrap=True)
    table.add_column("Started", style="dim")
    table.add_column("Finished", style="dim")

    for manifest in manifests:
        data = _manifest_dict(manifest)
        run_id = _string_field(data, "run_id", manifest.parent.name)
        table.add_row(
            run_id,
            _status_text(_string_field(data, "status", "unknown")),
            _format_claim_ids(_string_list_field(data, "claims")),
            _machine_descriptor_status(root, data, run_id),
            _string_field(data, "started_at", ""),
            _string_field(data, "finished_at", ""),
        )
    console = Console(highlight=False, width=140)
    console.print(table)
    console.print(
        Text(
            "Run `unfilter-rls results inspect [RUN_ID]` to see more information about a specific run.",
            style="dim",
        )
    )
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


def cleanup_vms(run_id: str | None, *, all_runs: bool, dry_run: bool) -> int:
    root = find_project_root()
    if root is None:
        Console().print("[red]Could not find the RLS project root.[/red]")
        return 2

    if all_runs and run_id is not None:
        Console().print("[red]Pass either a RUN_ID or --all, not both.[/red]")
        return 2
    if not all_runs and run_id is None:
        Console().print("[red]cleanup-vms requires a RUN_ID or --all.[/red]")
        return 2

    script = root / "orchestration" / "provision" / "cleanup_vms.sh"
    if not script.is_file():
        Console().print(f"[red]Cleanup script not found:[/red] {_display_path(root, script)}")
        return 2

    if all_runs:
        return _cleanup_all_vms(root, dry_run=dry_run)
    return _cleanup_one_run(root, run_id or "", dry_run=dry_run)


def _print_raw_manifest(root: Path, manifest: Path, data: object) -> int:
    text = dump_yaml(data)
    syntax = Syntax(text, "yaml", word_wrap=True)
    title = _display_path(root, manifest)
    Console(highlight=False).print(Panel(syntax, title=title, box=box.ROUNDED))
    return 0


def _cleanup_all_vms(root: Path, *, dry_run: bool) -> int:
    descriptors = sorted((root / "results" / "machines").glob("*.yml"))
    commands = [
        _cleanup_command(root, "--machines", _display_path(root, descriptor))
        for descriptor in descriptors
    ]

    if descriptors:
        Console().print(f"Found {len(descriptors)} machine descriptor(s) under results/machines/.")
    else:
        Console().print("No machine descriptors found under results/machines/.")
    return _run_cleanup_commands(root, commands, dry_run=dry_run)


def _cleanup_one_run(root: Path, run_ref: str, *, dry_run: bool) -> int:
    command = _cleanup_command_for_run(root, run_ref)
    if command is None:
        Console().print(
            f"[red]Could not find cleanup metadata for:[/red] {run_ref}\n"
            "Expected a machine descriptor under results/machines/ or a run manifest with config metadata."
        )
        return 2
    return _run_cleanup_commands(root, [command], dry_run=dry_run)


def _cleanup_command_for_run(root: Path, run_ref: str) -> list[str] | None:
    manifest = _resolve_manifest(root, Path(run_ref))
    data = _manifest_dict(manifest) if manifest is not None else {}
    resolved_run_id = _string_field(
        data,
        "run_id",
        Path(run_ref).name if Path(run_ref).name else run_ref,
    )

    for machines in _machine_descriptor_paths(root, data, resolved_run_id):
        if machines.is_file():
            return _cleanup_command(root, "--machines", _display_path(root, machines))

    config = _string_field(data, "config", "")
    if config:
        return _cleanup_command(root, "--config", config, "--run-id", resolved_run_id)
    return None


def _manifest_machine_paths(root: Path, data: dict[str, object]) -> list[Path]:
    environment = data.get("environment")
    if not isinstance(environment, dict):
        return []
    machines = environment.get("MACHINES")
    if not isinstance(machines, str) or not machines:
        return []
    path = Path(machines)
    return [path if path.is_absolute() else root / path]


def _machine_descriptor_paths(
    root: Path,
    data: dict[str, object],
    run_id: str,
) -> list[Path]:
    paths = _manifest_machine_paths(root, data)
    paths.append(root / "results" / "machines" / f"{run_id}.yml")
    return _dedupe_paths(paths)


def _machine_descriptor_status(
    root: Path,
    data: dict[str, object],
    run_id: str,
) -> Text:
    exists = any(path.is_file() for path in _machine_descriptor_paths(root, data, run_id))
    return Text("yes", style="bold yellow") if exists else Text("no", style="dim")


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        key = path.resolve() if path.exists() else path
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _cleanup_command(root: Path, *args: str) -> list[str]:
    return [
        "bash",
        _display_path(root, root / "orchestration" / "provision" / "cleanup_vms.sh"),
        *args,
    ]


def _run_cleanup_commands(root: Path, commands: list[list[str]], *, dry_run: bool) -> int:
    console = Console(highlight=False, width=200)
    exit_code = 0
    for command in commands:
        console.print(f"[bold cyan]cleanup[/bold cyan] {shlex.join(command)}")
        if dry_run:
            continue
        completed = subprocess.run(command, cwd=root, check=False)
        if completed.returncode != 0 and exit_code == 0:
            exit_code = completed.returncode
    return exit_code


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


def _status_text(status: str) -> Text:
    normalized = status.lower()
    style = {
        "success": "bold green",
        "failed": "bold red",
        "running": "bold yellow",
    }.get(normalized, "dim")
    return Text(status, style=style)


def _format_claim_ids(claims: list[str]) -> str:
    shortened: list[str] = []
    for claim in claims:
        if claim.startswith("C-R") and claim[3:].isdigit():
            shortened.append(claim[3:])
        else:
            shortened.append(claim)
    return ", ".join(shortened)


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
