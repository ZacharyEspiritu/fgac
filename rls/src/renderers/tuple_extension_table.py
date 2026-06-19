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

"""Render the c4 tuple-extension reconstruction table (full-width table*, footnotesize).

Same visual language as single_attribute_table.py: RdYlGn_r heat-colored
Recall/Precision cells, CIs in a smaller \\scalebox font, mean +/- 95% CI
(Student-t over every rep found on disk), and the mean/CI alignment of the single-attribute table
(values right-aligned, CIs left-aligned so the +/- line up). One row per attribute
reconstruction ordering, grouped by leading-attribute selectivity (ssn > zip >
age, most selective first). For each worker count W in {1,2,4} there are
sub-columns for #Queries (millions), Recall (heatmap), Precision (heatmap), and
wall-clock Time (hours; column header "Time (h)"). Per column the lowest #Queries
and Time are bolded (with display-ties); the heat-colored Recall/Precision are not.

Reads results/<RUN_ID>/reconstruction_summary.json for the c4 recompute runs:
    W=1  tuplext-rc100k-c4[-rN]-<ord>
    W=2  tuplext-rc100kw2-c4[-rN]-<ord>
    W=4  tuplext-rc100kw4-c4[-rN]-<ord>
(<ord> in {sza,saz,asz,azs,zsa,zas}; every c4 / c4-r<N> rep tag present on disk is
auto-discovered, so backfilled reps are picked up with no edit; the 'repro' tag is skipped.) Per rep:
Recall = min(100, 100*tp/10^5), Precision = 100*tp/(tp+fp) from
tuple_step_stats["3"]; #Queries = attacker_query_total; Time = sum(stage_times_s)/3600 (wall).

Preamble (same as the single-attribute table, plus calc for the CI alignment):
    \\usepackage{booktabs,multirow,graphicx,calc} + \\usepackage[table]{xcolor}
    (graphicx: \\scalebox; calc: \\widthof; xcolor[table]: \\cellcolor).

Run from the repo root:
    uv run python -m renderers.tuple_extension_table --out results/table4/table4.tex
    # compact single-column W=1 table (one column per metric, single-row header):
    uv run python -m renderers.tuple_extension_table --workers 1 --out results/table4/table4.tex
"""
from __future__ import annotations
import argparse
import glob
import os
import re
from typing import Dict, List, Optional, Tuple

from renderers.renderer_util.reconstruction_table import (
    bold_math,
    ci_part,
    fmt_fixed,
    heatmap_accuracy_cell,
    load_json_file,
    mean_ci,
)
from renderers.renderer_util.io import write_text
from renderers.renderer_util.heatmap_table import build_color_norm

RESULTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results")
WORKERS = [1, 2, 4]
WORKER_PREFIX = {1: "tuplext-rc100k", 2: "tuplext-rc100kw2", 4: "tuplext-rc100kw4"}
TARGET = 100000
LOG_FLOOR = 0.01
DP = 1  # all metrics rendered to one decimal place

# Per-attribute selectivity rank (0 = most selective): ssn > zip > age. Orderings
# are listed by (1st, 2nd, 3rd)-attribute selectivity and grouped (midrules) by
# the leading attribute -> sza, saz | zsa, zas | asz, azs.
_SEL = {"s": 0, "z": 1, "a": 2}
_ATTR = {"s": "ssn", "z": "zip", "a": "age"}
ORDERINGS = sorted(["sza", "saz", "asz", "azs", "zsa", "zas"],
                   key=lambda o: tuple(_SEL[c] for c in o))

# Metric columns in display order: (key, group header, heatmap?, best-is-max?).
# Recall/Precision are heat-colored (not bolded); #Queries/Time bold the min per column.
METRICS = [
    ("q",      r"\textbf{Queries} ($\times 10^6$)", False, False),
    ("recall", r"\textbf{Recall}",                     True,  True),
    ("prec",   r"\textbf{Precision}",                  True,  True),
    ("wall",   r"\textbf{Time} (h)",                   False, False),
]


def ordering_label(o: str) -> str:
    # A script-size arrow ({\scriptstyle\to}) is narrower than the full-size \to (LaTeX
    # has no built-in narrower arrow glyph without extra symbol packages) and stays
    # centered on the math axis.
    return r"\,${\scriptstyle\to}$\,".join(rf"\texttt{{{_ATTR[c]}}}" for c in o)


# A rep tag is the base 'c4' run or a numbered 'c4-r<N>' repeat; anything else
# (e.g. the one-off 'repro' tag) is not counted as a repetition of this experiment.
_REP_TAG_RE = re.compile(r"^c4(?:-r(\d+))?$")


