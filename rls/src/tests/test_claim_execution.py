from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from rls_artifact import claims


def make_project_root(path: Path) -> Path:
    (path / "pyproject.toml").write_text("[project]\nname = 'rls-test'\n", encoding="utf-8")
    (path / "orchestration" / "config").mkdir(parents=True)
    (path / "orchestration" / "config" / "shared_config.yml").write_text(
        "singleattr:\n  reps: 1\n  run_linear: true\n",
        encoding="utf-8",
    )
    (path / "orchestration" / "config" / "crosszone_config.yml").write_text(
        "crosszone:\n  reps: 1\n",
        encoding="utf-8",
    )
    (path / "orchestration" / "config" / "tde_config.yml").write_text(
        "tde:\n  reps: 1\n",
        encoding="utf-8",
    )
    (path / "orchestration" / "run_samezone_exps.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (path / "orchestration" / "run_crosszone_exps.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    return path


def test_run_claim_executes_with_generated_config_run_id_and_cwd(tmp_path, monkeypatch) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> SimpleNamespace:
        calls.append({"command": command, "cwd": cwd, "env": env, "check": check})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(claims.subprocess, "run", fake_run)

    exit_code = claims.run_claim(
        claim_refs=["C-R6"],
        dry_run=False,
        config=None,
        config_overrides=(("singleattr.reps", "10"), ("singleattr.run_linear", "false")),
        run_id="review",
        machines="machines.yml",
    )

    assert exit_code == 0
    assert len(calls) == 1
    call = calls[0]
    assert call["command"] == [
        "bash",
        "orchestration/run_samezone_exps.sh",
        "--experiments",
        "6",
        "--machines",
        "machines.yml",
    ]
    assert call["cwd"] == root
    assert call["check"] is False
    env = call["env"]
    assert isinstance(env, dict)
    assert env["RUN_ID"] == "review"
    generated_config = Path(env["CONFIG"])
    assert generated_config.is_file()
    assert generated_config.parent == root / "results" / "config-overrides"
    assert yaml.safe_load(generated_config.read_text(encoding="utf-8")) == {
        "singleattr": {"reps": 10, "run_linear": False}
    }


def test_run_claim_suffixes_run_ids_for_multiple_runners(tmp_path, monkeypatch) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)
    run_ids: list[str | None] = []

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> SimpleNamespace:
        del command, cwd, check
        run_ids.append(env.get("RUN_ID"))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(claims.subprocess, "run", fake_run)

    assert claims.run_claim(
        claim_refs=["C-R6,C-R4,C-R5"],
        dry_run=False,
        config=None,
        config_overrides=None,
        run_id="review",
        machines=None,
    ) == 0

    assert run_ids == ["review-samezone", "review-tde", "review-crosszone"]


def test_run_claim_stops_on_first_failed_command(tmp_path, monkeypatch) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path, env: dict[str, str], check: bool) -> SimpleNamespace:
        del cwd, env, check
        commands.append(command)
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(claims.subprocess, "run", fake_run)

    assert claims.run_claim(
        claim_refs=["C-R6,C-R4"],
        dry_run=False,
        config=None,
        config_overrides=None,
        run_id=None,
        machines=None,
    ) == 7
    assert len(commands) == 1


def test_run_claim_dry_run_uses_default_config_without_writing_override(tmp_path, monkeypatch, capsys) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)

    exit_code = claims.run_claim(
        claim_refs=["C-R6"],
        dry_run=True,
        config=None,
        config_overrides=(("singleattr.reps", "10"),),
        run_id="dry-run",
        machines=None,
    )

    assert exit_code == 0
    assert not (root / "results" / "config-overrides").exists()
    output = capsys.readouterr().out
    assert "Generated CONFIG" in output
    assert "not written" in output


def test_run_claim_reports_missing_project_root(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(claims, "find_project_root", lambda: None)

    assert claims.run_claim(
        claim_refs=["C-R6"],
        dry_run=True,
        config=None,
        config_overrides=None,
        run_id=None,
        machines=None,
    ) == 2
    assert "Could not find the RLS project root" in capsys.readouterr().out


def test_run_claim_rejects_ambiguous_config_across_runners(tmp_path, monkeypatch, capsys) -> None:
    root = make_project_root(tmp_path)
    monkeypatch.chdir(root)

    assert claims.run_claim(
        claim_refs=["C-R6,C-R4"],
        dry_run=True,
        config="orchestration/config/shared_config.yml",
        config_overrides=None,
        run_id=None,
        machines=None,
    ) == 2
    assert "--config is ambiguous" in capsys.readouterr().out


def test_config_override_helpers_validate_keys_values_and_base_config(tmp_path) -> None:
    root = make_project_root(tmp_path)
    command = claims._claim_commands((claims.CLAIMS_BY_ID["C-R6"],), machines=None)[0]

    assert claims._normalize_config_overrides([["a.b", "1"]]) == (("a.b", "1"),)
    with pytest.raises(claims.ConfigOverrideError, match="exactly KEY VALUE"):
        claims._normalize_config_overrides([["a"]])
    with pytest.raises(claims.ConfigOverrideError, match="Invalid config override key"):
        claims._config_override_parts("a..b")
    with pytest.raises(claims.ConfigOverrideError, match="only supports scalar"):
        claims._parse_config_override_value("{a: 1}")
    with pytest.raises(claims.ConfigOverrideError, match="Config not found"):
        claims._resolve_override_base_config(root, "missing.yml", command)

    data: dict[str, object] = {}
    claims._apply_config_override(data, ".a.b", "true")
    assert data == {"a": {"b": True}}
