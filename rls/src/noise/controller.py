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
import multiprocessing as mp
import os
import queue
import random
import time
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from util.db_backend import DatabaseBackend
from util.random_utils import choose_weighted
from util.timing import timed_query
from noise.workload import (
    NoiseClientConfig,
    NoiseRunSummary,
    aggregate_noise_latency,
    empty_noise_counter,
    select_noise_key,
)
from patients.queries import PatientQuery


class _NoiseProcess(Protocol):
    @property
    def name(self) -> str:
        ...

    @property
    def exitcode(self) -> Optional[int]:
        ...

    def start(self) -> None:
        ...

    def join(self) -> None:
        ...


def _wait_for_deadline_or_stop(deadline: float, stop_event) -> bool:
    sleep_s = deadline - time.perf_counter()
    if sleep_s <= 0:
        return stop_event.is_set()
    return bool(stop_event.wait(sleep_s))


def _noise_worker_process(
    index: int,
    backend_name: str,
    patient_query: PatientQuery,
    config: NoiseClientConfig,
    client_count: int,
    ratios: Sequence[Tuple[str, float]],
    max_id: int,
    nonexistent_offset: int,
    seed: int,
    total_qps,
    start_event,
    stop_event,
    result_queue,
    error_queue,
) -> None:
    local_rng = random.Random(seed + 1000 + index)
    local_counts: Dict[str, int] = empty_noise_counter()
    local_latency_sum_ns: Dict[str, int] = empty_noise_counter()
    conn = None
    next_deadline = time.perf_counter()

    try:
        backend = DatabaseBackend.from_dsn(config.dsn)
        if backend.name != backend_name:
            raise RuntimeError(
                f"Noise client {config.user_name} backend mismatch: "
                f"expected {backend_name}, got {backend.name}"
            )
        conn = backend.connect(config.dsn)
        with conn.cursor() as cur:
            start_event.wait()
            while not stop_event.is_set():
                current_total_qps = max(float(total_qps.value), 0.0)
                # In the standalone controller, zero is a real throttle target
                # used by external CPU-control loops, so pause instead of
                # switching to unthrottled load.
                if current_total_qps <= 0:
                    if stop_event.wait(0.5):
                        break
                    next_deadline = time.perf_counter()
                    continue
                per_client_qps = 0.0
                if client_count > 0:
                    per_client_qps = current_total_qps / client_count
                interval_s = (1.0 / per_client_qps) if per_client_qps > 0 else 0.0
                if interval_s > 0:
                    if _wait_for_deadline_or_stop(next_deadline, stop_event):
                        break
                    next_deadline = time.perf_counter() + interval_s
                else:
                    next_deadline = time.perf_counter()

                query_type = choose_weighted(local_rng, ratios)
                key = select_noise_key(
                    local_rng,
                    query_type,
                    config.key_pool,
                    max_id,
                    nonexistent_offset,
                )
                elapsed_ns, _ = timed_query(
                    cur,
                    patient_query.sql,
                    patient_query.params_for_key(key),
                    fetch_one=patient_query.fetch_one_only,
                )
                local_counts[query_type] += 1
                local_latency_sum_ns[query_type] += elapsed_ns
    except Exception as exc:  # pragma: no cover - exercised against live databases
        error_queue.put(f"noise client {config.user_name} failed: {exc}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        result_queue.put(
            {
                "index": index,
                "counts": local_counts,
                "latency_sum_ns": local_latency_sum_ns,
            }
        )


