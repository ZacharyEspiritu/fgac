from __future__ import annotations

from argparse import Namespace

import pytest

from reconstruction.config_loader import (
    DEFAULT_RECONSTRUCTION_CONFIG,
    _as_json_object,
    _as_json_value,
    _resolve_config_path,
    load_reconstruction_config,
)
from reconstruction.config_options import (
    bool_option,
    int_option,
    optional_string_option,
    string_option,
)
from reconstruction.config_loader import ReconstructionConfig


def make_config(**options: object) -> ReconstructionConfig:
    return ReconstructionConfig(path="config.yml", options=dict(options), candidates={})


def test_load_reconstruction_config_splits_options_and_candidates(tmp_path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        "\n".join(
            [
                "table: patients",
                "verify: true",
                "candidates:",
                "  age:",
                "    range:",
                "      start: 18",
                "      end: 20",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_reconstruction_config(str(config_path))

    assert loaded.path == str(config_path)
    assert loaded.options == {"table": "patients", "verify": True}
    assert loaded.candidates == {"age": {"range": {"start": 18, "end": 20}}}


def test_load_reconstruction_config_rejects_missing_candidates_mapping(tmp_path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text("table: patients\n", encoding="utf-8")

    with pytest.raises(ValueError, match="top-level candidates mapping"):
        load_reconstruction_config(str(config_path))


def test_resolve_config_path_falls_back_to_packaged_default() -> None:
    path = _resolve_config_path(DEFAULT_RECONSTRUCTION_CONFIG)

    assert path.is_file()
    assert path.name == "singleattr_binary.yml"


def test_json_normalization_rejects_non_string_keys_and_unsupported_values() -> None:
    assert _as_json_value({"a": [1, None, True]}, "config") == {"a": [1, None, True]}

    with pytest.raises(ValueError, match="YAML key at config must be a string"):
        _as_json_object({1: "bad"}, "config")
    with pytest.raises(ValueError, match="Unsupported YAML value"):
        _as_json_value(object(), "config.value")


def test_config_options_prefer_cli_values_then_config_then_defaults() -> None:
    namespace = Namespace(table="cli_table", optional=None, reps=None, verify=None)
    config = make_config(table="config_table", optional="from-config", reps=3, verify=True)

    assert string_option(namespace, config, "table", "default_table") == "cli_table"
    assert optional_string_option(namespace, config, "optional") == "from-config"
    assert int_option(namespace, config, "reps", 1) == 3
    assert bool_option(namespace, config, "verify", False) is True

    empty_config = make_config()
    assert string_option(Namespace(table=None), empty_config, "table", "default_table") == "default_table"
    assert optional_string_option(Namespace(optional=None), empty_config, "optional") is None
    assert int_option(Namespace(reps=None), empty_config, "reps", 5) == 5
    assert bool_option(Namespace(verify=None), empty_config, "verify", False) is False


def test_config_option_helpers_validate_config_value_types() -> None:
    with pytest.raises(ValueError, match="must be a string"):
        string_option(Namespace(table=None), make_config(table=1), "table", "patients")
    with pytest.raises(ValueError, match="must be a string"):
        optional_string_option(Namespace(attr=None), make_config(attr=False), "attr")
    with pytest.raises(ValueError, match="must be an integer"):
        int_option(Namespace(reps=None), make_config(reps=True), "reps", 1)
    with pytest.raises(ValueError, match="must be true or false"):
        bool_option(Namespace(verify=None), make_config(verify="yes"), "verify", False)


def test_config_option_helpers_coerce_cli_values() -> None:
    config = make_config(table="config_table", reps=3, verify=False)

    assert string_option(Namespace(table=123), config, "table", "default") == "123"
    assert int_option(Namespace(reps="10"), config, "reps", 1) == 10
    assert bool_option(Namespace(verify=1), config, "verify", False) is True
