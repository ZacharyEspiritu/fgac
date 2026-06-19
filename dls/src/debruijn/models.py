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

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple


Node = Tuple[str, ...]


@dataclass(frozen=True)
class Edge:
    edge_id: int
    kgram: str
    tokens: Tuple[str, ...]
    src: Node
    dst: Node


@dataclass
class Graph:
    k: int
    edges: List[Edge]
    adjacency: Dict[Node, List[int]]
    reverse_adjacency: Dict[Node, List[int]]
    nodes: Set[Node]
    indegree: Dict[Node, int]
    outdegree: Dict[Node, int]


@dataclass
class Component:
    component_id: int
    nodes: Set[Node]
    edge_ids: Set[int]


@dataclass
class Contig:
    component_id: int
    edge_ids: List[int]
    tokens: List[str]
    traversal: str

    @property
    def edge_count(self) -> int:
        return len(self.edge_ids)

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    @property
    def text(self) -> str:
        return " ".join(self.tokens)


@dataclass(frozen=True)
class KgramCandidate:
    name: str
    priority: int
    k: int
    kgrams: List[str]


@dataclass(frozen=True)
class LoadedKgrams:
    kind: str
    stage: str
    k: int
    kgrams: List[str]


@dataclass(frozen=True)
class Reconstruction:
    loaded: LoadedKgrams
    graph: Graph
    components: List[Component]
    contigs: List[Contig]
    total_contigs: int

