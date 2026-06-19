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

import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, TextIO

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)
from rich.table import Table


STAGE_STYLES = {
    1: "bright_cyan",
    2: "bright_green",
    3: "bright_yellow",
    4: "bright_magenta",
}


def rich_available() -> bool:
    return True


def open_progress_console(
    *,
    progress_tty: bool,
    force_terminal: bool,
) -> tuple[Console, Optional[TextIO]]:
    if progress_tty:
        try:
            tty = open("/dev/tty", "w")
        except OSError:
            pass
        else:
            return (
                Console(
                    file=tty,
                    force_terminal=force_terminal or None,
                ),
                tty,
            )
    return (
        Console(
            stderr=True,
            force_terminal=force_terminal or None,
        ),
        None,
    )


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    return f"{minutes:02d}m {seconds:02d}s"


def metric(value: int) -> str:
    return f"{value:,}"


def style_for_stage(title: str) -> str:
    normalized = title.lower()
    for stage, style in STAGE_STYLES.items():
        if f"{stage}-gram" in normalized:
            return style
    return "bright_blue"


@dataclass
class ProgressSnapshot:
    dataset: str
    title: str
    status: str
    activity: str
    elapsed_seconds: float
    logical_queries: int
    msearch_requests: int
    injected_docs: int
    bulk_requests: int
    prefixes_tested: int
    exact_terms_tested: int
    prefix_batches: int
    exact_batches: int


class ProgressStats(Protocol):
    @property
    def logical_score_queries(self) -> int: ...

    @property
    def msearch_requests(self) -> int: ...

    @property
    def probe_docs_injected(self) -> int: ...

    @property
    def bulk_injection_requests(self) -> int: ...

    @property
    def prefixes_tested(self) -> int: ...

    @property
    def exact_terms_tested(self) -> int: ...

    @property
    def prefix_batches(self) -> int: ...

    @property
    def exact_batches(self) -> int: ...


