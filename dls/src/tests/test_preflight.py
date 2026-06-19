from __future__ import annotations

from pathlib import Path

import pytest

from util import preflight


class FakeConfig:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def value(self, expression: str) -> str:
        try:
            return self.values[expression]
        except KeyError as exc:
            raise preflight.ConfigError(f"missing {expression}") from exc


def reviewer_attack_config(prefix_query_mode: str = "span_prefix") -> FakeConfig:
    return FakeConfig(
        {
            ".attack.text_field_type": "search_as_you_type",
            ".attack.search_as_you_type_max_shingle_size": "4",
            ".attack.analyze_max_token_count": "1000000",
            ".attack.prefix_query_mode": prefix_query_mode,
            ".attack.chars": "abc123",
            ".attack.exact_strategy": "optimized",
            ".attack.batch_size": "2048",
            ".attack.recover_ngrams": "true",
            ".attack.ngram_size": "4",
        }
    )


def valid_reconstruction_config(**overrides: str) -> FakeConfig:
    values = {
        ".reconstruction.k": "4",
        ".reconstruction.source": "recovered",
        ".reconstruction.traversal": "euler",
    }
    values.update(overrides)
    return FakeConfig(values)


def test_parse_dataset_spec_normalizes_and_deduplicates() -> None:
    assert preflight.parse_dataset_spec("D1,d10 100,d1") == ["1", "10", "100"]


def test_parse_dataset_spec_rejects_unknown_dataset() -> None:
    with pytest.raises(ValueError, match="d1,d10,d100,d1000"):
        preflight.parse_dataset_spec("d5")


def test_parse_backends_normalizes_aliases_and_deduplicates() -> None:
    assert preflight.parse_backends(["elastic", "opensearch", "elasticsearch"]) == [
        "elasticsearch",
        "opensearch",
    ]


def test_parse_backends_defaults_to_both_backends() -> None:
    assert preflight.parse_backends([]) == ["opensearch", "elasticsearch"]


