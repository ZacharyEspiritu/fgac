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

from reconstruction.cli import parse_args, print_params, validate_run_args
from reconstruction.runtime.context import open_reconstruction_context
from reconstruction.runner import run_reconstruction


def main() -> None:
    args = parse_args()
    validate_run_args(args)
    print_params(args)

    with open_reconstruction_context(args) as ctx:
        run_reconstruction(ctx)


if __name__ == "__main__":
    main()
