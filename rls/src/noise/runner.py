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
import os
import random
import signal
import time
from typing import Dict, Optional

from util.db_backend import DatabaseBackend
from util.io import ensure_parent_dir, write_json
from noise.controller import MultiprocessNoiseController, maybe_update_total_qps
from noise.workload import (
    add_noise_workload_args,
    build_noise_configs,
    validate_noise_args,
)
from patients.queries import build_noise_query
from patients.sampling import load_patient_max_id


_STOP_REQUESTED = False


def _handle_stop(_signum, _frame) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m noise",
        description=(
            "Run Table 1 background noise from a separate client VM until terminated."
        )
    )
    parser.add_argument("--admin-dsn", required=True, help="Admin DSN for sampling keys.")
    parser.add_argument(
        "--base-dsn",
        required=True,
        help="URL-style DSN used as the base for per-user noise client connections.",
    )
    parser.add_argument("--attacker-user", required=True, help="Attacker user to exclude from noise clients.")
    add_noise_workload_args(parser)
    parser.add_argument(
        "--nonexistent-offset",
        type=int,
        default=1000,
        help="Offset above max(id_number) for nonexistent keys.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join("results", "table1_noise.json"),
        help="Summary JSON output path.",
    )
    parser.add_argument(
        "--ready-file",
        default=os.path.join("results", "table1_noise.ready"),
        help="Marker file written after warmup completes.",
    )
    parser.add_argument(
        "--control-file",
        default="",
        help="Optional file containing the current aggregate target QPS as plain text.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use SELECT 1 with LIMIT/TOP 1 instead of fetching full rows.",
    )
    parser.add_argument(
        "--noise-query-mode",
        choices=("point", "range"),
        default="point",
        help=(
            "Shape of the noise query. 'point' (default) does the original "
            "SELECT ... WHERE id_number = $1 LIMIT 1; 'range' issues a "
            "SELECT count(*) ... WHERE id_number BETWEEN $1 AND $1+width "
            "so each noise query touches O(width) heap rows. Use 'range' on "
            "fast CPUs (e.g. c4) where point queries are too cheap to drive "
            "the DB CPU axis."
        ),
    )
    parser.add_argument(
        "--noise-range-width",
        type=int,
        default=100,
        help="Range width N for --noise-query-mode=range (BETWEEN id AND id+N).",
    )
    parser.add_argument("--seed", type=int, default=1, help="Random seed.")
    return parser.parse_args()


def main() -> None:
    global _STOP_REQUESTED
    args = parse_args()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    ratios = validate_noise_args(args)

    backend = DatabaseBackend.from_dsn(args.admin_dsn)

    admin = backend.connect(args.admin_dsn)
    controller: Optional[MultiprocessNoiseController] = None
    summary = None
    run_error = None
    ready_written = False
    start_wall = time.time()
    current_target_qps = args.noise_total_qps

    try:
        with admin.cursor() as cur:
            max_id = load_patient_max_id(cur)

        noise_client_configs = build_noise_configs(
            admin,
            backend,
            args.users_file,
            args.attacker_user,
            args.base_dsn,
            args.noise_clients,
            args.noise_pool_size,
            random.Random(args.seed),
        )

        patient_query = build_noise_query(
            backend,
            args.noise_query_mode,
            args.fast,
            args.noise_range_width,
        )

        controller = MultiprocessNoiseController(
            backend=backend,
            patient_query=patient_query,
            client_configs=noise_client_configs,
            total_qps=args.noise_total_qps,
            warmup_seconds=args.noise_warmup_seconds,
            ratios=ratios,
            max_id=max_id,
            nonexistent_offset=args.nonexistent_offset,
            seed=args.seed,
            policy="external",
        )
        controller.start()

        ensure_parent_dir(args.ready_file)
        with open(args.ready_file, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        ready_written = True

        while not _STOP_REQUESTED:
            controller.raise_if_failed()
            current_target_qps = maybe_update_total_qps(
                controller,
                args.control_file,
                current_target_qps,
            )
            time.sleep(0.5)
    except Exception as exc:
        run_error = str(exc)
    finally:
        if controller is not None:
            try:
                summary = controller.stop()
            except Exception as exc:
                if run_error is None:
                    run_error = str(exc)
        admin.close()
        if ready_written and os.path.exists(args.ready_file):
            try:
                os.remove(args.ready_file)
            except OSError:
                pass

    payload: Dict[str, object] = {
        "source": "external_vm",
        "pid": os.getpid(),
        "noise_clients": args.noise_clients,
        "control_file": args.control_file or None,
        "initial_target_total_qps": args.noise_total_qps,
        "target_total_qps": current_target_qps,
        "started_at_unix": start_wall,
        "completed_at_unix": time.time(),
        "status": "completed" if run_error is None else "error",
    }
    if summary is not None:
        payload.update(
            {
                "elapsed_s": summary.elapsed_s,
                "actual_total_qps": summary.actual_total_qps,
                "counts": summary.counts,
                "avg_latency_ns": summary.avg_latency_ns,
            }
        )
    else:
        payload.update(
            {
                "elapsed_s": None,
                "actual_total_qps": None,
                "counts": None,
                "avg_latency_ns": None,
            }
        )
    if run_error is not None:
        payload["error"] = run_error

    write_json(args.output, payload)

    if run_error is not None:
        raise RuntimeError(run_error)
