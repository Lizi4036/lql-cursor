---
name: run-tests
description: 运行项目测试并收集失败详情；附带 collect_failures.py 脚本，可把失败用例整理成 JSON 简报
demo_prompt: 请使用 run-tests 技能跑一遍测试并收集失败用例
---

# 测试执行技能 (run-tests)

当用户要求「跑测试」「测试怎么失败了」「统计失败用例」时，按以下流程执行。

## 使用步骤

1. 用 `bash` 运行测试（项目根目录）：

   ```
   py -m pytest tests/ -q --tb=short
   ```

   没有 tests/ 目录时先确认测试位置，不要盲目跑全盘。

2. 若有失败，调用本技能自带的脚本收集失败详情：

   ```
   py skills/run-tests/scripts/collect_failures.py
   ```

   脚本会重跑 pytest 并把失败信息整理成 JSON 简报。

3. 根据脚本输出向用户解释：失败的是什么、为什么失败、建议怎么修。

## collect_failures.py 说明

- 位置：`skills/run-tests/scripts/collect_failures.py`
- 输入：无参数（自动找 pytest）或 `--from-file <文件>` 解析已有输出
- 输出：JSON，包含 `total`（用例总数）、`failed`（失败用例列表，含
  测试名 / 文件 / 断言信息）、`summary`（一句话总结）
- 退出码：有失败返回 1，全部通过返回 0

## 注意

- 测试会执行任意代码，运行前确认测试来源可信
- 长时间测试用 `bash` 的 `timeout` 参数调整，默认 30 秒可能不够
- 收集脚本本身不改任何文件，只读取
