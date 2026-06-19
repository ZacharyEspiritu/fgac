from __future__ import annotations

import yaml

import pytest

from rls_artifact.claims import (
    ConfigOverrideError,
    _materialize_config_overrides,
)
from rls_artifact.cli import build_parser


def test_claim_run_accepts_repeatable_config_overrides() -> None:
    args = build_parser().parse_args(
        [
            "claims",
            "run",
            "C-R6",
            "--set",
            "singleattr.reps",
            "10",
            "--set",
            "singleattr.run_linear",
            "false",
        ]
    )

    assert args.config_overrides == [
        ["singleattr.reps", "10"],
        ["singleattr.run_linear", "false"],
    ]


def test_materialize_config_overrides_writes_yaml_scalars(tmp_path) -> None:
    base_config = tmp_path / "shared_config.yml"
    base_config.write_text(
        "\n".join(
            [
                "project: test-project",
                "singleattr:",
                "  reps: 1",
                "  run_linear: true",
                "tuplext:",
                "  reps: 1",
                "  orderings: sza,saz",
            ]
        ),
        encoding="utf-8",
    )

    generated = _materialize_config_overrides(
        root=tmp_path,
        base_config=base_config,
        overrides=(
            ("singleattr.reps", "10"),
            ("singleattr.run_linear", "false"),
            ("tuplext.reps", "3"),
        ),
        run_id="review-run",
    )

    data = yaml.safe_load(generated.read_text(encoding="utf-8"))
    assert generated.parent == tmp_path / "results" / "config-overrides"
    assert data["project"] == "test-project"
    assert data["singleattr"]["reps"] == 10
    assert data["singleattr"]["run_linear"] is False
    assert data["tuplext"]["reps"] == 3
    assert data["tuplext"]["orderings"] == "sza,saz"


def test_materialize_config_overrides_rejects_non_scalar_values(tmp_path) -> None:
    base_config = tmp_path / "shared_config.yml"
    base_config.write_text("singleattr:\n  reps: 1\n", encoding="utf-8")

    with pytest.raises(ConfigOverrideError, match="only supports scalar"):
        _materialize_config_overrides(
            root=tmp_path,
            base_config=base_config,
            overrides=(("singleattr.reps", "[1, 2]"),),
            run_id="bad-run",
        )


def test_materialize_config_overrides_rejects_path_through_scalar(tmp_path) -> None:
    base_config = tmp_path / "shared_config.yml"
    base_config.write_text("singleattr: 1\n", encoding="utf-8")

    with pytest.raises(ConfigOverrideError, match="is not a mapping"):
        _materialize_config_overrides(
            root=tmp_path,
            base_config=base_config,
            overrides=(("singleattr.reps", "10"),),
            run_id="bad-run",
        )
