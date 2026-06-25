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

import os
import re
import shlex
import subprocess
import uuid
from argparse import Namespace
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from rls_artifact.paths import find_project_root


@dataclass(frozen=True)
class Claim:
    claim_id: str
    title: str
    runner: str
    outputs: tuple[str, ...]
    human_check: str
    samezone_experiment: str | None = None


@dataclass(frozen=True)
class ClaimCommand:
    label: str
    run_id_suffix: str
    claims: tuple[Claim, ...]
    command: list[str]


ConfigOverride = tuple[str, str]


class ConfigOverrideError(ValueError):
    """Raised when claim config overrides cannot be applied safely."""


CLAIMS: tuple[Claim, ...] = (
    Claim(
        claim_id="C-R1",
        title="Equality-probe latency separation",
        runner="orchestration/run_samezone_exps.sh --experiments 1",
        outputs=("results/existence/existence_kde_figure.tex",),
        human_check="Open the Figure 2 TeX/PGF output and confirm the three latency classes are separated.",
        samezone_experiment="1",
    ),
    Claim(
        claim_id="C-R2",
        title="Range-query timing separation",
        runner="orchestration/run_samezone_exps.sh --experiments 2",
        outputs=("results/range/existence_range_kde_figure.tex",),
        human_check="Open the range-query TeX/PGF output and confirm the timing separation remains visible.",
        samezone_experiment="2",
    ),
    Claim(
        claim_id="C-R3",
        title="Oracle accuracy under background load",
        runner="orchestration/run_samezone_exps.sh --experiments 3",
        outputs=("results/table1/table1.{png,pdf,pgf}",),
        human_check="Inspect Table 1 and compare join/inline accuracy across CPU-load rows.",
        samezone_experiment="3",
    ),
    Claim(
        claim_id="C-R4",
        title="Cross-region oracle accuracy",
        runner="orchestration/run_crosszone_exps.sh",
        outputs=("results/table2/table2.{png,pdf,pgf}",),
        human_check="Inspect Table 2 and compare cross-region accuracy against the same-zone baseline.",
    ),
    Claim(
        claim_id="C-R5",
        title="TDE timing-gap microbenchmark",
        runner="orchestration/run_tde_exps.sh",
        outputs=(
            "results/existence/existence_tde_figure.tex",
            "results/range/existence_range_tde_figure.tex",
        ),
        human_check="Open the TDE figures next to C-R1/C-R2 and compare the timing-gap shape visually.",
    ),
    Claim(
        claim_id="C-R6",
        title="Single-attribute reconstruction",
        runner="orchestration/run_samezone_exps.sh --experiments 6",
        outputs=("results/table3/table3.tex",),
        human_check="Inspect Table 3 for binary-search vs. linear reconstruction behavior.",
        samezone_experiment="6",
    ),
    Claim(
        claim_id="C-R7",
        title="Tuple-extension reconstruction",
        runner="orchestration/run_samezone_exps.sh --experiments 7",
        outputs=("results/table4/table4.tex",),
        human_check="Inspect Table 4 for tuple-extension ordering and worker-count effects.",
        samezone_experiment="7",
    ),
    Claim(
        claim_id="C-R8",
        title="Join-policy mitigations",
        runner="orchestration/run_samezone_exps.sh --experiments 8",
        outputs=("results/table5/table5.{png,pdf,pgf}",),
        human_check="Inspect Table 5 and confirm the mitigation cells close or reduce the channel.",
        samezone_experiment="8",
    ),
    Claim(
        claim_id="C-R9",
        title="Database physical-size measurement",
        runner="orchestration/run_samezone_exps.sh --experiments 9",
        outputs=("results/dbsize/summary.txt", "results/dbsize/db_sizes.json"),
        human_check="Read summary.txt and db_sizes.json for the measured PostgreSQL size breakdown.",
        samezone_experiment="9",
    ),
)
CLAIMS_BY_ID = {claim.claim_id: claim for claim in CLAIMS}
DEFAULT_CONFIG_BY_RUNNER = {
    "same-zone": "orchestration/config/shared_config.yml",
    "tde": "orchestration/config/tde_config.yml",
    "cross-zone": "orchestration/config/crosszone_config.yml",
}


def run_claims_command(args: Namespace) -> int:
    if args.claims_command == "list":
        return list_claims()
    if args.claims_command == "inspect":
        return inspect_claim(args.claim)
    if args.claims_command == "run":
        return run_claim(
            claim_refs=args.claims,
            dry_run=args.dry_run,
            config=args.config,
            config_overrides=getattr(args, "config_overrides", None),
            run_id=args.run_id,
            machines=args.machines,
        )
    raise AssertionError(f"unhandled claims command: {args.claims_command}")


