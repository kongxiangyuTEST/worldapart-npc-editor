# -*- coding: utf-8 -*-
"""从 A1IndexTTSMod.dll 里挖 V2 台本的提示词模板（C# 字符串是 UTF-16LE）。"""
import re

P = r"H:\SteamLibrary\steamapps\common\A1\BepInEx\plugins\A1IndexTTSMod\A1IndexTTSMod.dll"
data = open(P, "rb").read()
print("DLL 大小:", len(data))

# UTF-16LE 里，ASCII 字符 = 字节 + \x00；中文 = 两字节（第二字节 0x4E-0x9F 区）
# 提取所有“可读 UTF-16 串”：连续的 (低字节可打印或中文) + \x00
pat = re.compile(
    b"(?:[\x20-\x7e]\x00|[\x00-\xff][\x4e-\x9f]){30,}"
)

found = []
for m in pat.finditer(data):
    try:
        s = m.group(0).decode("utf-16-le", errors="ignore")
    except Exception:
        continue
    if re.search(r"a1tts|台本|Segments|SpokenText|BreezeInput|Instruct", s, re.I):
        found.append((m.start(), s))

print("相关长字符串:", len(found))
for pos, s in found[:8]:
    print("=" * 60)
    print(f"@{pos}  长度 {len(s)}")
    print(s[:800])
