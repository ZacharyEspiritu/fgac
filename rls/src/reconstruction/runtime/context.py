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
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Protocol, Sequence, Tuple, cast

from reconstruction.cli import ReconstructionOptions
from reconstruction.reporting.oracle_logs import OracleLogs
from reconstruction.truth import BackendLike
from reconstruction.types import CsvRow, DbConnection, DbCursor, DbValue
from util.db_backend import DatabaseBackend, connect


class ReconstructionBackend(BackendLike, Protocol):
    def apply_rls_policy(
        self,
        cur: DbCursor,
        _policy: str,
        _tables: Sequence[str],
    ) -> None: ...


@dataclass
class ReconstructionState:
    recovered: Dict[str, List[DbValue]]
    values_rows: List[CsvRow]
    skipped_probe: Dict[str, bool]
    query_counts: Dict[str, int]
    stage_times: Dict[str, float]
    binary_verify_counts: Dict[str, Tuple[int, int, int]]
    sampled_verify_counts: Dict[str, Tuple[int, int, int]]
    single_attr_threshold: Optional[int] = None

    def __init__(self) -> None:
        self.recovered = {}
        self.values_rows = []
        self.skipped_probe = {}
        self.query_counts = {}
        self.stage_times = {}
        self.binary_verify_counts = {}
        self.sampled_verify_counts = {}


@dataclass
class ReconstructionContext:
    args: ReconstructionOptions
    backend: ReconstructionBackend
    admin: Optional[DbConnection]
    attacker: DbConnection
    state: ReconstructionState
    oracle_logs: OracleLogs


@contextmanager
def open_reconstruction_context(
    args: ReconstructionOptions,
) -> Iterator[ReconstructionContext]:
    with ExitStack() as stack:
        backend = cast(
            ReconstructionBackend,
            DatabaseBackend.from_dsn(args.admin_dsn or args.attacker_dsn),
        )
        admin: Optional[DbConnection] = None
        if args.admin_dsn:
            admin = stack.enter_context(closing(connect(args.admin_dsn)))
        attacker: DbConnection = stack.enter_context(
            closing(connect(args.attacker_dsn))
        )

        if admin:
            with admin.cursor() as cur:
                backend.apply_rls_policy(cur, args.rls_policy, [args.table])

        os.makedirs(args.output_dir, exist_ok=True)
        oracle_logs = stack.enter_context(
            OracleLogs.create(args.output_dir, args.log_oracle_calls, args.workers)
        )
        yield ReconstructionContext(
            args=args,
            backend=backend,
            admin=admin,
            attacker=attacker,
            state=ReconstructionState(),
            oracle_logs=oracle_logs,
        )