def list_claims() -> int:
    table = Table(title="RLS Artifact Claims", box=box.ROUNDED, show_lines=False)
    table.add_column("Claim", style="bold", no_wrap=True)
    table.add_column("Title")
    table.add_column("Runner", no_wrap=True)
    table.add_column("Published Output", overflow="fold")

    for claim in CLAIMS:
        table.add_row(
            claim.claim_id,
            claim.title,
            claim.runner,
            "\n".join(claim.outputs),
        )
    Console(highlight=False, width=140).print(table)
    return 0


def inspect_claim(claim_ref: str) -> int:
    claim = _resolve_claim(claim_ref)
    if claim is None:
        valid = ", ".join(claim.claim_id for claim in CLAIMS)
        Console().print(f"[red]Unknown claim:[/red] {claim_ref}. Use one of: {valid}")
        return 2

    command = _claim_command(claim)
    rows = Table.grid(padding=(0, 1))
    rows.add_column(style="bold")
    rows.add_column()
    rows.add_row("Claim", f"{claim.claim_id} - {claim.title}")
    rows.add_row("Run", f"unfilter-rls claims run {claim.claim_id}")
    rows.add_row("Underlying", _format_command(command))
    rows.add_row("Manifest", "results/runs/<RUN_ID>/manifest.yml")
    rows.add_row("Inspect", "unfilter-rls results inspect <RUN_ID>")
    rows.add_row("Human check", claim.human_check)

    output_table = Table(box=box.SIMPLE, show_header=False)
    output_table.add_column("Published output", overflow="fold")
    for output in claim.outputs:
        output_table.add_row(output)

    Console(highlight=False, width=110).print(Panel(rows, title="Claim", box=box.ROUNDED))
    Console(highlight=False, width=110).print(Panel(output_table, title="Published Outputs", box=box.ROUNDED))
    return 0


