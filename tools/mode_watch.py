"""盯住 TTS 相关配置，变化时在 tts_decisions.log 留一行 [MODE]。

用途：用户自己反复切换语音包/透传开关时不逐次告知，
靠这行记录事后才能把 prompt 样本对齐到当时的运行模式。

只读 cfg 与 ai_config，不改任何东西。默认 20 秒轮询。
"""
import json
import os
import re
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = r"H:\SteamLibrary\steamapps\common\A1\BepInEx\config\org.a1indextts.mod.cfg"
AIC = os.path.join(ROOT, "ai_config.json")
LOG = os.path.join(ROOT, "proxy", "tts_decisions.log")

CFG_KEYS = ["TtsMode", "VoiceEngine", "Backend", "AudioCppModelId", "AudioCppPrecision"]
AIC_KEYS = ["proxy_passthrough", "tts_style_auto_perf", "tts_instruct_override",
            "tts_instruct_mode", "tts_style_inject", "tts_script_v2", "tts_autofill"]
POLL = 20


def read_cfg():
    out = {}
    try:
        t = open(CFG, "rb").read().decode("utf-8-sig")
    except Exception as e:
        return {"_err": f"cfg 读不到 {e}"}
    for k in CFG_KEYS:
        m = re.search(r"(?m)^" + re.escape(k) + r"[ \t]*=[ \t]*(.*?)\r?$", t)
        out[k] = m.group(1) if m else None
    return out


def read_aic():
    try:
        d = json.load(open(AIC, encoding="utf-8-sig"))
    except Exception as e:
        return {"_err": f"ai_config 读不到 {e}"}
    out = {}
    for k in AIC_KEYS:
        v = d.get(k, "")
        out[k] = (v[:20] + "…") if isinstance(v, str) and len(v) > 20 else v
    return out


def snapshot():
    s = read_cfg()
    s.update(read_aic())
    return s


def fmt(s):
    return " ".join(f"{k}={v!r}" for k, v in s.items() if v not in (None, "", False))


def main():
    last = None
    print(f"[MODE] 监视启动，轮询 {POLL}s，日志 {LOG}", flush=True)
    while True:
        cur = snapshot()
        if cur != last:
            line = f"{time.strftime('%m-%d %H:%M:%S')}  [MODE] 配置变更 | {fmt(cur)}\n"
            try:
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception as e:
                print(f"写日志失败 {e}", flush=True)
            print(line.strip(), flush=True)
            last = cur
        time.sleep(POLL)


if __name__ == "__main__":
    main()
