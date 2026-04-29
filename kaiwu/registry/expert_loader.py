"""
Expert loader: supports both YAML files and SKILL.md directory format.
Progressive disclosure: metadata (Level 1) loaded at startup,
instructions (Level 2) loaded on demand, scripts (Level 3) on demand.
"""

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = {"name", "version", "type", "trigger_keywords", "trigger_min_confidence", "system_prompt", "pipeline"}
REQUIRED_FIELDS_SKILL = {"name", "trigger_keywords", "trigger_min_confidence", "pipeline"}
VALID_LIFECYCLES = {"new", "mature", "declining", "archived"}
VALID_PIPELINE_STEPS = {"locator", "generator", "verifier", "office", "chat"}


class ExpertLoader:
    """Load expert YAML files and SKILL.md directories."""

    @staticmethod
    def load_yaml(path: str) -> dict:
        """Load and validate a single expert YAML file."""
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid YAML structure in {path}")

        valid, err = ExpertLoader.validate(data)
        if not valid:
            raise ValueError(f"Validation failed for {path}: {err}")

        # Ensure defaults
        data.setdefault("lifecycle", "new")
        data.setdefault("performance", {"success_rate": 0.0, "avg_latency_s": 0, "task_count": 0})
        data["_source"] = path
        data["_format"] = "yaml"
        return data

    @staticmethod
    def load_skill_dir(dir_path: str) -> dict:
        """
        Load a SKILL.md directory-based expert.
        Returns expert_def dict compatible with registry.

        Progressive disclosure:
          - Level 1 (metadata): name, keywords, confidence, pipeline → loaded now
          - Level 2 (instructions): markdown body → stored in 'instructions' field
          - Level 3 (scripts): paths stored in 'scripts' field, executed on demand
        """
        import yaml

        skill_path = os.path.join(dir_path, "SKILL.md")
        if not os.path.isfile(skill_path):
            raise ValueError(f"No SKILL.md found in {dir_path}")

        with open(skill_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse YAML frontmatter
        frontmatter, body = ExpertLoader._parse_frontmatter(content)
        if not frontmatter:
            raise ValueError(f"No YAML frontmatter in {skill_path}")

        # Validate required fields
        missing = REQUIRED_FIELDS_SKILL - set(frontmatter.keys())
        if missing:
            raise ValueError(f"SKILL.md missing fields {missing}: {skill_path}")

        # Build expert definition (compatible with existing registry)
        data = {
            "name": frontmatter["name"],
            "version": frontmatter.get("version", "1.0.0"),
            "type": "skill",
            "trigger_keywords": frontmatter["trigger_keywords"],
            "trigger_min_confidence": frontmatter["trigger_min_confidence"],
            "pipeline": frontmatter["pipeline"],
            "lifecycle": frontmatter.get("lifecycle", "new"),
            "performance": {"success_rate": 0.0, "avg_latency_s": 0, "task_count": 0},
            # Level 2: full instructions (markdown body)
            "instructions": body.strip(),
            # Backward compat: system_prompt = instructions for existing code paths
            "system_prompt": body.strip(),
            # Level 3: scripts
            "scripts": ExpertLoader._scan_scripts(dir_path),
            # References
            "references": ExpertLoader._scan_references(dir_path),
            # Source tracking
            "_source": skill_path,
            "_source_dir": dir_path,
            "_format": "skill",
        }

        return data

    @staticmethod
    def load_directory(dir_path: str) -> list[dict]:
        """Load all experts from a directory (YAML files + SKILL.md subdirectories)."""
        experts = []
        if not os.path.isdir(dir_path):
            logger.debug("Expert directory not found: %s", dir_path)
            return experts

        loaded_names = {}  # name -> format, SKILL.md takes priority

        for fname in sorted(os.listdir(dir_path)):
            fpath = os.path.join(dir_path, fname)

            # SKILL.md directory format (priority)
            if os.path.isdir(fpath):
                skill_md = os.path.join(fpath, "SKILL.md")
                if os.path.isfile(skill_md):
                    try:
                        expert = ExpertLoader.load_skill_dir(fpath)
                        name = expert["name"]
                        # SKILL.md has priority over YAML
                        if name in loaded_names and loaded_names[name] == "yaml":
                            experts = [e for e in experts if e["name"] != name]
                        loaded_names[name] = "skill"
                        experts.append(expert)
                        logger.debug("Loaded SKILL.md expert: %s from %s/", name, fname)
                    except Exception as e:
                        logger.warning("Failed to load skill dir %s: %s", fname, e)
                continue

            # YAML file format (backward compat)
            if not fname.endswith((".yaml", ".yml")):
                continue
            try:
                expert = ExpertLoader.load_yaml(fpath)
                name = expert["name"]
                # Only add if no SKILL.md version already loaded
                if name not in loaded_names or loaded_names[name] != "skill":
                    loaded_names[name] = "yaml"
                    experts.append(expert)
                    logger.debug("Loaded expert: %s from %s", name, fname)
            except Exception as e:
                logger.warning("Failed to load expert %s: %s", fname, e)

        return experts

    @staticmethod
    def validate(expert_def: dict) -> tuple[bool, str]:
        """Validate expert definition. Returns (valid, error_message)."""
        missing = REQUIRED_FIELDS - set(expert_def.keys())
        if missing:
            return False, f"Missing fields: {missing}"

        if not isinstance(expert_def["trigger_keywords"], list) or len(expert_def["trigger_keywords"]) == 0:
            return False, "trigger_keywords must be a non-empty list"

        conf = expert_def["trigger_min_confidence"]
        if not isinstance(conf, (int, float)) or not (0.0 < conf <= 1.0):
            return False, f"trigger_min_confidence must be in (0, 1], got {conf}"

        for step in expert_def["pipeline"]:
            if step not in VALID_PIPELINE_STEPS:
                return False, f"Invalid pipeline step: {step}"

        lifecycle = expert_def.get("lifecycle", "new")
        if lifecycle not in VALID_LIFECYCLES:
            return False, f"Invalid lifecycle: {lifecycle}"

        return True, ""

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        """Parse YAML frontmatter from SKILL.md content."""
        import yaml

        # Match --- ... --- frontmatter block
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
        if not match:
            return {}, content

        try:
            frontmatter = yaml.safe_load(match.group(1))
            body = match.group(2)
            return frontmatter or {}, body
        except Exception:
            return {}, content

    @staticmethod
    def _scan_scripts(dir_path: str) -> list[dict]:
        """Scan scripts/ directory for executable scripts."""
        scripts_dir = os.path.join(dir_path, "scripts")
        if not os.path.isdir(scripts_dir):
            return []

        scripts = []
        for fname in sorted(os.listdir(scripts_dir)):
            if fname.endswith(".py"):
                scripts.append({
                    "name": fname[:-3],  # strip .py
                    "path": os.path.join(scripts_dir, fname),
                })
        return scripts

    @staticmethod
    def _scan_references(dir_path: str) -> list[str]:
        """Scan references/ directory for supplementary docs."""
        refs_dir = os.path.join(dir_path, "references")
        if not os.path.isdir(refs_dir):
            return []

        return [
            os.path.join(refs_dir, f)
            for f in sorted(os.listdir(refs_dir))
            if f.endswith((".md", ".txt"))
        ]
