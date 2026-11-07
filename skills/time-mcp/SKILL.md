---
name: time-mcp
description: 查询当前时间或任意城市时区时间；必须通过 MCP 工具 get_current_time 获取，禁止凭记忆猜测
demo_prompt: 请使用 time-mcp 技能查询现在东京几点
---

# 时间查询技能 (time-mcp)

当用户问「现在几点」「东京/纽约现在是几点」「今天星期几」等时间问题时使用。

## 如何调用

调用 MCP 工具 `get_current_time`，参数：

- `city`：城市名，中文或英文均可（如 北京、东京、New York）

## 返回值说明

- 正常：`YYYY-MM-DD HH:MM:SS (UTC±H) 城市名`
- 未收录城市：回退 UTC 并明确说明该城市未收录，**如实转告用户**，不要编造

## 纪律

- 任何时间问题都必须调用工具获取，不要用训练数据里的记忆回答——
  你的记忆是过时的
- 用户连续追问不同城市时，每个城市单独调用一次
- 若 MCP 未连接（工具列表里没有 get_current_time），告知用户
  MCP Server 未启用，而不是拒绝回答其他部分
