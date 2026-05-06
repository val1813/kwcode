"""
SKILL.md 自动提炼器。
从成功轨迹中自动生成 SKILL.md 草稿，用户审核后采纳。
不收集代码内容，只分析错误类型分布和修复策略统计。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_TRAJECTORIES_FOR_DRAFT = 30
DRAFT_PATH = ".kaiwu/skill_draft.md"


class SkillDrafter:
    """
    基于统计数据自动生成 SKILL.md 草稿。
    草稿生成后通过 CLI 提示用户审核，不自动写入正式 SKILL.md。
    """

    def __init__(self, strategy_stats, trajectory_collector):
        self._stats = strategy_stats
        self._collector = trajectory_collector

    def should_generate_draft(self) -> bool:
        """判断是否积累了足够数据生成草稿。"""
        try:
            successful = self._collector.load_recent(limit=500)
            success_count = sum(1 for t in successful if t.get("success"))
            return success_count >= MIN_TRAJECTORIES_FOR_DRAFT
        except Exception:
            return False

    def generate_draft(self, expert_type: str = "general") -> str | None:
        """
        为指定专家类型生成 SKILL.md 草稿。
        基于策略统计数据生成，不包含任何代码内容。
        """
        if not self.should_generate_draft():
            return None

        stats_summary = self._stats.get_summary()
        if not stats_summary:
            return None

        lines = [
            f"# {expert_type} 自动提炼草稿",
            f"> 基于 {MIN_TRAJECTORIES_FOR_DRAFT}+ 次成功任务自动生成",
            "> 请审核后决定是否采纳到正式 SKILL.md",
            "",
            "## 高效策略总结",
            "",
        ]

        for error_type, info in stats_summary.items():
            lines.append(f"### {error_type} 错误")
            lines.append(f"- 最优策略：{info['best_sequence']}")
            lines.append(f"- 成功率：{info['best_success_rate']}")
            lines.append(f"- 累计样本：{info['total_attempts']} 次")
            lines.append("")

        lines += [
            "## 建议",
            "",
            "以上数据来自你的实际使用统计。",
            "如果某个策略成功率持续偏低，考虑在 SKILL.md 里添加针对性指导。",
            "",
            "---",
            "运行 `kwcode skill accept` 将此草稿合并到正式 SKILL.md",
            "运行 `kwcode skill discard` 丢弃此草稿",
        ]

        return "\n".join(lines)

    def save_draft(self, content: str, project_root: str = "."):
        """保存草稿到项目目录。"""
        draft_path = Path(project_root) / DRAFT_PATH
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(content, encoding="utf-8")
        logger.info("SKILL.md 草稿已生成：%s", draft_path)

    def draft_exists(self, project_root: str = ".") -> bool:
        """检查是否有待审核的草稿。"""
        return (Path(project_root) / DRAFT_PATH).exists()
