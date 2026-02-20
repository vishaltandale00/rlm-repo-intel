from rlm_repo_intel.tools.repo_loader import build_repo_tree, load_repo_to_repl


def test_load_repo_to_repl_loads_text_and_skips_binary_and_ignored_dirs(tmp_path):
    repo_root = tmp_path / "repo" / "acme" / "widget"
    repo_root.mkdir(parents=True)

    (repo_root / "app.py").write_text("print('ok')\n")
    (repo_root / "pkg").mkdir()
    (repo_root / "pkg" / "mod.txt").write_text("module\n")

    (repo_root / ".git").mkdir()
    (repo_root / ".git" / "config").write_text("ignored\n")
    (repo_root / "__pycache__").mkdir()
    (repo_root / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01")
    (repo_root / "node_modules").mkdir()
    (repo_root / "node_modules" / "index.js").write_text("ignored\n")
    (repo_root / ".venv").mkdir()
    (repo_root / ".venv" / "pyvenv.cfg").write_text("ignored\n")
    (repo_root / "binary.bin").write_bytes(b"\x00\xff\x01")

    config = {
        "paths": {"repo_dir": str(tmp_path / "repo")},
        "repo": {"owner": "acme", "name": "widget"},
    }

    repo = load_repo_to_repl(config)

    assert repo == {
        "app.py": "print('ok')\n",
        "pkg/mod.txt": "module\n",
    }


def test_load_repo_to_repl_returns_empty_when_repo_missing(tmp_path):
    config = {
        "paths": {"repo_dir": str(tmp_path / "repo")},
        "repo": {"owner": "missing", "name": "repo"},
    }

    assert load_repo_to_repl(config) == {}


def test_build_repo_tree_renders_hierarchy():
    repo = {
        "src/rlm_repo_intel/pipeline/rlm_session.py": "session\n",
        "src/rlm_repo_intel/tools/repo_loader.py": "loader\n",
        "README.md": "readme\n",
    }

    assert build_repo_tree(repo) == (
        "README.md\n"
        "src/\n"
        "  rlm_repo_intel/\n"
        "    pipeline/\n"
        "      rlm_session.py\n"
        "    tools/\n"
        "      repo_loader.py"
    )
