from __future__ import annotations

from pathlib import Path

from util import paths


def test_resolve_artifact_path_prefers_existing_cwd_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    local_file = tmp_path / "config" / "config.yml"
    local_file.parent.mkdir()
    local_file.write_text("local: true\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "artifact_root", lambda: tmp_path / "dls")

    assert paths.resolve_artifact_path("config/config.yml") == Path("config/config.yml")


def test_resolve_artifact_path_falls_back_to_artifact_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "dls"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "artifact_root", lambda: artifact_root)

    assert paths.resolve_artifact_path("config/config.yml") == (
        artifact_root / "config" / "config.yml"
    )
