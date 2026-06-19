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
import sys
from pathlib import Path
from typing import List, Optional

from util.paths import resolve_artifact_path


def config_value(config: Path, expression: str) -> str:
    completed = subprocess.run(
        ["yq", "-r", f"{expression} | tostring", str(config)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    value = completed.stdout.strip()
    if not value or value == "null":
        raise RuntimeError(f"missing {expression} in {config}")
    return value


def config_bool(config: Path, expression: str) -> bool:
    value = config_value(config, expression).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"expected boolean {expression} in {config}, got {value!r}")


def optional_arg(flag: str, value: Optional[str]) -> List[str]:
    if value is None:
        return []
    return [flag, value]


def build_command(args: argparse.Namespace) -> List[str]:
    config = resolve_artifact_path(args.config)
    command = [] if args.arguments_only else [args.cli_bin, "enumerate"]
    command.extend(
        [
        "--backend",
        args.backend,
        "--text-field-type",
        config_value(config, ".attack.text_field_type"),
        "--search-as-you-type-max-shingle-size",
        config_value(config, ".attack.search_as_you_type_max_shingle_size"),
        "--analyze-max-token-count",
        config_value(config, ".attack.analyze_max_token_count"),
        "--prefix-query-mode",
        config_value(config, ".attack.prefix_query_mode"),
        "--corpus-file",
        args.corpus_file,
        "--chars",
        config_value(config, ".attack.chars"),
        "--exact-strategy",
        config_value(config, ".attack.exact_strategy"),
        "--batch-size",
        config_value(config, ".attack.batch_size"),
        "--stats-file",
        args.stats_file,
        "--random-seed",
        args.random_seed,
        ]
    )
    command.extend(optional_arg("--progress-interval", args.progress_interval))
    command.extend(optional_arg("--attack-log-file", args.attack_log_file))
    if args.rich_progress:
        command.extend(["--attack-progress-tty", "--rich-force-terminal"])
    if config_bool(config, ".attack.recover_ngrams"):
        command.extend(
            [
                "--recover-ngrams",
                "--ngram-size",
                config_value(config, ".attack.ngram_size"),
            ]
        )
    command.extend(args.extra_args)
    return command


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the unfilter-dls enumerate command used by reviewer scripts "
            "from config/config.yml."
        )
    )
    parser.add_argument("--config", default="config/config.yml")
    parser.add_argument(
        "--cli-bin",
        default="unfilter-dls",
        help="CLI executable to use when printing a full command.",
    )
    parser.add_argument(
        "--python-bin",
        default="python3",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--arguments-only",
        action="store_true",
        help="Print only enumerator arguments, omitting unfilter-dls enumerate.",
    )
    parser.add_argument("--backend", choices=("opensearch", "elasticsearch"), required=True)
    parser.add_argument("--corpus-file", required=True)
    parser.add_argument("--stats-file", required=True)
    parser.add_argument("--random-seed", required=True)
    parser.add_argument("--progress-interval", default=None)
    parser.add_argument("--attack-log-file", default=None)
    parser.add_argument("--rich-progress", action="store_true")
    parser.add_argument(
        "--extra-arg",
        dest="extra_args",
        action="append",
        default=[],
        help="Extra argument to append to the enumerator command. Repeat as needed.",
    )
    return parser.parse_args()


def main() -> int:
    try:
        for argument in build_command(parse_args()):
            print(argument)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
