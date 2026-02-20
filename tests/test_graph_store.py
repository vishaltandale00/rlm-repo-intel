from rlm_repo_intel.graph.store import GraphStore


def test_graph_store_add_and_neighbors(tmp_path):
    store = GraphStore(tmp_path)

    store.add_node("module:src/api", "module", path="src/api")
    store.add_node("file:src/api/main.py", "file", path="src/api/main.py")
    store.add_node("file:src/api/util.py", "file", path="src/api/util.py")

    store.add_edge("module:src/api", "file:src/api/main.py", "contains")
    store.add_edge("file:src/api/main.py", "file:src/api/util.py", "imports")

    neighbors = store.neighbors("file:src/api/main.py", radius=1)

    assert "file:src/api/main.py" in neighbors
    assert "module:src/api" in neighbors
    assert "file:src/api/util.py" in neighbors


def test_graph_store_persistence_and_query_helpers(tmp_path):
    store = GraphStore(tmp_path)
    store.add_node("module:src/core", "module", path="src/core")
    store.add_node("file:src/core/a.py", "file", path="src/core/a.py")
    store.add_edge("module:src/core", "file:src/core/a.py", "contains")
    store.save()

    loaded = GraphStore(tmp_path)
    loaded.load()

    module = loaded.get_module_for_file("src/core/a.py")
    assert module is not None
    assert module.id == "module:src/core"

    files = loaded.files_in_module("module:src/core")
    assert [node.id for node in files] == ["file:src/core/a.py"]
