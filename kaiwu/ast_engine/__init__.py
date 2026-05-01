"""
AST Engine: tree-sitter based call graph locator (spec S6).
Provides function-level code location via call graph analysis.
BM25+graph retrieval (spec LOC upgrade).
"""

try:
    from kaiwu.ast_engine.parser import TreeSitterParser
    from kaiwu.ast_engine.call_graph import CallGraph
    from kaiwu.ast_engine.locator import ASTLocator
    from kaiwu.ast_engine.graph_builder import GraphBuilder
    from kaiwu.ast_engine.graph_retriever import GraphRetriever
    AST_AVAILABLE = True
except ImportError:
    TreeSitterParser = None
    CallGraph = None
    ASTLocator = None
    GraphBuilder = None
    GraphRetriever = None
    AST_AVAILABLE = False

__all__ = [
    "TreeSitterParser", "CallGraph", "ASTLocator",
    "GraphBuilder", "GraphRetriever", "AST_AVAILABLE",
]
