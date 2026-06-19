from __future__ import annotations

import sys
from collections.abc import Sequence

import pytest

import cli


def test_command_choices_include_primary_commands_and_aliases() -> None:
    choices = cli.command_choices()

    assert "enumerate" in choices
    assert "debruijn" in choices
    assert "table" in choices
    assert "doctor" in choices
    assert "preflight" not in choices
    assert "enum" in choices
    assert "render-table" in choices


def test_run_command_forwards_args_and_restores_argv(monkeypatch) -> None:
    observed: list[str] = []
    original_argv = list(sys.argv)

    def fake_module_main(module_name: str):
        assert module_name == "latex.render_table"

        def main() -> int:
            observed.extend(sys.argv)
            return 0

        return main

    monkeypatch.setattr(cli, "module_main", fake_module_main)

    assert cli.run_command("table", ["stats.json", "--output", "table.tex"]) == 0
    assert observed == [
        "unfilter-dls table",
        "stats.json",
        "--output",
        "table.tex",
    ]
    assert sys.argv == original_argv


def test_run_command_enumerate_uses_wrapper_prog(monkeypatch) -> None:
    import enumerator.runner

    observed: list[tuple[Sequence[str] | None, str]] = []

    def fake_main(argv: Sequence[str] | None = None, *, prog: str) -> int:
        observed.append((list(argv) if argv is not None else None, prog))
        return 0

    monkeypatch.setattr(enumerator.runner, "main", fake_main)

    assert cli.run_command("enumerate", ["--help"]) == 0
    assert observed == [(["--help"], "unfilter-dls enumerate")]


def test_run_command_resolves_aliases(monkeypatch) -> None:
    observed: list[str] = []

    def fake_module_main(module_name: str):
        observed.append(module_name)

        def main() -> int:
            return 0

        return main

    monkeypatch.setattr(cli, "module_main", fake_module_main)

    assert cli.run_command("render-table", ["stats.json"]) == 0
    assert observed == ["latex.render_table"]


@pytest.mark.parametrize(
    ("system_exit_code", "expected"),
    [
        (None, 0),
        (2, 2),
    ],
)
def test_run_command_normalizes_system_exit_codes(
    monkeypatch,
    system_exit_code: int | None,
    expected: int,
) -> None:
    def fake_module_main(module_name: str):
        del module_name

        def main() -> int:
            raise SystemExit(system_exit_code)

        return main

    monkeypatch.setattr(cli, "module_main", fake_module_main)

    assert cli.run_command("doctor", []) == expected


def test_main_parses_command_and_remainder(monkeypatch) -> None:
    observed: list[tuple[str, Sequence[str]]] = []

    def fake_run_command(command: str, args: Sequence[str]) -> int:
        observed.append((command, list(args)))
        return 0

    monkeypatch.setattr(cli, "run_command", fake_run_command)

    assert cli.main(["debruijn", "stats.json", "--k", "4"]) == 0
    assert observed == [("debruijn", ["stats.json", "--k", "4"])]
