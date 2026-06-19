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

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterator, Optional

from .utils import JsonDict


@dataclass
class TimingTelemetry:
    started_at: float = field(default_factory=time.perf_counter)
    ended_at: Optional[float] = None
    opensearch_wait_seconds: float = 0.0
    opensearch_wait_by_operation: Dict[str, float] = field(default_factory=dict)
    opensearch_calls_by_operation: Dict[str, int] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @contextmanager
    def opensearch(self, operation: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            with self.lock:
                self.opensearch_wait_seconds += elapsed
                self.opensearch_wait_by_operation[operation] = (
                    self.opensearch_wait_by_operation.get(operation, 0.0) + elapsed
                )
                self.opensearch_calls_by_operation[operation] = (
                    self.opensearch_calls_by_operation.get(operation, 0) + 1
                )

    def finish(self) -> None:
        if self.ended_at is None:
            self.ended_at = time.perf_counter()

    @property
    def runtime_seconds(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.perf_counter()
        return end - self.started_at

    @property
    def attacker_script_seconds(self) -> float:
        return max(0.0, self.runtime_seconds - self.opensearch_wait_seconds)

    def to_json(self) -> JsonDict:
        self.finish()
        return {
            "runtime_seconds": self.runtime_seconds,
            "opensearch_wait_seconds": self.opensearch_wait_seconds,
            "attacker_script_seconds": self.attacker_script_seconds,
            "opensearch_wait_by_operation": dict(
                sorted(self.opensearch_wait_by_operation.items())
            ),
            "opensearch_calls_by_operation": dict(
                sorted(self.opensearch_calls_by_operation.items())
            ),
        }
