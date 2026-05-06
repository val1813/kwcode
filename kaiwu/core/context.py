"""
TaskContext: shared data structure passed through the expert pipeline.
Each expert reads from and writes to specific fields only.
"""

from dataclasses import dataclass, field
from typing import Optional

__all__ = ["TaskContext"]


@dataclass
class TaskContext:
    """Immutable-ish context flowing through the pipeline. Each expert owns its output field."""

    # 输入（流水线启动时设置一次）
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

    # 专家系统提示词（通过注册表路由时注入）
    expert_system_prompt: str = ""

    # 重试/搜索状态
    retry_count: int = 0
    retry_strategy: int = 0       # 0=正常/1=从错误出发/2=最小化修改
    previous_failure: str = ""    # 上次失败的error_detail
    reflection: str = ""          # LLM对失败原因的一句话分析
    search_triggered: bool = False
    search_results: str = ""

    # 收集的文件内容（Locator填充，Generator使用）
    relevant_code_snippets: dict = field(default_factory=dict)
    # shape: {"path/to/file.py": "code content around target function"}

    # 文档上下文（DocReader通过Locator填充）
    doc_context: str = ""

    # Debug子代理输出（orchestrator重试时填充）
    debug_info: str = ""

    # KWCODE.md注入规则（orchestrator填充）
    kwcode_rules: str = ""

    # 图片上下文（CLI/orchestrator填充）
    image_paths: list[str] = field(default_factory=list)
    image_path: str = ""

    # ── 多任务编排（TaskPlanner/TaskCompiler使用）──
    # 子任务执行结果，供下游子任务读取
    # shape: {"t1": {"success": bool, "files_modified": [...], "explanation": str, "patches": [...], "search_data": str}}
    subtask_results: dict = field(default_factory=dict)

    # 当前子任务ID
    current_task_id: str = ""

    # 上游依赖结果（结构化，供Gate/Locator/Generator消费）
    upstream_summary: dict = field(default_factory=dict)
    # shape: {"modified_files": [...], "diffs": {...}, "new_symbols": [...], "broken_interfaces": [...]}

    # 经验回放：历史相似成功轨迹
    similar_trajectories: list = field(default_factory=list)

    # SearchSubagent跨文件契约，注入Generator prompt
    upstream_constraints: str = ""

    # 重试提示：按错误类型生成的指导，注入Generator prompt
    retry_hint: str = ""

    # AdaptThink: think模式配置（orchestrator根据expert_type×difficulty设置）
    think_config: dict = field(default_factory=lambda: {"think": False, "budget": 0})

    # 模型能力等级（orchestrator检测后写入，Generator按此调整约束）
    model_tier: str = ""  # "small"/"medium"/"large"

    # 实际可用ctx大小（orchestrator检测后写入）
    effective_ctx: int = 32768
