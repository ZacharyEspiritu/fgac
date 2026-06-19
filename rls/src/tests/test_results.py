from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from rls_artifact.results import _resolve_manifest, run_results_command


def _make_project_root(path: Path) -> Path:
    (path / "pyproject.toml").write_text("[project]\nname = 'test-rls'\n", encoding="utf-8")
    (path / "orchestration").mkdir()
    return path


def _write_manifest(root: Path, run_id: str) -> Path:
    manifest = root / "results" / "runs" / run_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "\n".join(
            [
                f"run_id: {run_id}",
                "status: success",
                "claims:",
                "  - C-R6",
                "runner: same-zone",
                "command: bash orchestration/run_samezone_exps.sh --experiments 6",
                "config: orchestration/config/shared_config.yml",
                "started_at: '2026-01-01T00:00:00Z'",
                "finished_at: '2026-01-01T01:00:00Z'",
                "git_commit: abc123",
                "git_dirty: false",
                "outputs:",
                "  - results/table3/review-run/table3.tex",
                "published_outputs:",
                "  - results/table3/table3.tex",
                "notes: []",
            ]
        ),
        encoding="utf-8",
    )
    return manifest


def test_resolve_manifest_accepts_run_id_manifest_path_and_join_output_path(tmp_path) -> None:
    root = _make_project_root(tmp_path)
    manifest = _write_manifest(root, "review-run")

    assert _resolve_manifest(root, Path("review-run")) == manifest
    assert _resolve_manifest(root, Path("results/runs/review-run/manifest.yml")) == manifest
    assert _resolve_manifest(root, Path("results/table3/join-review-run")) == manifest


def test_results_command_lists_and_inspects_manifests(tmp_path, monkeypatch, capsys) -> None:
    root = _make_project_root(tmp_path)
    monkeypatch.chdir(root)
    _write_manifest(root, "review-run")

    assert run_results_command(Namespace(results_command="list")) == 0
    list_output = capsys.readouterr().out
    assert "RLS Artifact Runs" in list_output
    assert "review-run" in list_output
    assert "success" in list_output

    assert run_results_command(
        Namespace(results_command="inspect", path="results/table3/join-review-run", raw=False)
    ) == 0
    inspect_output = capsys.readouterr().out
    assert "Run Summary" in inspect_output
    assert "review-run" in inspect_output
    assert "results/runs/review-run/manifest.yml" in inspect_output
