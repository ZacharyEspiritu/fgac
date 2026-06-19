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
import os
import sys
import time
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enumerator.search_backend import BACKEND_NAMES, backend_from_name


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean value, got {value!r}")


def configure_backend_env(args: argparse.Namespace) -> None:
    prefix = "OPENSEARCH" if args.backend == "opensearch" else "ELASTICSEARCH"
    os.environ[f"{prefix}_HOST"] = args.host
    os.environ[f"{prefix}_PORT"] = str(args.port)
    os.environ[f"{prefix}_SCHEME"] = args.scheme
    os.environ[f"{prefix}_ADMIN_USERNAME"] = args.username
    os.environ[f"{prefix}_ADMIN_PASSWORD"] = args.password
    os.environ[f"{prefix}_VERIFY_CERTS"] = str(args.verify_certs).lower()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=BACKEND_NAMES, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scheme", choices=("http", "https"), required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--verify-certs", type=parse_bool, required=True)
    parser.add_argument("--attempts", type=int, default=120)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_backend_env(args)
    backend = backend_from_name(args.backend)
    client = backend.connect_admin(timeout=10, max_retries=0, retry_on_timeout=False)

    last_error = None
    for _ in range(args.attempts):
        try:
            client.info()
            print(f"{backend.product_label} is ready")
            return 0
        except Exception as exc:
            last_error = exc
            time.sleep(args.sleep_seconds)

    print(f"{backend.product_label} did not become ready: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