def test_doctor_defaults_paths_to_artifact_root(monkeypatch, tmp_path: Path) -> None:
    artifact_root = tmp_path / "dls"
    observed: dict[str, Path] = {}
    config_file = artifact_root / "config" / "config.yml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("attack: {}\nreconstruction: {}\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preflight, "artifact_root", lambda: artifact_root)
    monkeypatch.setattr(preflight, "require_command", lambda report, name: "/usr/bin/yq")

    def fake_check_required_files(
        report: preflight.CheckReport,
        *,
        root_dir: Path,
        config_file: Path,
        datasets: list[str],
    ) -> None:
        del report, datasets
        observed["root_dir"] = root_dir
        observed["config_file"] = config_file

    monkeypatch.setattr(preflight, "check_required_files", fake_check_required_files)
    monkeypatch.setattr(preflight, "check_corpus_files", lambda *args, **kwargs: None)
    monkeypatch.setattr(preflight, "check_python_environment", lambda *args, **kwargs: None)
    monkeypatch.setattr(preflight, "check_output_dirs", lambda report, output_dir: observed.setdefault("output_dir", output_dir))
    monkeypatch.setattr(preflight, "check_attack_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(preflight, "check_reconstruction_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(preflight, "check_backend_configs", lambda *args, **kwargs: [])
    monkeypatch.setattr(preflight, "check_backends", lambda *args, **kwargs: None)

    assert preflight.main(["--quiet", "--datasets", "1", "--backend", "opensearch"]) == 0

    assert observed["root_dir"] == artifact_root
    assert observed["config_file"] == artifact_root / "config" / "config.yml"
    assert observed["output_dir"] == artifact_root / "results" / "reviewer"


def test_backend_required_uses_existing_stats_unless_forced(tmp_path: Path) -> None:
    assert preflight.backend_required(
        tmp_path,
        "elasticsearch",
        ["1"],
        1,
        destructive_rerun=False,
    )

    stats_path = preflight.stats_file_for(tmp_path, "elasticsearch", "1", 1)
    stats_path.parent.mkdir(parents=True)
    stats_path.write_text("{}", encoding="utf-8")

    assert not preflight.backend_required(
        tmp_path,
        "elasticsearch",
        ["1"],
        1,
        destructive_rerun=False,
    )
    assert preflight.backend_required(
        tmp_path,
        "elasticsearch",
        ["1"],
        1,
        destructive_rerun=True,
    )


def test_reviewer_attack_config_accepts_span_prefix_unigrams() -> None:
    report = preflight.CheckReport(verbose=False)

    preflight.check_attack_config(report, reviewer_attack_config("span_prefix"))

    assert report.errors == []


def test_reviewer_attack_config_requires_span_prefix_for_recovered_ngrams() -> None:
    report = preflight.CheckReport(verbose=False)

    preflight.check_attack_config(
        report,
        reviewer_attack_config("match_phrase_prefix"),
    )

    assert any("span_prefix for 1-gram recovery" in error for error in report.errors)


def test_reviewer_attack_config_rejects_bad_ngram_size() -> None:
    config = reviewer_attack_config("span_prefix")
    config.values[".attack.ngram_size"] = "5"
    report = preflight.CheckReport(verbose=False)

    preflight.check_attack_config(report, config)

    assert any(".attack.ngram_size must be 2, 3, or 4" in error for error in report.errors)


def test_reconstruction_config_accepts_valid_defaults() -> None:
    report = preflight.CheckReport(verbose=False)

    preflight.check_reconstruction_config(report, valid_reconstruction_config())

    assert report.errors == []


def test_reconstruction_config_rejects_invalid_traversal() -> None:
    report = preflight.CheckReport(verbose=False)

    preflight.check_reconstruction_config(
        report,
        valid_reconstruction_config(**{".reconstruction.traversal": "random"}),
    )

    assert any(".reconstruction.traversal must be one of" in error for error in report.errors)


def test_output_dir_check_rejects_file_path(tmp_path: Path) -> None:
    output_path = tmp_path / "not-a-directory"
    output_path.write_text("content", encoding="utf-8")
    report = preflight.CheckReport(verbose=False)

    preflight.check_output_dirs(report, output_path)

    assert report.errors == [
        f"output path exists but is not a directory: {output_path}"
    ]


def test_corpus_check_reports_invalid_jsonl(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "enron_d1.jsonl").write_text('{"text": "ok"}\nnot-json\n')
    report = preflight.CheckReport(verbose=False)

    preflight.check_corpus_files(report, root_dir=tmp_path, datasets=["1"])

    assert any("invalid JSON" in error for error in report.errors)


def test_corpus_check_reports_empty_text(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    (dataset_dir / "enron_d1.jsonl").write_text('{"text": ""}\n', encoding="utf-8")
    report = preflight.CheckReport(verbose=False)

    preflight.check_corpus_files(report, root_dir=tmp_path, datasets=["1"])

    assert any("expected non-empty string text field" in error for error in report.errors)


def test_backend_config_reports_invalid_port_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = FakeConfig(
        {
            ".backends.elasticsearch.label": "Elasticsearch",
            ".backends.elasticsearch.compose_file": str(compose_file),
            ".backends.elasticsearch.service": "elasticsearch",
            ".backends.elasticsearch.env.ELASTICSEARCH_HOST": "localhost",
            ".backends.elasticsearch.env.ELASTICSEARCH_PORT": "9201",
            ".backends.elasticsearch.env.ELASTICSEARCH_SCHEME": "http",
            ".backends.elasticsearch.env.ELASTICSEARCH_VERIFY_CERTS": "false",
            ".backends.elasticsearch.env.ELASTICSEARCH_ADMIN_USERNAME": "elastic",
            ".backends.elasticsearch.env.ELASTICSEARCH_ADMIN_PASSWORD": "secret",
            ".backends.elasticsearch.env.ELASTICSEARCH_USER_PASSWORD": "secret",
            ".backends.elasticsearch.env.ELASTICSEARCH_USER_USERNAME": "user",
        }
    )
    report = preflight.CheckReport(verbose=False)
    monkeypatch.setenv("ELASTICSEARCH_PORT", "not-a-port")

    result = preflight.read_backend_config(
        report,
        config=config,
        root_dir=tmp_path,
        backend="elasticsearch",
    )

    assert result is None
    assert any("ELASTICSEARCH_PORT must be an integer" in error for error in report.errors)


def test_backend_config_applies_valid_environment_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    config = FakeConfig(
        {
            ".backends.opensearch.label": "OpenSearch",
            ".backends.opensearch.compose_file": str(compose_file),
            ".backends.opensearch.service": "opensearch",
            ".backends.opensearch.env.OPENSEARCH_HOST": "localhost",
            ".backends.opensearch.env.OPENSEARCH_PORT": "9200",
            ".backends.opensearch.env.OPENSEARCH_SCHEME": "https",
            ".backends.opensearch.env.OPENSEARCH_VERIFY_CERTS": "false",
            ".backends.opensearch.env.OPENSEARCH_ADMIN_USERNAME": "admin",
            ".backends.opensearch.env.OPENSEARCH_ADMIN_PASSWORD": "secret",
            ".backends.opensearch.env.OPENSEARCH_USER_PASSWORD": "secret",
        }
    )
    report = preflight.CheckReport(verbose=False)
    monkeypatch.setenv("OPENSEARCH_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENSEARCH_PORT", "19200")
    monkeypatch.setenv("OPENSEARCH_SCHEME", "http")
    monkeypatch.setenv("OPENSEARCH_VERIFY_CERTS", "true")

    result = preflight.read_backend_config(
        report,
        config=config,
        root_dir=tmp_path,
        backend="opensearch",
    )

    assert result is not None
    assert result.host == "127.0.0.1"
    assert result.port == 19200
    assert result.scheme == "http"
    assert result.verify_certs is True
    assert report.errors == []


def test_doctor_report_prints_rich_table(capsys) -> None:
    report = preflight.CheckReport(verbose=True)
    report.ok("Python dependencies import successfully")
    report.warn(".attack.analyze_max_token_count is 0; backend default will apply")
    report.fail("missing required file: /tmp/missing")

    report.print_table()

    output = capsys.readouterr().out
    assert "DLS Artifact Doctor" in output
    assert "Check" in output
    assert "Status" in output
    assert "Detail" in output
    assert "OK" in output
    assert "WARN" in output
    assert "FAIL" in output
