from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from rls_artifact import doctor, manifest, results, yaml_io
from rls_artifact.doctor import Check


def make_project_root(path: Path) -> Path:
    (path / "pyproject.toml").write_text("[project]\nname = 'rls-test'\n", encoding="utf-8")
    (path / "orchestration").mkdir()
    return path


def test_manifest_discovers_join_inline_and_file_outputs_with_allowed_suffixes(tmp_path, monkeypatch) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)
    (root / "results" / "table3" / "join-review").mkdir(parents=True)
    (root / "results" / "table3" / "join-review" / "table3.tex").write_text("tex", encoding="utf-8")
    (root / "results" / "table3" / "join-review" / "ignored.bin").write_bytes(b"binary")
    (root / "results" / "table4" / "inline-review").mkdir(parents=True)
    (root / "results" / "table4" / "inline-review" / "table4.pdf").write_bytes(b"pdf")
    (root / "results" / "dbsize").mkdir(parents=True)
    (root / "results" / "dbsize" / "review").write_text("summary", encoding="utf-8")

    assert manifest._discover_run_outputs(root, "review") == [
        "results/dbsize/review",
        "results/table3/join-review/table3.tex",
        "results/table4/inline-review/table4.pdf",
    ]


def test_manifest_published_outputs_env_entries_git_and_cli(tmp_path, monkeypatch) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)

    assert manifest._published_outputs_for_claims(["C-R6", "missing", "C-R6"]) == [
        "results/table3/table3.tex"
    ]
    assert manifest._parse_env_entries(["A=1", "bad", "=missing", "A=2", "EMPTY="]) == {
        "A": "2",
        "EMPTY": "",
    }

    monkeypatch.setattr(manifest, "_run_git", lambda _root, *args: "")
    assert manifest._git_dirty(root) is False
    monkeypatch.setattr(manifest, "_run_git", lambda _root, *args: " M file.py")
    assert manifest._git_dirty(root) is True
    monkeypatch.setattr(manifest, "_run_git", lambda _root, *args: "unknown")
    assert manifest._git_dirty(root) == "unknown"

    out = root / "results" / "runs" / "cli-run" / "manifest.yml"
    parser = manifest.build_parser()
    args = parser.parse_args(
        [
            "start",
            "--path",
            str(out),
            "--run-id",
            "cli-run",
            "--runner",
            "same-zone",
            "--config",
            "shared.yml",
            "--command-line",
            "bash run.sh",
            "--claim",
            "C-R6",
            "--env",
            "CONFIG=shared.yml",
        ]
    )
    monkeypatch.setattr(manifest, "build_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    assert manifest.main() == 0
    assert "run_id: cli-run" in out.read_text(encoding="utf-8")

    finish_args = parser.parse_args(
        ["finish", "--path", str(out), "--run-id", "cli-run", "--status", "failed", "--note", "boom"]
    )
    monkeypatch.setattr(manifest, "build_parser", lambda: SimpleNamespace(parse_args=lambda: finish_args))
    assert manifest.main() == 0
    loaded = yaml_io.load_yaml(out)
    assert isinstance(loaded, dict)
    assert loaded["status"] == "failed"
    assert loaded["notes"] == ["boom"]


def test_results_raw_inspect_missing_manifest_empty_list_and_helpers(tmp_path, monkeypatch, capsys) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)

    assert results.run_results_command(Namespace(results_command="list")) == 0
    assert "No run manifests found" in capsys.readouterr().out

    assert results.inspect_result("missing-run", raw=False) == 2
    assert "Could not find a manifest" in capsys.readouterr().out

    manifest_path = root / "results" / "runs" / "review" / "manifest.yml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("run_id: review\nclaims:\n  - C-R6\n", encoding="utf-8")

    assert results.inspect_result("review", raw=True) == 0
    assert "run_id" in capsys.readouterr().out
    assert results._possible_run_ids(Path("results/table3/join-review")) == [
        "join-review",
        "review",
    ]
    assert results._possible_run_ids(Path("results/table3/inline-review")) == [
        "inline-review",
        "review",
    ]
    assert results._git_summary({"git_commit": "abc", "git_dirty": True}) == "abc (dirty)"
    assert results._git_summary({"git_commit": "abc", "git_dirty": False}) == "abc (clean)"
    assert results._git_summary({"git_dirty": "unknown"}) == "unknown"
    assert results._string_field({"x": 1}, "x", "default") == "default"
    assert results._string_list_field({"x": ["a", 1, "b"]}, "x") == ["a", "b"]


def test_results_command_rejects_unknown_subcommand() -> None:
    with pytest.raises(AssertionError, match="unhandled results command"):
        results.run_results_command(Namespace(results_command="bad"))


def test_doctor_gcloud_checks_and_run_stdout(monkeypatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/gcloud" if name == "gcloud" else None)

    def fake_run_stdout(*cmd: str) -> str:
        if cmd[:3] == ("gcloud", "auth", "list"):
            return "alice@example.com\n"
        if cmd[:3] == ("gcloud", "config", "get-value"):
            return "artifact-project\n"
        return ""

    monkeypatch.setattr(doctor, "_run_stdout", fake_run_stdout)
    assert doctor._check_gcloud() == [
        Check("GCloud account", "OK", "alice@example.com"),
        Check("GCloud project", "OK", "artifact-project"),
    ]

    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    assert doctor._check_gcloud() == [Check("Tool gcloud", "FAIL", "not found on PATH")]

    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/bin/gcloud")
    monkeypatch.setattr(doctor, "_run_stdout", lambda *cmd: "")
    assert doctor._check_gcloud() == [
        Check("GCloud account", "FAIL", "no active account"),
        Check("GCloud project", "FAIL", "no active project"),
    ]


def test_run_stdout_handles_failures_and_timeouts(monkeypatch) -> None:
    class FailingSubprocess:
        PIPE = object()
        DEVNULL = object()
        TimeoutExpired = doctor.subprocess.TimeoutExpired

        @staticmethod
        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout="ignored")

    monkeypatch.setattr(doctor, "subprocess", FailingSubprocess)
    assert doctor._run_stdout("gcloud") == ""

    class RaisingSubprocess(FailingSubprocess):
        @staticmethod
        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            raise OSError("missing")

    monkeypatch.setattr(doctor, "subprocess", RaisingSubprocess)
    assert doctor._run_stdout("gcloud") == ""


def test_yaml_io_round_trips_ascii_yaml(tmp_path) -> None:
    path = tmp_path / "manifest.yml"
    text = yaml_io.dump_yaml({"b": 2, "a": ["x", "y"]})
    path.write_text(text, encoding="utf-8")

    assert text.startswith("b: 2\n")
    assert yaml_io.load_yaml(path) == {"b": 2, "a": ["x", "y"]}
