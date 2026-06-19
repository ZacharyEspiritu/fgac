from __future__ import annotations

import pytest

from renderers.renderer_util import heatmap_table, pgf, reconstruction_table, timing_distribution


def test_reconstruction_table_mean_ci_and_required_mean_ci() -> None:
    assert reconstruction_table.mean_ci([]) == (None, None)
    assert reconstruction_table.mean_ci([5.0]) == (5.0, None)

    mean, ci = reconstruction_table.mean_ci([1.0, 2.0, 3.0])
    assert mean == 2.0
    assert ci == pytest.approx(2.484338)
    assert reconstruction_table.required_mean_ci([2.0]) == (2.0, None)

    with pytest.raises(ValueError, match="at least one value"):
        reconstruction_table.required_mean_ci([])


def test_reconstruction_table_formatters() -> None:
    assert reconstruction_table.ci_fragment(0.42, "1em") == r"\,\scalebox{0.7}{$\pm$\,\makebox[1em][r]{}.4}"
    assert reconstruction_table.latex_commas(1234567) == "1{,}234{,}567"
    assert reconstruction_table.fmt_percent_cell(99.999, 0.5, "1em", "0.5em") == (
        r"99.99\makebox[1em][l]{\,\scalebox{0.7}{$\pm$\,\makebox[0.5em][r]{}.01}}"
    )
    assert reconstruction_table.fmt_domain_size(1000) == r"$10^{3}$"
    assert reconstruction_table.fmt_domain_size(1200) == "1{,}200"
    assert reconstruction_table.fmt_speedup(2.25) == r"2.2$\times$"
    assert reconstruction_table.fmt_speedup(None) == "--"


def test_heatmap_table_formatters_escape_and_parse_values() -> None:
    assert heatmap_table.fmt_acc(99.999) == "99.99"
    assert heatmap_table.fmt_acc(100.0) == "100.0"
    assert heatmap_table.fmt_ci(0.06) == ".06"
    assert heatmap_table.fmt_ci("+/- 0.06".replace("+/-", "±")) == ".06"
    assert heatmap_table.parse_float(" 1.5 ") == 1.5
    assert heatmap_table.parse_float("") is None
    assert heatmap_table.fmt_qps(1234.4, comma=True) == "1,234"
    assert heatmap_table.fmt_qps(None, missing="n/a") == "n/a"
    assert heatmap_table.fmt_min_k("bad") == "\u2014"
    assert heatmap_table.fmt_cost_multiplier("2.9") == "2\u00d7"
    assert heatmap_table.latex_escape(r"a_b&100%") == r"a\_b\&100\%"


def test_heatmap_table_confidence_intervals() -> None:
    assert heatmap_table.ci_margin_pct(50.0, 100, 1.96) == pytest.approx(9.8)
    assert heatmap_table.ci_margin_pct(50.0, 0, 1.96) == 0.0
    assert heatmap_table.wilson_half_width(50, 100) == pytest.approx(12.471904)
    assert heatmap_table.wilson_half_width(1, 0) == 0.0


def test_pgf_helpers_append_expected_commands() -> None:
    lines: list[str] = []

    pgf.tikz_node(lines, 1.2345, 2.0, "text", r"\small", "black", extra="rotate=90")
    pgf.tikz_hrule(lines, 3.0, 0.0, 4.0, "0.4pt")
    pgf.tikz_rect(lines, 1.0, 2.0, 3.0, 4.0, "none", draw="black")

    assert lines == [
        r"\node[font=\small, text=black, inner sep=0pt, align=center, rotate=90] at (1.234,-2.000) {text};",
        r"\draw[rlsOuter, line width=0.4pt] (0.000,-3.000) -- (4.000,-3.000);",
        r"\path[draw=black, line width=0pt] (1.000,-2.000) rectangle (4.000,-6.000);",
    ]


def test_timing_distribution_summarizes_and_renders_latency_rows(tmp_path) -> None:
    rows = [
        ("authorized", 1000),
        ("authorized", 3000),
        ("unauthorized", 5000),
        ("nonexistent", 9000),
    ]

    summary = timing_distribution.summarize_latency_rows(rows, 1)
    assert summary["authorized"] == (1000, 2000, 1000)

    csv_path = tmp_path / "latencies.csv"
    csv_path.write_text(
        "query_type,elapsed_ns\n"
        "authorized,1000\n"
        "authorized,3000\n"
        "unauthorized,5000\n",
        encoding="utf-8",
    )
    groups, fieldnames = timing_distribution.read_latency_groups(str(csv_path))
    assert fieldnames == ["query_type", "elapsed_ns"]
    assert groups == {"authorized": [1.0, 3.0], "unauthorized": [5.0]}

    table = timing_distribution.render_compact_latency_stats_table(groups)
    assert r"\textit{auth}" in table
    assert r"\textit{unauth}" in table
    assert r"\textit{nonexist}" not in table


def test_timing_distribution_normalizes_plot_outputs() -> None:
    assert timing_distribution.normalized_plot_output("figure.png", "png") == "figure.png"
    assert timing_distribution.normalized_plot_output("figure.png", "pdf") == "figure.pdf"
    assert timing_distribution.normalized_plot_output("figure.any", "pgf") == "figure.pgf"
