from __future__ import annotations

from pytest import CaptureFixture

from reconstruction.reporting import console
from reconstruction.truth import CorrectnessStats
from reconstruction.types import Summary


def test_reconstruction_parameters_print_as_rich_panel(
    capsys: CaptureFixture[str],
) -> None:
    console.print_parameters({"table": "patients", "workers": 4})

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Reconstruction Parameters" in captured.out
    assert "Parameter" in captured.out
    assert "patients" in captured.out
    assert "workers" in captured.out


def test_reconstruction_info_prints_to_stderr(capsys: CaptureFixture[str]) -> None:
    console.print_info("Loaded ground truth: 10 rows in 0.01s")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Loaded ground truth" in captured.err


def test_reconstruction_final_report_prints_rich_tables(
    capsys: CaptureFixture[str],
) -> None:
    summary: Summary = {
        "value_true_positives": 8,
        "value_false_positives": 1,
        "value_false_negatives": 2,
        "tuple_true_positives": 5,
        "tuple_false_positives": 0,
        "tuple_false_negatives": 1,
        "value_true_positives_per_attr": {"age": 3},
        "value_false_positives_per_attr": {"age": 1},
        "value_false_negatives_per_attr": {"age": 2},
    }

    console.print_final_report(
        summary,
        ["age"],
        {2: CorrectnessStats(tp=5, fp=0, fn=1, total=6)},
        {"attr_probe:age": 12},
        {"attr_probe:age": 0.125},
        verify=True,
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Reconstruction Summary" in captured.err
    assert "Scope" in captured.err
    assert "Values" in captured.err
    assert "Attribute" in captured.err
    assert "age" in captured.err
    assert "Tuple length" in captured.err
    assert "Attacker queries" in captured.err
    assert "Seconds" in captured.err
