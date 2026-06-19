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

from typing import List


def tikz_node(
    lines: List[str],
    x: float,
    y: float,
    text: str,
    font: str,
    color: str,
    *,
    extra: str = "",
) -> None:
    opts = [f"font={font}", f"text={color}", "inner sep=0pt", "align=center"]
    if extra:
        opts.append(extra)
    lines.append(rf"\node[{', '.join(opts)}] at ({x:.3f},{-y:.3f}) {{{text}}};")


def tikz_hrule(
    lines: List[str],
    y: float,
    x_left: float,
    x_right: float,
    width: str,
    *,
    color: str = "rlsOuter",
) -> None:
    lines.append(
        rf"\draw[{color}, line width={width}] "
        rf"({x_left:.3f},{-y:.3f}) -- ({x_right:.3f},{-y:.3f});"
    )


def tikz_rect(
    lines: List[str],
    left: float,
    top: float,
    width: float,
    height: float,
    fill: str,
    *,
    draw: str = "none",
    line_width: str = "0pt",
) -> None:
    fill_opt = "" if fill == "none" else f"fill={fill}, "
    lines.append(
        rf"\path[{fill_opt}draw={draw}, line width={line_width}] "
        rf"({left:.3f},{-top:.3f}) rectangle "
        rf"({left + width:.3f},{-(top + height):.3f});"
    )
