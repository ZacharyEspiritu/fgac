from __future__ import annotations

import pytest

from util.db_backend import PostgresBackend
from util.db_utils import fetch_all, fetch_one, fetch_optional_scalar, fetch_optional_value


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = ()) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> tuple[object, ...] | None:
        if not self.rows:
            return None
        return self.rows.pop(0)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


def test_postgres_backend_limit_count_filter_and_explain() -> None:
    backend = PostgresBackend()
    cur = FakeCursor(rows=[("plan line 1",), ("plan line 2",)])

    assert backend.add_limit("SELECT * FROM patients", None) == ("SELECT * FROM patients", ())
    assert backend.add_limit("SELECT * FROM patients", 5) == (
        "SELECT * FROM patients LIMIT %s",
        (5,),
    )
    assert backend.count_filter("site_id = 1") == "COUNT(*) FILTER (WHERE site_id = 1)"
    assert list(backend.explain(cur, "SELECT * FROM patients WHERE id = %s", (10,)) or []) == [
        "plan line 1",
        "plan line 2",
    ]
    assert cur.calls == [("EXPLAIN ANALYZE SELECT * FROM patients WHERE id = %s", (10,))]


def test_apply_rls_policy_updates_existing_tables_and_skips_missing_tables() -> None:
    backend = PostgresBackend()
    cur = FakeCursor(rows=[("patients",), (None,)])

    backend.apply_rls_policy(cur, "JOIN", ["patients", "missing_table"])

    queries = [query for query, _params in cur.calls]
    assert queries == [
        "SELECT to_regclass(%s)",
        "ALTER TABLE patients ENABLE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS doctor_read ON patients",
        "CREATE POLICY doctor_read ON patients FOR SELECT USING (site_policy_join(site_id, current_user))",
        "SELECT to_regclass(%s)",
    ]


def test_apply_rls_policy_rejects_unknown_policy_and_unsafe_table() -> None:
    backend = PostgresBackend()

    with pytest.raises(ValueError, match="Unknown RLS policy"):
        backend.apply_rls_policy(FakeCursor(), "bad", ["patients"])
    with pytest.raises(ValueError, match="Unsafe identifier"):
        backend.apply_rls_policy(FakeCursor(), "join", ["patients;drop"])


def test_db_utils_fetch_helpers_return_values_and_rows() -> None:
    cur = FakeCursor(rows=[(12,), (13,)])

    assert fetch_optional_value(cur, "SELECT one", ("x",)) == 12
    assert fetch_optional_scalar(cur, "SELECT scalar") == 13

    cur = FakeCursor(rows=[(1,), (2,)])
    assert fetch_all(cur, "SELECT all") == [(1,), (2,)]
    assert cur.calls == [("SELECT all", ())]


def test_db_utils_fetch_helpers_validate_missing_and_non_integer_values() -> None:
    assert fetch_optional_value(FakeCursor(), "SELECT none") is None
    assert fetch_optional_scalar(FakeCursor(), "SELECT none") is None

    with pytest.raises(TypeError, match="Expected scalar value to be an integer"):
        fetch_optional_scalar(FakeCursor(rows=[(True,)]), "SELECT bool")
    with pytest.raises(RuntimeError, match="query returned none"):
        fetch_one(FakeCursor(), "SELECT none")
    with pytest.raises(RuntimeError, match="query returned NULL"):
        fetch_one(FakeCursor(rows=[(None,)]), "SELECT null")
    with pytest.raises(TypeError, match="Expected scalar value to be an integer"):
        fetch_one(FakeCursor(rows=[("not-int",)]), "SELECT text")


def test_fetch_one_returns_integer_scalar() -> None:
    assert fetch_one(FakeCursor(rows=[(99,)]), "SELECT count") == 99
