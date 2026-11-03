"""MCP Client 封装 —— 让同步的 tkinter 程序能用上异步的 MCP 协议。

设计要点：

1. MCP 官方 SDK 是 asyncio 的，而 app.py 是同步 + tkinter。
   所以这里开一个**后台线程**跑独立事件循环，常驻维持与 Server 的会话。

2. stdio_client 内部用 anyio 的 task group，进入和退出必须在**同一个 task**里，
   因此整个连接生命周期包在一个长驻协程 _serve() 里，
   而不是手动 __aenter__/__aexit__（那样会报 cancel scope 跨 task 错误）。

3. 主线程通过 asyncio.run_coroutine_threadsafe() 把协程丢进后台循环执行，
   再同步等待结果——对调用方呈现为普通阻塞函数。

4. 任何失败都只返回错误字符串，绝不抛异常打断 Agent 主循环。
"""

import asyncio
import os
import shutil
import sys
import threading

from mcp import ClientSession, StdioServerParameters, stdio_client

DEFAULT_SERVER = "mcp_server.py"


class MCPClient:
    """一个最小可用的 MCP 客户端（stdio 传输）。"""

    def __init__(self, server_script=None):
        self.server_script = server_script or self._default_server_path()
        self.loop = None
        self.thread = None
        self.session = None
        self.server_name = ""
        self._tools = []          # 已转成 dict 的工具元信息
        self._error = None
        self._ready = threading.Event()
        self._shutdown = None     # asyncio.Event，在 _serve 内创建

    @staticmethod
    def _default_server_path():
        """定位 mcp_server.py 的真实位置。

        开发时：脚本所在目录，__file__ 可靠。
        打包后：__file__ 指向 PyInstaller 的临时解压目录 _MEIxxxxxx，
                该目录随进程退出即被删除，用户也不会把 mcp_server.py 放进去。
                因此必须改用 exe 所在目录，让脚本与 exe 并排放置。
        """
        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)      # 打包后：exe 所在目录
        else:
            base = os.path.dirname(os.path.abspath(__file__))   # 开发时：脚本目录
        return os.path.join(base, DEFAULT_SERVER)

    # ------------------------------------------------------------ 生命周期
    def start(self, timeout=25.0):
        """启动后台线程并连接 Server。

        Returns:
            True 表示连接成功且已拿到工具列表；
            False 表示失败（原因见 self.error），此时不影响本地工具。
        """
        if not os.path.exists(self.server_script):
            hint = (
                "（打包版请把 mcp_server.py 放到 exe 同级目录）"
                if getattr(sys, "frozen", False) else ""
            )
            self._error = f"找不到 Server 脚本: {self.server_script}{hint}"
            self._ready.set()
            return False

        self.thread = threading.Thread(
            target=self._thread_main, name="mcp-client", daemon=True
        )
        self.thread.start()

        if not self._ready.wait(timeout):
            self._error = self._error or f"连接超时（{timeout:.0f}s）"
            return False

        return self.is_connected()

    def is_connected(self):
        return self.session is not None

    @property
    def error(self):
        return self._error or "未知原因"

    def stop(self, timeout=3.0):
        """尽力优雅关闭（daemon 线程即使没关掉也会随进程退出）。"""
        try:
            if self.loop and self._shutdown:
                self.loop.call_soon_threadsafe(self._shutdown.set)
        except Exception:
            pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout)

    # ------------------------------------------------------------ 内部实现
    def _thread_main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
            self._ready.set()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    @staticmethod
    def _python_command():
        """返回用来拉起 Server 的解释器。

        开发时：直接复用当前解释器 sys.executable。
        打包成 exe 后：sys.executable 是 exe 本身（不是 Python），
        必须去找系统的 python / py，找不到就没法用 MCP。
        """
        if not getattr(sys, "frozen", False):
            return sys.executable

        for name in ("python", "python3", "py"):
            found = shutil.which(name)
            if found:
                return found
        return None

    async def _serve(self):
        """长驻协程：进入 → 初始化 → 列工具 → 一直挂着 → 退出。"""
        python_cmd = self._python_command()
        if not python_cmd:
            self._error = "打包环境中未找到 Python 解释器，MCP 不可用"
            self._ready.set()
            return

        params = StdioServerParameters(
            command=python_cmd,
            args=[self.server_script],
            cwd=os.path.dirname(self.server_script) or ".",
        )

        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    init_result = await session.initialize()
                    self.session = session
                    # server 名称在 initialize 的返回里（2.x 是 server_info）
                    info = getattr(init_result, "server_info", None)
                    self.server_name = getattr(info, "name", "") if info else ""

                    result = await session.list_tools()
                    self._tools = [
                        {
                            "name": t.name,
                            "description": t.description or "",
                            "input_schema": t.input_schema
                            or {"type": "object", "properties": {}},
                        }
                        for t in result.tools
                    ]
                    self._shutdown = asyncio.Event()
                    self._ready.set()          # 通知主线程：已就绪

                    await self._shutdown.wait()  # 挂起，保持连接存活
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
            self.session = None
            self._ready.set()

    # ------------------------------------------------------------ 对外接口
    @property
    def tool_names(self):
        return [t["name"] for t in self._tools]

    def openai_tools(self):
        """把 MCP 工具转换成 OpenAI function-calling 的 Schema 格式。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in self._tools
        ]

    @staticmethod
    def _content_text(result):
        """把 CallToolResult 里的内容块拼成纯文本。"""
        parts = []
        for item in getattr(result, "content", None) or []:
            text = getattr(item, "text", None)
            parts.append(text if text is not None else str(item))
        return "\n".join(parts) if parts else "(MCP 工具无返回内容)"

    def call_tool(self, name, arguments=None, timeout=30.0):
        """同步调用 MCP 工具。

        返回：
            成功 → 工具输出的文本
            失败 → "[错误] ..." 字符串（不抛异常）
        """
        if not self.is_connected():
            return f"[错误] MCP 未连接（{self.error}）"

        async def _do():
            return await self.session.call_tool(name, arguments or {})

        try:
            future = asyncio.run_coroutine_threadsafe(_do(), self.loop)
            result = future.result(timeout)
        except TimeoutError:
            return f"[错误] MCP 工具调用超时（{timeout:.0f}s）: {name}"
        except Exception as e:
            return f"[错误] MCP 工具调用失败: {type(e).__name__}: {e}"

        if getattr(result, "is_error", False):
            return f"[错误] MCP 工具返回错误: {self._content_text(result)}"

        return self._content_text(result)
