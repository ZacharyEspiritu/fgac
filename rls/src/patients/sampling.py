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

import random
from dataclasses import dataclass
from typing import List

from util.db_backend import DatabaseBackend
from util.db_utils import fetch_optional_scalar
from util.sql_utils import validate_identifier


_NONEXISTENT_INT_OFFSET = 10_000_000


@dataclass(frozen=True)
class PatientSamplingContext:
    attacker_site: int
    max_id: int


@dataclass(frozen=True)
class SiteKeyPool:
    authorized_keys: List[int]
    unauthorized_keys: List[int]


@dataclass(frozen=True)
class AttributeValuePool:
    authorized_values: List[object]
    unauthorized_values: List[object]
    nonexistent_values: List[object]


def load_patient_sampling_context(
    cur,
    backend: DatabaseBackend,
    attacker_user: str,
) -> PatientSamplingContext:
    attacker_site = fetch_optional_scalar(
        cur,
        f"SELECT site_id FROM doctors WHERE user_name = {backend.param}",
        (attacker_user,),
    )
    if attacker_site is None:
        raise RuntimeError(f"Attacker user {attacker_user!r} not found in doctors table")

    max_id = load_patient_max_id(cur)
    return PatientSamplingContext(attacker_site=attacker_site, max_id=max_id)


def load_patient_max_id(cur) -> int:
    max_id = fetch_optional_scalar(cur, "SELECT max(id_number) FROM patients", ())
    if max_id is None:
        raise RuntimeError("patients table appears empty")
    return max_id


def sample_patient_keys(
    cur,
    backend: DatabaseBackend,
    site_id: int,
    samples: int,
    visible: bool,
) -> List[int]:
    comparator = "=" if visible else "<>"
    base_sql = (
        f"SELECT id_number FROM patients "
        f"WHERE site_id {comparator} {backend.param} "
        f"ORDER BY id_number"
    )
    query, limit_params = backend.add_limit(base_sql, samples)
    cur.execute(query, (site_id, *limit_params))
    rows = [int(row[0]) for row in cur.fetchall()]
    if not rows:
        kind = "authorized" if visible else "unauthorized"
        raise RuntimeError(f"No {kind} keys found for site {site_id}")
    return rows


def load_site_key_pool(
    cur,
    backend: DatabaseBackend,
    site_id: int,
    samples: int,
) -> SiteKeyPool:
    return SiteKeyPool(
        authorized_keys=sample_patient_keys(cur, backend, site_id, samples, visible=True),
        unauthorized_keys=sample_patient_keys(cur, backend, site_id, samples, visible=False),
    )


def sample_attribute_values(
    cur,
    backend: DatabaseBackend,
    attribute: str,
    site_id: int,
    samples: int,
    kind: str,
) -> List[object]:
    validate_identifier(attribute)
    p = backend.param
    if kind == "authorized":
        query, limit_params = backend.add_limit(
            f"SELECT {attribute} FROM patients WHERE site_id = {p} ORDER BY id_number",
            samples,
        )
        params = (site_id, *limit_params)
    elif kind == "unauthorized":
        query, limit_params = backend.add_limit(
            f"SELECT p.{attribute} FROM patients p "
            f"WHERE p.site_id <> {p} "
            f"  AND NOT EXISTS (SELECT 1 FROM patients p2 "
            f"                  WHERE p2.site_id = {p} "
            f"                    AND p2.{attribute} = p.{attribute}) "
            f"ORDER BY p.id_number",
            samples,
        )
        params = (site_id, site_id, *limit_params)
    else:
        raise ValueError(f"Unknown sampling kind: {kind!r}")

    cur.execute(query, params)
    rows = [row[0] for row in cur.fetchall()]
    if not rows:
        raise RuntimeError(
            f"No {kind} {attribute} values found for site {site_id}. "
            f"For low-cardinality attributes or values that overlap across all "
            f"sites, the {kind!r} class is empty; choose ssn or id_number."
        )
    return rows


def make_nonexistent_value(attribute: str, seed: int, max_id: int) -> object:
    if attribute == "id_number":
        return max_id + 1000 + seed
    if attribute == "age":
        return _NONEXISTENT_INT_OFFSET + seed
    if attribute == "ssn":
        return f"NX-NX-{seed % 10_000_000:07d}"
    if attribute == "zip_code":
        return f"Z{seed % 1_000_000_000:09d}"
    if attribute == "name":
        return f"__NONEXIST_{seed:010d}"
    raise ValueError(
        f"Unsupported attribute for nonexistent generation: {attribute!r}. "
        f"Extend make_nonexistent_value() to add a generator for it."
    )


def sample_nonexistent_values(
    cur,
    attribute: str,
    samples: int,
    rng: random.Random,
    max_id: int,
) -> List[object]:
    if attribute != "ssn":
        return [make_nonexistent_value(attribute, i, max_id) for i in range(samples)]

    pool: List[object] = []
    seen: set = set()
    for _ in range(200):
        if len(pool) >= samples:
            break
        need = samples - len(pool)
        candidates: List[str] = []
        cand_seen = set()
        while len(candidates) < need * 3:
            value = (
                f"{rng.randint(0, 999):03d}-"
                f"{rng.randint(0, 99):02d}-"
                f"{rng.randint(0, 9999):04d}"
            )
            if value not in cand_seen and value not in seen:
                cand_seen.add(value)
                candidates.append(value)
        cur.execute("SELECT ssn FROM patients WHERE ssn = ANY(%s)", (candidates,))
        present = {row[0] for row in cur.fetchall()}
        for value in candidates:
            if value not in present:
                pool.append(value)
                seen.add(value)
                if len(pool) >= samples:
                    break
    if len(pool) < samples:
        raise RuntimeError(
            f"Could only sample {len(pool)}/{samples} absent ssns; table too dense?"
        )
    return pool


def load_attribute_value_pool(
    cur,
    backend: DatabaseBackend,
    attribute: str,
    site_id: int,
    samples: int,
    rng: random.Random,
    max_id: int,
) -> AttributeValuePool:
    return AttributeValuePool(
        authorized_values=sample_attribute_values(
            cur, backend, attribute, site_id, samples, "authorized"
        ),
        unauthorized_values=sample_attribute_values(
            cur, backend, attribute, site_id, samples, "unauthorized"
        ),
        nonexistent_values=sample_nonexistent_values(cur, attribute, samples, rng, max_id),
    )
