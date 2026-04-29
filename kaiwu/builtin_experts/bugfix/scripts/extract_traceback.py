"""从pytest输出中提取关键失败信息。
确定性脚本，不进LLM context，直接执行返回结构化结果。
"""

import re
import sys


def extract_traceback(pytest_output: str) -> dict:
    """
    从pytest输出中提取：
    - 失败的测试名
    - 异常类型和消息
    - 出错的文件和行号
    返回结构化dict。
    """
    result = {
        "failed_tests": [],
        "exceptions": [],
        "error_locations": [],
    }

    # 提取失败的测试名
    failed_pattern = r"FAILED\s+([\w/.:]+)"
    for m in re.finditer(failed_pattern, pytest_output):
        result["failed_tests"].append(m.group(1))

    # 提取异常类型和消息
    exc_pattern = r"([\w.]+Error|[\w.]+Exception):\s*(.+)"
    for m in re.finditer(exc_pattern, pytest_output):
        result["exceptions"].append({
            "type": m.group(1),
            "message": m.group(2).strip()[:200],
        })

    # 提取文件:行号
    loc_pattern = r'File "([^"]+)", line (\d+)'
    for m in re.finditer(loc_pattern, pytest_output):
        filepath = m.group(1)
        # 跳过标准库和site-packages
        if "site-packages" in filepath or "lib/python" in filepath:
            continue
        result["error_locations"].append({
            "file": filepath,
            "line": int(m.group(2)),
        })

    return result


if __name__ == "__main__":
    import json
    text = sys.stdin.read()
    print(json.dumps(extract_traceback(text), ensure_ascii=False, indent=2))
