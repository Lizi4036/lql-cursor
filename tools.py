"""极简桌面助手 - 文件读写工具

read(path, limit)  读取文件，超出长度自动截断并提示
write(path, content) 写入文件，父目录自动创建
"""

import os

DEFAULT_LIMIT = 2000


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
