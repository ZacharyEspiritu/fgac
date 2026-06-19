#!/usr/bin/env python3
"""Check the local Python environment used by the RLS run scripts.

This intentionally avoids third-party parsers: the run-script preflight should
work even when the project requirements are missing. It reads requirements.txt,
derives import module names from requirement package names, and verifies that
the active python can import them.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import importlib
import os
import re
import sys
import tempfile
from pathlib import Path


REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
MODULE_NAME_OVERRIDES = {
    "pyyaml": "yaml",
}


def _strip_inline_comment(line: str) -> str:
    # Keep this conservative: requirements.txt here is simple, but this avoids
    # treating URLs/fragments as comments if future entries ever use them.
    if " #" in line:
        return line.split(" #", 1)[0]
    return line


def requirement_modules(requirements: Path) -> list[str]:
    modules: list[str] = []
    seen: set[str] = set()

    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = _strip_inline_comment(raw).strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r ", "--requirement ", "-c ", "--constraint ", "-e ", "--editable ")):
            continue

        # Drop environment markers, version constraints, and extras:
        #   psycopg[binary]>=3.1 ; python_version >= "3.10" -> psycopg
        line = line.split(";", 1)[0].strip()
        match = REQ_NAME_RE.match(line)
        if not match:
            continue
        package_name = match.group(1).split("[", 1)[0]
        module_name = package_name.lower().replace("-", "_").replace(".", "_")
        module_name = MODULE_NAME_OVERRIDES.get(module_name, module_name)
        if module_name and module_name not in seen:
            seen.add(module_name)
            modules.append(module_name)

    return modules


def prepare_import_environment() -> None:
    if "MPLCONFIGDIR" not in os.environ:
        mpl_config = Path(tempfile.gettempdir()) / "rls_matplotlib"
        mpl_config.mkdir(parents=True, exist_ok=True)
        os.environ["MPLCONFIGDIR"] = str(mpl_config)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check local Python dependencies for RLS runs.")
    parser.add_argument("--requirements", required=True, type=Path, help="Path to requirements.txt.")
    parser.add_argument("--expected-venv", default="", help="Resolved project virtualenv path.")
    parser.add_argument("--min-python", default="3.10", help="Minimum Python version, as major.minor.")
    args = parser.parse_args()

    failures: list[str] = []

    try:
        min_major, min_minor = (int(part) for part in args.min_python.split(".", 1))
    except ValueError:
        failures.append(f"invalid --min-python value: {args.min_python}")
        min_major, min_minor = 3, 10

    if sys.version_info < (min_major, min_minor):
        failures.append(f"python3 is {sys.version.split()[0]}, expected >= {args.min_python}")

    if args.expected_venv and os.path.realpath(sys.prefix) != os.path.realpath(args.expected_venv):
        failures.append(f"python3 is not running from the project venv selected by uv (sys.prefix={sys.prefix})")

    if not args.requirements.exists():
        failures.append(f"requirements.txt not found: {args.requirements}")
        modules: list[str] = []
    else:
        modules = requirement_modules(args.requirements)

    prepare_import_environment()
    for module in modules:
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                importlib.import_module(module)
        except Exception as exc:  # pragma: no cover - diagnostic path
            failures.append(f"missing Python module '{module}' ({exc.__class__.__name__}: {exc})")

    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
