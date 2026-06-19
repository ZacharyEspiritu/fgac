from __future__ import annotations

from argparse import Namespace
import random

import pytest

from noise import workload
from patients.credentials import UserCred, load_user_creds
from patients.sampling import SiteKeyPool, make_nonexistent_value, sample_nonexistent_values


def test_load_user_creds_supports_headers_site_alias_and_headerless_csv(tmp_path) -> None:
    header_path = tmp_path / "doctors.csv"
    header_path.write_text(
        "user_name,password,site_id\nalice,secret,1\nbob,pw,2\n",
        encoding="utf-8",
    )
    headerless_path = tmp_path / "headerless.csv"
    headerless_path.write_text("carol,pw3,3\n", encoding="utf-8")

    assert load_user_creds(str(header_path)) == [
        UserCred("alice", "secret", "1"),
        UserCred("bob", "pw", "2"),
    ]
    assert load_user_creds(str(headerless_path)) == [UserCred("carol", "pw3", "3")]


def test_make_nonexistent_value_generates_attribute_specific_values() -> None:
    assert make_nonexistent_value("id_number", 5, 1000) == 2005
    assert make_nonexistent_value("age", 5, 1000) == 10_000_005
    assert make_nonexistent_value("ssn", 5, 1000) == "NX-NX-0000005"
    assert make_nonexistent_value("zip_code", 5, 1000) == "Z000000005"
    assert make_nonexistent_value("name", 5, 1000) == "__NONEXIST_0000000005"

    with pytest.raises(ValueError, match="Unsupported attribute"):
        make_nonexistent_value("blood_type", 1, 1000)


def test_sample_nonexistent_values_for_non_ssn_does_not_query_database() -> None:
    class ExplodingCursor:
        def execute(self, *_args: object) -> None:
            raise AssertionError("non-ssn generation should not query")

    assert sample_nonexistent_values(ExplodingCursor(), "age", 3, random.Random(1), 100) == [
        10_000_000,
        10_000_001,
        10_000_002,
    ]


class ExistingSsnCursor:
    def __init__(self, present: set[str]) -> None:
        self.present = present
        self.last_candidates: list[str] = []

    def execute(self, _query: str, params: tuple[list[str]]) -> None:
        self.last_candidates = params[0]

    def fetchall(self) -> list[tuple[str]]:
        return [(value,) for value in self.last_candidates if value in self.present]


def test_sample_nonexistent_values_for_ssn_avoids_present_values() -> None:
    cursor = ExistingSsnCursor(present={"864-49-6890"})

    values = sample_nonexistent_values(cursor, "ssn", 2, random.Random(0), 100)

    assert len(values) == 2
    assert all(value not in cursor.present for value in values)
    assert all(len(value) == len("000-00-0000") for value in values)


def test_replace_dsn_credentials_encodes_user_password_and_preserves_ipv6() -> None:
    dsn = "postgresql://old:old@[::1]:5432/rls?sslmode=disable"

    replaced = workload.replace_dsn_credentials(dsn, "user name", "p@ss/word")

    assert replaced == "postgresql://user%20name:p%40ss%2Fword@[::1]:5432/rls?sslmode=disable"


def test_replace_dsn_credentials_rejects_non_url_dsn() -> None:
    with pytest.raises(ValueError, match="URL-style DSN"):
        workload.replace_dsn_credentials("dbname=rls user=alice", "bob", "pw")


def test_noise_arg_validation_and_ratios() -> None:
    args = Namespace(
        noise_clients=2,
        noise_authorized_ratio=0.8,
        noise_unauthorized_ratio=0.1,
        noise_nonexistent_ratio=0.1,
    )
    assert workload.validate_noise_args(args) == [
        ("authorized", 0.8),
        ("unauthorized", 0.1),
        ("nonexistent", 0.1),
    ]

    args.noise_clients = -1
    with pytest.raises(ValueError, match="must be >= 0"):
        workload.validate_noise_args(args)

    args.noise_clients = 1
    args.noise_authorized_ratio = args.noise_unauthorized_ratio = args.noise_nonexistent_ratio = 0.0
    with pytest.raises(ValueError, match="At least one noise ratio"):
        workload.validate_noise_args(args)

    args.noise_authorized_ratio = -0.1
    with pytest.raises(ValueError, match="Noise ratios must be >= 0"):
        workload.validate_noise_args(args)


def test_noise_key_selection_counter_and_latency_helpers() -> None:
    rng = random.Random(0)
    key_pool = SiteKeyPool(authorized_keys=[1, 2], unauthorized_keys=[9, 10])

    assert workload.select_noise_key(rng, "authorized", key_pool, 100, 1000) in {1, 2}
    assert workload.select_noise_key(rng, "unauthorized", key_pool, 100, 1000) in {9, 10}
    assert 1100 <= workload.select_noise_key(rng, "nonexistent", key_pool, 100, 1000) < 1_001_100
    assert workload.empty_noise_counter() == {
        "authorized": 0,
        "unauthorized": 0,
        "nonexistent": 0,
    }
    assert workload.aggregate_noise_latency(
        {"authorized": 2, "unauthorized": 0},
        {"authorized": 100, "unauthorized": 10},
    ) == {"authorized": 50.0, "unauthorized": 0.0}


def test_choose_noise_users_prefers_distinct_sites_and_skips_attacker(tmp_path) -> None:
    users_file = tmp_path / "doctors.csv"
    users_file.write_text(
        "user_name,password,tenant_id\n"
        "attacker,pw,1\n"
        "alice,a,1\n"
        "bob,b,2\n"
        "carol,c,2\n",
        encoding="utf-8",
    )

    selected = workload.choose_noise_users(
        str(users_file),
        "attacker",
        2,
        "postgresql://old:old@localhost/rls",
        random.Random(2),
    )

    assert {site_id for _user, _pw, site_id, _dsn in selected} == {1, 2}
    assert all(user != "attacker" for user, _pw, _site_id, _dsn in selected)
    assert all(dsn.startswith(f"postgresql://{user}:") for user, _pw, _site_id, dsn in selected)


def test_choose_noise_users_reports_insufficient_or_missing_site_ids(tmp_path) -> None:
    users_file = tmp_path / "doctors.csv"
    users_file.write_text("user_name,password,tenant_id\nattacker,pw,1\nalice,a,1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="only found 1 other users"):
        workload.choose_noise_users(
            str(users_file),
            "attacker",
            2,
            "postgresql://old:old@localhost/rls",
            random.Random(1),
        )

    missing_site_file = tmp_path / "missing_site.csv"
    missing_site_file.write_text("user_name,password\nalice,a\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing site_id"):
        workload.choose_noise_users(
            str(missing_site_file),
            "attacker",
            1,
            "postgresql://old:old@localhost/rls",
            random.Random(1),
        )
