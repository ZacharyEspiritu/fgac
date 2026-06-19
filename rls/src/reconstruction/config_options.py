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

import argparse
from typing import Optional

from reconstruction.config_loader import ReconstructionConfig


def string_option(
    namespace: argparse.Namespace,
    config: ReconstructionConfig,
    key: str,
    default: str,
) -> str:
    cli_value = getattr(namespace, key)
    if cli_value is not None:
        return str(cli_value)
    config_value = config.options.get(key)
    if config_value is None:
        return default
    if not isinstance(config_value, str):
        raise ValueError(f"Config option '{key}' must be a string")
    return config_value


def optional_string_option(
    namespace: argparse.Namespace,
    config: ReconstructionConfig,
    key: str,
) -> Optional[str]:
    cli_value = getattr(namespace, key)
    if cli_value is not None:
        return str(cli_value)
    config_value = config.options.get(key)
    if config_value is None:
        return None
    if not isinstance(config_value, str):
        raise ValueError(f"Config option '{key}' must be a string")
    return config_value


def int_option(
    namespace: argparse.Namespace,
    config: ReconstructionConfig,
    key: str,
    default: int,
) -> int:
    cli_value = getattr(namespace, key)
    if cli_value is not None:
        return int(cli_value)
    config_value = config.options.get(key)
    if config_value is None:
        return default
    if isinstance(config_value, bool) or not isinstance(config_value, int):
        raise ValueError(f"Config option '{key}' must be an integer")
    return config_value


def bool_option(
    namespace: argparse.Namespace,
    config: ReconstructionConfig,
    key: str,
    default: bool,
) -> bool:
    cli_value = getattr(namespace, key)
    if cli_value is not None:
        return bool(cli_value)
    config_value = config.options.get(key)
    if config_value is None:
        return default
    if not isinstance(config_value, bool):
        raise ValueError(f"Config option '{key}' must be true or false")
    return config_value
