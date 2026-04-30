import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")
pytest.importorskip("tree_sitter_go")

from kaiwu.ast_engine import graph_builder as graph_builder_module
from kaiwu.ast_engine import graph_retriever as graph_retriever_module
from kaiwu.ast_engine.graph_builder import GraphBuilder
from kaiwu.ast_engine.graph_retriever import GraphRetriever
from kaiwu.ast_engine.parser import TreeSitterParser


GO_SOURCE = b"""
package main

func Login(username string) bool {
    return validateToken(username)
}

func validateToken(token string) bool {
    return token != ""
}

type UserService struct{}

func (s *UserService) Handle(token string) bool {
    return s.checkToken(token)
}

func (s *UserService) checkToken(token string) bool {
    return token != ""
}
"""


def test_go_parser_extracts_functions_and_calls():
    parser = TreeSitterParser()

    tree = parser.parse_bytes(GO_SOURCE, "go")
    assert tree is not None

    functions = parser.extract_functions(tree, GO_SOURCE, "go")
    function_names = {func["name"] for func in functions}

    assert "Login" in function_names
    assert "validateToken" in function_names
    assert "UserService.Handle" in function_names
    assert "UserService.checkToken" in function_names

    login = next(func for func in functions if func["name"] == "Login")
    assert login["params"] == ["username"]

    calls = parser.extract_calls(tree, GO_SOURCE, "go")
    call_pairs = {(call["in_function"], call["name"]) for call in calls}

    assert ("Login", "validateToken") in call_pairs
    assert ("UserService.Handle", "checkToken") in call_pairs


def test_graph_builder_indexes_go_files(tmp_path, monkeypatch):
    db_path = tmp_path / ".kwcode" / "graph.db"
    monkeypatch.setattr(graph_builder_module, "DB_PATH", db_path)
    monkeypatch.setattr(graph_retriever_module, "DB_PATH", db_path)

    project = tmp_path / "go_project"
    project.mkdir()
    source_file = project / "user.go"
    source_file.write_bytes(GO_SOURCE)

    builder = GraphBuilder(str(project))
    result = builder.build_full()

    assert result["file_count"] == 1
    assert result["node_count"] == 4
    assert result["edge_count"] >= 2

    retriever = GraphRetriever(str(project))
    results = retriever.retrieve("login", max_results=5)

    assert any(item["file_path"] == "user.go" for item in results)
    assert any(item["name"] == "Login" for item in results)
