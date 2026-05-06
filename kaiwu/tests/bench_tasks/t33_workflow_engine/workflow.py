"""Workflow definition: nodes, edges, and parallel gateway logic."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set
from enum import Enum


class NodeType(Enum):
    START = "start"
    END = "end"
    TASK = "task"
    PARALLEL_SPLIT = "parallel_split"   # fork: one in, many out
    PARALLEL_JOIN = "parallel_join"     # merge: many in, one out


@dataclass
class Node:
    node_id: str
    node_type: NodeType
    name: str = ""


@dataclass
class Edge:
    edge_id: str
    source_id: str
    target_id: str
    condition: Optional[str] = None   # None means unconditional


class WorkflowDefinition:
    """Immutable workflow graph."""

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id
        self._nodes: Dict[str, Node] = {}
        self._edges: List[Edge] = []

    def add_node(self, node: Node) -> None:
        self._nodes[node.node_id] = node

    def add_edge(self, edge: Edge) -> None:
        self._edges.append(edge)

    def get_node(self, node_id: str) -> Node:
        return self._nodes[node_id]

    def outgoing_edges(self, node_id: str) -> List[Edge]:
        return [e for e in self._edges if e.source_id == node_id]

    def incoming_edges(self, node_id: str) -> List[Edge]:
        return [e for e in self._edges if e.target_id == node_id]

    def all_nodes(self) -> List[Node]:
        return list(self._nodes.values())

    def start_node(self) -> Node:
        for node in self._nodes.values():
            if node.node_type == NodeType.START:
                return node
        raise ValueError("No START node defined")

    def can_join(self, join_node_id: str, completed: Set[str], skipped: Set[str]) -> bool:
        """
        Return True when all branches feeding into a PARALLEL_JOIN have
        either completed or been skipped.

        BUG: only checks `completed`; ignores `skipped`, so a join that
        has one branch completed and one branch skipped will never fire.
        """
        incoming = self.incoming_edges(join_node_id)
        required_sources = {e.source_id for e in incoming}
        # BUG: should be `required_sources <= (completed | skipped)`
        return required_sources <= completed
