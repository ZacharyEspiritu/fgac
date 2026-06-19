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
from dataclasses import dataclass
from typing import Dict, List, Tuple
from urllib.parse import quote, urlparse, urlunparse

from util.db_backend import DatabaseBackend
from patients.sampling import SiteKeyPool, load_site_key_pool
from patients.credentials import load_user_creds


def replace_dsn_credentials(dsn: str, user_name: str, password: str) -> str:
    parsed = urlparse(dsn)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            "Background load requires a URL-style DSN so per-user credentials can be substituted"
        )
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    user_part = quote(user_name, safe="")
    password_part = quote(password, safe="")
    netloc = f"{user_part}:{password_part}@{host}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


@dataclass(frozen=True)
class NoiseClientConfig:
    user_name: str
    password: str
    site_id: int
    dsn: str
    key_pool: SiteKeyPool


@dataclass
class NoiseRunSummary:
    elapsed_s: float
    actual_total_qps: float
    counts: Dict[str, int]
    avg_latency_ns: Dict[str, float]


# Background-load query mix labels. The order matches the ratio tuples built in
# both Table 1 drivers and the per-label counters the controllers accumulate.
NOISE_QUERY_TYPES: Tuple[str, str, str] = ("authorized", "unauthorized", "nonexistent")


def empty_noise_counter() -> Dict[str, int]:
    return {label: 0 for label in NOISE_QUERY_TYPES}


def build_noise_ratios(args: argparse.Namespace) -> List[Tuple[str, float]]:
    return [
        ("authorized", args.noise_authorized_ratio),
        ("unauthorized", args.noise_unauthorized_ratio),
        ("nonexistent", args.noise_nonexistent_ratio),
    ]


def add_noise_workload_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--users-file",
        default=os.path.join("data", "doctors.csv"),
        help="Credential CSV used for background-load clients.",
    )
    parser.add_argument(
        "--noise-clients",
        type=int,
        default=0,
        help="Number of concurrent background clients (0 disables noise).",
    )
    parser.add_argument(
        "--noise-total-qps",
        type=float,
        default=0.0,
        help="Target aggregate QPS for the background load (0 = unthrottled).",
    )
    parser.add_argument(
        "--noise-warmup-seconds",
        type=float,
        default=2.0,
        help="Warmup duration before writing the ready marker or starting measurements.",
    )
    parser.add_argument(
        "--noise-authorized-ratio",
        type=float,
        default=0.90,
        help="Fraction of background queries that target rows visible to the noise client.",
    )
    parser.add_argument(
        "--noise-unauthorized-ratio",
        type=float,
        default=0.05,
        help="Fraction of background queries that target hidden rows.",
    )
    parser.add_argument(
        "--noise-nonexistent-ratio",
        type=float,
        default=0.05,
        help="Fraction of background queries that target nonexistent keys.",
    )
    parser.add_argument(
        "--noise-pool-size",
        type=int,
        default=256,
        help="Number of cached keys to sample per site for the background workload.",
    )


def validate_noise_args(args: argparse.Namespace) -> List[Tuple[str, float]]:
    ratios = build_noise_ratios(args)
    if args.noise_clients < 0:
        raise ValueError("--noise-clients must be >= 0")
    if any(weight < 0 for _, weight in ratios):
        raise ValueError("Noise ratios must be >= 0")
    if args.noise_clients > 0 and sum(weight for _, weight in ratios) <= 0:
        raise ValueError("At least one noise ratio must be > 0 when noise is enabled")
    return ratios


def select_noise_key(
    rng: random.Random,
    query_type: str,
    key_pool: SiteKeyPool,
    max_id: int,
    nonexistent_offset: int,
) -> int:
    if query_type == "authorized":
        return key_pool.authorized_keys[rng.randrange(len(key_pool.authorized_keys))]
    if query_type == "unauthorized":
        return key_pool.unauthorized_keys[rng.randrange(len(key_pool.unauthorized_keys))]
    return max_id + nonexistent_offset + rng.randrange(1_000_000)


def aggregate_noise_latency(
    counts: Dict[str, int],
    latency_sum_ns: Dict[str, int],
) -> Dict[str, float]:
    avg_latency_ns: Dict[str, float] = {}
    for label, count in counts.items():
        avg_latency_ns[label] = latency_sum_ns[label] / count if count else 0.0
    return avg_latency_ns


def choose_noise_users(
    users_file: str,
    attacker_user: str,
    noise_clients: int,
    attacker_dsn: str,
    rng: random.Random,
) -> List[Tuple[str, str, int, str]]:
    creds = [cred for cred in load_user_creds(users_file) if cred.user_name != attacker_user]
    if len(creds) < noise_clients:
        raise RuntimeError(
            f"Requested {noise_clients} noise clients but only found {len(creds)} other users in {users_file}"
        )

    rng.shuffle(creds)
    distinct_first: List = []
    used_sites = set()
    leftovers = []
    for cred in creds:
        if cred.tenant_id is None:
            leftovers.append(cred)
            continue
        site_id = int(cred.tenant_id)
        if site_id not in used_sites:
            used_sites.add(site_id)
            distinct_first.append(cred)
        else:
            leftovers.append(cred)
    ordered = distinct_first + leftovers

    selected = []
    for cred in ordered[:noise_clients]:
        if cred.tenant_id is None:
            raise RuntimeError(
                f"Noise user {cred.user_name} is missing site_id in {users_file}"
            )
        site_id = int(cred.tenant_id)
        selected.append(
            (
                cred.user_name,
                cred.password,
                site_id,
                replace_dsn_credentials(attacker_dsn, cred.user_name, cred.password),
            )
        )
    return selected


def build_noise_configs(
    admin,
    backend: DatabaseBackend,
    users_file: str,
    attacker_user: str,
    attacker_dsn: str,
    noise_clients: int,
    noise_pool_size: int,
    rng: random.Random,
) -> List[NoiseClientConfig]:
    if noise_clients <= 0:
        return []
    if noise_pool_size <= 0:
        raise ValueError("--noise-pool-size must be positive when noise is enabled")

    selected = choose_noise_users(users_file, attacker_user, noise_clients, attacker_dsn, rng)
    site_pools: Dict[int, SiteKeyPool] = {}
    unique_sites = sorted({site_id for _, _, site_id, _ in selected})

    with admin.cursor() as cur:
        for site_id in unique_sites:
            site_pools[site_id] = load_site_key_pool(cur, backend, site_id, noise_pool_size)

    return [
        NoiseClientConfig(
            user_name=user_name,
            password=password,
            site_id=site_id,
            dsn=dsn,
            key_pool=site_pools[site_id],
        )
        for user_name, password, site_id, dsn in selected
    ]
