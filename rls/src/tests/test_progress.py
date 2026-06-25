from __future__ import annotations

import io

from pytest import CaptureFixture, MonkeyPatch
from rich.console import Console
from rich.progress import SpinnerColumn

from util.progress import (
    LabeledElapsedColumn,
    LabeledEtaColumn,
    ProgressBar,
    PulseOverlayBarColumn,
    _pulse_start,
    _style_for_label,
)


def test_progress_bar_renders_rich_panel(capsys: CaptureFixture[str]) -> None:
    progress = ProgressBar(3, "demo", interval_s=0)

    initial = capsys.readouterr()
    assert "demo" in initial.err
    assert "0/3" in initial.err
    assert "Elapsed" in initial.err
    assert "ETA" in initial.err

    progress.update(3, preview="done")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Progress" not in captured.err
    assert "demo" in captured.err
    assert "3/3" in captured.err
    assert "done" in captured.err
    assert "╭" not in captured.err
    assert isinstance(progress._progress.columns[0], SpinnerColumn)
    assert isinstance(progress._progress.columns[2], PulseOverlayBarColumn)
    assert isinstance(progress._progress.columns[5], LabeledElapsedColumn)
    assert isinstance(progress._progress.columns[6], LabeledEtaColumn)


def test_progress_bar_respects_disabled_env(
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("RLS_PROGRESS", "0")
    progress = ProgressBar(2, "disabled", interval_s=0)

    progress.update(2, preview="done")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_progress_bar_prints_initial_panel_before_first_update(
    capsys: CaptureFixture[str],
) -> None:
    ProgressBar(10, "patients", interval_s=0)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "patients" in captured.err
    assert "0/10" in captured.err


def test_completed_progress_panels_do_not_run_together(
    capsys: CaptureFixture[str],
) -> None:
    first = ProgressBar(1, "doctors", interval_s=0)
    second = ProgressBar(1, "patients", interval_s=0)

    first.update(1, preview="doctor_s5_09999")
    second.update(1, preview="1000999999")

    captured = capsys.readouterr()
    assert "doctors" in captured.err
    assert "patients" in captured.err
    assert "╯╭" not in captured.err


def test_non_interactive_completion_is_compact_line(
    capsys: CaptureFixture[str],
) -> None:
    progress = ProgressBar(1, "doctors", interval_s=0)
    capsys.readouterr()

    progress.update(1, preview="doctor_s5_09999")

    captured = capsys.readouterr()
    assert "done doctors: 1/1 doctor_s5_09999 elapsed" in captured.err
    assert "╭" not in captured.err


def test_pulse_overlay_moves_right_before_wrapping() -> None:
    first = _pulse_start(
        animation_time=0.0,
        remaining_width=20,
        pulse_width=5,
        pulse_gap=18,
        pulse_speed=8.0,
    )
    second = _pulse_start(
        animation_time=1.0,
        remaining_width=20,
        pulse_width=5,
        pulse_gap=18,
        pulse_speed=8.0,
    )
    third = _pulse_start(
        animation_time=2.0,
        remaining_width=20,
        pulse_width=5,
        pulse_gap=18,
        pulse_speed=8.0,
    )

    assert first < second < third


def test_progress_panel_styles_distinguish_dataset_loaders() -> None:
    assert _style_for_label("doctors") == "bright_magenta"
    assert _style_for_label("patients") == "bright_green"
    assert _style_for_label("other") == "bright_blue"

    doctors = ProgressBar(1, "doctors", interval_s=0)
    patients = ProgressBar(1, "patients", interval_s=0)

    assert doctors._style == "bright_magenta"
    assert patients._style == "bright_green"


def test_progress_body_height_never_shrinks() -> None:
    progress = ProgressBar(3, "demo", interval_s=0)
    console = Console(file=io.StringIO(), width=60, highlight=False)

    progress._progress.update(
        progress._task_id,
        completed=1,
        preview="long-preview-" * 20,
    )
    tall_lines = console.render_lines(progress._body, console.options)
    tall_height = len(tall_lines)

    progress._progress.update(
        progress._task_id,
        completed=2,
        preview="short",
    )
    short_lines = console.render_lines(progress._body, console.options)

    assert len(short_lines) == tall_height
    assert progress._body.min_height == tall_height
