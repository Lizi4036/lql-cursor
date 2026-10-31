# 极简桌面助手

> 一个跑在你自己电脑上的 AI Agent 桌面应用 —— 能读文件、改文件、执行命令，
> 每一步操作都实时可见，随时可以按下红色按钮中断。

![Python](https://img.shields.io/badge/Python-3.12-blue)
![GUI](https://img.shields.io/badge/GUI-tkinter-green)
![LLM](https://img.shields.io/badge/LLM-DeepSeek%20%E5%85%BC%E5%AE%B9-orange)

---

## 一、项目简介

**极简桌面助手**是一个轻量级的本地 AI Agent 应用。它把大模型的"思考能力"和你本机的
"动手能力"连接起来：你用自然语言下达任务，Agent 自主决定调用哪个工具、执行几步，
并在图形界面上把全过程逐条展示出来。

和纯聊天机器人不同，它**真的会动手**：

- 你问"看看 `main.py` 里有什么" → 它调用 `read` 读文件
- 你说"把第 20 行的超时改成 60" → 它调用 `edit` 精确替换那一段
- 你说"跑一下测试" → 它调用 `bash` 执行命令并把结果带回来分析

整个过程中，Agent 最多自主循环 **5 步**，每一步的工具调用和返回结果都会实时打印，
你可以随时点【中断】叫停。

---

## 二、特色功能

### 1. 四大核心工具

| 工具 | 作用 | 关键设计 |
|:---|:---|:---|
| `read(path, limit)` | 读取文件内容 | 超过 2000 字符自动截断并提示，避免撑爆上下文 |
| `write(path, content)` | 写入文件 | `os.makedirs` 自动创建父目录，新建文件不用先建文件夹 |
| `edit(path, old, new)` | **精确替换**文本 | `old_str` 必须**恰好匹配 1 次**，0 次或多次一律拒绝 |
| `bash(command, timeout)` | 执行终端命令 | 捕获退出码/stdout/stderr，30 秒超时保护 |

**为什么 `edit` 要求匹配次数唯一？**

这是本项目最重要的一条工程纪律。如果允许 AI 图省事用 `write` 整文件覆盖，
一次误判就可能把你几百行的代码冲掉。`edit` 强制精确匹配、只替换一次，
匹配不唯一就直接报错要求补充上下文 —— **从机制上杜绝整文件被冲毁**。

### 2. 高危命令拦截

`bash` 工具内置黑名单，覆盖 Windows 与 Linux 常见破坏性命令，命中即拒绝执行：

| 平台 | 拦截项 |
|:---|:---|
| Linux / macOS | `rm -rf /`、`rm -rf ~`、`mkfs`、`dd if=...of=/dev/`、`> /dev/sd*`、`chmod -R 777 /`、`shutdown`、`halt`、`init 0`、Fork Bomb `:(){ :\|:& };` |
| Windows | `format c:`、`del /f /s`、`erase /f`、`rd /s`、`rmdir /s`、`diskpart`、`reg delete`、`takeown /r`、`cipher /w`、`shutdown /s /r` |

黑名单用**正则**而非裸关键字匹配，避免误伤正常命令：
`git log --format=%H`、`pytest --format=json` 这类带 `format` 的合法命令不会被拦。

### 3. 密钥绝不入库

- 真实 API Key 只存在本地 `.env`，代码里一律 `os.getenv()` 读取
- `.gitignore` 第一条就是 `.env`，从机制上保证它进不了 Git
- `read` 工具主动拒绝读取 `.env` 及其所有变体（`.env.local`、`.env.production` 等）

### 4. 多线程界面 + 一键中断

- Agent 跑在独立线程，界面**永不卡死**
- 红色【■ 中断】按钮通过 `threading.Event` 控制，在每一步、每个工具执行前检查中断信号
- 日志按类型着色：**AI 回复黑色、工具调用蓝色、报错信息红色**

---

## 三、三步启动

### 第 1 步：安装依赖

```bash
# 创建虚拟环境（推荐，可选）
python -m venv .venv
.venv\Scripts\activate

# 安装依赖（国内推荐加清华镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> Windows 上如果 `python` 命令弹出应用商店，改用 `py`：
> `py -m pip install -r requirements.txt`

### 第 2 步：配置 `.env`

在项目根目录新建 `.env` 文件（**注意前面有个点，没有 `.txt` 后缀**），写入：

```ini
OPENAI_API_KEY=sk-你的真实Key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

| 变量 | 说明 |
|:---|:---|
| `OPENAI_API_KEY` | 你的 API Key，DeepSeek / 阿里云百炼等兼容 OpenAI 格式的服务均可 |
| `OPENAI_BASE_URL` | 接口地址，DeepSeek 填 `https://api.deepseek.com/v1` |
| `OPENAI_MODEL` | 模型名，DeepSeek 填 `deepseek-chat` |

> **安全警告**
> - `.env` 已被 `.gitignore` 排除，**永远不会**被提交到 GitHub
> - 不要把 Key 发给任何人、任何 AI 对话框，也不要贴进代码或截图里
> - 如果不慎泄露，请立即到服务商后台 **Revoke（吊销）并重新生成**

### 第 3 步：启动

```bash
python app.py
# Windows 上若 python 不可用：py app.py
```

窗口弹出后，在底部输入框描述任务，回车或点【发送】即可。

---

## 四、界面预览

<!-- 截图占位：将实际截图放入 docs/images/ 后，下面的链接即可正常显示 -->

**主界面**

![主界面](docs/images/main.png)

> 占位说明：运行 `py app.py` 后截取主窗口，保存到 `docs/images/main.png`。

**工具调用过程（蓝色为工具日志，红色为报错）**

![工具调用日志](docs/images/tool-call.png)

> 占位说明：让 Agent 执行一次带工具调用的任务（如"读取 tools.py 前 50 行"）后截图。

**中断演示**

![中断演示](docs/images/stop.png)

> 占位说明：任务执行中点击红色【■ 中断】按钮的状态截图。

---

## 五、项目结构

```
lql-cursor/
├── app.py              主程序：Agent 循环 + tkinter 界面
├── tools.py            四大工具：read / write / edit / bash
├── test_tools.py       工具单元测试（25 项）
├── requirements.txt    依赖清单
├── icon.ico            窗口图标
├── .env                API Key（本地保存，已 gitignore）
├── .gitignore          忽略 .env / __pycache__ / dist / build / demo
└── README.md           本文件
```

运行测试：

```bash
py test_tools.py
```

---

## 六、打包成 exe

```bash
# 1. 安装 PyInstaller
pip install pyinstaller

# 2. 先打带控制台的版本试跑（出错能看到报错）
py -m PyInstaller -F -c app.py --name 极简桌面助手 --clean

# 3. 确认无误后打无黑框正式版
py -m PyInstaller -F -w app.py --name 极简桌面助手 --clean --add-data "icon.ico;."
```

打包产物在 `dist/极简桌面助手.exe`。把 exe 和 `.env` 放在**同一个目录**下即可运行：

```
发布目录/
├── 极简桌面助手.exe
└── .env        ← 自己放进去，不会被打包进 exe
```

程序通过 `sys.executable` 定位 exe 所在目录来读取 `.env`，
所以无论从哪里启动都能正确加载，且 **Key 不会进入 exe 内部**。

---

## 七、常见问题

| 问题 | 解决办法 |
|:---|:---|
| 提示"未读取到 OPENAI_API_KEY" | 检查 `.env` 是否存在于程序目录，变量名是否拼对 |
| `python` 弹出应用商店 | 改用 `py` 命令，或关闭"应用执行别名"中的 python.exe |
| 点中断后没有立刻停 | 中断在**步骤之间**生效，需等待当前 API 请求或命令结束 |
| exe 启动慢 | 单文件模式每次运行需解压到临时目录，属正常现象 |
| exe 被杀软报毒 | 单文件 exe 常见误报，添加信任即可 |

---

## 八、技术栈

- **语言**：Python 3.12
- **界面**：tkinter（标准库，无需额外安装）
- **LLM 接入**：`openai` SDK（兼容 DeepSeek / 阿里云百炼等 OpenAI 格式接口）
- **环境变量**：`python-dotenv`
- **打包**：PyInstaller
