from __future__ import annotations

from rls_artifact.paths import find_project_root, looks_like_project_root


def test_find_project_root_falls_back_to_package_project_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    root = find_project_root()

    assert root is not None
    assert looks_like_project_root(root)
