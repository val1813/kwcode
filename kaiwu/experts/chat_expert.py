"""
ChatExpert: 非编码输入直接LLM回复，不走流水线。
Gate解析失败或识别为chat类型时降级到此。

策略：
- 纯问候（你好/谢谢/再见等短句）→ 直接LLM回复
- 其他非编码问题 → 先搜索再回复（LLM不知道实时信息，搜一下总比瞎编好）
"""

import logging

from kaiwu.core.context import TaskContext

logger = logging.getLogger(__name__)

CHAT_SYSTEM = (
    "你是KWCode，一个本地模型coding agent。"
    "用户问非编码问题时≤100字回复，≤3句话。"
    "自然引导到代码任务。"
    "如果用户问运维操作（SSH/docker/nginx等），"
    "给出具体命令，提示用 /bash 执行。"
)

CHAT_SEARCH_FAIL_SYSTEM = (
    "你是KWCode。搜索失败，无实时数据。"
    "回复≤50字：'抱歉，无法获取XX信息，请检查网络后重试。'"
    "禁止编造数据，禁止列URL。"
)

CHAT_SEARCH_SYSTEM = (
    "你是KWCode，一个本地模型coding agent。"
    "用户问了一个问题，以下是搜索结果，请严格基于搜索结果中的数据回答。"
    "要求：1）只使用搜索结果中明确提到的数据（数字、日期、事实）；"
    "2）不要编造任何搜索结果中没有的信息；"
    "3）不要列出网站URL让用户自己去查；"
    "4）如果搜索结果不包含用户需要的具体数据，直接说'搜索结果中未找到相关数据'。"
)

# 纯问候，不需要搜索
_GREETING_WORDS = {"你好", "hello", "hi", "hey", "谢谢", "thanks", "再见", "bye", "嗨"}


class ChatExpert:
    """非编码输入：短问候直接回复，其他问题先搜索再回复。"""

    def __init__(self, llm, search_augmentor=None):
        self.llm = llm
        self.search = search_augmentor

    def run(self, ctx: TaskContext) -> dict:
        user_input = ctx.user_input.strip()

        # 纯问候 → 直接回复，不搜索
        if user_input.lower().rstrip("!！。.?？") in _GREETING_WORDS:
            return self._run_chat(ctx)

        # 判断是否需要搜索
        if self.search and self._needs_search(ctx):
            return self._run_with_search(ctx)

        return self._run_chat(ctx)

    def _needs_search(self, ctx: TaskContext) -> bool:
        """
        判断是否需要搜索。
        Follow-up追问、纯推理/建议类问题不搜索，让模型基于已有上下文回答。
        但包含实时数据关键词时始终搜索。
        """
        user_input = ctx.user_input.strip()

        # 实时数据关键词 → 始终搜索（优先级最高）
        _REALTIME_KEYWORDS = [
            "今天", "今日", "明天", "本周", "这周", "现在",
            "最新", "最近", "天气", "温度", "价格", "股价",
        ]
        if any(kw in user_input for kw in _REALTIME_KEYWORDS):
            return True

        # Follow-up 检测：短句 + 追问/指示词 → 不搜
        _FOLLOWUP_PATTERNS = [
            "穿什么", "怎么去", "那个呢", "还有呢", "然后呢",
            "具体说", "详细", "举个例", "比如", "展开",
            "为什么", "什么意思", "怎么理解",
        ]
        if len(user_input) < 20 and any(p in user_input for p in _FOLLOWUP_PATTERNS):
            logger.debug("[chat] follow-up detected, skip search")
            return False

        # 纯推理/建议类问题 → 不搜
        _REASONING_PATTERNS = [
            "建议", "合适", "应该", "怎么选", "哪个好",
            "优缺点", "对比", "区别", "适合", "推荐",
            "注意什么", "需要注意", "有什么技巧",
        ]
        if any(p in user_input for p in _REASONING_PATTERNS):
            logger.debug("[chat] reasoning question, skip search")
            return False

        # 其他情况 → 搜索
        return True

    def _build_system(self, ctx: TaskContext, base_system: str) -> str:
        """Combine expert_system_prompt with base system prompt."""
        expert_prompt = ctx.expert_system_prompt or ""
        if expert_prompt and base_system:
            return f"{expert_prompt}\n\n{base_system}"
        return expert_prompt or base_system

    def _run_chat(self, ctx: TaskContext) -> dict:
        try:
            reply = self.llm.generate(
                prompt=ctx.user_input,
                system=self._build_system(ctx, CHAT_SYSTEM),
                max_tokens=200,
                temperature=0.7,
            )
            ctx.generator_output = {"explanation": reply.strip(), "patches": []}
            return {"passed": True, "output": reply.strip()}
        except Exception as e:
            logger.warning("ChatExpert LLM call failed: %s", e)
            fallback = "你好！我是KWCode，专注于代码任务。有什么代码问题需要帮忙吗？"
            ctx.generator_output = {"explanation": fallback, "patches": []}
            return {"passed": True, "output": fallback}

    def _run_with_search(self, ctx: TaskContext) -> dict:
        """先搜索，再用LLM基于搜索结果回答。"""
        try:
            logger.info("[chat] 搜索中: %s", ctx.user_input[:60])
            search_result = self.search.search_only(ctx.user_input)
            if search_result and len(search_result) > 30:
                prompt = f"用户问题：{ctx.user_input}\n\n搜索结果：\n{search_result}"
                reply = self.llm.generate(
                    prompt=prompt,
                    system=self._build_system(ctx, CHAT_SEARCH_SYSTEM),
                    max_tokens=500,
                    temperature=0.3,
                )
                ctx.generator_output = {"explanation": reply.strip(), "patches": []}
                return {"passed": True, "output": reply.strip()}
        except Exception as e:
            logger.warning("ChatExpert search failed: %s", e)

        # 搜索失败或结果太短 → 用专门的降级prompt，不让模型瞎编
        return self._run_search_fail(ctx)

    def _run_search_fail(self, ctx: TaskContext) -> dict:
        """搜索不可用时的降级回复：诚实告知，不编造信息。"""
        try:
            reply = self.llm.generate(
                prompt=ctx.user_input,
                system=self._build_system(ctx, CHAT_SEARCH_FAIL_SYSTEM),
                max_tokens=300,
                temperature=0.3,
            )
            ctx.generator_output = {"explanation": reply.strip(), "patches": []}
            return {"passed": True, "output": reply.strip()}
        except Exception as e:
            logger.warning("ChatExpert search_fail LLM call failed: %s", e)
            fallback = "搜索服务暂时不可用，请启动Docker Desktop让SearXNG恢复。你也可以直接问我代码相关的问题。"
            ctx.generator_output = {"explanation": fallback, "patches": []}
            return {"passed": True, "output": fallback}
