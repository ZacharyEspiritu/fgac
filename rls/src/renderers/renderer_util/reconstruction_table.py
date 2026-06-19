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

import json
import math
import statistics
from typing import Optional, Sequence, Tuple

from .heatmap_table import pgf_rgb


STUDENT_T_95 = {
    2: 12.706,
    3: 4.303,
    4: 3.182,
    5: 2.776,
    6: 2.571,
    7: 2.447,
    8: 2.365,
    9: 2.306,
    10: 2.262,
}


def load_json_file(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def mean_ci(values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    n = len(values)
    if n == 0:
        return None, None
    mean = statistics.mean(values)
    if n < 2:
        return mean, None
    return mean, STUDENT_T_95.get(n, 1.96) * statistics.stdev(values) / math.sqrt(n)


def required_mean_ci(values: Sequence[float]) -> Tuple[float, Optional[float]]:
    mean, ci = mean_ci(values)
    if mean is None:
        raise ValueError("mean_ci requires at least one value")
    return mean, ci


def ci_fragment(ci: float, int_width: str, dp: int = 1) -> str:
    int_part, frac = f"{ci:.{dp}f}".split(".")
    if int_part == "0":
        int_part = ""
    return rf"\,\scalebox{{0.7}}{{$\pm$\,\makebox[{int_width}][r]{{{int_part}}}.{frac}}}"


def ci_part(ci: float, int_width: str, dp: int = 1) -> str:
    return ci_fragment(ci, int_width, dp=dp)


def latex_commas(value: float) -> str:
    return f"{round(value):,}".replace(",", "{,}")


def bold_math(text: str) -> str:
    return rf"\textbf{{\boldmath {text}}}"


def fmt_fixed(value: float, dp: int = 1) -> str:
    return f"{value:.{dp}f}"


def fmt_percent_cell(
    mean_pct: float,
    ci_pct: Optional[float],
    cell_width: str,
    ci_int_width: str,
    *,
    mean_dp: int = 2,
    ci_dp: int = 2,
) -> str:
    floored = math.floor(mean_pct * (10 ** mean_dp) + 1e-6) / (10 ** mean_dp)
    mean = f"{100.0:.{mean_dp}f}" if floored >= 100.0 else f"{floored:.{mean_dp}f}"
    ci_text = ""
    if ci_pct is not None:
        ci = min(ci_pct, max(0.0, 100.0 - floored))
        ci_text = ci_part(ci, ci_int_width, dp=ci_dp)
    return mean + rf"\makebox[{cell_width}][l]{{{ci_text}}}"


def fmt_domain_size(value: int) -> str:
    if value >= 1000:
        log10_value = math.log10(value)
        if abs(log10_value - round(log10_value)) < 1e-9:
            return rf"$10^{{{round(log10_value)}}}$"
    return latex_commas(value)


def fmt_count(value: int) -> str:
    return latex_commas(value)


def fmt_speedup(value: Optional[float]) -> str:
    return rf"{value:.1f}$\times$" if value is not None else "--"


def heatmap_accuracy_cell(
    value: float,
    body: str,
    cmap,
    norm,
    log_floor: float = 0.01,
) -> str:
    err = max(log_floor, 100.0 - round(value, 2))
    red, green, blue, _ = cmap(norm(err))
    cell_color = rf"\cellcolor[RGB]{{{pgf_rgb((red, green, blue))}}}"
    if 0.2126 * red + 0.7152 * green + 0.0722 * blue <= 0.55:
        body = rf"\textcolor{{white}}{{{body}}}"
    return f"{cell_color}{body}"
