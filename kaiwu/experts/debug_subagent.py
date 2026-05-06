"""
Debug Subagent: 运行时调试信息采集。
论文基础：Debug2Fix (Microsoft, 2026) — 弱模型+调试器 > 强模型裸跑。

核心思路：verifier 失败后，用 sys.settrace 非侵入式捕获目标行的变量值，
或用 pytest --tb=long 获取完整异常堆栈，为 generator 重试提供真实运行时数据。

约束：
- 不引入新依赖（sys.settrace/subprocess 是标准库）
- 超时 30s，失败返回空字符串，不中断主流程
- 只在有 pytest 输出的失败场景触发（语法错误不触发）
"""

import json
import logging
import os
import re
import tempfile
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

DEBUG_STRATEGY_PROMPT = """根据以下测试失败信息，决定需要检查什么运行时数据。

失败信息：
{error_detail}

修改的文件：{modified_file}
修改的代码片段：
```
{modified_snippet}
```

回答以下问题（JSON格式，不要解释）：
1. 哪个文件的哪一行最可能是问题所在？
2. 需要检查哪些变量的值？（最多5个）
3. 是什么类型的错误？(exception/assertion/logic)

格式：{{"file": "path/to/file.py", "line": 42, "variables": ["var1", "var2"], "error_type": "exception"}}"""

TRACE_SCRIPT_TEMPLATE = '''import sys, json, os

captured = {{"variables": {{}}, "exception": None, "reached": False}}
target_file = {target_file!r}
target_line = {target_line}
variables = {variables!r}

def tracer(frame, event, arg):
    fname = frame.f_code.co_filename
    if not fname.endswith(target_file):
        return tracer
    if event == "line" and frame.f_lineno == target_line:
        captured["reached"] = True
        for var in variables:
            if var in frame.f_locals:
                try:
                    captured["variables"][var] = repr(frame.f_locals[var])[:200]
                except Exception:
                    captured["variables"][var] = "<repr failed>"
    if event == "exception":
        exc_type, exc_value, _ = arg
        if exc_type is not None:
            captured["exception"] = f"{{exc_type.__name__}}: {{exc_value}}"
    return tracer

sys.settrace(tracer)
os.chdir({project_root!r})
try:
    import pytest
    pytest.main([{test_path!r}, "-x", "-q", "--tb=no", "--no-header"])
except SystemExit:
    pass
except Exception as e:
    captured["exception"] = f"{{type(e).__name__}}: {{e}}"
sys.settrace(None)
print("__DEBUG_JSON__" + json.dumps(captured, ensure_ascii=False))
'''