def run_claim(
    *,
    claim_refs: Sequence[str],
    dry_run: bool,
    config: str | None,
    config_overrides: Sequence[Sequence[str]] | None,
    run_id: str | None,
    machines: str | None,
) -> int:
    claims, error = _resolve_claims(claim_refs)
    if error is not None:
        Console().print(error)
        return 2

    root = find_project_root()
    if root is None:
        Console().print("[red]Could not find the RLS project root.[/red]")
        return 2

    commands = _claim_commands(claims, machines=machines)
    if config is not None and len(commands) > 1:
        Console().print(
            "[red]--config is ambiguous for claims that span multiple runners.[/red] "
            "Run those claim groups separately, or omit --config to use each runner's default config."
        )
        return 2
    try:
        overrides = _normalize_config_overrides(config_overrides)
    except ConfigOverrideError as exc:
        Console().print(f"[red]{exc}[/red]")
        return 2
    if overrides and len(commands) > 1:
        Console().print(
            "[red]--set is ambiguous for claims that span multiple runners.[/red] "
            "Run those claim groups separately, or provide overrides only for one runner at a time."
        )
        return 2

    effective_config = config
    config_override_base: Path | None = None
    generated_config: Path | None = None
    if overrides:
        try:
            config_override_base = _resolve_override_base_config(root, config, commands[0])
            if dry_run:
                effective_config = str(config_override_base)
            else:
                generated_config = _materialize_config_overrides(
                    root=root,
                    base_config=config_override_base,
                    overrides=overrides,
                    run_id=run_id,
                )
                effective_config = str(generated_config)
        except ConfigOverrideError as exc:
            Console().print(f"[red]{exc}[/red]")
            return 2

    _print_run_plan(
        claims,
        commands,
        root,
        config=effective_config,
        config_overrides=overrides,
        config_override_base=config_override_base,
        generated_config=generated_config,
        run_id=run_id,
        machines=machines,
        dry_run=dry_run,
    )
    if dry_run:
        return 0

    multiple_commands = len(commands) > 1
    for command in commands:
        env = os.environ.copy()
        if effective_config is not None:
            env["CONFIG"] = effective_config
        command_run_id = _command_run_id(run_id, command, multiple_commands=multiple_commands)
        if command_run_id is not None:
            env["RUN_ID"] = command_run_id
        result = subprocess.run(command.command, cwd=root, env=env, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


def _resolve_claims(refs: Sequence[str]) -> tuple[tuple[Claim, ...], str | None]:
    tokens = " ".join(refs).replace(",", " ").split()
    if not tokens:
        return (), "[red]No claims were provided.[/red]"

    selected: set[str] = set()
    unknown: list[str] = []
    for token in tokens:
        claim = _resolve_claim(token)
        if claim is None:
            unknown.append(token)
        else:
            selected.add(claim.claim_id)

    if unknown:
        valid = ", ".join(claim.claim_id for claim in CLAIMS)
        return (), f"[red]Unknown claim(s):[/red] {', '.join(unknown)}. Use one or more of: {valid}"

    return tuple(claim for claim in CLAIMS if claim.claim_id in selected), None


def _resolve_claim(ref: str) -> Claim | None:
    normalized = ref.strip().upper()
    if normalized in CLAIMS_BY_ID:
        return CLAIMS_BY_ID[normalized]
    if normalized.startswith("CR") and normalized[2:].isdigit():
        normalized = f"C-R{normalized[2:]}"
    elif normalized.startswith("C-R") and normalized[3:].isdigit():
        pass
    elif normalized.isdigit():
        normalized = f"C-R{normalized}"
    return CLAIMS_BY_ID.get(normalized)


def _claim_command(claim: Claim, *, machines: str | None = None) -> list[str]:
    if claim.claim_id == "C-R4":
        command = ["bash", "orchestration/run_crosszone_exps.sh"]
    elif claim.claim_id == "C-R5":
        command = ["bash", "orchestration/run_tde_exps.sh"]
    else:
        if claim.samezone_experiment is None:
            raise AssertionError(f"claim {claim.claim_id} has no runner mapping")
        command = ["bash", "orchestration/run_samezone_exps.sh", "--experiments", claim.samezone_experiment]
    if machines is not None:
        command.extend(["--machines", machines])
    return command


def _claim_commands(claims: tuple[Claim, ...], *, machines: str | None = None) -> tuple[ClaimCommand, ...]:
    commands: list[ClaimCommand] = []

    samezone_claims = tuple(claim for claim in claims if claim.samezone_experiment is not None)
    if samezone_claims:
        experiments = ",".join(
            claim.samezone_experiment
            for claim in samezone_claims
            if claim.samezone_experiment is not None
        )
        command = ["bash", "orchestration/run_samezone_exps.sh", "--experiments", experiments]
        if machines is not None:
            command.extend(["--machines", machines])
        commands.append(
            ClaimCommand(
                label="same-zone",
                run_id_suffix="samezone",
                claims=samezone_claims,
                command=command,
            )
        )

    cr5 = CLAIMS_BY_ID["C-R5"]
    if cr5 in claims:
        command = _claim_command(cr5, machines=machines)
        commands.append(
            ClaimCommand(
                label="tde",
                run_id_suffix="tde",
                claims=(cr5,),
                command=command,
            )
        )

    cr4 = CLAIMS_BY_ID["C-R4"]
    if cr4 in claims:
        command = _claim_command(cr4, machines=machines)
        commands.append(
            ClaimCommand(
                label="cross-zone",
                run_id_suffix="crosszone",
                claims=(cr4,),
                command=command,
            )
        )

    return tuple(commands)


def _normalize_config_overrides(raw_overrides: Sequence[Sequence[str]] | None) -> tuple[ConfigOverride, ...]:
    if not raw_overrides:
        return ()

    normalized: list[ConfigOverride] = []
    for override in raw_overrides:
        if len(override) != 2:
            raise ConfigOverrideError("--set requires exactly KEY VALUE.")
        key, value = override
        normalized.append((key, value))
    return tuple(normalized)


def _resolve_override_base_config(root: Path, config: str | None, command: ClaimCommand) -> Path:
    if config is not None:
        candidate = Path(config)
    else:
        default_config = DEFAULT_CONFIG_BY_RUNNER.get(command.label)
        if default_config is None:
            raise ConfigOverrideError(f"No default config is known for runner {command.label}.")
        candidate = Path(default_config)

    if not candidate.is_absolute():
        candidate = root / candidate
    if not candidate.is_file():
        raise ConfigOverrideError(f"Config not found: {candidate}")
    return candidate.resolve()


def _materialize_config_overrides(
    *,
    root: Path,
    base_config: Path,
    overrides: tuple[ConfigOverride, ...],
    run_id: str | None,
) -> Path:
    try:
        with base_config.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except OSError as exc:
        raise ConfigOverrideError(f"Could not read config {base_config}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigOverrideError(f"Could not parse config {base_config}: {exc}") from exc

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigOverrideError(f"Config root must be a mapping: {base_config}")

    for key, value in overrides:
        _apply_config_override(data, key, value)

    output_dir = root / "results" / "config-overrides"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id or "auto").strip("-") or "auto"
    output_path = output_dir / f"{safe_run_id}-{base_config.stem}-{uuid.uuid4().hex[:8]}.yml"

    try:
        with output_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False)
    except OSError as exc:
        raise ConfigOverrideError(f"Could not write generated config {output_path}: {exc}") from exc

    return output_path


