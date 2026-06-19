from __future__ import annotations

import argparse
from pathlib import Path

from util import build_enumerator_command


def attack_config_values(
    *,
    recover_ngrams: bool = True,
    prefix_query_mode: str = "span_prefix",
) -> tuple[dict[str, str], bool]:
    return (
        {
            ".attack.text_field_type": "search_as_you_type",
            ".attack.search_as_you_type_max_shingle_size": "4",
            ".attack.analyze_max_token_count": "1000000",
            ".attack.prefix_query_mode": prefix_query_mode,
            ".attack.chars": "abc",
            ".attack.exact_strategy": "optimized",
            ".attack.batch_size": "2048",
            ".attack.ngram_size": "4",
        },
        recover_ngrams,
    )


def patch_config(
    monkeypatch,
    values: dict[str, str],
    recover_ngrams: bool,
) -> None:
    def fake_config_value(config: Path, expression: str) -> str:
        del config
        return values[expression]

    def fake_config_bool(config: Path, expression: str) -> bool:
        del config
        assert expression == ".attack.recover_ngrams"
        return recover_ngrams

    monkeypatch.setattr(build_enumerator_command, "config_value", fake_config_value)
    monkeypatch.setattr(build_enumerator_command, "config_bool", fake_config_bool)


def test_build_command_uses_span_prefix_for_1grams_and_requests_ngram_recovery(
    monkeypatch,
) -> None:
    values, recover_ngrams = attack_config_values()
    patch_config(monkeypatch, values, recover_ngrams)

    args = argparse.Namespace(
        config="config/config.yml",
        arguments_only=True,
        python_bin="python3",
        backend="elasticsearch",
        corpus_file="dataset/enron_d1.jsonl",
        stats_file="results/stats.json",
        random_seed="7",
        progress_interval=None,
        attack_log_file=None,
        rich_progress=False,
        extra_args=[],
    )

    command = build_enumerator_command.build_command(args)

    assert command[command.index("--prefix-query-mode") + 1] == "span_prefix"
    assert "--recover-ngrams" in command
    assert command[command.index("--ngram-size") + 1] == "4"


def test_build_command_resolves_default_config_from_artifact_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    values, recover_ngrams = attack_config_values(recover_ngrams=False)
    observed_configs: list[Path] = []

    def fake_config_value(config: Path, expression: str) -> str:
        observed_configs.append(config)
        return values[expression]

    def fake_config_bool(config: Path, expression: str) -> bool:
        observed_configs.append(config)
        assert expression == ".attack.recover_ngrams"
        return recover_ngrams

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        build_enumerator_command,
        "resolve_artifact_path",
        lambda path: tmp_path / "dls" / path,
    )
    monkeypatch.setattr(build_enumerator_command, "config_value", fake_config_value)
    monkeypatch.setattr(build_enumerator_command, "config_bool", fake_config_bool)

    args = argparse.Namespace(
        config="config/config.yml",
        arguments_only=True,
        python_bin="python3",
        backend="opensearch",
        corpus_file="dataset/enron_d1.jsonl",
        stats_file="results/stats.json",
        random_seed="7",
        progress_interval=None,
        attack_log_file=None,
        rich_progress=False,
        extra_args=[],
    )

    build_enumerator_command.build_command(args)

    assert observed_configs
    assert set(observed_configs) == {tmp_path / "dls" / "config" / "config.yml"}


def test_build_command_full_entrypoint_uses_unfilter_dls(monkeypatch) -> None:
    values, recover_ngrams = attack_config_values(recover_ngrams=False)
    patch_config(monkeypatch, values, recover_ngrams)
    args = argparse.Namespace(
        config="config/config.yml",
        arguments_only=False,
        cli_bin="unfilter-dls",
        python_bin="python3",
        backend="opensearch",
        corpus_file="dataset/enron_d1.jsonl",
        stats_file="results/stats.json",
        random_seed="7",
        progress_interval=None,
        attack_log_file=None,
        rich_progress=False,
        extra_args=[],
    )

    command = build_enumerator_command.build_command(args)

    assert command[:2] == ["unfilter-dls", "enumerate"]


def test_build_command_omits_ngram_flags_when_disabled(monkeypatch) -> None:
    values, recover_ngrams = attack_config_values(recover_ngrams=False)
    patch_config(monkeypatch, values, recover_ngrams)
    args = argparse.Namespace(
        config="config/config.yml",
        arguments_only=True,
        python_bin="python3",
        backend="opensearch",
        corpus_file="dataset/enron_d1.jsonl",
        stats_file="results/stats.json",
        random_seed="7",
        progress_interval=None,
        attack_log_file=None,
        rich_progress=False,
        extra_args=[],
    )

    command = build_enumerator_command.build_command(args)

    assert "--recover-ngrams" not in command
    assert "--ngram-size" not in command


def test_build_command_appends_progress_logging_and_extra_args(monkeypatch) -> None:
    values, recover_ngrams = attack_config_values()
    patch_config(monkeypatch, values, recover_ngrams)
    args = argparse.Namespace(
        config="config/config.yml",
        arguments_only=True,
        python_bin="python3",
        backend="elasticsearch",
        corpus_file="dataset/enron_d1.jsonl",
        stats_file="results/stats.json",
        random_seed="7",
        progress_interval="0",
        attack_log_file="attack.log",
        rich_progress=True,
        extra_args=["--max-term-len", "12"],
    )

    command = build_enumerator_command.build_command(args)

    assert command[command.index("--progress-interval") + 1] == "0"
    assert command[command.index("--attack-log-file") + 1] == "attack.log"
    assert "--attack-progress-tty" in command
    assert "--rich-force-terminal" in command
    assert command[-2:] == ["--max-term-len", "12"]
