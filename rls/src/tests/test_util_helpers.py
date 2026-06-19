from __future__ import annotations

import argparse
import json
import random

import pytest

from util import args as util_args
from util import io as util_io
from util import random_utils, sql_utils, timing


def test_db_connection_arg_helpers_add_required_options() -> None:
    parser = argparse.ArgumentParser()
    util_args.add_db_connection_args(parser)
    util_args.add_seed_arg(parser, default=42)
    util_args.add_fast_arg(parser, help="fast mode")
    util_args.add_warm_cache_arg(parser, help="warm cache")
    util_args.add_probes_arg(parser, default=7, help="probe count")

    parsed = parser.parse_args(
        [
            "--admin-dsn",
            "postgresql://admin/db",
            "--attacker-dsn",
            "postgresql://attacker/db",
            "--attacker-user",
            "doctor_s1",
            "--fast",
            "--warm-cache",
        ]
    )

    assert parsed.admin_dsn == "postgresql://admin/db"
    assert parsed.attacker_dsn == "postgresql://attacker/db"
    assert parsed.attacker_user == "doctor_s1"
    assert parsed.seed == 42
    assert parsed.fast is True
    assert parsed.warm_cache is True
    assert parsed.probes == 7


def test_require_positive_accepts_positive_values_and_rejects_zero() -> None:
    util_args.require_positive(1, "samples")

    with pytest.raises(ValueError, match="samples must be positive"):
        util_args.require_positive(0, "samples")


def test_parse_optional_csv_ints_treats_blank_as_empty() -> None:
    assert util_args.parse_optional_csv_ints("  ", "experiments") == []
    assert util_args.parse_optional_csv_ints("1, 2,,3", "experiments") == [1, 2, 3]


def test_parse_csv_ints_validates_content_and_positivity() -> None:
    assert util_args.parse_csv_ints(" 3, -2, 0 ", "offsets", require_positive=False) == [
        3,
        -2,
        0,
    ]

    with pytest.raises(ValueError, match="must contain at least one integer"):
        util_args.parse_csv_ints(",,", "offsets")
    with pytest.raises(ValueError, match="must contain positive integers"):
        util_args.parse_csv_ints("1,0", "offsets")


def test_parse_csv_strings_trims_and_rejects_empty_values() -> None:
    assert util_args.parse_csv_strings("age, ssn,, zip_code ", "attributes") == [
        "age",
        "ssn",
        "zip_code",
    ]

    with pytest.raises(ValueError, match="attributes must contain at least one value"):
        util_args.parse_csv_strings(" , ", "attributes")


def test_io_helpers_create_parent_directories_and_round_trip_files(tmp_path) -> None:
    text_path = tmp_path / "nested" / "note.txt"
    json_path = tmp_path / "nested" / "payload.json"
    csv_path = tmp_path / "nested" / "rows.csv"

    util_io.write_text(str(text_path), "hello")
    util_io.write_json(str(json_path), {"b": 2, "a": 1}, sort_keys=True)
    util_io.write_csv(str(csv_path), [(1, "alice"), (2, "bob")], header=("id", "name"))

    assert text_path.read_text(encoding="utf-8") == "hello"
    assert json.loads(json_path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert util_io.load_csv(str(csv_path)) == [["id", "name"], ["1", "alice"], ["2", "bob"]]


def test_choose_weighted_uses_cumulative_weights() -> None:
    rng = random.Random(7)
    choices = [random_utils.choose_weighted(rng, [("a", 1.0), ("b", 3.0)]) for _ in range(5)]

    assert choices == ["b", "a", "b", "a", "b"]


def test_validate_identifier_accepts_sql_identifiers_and_rejects_unsafe_names() -> None:
    sql_utils.validate_identifier("_safe_name9")

    for value in ("9bad", "has-dash", "name; drop table patients"):
        with pytest.raises(ValueError, match="Unsafe identifier"):
            sql_utils.validate_identifier(value)


class FakeTimedCursor:
    def __init__(self, rows: list[tuple[int, ...]], one: tuple[int, ...] | None = None) -> None:
        self.rows = rows
        self.one = one
        self.calls: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = None) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[tuple[int, ...]]:
        return self.rows

    def fetchone(self) -> tuple[int, ...] | None:
        return self.one


def test_timed_query_counts_fetchall_and_fetchone(monkeypatch) -> None:
    ticks = iter([100, 175, 300, 420])
    monkeypatch.setattr(timing, "now_ns", lambda: next(ticks))
    cur = FakeTimedCursor(rows=[(1,), (2,)], one=(1,))

    assert timing.timed_query(cur, "SELECT * FROM t", ("p",)) == (75, 2)
    assert timing.timed_query(cur, "SELECT 1", fetch_one=True) == (120, 1)
    assert cur.calls == [("SELECT * FROM t", ("p",)), ("SELECT 1", None)]


def test_timed_query_fetchone_counts_absent_row_as_zero(monkeypatch) -> None:
    ticks = iter([10, 12])
    monkeypatch.setattr(timing, "now_ns", lambda: next(ticks))
    cur = FakeTimedCursor(rows=[], one=None)

    assert timing.timed_query(cur, "SELECT 1", fetch_one=True) == (2, 0)
