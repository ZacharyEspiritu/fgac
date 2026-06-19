from __future__ import annotations

from rls_artifact.claims import (
    CLAIMS_BY_ID,
    _claim_commands,
    _command_run_id,
    _resolve_claim,
    _resolve_claims,
    run_claim,
)


def test_resolve_claim_accepts_common_aliases() -> None:
    assert _resolve_claim("C-R6") == CLAIMS_BY_ID["C-R6"]
    assert _resolve_claim("CR6") == CLAIMS_BY_ID["C-R6"]
    assert _resolve_claim("6") == CLAIMS_BY_ID["C-R6"]
    assert _resolve_claim("  c-r6  ") == CLAIMS_BY_ID["C-R6"]


def test_resolve_claims_splits_commas_deduplicates_and_preserves_registry_order() -> None:
    claims, error = _resolve_claims(["C-R7,6", "CR6"])

    assert error is None
    assert [claim.claim_id for claim in claims] == ["C-R6", "C-R7"]


def test_resolve_claims_reports_unknown_tokens() -> None:
    claims, error = _resolve_claims(["C-R6,not-a-claim"])

    assert claims == ()
    assert error is not None
    assert "not-a-claim" in error


def test_claim_commands_group_same_zone_claims_and_keep_special_runners() -> None:
    claims, error = _resolve_claims(["C-R6,C-R7,C-R5,C-R4"])
    assert error is None

    commands = _claim_commands(claims, machines="machines.yml")

    assert [command.label for command in commands] == ["same-zone", "tde", "cross-zone"]
    assert [claim.claim_id for claim in commands[0].claims] == ["C-R6", "C-R7"]
    assert commands[0].command == [
        "bash",
        "orchestration/run_samezone_exps.sh",
        "--experiments",
        "6,7",
        "--machines",
        "machines.yml",
    ]
    assert commands[1].command == [
        "bash",
        "orchestration/run_tde_exps.sh",
        "--machines",
        "machines.yml",
    ]
    assert commands[2].command == [
        "bash",
        "orchestration/run_crosszone_exps.sh",
        "--machines",
        "machines.yml",
    ]


def test_command_run_id_suffixes_multi_runner_commands() -> None:
    claims, error = _resolve_claims(["C-R6,C-R4"])
    assert error is None
    same_zone, cross_zone = _claim_commands(claims, machines=None)

    assert _command_run_id("review", same_zone, multiple_commands=True) == "review-samezone"
    assert _command_run_id("review", cross_zone, multiple_commands=True) == "review-crosszone"
    assert _command_run_id("review", same_zone, multiple_commands=False) == "review"
    assert _command_run_id(None, same_zone, multiple_commands=True) is None


def test_run_claim_rejects_config_overrides_across_multiple_runners(capsys) -> None:
    exit_code = run_claim(
        claim_refs=["C-R6,C-R4"],
        dry_run=True,
        config=None,
        config_overrides=(("singleattr.reps", "10"),),
        run_id=None,
        machines=None,
    )

    assert exit_code == 2
    assert "--set is ambiguous" in capsys.readouterr().out
