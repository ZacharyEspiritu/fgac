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

"""Shared rendering helpers for policy/comparison heatmap renderers.

Provides:
  - fmt_ci         — strip leading zero from a CI float or '± 0.xx' string
  - build_color_norm — RdYlGn_r PowerNorm cmap/norm used by both heatmaps
  - pgf_rgb        — RGBA tuple → 'R,G,B' string for TikZ \definecolor
  - pgf_draw_heatmap_cell  — append PGF commands for one filled heat cell
  - mpl_draw_heatmap_cell  — draw one filled heat cell in matplotlib
  - CELL_FONT_PGF / CELL_FONTSIZE_MPL / CI_FONTSIZE_MPL — shared font constants
"""
from __future__ import annotations

import math
import os
import tempfile
from typing import List, Optional

# ---- Shared font size constants ------------------------------------------------
CELL_FONT_PGF = r"\fontsize{6.0}{6.5}\selectfont"
CELL_FONTSIZE_MPL: float = 6.0
CI_FONTSIZE_MPL: float = 4.3

_CI_FONT_PGF = r"\fontsize{4.3}{4.7}\selectfont"


# ---- CI value formatting -------------------------------------------------------
def fmt_acc(value: float) -> str:
    """Format an accuracy percentage to two dp, ROUNDED DOWN (truncated).

    Flooring (rather than round-to-nearest) keeps the printed value at or below
    the true accuracy, so 'value ± CI' never implies an upper bound above 100%.
    A true 99.997% prints as '99.99', so '99.99 ± .01' tops out at exactly
    100.00 instead of the meaningless '100.0 ± .01' (= 100.01). A genuine 100.0%
    (all probes correct, CI 0) still prints as '100.0'. The +1e-6 absorbs binary
    float noise so e.g. 72.75 doesn't truncate to 72.74.
    """
    floored = math.floor(value * 100.0 + 1e-6) / 100.0
    return "100.0" if floored >= 100.0 else f"{floored:.2f}"


def fmt_ci(v: object) -> str:
    """Strip leading zero from a CI value.

    Accepts a float (0.06 → '.06') or the '± 0.06'-style string returned by
    heatmap_ci_text() (→ '.06').
    """
    if isinstance(v, str):
        s = v.lstrip("± ").lstrip()
    else:
        parsed = parse_float(v)
        s = "" if parsed is None else f"{parsed:.2f}"
    return s[1:] if s.startswith("0.") else s


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return float(text) if text else None
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"expected a numeric value, got {type(value).__name__}")


def ci_margin_pct(accuracy_pct: float, probes: int, z_value: float) -> float:
    if probes <= 0:
        return 0.0
    p = min(max(accuracy_pct / 100.0, 0.0), 1.0)
    return 100.0 * z_value * math.sqrt((p * (1.0 - p)) / probes)


def wilson_half_width(successes: int, trials: int, z: float = 2.5758) -> float:
    if trials <= 0:
        return 0.0
    p = successes / trials
    denom = 1.0 + z * z / trials
    half = (z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))) / denom
    return half * 100.0


def fmt_qps(value: Optional[float], missing: str = "-", comma: bool = False) -> str:
    if value is None:
        return missing
    if comma and abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.0f}"


def fmt_min_k(value: object) -> str:
    try:
        parsed = parse_float(value)
    except (TypeError, ValueError):
        return "—"
    if parsed is None:
        return "—"
    return f"{int(parsed)}"


def fmt_cost_multiplier(value: object) -> str:
    try:
        parsed = parse_float(value)
    except (TypeError, ValueError):
        return "—"
    if parsed is None:
        return "—"
    return f"{int(parsed)}×"


def latex_escape(value: object) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "±": r"$\pm$",
        "×": r"$\times$",
        "—": r"--",
        "−": r"$-$",
        "≥": r"$\geq$",
        "↔": r"$\leftrightarrow$",
        "Δ": r"$\Delta$",
    }
    return "".join(replacements.get(char, char) for char in str(value))


