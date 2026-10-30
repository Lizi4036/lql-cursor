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
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

from dotenv import load_dotenv
from openai import OpenAI

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

# ---------------------------------------------------------------- 配置

MAX_STEPS = 5          # Agent 最大循环步数
PREVIEW_LIMIT = 300    # 界面上工具结果的预览长度
MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")

# 配色
BG = "#f5f6f8"
HEADER_BG = "#2c3e50"
ACCENT = "#3498db"
DANGER = "#e74c3c"
TEXT_DARK = "#2c3e50"

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
    """执行工具，任何异常都转成字符串返回，绝不抛给调用方。"""
    func = TOOL_MAP.get(name)
    if func is None:
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


# ---------------------------------------------------------------- Agent 大脑

def run_agent(user_input, emit, stop_event, max_steps=MAX_STEPS):
    """Agent 主循环。

    Args:
        user_input: 用户输入
        emit: 回调函数，用于把日志输出到界面
        stop_event: threading.Event，置位后循环会在下一步前退出
        max_steps: 最大步数
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        emit("[错误] 未读取到 OPENAI_API_KEY，请检查 .env 文件是否配置正确。")
        return None

    client = OpenAI(api_key=api_key, base_url=os.getenv("OPENAI_BASE_URL"))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

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

        self._build_ui()
        self.root.after(100, self._poll_queue)

    # ---- 界面搭建 ----

    def _build_ui(self):
        self.root.title("极简桌面助手 - AI Agent")
        self.root.geometry("780x580")
        self.root.minsize(560, 420)
        self.root.configure(bg=BG)

        # 标题栏
        header = tk.Frame(self.root, bg=HEADER_BG, height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header, text="极简桌面助手", fg="white", bg=HEADER_BG,
            font=("Microsoft YaHei", 15, "bold"),
        ).pack(side="left", padx=16)
        tk.Label(
            header, text=f"模型: {MODEL} ｜ 工具: read/write/edit/bash",
            fg="#aebac9", bg=HEADER_BG, font=("Microsoft YaHei", 9),
        ).pack(side="right", padx=16)

        # 聊天记录区
        chat_frame = tk.Frame(self.root, bg=BG)
        chat_frame.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        self.chat = ScrolledText(
            chat_frame, wrap="word", font=("Consolas", 10),
            bg="white", fg=TEXT_DARK, relief="solid", bd=1,
            padx=10, pady=8, spacing1=2, spacing3=2,
        )
        self.chat.pack(fill="both", expand=True)
        self.chat.configure(state="disabled")

        self.chat.tag_config("user", foreground="#1a73e8",
                             font=("Microsoft YaHei", 10, "bold"))
        self.chat.tag_config("tool", foreground="#b8860b")
        self.chat.tag_config("system", foreground="#999999")

        self._append(
            "你好，我是极简桌面助手。我可以读文件、改文件、跑命令。\n"
            "直接输入你想让我做的事，按回车或点【发送】开始。",
            "system",
        )

        # 输入区
        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=12, pady=(0, 10))

        self.entry = tk.Entry(
            bottom, font=("Microsoft YaHei", 10), relief="solid", bd=1,
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.entry.bind("<Return>", lambda _e: self.on_send())
        self.entry.focus_set()

        self.send_btn = tk.Button(
            bottom, text="发送", command=self.on_send,
            bg=ACCENT, fg="white", activebackground="#2980b9",
            activeforeground="white", relief="flat",
            font=("Microsoft YaHei", 10, "bold"), width=8, padx=10,
        )
        self.send_btn.pack(side="left", padx=(8, 0), ipady=5)

        self.stop_btn = tk.Button(
            bottom, text="■ 中断", command=self.on_stop,
            bg=DANGER, fg="white", activebackground="#c0392b",
            activeforeground="white", relief="flat",
            font=("Microsoft YaHei", 10, "bold"), width=8, padx=10,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(8, 0), ipady=5)

        # 状态栏
        self.status = tk.Label(
            self.root, text="就绪", anchor="w", bg=BG, fg="#7f8c8d",
            font=("Microsoft YaHei", 9),
        )
        self.status.pack(fill="x", padx=14, pady=(0, 10))

    # ---- 输出 ----

    def _append(self, text, tag=None):
        self.chat.configure(state="normal")
        self.chat.insert("end", text + "\n", tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def emit(self, text):
        """供工作线程调用：线程安全地投递到界面。"""
        self.msg_queue.put(text)

    def _poll_queue(self):
        """主线程定时取队列，避免跨线程直接操作 tkinter。"""
        try:
            while True:
                item = self.msg_queue.get_nowait()
                if item == "__DONE__":
                    self._on_finished()
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

    def _work(self, text):
        try:
            run_agent(text, self.emit, self.stop_event)
        except Exception as e:  # 兜底，保证界面一定能恢复
            self.emit(f"[严重错误] {type(e).__name__}: {e}")
        finally:
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
    AgentApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
