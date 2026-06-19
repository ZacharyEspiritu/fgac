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

from typing import Iterable, List, Optional, Sequence, Set, Tuple

from .graph import build_graph, is_eulerian_component, weak_components
from .models import Component, Contig, Graph, LoadedKgrams, Node, Reconstruction


def edge_path_tokens(graph: Graph, edge_ids: Sequence[int]) -> List[str]:
    if not edge_ids:
        return []
    first = graph.edges[edge_ids[0]]
    tokens = list(first.src)
    for edge_id in edge_ids:
        edge = graph.edges[edge_id]
        tokens.append(edge.dst[-1])
    return tokens


def make_contig(
    graph: Graph,
    *,
    component_id: int,
    edge_ids: List[int],
    traversal: str,
) -> Contig:
    return Contig(
        component_id=component_id,
        edge_ids=edge_ids,
        tokens=edge_path_tokens(graph, edge_ids),
        traversal=traversal,
    )


def is_linear_node(graph: Graph, node: Node) -> bool:
    return graph.indegree.get(node, 0) == 1 and graph.outdegree.get(node, 0) == 1


def available_outgoing_edges(
    graph: Graph,
    node: Node,
    component_edges: Set[int],
    used: Set[int],
) -> List[int]:
    return [
        edge_id
        for edge_id in graph.adjacency.get(node, [])
        if edge_id in component_edges and edge_id not in used
    ]


def walk_unitig_path(
    graph: Graph,
    first_edge_id: int,
    component_edges: Set[int],
    used: Set[int],
) -> List[int]:
    path = [first_edge_id]
    used.add(first_edge_id)
    current = graph.edges[first_edge_id].dst
    while is_linear_node(graph, current):
        next_edges = available_outgoing_edges(graph, current, component_edges, used)
        if not next_edges:
            break
        next_edge_id = next_edges[0]
        path.append(next_edge_id)
        used.add(next_edge_id)
        current = graph.edges[next_edge_id].dst
    return path


def walk_cycle_path(
    graph: Graph,
    first_edge_id: int,
    component_edges: Set[int],
    used: Set[int],
) -> List[int]:
    path = [first_edge_id]
    used.add(first_edge_id)
    start = graph.edges[first_edge_id].src
    current = graph.edges[first_edge_id].dst
    while current != start:
        next_edges = available_outgoing_edges(graph, current, component_edges, used)
        if not next_edges:
            break
        next_edge_id = next_edges[0]
        path.append(next_edge_id)
        used.add(next_edge_id)
        current = graph.edges[next_edge_id].dst
    return path


def unitig_contigs(graph: Graph, components: Sequence[Component]) -> List[Contig]:
    contigs: List[Contig] = []
    used: Set[int] = set()

    for component in components:
        component_edges = set(component.edge_ids)
        starts = [
            node
            for node in component.nodes
            if graph.outdegree.get(node, 0) > 0 and not is_linear_node(graph, node)
        ]

        for start in sorted(starts):
            for first_edge_id in graph.adjacency.get(start, []):
                if first_edge_id in used or first_edge_id not in component_edges:
                    continue
                contigs.append(
                    make_contig(
                        graph,
                        component_id=component.component_id,
                        edge_ids=walk_unitig_path(
                            graph,
                            first_edge_id,
                            component_edges,
                            used,
                        ),
                        traversal="unitig",
                    )
                )

        # Components that are pure cycles have no non-1-in/1-out start node.
        for edge_id in sorted(
            component_edges,
            key=lambda value: graph.edges[value].kgram,
        ):
            if edge_id in used:
                continue
            contigs.append(
                make_contig(
                    graph,
                    component_id=component.component_id,
                    edge_ids=walk_cycle_path(graph, edge_id, component_edges, used),
                    traversal="unitig-cycle",
                )
            )

    return contigs


def euler_trail(graph: Graph, component: Component, start: Node) -> List[int]:
    component_edges = set(component.edge_ids)
    adjacency = {
        node: [
            edge_id
            for edge_id in reversed(graph.adjacency.get(node, []))
            if edge_id in component_edges
        ]
        for node in component.nodes
    }
    stack: List[Tuple[Node, Optional[int]]] = [(start, None)]
    path: List[int] = []

    while stack:
        node, _incoming = stack[-1]
        if adjacency.get(node):
            edge_id = adjacency[node].pop()
            stack.append((graph.edges[edge_id].dst, edge_id))
        else:
            _node, incoming = stack.pop()
            if incoming is not None:
                path.append(incoming)

    path.reverse()
    return path


def greedy_trails(graph: Graph, component: Component) -> List[List[int]]:
    unused = set(component.edge_ids)
    trails: List[List[int]] = []

    def candidate_starts() -> List[Node]:
        starts = []
        for node in component.nodes:
            if any(edge_id in unused for edge_id in graph.adjacency.get(node, [])):
                starts.append(node)
        return sorted(
            starts,
            key=lambda node: (
                -(graph.outdegree.get(node, 0) - graph.indegree.get(node, 0)),
                node,
            ),
        )

    while unused:
        starts = candidate_starts()
        if not starts:
            break
        current = starts[0]
        path: List[int] = []
        while True:
            next_edges = [
                edge_id
                for edge_id in graph.adjacency.get(current, [])
                if edge_id in unused
            ]
            if not next_edges:
                break
            edge_id = next_edges[0]
            unused.remove(edge_id)
            path.append(edge_id)
            current = graph.edges[edge_id].dst
        if path:
            trails.append(path)

    return trails


def euler_contigs(graph: Graph, components: Sequence[Component]) -> List[Contig]:
    contigs: List[Contig] = []
    for component in components:
        is_eulerian, start = is_eulerian_component(graph, component)
        if is_eulerian and start is not None:
            path = euler_trail(graph, component, start)
            if path:
                contigs.append(
                    make_contig(
                        graph,
                        component_id=component.component_id,
                        edge_ids=path,
                        traversal="euler",
                    )
                )
            continue

        for path in greedy_trails(graph, component):
            contigs.append(
                make_contig(
                    graph,
                    component_id=component.component_id,
                    edge_ids=path,
                    traversal="greedy",
                )
            )

    return contigs


def traverse_graph(
    graph: Graph,
    components: Sequence[Component],
    traversal: str,
) -> List[Contig]:
    if traversal == "unitigs":
        return unitig_contigs(graph, components)
    if traversal == "euler":
        return euler_contigs(graph, components)
    raise ValueError(f"unknown traversal: {traversal}")


def sort_contigs(contigs: Iterable[Contig]) -> List[Contig]:
    return sorted(contigs, key=lambda contig: (-contig.edge_count, contig.text))


def select_contigs(
    contigs: Iterable[Contig],
    *,
    min_edges: int,
    max_contigs: int,
) -> tuple[List[Contig], int]:
    filtered = [contig for contig in contigs if contig.edge_count >= min_edges]
    total_contigs = len(filtered)
    selected = sort_contigs(filtered)
    if max_contigs > 0:
        selected = selected[:max_contigs]
    return selected, total_contigs


def reconstruct(
    loaded: LoadedKgrams,
    *,
    traversal: str,
    min_edges: int,
    max_contigs: int,
) -> Reconstruction:
    graph = build_graph(loaded.kgrams, loaded.k)
    components = weak_components(graph)
    contigs = traverse_graph(graph, components, traversal)
    selected, total_contigs = select_contigs(
        contigs,
        min_edges=min_edges,
        max_contigs=max_contigs,
    )
    return Reconstruction(
        loaded=loaded,
        graph=graph,
        components=components,
        contigs=selected,
        total_contigs=total_contigs,
    )

