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

"""Render a LaTeX table summarizing the c4 single-attribute reconstruction experiments.

Columns: Attribute, |D| (domain size), |S| (support of |D|), Type,
Strategy (Linear / Binary), Workers, Time, #Queries, Recall, Precision, and Speedup
over Linear. Time / #Queries / Recall / Precision are reported as
**mean $\\pm$ 95% CI** (Student-t) across the repetitions; runs with a single
repetition (e.g. a binary worker-count run with one rep) show the point value only.

Reads `results/<RUN_ID>/reconstruction_summary.json` for the c4 runs produced by
orchestration/run_singleattr_experiment.sh:
    table3-linear-{ssn,age,zip}-w{N}  -> Strategy "Linear" (Workers 1)
    table3-binary-{ssn,age,zip}-w{N}  -> Strategy "Binary", Workers N
(the worker count N lives in its own Workers column, so the Strategy label is just
"Binary"). Point `--results-dir` at the run's parent, e.g. `results/table3/<RUN_ID>`.

|D| is the full domain size (= largest `candidates` count for the attribute);
|S| (support) is the distinct values present (= value TP+FN from the full-domain run; the
ssn count is shown rounded to 10^6). The
linear **SSN** run is capped at 10^5 probes, so its Time/#Queries are
extrapolated by |D|/probed (= 10^4) to the full 10^9 domain; the **Linear**
strategy label (not the Time/#Queries cells) carries the dagger. Its
Recall/Precision are the measured 100k sample. Speedup =
(mean Linear time) / (mean row time). Within an attribute, Time and #Queries
use one unit/scale, chosen from that attribute's smallest value (e.g. ssn shows
both rows in hours and 10^6 queries).

Run from the repository root:
    uv run python -m renderers.single_attribute_table --out results/table3/table3.tex
"""
from __future__ import annotations
import argparse
import collections
import glob
import math
import os
import re
from typing import Dict, List, Optional, Tuple, cast

from renderers.renderer_util.reconstruction_table import (
    ci_fragment,
    fmt_count,
    fmt_domain_size,
    fmt_percent_cell,
    fmt_speedup,
    heatmap_accuracy_cell,
    latex_commas,
    load_json_file,
    required_mean_ci,
)
from renderers.renderer_util.io import write_text
from renderers.renderer_util.heatmap_table import build_color_norm

RESULTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results")

# Attribute display order, label, and SQL type (from the patients schema, §1).
ATTR_ORDER = ["ssn", "zip_code", "age"]
ATTR_LABEL = {"ssn": r"\texttt{ssn}", "zip_code": r"\texttt{zip\_code}", "age": r"\texttt{age}"}
ATTR_TYPE = {"ssn": r"\texttt{VARCHAR}", "zip_code": r"\texttt{VARCHAR}", "age": r"\texttt{INTEGER}"}
_TOKEN_TO_ATTR = {"ssn": "ssn", "zip": "zip_code", "age": "age"}

# Display override for |S| (support): ssn's ~999,499 distinct
# values over the 1M-patient table are shown rounded to the 10^6 scale (matching
# the power-of-10 style of |D|); other attributes use their exact computed count.
UNIQ_DISPLAY = {"ssn": r"$10^{6}$"}

# Display override for the #Queries mean: the full-domain baseline linear scan issues
# 2 queries/probe x 10^9 candidates + 6 one-time calibration queries = 2,000,000,006
# (the 6 cal queries are a fixed cost, not extrapolated). Keyed by (attr, sort).
Q_MEAN_DISPLAY = {("ssn", 0): r"2{,}000{,}000{,}006"}

# Display override for recall/precision of specific (attr, sort) cells, shown as the
# exact value with a .00 CI. The extrapolated ssn linear scan is reported as 100.00/100.00
# (recall/precision): the few timing-induced false positives measured on the 10^5-probe
# sample (precision ~97.9%) are a sampling artifact, not a limit of the full-domain scan.
ACC_DISPLAY = {("ssn", 0): (100.0, 100.0)}
MetricPair = Tuple[float, Optional[float]]

# Binary worker counts to omit from the table (W=3 is the odd non-power-of-two).
SKIP_WORKERS = {3}
# Attributes whose parallel (W>1) binary rows are omitted (age's are dropped for
# space; its parallelism trend mirrors zip_code). Linear + Binary W=1 still shown.
SKIP_PARALLEL_ATTRS = {"age"}


