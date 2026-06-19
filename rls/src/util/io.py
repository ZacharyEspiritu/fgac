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

import csv
import json
import os
from typing import Any, Iterable, List, Optional, Sequence


def ensure_parent_dir(path: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def write_text(path: str, text: str) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def write_json(path: str, payload: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=indent, sort_keys=sort_keys)


def write_csv(path: str, rows: Iterable[Sequence], header: Optional[Sequence[str]] = None) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if header:
            writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def load_csv(path: str) -> List[List[str]]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        return list(reader)
