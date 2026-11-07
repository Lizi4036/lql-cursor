"""收集 pytest 失败用例，输出 JSON 简报。

用法:
    py skills/run-tests/scripts/collect_failures.py                 # 自动重跑 pytest
    py skills/run-tests/scripts/collect_failures.py --from-file log # 解析已有输出

只读不写：不改任何项目文件。
"""

import argparse
import json
import os
import re
import subprocess
import sys

FAILED_RE = re.compile(r"^FAILED (.+?)(?: - (.*))?$")
SUMMARY_RE = re.compile(r"(\d+) failed(?:, (\d+) passed)?")


def run_pytest(extra):
    """重跑 pytest（不带 --tb 详情，行短易解析），返回 stdout。"""
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=no", "-rA"] + list(extra)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=120,
            encoding="utf-8",
        )
        out = r.stdout or ""
        if "No module named pytest" in (r.stderr or ""):
            out += "\n[collect_failures] pytest 未安装，请先执行: py -m pip install pytest"
        return out
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired as e:
        return (e.stdout or "") + "\n[collect_failures] pytest 超时(120s)"


def parse(text):
    failed = []
    for line in text.splitlines():
        line = line.strip()
        m = FAILED_RE.match(line)
        if m:
            nodeid, msg = m.group(1), (m.group(2) or "").strip()
            path, _, case = nodeid.partition("::")
            failed.append({
                "test": nodeid,
                "file": path,
                "case": case or path,
                "error": msg[:300],
            })
    total_failed = passed = 0
    for line in text.splitlines():
        if " failed" in line or " passed" in line:
            m = SUMMARY_RE.search(line)
            if m:
                total_failed = int(m.group(1))
                passed = int(m.group(2) or 0)
    return failed, total_failed, passed


def main():
    ap = argparse.ArgumentParser(description="收集 pytest 失败用例为 JSON 简报")
    ap.add_argument("--from-file", help="从文件读取 pytest 输出，而不是重跑")
    ap.add_argument("pytest_args", nargs="*",
                    help="传给 pytest 的额外参数（如 tests/ 目录）")
    args = ap.parse_args()

    if args.from_file:
        with open(args.from_file, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = run_pytest(args.pytest_args)

    failed, n_failed, n_passed = parse(text)
    report = {
        "total_failed": n_failed,
        "total_passed": n_passed,
        "failed": failed,
        "summary": (
            f"共 {n_failed} 个失败、{n_passed} 个通过"
            if (n_failed or n_passed) else "未解析到 pytest 结果（测试未运行或输出格式不符）"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(1 if n_failed else 0)


if __name__ == "__main__":
    main()
