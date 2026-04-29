"""
PCED-Lite：Parallel Context-of-Experts Decoding 的黑盒近似版。
论文基础：arXiv:2601.08670 (PCED, 2026)

原版PCED需要访问模型内部logits（transformers库）。
PCED-Lite适配Ollama黑盒API：
  1. 对每个搜索结果独立生成答案（并行）
  2. 用一致性投票选最终答案
  3. FLEX-2：VRAM不足或候选少于3时静默降级
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from kaiwu.llm.llama_backend import LLMBackend

logger = logging.getLogger(__name__)

PCED_SYSTEM = """你是信息提取专家。根据以下参考资料回答问题。
只使用提供的资料，不要编造信息。如果资料不足以回答，直接说"资料不足"。
回答简洁，不超过100字。"""

PCED_VOTER_SYSTEM = """你是信息综合专家。以下是多个来源对同一问题的回答。
选出信息最可靠、最具体的回答，或综合多个一致的答案。
直接输出最终答案，不要解释选择过程。"""

MAX_PARALLEL = 3
TIMEOUT_PER_DOC = 8
VOTE_TIMEOUT = 5


def pced_lite_aggregate(
    query: str,
    documents: list[dict],
    llm: LLMBackend,
    vram_gb: float = 0,
) -> Optional[str]:
    """
    PCED-Lite主入口。
    返回聚合后的答案字符串，降级时返回None（调用方回退到拼接模式）。
    """
    # FLEX-2：VRAM不足或候选太少时降级
    if vram_gb > 0 and vram_gb < 6:
        logger.info("[pced_lite] VRAM %.1fGB < 6GB，降级（FLEX-2）", vram_gb)
        return None

    valid_docs = [d for d in documents if d.get("content") or d.get("snippet")]
    if len(valid_docs) < 3:
        logger.info("[pced_lite] 有效文档 %d < 3，降级", len(valid_docs))
        return None

    docs_to_use = valid_docs[:MAX_PARALLEL]

    # Step 1：并行对每个文档独立推理
    candidate_answers = _parallel_inference(query, docs_to_use, llm)

    if not candidate_answers:
        return None

    if len(candidate_answers) == 1:
        return candidate_answers[0]

    # Step 2：投票选最一致的答案
    final_answer = _vote(query, candidate_answers, llm)
    return final_answer


def _parallel_inference(
    query: str,
    documents: list[dict],
    llm: LLMBackend,
) -> list[str]:
    """对每个文档独立推理，返回候选答案列表。"""

    def _infer_one(doc: dict) -> Optional[str]:
        content = doc.get("content") or doc.get("snippet", "")
        if not content:
            return None

        prompt = f"""参考资料：
{content[:1500]}

问题：{query}"""

        try:
            answer = llm.generate(
                prompt=prompt,
                system=PCED_SYSTEM,
                max_tokens=150,
                temperature=0.0,
            )
            if answer and "资料不足" not in answer:
                return answer.strip()
        except Exception as e:
            logger.debug("[pced_lite] 单文档推理失败: %s", e)
        return None

    answers = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = {executor.submit(_infer_one, doc): doc for doc in documents}
        for future in as_completed(futures, timeout=TIMEOUT_PER_DOC + 2):
            try:
                result = future.result()
                if result:
                    answers.append(result)
            except Exception as e:
                logger.debug("[pced_lite] future失败: %s", e)

    logger.info("[pced_lite] 并行推理完成: %d/%d 个候选", len(answers), len(documents))
    return answers


def _vote(query: str, candidates: list[str], llm: LLMBackend) -> str:
    """投票选最一致的答案。"""
    # 如果答案都很相似，直接用第一个
    if _all_similar(candidates):
        logger.info("[pced_lite] 候选答案高度一致，直接使用第一个")
        return candidates[0]

    # 否则让LLM综合
    candidates_text = "\n\n".join(
        f"来源{i+1}：{c}" for i, c in enumerate(candidates)
    )
    prompt = f"""问题：{query}

多个来源的回答：
{candidates_text}

请选择或综合最可靠的答案："""

    try:
        final = llm.generate(
            prompt=prompt,
            system=PCED_VOTER_SYSTEM,
            max_tokens=150,
            temperature=0.0,
        )
        return final.strip() if final else candidates[0]
    except Exception:
        return candidates[0]


def _all_similar(candidates: list[str], threshold: float = 0.5) -> bool:
    """简单判断候选答案是否高度一致（字符级重叠率）。"""
    if len(candidates) < 2:
        return True

    def char_set(text: str) -> set:
        """提取所有有意义的字符（中文单字+英文单词+数字）。"""
        chars = set()
        # 中文单字
        for c in text:
            if '\u4e00' <= c <= '\u9fff':
                chars.add(c)
        # 英文单词和数字
        for w in re.findall(r'[a-z]+|\d+', text.lower()):
            chars.add(w)
        return chars

    base = char_set(candidates[0])
    if not base:
        return False

    for c in candidates[1:]:
        other = char_set(c)
        if not other:
            continue
        overlap = len(base & other) / len(base | other)
        if overlap < threshold:
            return False
    return True
