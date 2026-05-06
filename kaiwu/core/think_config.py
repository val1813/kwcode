"""
AdaptThink: 自适应think模式配置。

根据expert_type × difficulty自动决定think预算：
- 简单任务：关闭think，直接生成（省token、省时间）
- 中等任务：低think预算
- 困难任务：完整think预算
- 调试任务：最高think预算

基于AdaptThink论文：在GSM8K/MATH500/AIME2024上，
自适应选择Think/NoThink模式平均减少50%响应长度，同时提升准确率。
"""

import logging

logger = logging.getLogger(__name__)

# (expert_type, difficulty) → {"think": bool, "budget": int}
# budget是max_tokens的乘数，0表示关闭think
_THINK_TABLE = {
    # 简单任务：关闭think
    ("chat", "easy"):              {"think": False, "budget": 0},
    ("chat", "medium"):            {"think": False, "budget": 0},
    ("chat", "hard"):              {"think": False, "budget": 0},
    ("codegen", "easy"):           {"think": False, "budget": 0},
    ("locator_repair", "easy"):    {"think": False, "budget": 0},
    ("doc", "easy"):               {"think": False, "budget": 0},
    ("office", "easy"):            {"think": False, "budget": 0},

    # 中等任务：低think预算
    ("codegen", "medium"):         {"think": True, "budget": 512},
    ("locator_repair", "medium"):  {"think": True, "budget": 512},
    ("refactor", "medium"):        {"think": True, "budget": 1024},
    ("doc", "medium"):             {"think": False, "budget": 0},
    ("office", "medium"):          {"think": False, "budget": 0},

    # 困难任务：高think预算
    ("codegen", "hard"):           {"think": True, "budget": 2048},
    ("locator_repair", "hard"):    {"think": True, "budget": 2048},
    ("refactor", "hard"):          {"think": True, "budget": 4096},
    ("doc", "hard"):               {"think": True, "budget": 1024},
    ("office", "hard"):            {"think": True, "budget": 1024},
}

# 默认：关闭think（安全默认值，不浪费token）
_DEFAULT = {"think": False, "budget": 0}


def get_think_config(expert_type: str, difficulty: str) -> dict:
    """
    根据任务类型和难度返回think模式配置。

    Returns:
        {"think": bool, "budget": int}
        - think=False: 不使用reasoning tokens
        - think=True, budget=N: 允许最多N个reasoning tokens
    """
    return _THINK_TABLE.get((expert_type, difficulty), _DEFAULT)


def apply_think_to_max_tokens(max_tokens: int, think_config: dict, is_reasoning_model: bool) -> int:
    """
    根据think配置调整max_tokens。

    - 非reasoning模型：不变
    - reasoning模型 + think=False：保持原值（模型不会产生think tokens）
    - reasoning模型 + think=True：max_tokens + budget
    """
    if not is_reasoning_model:
        return max_tokens
    if not think_config.get("think"):
        return max_tokens
    return max_tokens + think_config.get("budget", 0)