class DebugSubagent:
    """
    运行时调试子代理。
    verifier 失败后调用 investigate()，返回结构化调试信息。
    """

    TIMEOUT = 30  # seconds

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor):
        self.llm = llm
        self.tools = tool_executor

    def investigate(self, ctx: TaskContext) -> str:
        """
        主入口。分析 verifier 失败，获取运行时信息。
        返回人类可读的调试结论字符串，失败返回空字符串。
        """
        try:
            # 前置条件：必须有 verifier 失败输出
            if not ctx.verifier_output:
                return ""
            error_detail = ctx.verifier_output.get("error_detail", "")
            if not error_detail or "Syntax error" in error_detail:
                return ""  # 语法错误不需要运行时调试

            # Step 1: LLM 决定调试策略
            strategy = self._plan_debug_strategy(ctx, error_detail)
            if not strategy:
                return ""

            # Step 2: 生成 trace 脚本并执行
            runtime_data = self._execute_trace(ctx, strategy)
            if not runtime_data:
                # Fallback: 用 pytest --tb=long 获取详细堆栈
                return self._fallback_detailed_traceback(ctx)

            # Step 3: 格式化结果
            return self._format_results(strategy, runtime_data)

        except Exception as e:
            logger.warning("[debug_subagent] investigate failed: %s", e)
            return ""

    def _plan_debug_strategy(self, ctx: TaskContext, error_detail: str) -> Optional[dict]:
        """用 LLM 决定要检查哪个文件、哪一行、哪些变量。"""
        patches = ctx.generator_output.get("patches", []) if ctx.generator_output else []
        modified_file = patches[0].get("file", "") if patches else ""
        modified_snippet = patches[0].get("modified", "")[:300] if patches else ""

        prompt = DEBUG_STRATEGY_PROMPT.format(
            error_detail=error_detail[:500],
            modified_file=modified_file,
            modified_snippet=modified_snippet,
        )

        try:
            response = self.llm.generate(prompt=prompt, max_tokens=200, temperature=0.1)
            # 提取 JSON
            json_match = re.search(r'\{[^}]+\}', response)
            if not json_match:
                return None
            try:
                strategy = json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError):
                return None
            # 验证必要字段
            if "file" not in strategy or "line" not in strategy:
                return None
            strategy.setdefault("variables", [])
            strategy.setdefault("error_type", "unknown")
            # 限制变量数量
            strategy["variables"] = strategy["variables"][:5]
            return strategy
        except Exception as e:
            logger.warning("[debug_subagent] strategy planning failed: %s", e)
            return None

    def _execute_trace(self, ctx: TaskContext, strategy: dict) -> Optional[dict]:
        """生成并执行 sys.settrace 脚本，捕获运行时变量。"""
        target_file = strategy["file"]
        target_line = int(strategy["line"])
        variables = strategy["variables"]

        # 找到测试文件
        test_path = self._find_test_file(ctx.project_root)
        if not test_path:
            return None

        script = TRACE_SCRIPT_TEMPLATE.format(
            target_file=target_file,
            target_line=target_line,
            variables=variables,
            project_root=ctx.project_root,
            test_path=test_path,
        )

        # 写入临时文件执行（避免命令行转义问题）
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(script)
                script_path = f.name

            result = self.tools.run_bash(
                f'python "{script_path}"',
                timeout=self.TIMEOUT,
            )

            # 清理临时文件
            try:
                os.unlink(script_path)
            except OSError:
                pass

            if not result or not result.get("stdout"):
                return None

            # 提取 JSON 结果
            stdout = result["stdout"]
            marker = "__DEBUG_JSON__"
            if marker in stdout:
                json_str = stdout.split(marker)[-1].strip()
                try:
                    return json.loads(json_str)
                except (json.JSONDecodeError, ValueError):
                    return None

            return None

        except Exception as e:
            logger.warning("[debug_subagent] trace execution failed: %s", e)
            return None

    def _fallback_detailed_traceback(self, ctx: TaskContext) -> str:
        """Fallback：用 pytest --tb=long 获取详细异常堆栈。"""
        test_path = self._find_test_file(ctx.project_root)
        if not test_path:
            return ""

        try:
            result = self.tools.run_bash(
                f'cd "{ctx.project_root}" && python -m pytest "{test_path}" -x --tb=long -q 2>&1 | tail -40',
                timeout=self.TIMEOUT,
            )
            if result and result.get("stdout"):
                output = result["stdout"][:800]
                return f"[详细堆栈]\n{output}"
        except Exception as e:
            logger.warning("[debug_subagent] fallback traceback failed: %s", e)
        return ""

    @staticmethod
    def _find_test_file(project_root: str) -> Optional[str]:
        """找到项目的测试文件路径。"""
        candidates = [
            "tests/",
            "test/",
            ".",
        ]
        for candidate in candidates:
            test_dir = os.path.join(project_root, candidate)
            if os.path.isdir(test_dir):
                for fname in os.listdir(test_dir):
                    if fname.startswith("test_") and fname.endswith(".py"):
                        return os.path.join(candidate, fname)
                    if fname.endswith("_test.py"):
                        return os.path.join(candidate, fname)
        return None

    @staticmethod
    def _format_results(strategy: dict, runtime_data: dict) -> str:
        """把运行时数据格式化为人类可读的调试结论。"""
        parts = []

        if runtime_data.get("exception"):
            parts.append(f"异常: {runtime_data['exception']}")

        if not runtime_data.get("reached"):
            parts.append(f"注意: 执行未到达 {strategy['file']}:{strategy['line']}")
        else:
            parts.append(f"断点命中: {strategy['file']}:{strategy['line']}")

        variables = runtime_data.get("variables", {})
        if variables:
            var_lines = [f"  {k} = {v}" for k, v in variables.items()]
            parts.append("变量值:\n" + "\n".join(var_lines))

        return "\n".join(parts) if parts else ""
