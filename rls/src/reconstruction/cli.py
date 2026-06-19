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

from dataclasses import asdict, dataclass
from typing import Dict, Literal, Optional, Sequence, cast

from reconstruction.candidates import CandidateConfig
from reconstruction.cli_parser import build_parser
from reconstruction.config_loader import load_reconstruction_config
from reconstruction.config_options import (
    bool_option,
    int_option,
    optional_string_option,
    string_option,
)


TupleExtensionMode = Literal["any", "between"]


@dataclass(frozen=True)
class ReconstructionOptions:
    attacker_dsn: str
    admin_dsn: str
    rls_policy: str
    table: str
    attributes: Optional[str]
    config: str
    candidates: Dict[str, CandidateConfig]
    skip_attr_probe: bool
    sample_tuples: int
    num_queries_for_initial_calibration: int
    num_queries_per_probe: int
    workers: int
    verify: bool
    output_dir: str
    no_progress_output: bool
    log_oracle_calls: bool
    tuple_recompute_threshold: bool
    tuple_recompute_cal_rounds: int
    tuple_extension_mode: TupleExtensionMode


def print_params(args: ReconstructionOptions) -> None:
    print("Parameters:")
    values = asdict(args)
    values.pop("candidates")
    values["candidate_attributes"] = ",".join(sorted(args.candidates))
    for key in sorted(values):
        print(f"  {key}={values[key]}")


def validate_run_args(args: ReconstructionOptions) -> None:
    if args.workers < 1:
        raise RuntimeError("--workers must be >= 1")
    if not args.admin_dsn:
        raise RuntimeError("--admin-dsn is required")
    if args.skip_attr_probe and args.sample_tuples <= 0:
        raise RuntimeError("--skip-attr-probe requires --sample-tuples > 0")
    if not args.candidates and not args.skip_attr_probe and args.sample_tuples <= 0:
        raise RuntimeError(
            "Select a reconstruction config with candidates, or use "
            "--skip-attr-probe/--sample-tuples."
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> ReconstructionOptions:
    parser = build_parser()
    namespace = parser.parse_args(argv)
    try:
        config = load_reconstruction_config(namespace.config)
        rls_policy = string_option(namespace, config, "rls_policy", "join")
        tuple_extension_mode = string_option(
            namespace, config, "tuple_extension_mode", "any"
        )
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    if rls_policy not in ("join", "inline"):
        parser.error("rls_policy must be one of: join, inline")
    if tuple_extension_mode not in ("any", "between"):
        parser.error("tuple_extension_mode must be one of: any, between")

    return ReconstructionOptions(
        attacker_dsn=namespace.attacker_dsn,
        admin_dsn=namespace.admin_dsn,
        rls_policy=rls_policy,
        table=string_option(namespace, config, "table", "patients"),
        attributes=optional_string_option(namespace, config, "attributes"),
        config=config.path,
        candidates=config.candidates,
        skip_attr_probe=bool_option(namespace, config, "skip_attr_probe", False),
        sample_tuples=int_option(namespace, config, "sample_tuples", 0),
        num_queries_for_initial_calibration=int_option(
            namespace, config, "num_queries_for_initial_calibration", 3
        ),
        num_queries_per_probe=int_option(
            namespace, config, "num_queries_per_probe", 1
        ),
        workers=int_option(namespace, config, "workers", 1),
        verify=bool_option(namespace, config, "verify", False),
        output_dir=string_option(namespace, config, "output_dir", "results"),
        no_progress_output=bool_option(
            namespace, config, "no_progress_output", False
        ),
        log_oracle_calls=bool_option(namespace, config, "log_oracle_calls", False),
        tuple_recompute_threshold=bool_option(
            namespace, config, "tuple_recompute_threshold", False
        ),
        tuple_recompute_cal_rounds=int_option(
            namespace, config, "tuple_recompute_cal_rounds", 1
        ),
        tuple_extension_mode=cast(TupleExtensionMode, tuple_extension_mode),
    )
