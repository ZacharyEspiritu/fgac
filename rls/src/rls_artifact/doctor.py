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

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.table import Table

from rls_artifact.paths import find_project_root


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


REQUIRED_TOOLS = ("bash", "git", "tar", "ssh", "scp", "yq", "uv")
TEX_TOOLS = ("xelatex", "latexmk")
PACKAGES = (
    "microbenchmarks",
    "mitigation",
    "noise",
    "patients",
    "reconstruction",
    "renderers",
    "timing_oracle",
    "util",
)


def run_doctor(*, skip_gcloud: bool = False, skip_tex: bool = False) -> int:
    checks = [
        _check_python_version(),
        _check_project_root(),
        *_check_package_imports(),
        *_check_tools(REQUIRED_TOOLS),
    ]
    if not skip_tex:
        checks.extend(_check_tools(TEX_TOOLS))
    if not skip_gcloud:
        checks.extend(_check_gcloud())

    _print_checks(checks)
    return 1 if any(check.status == "FAIL" for check in checks) else 0


def _check_python_version() -> Check:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        return Check("Python", "OK", f"{version} at {sys.executable}")
    return Check("Python", "FAIL", f"{version}; expected >= 3.10")


def _check_project_root() -> Check:
    root = find_project_root()
    if root is None:
        return Check("Project root", "FAIL", "could not find rls/pyproject.toml")
    return Check("Project root", "OK", str(root))


def _check_package_imports() -> list[Check]:
    checks: list[Check] = []
    for package in PACKAGES:
        try:
            importlib.import_module(package)
        except Exception as exc:
            checks.append(Check(f"Import {package}", "FAIL", f"{exc.__class__.__name__}: {exc}"))
        else:
            checks.append(Check(f"Import {package}", "OK", "available"))
    return checks


def _check_tools(tools: tuple[str, ...]) -> list[Check]:
    checks: list[Check] = []
    for tool in tools:
        path = shutil.which(tool)
        if path is None:
            checks.append(Check(f"Tool {tool}", "FAIL", "not found on PATH"))
        else:
            checks.append(Check(f"Tool {tool}", "OK", path))
    return checks


def _check_gcloud() -> list[Check]:
    if shutil.which("gcloud") is None:
        return [Check("Tool gcloud", "FAIL", "not found on PATH")]

    account = _run_stdout(
        "gcloud",
        "auth",
        "list",
        "--filter=status:ACTIVE",
        "--format=value(account)",
    ).splitlines()
    project = _run_stdout("gcloud", "config", "get-value", "project").strip()

    checks: list[Check] = []
    if account:
        checks.append(Check("GCloud account", "OK", account[0]))
    else:
        checks.append(Check("GCloud account", "FAIL", "no active account"))

    if project and project != "(unset)":
        checks.append(Check("GCloud project", "OK", project))
    else:
        checks.append(Check("GCloud project", "FAIL", "no active project"))
    return checks


def _run_stdout(*cmd: str) -> str:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout


def _print_checks(checks: list[Check]) -> None:
    table = Table(title="RLS Artifact Doctor", box=box.ROUNDED, show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Detail", overflow="fold")

    for check in checks:
        style = "green" if check.status == "OK" else "red"
        table.add_row(check.name, f"[{style}]{check.status}[/{style}]", check.detail)

    Console(highlight=False).print(table)