def discover_tags(results_dir: str, w: int, o: str) -> List[str]:
    """Rep tags present on disk for one (W, ordering): every directory
    {WORKER_PREFIX[w]}-<tag>-<o> whose <tag> matches 'c4' or 'c4-r<N>', so backfilled
    reps are picked up with no code change (the 'repro' tag is skipped). Sorted
    base-first then by rep number (base 'c4' == rep 1) for a deterministic order."""
    prefix, suffix = f"{WORKER_PREFIX[w]}-", f"-{o}"
    tags = set()
    for path in glob.glob(os.path.join(results_dir, f"{prefix}*{suffix}",
                                       "reconstruction_summary.json")):
        tag = os.path.basename(os.path.dirname(path))[len(prefix):-len(suffix)]
        if _REP_TAG_RE.match(tag):
            tags.add(tag)

    def rep_sort_key(tag: str) -> int:
        match = _REP_TAG_RE.match(tag)
        if match is None:
            raise ValueError(f"invalid tuple-extension repetition tag: {tag}")
        return int(match.group(1) or 1)

    return sorted(tags, key=rep_sort_key)


def load_cell(results_dir: str, w: int, o: str) -> Dict[str, List[float]]:
    """Per-rep recall/precision (%), #Queries (millions), wall (hours) for one (W, ordering)."""
    out: Dict[str, List[float]] = dict(recall=[], prec=[], q=[], wall=[])
    for tag in discover_tags(results_dir, w, o):
        path = os.path.join(results_dir, f"{WORKER_PREFIX[w]}-{tag}-{o}",
                            "reconstruction_summary.json")
        if not os.path.isfile(path):
            continue
        try:
            d = load_json_file(path)
        except Exception:
            continue
        s3 = d.get("tuple_step_stats", {}).get("3", {}) or {}
        tp, fp = s3.get("tp", 0), s3.get("fp", 0)
        out["recall"].append(min(100.0, 100.0 * tp / TARGET))
        out["prec"].append(100.0 * tp / max(tp + fp, 1))
        out["q"].append(d.get("attacker_query_total", 0) / 1e6)
        out["wall"].append(sum(d.get("stage_times_s", {}).values()) / 3600.0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", default=RESULTS)
    ap.add_argument("--out", default="", help="Write here instead of stdout.")
    ap.add_argument("--workers", default=",".join(map(str, WORKERS)),
                    help="Comma-separated worker counts to show; '1' renders the compact "
                         "single-column W=1 table.")
    args = ap.parse_args()
    workers = [int(x) for x in args.workers.split(",") if x]

    # cells[(ordering, W)] = {metric: (mean, ci)}.
    cells: Dict[Tuple[str, int], Dict[str, Tuple[Optional[float], Optional[float]]]] = {}
    for o in ORDERINGS:
        for w in workers:
            cells[(o, w)] = {k: mean_ci(v) for k, v in load_cell(args.results_dir, w, o).items()}

    # Shared accuracy heat scale anchored to the full 0-100pp range (vmax=100),
    # same cmap/gamma/log_floor as the policy accuracy heatmap and the single-attr table.
    cmap, norm = build_color_norm([100.0], LOG_FLOOR, gamma=0.3)

    # Per metric: a fixed CI-box width (widest CI -> \widthof) so means right-align
    # while CIs left-align; and, for the non-heat columns only, per column (metric, W)
    # the best *displayed* value to bold (min for #Queries/Time), so display-ties bold.
    wc: Dict[str, str] = {}
    wint: Dict[str, str] = {}
    best: Dict[Tuple[str, int], float] = {}
    for key, _, heat, is_max in METRICS:
        cis: List[float] = []
        for o in ORDERINGS:
            for w in workers:
                ci = cells[(o, w)][key][1]
                if ci is not None:
                    cis.append(ci)
        mci = max(cis) if cis else 0.0
        maxint = f"{mci:.{DP}f}".split(".")[0]               # widest integer part in column
        wint[key] = rf"\widthof{{{maxint}}}" if maxint != "0" else "0pt"
        wc[key] = rf"\widthof{{{ci_part(mci, wint[key], dp=DP)}}}"  # fits the widest CI fragment
        if heat:
            continue  # Recall/Precision are heat-colored and NOT bolded
        for w in workers:
            disp: List[float] = []
            for o in ORDERINGS:
                mean = cells[(o, w)][key][0]
                if mean is not None:
                    disp.append(round(mean, DP))
            if disp:
                best[(key, w)] = max(disp) if is_max else min(disp)

    def render(key: str, o: str, w: int, heat: bool) -> str:
        mean, ci = cells[(o, w)][key]
        if mean is None:
            return "--"
        m = fmt_fixed(mean, DP)
        if (key, w) in best and round(mean, DP) == best[(key, w)]:
            m = bold_math(m)                               # bold the best value (mean only)
        cip = ci_part(ci, wint[key], dp=DP) if ci is not None else ""
        body = m + rf"\makebox[{wc[key]}][l]{{{cip}}}"      # CI in a fixed-width left box
        if heat:
            body = heatmap_accuracy_cell(mean, body, cmap, norm, LOG_FLOOR)
        return body

    Wn = len(workers)
    env = "table" if Wn == 1 else "table*"        # compact single-column table for one worker
    worker_note = (rf"All values at $W={workers[0]}$ (single worker)."
                   if Wn == 1 else r"$W$ $=$ parallel workers.")
    # ---- emit LaTeX --------------------------------------------------------
    L: List[str] = []
    L.append("% Auto-generated by src/renderers/tuple_extension_table.py.")
    L.append(r"% Preamble: \usepackage{booktabs,multirow,graphicx,calc} + \usepackage[table]{xcolor}")
    L.append(r"% (graphicx: \scalebox; calc: \widthof; xcolor[table]: \cellcolor).")
    L.append(rf"\begin{{{env}}}[t]")
    L.append(r"\centering")
    L.append(r"\footnotesize")
    L.append(r"\setlength{\tabcolsep}{4pt}")
    if Wn == 1:
        # Compact single-worker table: the #Queries unit now lives in the header, so
        # the caption drops the "in millions" note and states the single-thread setting.
        L.append(
            r"\caption{Single-threaded tuple  reconstruction on a 100k row target set. "
            r"Student-$t$ 95\% CI over $3$ repetitions in small text. "
            r"\textbf{Takeaway:} Attack performs better when starting with most selective "
            r"attributes first. The low selectivity of \texttt{age} and \texttt{zip} introduces "
            r"timing variance which results in loss of accuracy.}")
    else:
        L.append(
            r"\caption{Tuple extension reconstruction on a 100k row target set. "
            r"\textit{Queries} in millions ($\times 10^6$), Student-$t$ 95\% CI over $n=3$ "
            r"repetitions in small text. " + worker_note
            + r" \textbf{Takeaway:} Parallel workers reduce wall-clock time, while "
            r"attribute ordering remains the main driver of query volume and accuracy.}")
    L.append(r"\label{table:tuplext-reconstruction}")
    L.append(rf"\begin{{tabular}}{{r{'r' * (4 * Wn)}}}")
    L.append(r"\toprule")
    if Wn == 1:
        # Compact: one column per metric (the single W is stated in the caption).
        # Single-line headers; #Queries keeps its ($\times 10^6$) unit inline on one
        # line. Ordering right-aligns in its (now r-aligned) column.
        heads = [r"\textbf{Ordering}"] + [
            rf"\multicolumn{{1}}{{c}}{{{label}}}" for _k, label, _h, _m in METRICS]
        L.append(" & ".join(heads) + r" \\")
    else:
        # Grouped header: metric name spanning each metric's Wn worker sub-columns,
        # then a centered W label under each.
        L.append("& " + " & ".join(rf"\multicolumn{{{Wn}}}{{c}}{{{label}}}"
                                   for _k, label, _h, _m in METRICS) + r" \\")
        L.append("".join(rf"\cmidrule(lr){{{2 + i * Wn}-{1 + (i + 1) * Wn}}}"
                         for i in range(len(METRICS))))
        wsub = [rf"\multicolumn{{1}}{{c}}{{\textbf{{\boldmath$W{{=}}{w}$}}}}" for w in workers]
        L.append(r"\textbf{Ordering} & " + " & ".join(wsub * len(METRICS)) + r" \\")
    L.append(r"\midrule")

    prev_lead: Optional[str] = None
    for o in ORDERINGS:
        if prev_lead is not None and o[0] != prev_lead:
            L.append(r"\midrule")
        prev_lead = o[0]
        row = [ordering_label(o)]
        for key, _label, heat, _m in METRICS:
            for w in workers:
                row.append(render(key, o, w, heat))
        L.append(" & ".join(row) + r" \\")

    L.append(r"\bottomrule")
    L.append(r"\end{tabular}")
    L.append(rf"\end{{{env}}}")

    out = "\n".join(L) + "\n"
    if args.out:
        write_text(args.out, out)
        print(f"wrote {args.out}")
    else:
        print(out, end="")


if __name__ == "__main__":
    main()
