"""
Prompt Optimizer: analyzes successful trajectories and appends learned patterns
to the expert's YAML system_prompt field.
SE-RED-4: uses external API (Opus/Sonnet), offline execution.
"""

import logging
import os
from typing import Optional

import httpx
import yaml

from kaiwu.flywheel.trajectory_collector import TaskTrajectory
from kaiwu.registry.expert_registry import ExpertRegistry

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = '''你是KWCode专家系统的prompt优化器。

分析以下{task_count}个成功任务的执行轨迹，提取可复用的经验规则。

## 轨迹摘要
{trajectory_summary}

## 当前专家system_prompt
```
{current_prompt}
```

## 任务
基于轨迹分析，生成2-5条具体的经验规则，格式如下：
- 每条规则一行，以"- "开头
- 规则必须具体可操作（不要泛泛而谈）
- 规则应该帮助未来同类任务提高成功率
- 不要重复已有prompt中的内容

只输出规则列表，不要解释。'''


class PromptOptimizer:
    """
    Analyzes successful trajectories and appends learned patterns
    to expert YAML system_prompt.
    """

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model

    def optimize_expert(
        self,
        expert_name: str,
        trajectories: list[TaskTrajectory],
        registry: ExpertRegistry,
    ) -> bool:
        """
        Analyze trajectories, generate conclusions, append to expert's system_prompt.
        Supports both YAML and SKILL.md formats.
        Returns True if optimization was applied.
        """
        expert_def = registry.get(expert_name)
        if not expert_def:
            logger.warning("[prompt_optimizer] Expert not found: %s", expert_name)
            return False

        source_path = expert_def.get("_source")
        if not source_path or not os.path.isfile(source_path):
            logger.warning("[prompt_optimizer] No source file for: %s", expert_name)
            return False

        current_prompt = expert_def.get("instructions") or expert_def.get("system_prompt", "")
        summary = self._summarize_trajectories(trajectories)

        # Call API for analysis
        new_rules = self._call_api(len(trajectories), summary, current_prompt)
        if not new_rules:
            logger.info("[prompt_optimizer] API returned no rules")
            return False

        # Update based on format
        fmt = expert_def.get("_format", "yaml")
        if fmt == "skill":
            return self._update_skill_md(source_path, new_rules, expert_name, registry)
        else:
            updated_prompt = current_prompt.rstrip() + "\n\n## 经验规则（自动生成）\n" + new_rules
            return self._update_yaml(source_path, updated_prompt, expert_name, registry)

    def _call_api(self, task_count: int, summary: str, current_prompt: str) -> Optional[str]:
        """Call Opus/Sonnet API to generate optimization rules."""
        prompt = ANALYSIS_PROMPT.format(
            task_count=task_count,
            trajectory_summary=summary,
            current_prompt=current_prompt[:2000],
        )

        try:
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1000,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"].strip()
        except Exception as e:
            logger.error("[prompt_optimizer] API call failed: %s", e)
            return None

    def _summarize_trajectories(self, trajectories: list[TaskTrajectory]) -> str:
        """Extract patterns from trajectories for the API prompt."""
        from collections import Counter

        parts = []
        all_files = []
        for t in trajectories:
            all_files.extend(t.files_modified)

        if all_files:
            top_files = Counter(all_files).most_common(5)
            parts.append("高频修改文件: " + ", ".join(f"{f}({c}次)" for f, c in top_files))

        if trajectories:
            avg_elapsed = sum(t.latency_s for t in trajectories) / len(trajectories)
            parts.append(f"平均耗时: {avg_elapsed:.1f}秒")
            avg_retries = sum(t.retry_count for t in trajectories) / len(trajectories)
            parts.append(f"平均重试: {avg_retries:.1f}次")

            # Sample user inputs
            inputs = [t.user_input[:80] for t in trajectories[:5]]
            parts.append("典型任务: " + " | ".join(inputs))

        return "\n".join(parts) if parts else "无足够数据"

    @staticmethod
    def _update_yaml(source_path: str, new_prompt: str, expert_name: str,
                     registry: ExpertRegistry) -> bool:
        """Write updated system_prompt back to YAML file."""
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            data["system_prompt"] = new_prompt

            with open(source_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                          sort_keys=False, width=120)

            # Update in-memory registry
            expert_def = registry.get(expert_name)
            if expert_def:
                expert_def["system_prompt"] = new_prompt

            logger.info("[prompt_optimizer] Updated system_prompt for %s", expert_name)
            return True
        except Exception as e:
            logger.error("[prompt_optimizer] Failed to update YAML: %s", e)
            return False

    @staticmethod
    def _update_skill_md(source_path: str, new_rules: str, expert_name: str,
                         registry: ExpertRegistry) -> bool:
        """Append rules to SKILL.md's '## 经验规则' section."""
        try:
            with open(source_path, "r", encoding="utf-8") as f:
                content = f.read()

            rules_header = "## 经验规则（自动生成）"
            if rules_header in content:
                # Append after existing header
                content = content.rstrip() + "\n" + new_rules + "\n"
            else:
                # Add new section at end
                content = content.rstrip() + f"\n\n{rules_header}\n{new_rules}\n"

            with open(source_path, "w", encoding="utf-8") as f:
                f.write(content)

            # Update in-memory registry
            expert_def = registry.get(expert_name)
            if expert_def:
                # Re-read instructions from updated file
                from kaiwu.registry.expert_loader import ExpertLoader
                _, body = ExpertLoader._parse_frontmatter(content)
                expert_def["instructions"] = body.strip()
                expert_def["system_prompt"] = body.strip()

            logger.info("[prompt_optimizer] Updated SKILL.md for %s", expert_name)
            return True
        except Exception as e:
            logger.error("[prompt_optimizer] Failed to update SKILL.md: %s", e)
            return False
