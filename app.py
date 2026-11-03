"""极简桌面助手 - Agent 主程序

后端：调用大模型 + read/write/edit/bash 四个工具组成 Agent 循环
前端：tkinter 桌面窗口，带实时日志与中断按钮

运行：  py app.py
"""

import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from dotenv import load_dotenv
from openai import OpenAI

try:                    # 主题库缺失时自动降级为默认样式，不影响任何功能
    import sv_ttk
except ImportError:
    sv_ttk = None

try:                    # MCP 依赖缺失时仅关闭 MCP 功能，其余照常运行
    import mcp_client as mcp_module
except ImportError:
    mcp_module = None

# 全局 MCP 客户端；None 表示未启用或连接失败（此时只有本地 4 个工具）
MCP_CLIENT = None

from tools import bash, edit, read, write


def base_dir():
    """返回 .env 应该所在的目录。

    - 开发时：app.py 所在目录
    - 打包成 exe 后：exe 所在目录（不是临时解压目录 _MEIPASS）
      这样把 .env 放在 exe 旁边即可，无论从哪里启动都能读到。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# 用绝对路径加载，避免依赖"当前工作目录"
load_dotenv(os.path.join(base_dir(), ".env"))


def resource_path(name):
    """查找随程序附带的资源文件（如 icon.ico）。

    依次尝试：exe / 脚本所在目录 → PyInstaller 临时解压目录 _MEIPASS。
    找不到返回 None，调用方需自行容错。
    """
    for folder in (base_dir(), getattr(sys, "_MEIPASS", "")):
        if folder:
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return path
    return None

# ---------------------------------------------------------------- 配置

MAX_STEPS = 5          # Agent 最大循环步数
PREVIEW_LIMIT = 300    # 界面上工具结果的预览长度
HISTORY_FILE = "history.json"   # 会话历史文件名（存于程序同目录）
MAX_HISTORY = 50       # 历史消息上限，超出时保留 system + 最近 N 条
MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")

# 配色：Sun Valley 深色（Win11 风格）
BG = "#1c1c1c"          # 窗口底色
SURFACE = "#272727"     # 聊天区底色
BORDER = "#3a3a3a"      # 分割线 / 边框
FG = "#e8e8e8"          # 主文字（深色背景上用浅色替代黑色）
FG_MUTED = "#9a9a9a"    # 次要文字
ACCENT = "#4cc2ff"      # 强调蓝
DANGER = "#c42b1c"      # 危险红

UI_FONT = "Microsoft YaHei UI"   # 界面字体
CODE_FONT = "Consolas"           # 聊天区字体（代码较多）

SYSTEM_PROMPT = """你是运行在用户本机上的桌面助手 Agent，可以使用四个工具：
- read(path, limit)：读取文件内容
- write(path, content)：新建或整文件写入
- edit(path, old_str, new_str)：精确替换文件中的一段文本
- bash(command, timeout)：执行终端命令

