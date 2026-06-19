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
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from rich import box
from rich.console import Console
from rich.table import Table

from util.paths import artifact_root


VALID_BACKENDS = ("opensearch", "elasticsearch")
VALID_DATASETS = ("1", "10", "100", "1000")
VALID_PREFIX_QUERY_MODES = ("match_phrase_prefix", "span_prefix")
VALID_EXACT_STRATEGIES = ("eager", "optimized")
VALID_RECONSTRUCTION_SOURCES = (
    "recovered",
    "indexed",
    "recovered-indexed",
    "missing",
    "extra",
)
VALID_RECONSTRUCTION_TRAVERSALS = ("unitigs", "euler")


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass
class CheckReport:
    verbose: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    def ok(self, message: str) -> None:
        self.checks.append(check_from_message(message, "OK"))

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.checks.append(check_from_message(message, "WARN"))

    def fail(self, message: str) -> None:
        self.errors.append(message)
        self.checks.append(check_from_message(message, "FAIL"))

    def print_table(self) -> None:
        checks = self.checks if self.verbose else [
            check for check in self.checks if check.status != "OK"
        ]
        if not checks:
            print(f"doctor passed with {len(self.warnings)} warning(s)")
            return

        table = Table(title="DLS Artifact Doctor", box=box.ROUNDED, show_lines=False)
        table.add_column("Check", style="bold")
        table.add_column("Status", justify="center")
        table.add_column("Detail", overflow="fold")

        for check in checks:
            style = {
                "OK": "green",
                "WARN": "yellow",
                "FAIL": "red",
            }[check.status]
            table.add_row(
                check.name,
                f"[{style}]{check.status}[/{style}]",
                check.detail,
            )

        Console(highlight=False).print(table)


def check_from_message(message: str, status: str) -> Check:
    name, detail = split_check_message(message)
    return Check(name, status, detail)


def split_check_message(message: str) -> tuple[str, str]:
    if message.startswith("found ") and ": " in message:
        name, detail = message.removeprefix("found ").split(": ", 1)
        return f"Tool {name}", detail
    if message.startswith("found "):
        detail = message.removeprefix("found ")
        return f"File {Path(detail).name}", detail
    if message.startswith("validated ") and " in " in message:
        detail, name = message.split(" in ", 1)
        return f"Corpus {name}", detail
    if message.startswith("doctor Python version "):
        return "Python", message.removeprefix("doctor Python version ")
    if message.startswith("doctor is running under Python "):
        return "Python", message.removeprefix("doctor is running under ")
    if message.startswith("uv "):
        return "Tool uv", message
    if message == "Python dependencies import successfully":
        return "Python dependencies", "available"
    if message.startswith("output directory is writable: "):
        return "Output directory", message.removeprefix("output directory is writable: ")
    if message == "attack config is internally consistent":
        return "Attack config", "internally consistent"
    if message == "reconstruction config is internally consistent":
        return "Reconstruction config", "internally consistent"
    if message.startswith("backend startup is not required"):
        return "Backend startup", message
    if message in {"Docker Compose v2 is available", "Docker daemon is available"}:
        return message.removesuffix(" is available"), "available"
    if message.startswith("Docker Compose config is valid for "):
        return "Docker Compose config", message.removeprefix(
            "Docker Compose config is valid for "
        )
    if " is required but --skip-docker-start is set and " in message:
        name, detail = message.split(" is required but ", 1)
        return f"{name} reachability", f"required but {detail}"
    if " config: " in message:
        name, detail = message.split(": ", 1)
        return name, detail
    if " is reachable at " in message:
        name, detail = message.split(" is reachable at ", 1)
        return f"{name} reachability", detail
    if ": " in message:
        name, detail = message.split(": ", 1)
        return name, detail
    return message, ""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BackendConfig:
    name: str
    label: str
    compose_file: Path
    service: str
    host: str
    port: int
    scheme: str
    verify_certs: bool


class ConfigError(RuntimeError):
    pass


def run_command(args: Sequence[str], *, timeout: float = 15.0) -> CommandResult:
    try:
        completed = subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"command not found: {args[0]}")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(124, stdout, stderr or f"timed out after {timeout}s")
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def require_command(report: CheckReport, name: str) -> Optional[str]:
    path = shutil.which(name)
    if path is None:
        report.fail(f"{name} is missing; run dls/setup.sh first")
        return None
    report.ok(f"found {name}: {path}")
    return path


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"expected boolean value, got {value!r}")


def positive_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive, got {parsed}")
    return parsed


