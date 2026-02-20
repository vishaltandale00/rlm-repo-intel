import json

from rlm_repo_intel.ingest.graph_builder import build_structural_graph


def _base_config(tmp_path):
    return {
        "paths": {
            "graph_dir": str(tmp_path / "graph"),
        }
    }


def test_build_structural_graph_empty_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config = _base_config(tmp_path)

    stats = build_structural_graph(repo_dir, config)

    assert stats == {"files": 0, "modules": 0, "import_edges": 0, "total_bytes": 0}

    graph_path = tmp_path / "graph" / "structural_graph.json"
    graph = json.loads(graph_path.read_text())
    assert graph == {"nodes": [], "edges": []}


def test_build_structural_graph_single_file_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("print('hello')\n")
    config = _base_config(tmp_path)

    stats = build_structural_graph(repo_dir, config)

    assert stats["files"] == 1
    assert stats["modules"] == 0
    assert stats["import_edges"] == 0
    assert stats["total_bytes"] > 0

    graph_path = tmp_path / "graph" / "structural_graph.json"
    graph = json.loads(graph_path.read_text())
    file_nodes = [node for node in graph["nodes"] if node["type"] == "file"]
    assert len(file_nodes) == 1
    assert file_nodes[0]["path"] == "main.py"
    assert graph["edges"] == []