规则：
1. 修改已有文件时**必须**用 edit 精确替换，禁止用 write 整文件覆盖。
2. 只有新建文件、或确实需要整体重写时才用 write。
3. 工具结果会反馈给你，据此决定下一步。
4. 最多 5 步内完成任务；做不到就说明原因，不要编造结果。"""

# ---------------------------------------------------------------- 工具 Schema

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "读取文件内容，超过长度会自动截断。.env 相关文件会被拒绝读取。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径"},
                    "limit": {
                        "type": "integer",
                        "description": "最大读取字符数，默认 2000",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "写入文件，父目录会自动创建。适合新建文件，或确认需要整体重写时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要写入的文件路径"},
                    "content": {"type": "string", "description": "要写入的完整内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit",
            "description": "精确替换文件中的一段文本。old_str 必须在文件中恰好出现 1 次，否则会失败。修改已有文件时优先使用本工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要修改的文件路径"},
                    "old_str": {"type": "string", "description": "要被替换的原文本，必须唯一"},
                    "new_str": {"type": "string", "description": "替换后的新文本"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "执行终端命令并返回退出码、stdout、stderr。高危破坏性命令会被拦截。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {
                        "type": "integer",
                        "description": "超时秒数，默认 30",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

TOOL_MAP = {"read": read, "write": write, "edit": edit, "bash": bash}


# ---------------------------------------------------------------- 工具执行

def execute_tool(name, arguments):
    """执行工具，任何异常都转成字符串返回，绝不抛给调用方。

    本地工具优先；本地没有的，转发给 MCP Server（若已连接）。
    """
    func = TOOL_MAP.get(name)
    if func is None:
        # 不是本地工具，尝试交给 MCP
        if MCP_CLIENT is not None and name in MCP_CLIENT.tool_names:
            return MCP_CLIENT.call_tool(name, arguments)
        return f"[错误] 未知工具: {name}"
    try:
        return func(**arguments)
    except TypeError as e:
        return f"[错误] 工具参数不正确: {e}"
    except Exception as e:  # 兜底，防止模型乱传参数搞崩主循环
        return f"[错误] 工具执行异常: {type(e).__name__}: {e}"


def preview(text, limit=PREVIEW_LIMIT):
    """界面预览用的截断，完整结果仍会送回模型。"""
    text = str(text)
    if len(text) > limit:
        return text[:limit] + f"\n   ...(结果过长，共 {len(text)} 字符，已截断显示)"
    return text


# ---------------------------------------------------------------- 会话持久化

def history_path():
    """历史文件路径：始终与程序本体同目录（开发时为脚本目录，打包后为 exe 目录）。"""
    return os.path.join(base_dir(), HISTORY_FILE)


def save_history(messages):
    """把会话历史写入 history.json。

    序列化要点：
      - ensure_ascii=False 保证中文正常存储
      - default=str 兜底，任何 json 无法直接序列化的对象
        （如 SDK 返回的复杂对象）都退化为字符串，避免写入时抛异常
    """
    try:
        with open(history_path(), "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2, default=str)
        return True
    except Exception:
        return False


def load_history():
    """读取并校验会话历史；任何异常都退化为空列表，绝不让程序启动失败。

    兼容策略（逐层降级）：
      文件不存在 → []
      JSON 解析失败 / 编码错误 / IO 错误 → []
      顶层不是 list → []
      元素不是 dict、role 非法 → 跳过该条
      content 不是字符串 → 转字符串（None 转空串）
    """
    path = history_path()
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    valid_roles = ("system", "user", "assistant", "tool")
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in valid_roles:
            continue

        content = item.get("content")
        if isinstance(content, str):
            text = content
        elif content is None:
            text = ""
        else:
            text = str(content)

        msg = {"role": role, "content": text}
        if role == "assistant" and isinstance(item.get("tool_calls"), list):
            msg["tool_calls"] = item["tool_calls"]
        if role == "tool" and item.get("tool_call_id"):
            msg["tool_call_id"] = item["tool_call_id"]
        result.append(msg)

    return result


def trim_history(messages, keep=MAX_HISTORY):
    """就地裁剪历史，避免无限增长撑爆模型上下文窗口。

    保留 system 消息 + 最近的 keep 条对话消息。
    用切片赋值就地修改，保证调用方持有的引用同步生效。
    """
    if len(messages) <= keep:
        return
    system = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    messages[:] = system + rest[-(keep - len(system)):]


# ---------------------------------------------------------------- Agent 大脑

def run_agent(user_input, emit, stop_event, messages=None, max_steps=MAX_STEPS):
    """Agent 主循环。

    Args:
        user_input: 用户输入
        emit: 回调函数，用于把日志输出到界面
        stop_event: threading.Event，置位后循环会在下一步前退出
        messages: 会话历史，会**就地追加**本轮消息，调用方据此持久化；
                  传 None 时内部新建
        max_steps: 最大步数
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        emit("[错误] 未读取到 OPENAI_API_KEY，请检查 .env 文件是否配置正确。")
        return None

    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))

    if messages is None:
        messages = []

    # 保证 system prompt 存在且位于最前（兼容历史文件里没有 system 的情况）
    if not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    # 历史过长时裁剪，避免超出模型上下文窗口
    trim_history(messages)

    messages.append({"role": "user", "content": user_input})

    for step in range(1, max_steps + 1):
        # 中断检查点 1：每步开始前
        if stop_event.is_set():
            emit("\n[已中断] 用户停止了本次任务。")
            return None

        emit(f"\n—— 第 {step}/{max_steps} 步 ——")

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except Exception as e:
            emit(f"[错误] 调用大模型失败: {type(e).__name__}: {e}")
            return None

        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        # 没有工具调用 → 这就是最终答案
        if not tool_calls:
            answer = msg.content or "(模型未返回内容)"
            emit(f"\n{answer}")
            return answer

        # 把 assistant 的 tool_calls 消息放回对话（转成纯 dict，兼容性最好）
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.function.name,
                            "arguments": c.function.arguments,
                        },
                    }
                    for c in tool_calls
                ],
            }
        )

        for call in tool_calls:
            # 中断检查点 2：每个工具执行前
            if stop_event.is_set():
                emit("\n[已中断] 用户停止了本次任务。")
                return None

            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
                emit(f"[警告] 工具参数解析失败: {call.function.arguments}")

            emit(f"🔧 正在调用工具：{name}")
            result = execute_tool(name, arguments)
            emit(f"   结果: {preview(result)}")

            # edit 成功后追加 Diff 预览：红=删除，绿=新增。
            # 由于 edit 保证只替换一处，old_str/new_str 就是精确的删/增内容，
            # 无需读文件对比，也不会把 diff 发给模型（只走界面显示）。
            if name == "edit" and str(result).startswith("[成功]"):
                old = str(arguments.get("old_str", ""))
                new = str(arguments.get("new_str", ""))
                emit("   ── 修改预览 ──", "system")
                for line in (old.splitlines() or [""]):
                    emit("   - " + line, "del")
                for line in (new.splitlines() or [""]):
                    emit("   + " + line, "add")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                }
            )

    emit("\n[结束] 已达到最大步数，任务可能未完成。")
    return None


