"""
Tests for SKILL.md progressive disclosure expert loading.
"""

import os
import tempfile
from unittest.mock import MagicMock

import pytest

from kaiwu.registry.expert_loader import ExpertLoader
from kaiwu.registry.expert_registry import ExpertRegistry


# ── Fixtures ──

SAMPLE_SKILL_MD = """---
name: TestExpert
version: 1.0.0
trigger_keywords: [test, pytest, unittest]
trigger_min_confidence: 0.7
pipeline: [locator, generator, verifier]
lifecycle: mature
---

## 领域知识

你是测试专家。

### 策略
- 用AAA模式
- 每个函数至少2个测试

## 经验规则（自动生成）
- [2026-04-29] mock路径要指向被测模块
"""

SAMPLE_YAML = """
name: YamlExpert
version: 1.0.0
type: builtin
trigger_keywords:
  - yaml
  - config
trigger_min_confidence: 0.5
system_prompt: "你是YAML专家"
pipeline:
  - generator
  - verifier
lifecycle: mature
"""


def _create_skill_dir(base_dir, name="testexpert", skill_content=SAMPLE_SKILL_MD, scripts=None):
    """Create a SKILL.md directory structure for testing."""
    skill_dir = os.path.join(base_dir, name)
    os.makedirs(skill_dir, exist_ok=True)

    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(skill_content)

    if scripts:
        scripts_dir = os.path.join(skill_dir, "scripts")
        os.makedirs(scripts_dir, exist_ok=True)
        for script_name, script_content in scripts.items():
            with open(os.path.join(scripts_dir, script_name), "w", encoding="utf-8") as f:
                f.write(script_content)

    return skill_dir


