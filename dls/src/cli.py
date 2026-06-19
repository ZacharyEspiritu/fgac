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
import importlib
import sys
from collections.abc import Callable, Sequence
from typing import cast


CommandMain = Callable[[], int]
CLI_NAME = "unfilter-dls"


COMMANDS = {
    "enumerate": (
        "enumerator.cli",
        "Run the DLS term and n-gram enumerator.",
        "python -m enumerator",
    ),
    "debruijn": (
        "debruijn.reconstruct_debruijn",
        "Build and traverse a de Bruijn graph from recovered k-grams.",
        "python src/debruijn/reconstruct_debruijn.py",
    ),
    "table": (
        "latex.render_table",
        "Render a LaTeX recovery table from enumerator stats JSON files.",
        "python src/latex/render_table.py",
    ),
    "doctor": (
        "util.preflight",
        "Run local reviewer workflow doctor checks.",
        "python src/util/preflight.py",
    ),
    "build-command": (
        "util.build_enumerator_command",
        "Print the config-driven enumerator command used by wrapper scripts.",
        "python src/util/build_enumerator_command.py",
    ),
    "wait-backend": (
        "util.wait_for_search_backend",
        "Wait for an OpenSearch or Elasticsearch endpoint to become ready.",
        "python src/util/wait_for_search_backend.py",
    ),
    "parse-hf-dataset": (
        "dataset.util.parse_hf_dataset",
        "Write a deterministic Hugging Face Enron slice to local JSONL.",
        "python src/dataset/util/parse_hf_dataset.py",
    ),
}

ALIASES = {
    "enum": "enumerate",
    "reconstruct": "debruijn",
    "render-table": "table",
}


def command_choices() -> list[str]:
    return sorted([*COMMANDS, *ALIASES])


def module_main(module_name: str) -> CommandMain:
    module = importlib.import_module(module_name)
    main = getattr(module, "main", None)
    if not callable(main):
        raise RuntimeError(f"{module_name} does not expose a callable main()")
    return cast(CommandMain, main)


def run_command(command: str, args: Sequence[str]) -> int:
    command = ALIASES.get(command, command)
    module_name, _, _legacy_entrypoint = COMMANDS[command]
    if command == "enumerate":
        from enumerator.runner import main as enumerator_main

        try:
            return int(enumerator_main(args, prog=f"{CLI_NAME} {command}"))
        except SystemExit as exc:
            return system_exit_code(exc)

    main = module_main(module_name)

    old_argv = sys.argv
    sys.argv = [f"{CLI_NAME} {command}", *args]
    try:
        return int(main())
    except SystemExit as exc:
        return system_exit_code(exc)
    finally:
        sys.argv = old_argv


def system_exit_code(exc: SystemExit) -> int:
    if isinstance(exc.code, int):
        return exc.code
    if exc.code is None:
        return 0
    print(exc.code, file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CLI_NAME,
        description=(
            "Unified CLI for the DLS artifact. Use a subcommand followed by "
            "`--help` to see that tool's original options."
        ),
    )
    parser.add_argument(
        "command",
        choices=command_choices(),
        help="Tool to run.",
    )
    parser.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to the selected tool.",
    )

    lines = ["available commands:"]
    for name in sorted(COMMANDS):
        _, summary, legacy = COMMANDS[name]
        lines.append(f"  {name:<16} {summary} Replaces `{legacy}`.")
    lines.append("aliases:")
    for alias in sorted(ALIASES):
        lines.append(f"  {alias:<16} {ALIASES[alias]}")
    parser.epilog = "\n".join(lines)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    return run_command(namespace.command, namespace.args)


if __name__ == "__main__":
    raise SystemExit(main())
