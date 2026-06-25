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

import math
import threading
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple, TypeVar

from reconstruction.types import DbConnection, DbCounter, DbCursor
from util.db_backend import connect
from util.progress import ProgressBar


@dataclass
class ProgressTracker:
    progress: Optional[ProgressBar]
    total: int
    count: int = 0
    lock: Optional[threading.Lock] = None

    def advance(self, delta: int, preview: Optional[str] = None) -> None:
        if self.lock:
            with self.lock:
                self.count += delta
                if self.progress:
                    self.progress.update(
                        min(self.count, self.total), preview=preview or ""
                    )
            return
        self.count += delta
        if self.progress:
            self.progress.update(min(self.count, self.total), preview=preview or "")

    def update(self, preview: Optional[str] = None) -> None:
        if self.lock:
            with self.lock:
                if self.progress:
                    self.progress.update(
                        min(self.count, self.total), preview=preview or ""
                    )
            return
        if self.progress:
            self.progress.update(min(self.count, self.total), preview=preview or "")


@dataclass
class ThreadSafeCounter:
    counts: Dict[str, int]
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, label: str, delta: int = 1) -> None:
        with self.lock:
            self.counts[label] = self.counts.get(label, 0) + delta


@dataclass(frozen=True)
class ProbeStep:
    tracker: ProgressTracker
    counter: DbCounter
    use_workers: bool


def make_probe_step(
    total: int,
    label: str,
    no_progress_output: bool,
    workers: int,
    query_counts: Dict[str, int],
    *,
    enable_workers: bool = True,
    create_progress: bool = True,
) -> ProbeStep:
    use_workers = enable_workers and workers > 1 and total > 0
    progress = (
        ProgressBar(total, label)
        if create_progress and not no_progress_output
        else None
    )
    progress_lock = threading.Lock() if use_workers else None
    tracker = ProgressTracker(progress, total, lock=progress_lock)
    counter = ThreadSafeCounter(query_counts) if use_workers else query_counts
    return ProbeStep(tracker=tracker, counter=counter, use_workers=use_workers)


def chunk_indices(total: int, chunks: int) -> List[Tuple[int, int]]:
    if total <= 0 or chunks <= 0:
        return []
    size = max(1, math.ceil(total / chunks))
    ranges: List[Tuple[int, int]] = []
    for start in range(0, total, size):
        end = min(total - 1, start + size - 1)
        ranges.append((start, end))
    return ranges


V = TypeVar("V")


def chunk_list(values: Sequence[V], chunks: int) -> List[Sequence[V]]:
    ranges = chunk_indices(len(values), chunks)
    return [values[start : end + 1] for start, end in ranges]


T = TypeVar("T")
R = TypeVar("R")


def execute_workers(
    use_workers: bool,
    work_items: Sequence[T],
    worker_fn: Callable[[T], R],
    merge_fn: Callable[[R], None],
    inline_fn: Optional[Callable[[], None]] = None,
) -> None:
    if not work_items:
        return
    if use_workers:
        with ThreadPoolExecutor(max_workers=len(work_items)) as executor:
            futures = [executor.submit(worker_fn, item) for item in work_items]
            for future in as_completed(futures):
                merge_fn(future.result())
    else:
        if inline_fn is not None:
            inline_fn()
        else:
            merge_fn(worker_fn(work_items[0]))


DbWorkItem = TypeVar("DbWorkItem")
DbWorkerResult = TypeVar("DbWorkerResult")


DbWorkerBody = Callable[[DbWorkItem, DbCursor, Optional[DbCursor]], DbWorkerResult]


def execute_db_workers(
    *,
    use_workers: bool,
    work_items: Sequence[DbWorkItem],
    attacker_dsn: str,
    admin_dsn: Optional[str],
    admin_enabled: bool,
    verify: bool,
    inline_attacker_cur: DbCursor,
    worker_body: DbWorkerBody[DbWorkItem, DbWorkerResult],
    merge_fn: Callable[[DbWorkerResult], None],
    inline_admin: Optional[DbConnection] = None,
    inline_verify_cur: Optional[DbCursor] = None,
) -> None:
    def worker_fn(item: DbWorkItem) -> DbWorkerResult:
        attacker_conn = connect(attacker_dsn)
        admin_conn = (
            connect(admin_dsn) if verify and admin_enabled and admin_dsn else None
        )
        try:
            with ExitStack() as stack:
                worker_cur = stack.enter_context(attacker_conn.cursor())
                worker_verify_cur = (
                    stack.enter_context(admin_conn.cursor()) if admin_conn else None
                )
                return worker_body(item, worker_cur, worker_verify_cur)
        finally:
            attacker_conn.close()
            if admin_conn:
                admin_conn.close()

    def inline_fn() -> None:
        with ExitStack() as stack:
            worker_verify_cur = inline_verify_cur
            if worker_verify_cur is None and verify and admin_enabled and inline_admin:
                worker_verify_cur = stack.enter_context(inline_admin.cursor())
            merge_fn(worker_body(work_items[0], inline_attacker_cur, worker_verify_cur))

    execute_workers(use_workers, work_items, worker_fn, merge_fn, inline_fn)
