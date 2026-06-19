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

import argparse
from typing import List


DEFAULT_ADMIN_DSN_HELP = "Admin DSN for sampling keys."
DEFAULT_ATTACKER_DSN_HELP = "Attacker DSN (RLS applies)."
DEFAULT_ATTACKER_USER_HELP = "Attacker user name."
DEFAULT_SEED_HELP = "Random seed."


def add_admin_dsn_arg(
    parser: argparse.ArgumentParser,
    *,
    help: str = DEFAULT_ADMIN_DSN_HELP,
) -> None:
    parser.add_argument("--admin-dsn", required=True, help=help)


def add_attacker_dsn_arg(
    parser: argparse.ArgumentParser,
    *,
    help: str = DEFAULT_ATTACKER_DSN_HELP,
) -> None:
    parser.add_argument("--attacker-dsn", required=True, help=help)


def add_attacker_user_arg(
    parser: argparse.ArgumentParser,
    *,
    help: str = DEFAULT_ATTACKER_USER_HELP,
) -> None:
    parser.add_argument("--attacker-user", required=True, help=help)


def add_db_connection_args(
    parser: argparse.ArgumentParser,
    *,
    admin_help: str = DEFAULT_ADMIN_DSN_HELP,
    attacker_help: str = DEFAULT_ATTACKER_DSN_HELP,
    attacker_user_help: str = DEFAULT_ATTACKER_USER_HELP,
) -> None:
    add_admin_dsn_arg(parser, help=admin_help)
    add_attacker_dsn_arg(parser, help=attacker_help)
    add_attacker_user_arg(parser, help=attacker_user_help)


def add_seed_arg(
    parser: argparse.ArgumentParser,
    *,
    default: int = 1,
    help: str = DEFAULT_SEED_HELP,
) -> None:
    parser.add_argument("--seed", type=int, default=default, help=help)


def add_fast_arg(
    parser: argparse.ArgumentParser,
    *,
    help: str,
) -> None:
    parser.add_argument("--fast", action="store_true", help=help)


def add_warm_cache_arg(
    parser: argparse.ArgumentParser,
    *,
    help: str,
) -> None:
    parser.add_argument("--warm-cache", action="store_true", help=help)


def add_probes_arg(
    parser: argparse.ArgumentParser,
    *,
    default: int,
    help: str,
) -> None:
    parser.add_argument("--probes", type=int, default=default, help=help)


def require_positive(value: int, label: str) -> None:
    if value <= 0:
        raise ValueError(f"{label} must be positive")


def parse_optional_csv_ints(value: str, label: str) -> List[int]:
    if not value.strip():
        return []
    return parse_csv_ints(value, label)


def parse_csv_ints(value: str, label: str = "value", *, require_positive: bool = True) -> List[int]:
    items = []
    for raw in value.split(","):
        raw = raw.strip()
        if raw:
            items.append(int(raw))
    if not items:
        raise ValueError(f"{label} must contain at least one integer")
    if require_positive and any(item <= 0 for item in items):
        raise ValueError(f"{label} must contain positive integers")
    return items


def parse_csv_strings(value: str, label: str) -> List[str]:
    items = [raw.strip() for raw in value.split(",") if raw.strip()]
    if not items:
        raise ValueError(f"{label} must contain at least one value")
    return items
