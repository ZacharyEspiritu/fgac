from __future__ import annotations

from pathlib import Path

from rls_artifact.manifest import finish_manifest, write_start_manifest
from rls_artifact.yaml_io import load_yaml


def _make_project_root(path: Path) -> Path:
    (path / "pyproject.toml").write_text("[project]\nname = 'test-rls'\n", encoding="utf-8")
    (path / "orchestration").mkdir()
    return path


def test_write_start_and_finish_manifest_discovers_outputs(tmp_path, monkeypatch) -> None:
    root = _make_project_root(tmp_path)
    monkeypatch.chdir(root)
    manifest = root / "results" / "runs" / "review-run" / "manifest.yml"

    write_start_manifest(
        path=manifest,
        run_id="review-run",
        runner="same-zone",
        config="orchestration/config/shared_config.yml",
        command_line="bash orchestration/run_samezone_exps.sh --experiments 6",
        claims=("C-R6",),
        env_entries=("CONFIG=shared.yml", "IGNORED", "EMPTY="),
    )

    started = load_yaml(manifest)
    assert isinstance(started, dict)
    assert started["status"] == "running"
    assert started["claims"] == ["C-R6"]
    assert started["environment"] == {"CONFIG": "shared.yml", "EMPTY": ""}

    run_output_dir = root / "results" / "table3" / "review-run"
    run_output_dir.mkdir(parents=True)
    (run_output_dir / "table3.tex").write_text("run table", encoding="utf-8")
    (run_output_dir / "ignored.bin").write_bytes(b"not a published artifact")
    published_output = root / "results" / "table3" / "table3.tex"
    published_output.write_text("published table", encoding="utf-8")

    finish_manifest(
        path=manifest,
        run_id="review-run",
        status="success",
        notes=("completed",),
    )

    finished = load_yaml(manifest)
    assert isinstance(finished, dict)
    assert finished["status"] == "success"
    assert finished["outputs"] == ["results/table3/review-run/table3.tex"]
    assert finished["published_outputs"] == ["results/table3/table3.tex"]
    assert finished["notes"] == ["completed"]
    assert isinstance(finished["finished_at"], str)