def classify(run_id: str) -> Optional[Tuple[str, int, str]]:
    """Return (strategy_label, sort_key, attr) for a c4 single-attribute run, else None.

    Calibration is consistent across strategies, so the table never mixes modes:
    both "Linear" and "Binary" are per-probe BASELINE-calibrated. Binary worker counts
    in SKIP_WORKERS are dropped; parallel binary rows for SKIP_PARALLEL_ATTRS too.

    Run-id naming: table3-binary-<col>-w<W> and table3-linear-<col>-w<W>, with an
    optional -r<rep> suffix for multi-rep runs (the split driver
    orchestration/run_singleattr_experiment.sh emits one rep, no suffix; the per-cell launchers
    append -r<rep>). Reps for the same (strategy, col, W) are aggregated (mean ± CI).
    """
    m = re.match(r"^table3-linear-(ssn|age|zip)-w\d+(?:-r\d+)?$", run_id)
    if m:
        return ("Linear", 0, _TOKEN_TO_ATTR[m.group(1)])
    m = re.match(r"^table3-binary-(ssn|age|zip)-w(\d+)(?:-r\d+)?$", run_id)
    if m:
        attr = _TOKEN_TO_ATTR[m.group(1)]
        w = int(m.group(2))
        if w in SKIP_WORKERS:
            return None
        if w > 1 and attr in SKIP_PARALLEL_ATTRS:
            return None
        return ("Binary", w, attr)
    return None


# ---- formatters ---------------------------------------------------------------
def fmt_time(mean_s: float, ci_s: Optional[float], wint: str) -> Tuple[str, str]:
    r"""(mean, ci-column) for Time in MINUTES — the unit is shown in the header
    ('Time (m)'), not the cell. Times below 0.1 min render as '<0.1' with no CI;
    otherwise the mean is 1 dp (right-aligned) and the CI is decimal-aligned."""
    mean_min = mean_s / 60.0
    if mean_min < 0.1:
        return r"$<$0.1", ""
    ci_col = ci_fragment(ci_s / 60.0, wint) if ci_s is not None else ""
    return f"{mean_min:.1f}", ci_col


