from __future__ import annotations

import random

import pytest

from patients.queries import (
    PatientQuery,
    build_noise_query,
    build_patient_point_query,
    build_patient_range_query,
)
from patients.sampling import (
    AttributeValuePool,
    PatientSamplingContext,
    SiteKeyPool,
    load_attribute_value_pool,
    load_patient_max_id,
    load_patient_sampling_context,
    load_site_key_pool,
    sample_attribute_values,
    sample_patient_keys,
)
from util.db_backend import PostgresBackend


class FakeCursor:
    def __init__(
        self,
        *,
        fetchone_rows: list[tuple[object, ...] | None] | None = None,
        fetchall_rows: list[list[tuple[object, ...]]] | None = None,
    ) -> None:
        self.fetchone_rows = fetchone_rows or []
        self.fetchall_rows = fetchall_rows or []
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, query: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.fetchone_rows.pop(0) if self.fetchone_rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.fetchall_rows.pop(0) if self.fetchall_rows else []


def test_patient_query_params_for_key_and_range() -> None:
    limited = PatientQuery("SELECT *", limit_params=(1,))
    ranged = PatientQuery("SELECT count(*)", range_width=10)

    assert limited.params_for_key(5) == (5, 1)
    assert limited.params_for_range(5, 15) == (5, 15, 1)
    assert ranged.params_for_key(5) == (5, 15)
    assert ranged.params_for_range(5, 15) == (5, 15)


def test_build_patient_queries_use_fast_limit_when_requested() -> None:
    backend = PostgresBackend()

    point = build_patient_point_query(backend, fast=True)
    range_query = build_patient_range_query(backend, fast=True)
    full = build_patient_point_query(backend, fast=False)

    assert point.sql == "SELECT 1 FROM patients WHERE id_number = %s LIMIT %s"
    assert point.limit_params == (1,)
    assert point.fetch_one_only is True
    assert range_query.sql == (
        "SELECT 1 FROM patients WHERE id_number BETWEEN %s AND %s LIMIT %s"
    )
    assert range_query.limit_params == (1,)
    assert range_query.fetch_one_only is True
    assert full.sql == "SELECT * FROM patients WHERE id_number = %s"
    assert full.limit_params == ()
    assert full.fetch_one_only is False


def test_build_noise_query_supports_point_and_range_modes() -> None:
    backend = PostgresBackend()

    assert build_noise_query(backend, "point", True, 0).sql == (
        "SELECT 1 FROM patients WHERE id_number = %s LIMIT %s"
    )
    range_query = build_noise_query(backend, "range", False, 25)
    assert range_query.sql == "SELECT count(*) FROM patients WHERE id_number BETWEEN %s AND %s"
    assert range_query.fetch_one_only is True
    assert range_query.params_for_key(100) == (100, 125)

    with pytest.raises(ValueError, match="must be > 0"):
        build_noise_query(backend, "range", False, 0)
    with pytest.raises(ValueError, match="Unknown noise query mode"):
        build_noise_query(backend, "bad", False, 1)


def test_load_patient_sampling_context_reads_attacker_site_and_max_id() -> None:
    cur = FakeCursor(fetchone_rows=[(3,), (999,)])

    context = load_patient_sampling_context(cur, PostgresBackend(), "doctor_s3")

    assert context == PatientSamplingContext(attacker_site=3, max_id=999)
    assert cur.calls == [
        ("SELECT site_id FROM doctors WHERE user_name = %s", ("doctor_s3",)),
        ("SELECT max(id_number) FROM patients", ()),
    ]


def test_load_patient_sampling_context_reports_missing_attacker_or_empty_patients() -> None:
    with pytest.raises(RuntimeError, match="not found"):
        load_patient_sampling_context(FakeCursor(fetchone_rows=[None]), PostgresBackend(), "missing")

    with pytest.raises(RuntimeError, match="patients table appears empty"):
        load_patient_max_id(FakeCursor(fetchone_rows=[None]))


def test_sample_patient_keys_and_site_key_pool() -> None:
    cur = FakeCursor(fetchall_rows=[[(1,), (2,)], [(9,), (10,)]])

    pool = load_site_key_pool(cur, PostgresBackend(), site_id=3, samples=2)

    assert pool == SiteKeyPool(authorized_keys=[1, 2], unauthorized_keys=[9, 10])
    assert cur.calls[0] == (
        "SELECT id_number FROM patients WHERE site_id = %s ORDER BY id_number LIMIT %s",
        (3, 2),
    )
    assert cur.calls[1] == (
        "SELECT id_number FROM patients WHERE site_id <> %s ORDER BY id_number LIMIT %s",
        (3, 2),
    )


