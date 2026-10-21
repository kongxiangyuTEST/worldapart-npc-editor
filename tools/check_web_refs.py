# -*- coding: utf-8 -*-
"""
tools/check_web_refs.py

前端引用体检：确认 JS 里 getElementById('x') / querySelector('#x') 引用的元素，
在 index.html 或 JS 自己渲染的模板里真的存在。

为什么需要它：一个失效的 id 引用会让 JS 在初始化时抛错，
后面的初始化全部中断 —— 表现就是"界面能打开，但日志空白、服务状态不动"。
这类问题在浏览器里不报错、不影响打包，只能运行时才发现。

用法：.venv\\Scripts\\python.exe tools\\check_web_refs.py
退出码 0 = 通过，1 = 有缺失引用
"""

import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(BASE, "web")

ID_ATTR = re.compile(r'id="([^"${}]+)"')
# 元素是运行时创建的：overlay.id = 'prompt-overlay'
ID_ASSIGN = re.compile(r"""\.id\s*=\s*['"]([^'"${}]+)['"]""")
# 只取纯字面量引用；带拼接（'a' + name）的跳过，避免误报
GET_BY_ID = re.compile(r"""getElementById\(\s*['"]([^'"]+)['"]\s*\)""")
QS_ID = re.compile(r"""querySelector(?:All)?\(\s*['"]#([^'"\s.>\[]+)['"]\s*\)""")
SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def main():
    html_path = os.path.join(WEB, "index.html")
    if not os.path.isfile(html_path):
        print(f"[错误] 找不到 {html_path}")
        return 1

    html = read(html_path)
    js_files = sorted(f for f in os.listdir(WEB) if f.endswith(".js"))

    # 1. 页面里声明的 id
    declared = set(ID_ATTR.findall(html))

    # 2. JS 模板里动态生成的 id（innerHTML 里的 id="..."、以及 el.id = "..."）
    for f in js_files:
        text = read(os.path.join(WEB, f))
        declared |= set(ID_ATTR.findall(text))
        declared |= set(ID_ASSIGN.findall(text))

    # 3. 收集引用
    refs = {}
    for f in js_files:
        text = read(os.path.join(WEB, f))
        for i, line in enumerate(text.split("\n"), 1):
            for m in GET_BY_ID.finditer(line):
                refs.setdefault(m.group(1), []).append(f"{f}:{i}")
            for m in QS_ID.finditer(line):
                refs.setdefault(m.group(1), []).append(f"{f}:{i}")

    # 4. 脚本文件是否都存在
    problems = []
    for src in SCRIPT_SRC.findall(html):
        if src.startswith("http"):
            continue
        p = os.path.join(WEB, src.split("?")[0])
        if not os.path.isfile(p):
            problems.append(f"index.html 引用的脚本不存在: {src}")

    # 5. 缺失的 id
    missing = {k: v for k, v in refs.items() if k not in declared}

    print("=" * 60)
    print("  前端引用体检")
    print("=" * 60)
    print(f"页面/模板里声明的 id：{len(declared)} 个")
    print(f"JS 里引用的 id：      {len(refs)} 个")

    if missing:
        print()
        print(f"[X] 有 {len(missing)} 个 id 被引用但不存在：")
        for k in sorted(missing):
            print(f"    - #{k}    ← {', '.join(missing[k])}")
        print()
        print("  修法：删掉这些引用，或在 HTML/模板里补上对应的元素。")
    else:
        print("[OK] 所有 id 引用都能对上")

    if problems:
        print()
        for p in problems:
            print("  [X] " + p)

    print("=" * 60)
    return 1 if (missing or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
