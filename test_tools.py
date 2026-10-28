"""tools.py 测试脚本

运行：  py test_tools.py
覆盖：edit() 的 5 种情况 + bash() 的 5 种情况
"""

import os
import shutil
import sys
import tempfile

from tools import bash, edit, read, write

PASS, FAIL = "[通过]", "[失败]"


def check(name, cond, detail=""):
    print(f"{PASS if cond else FAIL} {name}")
    if detail:
        print(f"       {detail}")
    return cond


def test_edit():
    print("=" * 50)
    print("一、edit() 精确替换")
    print("=" * 50)

    tmp = tempfile.mkdtemp(prefix="tools_edit_")
    try:
        f = os.path.join(tmp, "a.txt")

        # 1. 正常：old_str 唯一，替换成功
        write(f, "hello world\nfoo bar\n")
        r = edit(f, "foo bar", "FOO BAR")
        check("1. 单次匹配替换成功",
              r.startswith("[成功]") and read(f) == "hello world\nFOO BAR\n", r)

        # 2. 0 次匹配：必须拒绝，且不改动文件
        before = read(f)
        r = edit(f, "不存在的文本", "xxx")
        check("2. 0 次匹配被拒绝",
              "不唯一" in r and "0 处" in r and read(f) == before, r)

        # 3. 多次匹配：必须拒绝，且不改动文件
        write(f, "dup\ndup\ndup\n")
        r = edit(f, "dup", "xxx")
        check("3. 多次匹配被拒绝",
              "不唯一" in r and "3 处" in r and read(f) == "dup\ndup\ndup\n", r)

        # 4. old_str 为空
        r = edit(f, "", "xxx")
        check("4. 空 old_str 被拒绝", r.startswith("[错误]"), r)

        # 5. 文件不存在
        r = edit(os.path.join(tmp, "nope.txt"), "a", "b")
        check("5. 文件不存在返回错误", "文件不存在" in r, r)

        # 6. 关键：确认没有整文件覆盖——替换后其余内容仍在
        write(f, "line1\nline2\nline3\n")
        edit(f, "line2", "LINE2")
        r = read(f)
        check("6. 只改目标行，其余内容保留",
              r == "line1\nLINE2\nline3\n", repr(r))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bash():
    print()
    print("=" * 50)
    print("二、bash() 执行命令")
    print("=" * 50)

    # 1. 正常命令
    r = bash("echo hello_bash")
    check("1. 正常命令执行", "hello_bash" in r and "[退出码] 0" in r, r.strip())

    # 2. 非零退出码：必须捕获 returncode 和 stderr
    r = bash(f'{sys.executable} -c "import sys; sys.stderr.write(\'bad\'); sys.exit(3)"')
    check("2. 非零退出码被捕获",
          "[退出码] 3" in r and "bad" in r, r.strip().replace("\n", " | "))

    # 3. 超时：sleep 5 秒但只给 2 秒，必须走 TimeoutExpired 分支
    r = bash(f'{sys.executable} -c "import time; time.sleep(5)"', timeout=2)
    check("3. 超时被捕获（未卡死）", "超时" in r, r.strip())

    # 4. 长输出截断：生成 5000 字符，超过 MAX_OUTPUT=2000
    r = bash(f'{sys.executable} -c "print(\'x\'*5000)"')
    check("4. 超长输出被截断", "已截断" in r, r.strip()[-60:])

    # 5. 空命令
    r = bash("   ")
    check("5. 空命令被拒绝", r.startswith("[错误]"), r)


def test_bash_guard():
    print()
    print("=" * 50)
    print("三、bash() 高危命令拦截")
    print("=" * 50)

    dangerous = [
        "rm -rf /",                          # Linux 删根
        "rm -rf /home/user",
        "rm -rf ~",
        ":(){ :|:& };:",                     # fork bomb
        "mkfs.ext4 /dev/sda1",               # 格式化
        "dd if=/dev/zero of=/dev/sda",       # 写裸盘
        "chmod -R 777 /",
        "shutdown now",
        "shutdown /s /t 0",                  # Windows 关机
        "format c:",                         # Windows 格式化
        "del /f /s C:\\test",                # Windows 强制删除
        "rd /s /q C:\\test",
        "rmdir /s C:\\test",
        "diskpart",
        "reg delete HKLM\\Software",
    ]
    ok = True
    for cmd in dangerous:
        r = bash(cmd)
        passed = "[拒绝]" in r
        ok = check(f"拦截: {cmd}", passed, "" if passed else r) and ok
    print(f"       -> 高危共 {len(dangerous)} 条，全部拦截: {ok}")

    # 误伤检查：这些是正常命令，必须放行
    safe = [
        "echo hello",
        "git log --format=%H",
        f'{sys.executable} -c "print(\'--format=json\')"',
        "dir",
    ]
    ok2 = True
    for cmd in safe:
        r = bash(cmd)
        passed = "[拒绝]" not in r
        ok2 = check(f"放行: {cmd[:40]}", passed, "" if passed else r) and ok2
    print(f"       -> 正常共 {len(safe)} 条，均未误伤: {ok2}")


if __name__ == "__main__":
    test_edit()
    test_bash()
    test_bash_guard()
    print()
    print("测试结束。若全部显示 [通过] 即符合预期。")
