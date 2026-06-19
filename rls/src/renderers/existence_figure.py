#!/usr/bin/env python3
# Copyright 2026 MongoDB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

r"""Render a complete LaTeX figure (\begin{figure}...\end{figure}) for an
existence-family timing experiment: the histogram .pgf with the compact
per-class stats table overlaid in a corner (no stacking), plus caption + label.

The per-class numbers come from the run's own CSV; the histogram path is
whatever the paper \input{}s. Works for both the equality (existence) and range experiments —
defaults (pgf path, caption, label) are picked by experiment, auto-detected
from the CSV schema (range CSVs have start/end columns) or set with
--experiment. Layout knobs (--anchor/--xshift/--yshift/--tabcolsep/
--arraystretch) default to the values used in the paper figure.

Usage:
    uv run python -m renderers.existence_figure \
        --input  results/existence/<RUN_ID>/existence_latency.csv \
        --output results/existence/<RUN_ID>/existence_kde_figure.tex
    uv run python -m renderers.existence_figure \
        --input  results/range/<RUN_ID>/existence_range_latency.csv \
        --output results/range/<RUN_ID>/existence_range_kde_figure.tex
"""
import argparse
import os

from renderers.renderer_util.io import write_text
from renderers.renderer_util.timing_distribution import (
    read_latency_groups,
    render_compact_latency_stats_table,
)


def _fmt_trials(n):
    if n >= 1000 and n % 1000 == 0:
        return f"{n // 1000}k"
    return str(n)


# @TRIALS@ is substituted with the per-class sample count. Captions reference
# paper macros (\Cref, \hl, \sethlcolor) — override with --caption if absent.
# Keyed by [variant][experiment]: variant is the DB setup — `baseline` (plain
# PostgreSQL) or `tde` (LUKS-encrypted data directory). The tde captions point
# back at the baseline figure so a reviewer can see they are ~identical.
CAPTIONS = {
    "baseline": {
        "existence": r"Equality query timing distributions from @TRIALS@ trials in each class under a join-based RLS policy (see \Cref{sec:evaluation}) based on client observed RTT. \textbf{Takeaway:} \sethlcolor{orange!10}\hl{\,\emph{Auth}\,} and \sethlcolor{green!10}\hl{\,\emph{unauth}\,} queries are reliably longer than \sethlcolor{cyan!10}\hl{\,\emph{nonexist}\,} under join policies.",
        "range": r"Range  query timing distributions from @TRIALS@ trials in each class  under a join-based  policy. On  \sethlcolor{orange!10}\hl{\,\textit{auth}\,} queries, the cardinality of $\qry(\tble)$ ranges from 1--4; the distinct peaks arise from different query cardinalities (which in turn check the policy different numbers of times). \textbf{Takeaway:} The timing side-channel is preserved under expressive queries.",
    },
    "tde": {
        "existence": r"Equality query timing distributions from @TRIALS@ trials in each class against a \textbf{LUKS-encrypted (TDE)} PostgreSQL under a join-based RLS policy, based on client observed RTT. \textbf{Takeaway:} The distributions are approximately identical to the unencrypted baseline (\Cref{fig:existence-kde-join}) --- block-level encryption at rest does not close the timing channel.",
        "range": r"Range query timing distributions from @TRIALS@ trials in each class against a \textbf{LUKS-encrypted (TDE)} PostgreSQL under a join-based policy. \textbf{Takeaway:} Approximately identical to the unencrypted baseline (\Cref{fig:existence-range-kde-join}) --- TDE does not close the channel under expressive range queries.",
    },
}
DEFAULT_PGF = {
    "baseline": {"existence": "existence_kde.pgf", "range": "existence_range_kde.pgf"},
    "tde": {"existence": "existence_tde_figure.pgf", "range": "existence_range_tde_figure.pgf"},
}
DEFAULT_LABEL = {
    "baseline": {"existence": "fig:existence-kde-join", "range": "fig:existence-range-kde-join"},
    "tde": {"existence": "fig:tde-existence-kde-join", "range": "fig:tde-existence-range-kde-join"},
}

