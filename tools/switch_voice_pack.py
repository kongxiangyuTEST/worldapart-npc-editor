"""在 Breeze / CosyVoice3 之间切换游戏语音包。

只改 BepInEx/config/org.a1indextts.mod.cfg 四个键，其余原样保留。
逐行处理，\r\n 与 BOM 都不动（Install.ps1 的 [regex]::Replace 会吃掉行尾 \r）。

用法：
    python tools/switch_voice_pack.py            # 只看当前状态
    python tools/switch_voice_pack.py breeze     # 切 Breeze（TtsMode=v2, 8893）
    python tools/switch_voice_pack.py cosy       # 切 CosyVoice3（TtsMode=v1, 8892）
"""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_REL = os.path.join("BepInEx", "config", "org.a1indextts.mod.cfg")

KEYS = ["TtsMode", "VoiceEngine", "Backend", "AudioCppModelId",
        "AudioCppPrecision", "BreezeModelPath", "TtsUrl", "BreezeTtsUrl"]

PACKS = {
    "breeze": {
        "TtsMode": "v2",
        "VoiceEngine": "BreezeAudioCpp",
        "Backend": "CosyVoiceAudioCpp",
        "AudioCppModelId": "cosyvoice3",
    },
    "cosy": {
        "TtsMode": "v1",
        "VoiceEngine": "CosyVoiceAudioCpp",
        "Backend": "CosyVoiceAudioCpp",
        "AudioCppModelId": "cosyvoice3",
        "AudioCppPrecision": "q8_0",
    },
}


def game_dir():
    p = os.path.join(ROOT, "user_config.json")
    if not os.path.isfile(p):
        sys.exit("找不到 user_config.json，先在编辑器里设置游戏目录")
    with open(p, encoding="utf-8") as f:
        gd = json.load(f).get("game_dir", "")
    if not gd or not os.path.isfile(os.path.join(gd, "WorldApart.exe")):
        sys.exit(f"user_config.json 里的 game_dir 无效：{gd!r}")
    return gd


def game_running():
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq WorldApart.exe", "/NH"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            timeout=15).stdout
    except Exception:
        return False
    return "WorldApart.exe" in out


def read_cfg(path):
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    return text, bom


def get_key(text, key):
    m = re.search(r"(?m)^" + re.escape(key) + r"[ \t]*=[ \t]*(.*?)\r?$", text)
    return m.group(1) if m else None


def set_key(text, key, value):
    """逐行替换，行尾 \r 原样保留；键不存在就追加到文件末尾。"""
    lines = text.split("\n")
    pat = re.compile(r"^(" + re.escape(key) + r"[ \t]*=[ \t]*)(.*?)(\r?)$")
    hit = False
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            lines[i] = m.group(1) + value + m.group(3)
            hit = True
            break
    if not hit:
        tail = "" if text.endswith("\n") else "\r\n"
        return text + tail + f"{key} = {value}\r\n"
    return "\n".join(lines)


def newline_stats(path):
    with open(path, "rb") as f:
        d = f.read()
    crlf = d.count(b"\r\n")
    lf = d.count(b"\n") - crlf
    return crlf, lf


def show(text, title):
    print(f"  {title}")
    for k in KEYS:
        v = get_key(text, k)
        print(f"    {k:18}= {v if v is not None else '(缺)'}")


def main():
    target = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    gd = game_dir()
    path = os.path.join(gd, CFG_REL)
    if not os.path.isfile(path):
        sys.exit(f"找不到配置：{path}")

    text, bom = read_cfg(path)
    show(text, "当前：")

    if not target:
        print("\n用法：switch_voice_pack.py breeze | cosy")
        return 0
    if target not in PACKS:
        sys.exit(f"未知目标 {target!r}，只能是 breeze 或 cosy")
    if game_running():
        sys.exit("游戏正在运行，配置改了不起作用。请先正常退出游戏再切。")

    want = PACKS[target]
    bak = path + ".bak-switch"
    shutil.copy2(path, bak)

    for k, v in want.items():
        text = set_key(text, k, v)
    with open(path, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))

    text2, _ = read_cfg(path)
    show(text2, f"已切到 {target}：")
    crlf, lf = newline_stats(path)
    print(f"\n  换行：CRLF={crlf}  LF-only={lf}" + ("  ⚠ 有纯 LF 行！" if lf else "  OK"))
    print(f"  备份：{bak}")

    if target == "cosy":
        if not os.path.isfile(os.path.join(
                gd, "A1IndexTTSMod", ".cache", "audiocpp", "models",
                "CosyVoice3-GGUF", "cosyvoice3-q8_0.gguf")):
            print("  ⚠ 没找到 cosyvoice3-q8_0.gguf，CosyVoice3 包还没装")
        print("  注意：CosyVoice3 需要 TtsMode=v1，代理侧台本帧要退回 a1tts_v1，"
              "否则 NPC 可能不出声")
    return 0


if __name__ == "__main__":
    sys.exit(main())
