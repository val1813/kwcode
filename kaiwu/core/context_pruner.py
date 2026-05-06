"""
Context Pruner：纯算法上下文压缩，不调用LLM。
UI-RED-2：耗时必须 <5ms。

策略：
  保留头部（system + 首轮）+ 保留尾部（最近8K tokens）
  中间部分：tool输出提取关键词，其余掩码
"""

import re
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 关键词提取正则（编译一次，重复使用）
_PATTERNS = [
    re.compile(r'(?:^|\s)((?:\w+/)+\w+\.\w+)'),           # 文件路径
    re.compile(r'\bdef\s+(\w+)\s*\('),                      # Python函数
    re.compile(r'\bfunction\s+(\w+)\s*[\(\{]'),             # JS函数
    re.compile(r'\bfunc\s+(\w+)\s*\('),                     # Go函数
    re.compile(r'\bclass\s+(\w+)[\s:\(]'),                  # 类名
    re.compile(r'(?:TODO|FIXME|BUG|HACK|NOTE):\s*(.{0,60})'),  # 注释标记
    re.compile(r'(?:Error|Exception|Traceback)[:\s]+(.{0,80})'), # 错误信息
    re.compile(r'(?:line\s+|L)(\d+)'),                      # 行号
    re.compile(r'^(?:import|from)\s+\S+', re.MULTILINE),    # import语句
]

# CTX-RED-1: 代码块检测正则
_CODE_BLOCK_RE = re.compile(r'```[\w]*\n(.*?)```', re.DOTALL)

_TAIL_TOKENS = 8192   # 尾部保留token数
_MASK_MIN = 200       # 短于此token数不掩码
_HEAD_TURNS = 1       # 保留头部的对话轮数（1=首轮问答）


def _count_tokens(text: str) -> int:
    """粗估token数，复用旧版逻辑。中文1.5字/token，英文4字符/token。"""
    cn = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    en = len(text) - cn
    return int(cn * 1.5 + en / 4)


def _extract_keywords(text: str) -> str:
    """从文本中提取关键词/路径/函数名，拼成一行摘要。"""
    hits = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            # 取第一个捕获组（如果有），否则取全匹配
            val = m.group(1) if m.lastindex else m.group(0)
            val = val.strip()
            if val and len(val) > 2:
                hits.append(val)

    if not hits:
        return ""

    # 去重保序，限制长度
    seen = set()
    unique = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            unique.append(h)
        if len(unique) >= 15:
            break

    return "[摘要] " + " · ".join(unique)