TEMPLATE = r"""\begin{figure}@PLACEMENT@
\centering
\footnotesize
% Histogram with the compact stats table overlaid in its corner whitespace
% (instead of stacked above) -- overall height becomes just the histogram's.
\begin{tikzpicture}
  % Base layer: the timing histogram.
  \node[anchor=south west, inner sep=0pt] (hist) {\input{@PGF@}};
  % Overlay: compact Class/Min/Mean/Std table tucked into the corner.
  \node[anchor=@ANCHOR@, inner sep=1pt, xshift=@XSHIFT@, yshift=@YSHIFT@]
       at (hist.@ANCHOR@) {%
\setlength{\aboverulesep}{0pt}
\setlength{\belowrulesep}{0pt}
       \setlength{\tabcolsep}{@TABCOLSEP@}%
    \renewcommand{\arraystretch}{@ARRAYSTRETCH@}%
@TABLE@%
  };
\end{tikzpicture}
\vspace{-2em}
\caption{@CAPTION@}
\label{@LABEL@}
\end{figure}
"""


def main():
    ap = argparse.ArgumentParser(description="Render the overlaid existence/range timing figure (.tex).")
    ap.add_argument("--input", required=True, help="Latency CSV (query_type + elapsed_ns columns).")
    ap.add_argument("--output", default=None, help="Output .tex (default: <input-stem>_figure.tex).")
    ap.add_argument("--experiment", choices=("existence", "range"), default=None,
                    help="Defaults are auto-detected from the CSV (range has start/end columns).")
    ap.add_argument("--variant", choices=("baseline", "tde"), default="baseline",
                    help="DB setup: baseline (plain PostgreSQL, default) or tde (LUKS-encrypted). "
                         "Picks the default pgf path / caption / label so the TDE figure mirrors the baseline.")
    ap.add_argument("--pgf", default=None, help="Path the figure \\input{}s (default per experiment).")
    ap.add_argument("--caption", default=None, help="Override the caption text.")
    ap.add_argument("--label", default=None, help="Override the \\label.")
    ap.add_argument("--anchor", default="north east", help="TikZ corner for the table (default: north east).")
    ap.add_argument("--xshift", default="-2mm", help="Table xshift (default: -2mm; use +2mm for a west anchor).")
    ap.add_argument("--yshift", default="-5.5pt", help="Table yshift (default: -5.5pt).")
    ap.add_argument("--tabcolsep", default="4.5pt", help="Table \\tabcolsep (default: 4.5pt).")
    ap.add_argument("--arraystretch", default="1.05", help="Table \\arraystretch (default: 1.05).")
    ap.add_argument("--placement", default="", help="Float placement spec, e.g. t (default: none).")
    args = ap.parse_args()

    by, fieldnames = read_latency_groups(args.input)
    if not by:
        raise SystemExit(f"No rows found in {args.input}")

    experiment = args.experiment or ("range" if "start" in fieldnames else "existence")
    trials = _fmt_trials(min(len(v) for v in by.values()))

    caption = args.caption if args.caption is not None else CAPTIONS[args.variant][experiment].replace("@TRIALS@", trials)
    pgf = args.pgf or DEFAULT_PGF[args.variant][experiment]
    label = args.label or DEFAULT_LABEL[args.variant][experiment]

    table = render_compact_latency_stats_table(by).rstrip("\n")
    table_indented = "\n".join(("    " + ln) if ln.strip() else ln for ln in table.splitlines())

    out = (
        TEMPLATE
        .replace("@PLACEMENT@", f"[{args.placement}]" if args.placement else "")
        .replace("@PGF@", pgf)
        .replace("@ANCHOR@", args.anchor)
        .replace("@XSHIFT@", args.xshift)
        .replace("@YSHIFT@", args.yshift)
        .replace("@TABCOLSEP@", args.tabcolsep)
        .replace("@ARRAYSTRETCH@", args.arraystretch)
        .replace("@TABLE@", table_indented)
        .replace("@CAPTION@", caption)
        .replace("@LABEL@", label)
    )

    output = args.output or (os.path.splitext(args.input)[0] + "_figure.tex")
    write_text(output, out)
    print(f"Saved {args.variant} {experiment} figure to {output}")


if __name__ == "__main__":
    main()