def _apply_config_override(config_data: dict[str, Any], key: str, raw_value: str) -> None:
    parts = _config_override_parts(key)
    cursor: dict[str, Any] = config_data
    for index, part in enumerate(parts[:-1], start=1):
        child = cursor.get(part)
        if child is None:
            child = {}
            cursor[part] = child
        if not isinstance(child, dict):
            prefix = ".".join(parts[:index])
            raise ConfigOverrideError(f"Cannot set {key}: {prefix} is not a mapping.")
        cursor = child
    cursor[parts[-1]] = _parse_config_override_value(raw_value)


def _config_override_parts(key: str) -> tuple[str, ...]:
    normalized = key[1:] if key.startswith(".") else key
    parts = tuple(normalized.split("."))
    if not parts or any(part == "" for part in parts):
        raise ConfigOverrideError(f"Invalid config override key: {key}")
    return parts


def _parse_config_override_value(raw_value: str) -> Any:
    if raw_value == "":
        return ""
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        raise ConfigOverrideError(f"Invalid YAML value for --set: {raw_value}") from exc
    if isinstance(value, (dict, list)):
        raise ConfigOverrideError("--set only supports scalar YAML values.")
    return value


def _format_config_overrides(overrides: tuple[ConfigOverride, ...]) -> str:
    return "\n".join(f"{key} = {value}" for key, value in overrides)


def _format_command(command: Sequence[str]) -> str:
    return shlex.join(command)


def _command_run_id(
    run_id: str | None,
    command: ClaimCommand,
    *,
    multiple_commands: bool,
) -> str | None:
    if run_id is None:
        return None
    if not multiple_commands:
        return run_id
    return f"{run_id}-{command.run_id_suffix}"


def _print_run_plan(
    claims: tuple[Claim, ...],
    commands: tuple[ClaimCommand, ...],
    root: Path,
    *,
    config: str | None,
    config_overrides: tuple[ConfigOverride, ...],
    config_override_base: Path | None,
    generated_config: Path | None,
    run_id: str | None,
    machines: str | None,
    dry_run: bool,
) -> None:
    rows = Table.grid(padding=(0, 1))
    rows.add_column(style="bold bright_cyan")
    rows.add_column(style="white")
    rows.add_row("Claims", ", ".join(claim.claim_id for claim in claims))
    rows.add_row("Project", str(root))
    if len(commands) == 1:
        rows.add_row("Command", _format_command(commands[0].command))
        if run_id is not None:
            rows.add_row("RUN_ID", run_id)
    elif run_id is not None:
        rows.add_row("RUN_ID", f"{run_id}-<runner>")
    if config is not None:
        rows.add_row("CONFIG", config)
    if config_override_base is not None:
        rows.add_row("Config base", str(config_override_base))
    if config_overrides:
        rows.add_row("Overrides", _format_config_overrides(config_overrides))
        if generated_config is not None:
            rows.add_row("Generated CONFIG", str(generated_config))
        elif dry_run:
            rows.add_row("Generated CONFIG", "not written (dry run)")
    if machines is not None:
        rows.add_row("Machines", machines)
    modes: list[str] = []
    if machines is not None:
        modes.append("attached BYO machines")
    if dry_run:
        modes.append("dry run")
    if modes:
        rows.add_row("Mode", ", ".join(modes))

    Console(highlight=False).print(
        Panel(
            rows,
            title=Text("Claim Run", style="bold bright_cyan"),
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )

    if len(commands) > 1:
        command_table = Table(
            title=Text("Command Sequence", style="bold bright_magenta"),
            box=box.ROUNDED,
            border_style="bright_magenta",
            header_style="bold bright_cyan",
        )
        command_table.add_column("#", justify="right", no_wrap=True, style="bold bright_cyan")
        command_table.add_column("Claims", no_wrap=True, style="bold white")
        command_table.add_column("Command", overflow="fold", style="white")
        command_table.add_column("RUN_ID", no_wrap=True, style="bright_green")
        for idx, command in enumerate(commands, start=1):
            command_table.add_row(
                str(idx),
                ", ".join(claim.claim_id for claim in command.claims),
                _format_command(command.command),
                _command_run_id(run_id, command, multiple_commands=True) or "auto",
            )
        Console(highlight=False, width=140).print(command_table)
