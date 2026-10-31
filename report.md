# 极简桌面助手 —— 项目设计报告

| 项目 | 内容 |
|:---|:---|
| **项目名称** | 极简桌面助手（AI Agent 桌面应用） |
| **开发语言** | Python 3.12.5 |
| **图形界面** | tkinter（标准库） |
| **核心依赖** | openai 3.41.0、python-dotenv 1.2.4 |
| **打包工具** | PyInstaller（单文件模式） |
| **代码规模** | 主程序 + 工具模块 + 测试脚本，共 3 个 Python 文件 |
| **测试用例** | 25 项，全部通过 |

---

## 一、项目背景与目标

### 1.1 项目背景

大模型具备强大的理解与推理能力，但它本身只能"说话"，不能"动手"。
实际开发中大量工作是机械性的：读代码、改配置、跑命令、看结果。
如果能让模型调用本地工具，就能把"理解"转化为"执行"。

然而让 AI 操作本地文件系统存在明显风险：

- AI 图省事用整文件覆盖，可能冲掉几百行已有代码
- AI 执行终端命令，可能误删系统文件
- AI 读取配置文件，可能把密钥送进对话记录造成泄露

本项目正是在"赋予能力"与"约束风险"之间寻求平衡的一次实践。

### 1.2 项目目标

1. 实现一个能调用本地工具的 AI Agent，支持读文件、写文件、精确修改、执行命令四类操作
2. 提供图形界面，把 Agent 的每一步思考与操作**实时、可读地展示**给用户
3. 建立安全边界：密钥不入库、危险命令不执行、文件修改不覆盖
4. 保证界面不卡死，且用户随时可以中断 Agent

### 1.3 与同类产品的差异

| 对比项 | 普通聊天机器人 | 本助手 |
|:---|:---|:---|
| 能否操作文件 | 不能，只给代码让你自己复制 | 能，直接读写本机文件 |
| 操作是否可见 | 不可见 | 每步工具调用与结果实时打印 |
| 能否中断 | 不能 | 红色按钮随时中断 |
| 文件修改方式 | 通常整文件重写 | 强制精确替换，匹配不唯一即拒绝 |
| 密钥处理 | 需粘贴到对话框 | 只存本地 `.env`，代码用 `os.getenv()` |

---

## 二、需求分析

### 2.1 功能性需求

| 编号 | 需求 | 实现情况 |
|:---:|:---|:---|
| F1 | 读取文件内容，超长自动截断 | ✅ `read(path, limit=2000)` |
| F2 | 写入文件，父目录自动创建 | ✅ `write(path, content)` |
| F3 | 精确替换文件中的一段文本 | ✅ `edit(path, old_str, new_str)` |
| F4 | 执行终端命令并捕获输出 | ✅ `bash(command, timeout=30)` |
| F5 | 调用大模型，根据返回决定下一步 | ✅ `run_agent()` 循环 |
| F6 | 图形界面展示对话与工具日志 | ✅ tkinter + ScrolledText |
| F7 | 随时中断 Agent 执行 | ✅ `threading.Event` + 红色按钮 |
| F8 | 从 `.env` 读取配置 | ✅ `python-dotenv` |

### 2.2 非功能性需求

| 类型 | 要求 | 实现方式 |
|:---|:---|:---|
| **安全性** | 密钥不得进入代码库 | `.gitignore` 首行即 `.env`；代码只用 `os.getenv()` |
| **安全性** | 不得执行破坏性命令 | 15 条正则黑名单，命中即拒 |
| **安全性** | 不得泄露配置文件内容 | `read` 拒绝所有 `.env` 前缀文件 |
| **可靠性** | 单次操作不得卡死界面 | 工具执行放在工作线程，超时 30 秒强制返回 |
| **可靠性** | 修改文件不得造成大面积损毁 | `edit` 唯一性校验，禁止整文件覆盖 |
| **易用性** | 日志可区分类型 | AI 回复黑色、工具调用蓝色、报错红色 |
| **可移植性** | 能分发为独立可执行程序 | PyInstaller 单文件打包 |

### 2.3 约束条件

- 大模型接口需兼容 OpenAI 格式（本项目使用 DeepSeek）
- Agent 自主步数上限 5 步，避免无限循环消耗 token
- 界面使用标准库 tkinter，不引入重量级 GUI 框架

