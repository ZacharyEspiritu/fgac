from argparse import Namespace

from mitigation.attack import AttackResult
from mitigation.configs import CONFIGS_BY_NAME
from mitigation.runner import (
    print_accuracy_panel,
    print_artifact_panel,
    print_config_panel,
    print_explain_panel,
    print_run_panel,
)


def test_mitigation_runner_prints_rich_panels(capsys) -> None:
    config = CONFIGS_BY_NAME["baseline"]
    args = Namespace(
        attribute="ssn",
        attacker_user="doctor_s1_00000",
        probes=10,
        output_dir="results/join_mitigations",
        doctors_index=False,
    )
    print_run_panel(
        args,
        k_values=[1, 2],
        selected=[config],
        attacker_site=1,
        authorized_count=10,
        unauthorized_count=10,
        nonexistent_count=10,
    )
    print_config_panel(config, 1, 1)
    print_explain_panel("authorized", ["Index Scan using patients_ssn_idx on patients"])

    result = AttackResult(config_name=config.name, probes=10, k_values=[1, 2])
    result.total_positive = 5
    result.total_negative = 5
    for k in [1, 2]:
        result.tp_by_k[k] = 4
        result.tn_by_k[k] = 3
    print_accuracy_panel(config.name, result, [1, 2])
    print_artifact_panel("results/join_mitigations/summary.csv")

    output = capsys.readouterr().out
    assert "Join-Policy Mitigation Sweep" in output
    assert "Configuration 1/1" in output
    assert "EXPLAIN (authorized)" in output
    assert "Mitigation Accuracy - baseline" in output
    assert "Mitigation Artifacts Written" in output