class MultiprocessNoiseController:
    def __init__(
        self,
        backend: DatabaseBackend,
        patient_query: PatientQuery,
        client_configs: Sequence[NoiseClientConfig],
        total_qps: float,
        warmup_seconds: float,
        ratios: Sequence[Tuple[str, float]],
        max_id: int,
        nonexistent_offset: int,
        seed: int,
        policy: str,
    ) -> None:
        self.backend = backend
        self.patient_query = patient_query
        self.client_configs = list(client_configs)
        self.warmup_seconds = warmup_seconds
        self.ratios = list(ratios)
        self.max_id = max_id
        self.nonexistent_offset = nonexistent_offset
        self.seed = seed
        self.policy = policy
        self._ctx = mp.get_context("spawn")
        self._stop_event = self._ctx.Event()
        self._start_event = self._ctx.Event()
        self._total_qps = self._ctx.Value("d", max(float(total_qps), 0.0))
        self._result_queue = self._ctx.Queue()
        self._error_queue = self._ctx.Queue()
        self._processes: List[_NoiseProcess] = []
        self._errors: List[str] = []
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None

    def set_total_qps(self, total_qps: float) -> None:
        with self._total_qps.get_lock():
            self._total_qps.value = max(float(total_qps), 0.0)

    def start(self) -> None:
        if not self.client_configs:
            self._started_at = time.perf_counter()
            self._stopped_at = self._started_at
            return

        for index, config in enumerate(self.client_configs):
            process: _NoiseProcess = self._ctx.Process(
                target=_noise_worker_process,
                args=(
                    index,
                    self.backend.name,
                    self.patient_query,
                    config,
                    len(self.client_configs),
                    self.ratios,
                    self.max_id,
                    self.nonexistent_offset,
                    self.seed,
                    self._total_qps,
                    self._start_event,
                    self._stop_event,
                    self._result_queue,
                    self._error_queue,
                ),
                daemon=True,
                name=f"noise-{self.policy}-{index}",
            )
            self._processes.append(process)
            process.start()

        self._started_at = time.perf_counter()
        self._start_event.set()
        if self.warmup_seconds > 0:
            time.sleep(self.warmup_seconds)
        self.raise_if_failed()

    def stop(self) -> NoiseRunSummary:
        self._stop_event.set()
        for process in self._processes:
            process.join()
        self._stopped_at = time.perf_counter()

        worker_results = self._drain_results()
        self._drain_errors()
        for process in self._processes:
            if process.exitcode not in (0, None):
                self._errors.append(
                    f"noise worker {process.name} exited with code {process.exitcode}"
                )
        if len(worker_results) != len(self._processes):
            self._errors.append(
                f"expected {len(self._processes)} noise worker summaries "
                f"but received {len(worker_results)}"
            )
        self.raise_if_failed()

        counts: Dict[str, int] = empty_noise_counter()
        latency_sum_ns: Dict[str, int] = empty_noise_counter()
        for result in worker_results.values():
            worker_counts = result["counts"]
            worker_latency = result["latency_sum_ns"]
            for label in counts:
                counts[label] += int(worker_counts[label])
                latency_sum_ns[label] += int(worker_latency[label])

        elapsed_s = max((self._stopped_at or 0.0) - (self._started_at or 0.0), 0.0)
        total_queries = sum(counts.values())
        actual_qps = total_queries / elapsed_s if elapsed_s > 0 else 0.0
        return NoiseRunSummary(
            elapsed_s=elapsed_s,
            actual_total_qps=actual_qps,
            counts=counts,
            avg_latency_ns=aggregate_noise_latency(counts, latency_sum_ns),
        )

    def raise_if_failed(self) -> None:
        self._drain_errors()
        if self._errors:
            raise RuntimeError("; ".join(self._errors))

    def _drain_errors(self) -> None:
        while True:
            try:
                message = self._error_queue.get_nowait()
            except queue.Empty:
                return
            self._errors.append(str(message))

    def _drain_results(self) -> Dict[int, Dict[str, Dict[str, int]]]:
        results: Dict[int, Dict[str, Dict[str, int]]] = {}
        while True:
            try:
                payload = self._result_queue.get_nowait()
            except queue.Empty:
                return results
            results[int(payload["index"])] = payload


def maybe_update_total_qps(
    controller: MultiprocessNoiseController,
    control_file: str,
    last_value: Optional[float],
) -> Optional[float]:
    if not control_file or not os.path.exists(control_file):
        return last_value
    try:
        with open(control_file, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError:
        return last_value
    if not raw:
        return last_value
    try:
        value = float(raw)
    except ValueError:
        return last_value
    if not math.isfinite(value):
        return last_value
    if last_value is None or abs(value - last_value) > 1e-9:
        controller.set_total_qps(value)
        return value
    return last_value