---

## 三、系统设计

### 3.1 总体架构

```
┌─────────────────────────────────────────────┐
│  表现层  app.py / AgentApp                   │
│  tkinter 窗口：标题栏 / 聊天区 / 输入区      │
│  线程安全队列 Queue + after() 轮询           │
└──────────────────┬──────────────────────────┘
                   │ 工作线程调用
┌──────────────────▼──────────────────────────┐
│  控制层  app.py / run_agent()                │
│  消息组装 → 调用模型 → 解析 tool_calls       │
│  → 分发工具 → 结果回灌 → 循环（≤5 步）       │
└──────────────────┬──────────────────────────┘
                   │ 函数调用
┌──────────────────▼──────────────────────────┐
│  工具层  tools.py                            │
│  read / write / edit / bash                  │
│  内置：截断、建目录、唯一性校验、黑名单       │
└─────────────────────────────────────────────┘
```

### 3.2 模块划分

| 文件 | 职责 |
|:---|:---|
| `app.py` | 工具 Schema 定义、Agent 主循环、GUI 界面、线程与中断控制 |
| `tools.py` | 四个工具的底层实现与各自的异常处理 |
| `test_tools.py` | 25 项单元测试，覆盖正常路径与各类异常分支 |
| `requirements.txt` | 依赖清单 |
| `.env` | 本地密钥（不入库） |
| `.gitignore` | 排除 `.env`、`__pycache__/`、`dist/`、`build/`、`demo/` |

### 3.3 工具接口设计

四个工具均以**字符串返回值**作为统一契约：成功返回 `[成功] ...`，
失败返回 `[错误] ...`。这样做的好处是——工具的执行结果可以直接作为
`role: tool` 的消息回灌给大模型，异常也不会中断 Agent 循环。

| 工具 | 参数 | 返回值 |
|:---|:---|:---|
| `read` | `path`, `limit=2000` | 文件内容；超限截断并提示 |
| `write` | `path`, `content` | `[成功] 已写入 ...（N 字符）` |
| `edit` | `path`, `old_str`, `new_str` | `[成功] 已替换 ...` 或唯一性错误 |
| `bash` | `command`, `timeout=30` | `[退出码] N` + stdout + stderr |

### 3.4 Agent 循环流程

```
输入用户指令
  ↓
[循环中，最多 5 步]
  ├─ 检查中断信号 ── 已中断 → 退出
  ├─ 调用大模型（携带 4 个工具的 JSON Schema）
  ├─ 无 tool_calls → 输出最终文本，结束
  └─ 有 tool_calls →
       对每个调用：
         ├─ 检查中断信号 ── 已中断 → 退出
         ├─ 打印 🔧 正在调用工具：<name>
         ├─ 执行工具，打印结果（截断显示）
         └─ 把结果追加为 role=tool 消息
  ↓
回到循环开头
```

### 3.5 线程模型

界面主线程与 Agent 工作线程严格分离，通过 `queue.Queue` 单向通信：

- **工作线程**：执行 `run_agent()`，只调用 `emit()` 往队列放字符串
- **主线程**：`root.after(100, ...)` 每 100 毫秒轮询队列，取出后写入文本框

这样避免了跨线程直接操作 tkinter 控件（会导致随机崩溃），
同时保证界面始终响应，用户点击中断、拖动窗口都不受影响。

### 3.6 中断机制

使用 `threading.Event` 作为协作式中断信号，在两个位置检查：

1. 每一轮循环开始前
2. 每个工具执行之前

用户点击红色按钮 → `stop_event.set()` → 循环在下一个检查点退出 →
队列放入 `__DONE__` → 主线程恢复"发送"按钮。

---

## 四、核心实现

### 4.1 `read`：截断与敏感文件拦截

```python
if os.path.basename(path).startswith(".env"):
    return "[拒绝] 出于安全考虑，不读取 .env 相关文件（含 .env.local 等变体）"

try:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
except FileNotFoundError:
    return f"[错误] 文件不存在: {path}"
```

设计要点：

- 用 `startswith(".env")` 而非等值判断，堵住 `.env.local`、`.env.production` 等变体
- 用 `os.path.basename()` 取文件名，跨目录路径（如 `config/.env.local`）同样拦得住
- `errors="replace"` 保证遇到 GBK 老文件不会抛异常
- 超长截断后附带 `...[已截断] 文件共 N 字符` 提示，让模型知道内容不完整

