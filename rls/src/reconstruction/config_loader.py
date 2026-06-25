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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Protocol, cast

from reconstruction.candidates import CandidateConfig
from reconstruction.types import JsonObject, JsonValue


DEFAULT_RECONSTRUCTION_CONFIG = "src/reconstruction/config/singleattr_binary.yml"
_MODULE_DEFAULT_CONFIG = (
    Path(__file__).resolve().parent / "config" / "singleattr_binary.yml"
)


@dataclass(frozen=True)
class ReconstructionConfig:
    path: str
    options: JsonObject
    candidates: Dict[str, CandidateConfig]


class _YamlModule(Protocol):
    def safe_load(self, _stream: object) -> object: ...


def load_reconstruction_config(path: str) -> ReconstructionConfig:
    resolved_path = _resolve_config_path(path)
    raw = _load_yaml(resolved_path)
    root = _as_json_object(raw, str(resolved_path))
    candidates_raw = root.get("candidates")
    if not isinstance(candidates_raw, dict):
        raise ValueError(f"{resolved_path} must define a top-level candidates mapping")

    options: JsonObject = {}
    for key, value in root.items():
        if key != "candidates":
            options[key] = value

    candidates: Dict[str, CandidateConfig] = dict(candidates_raw)
    return ReconstructionConfig(
        path=str(resolved_path),
        options=options,
        candidates=candidates,
    )


def _resolve_config_path(path: str) -> Path:
    requested = Path(path)
    if requested.exists():
        return requested
    if path == DEFAULT_RECONSTRUCTION_CONFIG and _MODULE_DEFAULT_CONFIG.exists():
        return _MODULE_DEFAULT_CONFIG
    return requested


def _load_yaml(path: Path) -> object:
    try:
        yaml = cast(_YamlModule, importlib.import_module("yaml"))
    except ImportError as exc:
        raise RuntimeError(
            "PyYAML is required to read reconstruction config files. "
            "Run rls/setup.sh to install the artifact dependencies."
        ) from exc

    try:
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except OSError as exc:
        raise ValueError(f"Unable to read reconstruction config {path}: {exc}") from exc


def _as_json_object(value: object, path: str) -> JsonObject:
    normalized = _as_json_value(value, path)
    if not isinstance(normalized, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return normalized


def _as_json_value(value: object, path: str) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_as_json_value(item, f"{path}[]") for item in value]
    if isinstance(value, dict):
        normalized: JsonObject = {}
        for key, raw_value in value.items():
            if not isinstance(key, str):
                raise ValueError(f"YAML key at {path} must be a string")
            normalized[key] = _as_json_value(raw_value, f"{path}.{key}")
        return normalized
    raise ValueError(f"Unsupported YAML value at {path}: {type(value).__name__}")