def _create_yaml_file(base_dir, name="yaml_expert.yaml", content=SAMPLE_YAML):
    """Create a YAML expert file for testing."""
    path = os.path.join(base_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ── SKILL.md Loading Tests ──

class TestLoadSkillDir:
    """Test loading SKILL.md directory format."""

    def test_basic_load(self):
        """Should parse frontmatter and body correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = _create_skill_dir(tmpdir)
            result = ExpertLoader.load_skill_dir(skill_dir)

        assert result["name"] == "TestExpert"
        assert result["version"] == "1.0.0"
        assert result["trigger_keywords"] == ["test", "pytest", "unittest"]
        assert result["trigger_min_confidence"] == 0.7
        assert result["pipeline"] == ["locator", "generator", "verifier"]
        assert result["lifecycle"] == "mature"
        assert result["_format"] == "skill"

    def test_instructions_field(self):
        """Level 2: instructions should contain markdown body."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = _create_skill_dir(tmpdir)
            result = ExpertLoader.load_skill_dir(skill_dir)

        assert "你是测试专家" in result["instructions"]
        assert "AAA模式" in result["instructions"]
        assert "经验规则" in result["instructions"]

    def test_system_prompt_backward_compat(self):
        """system_prompt should equal instructions for backward compat."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = _create_skill_dir(tmpdir)
            result = ExpertLoader.load_skill_dir(skill_dir)

        assert result["system_prompt"] == result["instructions"]

    def test_scripts_scanned(self):
        """Level 3: scripts should be discovered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scripts = {"extract.py": "print('hello')", "analyze.py": "pass"}
            skill_dir = _create_skill_dir(tmpdir, scripts=scripts)
            result = ExpertLoader.load_skill_dir(skill_dir)

        assert len(result["scripts"]) == 2
        names = [s["name"] for s in result["scripts"]]
        assert "extract" in names
        assert "analyze" in names

    def test_no_scripts_dir(self):
        """Should handle missing scripts/ gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = _create_skill_dir(tmpdir)
            result = ExpertLoader.load_skill_dir(skill_dir)

        assert result["scripts"] == []

    def test_missing_skill_md_raises(self):
        """Should raise if SKILL.md not found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_dir = os.path.join(tmpdir, "empty")
            os.makedirs(empty_dir)
            with pytest.raises(ValueError, match="No SKILL.md"):
                ExpertLoader.load_skill_dir(empty_dir)

    def test_missing_required_fields_raises(self):
        """Should raise if frontmatter missing required fields."""
        bad_skill = "---\nname: Bad\n---\nNo keywords or pipeline."
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = _create_skill_dir(tmpdir, skill_content=bad_skill)
            with pytest.raises(ValueError, match="missing fields"):
                ExpertLoader.load_skill_dir(skill_dir)


# ── Backward Compatibility Tests ──

class TestBackwardCompat:
    """Test that YAML experts still load correctly."""

    def test_yaml_still_loads(self):
        """YAML experts should load as before."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_yaml_file(tmpdir)
            experts = ExpertLoader.load_directory(tmpdir)

        assert len(experts) == 1
        assert experts[0]["name"] == "YamlExpert"
        assert experts[0]["_format"] == "yaml"

    def test_skill_priority_over_yaml(self):
        """SKILL.md should take priority over same-name YAML."""
        # Create both formats with same expert name
        skill_content = """---
name: YamlExpert
version: 2.0.0
trigger_keywords: [yaml, config]
trigger_min_confidence: 0.5
pipeline: [generator, verifier]
lifecycle: mature
---

## 升级版指令
更好的YAML专家。
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_yaml_file(tmpdir)  # YamlExpert via YAML
            _create_skill_dir(tmpdir, name="yamlexpert", skill_content=skill_content)

            experts = ExpertLoader.load_directory(tmpdir)

        # Should only have one expert (SKILL.md version)
        yaml_experts = [e for e in experts if e["name"] == "YamlExpert"]
        assert len(yaml_experts) == 1
        assert yaml_experts[0]["_format"] == "skill"
        assert yaml_experts[0]["version"] == "2.0.0"

    def test_mixed_loading(self):
        """Should load both YAML and SKILL.md experts from same directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_yaml_file(tmpdir)  # YamlExpert
            _create_skill_dir(tmpdir)  # TestExpert (different name)

            experts = ExpertLoader.load_directory(tmpdir)

        names = [e["name"] for e in experts]
        assert "YamlExpert" in names
        assert "TestExpert" in names
        assert len(experts) == 2


# ── Progressive Disclosure Tests ──

class TestProgressiveDisclosure:
    """Test that Gate only uses metadata, Generator gets full instructions."""

    def test_registry_match_uses_only_keywords(self):
        """match() should work with just Level 1 metadata (keywords)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_skill_dir(tmpdir)
            experts = ExpertLoader.load_directory(tmpdir)

        reg = ExpertRegistry()
        for e in experts:
            reg.register(e)

        # Match should work using keywords only
        result = reg.match("写一个pytest单元测试")
        assert result is not None
        assert result["name"] == "TestExpert"

    def test_get_instructions_returns_level2(self):
        """get_instructions() should return full markdown body."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _create_skill_dir(tmpdir)
            experts = ExpertLoader.load_directory(tmpdir)

        reg = ExpertRegistry()
        for e in experts:
            reg.register(e)

        instructions = reg.get_instructions("TestExpert")
        assert "你是测试专家" in instructions
        assert "AAA模式" in instructions

    def test_get_scripts_returns_level3(self):
        """get_scripts() should return script paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scripts = {"helper.py": "pass"}
            _create_skill_dir(tmpdir, scripts=scripts)
            experts = ExpertLoader.load_directory(tmpdir)

        reg = ExpertRegistry()
        for e in experts:
            reg.register(e)

        scripts_list = reg.get_scripts("TestExpert")
        assert len(scripts_list) == 1
        assert scripts_list[0]["name"] == "helper"

    def test_scripts_not_in_system_prompt(self):
        """Script content should NOT be in system_prompt/instructions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scripts = {"secret.py": "API_KEY = 'should_not_appear'"}
            _create_skill_dir(tmpdir, scripts=scripts)
            experts = ExpertLoader.load_directory(tmpdir)

        reg = ExpertRegistry()
        for e in experts:
            reg.register(e)

        instructions = reg.get_instructions("TestExpert")
        assert "should_not_appear" not in instructions
        assert "API_KEY" not in instructions


# ── Prompt Optimizer SKILL.md Tests ──

class TestPromptOptimizerSkill:
    """Test that prompt optimizer can update SKILL.md format."""

    def test_update_skill_md(self):
        """Should append rules to SKILL.md."""
        from kaiwu.flywheel.prompt_optimizer import PromptOptimizer

        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = _create_skill_dir(tmpdir)
            experts = ExpertLoader.load_directory(tmpdir)

            reg = ExpertRegistry()
            for e in experts:
                reg.register(e)

            skill_path = os.path.join(skill_dir, "SKILL.md")
            new_rules = "- [2026-04-30] 新发现的规则"

            # Verify file exists before calling
            assert os.path.isfile(skill_path)

            success = PromptOptimizer._update_skill_md(
                skill_path, new_rules, "TestExpert", reg
            )

            assert success is True
            # Verify file was updated
            with open(skill_path, "r", encoding="utf-8") as f:
                content = f.read()
            assert "新发现的规则" in content

    def test_update_yaml_still_works(self):
        """Should still update YAML format experts."""
        from kaiwu.flywheel.prompt_optimizer import PromptOptimizer

        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = _create_yaml_file(tmpdir)
            experts = ExpertLoader.load_directory(tmpdir)

            reg = ExpertRegistry()
            for e in experts:
                reg.register(e)

            success = PromptOptimizer._update_yaml(
                yaml_path, "updated prompt content", "YamlExpert", reg
            )

        assert success is True


# ── Real Builtin Experts Tests ──

class TestBuiltinExperts:
    """Test loading the actual builtin experts directory."""

    def test_loads_all_experts(self):
        """Should load all 15+ experts (mix of YAML and SKILL.md)."""
        builtin_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "builtin_experts"
        )
        experts = ExpertLoader.load_directory(builtin_dir)

        # Should have at least 15 experts
        assert len(experts) >= 15
        names = [e["name"] for e in experts]

        # SKILL.md versions should be loaded
        assert "BugFixExpert" in names
        assert "FastAPIExpert" in names
        assert "TestGenExpert" in names

    def test_skill_experts_have_instructions(self):
        """SKILL.md experts should have non-empty instructions."""
        builtin_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "builtin_experts"
        )
        experts = ExpertLoader.load_directory(builtin_dir)

        skill_experts = [e for e in experts if e.get("_format") == "skill"]
        assert len(skill_experts) >= 3

        for e in skill_experts:
            assert e.get("instructions"), f"{e['name']} has empty instructions"
            assert "领域知识" in e["instructions"], f"{e['name']} missing 领域知识 section"

    def test_bugfix_has_scripts(self):
        """BugFix SKILL.md expert should have scripts."""
        builtin_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "builtin_experts"
        )
        experts = ExpertLoader.load_directory(builtin_dir)

        bugfix = next(e for e in experts if e["name"] == "BugFixExpert")
        assert bugfix["_format"] == "skill"
        assert len(bugfix["scripts"]) >= 1
        assert bugfix["scripts"][0]["name"] == "extract_traceback"