### 4.2 `write`：父目录自动创建

```python
parent = os.path.dirname(os.path.abspath(path))
os.makedirs(parent, exist_ok=True)
```

先用 `abspath()` 取绝对路径，保证传入纯文件名（如 `a.txt`）时
`dirname` 也不会返回空字符串；`exist_ok=True` 让目录已存在时不报错。

### 4.3 `edit`：唯一性校验（本项目最关键的设计）

```python
count = content.count(old_str)
if count != 1:
    return (
        f"[错误] old_str 出现次数不唯一，请提供更多上下文。"
        f"（在 {path} 中找到 {count} 处匹配）"
    )

new_content = content.replace(old_str, new_str, 1)
```

**设计动机**：AI 为了省事，天然倾向于用整文件重写完成修改任务。
一旦它把记忆中的旧版本内容写回文件，未被它"记住"的部分就会被永久抹除。

**约束方式**：

- `count != 1` 一次判断同时挡掉 0 次（没匹配上）和 ≥2 次（上下文不足）
- 只调用 `replace(old, new, 1)`，代码中不存在任何整文件覆盖路径
- 额外拦截空 `old_str`（否则 `count("")` 会返回 `len+1` 这种无意义值）

**效果**：把"AI 可能毁掉文件"从**概率问题**变成了**机制上不可能**。

### 4.4 `bash`：超时保护与高危拦截

```python
BLOCKED_PATTERNS = [
    r"rm\s+-[rR][fF]\s+(/|~|\*|\$HOME)",   # rm -rf /
    r":\(\)\s*\{",                          # Fork Bomb
    r"\bmkfs",                              # 格式化
    r"\bdd\s+if=.+\s+of=/dev/",             # 写裸盘
    r"\bformat\s+[a-zA-Z]:",                # format c:
    r"\bdel\s+/[fFsS]",                     # del /f /s
    r"\brd\s+/[sS]",                        # rd /s
    r"\bdiskpart\b",
    ...
]
```

拦截发生在 `subprocess.run()` 之前：

```python
hit = _is_dangerous(command)
if hit:
    return f"[拒绝] 检测到高危命令，出于安全考虑已拦截（命中规则: {hit}）"
```

**防误伤设计**：使用正则而非裸关键字。若直接用 `"format"` 做子串匹配，
`git log --format=%H`、`pytest --format=json` 这类合法命令都会被误杀。
改为 `\bformat\s+[a-zA-Z]:` 后，只有真正格式化磁盘的写法才会命中。

**超时处理**：

```python
result = subprocess.run(command, shell=True, capture_output=True, timeout=timeout)
...
except subprocess.TimeoutExpired:
    return f"[错误] 命令超时（超过 {timeout} 秒）: {command}"
```

**输出解码**：`_decode()` 依次尝试 UTF-8 → GBK → `replace` 兜底，
兼顾 Linux 工具与 Windows 中文终端（cmd 默认 GBK）的输出。

### 4.5 Agent 主循环

```python
for step in range(1, max_steps + 1):
    if stop_event.is_set():
        emit("\n[已中断] 用户停止了本次任务。")
        return None

    response = client.chat.completions.create(
        model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto",
    )

    msg = response.choices[0].message
    if not getattr(msg, "tool_calls", None):
        emit(f"\n{msg.content}")
        return msg.content

    messages.append({...含 tool_calls 的 assistant 消息...})

    for call in msg.tool_calls:
        emit(f"🔧 正在调用工具：{name}")
        result = execute_tool(name, arguments)
        emit(f"   结果: {preview(result)}")
        messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)})
```

关键点：工具结果**完整送回模型**（保证判断准确），
界面上才截断到 300 字符显示（保证可读），两者分开处理。

### 4.6 界面与日志着色

```python
@staticmethod
def _guess_tag(text):
    t = text.strip()
    if t.startswith("🔧"):
        return "tool"                      # 蓝色
    if "[错误]" in t or "[严重错误]" in t or "[拒绝]" in t:
        return "error"                     # 红色
    if t.startswith("你："):
        return "user"
    if t.startswith("结果:"):
        return "result"
    if t.startswith("——") or t.startswith("["):
        return "system"
    return "ai"                            # 黑色
```

