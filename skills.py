"""技能系统：按需加载（渐进披露）。

启动时只扫描各技能的 name / description 组成"技能目录"注入 System Prompt；
模型调用 use_skill(skill_name) 时才读取 SKILL.md 全文。

目录结构（与 Claude Code 的 Skill 约定一致）：

    skills/
    ├── review-diff/SKILL.md
    ├── run-tests/
    │   ├── SKILL.md
    │   └── scripts/collect_failures.py
    └── time-mcp/SKILL.md

SKILL.md 头部使用 front-matter：

    ---
    name: review-diff
    description: 一句话说明（进入目录，常驻上下文）
    ---
    正文（按需加载，不进目录）
"""

import os
import sys

USE_SKILL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "按需加载一个技能的完整指引。技能目录已在系统提示中，"
            "当用户任务与某个技能相关时，先调用本工具读取全文，再按指引操作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "技能名，须与目录中列出的名称完全一致",
                },
            },
            "required": ["skill_name"],
        },
    },
}


def _skills_dir():
    """skills 目录：开发态为脚本同级，打包后为 exe 同级（与 mcp_client 同策略）。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "skills")


def parse_front_matter(path):
    """解析 SKILL.md 头部的 --- 包裹键值对；解析失败返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(2048)
    except OSError:
        return None

    if not head.startswith("---"):
        return None
    end = head.find("---", 3)
    if end < 0:
        return None

    meta = {}
    for line in head[3:end].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    if meta.get("name") and meta.get("description"):
        return meta
    return None


def load_catalog():
    """扫描 skills/，只读 front-matter，返回技能目录列表。"""
    skills = []
    root = _skills_dir()
    if not os.path.isdir(root):
        return skills
    for entry in sorted(os.listdir(root)):
        skill_md = os.path.join(root, entry, "SKILL.md")
        if os.path.isfile(skill_md):
            meta = parse_front_matter(skill_md)
            if meta:
                skills.append({
                    "name": meta["name"],
                    "description": meta["description"],
                    "path": skill_md,
                })
    return skills


def catalog_prompt():
    """生成注入 System Prompt 的技能目录文本；无技能时返回空串。"""
    skills = load_catalog()
    if not skills:
        return ""
    lines = [
        "",
        "可用技能（按需加载）：执行相关任务前，先调用 use_skill(skill_name)",
        "读取完整指引，再按指引操作，不要凭记忆执行：",
    ]
    for s in skills:
        lines.append(f"- {s['name']}：{s['description']}")
    return "\n".join(lines)


def load_skill(skill_name):
    """读取技能全文；找不到返回 None。"""
    for s in load_catalog():
        if s["name"] == skill_name:
            try:
                with open(s["path"], encoding="utf-8") as f:
                    return f.read()
            except OSError as e:
                return f"[错误] 技能文件读取失败: {e}"
    return None


def use_skill(skill_name):
    """工具入口：返回 SKILL.md 全文。未知技能时列出可用项。"""
    content = load_skill(skill_name)
    if content is None:
        names = "、".join(s["name"] for s in load_catalog()) or "无"
        return f"[错误] 未知技能: {skill_name}。可用技能: {names}"
    return content