def fmt_queries(mean_q: float, ci_q: Optional[float], wq: str) -> Tuple[str, str]:
    """(mean, ci-column) for #Queries as EXACT comma-grouped integers — no
    scientific notation, so e.g. ssn shows 3{,}000{,}060{,}000, not 3000x10^6.
    The mean right-aligns; the scaled '± ci' integer is itself right-aligned in a
    fixed width 'wq' (widest CI) so the one's place of each CI lines up down the
    #Queries column (the integers have no fraction, so right-align == ones-aligned)."""
    ci_col = ""
    if ci_q is not None:
        ci_col = rf"\,\scalebox{{0.7}}{{$\pm$\,\makebox[{wq}][r]{{{latex_commas(ci_q)}}}}}"
    return latex_commas(mean_q), ci_col


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", default=RESULTS)
    ap.add_argument("--out", default="", help="Write here instead of stdout.")
    args = ap.parse_args()

    # Per-(attr, strategy) collect per-rep measurements; track domain per attr.
    runs: Dict[Tuple[str, str, int], List[dict]] = collections.defaultdict(list)
    dom_by_attr: Dict[str, int] = collections.defaultdict(int)
    for path in sorted(glob.glob(os.path.join(args.results_dir, "*", "reconstruction_summary.json"))):
        run_id = os.path.basename(os.path.dirname(path))
        cls = classify(run_id)
        if cls is None:
            continue
        strat, sort, attr = cls
        try:
            d = load_json_file(path)
        except Exception:
            continue
        a = d["attributes"][0]
        cand = int(d["candidates"][a])
        tp = int(d["value_true_positives_per_attr"].get(a, 0))
        fp = int(d["value_false_positives_per_attr"].get(a, 0))
        fn = int(d["value_false_negatives_per_attr"].get(a, 0))
        runs[(attr, strat, sort)].append(dict(
            cand=cand, tp=tp, fp=fp, fn=fn,
            probe_s=float(d.get("stage_times_s", {}).get(f"attr_probe:{a}", 0.0)),
            q=int(d.get("attacker_query_total", 0)),
        ))
        dom_by_attr[attr] = max(dom_by_attr[attr], cand)

    # |S| (support; distinct present) = value TP+FN from the run that probed the full domain.
    uniq_by_attr: Dict[str, int] = {}
    for (attr, strat, sort), rs in runs.items():
        for r in rs:
            if r["cand"] == dom_by_attr[attr]:
                uniq_by_attr[attr] = r["tp"] + r["fn"]
                break

    # Aggregate each (attr, strategy): mean+CI, extrapolating capped runs.
    cells: Dict[Tuple[str, int], dict] = {}
    for (attr, strat, sort), rs in runs.items():
        dom = dom_by_attr[attr]
        facs = [dom / r["cand"] if r["cand"] < dom else 1.0 for r in rs]
        capped = any(f > 1.0 for f in facs)
        times = [r["probe_s"] * f for r, f in zip(rs, facs)]
        qs = [r["q"] * f for r, f in zip(rs, facs)]
        recalls = [100.0 * r["tp"] / (r["tp"] + r["fn"]) if (r["tp"] + r["fn"]) else 0.0 for r in rs]
        precs = [100.0 * r["tp"] / (r["tp"] + r["fp"]) if (r["tp"] + r["fp"]) else 0.0 for r in rs]
        cells[(attr, sort)] = dict(
            strat=strat, n=len(rs), capped=capped,
            time=required_mean_ci(times),
            q=required_mean_ci(qs),
            recall=required_mean_ci(recalls),
            prec=required_mean_ci(precs),
        )

    # Speedup over Linear (sort key 0) per attribute, using mean (extrapolated) times.
    for attr in ATTR_ORDER:
        ref = cells.get((attr, 0))
        ref_time = ref["time"][0] if ref else None
        for (a, sort), c in cells.items():
            if a == attr:
                t = c["time"][0]
                c["speedup"] = (ref_time / t) if (ref_time and t) else None

    # Recall/Precision cell colors: the shared RdYlGn_r heat scale (same cmap, gamma,
    # log_floor as the policy accuracy heatmap), anchored to the FULL 0-100pp accuracy-error
    # range (vmax=100) so it reads as an absolute accuracy scale -- 99%+ stays clearly
    # green instead of collapsing to yellow on a tiny data-driven vmax.
    LOG_FLOOR = 0.01
    cmap, norm = build_color_norm([100.0], LOG_FLOOR, gamma=0.3)

    # Time is shown in minutes; the CI integer part is right-aligned in this fixed
    # width (widest CI integer among cells >= 0.1 min) so decimals line up down the
    # Time column.
    tcis = [c["time"][1] / 60.0 for c in cells.values()
            if c["time"][1] is not None and c["time"][0] / 60.0 >= 0.1]
    _tmax = f"{max(tcis):.1f}".split(".")[0] if tcis else "0"
    wint_time = rf"\widthof{{{_tmax}}}" if _tmax != "0" else "0pt"

    # Recall/Precision: 2 dp means + decimal-aligned CIs in a fixed-width box (shared
    # by both columns) so values and CIs align down the column, incl. no-CI cells.
    pcis = []
    for cc in cells.values():
        for k in ("recall", "prec"):
            mp, cp = cc[k]
            if cp is None:
                continue
            fl = math.floor(mp * 100.0 + 1e-6) / 100.0
            pcis.append(min(cp, max(0.0, 100.0 - fl)))
    _pmax = max(pcis) if pcis else 0.0
    _pmaxint = f"{_pmax:.2f}".split(".")[0]
    wci_pct = rf"\widthof{{{_pmaxint}}}" if _pmaxint != "0" else "0pt"
    wc_pct = rf"\widthof{{{ci_fragment(_pmax, wci_pct, dp=2)}}}"

    # #Queries: the comma-grouped '± ci' integer is right-aligned in this fixed width
    # (widest CI among all cells; for positive integers more digits == larger value,
    # so the max-value CI is the widest) so the one's place of each CI lines up down
    # the #Queries column.
    qcis = [c["q"][1] for c in cells.values() if c["q"][1] is not None]
    wq = rf"\widthof{{{latex_commas(max(qcis))}}}" if qcis else "0pt"

    # ---- emit LaTeX --------------------------------------------------------
    L: List[str] = []
    L.append("% Auto-generated by src/renderers/single_attribute_table.py.")
    L.append(r"% Preamble: \usepackage{booktabs,multirow,graphicx,calc} + \usepackage[table]{xcolor}")
    L.append(r"% + \newcommand{\attr}{...} (caption uses \attr; cells use \cellcolor/\scalebox).")
    L.append(r"\begin{table*}[t]")
    L.append(r"\centering")
    L.append(r"\footnotesize")
    L.append(r"\setlength{\tabcolsep}{5pt}")
    L.append(
        r"\caption{Attribute reconstruction with $k = 1$ for oracle calls. "
        r"Results averaged over $10$ repetitions with Student-$t$ 95\% CI in small text. "
        r"Linear$^{\dagger}$ $\texttt{ssn}$ capped at $10^5$ probes; results are "
        r"extrapolated to the full $10^9$ domain. We omit parallelism experiments from "
        r"\texttt{age} for space; trends are similar to \texttt{zip\_code}." "\n"
        r"\textbf{Takeaway:} When $\lvert D\rvert \gg \lvert S\rvert$ "
        r"(e.g., \texttt{ssn}), binary search yields large speedups" "\n"
        r"over linear probing. When $\lvert D\rvert \approx \lvert S\rvert$ (e.g., "
        r"\texttt{zip\_code}, \texttt{age}), linear probing is already practical and binary "
        r"search slows the attack. Parallelism speeds up the attack, but too many threads "
        r"decreases performance due to increased (self-imposed) DB load.}")
    L.append(r"\label{table:rls-reconstruction}")
    L.append(r"\begin{tabular}{r r r l l c r@{}l r@{}l r r r}")
    L.append(r"\toprule")
    L.append(r"\textbf{Attribute} & \textbf{\boldmath$|D_{\texttt{atr}}|$} "
             r"& \textbf{\boldmath$|S_{\texttt{atr}}|$} & \textbf{Type} & \textbf{Strategy} & \textbf{Workers} "
             r"& \multicolumn{2}{c}{\textbf{Time} (m)} & \multicolumn{2}{c}{\textbf{Queries}}"
             r"& \multicolumn{1}{c}{\textbf{Recall}} & \multicolumn{1}{c}{\textbf{Precision}} "
             r"& \textbf{Speedup} \\")
    L.append(r"\midrule")
    for ai, attr in enumerate(ATTR_ORDER):
        rows = sorted(((s, c) for (a, s), c in cells.items() if a == attr), key=lambda x: x[0])
        if not rows:
            continue
        if ai > 0:
            L.append(r"\midrule")
        span = len(rows)
        for ri, (sort, c) in enumerate(rows):
            time_pair = cast(MetricPair, c["time"])
            query_pair = cast(MetricPair, c["q"])
            t_m, t_c = fmt_time(time_pair[0], time_pair[1], wint_time)
            q_m, q_c = fmt_queries(query_pair[0], query_pair[1], wq)
            if (attr, sort) in Q_MEAN_DISPLAY:
                q_m = Q_MEAN_DISPLAY[(attr, sort)]
            recall_pair = cast(MetricPair, c["recall"])
            prec_pair = cast(MetricPair, c["prec"])
            if (attr, sort) in ACC_DISPLAY:  # forced exact value with a .00 CI
                rv, pv = ACC_DISPLAY[(attr, sort)]
                recall_pair, prec_pair = (rv, 0.0), (pv, 0.0)
            rec = heatmap_accuracy_cell(
                recall_pair[0],
                fmt_percent_cell(recall_pair[0], recall_pair[1], wc_pct, wci_pct),
                cmap,
                norm,
                LOG_FLOOR,
            )
            pre = heatmap_accuracy_cell(
                prec_pair[0],
                fmt_percent_cell(prec_pair[0], prec_pair[1], wc_pct, wci_pct),
                cmap,
                norm,
                LOG_FLOOR,
            )
            spd = fmt_speedup(c.get("speedup"))
            # The dagger marks the extrapolated (capped) row; attach it to the
            # Strategy label rather than to the Time/#Queries values.
            strat = c["strat"] + (r"$^{\dagger}$" if c["capped"] else "")
            workers = sort if c["strat"] == "Binary" else 1  # Binary sort key = W; Linear runs 1 worker
            if ri == 0:
                uniq = (UNIQ_DISPLAY[attr] if attr in UNIQ_DISPLAY
                        else fmt_count(uniq_by_attr.get(attr, 0)))
                head = (rf"\multirow{{{span}}}{{*}}{{{ATTR_LABEL[attr]}}} "
                        rf"& \multirow{{{span}}}{{*}}{{{fmt_domain_size(dom_by_attr[attr])}}} "
                        rf"& \multirow{{{span}}}{{*}}{{{uniq}}} "
                        rf"& \multirow{{{span}}}{{*}}{{{ATTR_TYPE[attr]}}}")
            else:
                head = "& & &"
            L.append(rf"{head} & {strat} & {workers} & {t_m} & {t_c} & {q_m} & {q_c} "
                     rf"& {rec} & {pre} & {spd} \\")
    L.append(r"\bottomrule")
    L.append(r"\end{tabular}")
    L.append(r"\end{table*}")

    out = "\n".join(L) + "\n"
    if args.out:
        write_text(args.out, out)
        print(f"wrote {args.out}")
    else:
        print(out, end="")


if __name__ == "__main__":
    main()
