"""
Pipeline factory: shared pipeline construction for CLI and server.
Extracted from kaiwu/cli/main.py to avoid duplication.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def build_pipeline(
    model_path: Optional[str] = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "qwen3-8b",
    project_root: str = ".",
    verbose: bool = False,
    api_key: str = "",
):
    """
    Construct the full kwcode pipeline.
    Returns (gate, orchestrator, memory, registry).

    This is the shared core used by both CLI and server.
    """
    from kaiwu.llm.llama_backend import LLMBackend
    from kaiwu.core.gate import Gate
    from kaiwu.core.orchestrator import PipelineOrchestrator
    from kaiwu.experts.locator import LocatorExpert
    from kaiwu.experts.generator import GeneratorExpert
    from kaiwu.experts.verifier import VerifierExpert
    from kaiwu.experts.search_augmentor import SearchAugmentorExpert
    from kaiwu.experts.office_handler import OfficeHandlerExpert
    from kaiwu.tools.executor import ToolExecutor
    from kaiwu.memory.kaiwu_md import KaiwuMemory
    from kaiwu.registry import ExpertRegistry
    from kaiwu.flywheel.trajectory_collector import TrajectoryCollector
    from kaiwu.flywheel.ab_tester import ABTester
    from kaiwu.experts.chat_expert import ChatExpert
    from kaiwu.experts.vision_expert import VisionExpert
    from kaiwu.experts.debug_subagent import DebugSubagent

    llm = LLMBackend(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        verbose=verbose,
        api_key=api_key,
    )
    tools = ToolExecutor(project_root=project_root)
    memory = KaiwuMemory()

    registry = ExpertRegistry()
    registry.load_builtin()
    registry.load_user()

    gate = Gate(llm=llm, registry=registry)

    locator = LocatorExpert(llm=llm, tool_executor=tools)
    generator = GeneratorExpert(llm=llm, tool_executor=tools)
    verifier = VerifierExpert(llm=llm, tool_executor=tools)
    search = SearchAugmentorExpert(llm=llm)
    office = OfficeHandlerExpert(llm=llm, tool_executor=tools)
    chat_expert = ChatExpert(llm=llm, search_augmentor=search)
    vision_expert = VisionExpert(llm=llm, tool_executor=tools)
    debug_subagent = DebugSubagent(llm, tools)

    trajectory_collector = TrajectoryCollector()

    ab_tester = ABTester(
        registry=registry,
        collector=trajectory_collector,
        orchestrator=None,
    )

    orchestrator = PipelineOrchestrator(
        locator=locator, generator=generator, verifier=verifier,
        search_augmentor=search, office_handler=office,
        tool_executor=tools, memory=memory, registry=registry,
        trajectory_collector=trajectory_collector,
        ab_tester=ab_tester,
        chat_expert=chat_expert,
        debug_subagent=debug_subagent,
        vision_expert=vision_expert,
    )

    # Wire circular reference
    ab_tester.orchestrator = orchestrator

    return gate, orchestrator, memory, registry
