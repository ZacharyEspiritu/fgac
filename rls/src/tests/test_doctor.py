from __future__ import annotations

from rls_artifact import doctor
from rls_artifact.doctor import Check


def test_run_doctor_respects_skip_flags_and_returns_failure(monkeypatch, capsys) -> None:
    checked_tools: list[tuple[str, ...]] = []

    def fake_check_tools(tools: tuple[str, ...]) -> list[Check]:
        checked_tools.append(tools)
        return [Check(f"Tool {tool}", "OK", f"/usr/bin/{tool}") for tool in tools]

    def fail_if_called() -> list[Check]:
        raise AssertionError("gcloud checks should be skipped")

    monkeypatch.setattr(doctor, "_check_python_version", lambda: Check("Python", "OK", "3.12"))
    monkeypatch.setattr(doctor, "_check_project_root", lambda: Check("Project root", "OK", "rls"))
    monkeypatch.setattr(
        doctor,
        "_check_package_imports",
        lambda: [Check("Import reconstruction", "FAIL", "missing dependency")],
    )
    monkeypatch.setattr(doctor, "_check_tools", fake_check_tools)
    monkeypatch.setattr(doctor, "_check_gcloud", fail_if_called)

    assert doctor.run_doctor(skip_gcloud=True, skip_tex=True) == 1

    assert checked_tools == [doctor.REQUIRED_TOOLS]
    output = capsys.readouterr().out
    assert "RLS Artifact Doctor" in output
    assert "Import reconstruction" in output
    assert "FAIL" in output


def test_check_tools_reports_missing_and_found_tools(monkeypatch) -> None:
    def fake_which(tool: str) -> str | None:
        if tool == "git":
            return "/usr/bin/git"
        return None

    monkeypatch.setattr(doctor.shutil, "which", fake_which)

    assert doctor._check_tools(("git", "uv")) == [
        Check("Tool git", "OK", "/usr/bin/git"),
        Check("Tool uv", "FAIL", "not found on PATH"),
    ]
