"""
TaskPlanner: 自动任务分解。
1次LLM调用将复合任务拆分为DAG，失败降级为单任务。

理论来源：
- "Hidden Architectural Seam" (2026): 分离 Planner 和 Executor 提升 9-15%
- CodeDelegator (2025): Delegator(持久) + Coder(临时) 隔离 context
- Cognition/Devin: hierarchical delegation 有效

设计原则：
- Planner 只做1次LLM调用，输出结构化JSON
- 失败时降级为单任务（不死循环）
- 简单任务不拆分（Gate difficulty=easy 直接跳过）
"""

import json
import logging
import re
from typing import Optional

from kaiwu.llm.llama_backend import LLMBackend

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """分析以下任务，判断是否需要拆分为多个子任务。

任务：{user_input}

规则：
1. 如果任务只涉及一个操作（修一个bug、写一个函数、加注释），输出 single
2. 如果任务涉及多个有依赖的步骤（先搜索数据再生成页面、先重构再写测试），拆分为子任务
3. 如果任务涉及多个独立操作（给3个函数各加注释），拆分为并行子任务
4. 最多拆分5个子任务

输出JSON格式（不要解释）：
单任务：{{"type": "single"}}
多任务：{{"type": "multi", "tasks": [{{"id": "t1", "input": "子任务描述", "depends_on": []}}, {{"id": "t2", "input": "子任务描述", "depends_on": ["t1"]}}]}}"""


class TaskPlanner:
    """
    自动任务分解。1次LLM调用，输出DAG或single。
    失败降级为单任务，不死循环。
    """

    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def plan(self, user_input: str, difficulty: str = "easy") -> Optional[list[dict]]:
        """
        分析任务是否需要拆分。
        返回 task list（供 TaskCompiler 执行）或 None（单任务，走普通流程）。

        只在 difficulty=hard 时尝试拆分。easy 任务直接返回 None。
        """
        # Easy 任务不拆分（节省 LLM 调用）
        if difficulty != "hard":
            return None

        # 短任务不拆分（<30字大概率是单操作）
        if len(user_input) < 30:
            return None

        try:
            prompt = PLANNER_PROMPT.format(user_input=user_input[:300])
            response = self.llm.generate(
                prompt=prompt,
                system="你是任务分解专家，只输出JSON，不要解释。",
                max_tokens=300,
                temperature=0.0,
            )

            result = self._parse_response(response)
            if result is None:
                return None  # single task or parse failure

            # 验证 DAG 结构
            if not self._validate_tasks(result):
                logger.warning("[task_planner] Invalid task structure, fallback to single")
                return None

            logger.info("[task_planner] Decomposed into %d subtasks", len(result))
            return result

        except Exception as e:
            logger.warning("[task_planner] Planning failed, fallback to single: %s", e)
            return None

    @staticmethod
    def _parse_response(response: str) -> Optional[list[dict]]:
        """解析 LLM 输出的 JSON。返回 task list 或 None。"""
        # 提取 JSON
        json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
        if not json_match:
            return None

        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return None

        # 判断类型
        if data.get("type") == "single":
            return None

        if data.get("type") == "multi" and "tasks" in data:
            tasks = data["tasks"]
            if isinstance(tasks, list) and len(tasks) >= 2:
                return tasks

        return None

    @staticmethod
    def _validate_tasks(tasks: list[dict]) -> bool:
        """验证任务列表结构正确。"""
        if not tasks or len(tasks) > 5:
            return False

        ids = set()
        for task in tasks:
            if "id" not in task or "input" not in task:
                return False
            if not task["input"].strip():
                return False
            task.setdefault("depends_on", [])
            ids.add(task["id"])

        # 验证依赖引用有效
        for task in tasks:
            for dep in task.get("depends_on", []):
                if dep not in ids:
                    return False

        return True