# ---------------------------------------------------------------- Agent 脸面

class AgentApp:
    def __init__(self, root):
        self.root = root
        self.msg_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.messages = load_history()

        self._build_ui()
        self._render_history()
        self.root.after(100, self._poll_queue)

        # 关窗时清理 MCP 子进程
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # 后台连接 MCP Server（不阻塞界面）
        self.init_mcp()

    # ---- 界面搭建 ----

    def _build_ui(self):
        self.root.title("极简桌面助手 - AI Agent")
        self._set_icon()
        self.root.geometry("840x640")
        self.root.minsize(620, 480)
        self.root.configure(bg=BG)

        # ---- 标题栏：用 ttk 容器，背景跟随主题，避免色差断层
        header = ttk.Frame(self.root, padding=(20, 14, 20, 10))
        header.pack(fill="x")
        ttk.Label(
            header, text="极简桌面助手",
            font=(UI_FONT, 16, "bold"),
        ).pack(side="left")
        ttk.Label(
            header, text=f"{MODEL}  ｜  read / write / edit / bash",
            font=(UI_FONT, 9), foreground=FG_MUTED,
        ).pack(side="right")

        # ---- 聊天区：ttk 没有 ScrolledText，用 Text + ttk.Scrollbar 组合
        chat_frame = ttk.Frame(self.root, padding=(16, 2, 16, 0))
        chat_frame.pack(fill="both", expand=True)

        self.chat = tk.Text(
            chat_frame, wrap="word", font=(CODE_FONT, 11),
            bg=SURFACE, fg=FG, insertbackground=FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=BORDER,
            padx=16, pady=14, spacing1=4, spacing3=4,
            selectbackground="#3a5a78", selectforeground="#ffffff",
        )
        scroll = ttk.Scrollbar(chat_frame, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.configure(state="disabled")

        # ---- 日志配色（深色背景适配，保持语义：回复浅白 / 工具蓝 / 报错红）
        self.chat.tag_config("ai", foreground=FG,
                             font=(UI_FONT, 11))                  # AI 普通回复
        self.chat.tag_config("tool", foreground="#4cc2ff",
                             font=(UI_FONT, 11, "bold"))          # 🔧 工具调用
        self.chat.tag_config("error", foreground="#ff6b6b",
                             font=(CODE_FONT, 11, "bold"))        # 报错信息
        self.chat.tag_config("user", foreground="#7ab8ff",
                             font=(UI_FONT, 11, "bold"))          # 用户输入
        self.chat.tag_config("result", foreground=FG_MUTED)       # 工具返回结果
        self.chat.tag_config("system", foreground="#7a7a7a")      # 系统提示
        self.chat.tag_config("del", foreground="#ff6b6b")         # Diff：删除行（红）
        self.chat.tag_config("add", foreground="#6dd47e")         # Diff：新增行（绿）

        self._append(
            "你好，我是极简桌面助手。我可以读文件、改文件、跑命令。\n"
            "直接输入你想让我做的事，按回车或点【发送】开始。",
            "system",
        )

        # ---- 输入区：加大间距，避免拥挤
        bottom = ttk.Frame(self.root, padding=(16, 14, 16, 8))
        bottom.pack(fill="x")

        self.entry = ttk.Entry(bottom, font=(UI_FONT, 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", lambda _e: self.on_send())
        self.entry.focus_set()

        self.send_btn = ttk.Button(
            bottom, text="发送", command=self.on_send,
            style="Accent.TButton", width=9,
        )
        self.send_btn.pack(side="left", padx=(12, 0))

        # 中断按钮：sv-ttk 的样式基于图片，无法直接改红，用 tk.Button 保证红色语义
        self.stop_btn = tk.Button(
            bottom, text="中断", command=self.on_stop,
            bg=DANGER, fg="white", activebackground="#a02218",
            activeforeground="white", relief="flat", bd=0,
            font=(UI_FONT, 10, "bold"), width=8, cursor="hand2",
            disabledforeground="#7a7a7a", state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(10, 0), ipady=6)

        # ---- 状态栏：左状态、右清空历史
        status_bar = ttk.Frame(self.root, padding=(18, 6, 18, 14))
        status_bar.pack(fill="x")
        self.status = ttk.Label(
            status_bar, text="就绪", foreground=FG_MUTED,
            font=(UI_FONT, 9),
        )
        self.status.pack(side="left")
        self.clear_btn = ttk.Button(
            status_bar, text="清空历史",
            command=self.on_clear_history, width=10,
        )
        self.clear_btn.pack(side="right")

    def _render_history(self):
        """把已保存的历史渲染到界面（system 消息不展示）。"""
        shown = [m for m in self.messages if m.get("role") != "system"]
        if not shown:
            return

        self._append(f"—— 已加载 {len(shown)} 条历史记录 ——", "system")
        for m in shown:
            role = m["role"]
            content = m.get("content") or ""
            if role == "user":
                self._append(f"你：{content}", "user")
            elif role == "assistant":
                if content:
                    self._append(content, "ai")
                for call in m.get("tool_calls") or []:
                    fn = (call.get("function") or {}).get("name", "?")
                    self._append(f"🔧 正在调用工具：{fn}", "tool")
            elif role == "tool":
                self._append(f"   结果: {preview(content)}", "result")
        self._append("—— 以上为历史记录 ——", "system")

    def init_mcp(self):
        """后台连接 MCP Server，成功后把它的工具追加进 TOOLS。

        全程非阻塞：连接在后台线程进行，界面不受影响；
        即使失败，也只是少一个加分工具，本地 4 个工具照常工作。
        """
        if mcp_module is None:
            self.emit(
                "[MCP] 未检测到 mcp 库，已跳过（安装: py -m pip install mcp）",
                "system",
            )
            return

        self.emit("⏳ 正在连接 MCP Server ...", "system")

        def _run():
            global MCP_CLIENT
            client = mcp_module.MCPClient()
            if client.start():
                MCP_CLIENT = client
                TOOLS.extend(client.openai_tools())
                self.emit(
                    f"[MCP] 已连接「{client.server_name}」，"
                    f"新增工具: {', '.join(client.tool_names)}",
                    "tool",
                )
                self.emit(f"[MCP] 当前共 {len(TOOLS)} 个工具可用", "system")
            else:
                self.emit(
                    f"[MCP] 连接失败：{client.error}（继续使用本地工具）", "error"
                )

        threading.Thread(target=_run, name="mcp-init", daemon=True).start()

    def _on_close(self):
        """关闭窗口前先收掉 MCP 子进程，避免进程残留。"""
        if MCP_CLIENT is not None:
            try:
                MCP_CLIENT.stop(timeout=2)
            except Exception:
                pass
        self.root.destroy()

    def _set_icon(self):
        """设置窗口图标；图标缺失或加载失败时静默跳过，不影响启动。"""
        try:
            path = resource_path("icon.ico")
            if path:
                self.root.iconbitmap(path)
        except Exception:
            pass

    # ---- 输出 ----

    def _append(self, text, tag=None):
        if tag is None:
            tag = self._guess_tag(text)
        self.chat.configure(state="normal")
        self.chat.insert("end", text + "\n", tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    @staticmethod
    def _guess_tag(text):
        """按日志内容自动着色：🔧 蓝、报错红、AI 回复黑。"""
        t = text.strip()
        if t.startswith("🔧"):
            return "tool"
        if "[错误]" in t or "[严重错误]" in t or "[拒绝]" in t:
            return "error"
        if t.startswith("你："):
            return "user"
        if t.startswith("结果:"):
            return "result"
        if t.startswith("——") or t.startswith("["):
            return "system"
        return "ai"

    def emit(self, text, tag=None):
        """供工作线程调用：线程安全地投递到界面。

        tag 为 None 时按内容自动着色；
        显式指定 tag（如 "del" / "add"）时直接使用，供 Diff 预览等场景。
        队列统一放 (text, tag) 元组，_poll_queue 负责解包。
        """
        self.msg_queue.put((text, tag))

    def _poll_queue(self):
        """主线程定时取队列，避免跨线程直接操作 tkinter。"""
        try:
            while True:
                item = self.msg_queue.get_nowait()
                if item == "__DONE__":
                    self._on_finished()
                elif isinstance(item, tuple):
                    self._append(item[0], item[1])
                else:
                    self._append(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    # ---- 交互 ----

    def on_send(self):
        text = self.entry.get().strip()
        if not text or self.worker and self.worker.is_alive():
            return

        self.entry.delete(0, "end")
        self._append(f"你：{text}", "user")

        self.stop_event.clear()
        self.send_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.configure(text="运行中...")

        self.worker = threading.Thread(
            target=self._work, args=(text,), daemon=True
        )
        self.worker.start()

    def on_stop(self):
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status.configure(text="正在中断，等待当前步骤结束...")
            self._append("[用户] 已请求中断。", "system")
        self.stop_btn.configure(state="disabled")

    def on_clear_history(self):
        """清空界面显示并删除本地 history.json。"""
        # 任务运行中禁止清空：工作线程正持有 self.messages 并会在
        # 结束时把它写回文件，此时清空会被旧数据覆盖，还会引发数据竞争
        if self.worker and self.worker.is_alive():
            self._append("[系统] 任务正在运行，请先点【中断】再清空历史。", "system")
            return

        if not messagebox.askyesno(
            "清空历史",
            "将删除全部聊天记录（含本地 history.json 文件），确定吗？\n此操作不可恢复。",
        ):
            return

        # 1) 清空内存中的会话
        self.messages = []
        # 2) 删除本地文件（不存在时静默跳过）
        try:
            if os.path.exists(history_path()):
                os.remove(history_path())
        except OSError as e:
            self._append(f"[警告] 删除 history.json 失败: {e}", "system")
            return
        # 3) 清空聊天框显示
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")

        self._append("历史已清空。有什么新任务，随时告诉我。", "system")
        self.status.configure(text="已清空历史")

    def _work(self, text):
        try:
            run_agent(text, self.emit, self.stop_event, self.messages)
        except Exception as e:  # 兜底，保证界面一定能恢复
            self.emit(f"[严重错误] {type(e).__name__}: {e}")
        finally:
            # 无论正常结束、被中断还是异常，都把会话落盘
            ok = save_history(self.messages)
            self.emit(
                "[系统] 会话已保存到 history.json"
                if ok
                else "[警告] 会话保存失败"
            )
            self.msg_queue.put("__DONE__")

    def _on_finished(self):
        self.send_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.status.configure(text="就绪")


# ---------------------------------------------------------------- 入口

def main():
    if not os.getenv("OPENAI_API_KEY"):
        messagebox.showwarning(
            "缺少 API Key",
            "未在 .env 中读取到 OPENAI_API_KEY。\n"
            "请在 .env 里配置好 Key 后重启程序。",
        )

    root = tk.Tk()
    # Sun Valley 深色主题（Win11 风格）；sv-ttk 未安装时自动降级
    if sv_ttk is not None:
        sv_ttk.set_theme("dark", root)

    AgentApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
