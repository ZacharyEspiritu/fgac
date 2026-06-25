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

from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Optional

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult, RenderableType
from rich.live import Live
from rich.measure import Measurement
from rich.panel import Panel
from rich.progress import (
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
)
from rich.segment import Segment
from rich.style import StyleType
from rich.table import Column
from rich.text import Text


def _style_for_label(label: str) -> str:
    normalized = label.strip().lower()
    if normalized == "doctors":
        return "bright_magenta"
    if normalized == "patients":
        return "bright_green"
    return "bright_blue"


def _pulse_start(
    animation_time: float,
    remaining_width: int,
    pulse_width: int,
    pulse_gap: int,
    pulse_speed: float,
) -> int:
    cycle_width = max(1, remaining_width + pulse_width + pulse_gap)
    return int(animation_time * pulse_speed) % cycle_width - pulse_width


class PulseOverlayBar:
    def __init__(
        self,
        *,
        total: Optional[float],
        completed: float,
        width: Optional[int],
        animation_time: float,
        style: StyleType,
        complete_style: StyleType,
        finished_style: StyleType,
        pulse_style: StyleType,
        pulse_width: int,
        pulse_gap: int,
        pulse_speed: float,
    ) -> None:
        self.total = total
        self.completed = completed
        self.width = width
        self.animation_time = animation_time
        self.style = style
        self.complete_style = complete_style
        self.finished_style = finished_style
        self.pulse_style = pulse_style
        self.pulse_width = pulse_width
        self.pulse_gap = pulse_gap
        self.pulse_speed = pulse_speed

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        width = min(self.width or options.max_width, options.max_width)
        ascii_only = options.legacy_windows or options.ascii_only
        bar = "-" if ascii_only else "━"
        half_bar_right = " " if ascii_only else "╸"
        half_bar_left = " " if ascii_only else "╺"

        total = self.total if self.total and self.total > 0 else 1.0
        completed = min(total, max(0.0, self.completed))
        complete_halves = int(width * 2 * completed / total)
        bar_count = complete_halves // 2
        half_bar_count = complete_halves % 2
        remaining_bars = width - bar_count - half_bar_count
        is_finished = completed >= total

        background_style = console.get_style(self.style)
        complete_style = console.get_style(
            self.finished_style if is_finished else self.complete_style
        )
        pulse_style = console.get_style(self.pulse_style)

        if bar_count:
            yield Segment(bar * bar_count, complete_style)
        if half_bar_count:
            yield Segment(half_bar_right, complete_style)

        if remaining_bars <= 0:
            return

        if not half_bar_count and bar_count:
            yield Segment(half_bar_left, background_style)
            remaining_bars -= 1
            if remaining_bars <= 0:
                return

        visible_pulse_width = min(self.pulse_width, max(1, remaining_bars))
        pulse_start = _pulse_start(
            self.animation_time,
            remaining_bars,
            visible_pulse_width,
            self.pulse_gap,
            self.pulse_speed,
        )
        for index in range(remaining_bars):
            style = (
                pulse_style
                if not is_finished
                and pulse_start <= index < pulse_start + visible_pulse_width
                else background_style
            )
            yield Segment(bar, style)

    def __rich_measure__(
        self,
        _console: Console,
        options: ConsoleOptions,
    ) -> Measurement:
        if self.width is not None:
            return Measurement(self.width, self.width)
        return Measurement(4, options.max_width)


class PulseOverlayBarColumn(ProgressColumn):
    def __init__(
        self,
        *,
        bar_width: Optional[int],
        style: StyleType,
        complete_style: StyleType,
        finished_style: StyleType,
        pulse_style: StyleType,
        pulse_width: int = 5,
        pulse_gap: int = 18,
        pulse_speed: float = 8.0,
    ) -> None:
        super().__init__()
        self.bar_width = bar_width
        self.style = style
        self.complete_style = complete_style
        self.finished_style = finished_style
        self.pulse_style = pulse_style
        self.pulse_width = pulse_width
        self.pulse_gap = pulse_gap
        self.pulse_speed = pulse_speed

    def render(self, task: Task) -> PulseOverlayBar:
        return PulseOverlayBar(
            total=task.total,
            completed=task.completed,
            width=self.bar_width,
            animation_time=task.get_time(),
            style=self.style,
            complete_style=self.complete_style,
            finished_style=self.finished_style,
            pulse_style=self.pulse_style,
            pulse_width=self.pulse_width,
            pulse_gap=self.pulse_gap,
            pulse_speed=self.pulse_speed,
        )


class HeightLockedRenderable:
    def __init__(self, renderable: RenderableType) -> None:
        self.renderable = renderable
        self.min_height = 0

    def __rich_console__(
        self,
        console: Console,
        options: ConsoleOptions,
    ) -> RenderResult:
        lines = console.render_lines(self.renderable, options)
        self.min_height = max(self.min_height, len(lines))
        for idx, line in enumerate(lines):
            if idx:
                yield Segment.line()
            yield from line
        for _ in range(self.min_height - len(lines)):
            yield Segment.line()


