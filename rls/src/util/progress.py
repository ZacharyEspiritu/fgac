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

import os
import re
import shutil
import sys
import time


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class ProgressBar:
    def __init__(self, total: int, label: str, width: int = 28, interval_s: float = 0.5) -> None:
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self.interval_s = interval_s
        self.start = time.perf_counter()
        self.last = 0.0
        self.last_visible_len = 0
        self.disabled = os.environ.get("RLS_PROGRESS", "1") == "0"
        self.use_color = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None

    def update(self, current: int, preview: str = "") -> None:
        if self.disabled:
            return
        now = time.perf_counter()
        if now - self.last < self.interval_s and current < self.total:
            return
        self.last = now
        ratio = min(max(current / self.total, 0.0), 1.0)
        elapsed = now - self.start
        rate = current / elapsed if elapsed > 0 else 0.0
        eta = (self.total - current) / rate if rate > 0 else 0.0
        stats = f"{current}/{self.total} {rate:.1f}/s ETA {eta:.1f}s"

        columns = shutil.get_terminal_size((80, 20)).columns
        min_bar_width = 10
        base_len = len(self.label) + len(stats) + 5
        preview_text = ""
        if preview:
            max_preview_total = max(0, columns - base_len - min_bar_width)
            if max_preview_total >= 4:
                preview_max = max_preview_total - 3
                preview_trimmed = preview
                if len(preview_trimmed) > preview_max:
                    if preview_max >= 3:
                        preview_trimmed = preview_trimmed[: preview_max - 3] + "..."
                    else:
                        preview_trimmed = preview_trimmed[:preview_max]
                if preview_trimmed:
                    preview_text = f" | {preview_trimmed}"

        bar_width = columns - len(self.label) - len(stats) - len(preview_text) - 5
        if bar_width < 3:
            bar_width = 3
        filled = int(bar_width * ratio)
        empty = bar_width - filled
        if self.use_color:
            bar = (
                f"\x1b[32m{'#' * filled}\x1b[0m"
                f"\x1b[90m{'-' * empty}\x1b[0m"
            )
        else:
            bar = "#" * filled + "-" * empty

        if self.use_color:
            label = f"\x1b[36m{self.label}\x1b[0m"
            stats_text = f"\x1b[33m{stats}\x1b[0m"
            preview_text = f"\x1b[35m{preview_text}\x1b[0m" if preview_text else ""
        else:
            label = self.label
            stats_text = stats
        msg = f"{label} [{bar}] {stats_text}{preview_text}"
        visible_len = len(ANSI_RE.sub("", msg))
        if visible_len < self.last_visible_len:
            msg = msg + (" " * (self.last_visible_len - visible_len))
        self.last_visible_len = len(ANSI_RE.sub("", msg))
        print(msg, end="\r", file=sys.stderr)
        if current >= self.total:
            print(file=sys.stderr)