def nonnegative_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative, got {parsed}")
    return parsed


def normalize_backend(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "elastic":
        normalized = "elasticsearch"
    if normalized not in VALID_BACKENDS:
        raise ValueError("--backend must be opensearch, elasticsearch, or elastic")
    return normalized


def parse_backends(values: Sequence[str]) -> list[str]:
    if not values:
        return list(VALID_BACKENDS)
    result: list[str] = []
    for value in values:
        backend = normalize_backend(value)
        if backend not in result:
            result.append(backend)
    return result


def parse_dataset_spec(spec: str) -> list[str]:
    result: list[str] = []
    normalized = spec.lower().replace(",", " ")
    for item in normalized.split():
        dataset = item.removeprefix("d")
        if dataset not in VALID_DATASETS:
            raise ValueError("--datasets only supports d1,d10,d100,d1000")
        if dataset not in result:
            result.append(dataset)
    if not result:
        raise ValueError("--datasets did not contain any datasets")
    return result


def absolute_path(root_dir: Path, path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return root_dir / candidate


def stats_file_for(output_dir: Path, backend: str, dataset: str, trial: int) -> Path:
    return output_dir / "stats" / f"{backend}-d{dataset}-r{trial}_stats.json"


def backend_required(
    output_dir: Path,
    backend: str,
    datasets: Sequence[str],
    trials: int,
    *,
    destructive_rerun: bool,
) -> bool:
    if destructive_rerun:
        return True
    for dataset in datasets:
        for trial in range(1, trials + 1):
            if not stats_file_for(output_dir, backend, dataset, trial).is_file():
                return True
    return False


class YqConfig:
    def __init__(self, config_file: Path) -> None:
        self.config_file = config_file

    def value(self, expression: str) -> str:
        result = run_command(
            ["yq", "-r", f"{expression} | tostring", str(self.config_file)]
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ConfigError(f"failed to read {expression}: {detail}")
        value = result.stdout.strip()
        if not value or value == "null":
            raise ConfigError(f"missing {expression} in {self.config_file}")
        return value


def check_required_files(
    report: CheckReport,
    *,
    root_dir: Path,
    config_file: Path,
    datasets: Sequence[str],
) -> None:
    source_dir = root_dir / "src"
    required = [
        root_dir / "setup.sh",
        root_dir / "run.sh",
        root_dir / "pyproject.toml",
        root_dir / "uv.lock",
        root_dir / "requirements.txt",
        config_file,
        source_dir / "cli.py",
        source_dir / "util" / "preflight.py",
        source_dir / "util" / "check_python_dependencies.py",
        source_dir / "util" / "wait_for_search_backend.py",
        source_dir / "util" / "build_enumerator_command.py",
        source_dir / "enumerator" / "__main__.py",
        source_dir / "enumerator" / "runner.py",
        source_dir / "enumerator" / "ngram_enumerator.py",
        source_dir / "latex" / "render_table.py",
        source_dir / "debruijn" / "reconstruct_debruijn.py",
        source_dir / "dataset" / "util" / "parse_hf_dataset.py",
    ]
    for dataset in datasets:
        required.append(root_dir / "dataset" / f"enron_d{dataset}.jsonl")

    for path in required:
        if not path.is_file():
            report.fail(f"missing required file: {path}")
        else:
            report.ok(f"found {path}")


def check_corpus_files(
    report: CheckReport,
    *,
    root_dir: Path,
    datasets: Sequence[str],
) -> None:
    for dataset in datasets:
        path = root_dir / "dataset" / f"enron_d{dataset}.jsonl"
        if not path.is_file():
            continue
        rows = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    rows += 1
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as exc:
                        report.fail(f"{path}:{line_number}: invalid JSON: {exc}")
                        break
                    if not isinstance(obj.get("text"), str) or not obj["text"]:
                        report.fail(
                            f"{path}:{line_number}: expected non-empty string text field"
                        )
                        break
        except OSError as exc:
            report.fail(f"cannot read corpus file {path}: {exc}")
            continue
        if rows == 0:
            report.fail(f"corpus file is empty: {path}")
        else:
            report.ok(f"validated {rows} JSONL rows in {path.name}")


def check_python_environment(
    report: CheckReport,
    *,
    root_dir: Path,
    venv_dir: Path,
) -> None:
    if sys.version_info < (3, 10):
        report.fail(
            "doctor is running under Python "
            f"{sys.version_info.major}.{sys.version_info.minor}; Python 3.10+ is required"
        )
    else:
        report.ok(
            "doctor Python version "
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )

    uv = require_command(report, "uv")
    if uv is not None:
        result = run_command([uv, "--version"])
        if result.returncode != 0:
            report.fail(f"uv is present but not runnable: {result.stderr.strip()}")
        else:
            report.ok(result.stdout.strip())

    python = venv_dir / "bin" / "python3"
    if not python.exists():
        python = venv_dir / "bin" / "python"
    if not python.is_file():
        report.fail(
            f"missing project Python at {venv_dir}; run {root_dir / 'setup.sh'} first "
            "or pass --venv PATH"
        )
        return
    if not os.access(python, os.X_OK):
        report.fail(f"project Python is not executable: {python}")
        return

    result = run_command(
        [
            str(python),
            str(root_dir / "src" / "util" / "check_python_dependencies.py"),
        ]
    )
    if result.returncode != 0:
        detail = result.stdout.strip() or result.stderr.strip()
        report.fail(
            "Python dependencies are missing from the project environment: "
            f"{detail}; rerun {root_dir / 'setup.sh'}"
        )
    else:
        report.ok("Python dependencies import successfully")


def check_output_dirs(report: CheckReport, output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        report.fail(f"output path exists but is not a directory: {output_dir}")
        return
    for directory in [
        output_dir,
        output_dir / "stats",
        output_dir / "logs",
        output_dir / "reconstructions",
    ]:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".preflight-", delete=True):
                pass
        except OSError as exc:
            report.fail(f"output directory is not writable: {directory}: {exc}")
        else:
            report.ok(f"output directory is writable: {directory}")


def validate_choice(
    report: CheckReport,
    value: str,
    *,
    name: str,
    choices: Sequence[str],
) -> None:
    if value not in choices:
        report.fail(f"{name} must be one of {', '.join(choices)}; got {value!r}")


def check_attack_config(report: CheckReport, config: YqConfig) -> None:
    errors_before = len(report.errors)
    try:
        text_field_type = config.value(".attack.text_field_type")
        max_shingle_size = positive_int(
            config.value(".attack.search_as_you_type_max_shingle_size"),
            ".attack.search_as_you_type_max_shingle_size",
        )
        analyze_max_token_count = nonnegative_int(
            config.value(".attack.analyze_max_token_count"),
            ".attack.analyze_max_token_count",
        )
        prefix_query_mode = config.value(".attack.prefix_query_mode")
        chars = config.value(".attack.chars")
        exact_strategy = config.value(".attack.exact_strategy")
        batch_size = config.value(".attack.batch_size")
        recover_ngrams = parse_bool(config.value(".attack.recover_ngrams"))
        ngram_size = positive_int(config.value(".attack.ngram_size"), ".attack.ngram_size")
    except (ConfigError, ValueError) as exc:
        report.fail(str(exc))
        return

    validate_choice(
        report,
        text_field_type,
        name=".attack.text_field_type",
        choices=("text", "search_as_you_type"),
    )
    validate_choice(
        report,
        prefix_query_mode,
        name=".attack.prefix_query_mode",
        choices=VALID_PREFIX_QUERY_MODES,
    )
    validate_choice(
        report,
        exact_strategy,
        name=".attack.exact_strategy",
        choices=VALID_EXACT_STRATEGIES,
    )
    if batch_size != "auto":
        try:
            positive_int(batch_size, ".attack.batch_size")
        except ValueError as exc:
            report.fail(str(exc))
    if any(ch.isspace() for ch in chars):
        report.fail(".attack.chars must not contain whitespace")
    if analyze_max_token_count == 0:
        report.warn(".attack.analyze_max_token_count is 0; backend default will apply")

    if recover_ngrams:
        if text_field_type != "search_as_you_type":
            report.fail(".attack.recover_ngrams requires search_as_you_type text field")
        if not 2 <= ngram_size <= 4:
            report.fail(".attack.ngram_size must be 2, 3, or 4")
        if max_shingle_size < ngram_size:
            report.fail(
                ".attack.search_as_you_type_max_shingle_size must be >= .attack.ngram_size"
            )
        if prefix_query_mode != "span_prefix":
            report.fail(
                "reviewer DLS config should use span_prefix for 1-gram recovery; "
                "2/3/4-gram recovery still uses match_phrase_prefix on SAYT shingle fields"
            )
    if len(report.errors) == errors_before:
        report.ok("attack config is internally consistent")


def check_reconstruction_config(report: CheckReport, config: YqConfig) -> None:
    errors_before = len(report.errors)
    try:
        k = positive_int(config.value(".reconstruction.k"), ".reconstruction.k")
        source = config.value(".reconstruction.source")
        traversal = config.value(".reconstruction.traversal")
    except (ConfigError, ValueError) as exc:
        report.fail(str(exc))
        return

    if k < 2:
        report.fail(".reconstruction.k must be at least 2 for de Bruijn assembly")
    validate_choice(
        report,
        source,
        name=".reconstruction.source",
        choices=VALID_RECONSTRUCTION_SOURCES,
    )
    validate_choice(
        report,
        traversal,
        name=".reconstruction.traversal",
        choices=VALID_RECONSTRUCTION_TRAVERSALS,
    )
    if len(report.errors) == errors_before:
        report.ok("reconstruction config is internally consistent")


def read_backend_config(
    report: CheckReport,
    *,
    config: YqConfig,
    root_dir: Path,
    backend: str,
) -> Optional[BackendConfig]:
    prefix = "OPENSEARCH" if backend == "opensearch" else "ELASTICSEARCH"
    required_env = [
        f"{prefix}_HOST",
        f"{prefix}_PORT",
        f"{prefix}_SCHEME",
        f"{prefix}_VERIFY_CERTS",
        f"{prefix}_ADMIN_USERNAME",
        f"{prefix}_ADMIN_PASSWORD",
        f"{prefix}_USER_PASSWORD",
    ]
    if backend == "elasticsearch":
        required_env.append("ELASTICSEARCH_USER_USERNAME")

    try:
        label = config.value(f".backends.{backend}.label")
        compose_file = absolute_path(
            root_dir,
            config.value(f".backends.{backend}.compose_file"),
        )
        service = config.value(f".backends.{backend}.service")
        env_values = {
            name: config.value(f".backends.{backend}.env.{name}") for name in required_env
        }
        port = positive_int(env_values[f"{prefix}_PORT"], f"{prefix}_PORT")
        scheme = env_values[f"{prefix}_SCHEME"]
        verify_certs = parse_bool(env_values[f"{prefix}_VERIFY_CERTS"])
    except (ConfigError, ValueError) as exc:
        report.fail(str(exc))
        return None

    if not compose_file.is_file():
        report.fail(f"missing Docker Compose file for {backend}: {compose_file}")
    if not service:
        report.fail(f"empty Docker Compose service for {backend}")
    if not 1 <= port <= 65535:
        report.fail(f"{prefix}_PORT must be in [1, 65535], got {port}")
    if scheme not in {"http", "https"}:
        report.fail(f"{prefix}_SCHEME must be http or https, got {scheme!r}")

    effective_host = os.environ.get(f"{prefix}_HOST", env_values[f"{prefix}_HOST"])
    effective_port_text = os.environ.get(f"{prefix}_PORT", str(port))
    effective_scheme = os.environ.get(f"{prefix}_SCHEME", scheme)
    effective_verify_text = os.environ.get(
        f"{prefix}_VERIFY_CERTS",
        str(verify_certs).lower(),
    )
    try:
        effective_port = positive_int(effective_port_text, f"{prefix}_PORT")
        effective_verify_certs = parse_bool(effective_verify_text)
    except ValueError as exc:
        report.fail(str(exc))
        return None
    if not 1 <= effective_port <= 65535:
        report.fail(f"{prefix}_PORT must be in [1, 65535], got {effective_port}")
        return None
    if effective_scheme not in {"http", "https"}:
        report.fail(
            f"{prefix}_SCHEME environment override must be http or https, "
            f"got {effective_scheme!r}"
        )
        return None

    report.ok(
        f"{label} config: {env_values[f'{prefix}_SCHEME']}://"
        f"{env_values[f'{prefix}_HOST']}:{port}, service={service}, "
        f"verify_certs={str(verify_certs).lower()}"
    )
    return BackendConfig(
        name=backend,
        label=label,
        compose_file=compose_file,
        service=service,
        host=effective_host,
        port=effective_port,
        scheme=effective_scheme,
        verify_certs=effective_verify_certs,
    )


def check_backend_configs(
    report: CheckReport,
    *,
    config: YqConfig,
    root_dir: Path,
    backends: Sequence[str],
) -> list[BackendConfig]:
    configs: list[BackendConfig] = []
    for backend in backends:
        backend_config = read_backend_config(
            report,
            config=config,
            root_dir=root_dir,
            backend=backend,
        )
        if backend_config is not None:
            configs.append(backend_config)
    return configs


def check_docker(
    report: CheckReport,
    *,
    backend_configs: Sequence[BackendConfig],
    required_backends: Sequence[str],
) -> None:
    if not required_backends:
        report.ok("backend startup is not required because selected stats already exist")
        return

    docker = require_command(report, "docker")
    if docker is None:
        return
    for args, label in [
        ([docker, "compose", "version"], "Docker Compose v2"),
        ([docker, "info"], "Docker daemon"),
    ]:
        result = run_command(args, timeout=30.0)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            report.fail(f"{label} check failed: {detail}")
        else:
            report.ok(f"{label} is available")

    required = {backend for backend in required_backends}
    for backend_config in backend_configs:
        if backend_config.name not in required:
            continue
        result = run_command(
            [
                docker,
                "compose",
                "-f",
                str(backend_config.compose_file),
                "config",
                "--quiet",
            ],
            timeout=30.0,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            report.fail(
                f"Docker Compose config failed for {backend_config.label}: {detail}"
            )
        else:
            report.ok(f"Docker Compose config is valid for {backend_config.label}")


def check_backend_reachable(report: CheckReport, backend_config: BackendConfig) -> None:
    try:
        with socket.create_connection(
            (backend_config.host, backend_config.port),
            timeout=3.0,
        ):
            pass
    except OSError as exc:
        report.fail(
            f"{backend_config.label} is required but --skip-docker-start is set and "
            f"{backend_config.host}:{backend_config.port} is not reachable: {exc}"
        )
    else:
        report.ok(
            f"{backend_config.label} is reachable at "
            f"{backend_config.scheme}://{backend_config.host}:{backend_config.port}"
        )


def check_backends(
    report: CheckReport,
    *,
    backend_configs: Sequence[BackendConfig],
    output_dir: Path,
    datasets: Sequence[str],
    trials: int,
    skip_docker_start: bool,
    destructive_rerun: bool,
) -> None:
    required_backends = [
        backend_config.name
        for backend_config in backend_configs
        if backend_required(
            output_dir,
            backend_config.name,
            datasets,
            trials,
            destructive_rerun=destructive_rerun,
        )
    ]
    if skip_docker_start:
        for backend_config in backend_configs:
            if backend_config.name in required_backends:
                check_backend_reachable(report, backend_config)
        return
    check_docker(
        report,
        backend_configs=backend_configs,
        required_backends=required_backends,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run doctor checks for the local DLS reviewer workflow."
    )
    parser.add_argument(
        "--root-dir",
        default=None,
        help="DLS artifact root. Defaults to the installed artifact directory.",
    )
    parser.add_argument("--config", default="config/config.yml")
    parser.add_argument("--venv", default=".venv")
    parser.add_argument("--output-dir", default="results/reviewer")
    parser.add_argument("--datasets", default="d1,d10,d100,d1000")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument(
        "--backend",
        action="append",
        default=[],
        help="Backend to check. Repeat for multiple. Defaults to both.",
    )
    parser.add_argument("--skip-docker-start", action="store_true")
    parser.add_argument("--destructive-rerun-existing-results", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    report = CheckReport(verbose=not args.quiet)

    root_dir = Path(args.root_dir).resolve() if args.root_dir else artifact_root()
    config_file = absolute_path(root_dir, args.config).resolve()
    venv_dir = absolute_path(root_dir, args.venv).resolve()
    output_dir = absolute_path(root_dir, args.output_dir).resolve()

    try:
        datasets = parse_dataset_spec(args.datasets)
        backends = parse_backends(args.backend)
        if args.trials <= 0:
            raise ValueError("--trials must be positive")
    except ValueError as exc:
        report.fail(str(exc))
        return 1

    check_required_files(
        report,
        root_dir=root_dir,
        config_file=config_file,
        datasets=datasets,
    )
    check_corpus_files(report, root_dir=root_dir, datasets=datasets)
    check_python_environment(report, root_dir=root_dir, venv_dir=venv_dir)
    yq = require_command(report, "yq")
    check_output_dirs(report, output_dir)

    backend_configs: list[BackendConfig] = []
    if yq is not None and config_file.is_file():
        config = YqConfig(config_file)
        check_attack_config(report, config)
        check_reconstruction_config(report, config)
        backend_configs = check_backend_configs(
            report,
            config=config,
            root_dir=root_dir,
            backends=backends,
        )
    else:
        report.fail("cannot validate config without yq and a readable config file")

    check_backends(
        report,
        backend_configs=backend_configs,
        output_dir=output_dir,
        datasets=datasets,
        trials=args.trials,
        skip_docker_start=bool(args.skip_docker_start),
        destructive_rerun=bool(args.destructive_rerun_existing_results),
    )

    report.print_table()
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
