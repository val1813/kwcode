"""
Generator expert: generates code patches based on Locator output.
RED-2: Deterministic pipeline, generates multiple candidates at fixed temperatures.
RED-3: Independent context window, only sees Locator output + relevant snippets.

Key design: original is read directly from file (never LLM-generated),
LLM only produces the modified version. This guarantees apply_patch exact match.
"""

import json
import logging
import re
from typing import Optional

from kaiwu.core.context import TaskContext
from kaiwu.llm.llama_backend import LLMBackend
from kaiwu.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

GENERATOR_BASE_SYSTEM = """## 行为准则
Anti-Overengineering:
- 只做任务要求的事。bug修复=修bug，不要顺手重构周围代码。
- 不要为不可能发生的场景添加错误处理。信任内部代码。
- 不要为一次性操作创建工具函数。三行相似代码 > 过早抽象。
- 不要给你没改动的代码加类型注解、docstring或注释。
- 例外：当任务明确要求重构/拆分/重组时，彻底执行。

Anti-Hallucination:
- 绝不猜测API端点、函数签名或配置键——先read_file读源码。
- 工具调用失败时仔细读错误信息，不要用相同参数重试。
- 不要编造不存在的npm/pip包或CLI参数——先验证。

Anti-Excessive-Verification:
- write_file成功后不要立即read_file验证——信任工具。
- 测试通过一次就够了，不要"再确认一下"。

Output Format:
- 数据文件(csv/json/yaml/toml)必须用ASCII标点：冒号:不用：，逗号,不用，
- 代码文件禁止中文标点，否则SyntaxError。
- 输出被截断时（"缺少必填参数"错误），拆成更小的片段。
"""

WEB_DESIGN_RULES = """\
## 网页设计规范（生成HTML/CSS时必须遵守）

### 设计思考（写代码前必做）
锁定一个大胆的视觉方向并贯彻到底。不要折中妥协。
交付的代码必须：生产级可用、视觉震撼、风格统一。

### 字体
- 用Google Fonts，选有个性的字体，不要通用字体
- 禁止：Arial、Roboto、system-ui、sans-serif作为主字体
- 标题用展示字体，正文用精致的阅读字体
- 推荐组合：Playfair Display+Lato（奢华）、Space Mono+Inter（科技）、Cormorant Garamond+Source Sans Pro（杂志）

### 配色
- 锁定一套有主见的配色，用CSS变量保持一致
- 主色压倒性占比，1-2个配色，1个强调色
- 禁止：白底紫色渐变、千篇一律的蓝白配色
- 深色背景往往比浅色更有视觉冲击力

### 布局
- 打破预期：不对称、叠加、对角线流向
- 不要每个卡片都一样大，不要每行都一样高
- 慷慨的留白 OR 精心控制的密度——二选一

### 背景与氛围
不要默认纯色背景，用渐变网格、噪点纹理、几何图案、透明度叠加、多层阴影营造深度。

### 动效
- 优先纯CSS动画，不引入额外JS库
- 页面加载入场动效（animation-delay错开）
- 所有交互元素加 hover 状态 + transition 200-300ms

### 技术规范
- 用Tailwind CDN：<script src="https://cdn.tailwindcss.com"></script>
- Google Fonts CDN引入
- 响应式：grid-cols-1 md:grid-cols-2 lg:grid-cols-3
- 毛玻璃：backdrop-blur-md bg-white/10 border border-white/20
- 渐变文字：bg-gradient-to-r bg-clip-text text-transparent
- 导航栏：fixed top-0 z-50 backdrop-blur-md

### 禁止
- 白底黑字无样式的默认页面
- 每个卡片一模一样的布局
- 缺少视觉重心（所有元素同等权重）
- 不同任务生成同样的审美风格

### 自检清单
- 字体有个性，不是Arial/Roboto
- 配色有主见，主色压倒性占比
- 背景有氛围（渐变/纹理），不是平铺纯色
- 有入场动效（至少fadeIn）
- 所有交互元素有hover+transition
- 移动端响应式，不溢出
"""