def _format_elapsed(seconds: Optional[float]) -> Text:
    if seconds is None:
        return Text("-:--:--", style="progress.elapsed")
    return Text(str(timedelta(seconds=max(0, int(seconds)))), style="progress.elapsed")


def _format_eta(seconds: Optional[float]) -> Text:
    if seconds is None:
        return Text("-:--:--", style="progress.remaining")
    minutes, seconds_int = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return Text(
        f"{hours:d}:{minutes:02d}:{seconds_int:02d}", style="progress.remaining"
    )


class LabeledElapsedColumn(ProgressColumn):
    def __init__(self) -> None:
        super().__init__(table_column=Column(no_wrap=True))

    def render(self, task: Task) -> Text:
        elapsed = task.finished_time if task.finished else task.elapsed
        text = Text("Elapsed", style="dim")
        text.append("\n")
        text.append(_format_elapsed(elapsed))
        return text


class LabeledEtaColumn(ProgressColumn):
    max_refresh = 0.5

    def __init__(self) -> None:
        super().__init__(table_column=Column(no_wrap=True))

    def render(self, task: Task) -> Text:
        task_time = task.finished_time if task.finished else task.time_remaining
        text = Text("ETA", style="dim")
        text.append("\n")
        text.append(_format_eta(task_time))
        return text


class ProgressBar:
    def __init__(
        self,
        total: int,
        label: str,
        width: int = 28,
        interval_s: float = 0.5,
    ) -> None:
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self.interval_s = interval_s
        self.started_at = time.perf_counter()
        self.last = 0.0
        self.disabled = os.environ.get("RLS_PROGRESS", "1") == "0"
        self._stopped = False
        self._style = _style_for_label(self.label)
        self._console = Console(
            stderr=True,
            highlight=False,
            markup=False,
            no_color=os.environ.get("NO_COLOR") is not None,
            width=120,
        )
        self._progress = Progress(
            SpinnerColumn("dots12", style=self._style, speed=1.2),
            TextColumn(
                "{task.description}",
                style=f"bold {self._style}",
                markup=False,
                table_column=Column(no_wrap=True),
            ),
            PulseOverlayBarColumn(
                bar_width=self.width,
                style="bright_black",
                complete_style=self._style,
                finished_style=self._style,
                pulse_style=self._style,
            ),
            TextColumn(
                "{task.completed:>d}/{task.total:<d}",
                style="yellow",
                table_column=Column(no_wrap=True),
            ),
            TextColumn(
                "{task.fields[preview]}",
                style="magenta",
                markup=False,
                table_column=Column(ratio=1, overflow="ellipsis"),
            ),
            LabeledElapsedColumn(),
            LabeledEtaColumn(),
            console=self._console,
            auto_refresh=False,
            expand=True,
        )
        self._task_id: TaskID = self._progress.add_task(
            self.label,
            total=self.total,
            preview="",
        )
        self._body = HeightLockedRenderable(self._progress)
        self._use_live = self._console.is_terminal
        self._live = Live(
            self._panel(),
            console=self._console,
            auto_refresh=True,
            refresh_per_second=6.0,
            redirect_stdout=False,
            redirect_stderr=False,
            transient=False,
        )
        if not self.disabled:
            self._start()

    def _panel(self) -> Panel:
        return Panel(
            self._body,
            border_style=self._style,
            box=box.ROUNDED,
            padding=(0, 1),
        )

    def _start(self) -> None:
        if self._use_live:
            self._live.start(refresh=True)
            return
        self._console.print(self._panel())
        self._console.line()

    def _print_completion_line(self, current: int, preview: str) -> None:
        elapsed = str(
            timedelta(seconds=max(0, int(time.perf_counter() - self.started_at)))
        )
        message = Text("done ", style=self._style)
        message.append(self.label, style=f"bold {self._style}")
        message.append(f": {current}/{self.total}")
        if preview:
            message.append(f" {preview}", style="magenta")
        message.append(f" elapsed {elapsed}", style="dim")
        self._console.print(message)

    def update(self, current: int, preview: str = "") -> None:
        if self.disabled or self._stopped:
            return

        clamped_current = min(max(current, 0), self.total)
        now = time.perf_counter()
        if now - self.last < self.interval_s and clamped_current < self.total:
            return
        self.last = now

        self._progress.update(
            self._task_id,
            completed=clamped_current,
            preview=preview,
        )
        if self._use_live:
            if not self._live.is_started:
                self._live.start(refresh=True)
            self._live.refresh()

        if clamped_current >= self.total:
            if self._use_live:
                self._live.stop()
            else:
                self._print_completion_line(clamped_current, preview)
            self._console.line()
            self._stopped = True
