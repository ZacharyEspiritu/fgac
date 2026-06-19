from __future__ import annotations

import sys

from rls_artifact import cli


def test_parser_accepts_claim_run_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "claims",
            "run",
            "C-R6",
            "C-R7",
            "--dry-run",
            "--run-id",
            "review-run",
            "--machines",
            "machines.yml",
        ]
    )

    assert args.command == "claims"
    assert args.claims_command == "run"
    assert args.claims == ["C-R6", "C-R7"]
    assert args.dry_run is True
    assert args.run_id == "review-run"
    assert args.machines == "machines.yml"


def test_main_dispatches_doctor(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_run_doctor(*, skip_gcloud: bool, skip_tex: bool) -> int:
        calls["skip_gcloud"] = skip_gcloud
        calls["skip_tex"] = skip_tex
        return 17

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)
    monkeypatch.setattr(
        sys,
        "argv",
        ["unfilter-rls", "doctor", "--skip-gcloud", "--skip-tex"],
    )

    assert cli.main() == 17
    assert calls == {"skip_gcloud": True, "skip_tex": True}


def test_main_dispatches_claims(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_run_claims_command(args: object) -> int:
        calls["args"] = args
        return 23

    monkeypatch.setattr(cli, "run_claims_command", fake_run_claims_command)
    monkeypatch.setattr(
        sys,
        "argv",
        ["unfilter-rls", "claims", "run", "C-R6", "--dry-run"],
    )

    assert cli.main() == 23
    args = calls["args"]
    assert getattr(args, "claims_command") == "run"
    assert getattr(args, "claims") == ["C-R6"]
    assert getattr(args, "dry_run") is True


def test_main_dispatches_results(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def fake_run_results_command(args: object) -> int:
        calls["args"] = args
        return 31

    monkeypatch.setattr(cli, "run_results_command", fake_run_results_command)
    monkeypatch.setattr(
        sys,
        "argv",
        ["unfilter-rls", "results", "inspect", "review-run", "--raw"],
    )

    assert cli.main() == 31
    args = calls["args"]
    assert getattr(args, "results_command") == "inspect"
    assert getattr(args, "path") == "review-run"
    assert getattr(args, "raw") is True