def test_sample_patient_keys_reports_empty_classes() -> None:
    with pytest.raises(RuntimeError, match="No authorized keys"):
        sample_patient_keys(
            FakeCursor(fetchall_rows=[[]]),
            PostgresBackend(),
            site_id=3,
            samples=1,
            visible=True,
        )


def test_sample_attribute_values_builds_authorized_and_unauthorized_queries() -> None:
    cur = FakeCursor(fetchall_rows=[[("111-11-1111",)], [("999-99-9999",)]])

    authorized = sample_attribute_values(cur, PostgresBackend(), "ssn", 3, 1, "authorized")
    unauthorized = sample_attribute_values(cur, PostgresBackend(), "ssn", 3, 1, "unauthorized")

    assert authorized == ["111-11-1111"]
    assert unauthorized == ["999-99-9999"]
    assert cur.calls[0] == (
        "SELECT ssn FROM patients WHERE site_id = %s ORDER BY id_number LIMIT %s",
        (3, 1),
    )
    assert "NOT EXISTS" in cur.calls[1][0]
    assert cur.calls[1][1] == (3, 3, 1)


def test_sample_attribute_values_rejects_bad_kind_unsafe_attribute_and_empty_result() -> None:
    with pytest.raises(ValueError, match="Unknown sampling kind"):
        sample_attribute_values(FakeCursor(), PostgresBackend(), "ssn", 3, 1, "other")
    with pytest.raises(ValueError, match="Unsafe identifier"):
        sample_attribute_values(FakeCursor(), PostgresBackend(), "ssn;drop", 3, 1, "authorized")
    with pytest.raises(RuntimeError, match="No authorized age values"):
        sample_attribute_values(
            FakeCursor(fetchall_rows=[[]]),
            PostgresBackend(),
            "age",
            3,
            1,
            "authorized",
        )


def test_load_attribute_value_pool_combines_authorized_unauthorized_and_absent_values() -> None:
    cur = FakeCursor(fetchall_rows=[[(20,), (21,)], [(99,)]])

    pool = load_attribute_value_pool(
        cur,
        PostgresBackend(),
        "age",
        site_id=3,
        samples=2,
        rng=random.Random(1),
        max_id=1000,
    )

    assert pool == AttributeValuePool(
        authorized_values=[20, 21],
        unauthorized_values=[99],
        nonexistent_values=[10_000_000, 10_000_001],
    )


def test_build_noise_configs_loads_one_pool_per_distinct_site(monkeypatch, tmp_path) -> None:
    from noise import workload

    users_file = tmp_path / "doctors.csv"
    users_file.write_text(
        "user_name,password,tenant_id\n"
        "alice,a,1\n"
        "bob,b,1\n"
        "carol,c,2\n",
        encoding="utf-8",
    )
    observed_sites: list[int] = []

    class FakeCursorContext:
        def __enter__(self) -> object:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class FakeAdmin:
        def cursor(self) -> FakeCursorContext:
            return FakeCursorContext()

    def fake_load_site_key_pool(_cur: object, _backend: object, site_id: int, samples: int) -> SiteKeyPool:
        observed_sites.append(site_id)
        return SiteKeyPool([site_id * 10 + samples], [site_id * 100 + samples])

    monkeypatch.setattr(workload, "load_site_key_pool", fake_load_site_key_pool)

    configs = workload.build_noise_configs(
        FakeAdmin(),
        PostgresBackend(),
        str(users_file),
        attacker_user="nobody",
        attacker_dsn="postgresql://old:old@localhost/rls",
        noise_clients=3,
        noise_pool_size=5,
        rng=random.Random(0),
    )

    assert sorted(observed_sites) == [1, 2]
    assert len(configs) == 3
    assert configs[0].key_pool in [
        SiteKeyPool([15], [105]),
        SiteKeyPool([25], [205]),
    ]


def test_build_noise_configs_validates_client_and_pool_counts() -> None:
    assert (
        __import__("noise.workload").workload.build_noise_configs(
            admin=object(),
            backend=PostgresBackend(),
            users_file="unused.csv",
            attacker_user="attacker",
            attacker_dsn="postgresql://old:old@localhost/rls",
            noise_clients=0,
            noise_pool_size=0,
            rng=random.Random(0),
        )
        == []
    )

    with pytest.raises(ValueError, match="noise-pool-size must be positive"):
        __import__("noise.workload").workload.build_noise_configs(
            admin=object(),
            backend=PostgresBackend(),
            users_file="unused.csv",
            attacker_user="attacker",
            attacker_dsn="postgresql://old:old@localhost/rls",
            noise_clients=1,
            noise_pool_size=0,
            rng=random.Random(0),
        )
