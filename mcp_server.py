"""极简 MCP Server —— 提供 get_current_time 工具。

⚠️ 协议红线（必读）：

    stdio 传输模式下，**stdout 是 JSON-RPC 消息的专用通道**。
    任何一句 print() 都会把普通文本混进协议流里，
    客户端解析失败 → 报 "invalid JSON" → 连接直接断开。

    所以本文件里：
      - 绝不出现 print()
      - 所有日志通过 logging 写到 stderr
      - 调试信息也只能走 stderr（sys.stderr.write）

单独运行（通常由客户端拉起，不需要手动跑）：
    py mcp_server.py
"""

import logging
import sys
from datetime import datetime, timedelta, timezone

from mcp.server.mcpserver import MCPServer

# ---------------------------------------------------------------- 日志配置
# stream 显式指向 stderr：这是唯一安全的输出通道
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="[mcp-server] %(levelname)s: %(message)s",
)
logger = logging.getLogger("mcp_server")

# ---------------------------------------------------------------- Server 实例
server = MCPServer("time-server", log_level="INFO")

# 城市 -> UTC 偏移（小时）。只做简单映射，不引入 pytz/zoneinfo 之外的重依赖；
# 说明：不处理夏令时，教学演示足够。
CITY_OFFSETS = {
    "北京": 8, "上海": 8, "广州": 8, "深圳": 8, "杭州": 8, "成都": 8,
    "香港": 8, "澳门": 8, "台北": 8, "新加坡": 8, "吉隆坡": 8,
    "东京": 9, "大阪": 9, "首尔": 9,
    "悉尼": 10, "墨尔本": 10,
    "迪拜": 4,
    "莫斯科": 3,
    "柏林": 1, "巴黎": 1, "罗马": 1, "马德里": 1,
    "伦敦": 0,
    "纽约": -5, "华盛顿": -5, "波士顿": -5,
    "芝加哥": -6,
    "洛杉矶": -8, "旧金山": -8, "西雅图": -8,
}


@server.tool()
def get_current_time(city: str = "北京") -> str:
    """获取指定城市的当前时间。

    Args:
        city: 城市名称，例如 北京、上海、东京、伦敦、纽约。不填默认北京。

    Returns:
        形如 "2026-11-03 14:25:07 (UTC+8)" 的时间字符串。
    """
    logger.info("收到调用 get_current_time(city=%r)", city)  # 只写 stderr

    name = (city or "北京").strip()
    offset = CITY_OFFSETS.get(name)

    if offset is None:
        # 未收录的城市：返回 UTC 时间 + 明确说明，让模型知道信息不完整
        now_utc = datetime.now(timezone.utc)
        logger.warning("未收录城市: %r，回退 UTC", name)
        return (
            f"未收录城市「{name}」，以下为 UTC 时间："
            f"{now_utc.strftime('%Y-%m-%d %H:%M:%S')} (UTC+0)"
        )

    tz = timezone(timedelta(hours=offset))
    now = datetime.now(tz)
    sign = "+" if offset >= 0 else "-"
    result = f"{now.strftime('%Y-%m-%d %H:%M:%S')} (UTC{sign}{abs(offset)}) {name}"
    logger.info("返回结果: %s", result)
    return result


@server.tool()
def add_numbers(a: float, b: float) -> str:
    """把两个数字相加，用于验证 MCP 能注册并调用多个工具。

    Args:
        a: 第一个数
        b: 第二个数

    Returns:
        计算过程与结果的字符串。
    """
    logger.info("收到调用 add_numbers(%r, %r)", a, b)
    return f"{a} + {b} = {a + b}"


if __name__ == "__main__":
    # 默认 transport="stdio"：由客户端以子进程方式拉起，通过标准输入输出通信
    logger.info("MCP Server 启动，transport=stdio")
    server.run()