class ContextPruner:
    """
    对话历史压缩器。
    调用 prune(messages) 返回压缩后的消息列表。
    """

    def __init__(self, max_tokens: int = 8192, tail_tokens: int = _TAIL_TOKENS):
        self.max_tokens = max_tokens
        self.tail_tokens = min(tail_tokens, max_tokens * 3 // 4)
        self.compress_count = 0      # 累计压缩次数（显示在状态栏）
        self._last_compress_ms = 0.0 # 上次压缩耗时

    def estimate_total(self, messages: list[dict]) -> int:
        """估算消息列表的总token数。"""
        return sum(_count_tokens(m.get("content", "")) for m in messages)

    def needs_pruning(self, messages: list[dict]) -> bool:
        """是否需要压缩：超过max_tokens的85%时触发。"""
        return self.estimate_total(messages) > self.max_tokens * 0.85

    def prune(self, messages: list[dict]) -> list[dict]:
        """
        压缩消息列表。返回压缩后的副本，不修改原列表。

        压缩流程：
          1. 分离头部（system + 前_HEAD_TURNS轮）
          2. 分离尾部（最近tail_tokens tokens）
          3. 中间部分：tool输出提取关键词，assistant长输出截断+关键词
          4. 合并返回
        """
        t0 = time.perf_counter()

        if not messages:
            return messages

        # ── 分离头部 ──
        head = []
        rest = list(messages)

        # system消息
        if rest and rest[0].get("role") == "system":
            head.append(rest.pop(0))

        # 首轮对话（_HEAD_TURNS轮 = user+assistant各一条）
        turns_kept = 0
        while rest and turns_kept < _HEAD_TURNS:
            if rest[0].get("role") == "user":
                head.append(rest.pop(0))
                if rest and rest[0].get("role") == "assistant":
                    head.append(rest.pop(0))
                turns_kept += 1
            else:
                break

        # ── 分离尾部 ──
        tail = []
        tail_tokens_acc = 0
        temp = list(reversed(rest))
        tail_raw = []
        for msg in temp:
            t = _count_tokens(msg.get("content", ""))
            if tail_tokens_acc + t > self.tail_tokens:
                break
            tail_raw.append(msg)
            tail_tokens_acc += t
        tail = list(reversed(tail_raw))
        middle = rest[:len(rest) - len(tail)]

        # ── 压缩中间部分 ──
        compressed_middle = []
        for msg in middle:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tokens = _count_tokens(content)

            if tokens < _MASK_MIN:
                # 短内容不压缩
                compressed_middle.append(msg)
                continue

            # CTX-RED-1：代码块保护，不压缩代码内容
            if _has_code_block(content):
                code_only = _extract_code_blocks(content)
                if code_only:
                    compressed_middle.append({**msg, "content": code_only})
                else:
                    compressed_middle.append(msg)
                continue

            if role == "tool":
                # tool输出：提取关键词
                keywords = _extract_keywords(content)
                if keywords:
                    compressed_middle.append({**msg, "content": keywords})
                else:
                    compressed_middle.append({
                        **msg,
                        "content": f"[output masked, {tokens} tokens]"
                    })

            elif role == "assistant":
                # assistant输出：保留前200字 + 关键词
                preview = content[:200].rstrip()
                keywords = _extract_keywords(content)
                summary = preview
                if keywords:
                    summary += "\n" + keywords
                compressed_middle.append({**msg, "content": summary})

            else:
                # user消息：保留（用户输入通常较短）
                compressed_middle.append(msg)

        result = head + compressed_middle + tail

        # ── 统计 ──
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._last_compress_ms = elapsed_ms
        self.compress_count += 1

        orig_tokens = self.estimate_total(messages)
        new_tokens = self.estimate_total(result)
        ratio = (1 - new_tokens / max(orig_tokens, 1)) * 100

        logger.info(
            "[pruner] 压缩完成 %.0f%%，%d→%d tokens，耗时 %.2fms（第%d次）",
            ratio, orig_tokens, new_tokens, elapsed_ms, self.compress_count
        )

        # UI-RED-2检查（>5ms警告，不报错）
        if elapsed_ms > 5:
            logger.warning("[pruner] 耗时 %.2fms 超过5ms红线", elapsed_ms)

        return result


# ── CTX-RED-1: 代码块保护辅助函数 ──

def _has_code_block(text: str) -> bool:
    """检测文本是否包含代码块（```包裹）"""
    return bool(_CODE_BLOCK_RE.search(text))


def _extract_code_blocks(text: str) -> str:
    """
    提取所有代码块内容，去掉解释文字。
    CTX-RED-1：代码块内容不压缩，保持精确。
    """
    blocks = _CODE_BLOCK_RE.findall(text)
    if not blocks:
        return ""
    # 重新用```包裹，保留原始格式（最多保留3个代码块）
    result_parts = []
    for block in blocks[:3]:
        result_parts.append(f"```\n{block.rstrip()}\n```")
    return "\n\n".join(result_parts)


# ── GraduatedCompactor: 3层渐进压缩 ──
# 理论来源：CC 5层压缩管道（arXiv:2604.14228）；OPENDEV Adaptive Compaction（arXiv:2603.05344）

class GraduatedCompactor:
    """
    3 层渐进压缩，按 token 使用率分级触发。
    Layer 1 (70%)：裁剪 tool 输出冗余
    Layer 2 (85%)：压缩中间轮次 assistant 输出
    Layer 3 (95%)：摘要化早期对话，只保留关键决策
    """

    def __init__(self, max_tokens: int = 8192):
        self._pruner = ContextPruner(max_tokens=max_tokens)
        self.max_tokens = max_tokens

    def compress(self, messages: list[dict], usage_ratio: float = 0.0,
                 bus=None) -> list[dict]:
        """
        按 token 使用率分级压缩。
        Args:
            messages: 消息列表
            usage_ratio: 当前 token 使用率 (0.0~1.0)，0 表示自动计算
            bus: EventBus 实例（可选，用于发射压缩事件）
        """
        if not messages:
            return messages

        # 自动计算使用率
        if usage_ratio <= 0:
            total = sum(_count_tokens(m.get("content", "")) for m in messages)
            usage_ratio = total / max(self.max_tokens, 1)

        if usage_ratio < 0.70:
            return messages

        layer = self._layer(usage_ratio)
        if bus:
            bus.emit("pre_compact", {"ratio": usage_ratio, "layer": layer})

        if usage_ratio < 0.85:
            result = self._layer1_trim_tools(messages)
        elif usage_ratio < 0.95:
            result = self._layer2_compress_middle(messages)
        else:
            result = self._layer3_summarize_early(messages)

        if bus:
            orig = sum(_count_tokens(m.get("content", "")) for m in messages)
            new = sum(_count_tokens(m.get("content", "")) for m in result)
            bus.emit("post_compact", {"saved_tokens": orig - new, "layer": layer})

        return result

    def _layer(self, ratio: float) -> int:
        return 1 if ratio < 0.85 else (2 if ratio < 0.95 else 3)

    def _layer1_trim_tools(self, messages: list[dict]) -> list[dict]:
        """Layer 1: 裁剪 tool 输出冗余（>500 token 的 tool 输出提取关键词）。"""
        result = []
        for msg in messages:
            if msg.get("role") == "tool" and _count_tokens(msg.get("content", "")) > 500:
                content = msg.get("content", "")
                # 保护代码块
                if _has_code_block(content):
                    code_only = _extract_code_blocks(content)
                    if code_only:
                        result.append({**msg, "content": code_only})
                        continue
                kw = _extract_keywords(content)
                if kw:
                    result.append({**msg, "content": kw})
                else:
                    tokens = _count_tokens(content)
                    result.append({**msg, "content": f"[tool output masked, {tokens} tokens]"})
            else:
                result.append(msg)
        return result

    def _layer2_compress_middle(self, messages: list[dict]) -> list[dict]:
        """Layer 2: 复用 ContextPruner 逻辑压缩中间轮次。"""
        return self._pruner.prune(messages)

    def _layer3_summarize_early(self, messages: list[dict]) -> list[dict]:
        """Layer 3: 摘要化早期对话，只保留关键决策。"""
        if len(messages) < 6:
            return self._layer2_compress_middle(messages)

        # 保留头部（system + 首轮）
        head = []
        rest = list(messages)
        if rest and rest[0].get("role") == "system":
            head.append(rest.pop(0))
        if rest and rest[0].get("role") == "user":
            head.append(rest.pop(0))
            if rest and rest[0].get("role") == "assistant":
                head.append(rest.pop(0))

        # 保留最近4条消息
        if len(rest) <= 4:
            return head + rest
        recent = rest[-4:]
        middle = rest[:-4]

        # 中间部分提取关键词摘要
        middle_keywords = []
        for m in middle:
            if m.get("role") in ("assistant", "tool"):
                kw = _extract_keywords(m.get("content", ""))
                if kw:
                    middle_keywords.append(kw.replace("[摘要] ", ""))
        summary_text = " | ".join(middle_keywords[:20]) if middle_keywords else "[早期对话已压缩]"
        summary = {"role": "system", "content": f"[早期对话摘要] {summary_text}"}

        return head + [summary] + recent
