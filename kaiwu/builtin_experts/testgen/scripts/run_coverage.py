"""运行pytest并返回覆盖率摘要。
确定性脚本，不进LLM context，直接执行。
"""

import subprocess
import sys
import json


def run_coverage(test_path: str = ".", source_path: str = ".") -> dict:
    """
    运行pytest --cov并返回覆盖率数据。
    返回：{"total_coverage": float, "uncovered_files": [...], "passed": bool}
    """
    result = {
        "total_coverage": 0.0,
        "uncovered_files": [],
        "passed": False,
    }

    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", test_path, f"--cov={source_path}",
             "--cov-report=json", "--cov-report=term", "-q", "--tb=no"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        result["passed"] = proc.returncode == 0

        # 尝试读取coverage JSON
        try:
            import os
            cov_file = os.path.join(os.getcwd(), "coverage.json")
            if os.path.exists(cov_file):
                with open(cov_file, "r", encoding="utf-8") as f:
                    cov_data = json.load(f)
                result["total_coverage"] = cov_data.get("totals", {}).get("percent_covered", 0.0)
                # 找出覆盖率低的文件
                for fname, fdata in cov_data.get("files", {}).items():
                    pct = fdata.get("summary", {}).get("percent_covered", 100)
                    if pct < 50:
                        result["uncovered_files"].append({"file": fname, "coverage": pct})
        except Exception:
            pass

    except subprocess.TimeoutExpired:
        result["passed"] = False
    except Exception:
        pass

    return result


if __name__ == "__main__":
    test_path = sys.argv[1] if len(sys.argv) > 1 else "."
    source_path = sys.argv[2] if len(sys.argv) > 2 else "."
    print(json.dumps(run_coverage(test_path, source_path), ensure_ascii=False, indent=2))