# ---- Color norm ----------------------------------------------------------------
def build_color_norm(finite_errors: List[float], log_floor: float = 0.01, gamma: float = 0.5):
    """Return (cmap, norm) for the shared RdYlGn_r PowerNorm heatmap.

    Args:
        finite_errors: pre-computed list of max(log_floor, 100 - accuracy) values.
        log_floor:     smallest non-zero error value used as vmin.
        gamma:         PowerNorm exponent (0.5 for cross-zone, 0.3 for unified).
    """
    os.environ.setdefault(
        "MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "rls_matplotlib")
    )
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import PowerNorm

    color_vmax = max(10.0, max(finite_errors) if finite_errors else log_floor * 10.0)
    cmap = plt.get_cmap("RdYlGn_r").copy()
    cmap.set_bad("white")
    norm = PowerNorm(gamma=gamma, vmin=log_floor, vmax=color_vmax, clip=True)
    return cmap, norm


# ---- PGF helpers ---------------------------------------------------------------
def pgf_rgb(color: object) -> str:
    red, green, blue = color[:3]  # type: ignore[index]
    return f"{round(255 * red)},{round(255 * green)},{round(255 * blue)}"


def pgf_draw_heatmap_cell(
    lines: List[str],
    left: float,
    y: float,
    width: float,
    value: float,
    ci_val: Optional[object],
    cmap,
    norm,
    color_idx: int,
    log_floor: float = 0.01,
    ci_show: bool = True,
) -> int:
    """Append PGF commands for one filled heatmap cell and return the next color_idx.

    Generates a \\definecolor, a \\path fill rectangle, and a \\node with the
    accuracy value and (when ci_show and ci_val are set) an inline CI suffix.
    """
    err = max(log_floor, 100.0 - round(value, 2))
    rgba = cmap(norm(err))
    fill_name = f"rlsHeat{color_idx}"
    lines.append(rf"\definecolor{{{fill_name}}}{{RGB}}{{{pgf_rgb(rgba)}}}")
    lines.append(
        rf"\path[fill={fill_name}, draw=none, line width=0pt] "
        rf"({left:.3f},{-y:.3f}) rectangle ({left + width:.3f},{-(y + 1.0):.3f});"
    )
    r, g, b, _ = rgba
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    text_color = "rlsText" if lum > 0.55 else "rlsWhite"
    if ci_show and ci_val is not None:
        cell_text = rf"{fmt_acc(value)}{{{_CI_FONT_PGF}\,$\pm${fmt_ci(ci_val)}}}"
    else:
        cell_text = fmt_acc(value)
    lines.append(
        rf"\node[font={CELL_FONT_PGF}, text={text_color}, inner sep=0pt, align=center] "
        rf"at ({left + width / 2.0:.3f},{-(y + 0.5):.3f}) {{{cell_text}}};"
    )
    return color_idx + 1


# ---- Matplotlib helpers --------------------------------------------------------
def mpl_draw_heatmap_cell(
    ax,
    left: float,
    y: float,
    width: float,
    value: float,
    ci_val: Optional[object],
    cmap,
    norm,
    log_floor: float = 0.01,
) -> None:
    """Draw one filled heatmap cell in matplotlib with inline CI to the right.

    Draws the background Rectangle, the main accuracy value anchored right of
    center, and (when ci_val is not None) a smaller CI text anchored left of
    center with a slight downward subscript offset.
    """
    from matplotlib.patches import Rectangle

    err = max(log_floor, 100.0 - round(value, 2))
    rgba = cmap(norm(err))
    ax.add_patch(Rectangle((left, y), width, 1.0, facecolor=rgba, edgecolor="none"))
    r, g, b, _ = rgba
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    tc = "#111111" if lum > 0.55 else "#FFFFFF"
    if ci_val is not None:
        ax.text(left + width / 2.0, y + 0.5, fmt_acc(value),
                ha="right", va="center", fontsize=CELL_FONTSIZE_MPL, color=tc)
        ax.text(left + width / 2.0 + width * 0.02, y + 0.56, f"±{fmt_ci(ci_val)}",
                ha="left", va="center", fontsize=CI_FONTSIZE_MPL, color=tc)
    else:
        ax.text(left + width / 2.0, y + 0.5, fmt_acc(value),
                ha="center", va="center", fontsize=CELL_FONTSIZE_MPL, color=tc)