# 网页任务检测关键词
_WEB_KEYWORDS = {"html", "css", "web", "网页", "页面", "前端", "界面", "landing",
                 "website", "网站", "落地页", "登录页", "注册页", "dashboard", "tailwind"}

GENERATOR_PROMPT = """你是代码修复/生成专家。根据任务描述，修改下面的函数代码。
你可以使用以下工具：read_file（读取文件）、write_file（写入文件）、run_bash（执行任意shell命令，包括ssh、git、pip等）。你拥有完整的文件系统和命令行访问权限。

任务描述：{task_description}

需要修改的原始代码（来自 {file_path}）：
```
{original_code}
```

{search_context}

请只输出修改后的完整函数代码。要求：
1. 保持原始缩进风格
2. 只修改必要的部分
3. 输出完整的函数（从def开始到函数结束）
4. 不要用markdown代码块包裹
5. 不要解释，只输出代码"""

GENERATOR_NEWFILE_PROMPT = """你是代码生成专家。根据任务描述生成文件内容。

任务描述：{task_description}
目标文件：{target_file}

相关代码上下文：
{code_snippets}

{search_context}

要求：
1. 直接输出文件的完整内容，不要输出任何命令
2. 不要输出 write_file、cd、mkdir、cat 等shell命令
3. 不要用markdown代码块包裹
4. 不要解释，只输出文件内容本身
5. 如果上面有"参考资料"，必须严格使用参考资料中的真实数据，禁止编造
6. 如果没有参考资料或参考资料为空，涉及实时数据（天气、股价、新闻等）时使用占位符如"[数据加载中]"，绝对不要编造虚假数据"""

GENERATOR_TEST_PROMPT = """你是测试生成专家。为下面的代码生成 pytest 单元测试。
你可以使用以下工具：read_file（读取文件）、write_file（写入文件）、run_bash（执行任意shell命令）。你拥有完整的文件系统和命令行访问权限。

源代码（来自 {source_file}）：
```
{source_code}
```

任务描述：{task_description}

{search_context}

请生成完整的 pytest 测试文件。要求：
1. 在文件开头 import 被测模块（使用相对路径或 sys.path）
2. 每个函数至少2个测试用例（正常+边界）
3. 使用 assert 语句
4. 只输出代码，不要解释
5. 不要用markdown代码块包裹"""


_LANG_KEYWORDS = {
    ".html": ["html", "网页", "页面", "web page", "webpage", "website", "前端页面"],
    ".js":   ["javascript", "js", "node", "nodejs", "react", "vue"],
    ".ts":   ["typescript", "ts", "angular"],
    ".css":  ["css", "样式", "stylesheet"],
    ".java": ["java", "spring", "springboot"],
    ".go":   ["golang", "go语言"],
    ".rs":   ["rust"],
    ".c":    ["c语言", "c程序"],
    ".cpp":  ["c++", "cpp"],
    ".sh":   ["shell", "bash", "脚本"],
    ".sql":  ["sql", "数据库查询"],
    ".json": ["json"],
    ".yaml": ["yaml", "yml"],
}


def _detect_extension(user_input: str) -> str:
    """从用户输入推断目标文件扩展名。默认.py。"""
    lower = user_input.lower()
    for ext, keywords in _LANG_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return ext
    return ".py"


