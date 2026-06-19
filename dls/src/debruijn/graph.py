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

from collections import defaultdict, deque
from typing import Any, Dict, List, Sequence, Set

from .loaders import tokenise_kgram
from .models import Component, Edge, Graph, Node


def build_graph(kgrams: Sequence[str], k: int) -> Graph:
    if k < 2:
        raise ValueError("de Bruijn reconstruction needs k >= 2")

    edges: List[Edge] = []
    adjacency: Dict[Node, List[int]] = defaultdict(list)
    reverse_adjacency: Dict[Node, List[int]] = defaultdict(list)
    nodes: Set[Node] = set()
    indegree: Dict[Node, int] = defaultdict(int)
    outdegree: Dict[Node, int] = defaultdict(int)

    for edge_id, kgram in enumerate(kgrams):
        tokens = tokenise_kgram(kgram)
        src = tokens[:-1]
        dst = tokens[1:]
        edge = Edge(edge_id=edge_id, kgram=kgram, tokens=tokens, src=src, dst=dst)
        edges.append(edge)
        adjacency[src].append(edge_id)
        reverse_adjacency[dst].append(edge_id)
        nodes.add(src)
        nodes.add(dst)
        outdegree[src] += 1
        indegree[dst] += 1

    for node in nodes:
        adjacency[node].sort(key=lambda edge_id: edges[edge_id].kgram)
        reverse_adjacency[node].sort(key=lambda edge_id: edges[edge_id].kgram)
        indegree.setdefault(node, 0)
        outdegree.setdefault(node, 0)

    return Graph(
        k=k,
        edges=edges,
        adjacency=dict(adjacency),
        reverse_adjacency=dict(reverse_adjacency),
        nodes=nodes,
        indegree=dict(indegree),
        outdegree=dict(outdegree),
    )


def weak_components(graph: Graph) -> List[Component]:
    seen: Set[Node] = set()
    components: List[Component] = []

    for start in sorted(graph.nodes):
        if start in seen:
            continue
        queue = deque([start])
        seen.add(start)
        nodes: Set[Node] = set()
        edge_ids: Set[int] = set()

        while queue:
            node = queue.popleft()
            nodes.add(node)
            incident = (
                graph.adjacency.get(node, [])
                + graph.reverse_adjacency.get(node, [])
            )
            for edge_id in incident:
                edge_ids.add(edge_id)
                edge = graph.edges[edge_id]
                for next_node in (edge.src, edge.dst):
                    if next_node not in seen:
                        seen.add(next_node)
                        queue.append(next_node)

        if edge_ids:
            components.append(
                Component(
                    component_id=len(components) + 1,
                    nodes=nodes,
                    edge_ids=edge_ids,
                )
            )

    return components


def graph_summary(graph: Graph, components: Sequence[Component]) -> Dict[str, Any]:
    branching_nodes = sum(
        1
        for node in graph.nodes
        if graph.indegree.get(node, 0) > 1 or graph.outdegree.get(node, 0) > 1
    )
    eulerian_components = sum(
        1 for component in components if is_eulerian_component(graph, component)[0]
    )
    return {
        "k": graph.k,
        "edges": len(graph.edges),
        "nodes": len(graph.nodes),
        "weak_components": len(components),
        "branching_nodes": branching_nodes,
        "eulerian_components": eulerian_components,
    }


def is_eulerian_component(
    graph: Graph,
    component: Component,
) -> tuple[bool, Node | None]:
    start: Node | None = None
    positive = 0
    negative = 0
    for node in component.nodes:
        diff = graph.outdegree.get(node, 0) - graph.indegree.get(node, 0)
        if diff == 1:
            positive += 1
            start = node
        elif diff == -1:
            negative += 1
        elif diff != 0:
            return False, None

    if positive == 1 and negative == 1:
        return True, start
    if positive == 0 and negative == 0:
        candidates = [
            node for node in component.nodes if graph.outdegree.get(node, 0) > 0
        ]
        return True, sorted(candidates)[0] if candidates else None
    return False, None

