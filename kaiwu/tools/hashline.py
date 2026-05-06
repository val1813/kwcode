"""
Hashline: content-hash anchored editing.

Each line gets a short content hash anchor (6-char MD5).
Model references anchors instead of reproducing text — no whitespace issues,
no "string not found", no fuzzy matching.

If the file changed since last read, hash mismatches → edit rejected before damage.

Based on oh-my-pi's Hashline approach:
- 61% output token reduction (model only specifies line numbers + new content)
- Eliminates patch_apply failures from text mismatch
"""

import hashlib
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def add_anchors(content: str) -> str:
    """
    给文件内容的每一行加上行号和内容哈希锚点。
    格式: {line_num}|{hash6}| {content}

    Example:
        1|a3f2c1| def hello():
        2|b2e1f3|     return "world"
    """
    lines = content.split("\n")
    result = []
    for i, line in enumerate(lines, 1):
        anchor = _line_hash(line)
        result.append(f"{i}|{anchor}| {line}")
    return "\n".join(result)


def strip_anchors(anchored_content: str) -> str:
    """从锚点格式还原为原始代码。"""
    lines = anchored_content.split("\n")
    result = []
    for line in lines:
        m = re.match(r'^\d+\|[a-f0-9]{6}\| (.*)$', line)
        if m:
            result.append(m.group(1))
        else:
            result.append(line)
    return "\n".join(result)


def parse_anchor_edits(model_output: str) -> list[dict]:
    """
    解析模型的锚点编辑指令。

    支持三种指令格式:
      EDIT {line}|{hash}| → {new_content}
      DELETE {line}|{hash}|
      INSERT_AFTER {line}|{hash}| → {new_content}

    返回: [{"action": "edit"|"delete"|"insert_after", "line": int, "hash": str, "content": str}]
    """
    edits = []
    for line in model_output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # EDIT 47|a3f2c1| → return validate_user(token, new_pass)
        m = re.match(r'^EDIT\s+(\d+)\|([a-f0-9]{6})\|\s*→\s*(.+)$', line)
        if m:
            edits.append({
                "action": "edit",
                "line": int(m.group(1)),
                "hash": m.group(2),
                "content": m.group(3),
            })
            continue

        # DELETE 47|a3f2c1|
        m = re.match(r'^DELETE\s+(\d+)\|([a-f0-9]{6})\|', line)
        if m:
            edits.append({
                "action": "delete",
                "line": int(m.group(1)),
                "hash": m.group(2),
                "content": "",
            })
            continue

        # INSERT_AFTER 47|a3f2c1| → new_line_content
        m = re.match(r'^INSERT_AFTER\s+(\d+)\|([a-f0-9]{6})\|\s*→\s*(.+)$', line)
        if m:
            edits.append({
                "action": "insert_after",
                "line": int(m.group(1)),
                "hash": m.group(2),
                "content": m.group(3),
            })
            continue

    return edits


def apply_anchor_edits(content: str, edits: list[dict]) -> tuple[str, list[str]]:
    """
    将锚点编辑指令应用到文件内容。

    Args:
        content: 原始文件内容（无锚点）
        edits: parse_anchor_edits()的返回值

    Returns:
        (new_content, errors): 新内容和错误列表。
        任何哈希不匹配的编辑被拒绝，其余正常应用。
    """
    lines = content.split("\n")
    errors = []

    # Validate all hashes first — reject entire batch if any mismatch
    for edit in edits:
        line_idx = edit["line"] - 1
        if line_idx < 0 or line_idx >= len(lines):
            errors.append(f"Line {edit['line']} out of range (file has {len(lines)} lines)")
            continue
        expected_hash = _line_hash(lines[line_idx])
        if edit["hash"] != expected_hash:
            errors.append(
                f"Hash mismatch at line {edit['line']}: "
                f"expected {expected_hash}, got {edit['hash']} "
                f"(file may have changed since last read)"
            )

    if errors:
        return content, errors  # No changes applied

    # Apply edits in reverse order (so line numbers stay valid)
    sorted_edits = sorted(edits, key=lambda e: e["line"], reverse=True)
    for edit in sorted_edits:
        idx = edit["line"] - 1
        if edit["action"] == "edit":
            # Preserve original indentation
            original_indent = _get_indent(lines[idx])
            new_content = edit["content"]
            if not new_content.startswith((" ", "\t")):
                new_content = original_indent + new_content
            lines[idx] = new_content
        elif edit["action"] == "delete":
            lines.pop(idx)
        elif edit["action"] == "insert_after":
            original_indent = _get_indent(lines[idx])
            new_content = edit["content"]
            if not new_content.startswith((" ", "\t")):
                new_content = original_indent + new_content
            lines.insert(idx + 1, new_content)

    return "\n".join(lines), []


def _line_hash(line: str) -> str:
    """计算单行内容的6字符MD5哈希锚点。"""
    return hashlib.md5(line.encode("utf-8")).hexdigest()[:6]


def _get_indent(line: str) -> str:
    """提取行首缩进。"""
    stripped = line.lstrip()
    return line[:len(line) - len(stripped)]