class AttackProgressDisplay:
    def __init__(
        self,
        *,
        dataset: str,
        title: str,
        stats_getter: Callable[[], ProgressStats],
        activity_getter: Callable[[], str],
        progress_tty: bool = False,
        force_terminal: bool = False,
    ) -> None:
        self.dataset = dataset
        self.title = title
        self.stats_getter = stats_getter
        self.activity_getter = activity_getter
        self.progress_tty = progress_tty
        self.force_terminal = force_terminal
        self.started_at = time.monotonic()
        self.live: Optional[Live] = None
        self.console: Optional[Console] = None
        self.console_file: Optional[TextIO] = None
        self.rich_enabled = rich_available()
        self.status = "active"
        self.pulse: Optional[Progress] = None
        self.pulse_task_id: Optional[TaskID] = None

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.status = "active"
        if not self.rich_enabled:
            return
        self.configure_pulse()
        self.console, self.console_file = open_progress_console(
            progress_tty=self.progress_tty,
            force_terminal=self.force_terminal,
        )
        self.live = Live(
            self,
            console=self.console,
            refresh_per_second=6,
            transient=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self.live.start(refresh=True)

    def update(self, *, final: bool = False) -> None:
        self.status = "complete" if final else "active"
        if self.rich_enabled and self.live is not None:
            self.live.refresh()
            return
        self.write_plain(final=final)

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
        if self.console_file is not None:
            self.console_file.close()
            self.console_file = None
        self.pulse = None
        self.pulse_task_id = None

    def snapshot(self, *, status: str) -> ProgressSnapshot:
        stats = self.stats_getter()
        return ProgressSnapshot(
            dataset=self.dataset,
            title=self.title,
            status=status,
            activity=self.activity_getter(),
            elapsed_seconds=time.monotonic() - self.started_at,
            logical_queries=stats.logical_score_queries,
            msearch_requests=stats.msearch_requests,
            injected_docs=stats.probe_docs_injected,
            bulk_requests=stats.bulk_injection_requests,
            prefixes_tested=stats.prefixes_tested,
            exact_terms_tested=stats.exact_terms_tested,
            prefix_batches=stats.prefix_batches,
            exact_batches=stats.exact_batches,
        )

    def __rich__(self) -> Panel:
        return self.render(status=self.status)

    def configure_pulse(self) -> None:
        stage_style = style_for_stage(self.title)
        self.pulse = Progress(
            SpinnerColumn("dots12", style=stage_style, speed=1.2),
            TextColumn(f"[bold {stage_style}]{{task.description}}"),
            BarColumn(
                bar_width=34,
                complete_style=stage_style,
                finished_style=stage_style,
                pulse_style=stage_style,
            ),
            TextColumn("[bold white]{task.fields[elapsed]}"),
            expand=False,
        )
        self.pulse_task_id = self.pulse.add_task(
            "probing",
            total=None,
            elapsed="00m 00s",
        )

    def render(self, *, status: str) -> Panel:
        snapshot = self.snapshot(status=status)
        stage_style = style_for_stage(snapshot.title)
        pulse = self.pulse
        if pulse is None:
            self.configure_pulse()
            pulse = self.pulse
        if pulse is not None and self.pulse_task_id is not None:
            pulse.update(
                self.pulse_task_id,
                description="done" if status == "complete" else "probing",
                elapsed=format_duration(snapshot.elapsed_seconds),
            )

        summary = Table.grid(expand=True, padding=(0, 1))
        summary.add_column(justify="right", style=f"bold {stage_style}", ratio=1)
        summary.add_column(style="white", ratio=4)
        summary.add_row("Status", snapshot.status)
        summary.add_row("Last action", snapshot.activity)

        metrics = Table.grid(expand=True, padding=(0, 1))
        for _ in range(4):
            metrics.add_column(justify="right")
            metrics.add_column(justify="left")
        metrics.add_row(
            "[bold]Logical queries[/]",
            f"[{stage_style}]{metric(snapshot.logical_queries)}[/]",
            "[bold]Query batches[/]",
            f"[{stage_style}]{metric(snapshot.msearch_requests)}[/]",
            "[bold]Injected docs[/]",
            f"[{stage_style}]{metric(snapshot.injected_docs)}[/]",
            "[bold]Bulk requests[/]",
            f"[{stage_style}]{metric(snapshot.bulk_requests)}[/]",
        )
        metrics.add_row(
            "[bold]Prefixes tested[/]",
            f"[{stage_style}]{metric(snapshot.prefixes_tested)}[/]",
            "[bold]Exact checks[/]",
            f"[{stage_style}]{metric(snapshot.exact_terms_tested)}[/]",
            "[bold]Prefix batches[/]",
            f"[{stage_style}]{metric(snapshot.prefix_batches)}[/]",
            "[bold]Exact batches[/]",
            f"[{stage_style}]{metric(snapshot.exact_batches)}[/]",
        )

        assert pulse is not None
        return Panel(
            Group(pulse, summary, metrics),
            title=f"[bold {stage_style}]{snapshot.dataset} · {snapshot.title}[/]",
            title_align="left",
            border_style=stage_style,
            box=box.ROUNDED,
            padding=(1, 2),
        )

    def write_plain(self, *, final: bool) -> None:
        snapshot = self.snapshot(status="complete" if final else "active")
        print(
            "[progress] "
            f"status={snapshot.status} "
            f"elapsed={format_duration(snapshot.elapsed_seconds)} "
            f"last={snapshot.activity} "
            f"logical={snapshot.logical_queries} "
            f"msearch={snapshot.msearch_requests} "
            f"injected_docs={snapshot.injected_docs} "
            f"bulk={snapshot.bulk_requests} "
            f"prefixes={snapshot.prefixes_tested} "
            f"exact={snapshot.exact_terms_tested}",
            file=sys.stderr,
            flush=True,
        )


class SetupProgressDisplay:
    def __init__(
        self,
        *,
        dataset: str,
        attack_chars: str,
        ngram_size: Optional[int],
        total_steps: int,
        progress_tty: bool = False,
        force_terminal: bool = False,
    ) -> None:
        self.dataset = dataset
        self.attack_chars = attack_chars
        self.ngram_size = ngram_size
        self.total_steps = total_steps
        self.progress_tty = progress_tty
        self.force_terminal = force_terminal
        self.completed_steps = 0
        self.current_action = "starting setup"
        self.search_system = "connecting"
        self.index_mapping = "pending"
        self.started_at = time.monotonic()
        self.live: Optional[Live] = None
        self.console: Optional[Console] = None
        self.console_file: Optional[TextIO] = None
        self.progress: Optional[Progress] = None
        self.task_id: Optional[TaskID] = None
        self.rich_enabled = rich_available()

    def start(self) -> None:
        self.started_at = time.monotonic()
        if not self.rich_enabled:
            self.write_plain()
            return
        self.console, self.console_file = open_progress_console(
            progress_tty=self.progress_tty,
            force_terminal=self.force_terminal,
        )
        self.configure_progress()
        self.live = Live(
            self,
            console=self.console,
            refresh_per_second=6,
            transient=False,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self.live.start(refresh=True)

    def configure_progress(self) -> None:
        style = "grey62"
        self.progress = Progress(
            SpinnerColumn("dots12", style=style, speed=1.2),
            TextColumn(f"[bold {style}]{{task.description}}"),
            BarColumn(
                bar_width=34,
                complete_style=style,
                finished_style=style,
                pulse_style=style,
            ),
            TaskProgressColumn(),
            TextColumn("[bold white]{task.fields[elapsed]}"),
            expand=False,
        )
        self.task_id = self.progress.add_task(
            "setting up",
            total=self.total_steps,
            completed=self.completed_steps,
            elapsed="00m 00s",
        )

    def update(self, action: str, *, completed_steps: Optional[int] = None) -> None:
        self.current_action = action
        if completed_steps is not None:
            self.completed_steps = completed_steps
        self.refresh()

    def set_search_system(self, search_system: str) -> None:
        self.search_system = search_system
        self.refresh()

    def set_index_mapping(self, index_mapping: str) -> None:
        self.index_mapping = index_mapping
        self.refresh()

    def advance(self, next_action: str) -> None:
        self.completed_steps = min(self.completed_steps + 1, self.total_steps)
        self.current_action = next_action
        self.refresh()

    def finish(self) -> None:
        self.completed_steps = self.total_steps
        self.current_action = "setup complete"
        self.refresh()

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None
        if self.console_file is not None:
            self.console_file.close()
            self.console_file = None
        self.progress = None
        self.task_id = None

    def refresh(self) -> None:
        if self.rich_enabled and self.live is not None:
            self.live.refresh()
            return
        self.write_plain()

    def __rich__(self) -> Panel:
        return self.render()

    def render(self) -> Panel:
        style = "grey62"
        elapsed = format_duration(time.monotonic() - self.started_at)
        progress = self.progress
        if progress is None:
            self.configure_progress()
            progress = self.progress
        if progress is not None and self.task_id is not None:
            progress.update(
                self.task_id,
                description="ready" if self.completed_steps >= self.total_steps else "setting up",
                completed=self.completed_steps,
                elapsed=elapsed,
            )

        details = Table.grid(expand=True, padding=(0, 1))
        details.add_column(justify="right", style=f"bold {style}", ratio=1)
        details.add_column(style="white", ratio=4)
        status = (
            "setup complete"
            if self.completed_steps >= self.total_steps
            else "setting up experiment"
        )
        details.add_row("Status", status)
        details.add_row("Action", self.current_action)
        details.add_row("System", self.search_system)
        details.add_row("Attack chars", self.attack_chars)
        details.add_row(
            "N-gram size",
            str(self.ngram_size) if self.ngram_size is not None else "disabled",
        )
        details.add_row("Index mapping", self.index_mapping)

        assert progress is not None
        return Panel(
            Group(progress, details),
            title=f"[bold {style}]{self.dataset} · setup[/]",
            title_align="left",
            border_style=style,
            box=box.ROUNDED,
            padding=(1, 2),
        )

    def write_plain(self) -> None:
        print(
            "[setup] "
            f"{self.completed_steps}/{self.total_steps} "
            f"elapsed={format_duration(time.monotonic() - self.started_at)} "
            f"action={self.current_action} "
            f"system={self.search_system!r} "
            f"chars={self.attack_chars!r} "
            f"ngram_size={self.ngram_size if self.ngram_size is not None else 'disabled'}",
            file=sys.stderr,
            flush=True,
        )


class SetupProgressReporter:
    def __init__(
        self,
        *,
        enabled: bool,
        dataset: str,
        attack_chars: str,
        ngram_size: Optional[int],
        total_steps: int,
        progress_tty: bool = False,
        force_terminal: bool = False,
    ) -> None:
        self.display = (
            SetupProgressDisplay(
                dataset=dataset,
                attack_chars=attack_chars,
                ngram_size=ngram_size,
                total_steps=total_steps,
                progress_tty=progress_tty,
                force_terminal=force_terminal,
            )
            if enabled
            else None
        )

    def start(self) -> None:
        if self.display is not None:
            self.display.start()

    def update(self, action: str, *, completed_steps: Optional[int] = None) -> None:
        if self.display is not None:
            self.display.update(action, completed_steps=completed_steps)

    def set_search_system(self, search_system: str) -> None:
        if self.display is not None:
            self.display.set_search_system(search_system)

    def set_index_mapping(self, index_mapping: str) -> None:
        if self.display is not None:
            self.display.set_index_mapping(index_mapping)

    def advance(self, next_action: str) -> None:
        if self.display is not None:
            self.display.advance(next_action)

    def finish(self) -> None:
        if self.display is not None:
            self.display.finish()

    def stop(self) -> None:
        if self.display is not None:
            self.display.stop()