日志到达时自动判断类型并着色，无需改动 `emit()` 接口，实现展示层与逻辑层解耦。

---

## 五、测试验证

### 5.1 测试环境

| 项目 | 值 |
|:---|:---|
| 操作系统 | Windows（PowerShell） |
| Python | 3.12.5（64-bit） |
| 测试命令 | `py test_tools.py` |

### 5.2 测试用例与结果

**`edit` 精确替换（6 项）**

| 用例 | 预期 | 结果 |
|:---|:---|:---:|
| 唯一匹配替换 | 替换成功，内容正确 | ✅ |
| 0 次匹配 | 报错，文件**不被改动** | ✅ |
| 3 次匹配 | 报错，文件**不被改动** | ✅ |
| 空 `old_str` | 拒绝 | ✅ |
| 文件不存在 | 返回错误提示 | ✅ |
| 只改目标行 | 其余内容完整保留 | ✅ |

**`bash` 命令执行（5 项）**

| 用例 | 预期 | 结果 |
|:---|:---|:---:|
| `echo hello_bash` | 退出码 0，输出正确 | ✅ |
| `sys.exit(3)` | 捕获退出码 3 与 stderr | ✅ |
| sleep 5 / timeout 2 | 返回超时，不卡死 | ✅ |
| 5000 字符输出 | 截断至 2000 并提示 | ✅ |
| 空命令 | 拒绝 | ✅ |

**`bash` 高危拦截（19 项）**

| 类别 | 用例 | 结果 |
|:---|:---|:---:|
| Linux | `rm -rf /`、`rm -rf /home/user`、`rm -rf ~` | ✅ 全部拦截 |
| Linux | `:(){ :\|:& };`（Fork Bomb） | ✅ 拦截 |
| Linux | `mkfs.ext4 /dev/sda1` | ✅ 拦截 |
| Linux | `dd if=/dev/zero of=/dev/sda` | ✅ 拦截 |
| Linux | `chmod -R 777 /` | ✅ 拦截 |
| Windows | `shutdown /s /t 0`、`shutdown now` | ✅ 拦截 |
| Windows | `format c:` | ✅ 拦截 |
| Windows | `del /f /s`、`rd /s /q`、`rmdir /s` | ✅ 拦截 |
| Windows | `diskpart`、`reg delete` | ✅ 拦截 |
| 防误伤 | `echo hello`、`dir` | ✅ 正常放行 |
| 防误伤 | `git log --format=%H`、`--format=json` | ✅ 正常放行 |

**其他验证**

| 项目 | 结果 |
|:---|:---:|
| `read(".env")` 拦截 | ✅ 返回拒绝提示 |
| 日志着色分类（8 条样本） | ✅ 全部匹配预期 tag |
| `py -m py_compile` 语法检查 | ✅ 通过 |
| 打包后 `resource_path("icon.ico")` 解析 | ✅ 正确定位 |

### 5.3 测试结论

25 项测试全部通过，功能需求 F1–F8 全部达成，
非功能性需求中的安全性与可靠性约束均通过机制而非约定得到保证。

---

## 六、创新亮点

### 6.1 用"唯一性约束"根治 AI 覆盖文件

不是靠提示词恳求 AI "请不要覆盖文件"，而是在工具层面让覆盖**无法发生**：
`edit` 只接受唯一匹配，代码中不存在整文件写入路径。
这是本项目最核心的工程纪律。

### 6.2 双层密钥防护

- **第一层**：`.gitignore` 首行即 `.env`，密钥进不了版本库
- **第二层**：`read` 工具主动拒绝读取 `.env*`，密钥进不了对话上下文

两层防护分别针对"提交到 GitHub"和"泄露给 AI"两个不同的泄露通道。

### 6.3 正则黑名单兼顾拦截与防误伤

15 条规则全部用正则精确描述，既覆盖了 Windows 与 Linux 双平台的破坏性命令，
又保证了 `--format=json` 这类合法参数不被误杀。

### 6.4 协作式中断 + 线程安全队列

Agent 跑在独立线程，通过 `queue.Queue` 与界面单向通信，
主线程用 `after()` 轮询。界面永不卡死，中断按钮始终可点。

### 6.5 完整结果回灌模型、截断结果展示给人

