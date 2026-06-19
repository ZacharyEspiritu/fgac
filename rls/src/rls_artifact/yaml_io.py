#!/usr/bin/env python3
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

import importlib
from pathlib import Path
from typing import Protocol, cast


class YamlModule(Protocol):
    def safe_load(self, stream: object) -> object:
        ...

    def safe_dump(self, data: object, **kwargs: object) -> str:
        ...


def load_yaml(path: Path) -> object:
    yaml = _yaml_module()
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def dump_yaml(data: object) -> str:
    yaml = _yaml_module()
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=False)


def _yaml_module() -> YamlModule:
    return cast(YamlModule, importlib.import_module("yaml"))
