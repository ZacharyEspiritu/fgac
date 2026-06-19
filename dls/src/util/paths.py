from __future__ import annotations

from pathlib import Path


def artifact_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (
            (candidate / "pyproject.toml").is_file()
            and (candidate / "config" / "config.yml").is_file()
            and (candidate / "src" / "cli.py").is_file()
        ):
            return candidate
    return current.parents[2]


def resolve_artifact_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    if candidate.exists():
        return candidate
    return artifact_root() / candidate