工具结果完整送回模型保证决策准确，界面只显示前 300 字符保证可读性——
同一份数据在两个消费端采用不同的呈现策略。

---

## 七、遇到的问题与解决

| # | 问题 | 原因分析 | 解决办法 |
|:---:|:---|:---|:---|
| 1 | 执行 `python` 无任何输出 | 命中微软商店占位程序 `WindowsApps\python.exe`，非真实解释器 | 改用 `py` 命令（Python Launcher），或关闭系统"应用执行别名" |
| 2 | `git commit` 报 `Author identity unknown` | 本机从未配置过提交身份 | 配置 `user.name` / `user.email`（注意：此属本机 git 配置，需本人执行） |
| 3 | 密钥被贴入对话记录 | 配置 `.env` 时把真实 Key 发给了 AI | 立即到服务商后台 **Revoke 并重新生成**；此后真值一律本人填写 |
| 4 | `.env` 拦截只能挡住精确同名文件 | 等值判断覆盖不到 `.env.local` 等变体 | 改用 `os.path.basename(path).startswith(".env")` |
| 5 | `write` 传入纯文件名时 `dirname` 为空 | 相对路径未展开 | 先 `os.path.abspath()` 再取 `dirname` |
| 6 | tkinter 跨线程操作存在崩溃风险 | 工作线程直接改控件不安全 | 引入 `queue.Queue` + `root.after()` 轮询，界面只由主线程更新 |
| 7 | 打包成 exe 后读不到 `.env` | PyInstaller 单文件会把依赖解压到 `_MEIPASS`，且"当前工作目录"随启动方式变化 | 改用 `sys.executable` 定位 exe 目录，按绝对路径 `load_dotenv()` |
| 8 | 中文命令输出乱码 | Windows cmd 默认 GBK，Python 按 UTF-8 解码 | `_decode()` 依次尝试 UTF-8 → GBK → `replace` 兜底 |
| 9 | 直接用 `format` 做黑名单会误伤 | `--format=json` 等合法参数含该子串 | 改用正则 `\bformat\s+[a-zA-Z]:` |

---

## 八、总结与展望

### 8.1 成果总结

本项目完成了一个可实际使用的本地 AI Agent 桌面应用：

- 实现 `read` / `write` / `edit` / `bash` 四个工具，覆盖文件与命令两大类操作
- 实现最多 5 步的 Agent 自主循环，工具调用过程完全透明
- 实现 tkinter 图形界面，多线程不卡死，支持一键中断
- 建立三层安全机制：密钥不入库、配置文件不可读、危险命令不执行
- 编写 25 项单元测试并全部通过
- 支持 PyInstaller 打包为单文件 exe 分发

在整个开发过程中，始终坚持一条底线：**用机制保证安全，而不是靠约定**。
提示词可以劝告 AI 不要覆盖文件，但只有 `count != 1` 的判断能真正阻止它。

### 8.2 存在的不足

| 不足 | 说明 |
|:---|:---|
| 中断非强制 | `threading.Event` 只能在步骤之间生效，API 请求或命令执行期间需等待 |
| 黑名单可被绕过 | 字符串匹配挡不住变量拼接（如 `RM="rm -rf /"; $RM`），真正隔离需容器沙箱 |
| 无会话持久化 | 关闭窗口后对话历史丢失 |
| 单文件 exe 启动慢 | 每次运行需解压到临时目录，约 5~10 秒 |
| 未做流式输出 | 模型回复需整体返回后才显示 |

### 8.3 后续展望

1. **强制中断**：改为子进程执行，中断时直接 `terminate()`
2. **沙箱隔离**：把 `bash` 放进容器或受限用户，彻底杜绝破坏性操作
3. **会话持久化**：支持保存与加载对话历史
4. **工具扩展**：增加文件搜索、目录树浏览、图片读取等工具
5. **流式输出**：模型回复逐字显示，提升交互体验
6. **多模型支持**：在界面上切换不同模型与接口地址

---

## 附录：主要提交记录

```
49fa6cc  编写 README：项目简介、特色功能、三步启动指南与截图占位
1b7c789  美化界面：日志按类型分色显示（AI黑/工具蓝/报错红），新增窗口图标
dc886cf  修复打包后无法读取 .env 的问题，采用绝对路径加载
3355e57  完成 Agent 主循环与 GUI 界面，支持多线程防卡死与中断
```
