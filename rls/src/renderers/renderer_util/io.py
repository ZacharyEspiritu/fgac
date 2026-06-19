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

from contextlib import contextmanager
from typing import Iterator, Optional, TextIO, cast

from util.io import ensure_parent_dir, write_text as write_text


@contextmanager
def open_output_text(
    path: str,
    mode: str = "w",
    *,
    encoding: str = "utf-8",
    newline: Optional[str] = None,
) -> Iterator[TextIO]:
    ensure_parent_dir(path)
    with open(path, mode, encoding=encoding, newline=newline) as handle:
        yield cast(TextIO, handle)