class GeneratorExpert:
    """Generates code patches. Original is read from file, LLM only generates modified."""

    def __init__(self, llm: LLMBackend, tool_executor: ToolExecutor = None, num_candidates: int = 3):
        self.llm = llm
        self.tools = tool_executor
        self.num_candidates = num_candidates
        self.temperatures = [0.0, 0.3, 0.6][:num_candidates]

    def run(self, ctx: TaskContext) -> Optional[dict]:
        """
        Generate patches. For each target function:
        1. Read original code directly from file (100% accurate)
        2. Ask LLM to generate only the modified version
        3. Package as {file, original, modified} patch
        """
        locator = ctx.locator_output or {}
        files = locator.get("relevant_files", [])
        funcs = locator.get("relevant_functions", [])

        if not files:
            # No locator output — pure codegen task
            return self._run_codegen(ctx)

        # Detect test generation tasks — need to CREATE test file, not modify source
        if self._is_test_generation_task(ctx):
            return self._run_test_generation(ctx, files)

        # For each file+function pair, extract original and generate modified
        # Deduplicate: only patch each (file, function) once
        patches = []
        explanation_parts = []
        seen = set()  # (file, func) pairs already processed

        for fpath in files[:3]:  # Cap at 3 files
            # Skip test files — we only modify source code
            if "test" in fpath.lower():
                continue

            # Read the actual file content
            if self.tools:
                content = self.tools.read_file(fpath)
            else:
                content = ctx.relevant_code_snippets.get(fpath, "")
            if not content or content.startswith("[ERROR]"):
                continue

            # Find target functions in this file (deduplicated)
            file_funcs = list(dict.fromkeys(
                f for f in funcs if self._func_in_file(f, content)
            ))
            if not file_funcs:
                snippet = ctx.relevant_code_snippets.get(fpath, "")
                if snippet:
                    file_funcs = ["_whole_snippet_"]

            for func_name in file_funcs[:2]:  # Cap at 2 functions per file
                key = (fpath, func_name)
                if key in seen:
                    continue
                seen.add(key)
                if func_name == "_whole_snippet_":
                    original = ctx.relevant_code_snippets.get(fpath, content[:2000])
                else:
                    # Extract the exact function text from file
                    original = self._extract_function(content, func_name)
                    if not original:
                        logger.warning("Could not extract function %s from %s", func_name, fpath)
                        continue

                # Ask LLM to generate only the modified version
                modified = self._generate_modified(
                    ctx, fpath, original, ctx.user_input
                )
                if not modified:
                    continue

                # Verify original exists in file (should always be true since we read it)
                if original not in content:
                    logger.error("Extracted original not found in file — this should not happen")
                    continue

                patches.append({
                    "file": fpath,
                    "original": original,
                    "modified": modified,
                })
                explanation_parts.append(f"{fpath}:{func_name}")

        if not patches:
            logger.warning("Generator: no patches produced")
            return None

        result = {
            "patches": patches,
            "explanation": f"Modified: {', '.join(explanation_parts)}",
        }
        ctx.generator_output = result
        return result

    def _build_system(self, ctx: TaskContext, base_system: str = "") -> str:
        """Combine expert_system_prompt (from registry) with base system prompt.
        Appends WEB_DESIGN_RULES when the task involves web/HTML generation."""
        expert_prompt = ctx.expert_system_prompt or ""
        base = base_system or GENERATOR_BASE_SYSTEM
        if expert_prompt:
            system = f"{expert_prompt}\n\n{base}"
        else:
            system = base
        # Append web design rules for HTML/CSS/web tasks
        if self._is_web_task(ctx.user_input):
            system = f"{system}\n\n{WEB_DESIGN_RULES}"
        return system

    @staticmethod
    def _is_web_task(user_input: str) -> bool:
        """Detect if the task involves web/HTML/CSS generation."""
        lower = user_input.lower()
        return any(kw in lower for kw in _WEB_KEYWORDS)

    def _generate_modified(self, ctx: TaskContext, fpath: str, original: str, task_desc: str) -> Optional[str]:
        """Ask LLM to generate modified code. Uses retry_strategy to vary prompt."""
        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        # Build prompt based on retry_strategy
        prompt = self._build_retry_prompt(ctx, fpath, original, task_desc, search_ctx)

        # Inject upstream constraints from SearchSubagent (cross-file contracts)
        upstream_constraints = ctx.upstream_constraints
        if upstream_constraints:
            prompt += f"\n\n## 跨文件契约（必须遵守）\n{upstream_constraints}"

        # Append doc_context if available (keep concise)
        if ctx.doc_context:
            prompt += f"\n\n## 相关文档参考\n{ctx.doc_context[:800]}"

        # Inject retry_hint if available
        if ctx.retry_hint:
            prompt += f"\n\n## 重试提示\n{ctx.retry_hint}"

        system = self._build_system(ctx)

        for temp in self.temperatures:
            raw = self.llm.generate(prompt=prompt, system=system, max_tokens=2048, temperature=temp)
            modified = self._clean_code_output(raw)
            if modified and modified != original:
                return modified

        logger.warning("Generator: all candidates identical to original or empty")
        return None

    def _build_retry_prompt(self, ctx: TaskContext, fpath: str, original: str,
                            task_desc: str, search_ctx: str) -> str:
        """Build prompt based on retry_strategy: 0=normal, 1=error-first, 2=minimal."""
        strategy = ctx.retry_strategy
        search_line = f"{search_ctx}\n" if search_ctx else ""

        if strategy == 0:
            prompt = GENERATOR_PROMPT.format(
                task_description=task_desc,
                file_path=fpath,
                original_code=original,
                search_context=search_ctx,
            )
            # Collapse triple+ newlines when search_context is empty
            while "\n\n\n" in prompt:
                prompt = prompt.replace("\n\n\n", "\n\n")
            return prompt

        elif strategy == 1:
            error = ctx.previous_failure or "验证失败"
            reflection_line = f"\n失败分析：{ctx.reflection}" if ctx.reflection else ""
            debug_line = f"\n运行时调试信息：{ctx.debug_info}" if ctx.debug_info else ""
            return (
                f"上次修改失败了。错误信息：\n{error[:500]}{reflection_line}{debug_line}\n\n"
                f"原始代码（来自 {fpath}）：\n```\n{original}\n```\n\n"
                f"{search_line}"
                f"直接修复这个错误。只输出修改后的完整函数代码，不要解释。"
            )

        else:
            error = ctx.previous_failure or "验证失败"
            reflection_line = f"\n上次失败原因：{ctx.reflection}" if ctx.reflection else ""
            debug_line = f"\n运行时调试信息：{ctx.debug_info}" if ctx.debug_info else ""
            return (
                f"只修改以下代码的最小必要部分，其他代码一行都不要动。{reflection_line}{debug_line}\n\n"
                f"需要修复的错误：{error[:300]}\n\n"
                f"原始代码（来自 {fpath}）：\n```\n{original}\n```\n\n"
                f"{search_line}"
                f"输出修改后的完整函数代码。只改必须改的行，其余保持原样。"
            )

    def _run_codegen(self, ctx: TaskContext) -> Optional[dict]:
        """Pure code generation (no existing file to patch). Writes to real project path."""
        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料（以下为真实搜索数据，必须使用）：\n{ctx.search_results}"
        elif self._needs_realtime_warning(ctx.user_input):
            search_ctx = "注意：未获取到实时数据。涉及天气、股价、新闻等实时信息时，请使用占位符（如[数据加载中]），不要编造虚假数据。"

        snippets_text = ""
        for fpath, snippet in ctx.relevant_code_snippets.items():
            snippets_text += f"\n--- {fpath} ---\n{snippet}\n"

        # Extract target filename BEFORE prompt so we can tell the model
        target_file = self._extract_filename(ctx.user_input)

        prompt = GENERATOR_NEWFILE_PROMPT.format(
            task_description=ctx.user_input,
            target_file=target_file,
            code_snippets=snippets_text[:3000] if snippets_text else "(无上下文)",
            search_context=search_ctx,
        )

        system = self._build_system(ctx)
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=2048, temperature=0.0)
        code = self._clean_code_output(raw)
        if not code:
            return None

        import os
        full_path = os.path.join(ctx.project_root, target_file)

        # 防止覆盖已有文件：如果文件已存在，加数字后缀
        if os.path.exists(full_path):
            base, ext = os.path.splitext(target_file)
            for i in range(1, 100):
                candidate = f"{base}_{i}{ext}"
                if not os.path.exists(os.path.join(ctx.project_root, candidate)):
                    target_file = candidate
                    full_path = os.path.join(ctx.project_root, candidate)
                    break

        result = {
            "patches": [{"file": target_file, "original": "", "modified": code}],
            "explanation": f"已生成：{full_path}",
        }
        ctx.generator_output = result
        return result

    def _is_test_generation_task(self, ctx: TaskContext) -> bool:
        """Detect if the task is about generating tests (not modifying source)."""
        keywords = ["生成测试", "写测试", "单元测试", "test", "pytest", "测试用例", "添加测试"]
        task_lower = ctx.user_input.lower()
        gate_type = ctx.gate_result.get("expert_type", "")
        expert_name = ctx.gate_result.get("expert_name", "")
        if expert_name == "TestGenExpert":
            return True
        if gate_type == "codegen" and any(kw in task_lower for kw in keywords):
            return True
        return any(kw in task_lower for kw in keywords[:3])  # Strong Chinese signals

    def _run_test_generation(self, ctx: TaskContext, source_files: list[str]) -> Optional[dict]:
        """Generate a new test file for the given source files."""
        import os

        search_ctx = ""
        if ctx.search_results:
            search_ctx = f"参考资料：\n{ctx.search_results}"

        # Read source files to provide as context
        source_code_parts = []
        primary_source = None
        for fpath in source_files[:3]:
            if "test" in fpath.lower():
                continue
            content = self.tools.read_file(fpath) if self.tools else ""
            if content and not content.startswith("[ERROR]"):
                source_code_parts.append(f"# {fpath}\n{content}")
                if primary_source is None:
                    primary_source = fpath

        if not source_code_parts:
            return self._run_codegen(ctx)

        source_code = "\n\n".join(source_code_parts)

        prompt = GENERATOR_TEST_PROMPT.format(
            source_file=primary_source or "source",
            source_code=source_code[:4000],
            task_description=ctx.user_input,
            search_context=search_ctx,
        )

        system = self._build_system(ctx)
        raw = self.llm.generate(prompt=prompt, system=system, max_tokens=2048, temperature=0.0)
        code = self._clean_code_output(raw)
        if not code:
            return None

        # Determine test file path
        test_dir = os.path.join(ctx.project_root, "tests")
        if primary_source:
            base = os.path.splitext(os.path.basename(primary_source))[0]
            test_file = os.path.join("tests", f"test_{base}.py")
        else:
            test_file = os.path.join("tests", "test_generated.py")

        # Ensure tests/ dir and __init__.py exist
        if self.tools:
            abs_test_dir = os.path.join(ctx.project_root, "tests")
            os.makedirs(abs_test_dir, exist_ok=True)
            init_path = os.path.join(abs_test_dir, "__init__.py")
            if not os.path.exists(init_path):
                with open(init_path, "w", encoding="utf-8") as f:
                    pass

        result = {
            "patches": [{"file": test_file, "original": "", "modified": code}],
            "explanation": f"Generated test file for {primary_source or 'source'}",
        }
        ctx.generator_output = result
        return result

    @staticmethod
    def _extract_function(content: str, func_name: str) -> Optional[str]:
        """Extract a complete function/method from file content by name."""
        lines = content.split("\n")
        start_idx = -1
        indent_level = -1

        # Handle "Class.method" names from AST — strip class prefix
        short_name = func_name.split(".")[-1] if "." in func_name else func_name

        for i, line in enumerate(lines):
            # Match def func_name or class func_name
            stripped = line.lstrip()
            if stripped.startswith(f"def {short_name}") or stripped.startswith(f"class {short_name}"):
                start_idx = i
                indent_level = len(line) - len(stripped)
                break

        if start_idx == -1:
            return None

        # Find the end of the function (next line at same or lower indent level)
        end_idx = start_idx + 1
        while end_idx < len(lines):
            line = lines[end_idx]
            if line.strip() == "":
                end_idx += 1
                continue
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent_level and line.strip():
                break
            end_idx += 1

        # Include trailing blank lines that are part of the function block
        while end_idx > start_idx + 1 and lines[end_idx - 1].strip() == "":
            end_idx -= 1

        return "\n".join(lines[start_idx:end_idx])

    @staticmethod
    def _extract_filename(user_input: str) -> str:
        """Extract target filename from user input. Falls back to output.py."""
        # 1. Explicit filename with extension mentioned in input
        # Longer extensions first to avoid partial matches (e.g. .h before .html)
        m = re.search(r'[\w\-]+\.(?:html|yaml|yml|json|toml|java|cpp|css|py|js|ts|go|rs|sh|c|h)\b', user_input)
        if m:
            return m.group(0)

        # 2. Detect target language/filetype from user input → pick correct extension
        ext = _detect_extension(user_input)

        # 3. Chinese/English patterns: "写个XX" / "create XX" → derive filename
        cn_patterns = [
            (r'写(?:个|一个)?(\w+)函数', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)接口', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)脚本', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)类', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)页面', lambda m: m.group(1)),
            (r'写(?:个|一个)?(\w+)组件', lambda m: m.group(1)),
            (r'创建(?:个|一个)?(\w+)文件', lambda m: m.group(1)),
            (r'生成(?:个|一个)?(\w+)代码', lambda m: m.group(1)),
        ]
        for pat, extractor in cn_patterns:
            m = re.search(pat, user_input)
            if m:
                name = extractor(m)
                if name.isascii() and name.isalnum():
                    return f"{name.lower()}{ext}"

        # 4. English patterns
        en_patterns = [
            r'(?:create|write|make|build|generate)\s+(?:a\s+)?(\w+)',
            r'(?:implement|code)\s+(?:a\s+)?(\w+)',
        ]
        for pat in en_patterns:
            m = re.search(pat, user_input, re.IGNORECASE)
            if m:
                name = m.group(1).lower()
                if name not in ('the', 'a', 'an', 'new', 'simple', 'basic', 'my', 'function', 'file', 'code', 'script', 'program'):
                    return f"{name}{ext}"

        return f"output{ext}"

    @staticmethod
    def _func_in_file(func_name: str, content: str) -> bool:
        """Check if a function/class definition exists in content."""
        # Handle "Class.method" names from AST — strip class prefix
        short_name = func_name.split(".")[-1] if "." in func_name else func_name
        return f"def {short_name}" in content or f"class {short_name}" in content

    @staticmethod
    def _clean_code_output(raw: str) -> str:
        """Strip markdown code blocks, thinking tags, tool-call lines from LLM output."""
        text = raw.strip()
        # Strip <think>...</think> blocks from reasoning models
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        # Remove markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        # Strip tool-call lines that small models sometimes emit
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            stripped = line.strip().lower()
            # Skip lines that look like tool calls, not file content
            if stripped.startswith(("write_file ", "read_file ", "run_bash ", "cd ", "mkdir ")):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned)

        return text.strip()

    @staticmethod
    def _needs_realtime_warning(user_input: str) -> bool:
        """检测用户输入是否涉及实时数据，用于在无搜索结果时添加防编造警告。"""
        keywords = [
            "天气", "气温", "温度", "weather", "forecast",
            "股价", "股票", "汇率", "价格", "price",
            "新闻", "最新", "最近", "今天", "今日", "本周",
            "news", "latest", "today", "recent",
        ]
        lower = user_input.lower()
        return any(kw in lower for kw in keywords)
