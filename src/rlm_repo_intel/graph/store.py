"""Graph storage — load, query, and update the codebase graph."""

import json
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Node:
    id: str
    type: str
    data: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    target: str
    type: str
    data: dict = field(default_factory=dict)


class GraphStore:
    """In-memory graph with JSON persistence.
    
    Designed to be loaded into the RLM REPL environment as a queryable variable.
    """

    def __init__(self, graph_dir: str | Path):
        self.graph_dir = Path(graph_dir)
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self._adjacency: dict[str, list[Edge]] = defaultdict(list)
        self._reverse_adjacency: dict[str, list[Edge]] = defaultdict(list)

    def load(self):
        """Load graph from disk."""
        graph_path = self.graph_dir / "structural_graph.json"
        if not graph_path.exists():
            return

        with open(graph_path) as f:
            data = json.load(f)

        for n in data.get("nodes", []):
            node = Node(id=n["id"], type=n["type"], data=n)
            self.nodes[n["id"]] = node

        for e in data.get("edges", []):
            edge = Edge(source=e["source"], target=e["target"], type=e["type"], data=e)
            self.edges.append(edge)
            self._adjacency[e["source"]].append(edge)
            self._reverse_adjacency[e["target"]].append(edge)

    def save(self):
        """Persist graph to disk."""
        data = {
            "nodes": [{"id": n.id, "type": n.type, **n.data} for n in self.nodes.values()],
            "edges": [{"source": e.source, "target": e.target, "type": e.type, **e.data} for e in self.edges],
        }
        with open(self.graph_dir / "structural_graph.json", "w") as f:
            json.dump(data, f, indent=2)

    def add_node(self, node_id: str, node_type: str, **data):
        """Add or update a node."""
        self.nodes[node_id] = Node(id=node_id, type=node_type, data=data)

    def add_edge(self, source: str, target: str, edge_type: str, **data):
        """Add an edge."""
        edge = Edge(source=source, target=target, type=edge_type, data=data)
        self.edges.append(edge)
        self._adjacency[source].append(edge)
        self._reverse_adjacency[target].append(edge)

    def neighbors(self, node_id: str, radius: int = 1, edge_types: list[str] | None = None) -> dict:
        """Get neighborhood of a node up to given radius.
        
        Returns dict of {node_id: Node} for all nodes within radius hops.
        This is the primary method used by PR evaluation to get context.
        """
        visited = {node_id}
        frontier = {node_id}
        result = {}

        if node_id in self.nodes:
            result[node_id] = self.nodes[node_id]

        for _ in range(radius):
            next_frontier = set()
            for nid in frontier:
                # Forward edges
                for edge in self._adjacency.get(nid, []):
                    if edge_types and edge.type not in edge_types:
                        continue
                    if edge.target not in visited:
                        visited.add(edge.target)
                        next_frontier.add(edge.target)
                        if edge.target in self.nodes:
                            result[edge.target] = self.nodes[edge.target]

                # Reverse edges
                for edge in self._reverse_adjacency.get(nid, []):
                    if edge_types and edge.type not in edge_types:
                        continue
                    if edge.source not in visited:
                        visited.add(edge.source)
                        next_frontier.add(edge.source)
                        if edge.source in self.nodes:
                            result[edge.source] = self.nodes[edge.source]

            frontier = next_frontier

        return result

    def get_by_type(self, node_type: str) -> list[Node]:
        """Get all nodes of a given type."""
        return [n for n in self.nodes.values() if n.type == node_type]

    def get_module_for_file(self, file_path: str) -> Node | None:
        """Find the module that contains a given file."""
        file_id = f"file:{file_path}"
        for edge in self._reverse_adjacency.get(file_id, []):
            if edge.type == "contains" and edge.source.startswith("module:"):
                return self.nodes.get(edge.source)
        return None

    def files_in_module(self, module_id: str) -> list[Node]:
        """Get all files in a module."""
        result = []
        for edge in self._adjacency.get(module_id, []):
            if edge.type == "contains" and edge.target in self.nodes:
                result.append(self.nodes[edge.target])
        return result

    def map_files_to_modules(self, file_paths: list[str]) -> list[str]:
        """Map a list of file paths to their containing modules."""
        modules = set()
        for fp in file_paths:
            mod = self.get_module_for_file(fp)
            if mod:
                modules.add(mod.id)
        return list(modules)

    def stats(self) -> dict:
        """Return graph statistics."""
        type_counts = defaultdict(int)
        for n in self.nodes.values():
            type_counts[n.type] += 1
        edge_type_counts = defaultdict(int)
        for e in self.edges:
            edge_type_counts[e.type] += 1
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "node_types": dict(type_counts),
            "edge_types": dict(edge_type_counts),
        }
