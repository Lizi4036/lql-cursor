"""极简桌面助手 - 文件读写工具

read(path, limit)  读取文件，超出长度自动截断并提示
write(path, content) 写入文件，父目录自动创建
"""

import os
import re
import subprocess

DEFAULT_LIMIT = 2000
MAX_OUTPUT = 2000

# 高危命令黑名单（正则，忽略大小写）
# 说明：刻意写得比裸关键字更严格，避免误伤正常命令
#   例：用 r"format\s+[a-zA-Z]:" 而不是 "format"，
#       这样 `--format=json`、`git log --format=` 不会被拦
BLOCKED_PATTERNS = [
    # ---- Linux / macOS ----
    r"rm\s+-[rR][fF]\s+(/|~|\*|\$HOME)",  # rm -rf / 、rm -rf ~
    r"rm\s+-[rR][fF]\s+/$",               # rm -rf /
    r":\(\)\s*\{",                        # fork bomb: :(){ :|:& };:
    r"\bmkfs",                            # 格式化文件系统
    r"\bdd\s+if=.+\s+of=/dev/",           # dd 直接写磁盘
    r">\s*/dev/(sd|nvme|hd)",             # 重定向写裸磁盘
    r"chmod\s+-R\s+777\s+/",              # chmod -R 777 /
    r"chown\s+-R\s+.+\s+/",               # chown -R ... /
    r"\bshutdown\b",                      # shutdown
    r"\bhalt\b|\bpoweroff\b",             # halt / poweroff
    r"\binit\s+0\b",                      # init 0
    # ---- Windows ----
    r"\bformat\s+[a-zA-Z]:",              # format c:
    r"\bdel\s+/[fFsS]",                   # del /f /s
    r"\berase\s+/[fFsS]",                 # erase /f
    r"\brd\s+/[sS]",                      # rd /s /q
    r"\brmdir\s+/[sS]",                   # rmdir /s
    r"\bdiskpart\b",                      # diskpart
    r"\breg\s+delete\b",                  # reg delete
    r"\btakeown\s+/[rR]",                 # takeown /r
    r"\bcipher\s+/[wW]",                  # cipher /w 擦除
    r"shutdown\s*/[sSrR]",                # shutdown /s /r
]


def _is_dangerous(command):
    """检查命令是否命中高危黑名单。

    Returns:
        命中的规则（字符串）；安全则返回 None。
    """
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return pattern
    return None


def _decode(raw):
    """把命令输出解码成文本：先试 UTF-8，失败回退 GBK（Windows 中文终端），
    再失败则用 replace 兜底，保证任何情况下都不抛异常。"""
    if not raw:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read(path, limit=DEFAULT_LIMIT):
    """读取文件内容。

    超过 limit 个字符时截断，并在末尾附上截断提示，
    避免超长文件一次性塞满上下文。

    Args:
        path: 文件路径
        limit: 最大读取字符数，默认 2000

    Returns:
        文件内容字符串；出错时返回错误说明。
    """
    if os.path.basename(path).startswith(".env"):
        return "[拒绝] 出于安全考虑，不读取 .env 相关文件（含 .env.local 等变体）"

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return f"[错误] 文件不存在: {path}"
    except IsADirectoryError:
        return f"[错误] 这是一个目录，不是文件: {path}"
    except PermissionError:
        return f"[错误] 没有读取权限: {path}"
    except OSError as e:
        return f"[错误] 读取失败: {path} -> {e}"

    if len(content) > limit:
        shown = content[:limit]
        tip = (
            f"\n\n...[已截断] 文件共 {len(content)} 字符，"
            f"仅显示前 {limit} 字符。调大 limit 可读取更多。"
        )
        return shown + tip

    return content


def write(path, content):
    """写入文件，父目录不存在时自动创建。

    Args:
        path: 文件路径
        content: 要写入的文本

    Returns:
        写入结果说明。
    """
    try:
        # 先建父目录：abspath 保证纯文件名也能拿到当前工作目录
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"[成功] 已写入 {path}（{len(content)} 字符）"
    except PermissionError:
        return f"[错误] 没有写入权限: {path}"
    except OSError as e:
        return f"[错误] 写入失败: {path} -> {e}"


def edit(path, old_str, new_str):
    """精确替换文件中的一段文本。

    要求 old_str 在文件中**恰好出现 1 次**：
      - 0 次：说明没匹配上，拒绝改动
      - >=2 次：说明上下文不够，拒绝改动，要求提供更多上下文

    只调用一次 str.replace(old, new, 1)，绝不做整文件覆盖重写。

    Args:
        path: 文件路径
        old_str: 要被替换的原文本
        new_str: 替换后的新文本

    Returns:
        替换结果说明；不唯一或出错时返回错误说明。
    """
    # 读原文件
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return f"[错误] 文件不存在: {path}"
    except IsADirectoryError:
        return f"[错误] 这是一个目录，不是文件: {path}"
    except PermissionError:
        return f"[错误] 没有读取权限: {path}"
    except UnicodeDecodeError:
        return f"[错误] 文件不是 UTF-8 文本，无法编辑: {path}"
    except OSError as e:
        return f"[错误] 读取失败: {path} -> {e}"

    # old_str 为空没有意义，直接拒绝
    if old_str == "":
        return "[错误] old_str 不能为空"

    # 唯一性校验：不是 1 次就直接打回
    count = content.count(old_str)
    if count != 1:
        return (
            f"[错误] old_str 出现次数不唯一，请提供更多上下文。"
            f"（在 {path} 中找到 {count} 处匹配）"
        )

    # 只替换一次，不做整文件重写
    new_content = content.replace(old_str, new_str, 1)

    # 写回
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except PermissionError:
        return f"[错误] 没有写入权限: {path}"
    except OSError as e:
        return f"[错误] 写入失败: {path} -> {e}"

    return f"[成功] 已替换 {path}（1 处，{len(old_str)} 字符 -> {len(new_str)} 字符）"


def bash(command, timeout=30):
    """执行终端命令。

    - 捕获 stdout / stderr / returncode
    - 输出超过 MAX_OUTPUT 字符时截断并提示
    - 强制设置 timeout（默认 30 秒），超时抛 TimeoutExpired 会被捕获，防止 Agent 卡死

    Args:
        command: 要执行的命令字符串
        timeout: 超时秒数，默认 30

    Returns:
        包含退出码、stdout、stderr 的文本；异常时返回错误说明。
    """
    if not command or not command.strip():
        return "[错误] 命令为空"

    # 高危命令拦截：放在任何执行动作之前
    hit = _is_dangerous(command)
    if hit:
        return f"[拒绝] 检测到高危命令，出于安全考虑已拦截（命中规则: {hit}）"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"[错误] 命令超时（超过 {timeout} 秒）: {command}"
    except FileNotFoundError:
        return f"[错误] 命令不存在: {command}"
    except OSError as e:
        return f"[错误] 执行失败: {command} -> {e}"

    stdout = _decode(result.stdout)
    stderr = _decode(result.stderr)

    output = f"[退出码] {result.returncode}\n[stdout]\n{stdout}"
    if stderr:
        output += f"\n[stderr]\n{stderr}"

    if len(output) > MAX_OUTPUT:
        output = (
            output[:MAX_OUTPUT]
            + f"\n\n...[已截断] 输出共 {len(output)} 字符，"
            f"仅显示前 {MAX_OUTPUT} 字符。"
        )

    return output
