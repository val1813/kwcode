"""
Checkpoint: file snapshot before task execution.
Git repos use git stash; non-git repos copy files to ~/.kwcode/checkpoints/.
P1-RED-3: Failure must be reported to user, never silent.
P1-FLEX-1: Non-git repos use file copy fallback.
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path.home() / ".kwcode" / "checkpoints"
STASH_PREFIX = "kwcode-checkpoint"


class Checkpoint:

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self._is_git = (self.project_root / ".git").exists()
        self._stash_name = f"{STASH_PREFIX}-{int(time.time())}"
        self._file_backup_dir: Path | None = None
        self._saved = False

    def save(self, modified_files: list[str] | None = None) -> bool:
        """
        Create snapshot before task execution.
        Returns True on success, False on failure (caller must notify user per P1-RED-3).
        """
        try:
            # Verify project_root exists
            if not self.project_root.exists():
                logger.debug("[checkpoint] project_root does not exist: %s", self.project_root)
                return False
            if self._is_git:
                return self._git_stash()
            else:
                return self._file_copy(modified_files or [])
        except Exception as e:
            logger.warning("[checkpoint] save failed: %s", e)
            return False

    def restore(self) -> bool:
        """Restore to snapshot state."""
        if not self._saved:
            return False
        try:
            if self._is_git:
                return self._git_stash_pop()
            else:
                return self._file_restore()
        except Exception as e:
            logger.warning("[checkpoint] restore failed: %s", e)
            return False

    def discard(self):
        """Clean up snapshot after successful task."""
        if not self._saved:
            return
        try:
            if self._is_git:
                subprocess.run(
                    ["git", "stash", "drop"],
                    cwd=self.project_root,
                    capture_output=True,
                    timeout=5,
                )
            elif self._file_backup_dir:
                shutil.rmtree(self._file_backup_dir, ignore_errors=True)
        except Exception:
            pass  # Cleanup failure is non-critical

    def _git_stash(self) -> bool:
        result = subprocess.run(
            ["git", "stash", "push", "--include-untracked", "-m", self._stash_name],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        # "No local changes" is not a failure — just nothing to stash
        if result.returncode == 0:
            self._saved = "No local changes" not in result.stdout
            return True
        logger.warning("[checkpoint] git stash failed: %s", result.stderr)
        return False

    def _git_stash_pop(self) -> bool:
        result = subprocess.run(
            ["git", "stash", "pop"],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    def _file_copy(self, files: list[str]) -> bool:
        """Non-git fallback: copy files to ~/.kwcode/checkpoints/."""
        try:
            backup_dir = CHECKPOINT_DIR / self._stash_name
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("[checkpoint] cannot create backup dir: %s", e)
            return False
        self._file_backup_dir = backup_dir

        # If no specific files given, scan project for common code files
        if not files:
            try:
                for ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".html", ".css"):
                    for p in self.project_root.rglob(f"*{ext}"):
                        if any(skip in p.parts for skip in (".git", "__pycache__", "node_modules", ".venv")):
                            continue
                        files.append(str(p))
            except OSError as e:
                logger.debug("[checkpoint] scan failed: %s", e)

        if not files:
            # No files to backup — codegen task creating new files, nothing to snapshot
            self._saved = True
            return True

        # Store relative path mapping for restore
        manifest = {}
        for f in files:
            src = Path(f)
            if not src.exists():
                continue
            try:
                rel = src.relative_to(self.project_root)
            except ValueError:
                rel = Path(src.name)
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            manifest[str(rel)] = str(src)

        # Save manifest
        import json
        manifest_path = backup_dir / "_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        self._saved = True
        return True

    def _file_restore(self) -> bool:
        """Restore files from backup using manifest."""
        if not self._file_backup_dir:
            return False

        import json
        manifest_path = self._file_backup_dir / "_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.debug("Manifest JSON damaged, falling back to name-based restore")
                manifest = None
            if manifest:
                for rel, original_path in manifest.items():
                    backup_file = self._file_backup_dir / rel
                    if backup_file.exists():
                        shutil.copy2(backup_file, original_path)
                return True

        # Fallback: simple name-based restore
        for f in self._file_backup_dir.rglob("*"):
            if f.is_file() and f.name != "_manifest.json":
                for candidate in self.project_root.rglob(f.name):
                    shutil.copy2(f, candidate)
                    break
        return True


def list_checkpoints() -> list[dict]:
    """List all checkpoint snapshots."""
    if not CHECKPOINT_DIR.exists():
        return []
    result = []
    for d in sorted(CHECKPOINT_DIR.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith(STASH_PREFIX):
            ts = d.name.split("-")[-1]
            try:
                created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
            except (ValueError, OSError):
                created = "unknown"
            files = [f.name for f in d.rglob("*") if f.is_file() and f.name != "_manifest.json"]
            result.append({"name": d.name, "created": created, "files": len(files), "path": str(d)})
    return result


def restore_latest() -> bool:
    """Restore the most recent checkpoint."""
    checkpoints = list_checkpoints()
    if not checkpoints:
        return False
    latest = checkpoints[0]
    backup_dir = Path(latest["path"])

    import json
    manifest_path = backup_dir / "_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        for rel, original_path in manifest.items():
            backup_file = backup_dir / rel
            if backup_file.exists():
                shutil.copy2(backup_file, original_path)
        return True
    return False
