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

import argparse
import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from debruijn.loaders import load_kgrams
from debruijn.render import output_metadata, render_reconstruction, write_output
from debruijn.traversal import reconstruct


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a de Bruijn graph from recovered k-grams in an attack result "
            "file and traverse it to reconstruct approximate document text."
        )
    )
    parser.add_argument(
        "result_file",
        type=Path,
        help="Stats JSON file, or a log containing 'recovered K-gram:' lines.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=None,
        help="Use recovered k-grams for this k. Defaults to the largest available k.",
    )
    parser.add_argument(
        "--source",
        choices=("recovered", "indexed", "recovered-indexed", "missing", "extra"),
        default="recovered",
        help=(
            "Which k-gram set to assemble from stats JSON. "
            "Logs only support recovered. Default: recovered."
        ),
    )
    parser.add_argument(
        "--traversal",
        choices=("unitigs", "euler"),
        default="unitigs",
        help=(
            "Traversal strategy. unitigs emits maximal non-branching paths; "
            "euler emits Euler trails for Eulerian components and greedy trails "
            "otherwise. Default: unitigs."
        ),
    )
    parser.add_argument(
        "--min-edges",
        type=int,
        default=1,
        help="Only emit contigs with at least this many k-gram edges. Default: 1.",
    )
    parser.add_argument(
        "--max-contigs",
        type=int,
        default=0,
        help="Maximum contigs to emit after sorting by edge count. 0 means all.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write reconstruction output to this file instead of stdout.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format. Default: text.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help=(
            "Also write JSON reconstruction output to this file. This reuses the "
            "same loaded graph and traversal as the primary output."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        loaded = load_kgrams(args.result_file, source=args.source, requested_k=args.k)
        reconstruction = reconstruct(
            loaded,
            traversal=args.traversal,
            min_edges=args.min_edges,
            max_contigs=args.max_contigs,
        )
        output = render_reconstruction(
            reconstruction,
            metadata=output_metadata(
                result_file=args.result_file,
                loaded=loaded,
                source=args.source,
                traversal=args.traversal,
            ),
            output_format=args.format,
        )
        write_output(output, args.output)
        if args.json_output is not None:
            json_output = render_reconstruction(
                reconstruction,
                metadata=output_metadata(
                    result_file=args.result_file,
                    loaded=loaded,
                    source=args.source,
                    traversal=args.traversal,
                ),
                output_format="json",
            )
            write_output(json_output, args.json_output)
        return 0
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
