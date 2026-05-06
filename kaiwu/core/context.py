"""
TaskContext: shared data structure passed through the expert pipeline.
Each expert reads from and writes to specific fields only.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TaskContext:
    """Immutable-ish context flowing through the pipeline. Each expert owns its output field."""

    # Input (set once at pipeline start)
    user_input: str = ""
    project_root: str = "."
    gate_result: dict = field(default_factory=dict)
    kaiwu_memory: str = ""

    # Locator output (RED-3: independent context, only Locator writes here)
    locator_output: Optional[dict] = None
    # Expected shape: {"relevant_files": [...], "relevant_functions": [...], "edit_locations": [...]}

    # Generator output (RED-3: independent context, only Generator writes here)
    generator_output: Optional[dict] = None
    # Expected shape: {"patches": [{"file": ..., "original": ..., "modified": ...}], "explanation": ...}

    # Verifier output (RED-3: independent context, only Verifier writes here)
    verifier_output: Optional[dict] = None
    # Expected shape: {"passed": bool, "syntax_ok": bool, "tests_passed": int, "tests_total": int, "error_detail": ...}

    # Expert system prompt (injected when routed via expert registry)
    expert_system_prompt: str = ""

    # Retry / search state
    retry_count: int = 0
    retry_strategy: int = 0       # 0=正常/1=从错误出发/2=最小化修改
    previous_failure: str = ""    # 上次失败的error_detail
    reflection: str = ""          # LLM对失败原因的一句话分析
    search_triggered: bool = False
    search_results: str = ""

    # Collected file contents (populated by Locator for Generator use)
    relevant_code_snippets: dict = field(default_factory=dict)
    # shape: {"path/to/file.py": "code content around target function"}

    # Document context (populated by DocReader via Locator)
    doc_context: str = ""

    # Debug Subagent output (populated by orchestrator on retry)
    debug_info: str = ""

    # KWCODE.md injected rules (populated by orchestrator)
    kwcode_rules: str = ""

    # Vision/image context (populated by CLI/orchestrator)
    image_paths: list[str] = field(default_factory=list)
    image_path: str = ""

    # ── 多任务编排（TaskPlanner/TaskCompiler使用）──
    # 子任务执行结果，供下游子任务读取
    # shape: {"t1": {"success": bool, "files_modified": [...], "explanation": str, "patches": [...], "search_data": str}}
    subtask_results: dict = field(default_factory=dict)

    # 当前子任务ID
    current_task_id: str = ""

    # 上游依赖结果摘要（Active Context，≤2K tokens，供Gate/Generator看）
    upstream_summary: str = ""
