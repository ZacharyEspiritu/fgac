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

import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .graph import graph_summary
from .models import Contig, LoadedKgrams, Reconstruction


def output_metadata(
    *,
    result_file: Path,
    loaded: LoadedKgrams,
    source: str,
    traversal: str,
) -> Dict[str, Any]:
    return {
        "path": str(result_file),
        "kind": loaded.kind,
        "stage": loaded.stage,
        "source": source,
        "traversal": traversal,
    }


def render_text(
    metadata: Dict[str, Any],
    summary: Dict[str, Any],
    contigs: Sequence[Contig],
    total_contigs: int,
) -> str:
    lines = [
        f"# source: {metadata['path']}",
        f"# input kind: {metadata['kind']}",
        f"# stage: {metadata['stage']}",
        f"# k: {summary['k']}",
        f"# recovered/input k-grams used: {summary['edges']}",
        f"# nodes: {summary['nodes']}",
        f"# weak components: {summary['weak_components']}",
        f"# branching nodes: {summary['branching_nodes']}",
        f"# Eulerian components: {summary['eulerian_components']}",
        f"# contigs emitted: {len(contigs)}/{total_contigs}",
        "",
    ]

    for index, contig in enumerate(contigs, start=1):
        lines.append(
            f">contig_{index} component={contig.component_id} "
            f"traversal={contig.traversal} edges={contig.edge_count} "
            f"tokens={contig.token_count}"
        )
        lines.append(contig.text)
        lines.append("")
    return "\n".join(lines)


def render_json(
    metadata: Dict[str, Any],
    summary: Dict[str, Any],
    contigs: Sequence[Contig],
    total_contigs: int,
) -> str:
    payload = {
        "metadata": metadata,
        "graph": summary,
        "contigs_emitted": len(contigs),
        "contigs_total": total_contigs,
        "contigs": [
            {
                "component_id": contig.component_id,
                "traversal": contig.traversal,
                "edge_count": contig.edge_count,
                "token_count": contig.token_count,
                "text": contig.text,
            }
            for contig in contigs
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_reconstruction(
    reconstruction: Reconstruction,
    *,
    metadata: Dict[str, Any],
    output_format: str,
) -> str:
    summary = graph_summary(reconstruction.graph, reconstruction.components)
    if output_format == "json":
        return render_json(
            metadata,
            summary,
            reconstruction.contigs,
            reconstruction.total_contigs,
        )
    if output_format == "text":
        return render_text(
            metadata,
            summary,
            reconstruction.contigs,
            reconstruction.total_contigs,
        )
    raise ValueError(f"unknown output format: {output_format}")


def write_output(output: str, path: Optional[Path]) -> None:
    if path is None:
        print(output)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output + "\n")

