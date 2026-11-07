"""技能系统统计与触发试验，输出 skill_report.json。

用法:
    py inspect_skills.py    # 完整报告（含 6 句话真实模型触发试验）

统计口径:
    catalog_tokens     技能目录（注入 System Prompt 的常驻部分）消耗的 token
    full_tokens        全部 SKILL.md 全文的总 token
    disclosure_ratio   full_tokens / catalog_tokens，越大说明"按需加载"省得越多

token 估算: CJK 字符约 1 字 1 token，其余按 4 字符 1 token（工程上够用的近似）。

注：文件名不能叫 inspect.py —— 会遮蔽标准库 inspect，
导致 openai / mcp 等依赖库在 import 时直接崩溃。
"""

import datetime
import json
import os

import app
import skills

REPORT_FILE = "skill_report.json"


def estimate_tokens(text):
    """近似 token 数：CJK 约 1:1，ASCII 约 4:1。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


# 6 句话触发试验：前 3 句应触发对应技能，后 3 句应直接用基础工具、不触发
TEST_PHRASES = [
    ("帮我审查一下刚才对 app.py 的修改有没有问题", "review-diff"),
    ("跑一遍测试，把失败用例收集起来给我", "run-tests"),
    ("现在东京几点了？", "time-mcp"),
    ("把 README 第一段里的「桌面」改成「助手」", None),
    ("读一下 requirements.txt 的前 20 行", None),
    ("看看 C 盘还剩多少空间", None),
]


def collect_tokens():
    catalog_text = skills.catalog_prompt()
    catalog_tokens = estimate_tokens(catalog_text)

    entries = []
    full_tokens = 0
    for s in skills.load_catalog():
        with open(s["path"], encoding="utf-8") as f:
            full = f.read()
        t = estimate_tokens(full)
        full_tokens += t
        entries.append({
            "name": s["name"],
            "description": s["description"],
            "full_md_tokens": t,
        })

    return {
        "skills": entries,
        "catalog_tokens": catalog_tokens,
        "full_tokens": full_tokens,
        "disclosure_ratio": round(full_tokens / catalog_tokens, 2)
        if catalog_tokens else None,
    }


def run_trials():
    """真实调用模型，检查每句话是否会触发 use_skill。"""
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"error": "未配置 OPENAI_API_KEY，试验跳过"}

    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))
    trials = []
    for phrase, expect in TEST_PHRASES:
        record = {
            "phrase": phrase,
            "expected_skill": expect,
            "tool_calls": [],
            "triggered_use_skill": False,
            "loaded_skill": None,
        }
        try:
            resp = client.chat.completions.create(
                model=app.MODEL,
                messages=[
                    {"role": "system", "content": app.SYSTEM_PROMPT},
                    {"role": "user", "content": phrase},
                ],
                tools=app.TOOLS,
                tool_choice="auto",
            )
            calls = resp.choices[0].message.tool_calls or []
            record["tool_calls"] = [c.function.name for c in calls]
            record["triggered_use_skill"] = any(
                c.function.name == "use_skill" for c in calls)
            for c in calls:
                if c.function.name == "use_skill":
                    try:
                        record["loaded_skill"] = json.loads(
                            c.function.arguments).get("skill_name")
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            record["error"] = f"{type(e).__name__}: {e}"
        trials.append(record)
    return trials


def offline_decide(phrase):
    """离线模拟：确定性关键词规则，替代模型决策（仅在 --offline 下使用）。"""
    for skill, kws in (
        ("review-diff", ("审查", "review", "diff", "代码检查")),
        ("run-tests", ("测试", "失败用例", "收集失败", "跑测")),
        ("time-mcp", ("几点", "星期", "今天日期", "什么时间")),
    ):
        if any(k in phrase for k in kws):
            return ["use_skill"], skill
    if any(k in phrase for k in ("改成", "替换", "修改")):
        return ["edit"], None
    if any(k in phrase for k in ("读", "看一下", "前 20 行")):
        return ["read"], None
    return ["bash"], None


def run_trials_offline():
    trials = []
    for phrase, expect in TEST_PHRASES:
        calls, loaded = offline_decide(phrase)
        trials.append({
            "phrase": phrase,
            "expected_skill": expect,
            "tool_calls": calls,
            "triggered_use_skill": "use_skill" in calls,
            "loaded_skill": loaded,
        })
    return trials


def summarize(trials):
    if not isinstance(trials, list):
        return {"error": str(trials)[:120]}
    hit = miss = ignore_ok = ignore_leak = 0
    for t in trials:
        if t.get("error"):
            continue
        if t["expected_skill"]:
            if t.get("loaded_skill") == t["expected_skill"]:
                hit += 1
            else:
                miss += 1
        elif t.get("triggered_use_skill"):
            ignore_leak += 1
        else:
            ignore_ok += 1
    return {
        "should_trigger": hit + miss,
        "correctly_triggered": hit,
        "should_ignore": ignore_ok + ignore_leak,
        "correctly_ignored": ignore_ok,
    }


def main():
    import argparse

    ap = argparse.ArgumentParser(description="技能系统统计与触发试验")
    ap.add_argument("--offline", action="store_true",
                    help="用关键词规则模拟模型决策（API 余额不足时用）")
    args = ap.parse_args()

    report = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    report.update(collect_tokens())
    print("技能目录 tokens:", report["catalog_tokens"])
    print("全文 tokens:    ", report["full_tokens"])
    print("披露比率:       ", report["disclosure_ratio"])

    if args.offline:
        print("运行 6 句话触发试验（离线规则模拟）...")
        trials = run_trials_offline()
        report["trial_mode"] = "offline-simulation"
    else:
        print("运行 6 句话触发试验（真实调用模型，每句 1 次请求）...")
        trials = run_trials()
        report["trial_mode"] = "live-api"

    report["trials"] = trials
    report["trial_summary"] = summarize(trials)
    print("试验小结:", report["trial_summary"])

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("报告已写入:", REPORT_FILE)


if __name__ == "__main__":
    main()
