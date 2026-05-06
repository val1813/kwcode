"""
Import Fixer: 确定性修复缺失 import（不调 LLM）。

当 Verifier 报告 import 错误时，尝试自动修复：
- 从错误信息提取缺失模块名
- 在文件头部添加 import 语句

理论来源：
- Turn-Control Strategies 动态预算（arXiv:2510.16786）
- Wink 失败类型分类（arXiv:2602.17037）
"""

import re
import logging

logger = logging.getLogger(__name__)

# 常见模块的正确 import 语句映射
KNOWN_IMPORTS = {
    "json": "import json",
    "os": "import os",
    "sys": "import sys",
    "re": "import re",
    "time": "import time",
    "datetime": "from datetime import datetime",
    "pathlib": "from pathlib import Path",
    "typing": "from typing import Optional, List, Dict",
    "logging": "import logging",
    "subprocess": "import subprocess",
    "shutil": "import shutil",
    "tempfile": "import tempfile",
    "threading": "import threading",
    "collections": "from collections import defaultdict",
    "dataclasses": "from dataclasses import dataclass, field",
    "functools": "import functools",
    "itertools": "import itertools",
    "hashlib": "import hashlib",
    "uuid": "import uuid",
    "copy": "import copy",
    "math": "import math",
    "random": "import random",
    "traceback": "import traceback",
    "inspect": "import inspect",
    "abc": "from abc import ABC, abstractmethod",
    "enum": "from enum import Enum",
    "contextlib": "import contextlib",
    "io": "import io",
    "glob": "import glob",
    "fnmatch": "import fnmatch",
    "textwrap": "import textwrap",
    "urllib": "import urllib",
    "http": "import http",
    "socket": "import socket",
    "asyncio": "import asyncio",
    "pytest": "import pytest",
    "yaml": "import yaml",
    "httpx": "import httpx",
    "requests": "import requests",
}


def fix_missing_import(content: str, error_message: str) -> str | None:
    """
    尝试修复缺失的 import。

    Args:
        content: 文件内容
        error_message: 错误信息（如 "No module named 'json'"）

    Returns:
        修复后的文件内容，或 None（无法修复时）
    """
    # 提取缺失模块名
    module = _extract_module_name(error_message)
    if not module:
        return None

    # 检查是否已经 import 了
    if _already_imported(content, module):
        return None

    # 生成 import 语句
    import_stmt = _build_import_statement(module)
    if not import_stmt:
        return None

    # 插入到文件合适位置
    return _insert_import(content, import_stmt)


def _extract_module_name(error_message: str) -> str | None:
    """从错误信息中提取模块名。"""
    patterns = [
        r"No module named '(\S+?)'",
        r"No module named \"(\S+?)\"",
        r"ModuleNotFoundError:.*'(\S+?)'",
        r"ImportError:.*cannot import name '(\w+)' from '(\S+?)'",
        r"NameError: name '(\w+)' is not defined",
    ]
    for pat in patterns:
        match = re.search(pat, error_message)
        if match:
            # 取顶层模块名
            full = match.group(1)
            return full.split(".")[0]
    return None


def _already_imported(content: str, module: str) -> bool:
    """检查模块是否已经被 import。"""
    patterns = [
        rf"^import\s+{re.escape(module)}\b",
        rf"^from\s+{re.escape(module)}\b",
    ]
    for pat in patterns:
        if re.search(pat, content, re.MULTILINE):
            return True
    return False


def _build_import_statement(module: str) -> str | None:
    """生成 import 语句。"""
    if module in KNOWN_IMPORTS:
        return KNOWN_IMPORTS[module]
    # 未知模块：生成通用 import
    if re.match(r'^[a-zA-Z_]\w*$', module):
        return f"import {module}"
    return None


def _insert_import(content: str, import_stmt: str) -> str:
    """在文件合适位置插入 import 语句。"""
    lines = content.split("\n")

    # 找到最后一个 import/from 行的位置
    last_import_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            last_import_idx = i
        # 跳过文件头部的注释和空行
        elif stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
            if last_import_idx >= 0:
                break

    if last_import_idx >= 0:
        # 在最后一个 import 后面插入
        lines.insert(last_import_idx + 1, import_stmt)
    else:
        # 没有 import 语句，在文件开头（跳过 shebang 和 docstring）
        insert_pos = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#!") or stripped.startswith("#") or not stripped:
                insert_pos = i + 1
            elif stripped.startswith('"""') or stripped.startswith("'''"):
                # 跳过 docstring
                end_quote = stripped[:3]
                if stripped.count(end_quote) >= 2:
                    insert_pos = i + 1
                else:
                    for j in range(i + 1, len(lines)):
                        if end_quote in lines[j]:
                            insert_pos = j + 1
                            break
                break
            else:
                break
        lines.insert(insert_pos, import_stmt)

    return "\n".join(lines)
