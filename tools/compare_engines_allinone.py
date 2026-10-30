"""三引擎对比（自管理服务生命周期）。

工具会话结束会清理后台进程，所以服务必须在同一次运行内起停。
串行执行，避免三个模型同时占显存（Breeze 1.2 + Omni 1.2 + VoxCPM2 4.5 GB）。

用法：python tools/compare_engines_allinone.py
"""
import array
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = r"H:\llama\audiocpp-0.9.3"
CACHE = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod\.cache"
NPCS = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod\references\npcs"
REF_WAV = os.path.join(NPCS, "100121.wav")
REF_TEXT = ("刚煮好的怎么样？好喝吧，仙长，这是我刚从树上摘的，可甜了。"
            "你看嘛，我想给你做件新衣服，你喜欢什么颜色的？")
TEXT = "你来了。今日山间有风，我正想同你说说话。"
CHARS = 21
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 引擎定义：(标签, 端口, model_id, family, 权重路径, mode, payload 构造)
ENGINES = [
    ("Breeze_24k_克隆", 8893, "breeze2", "breeze_tts",
     os.path.join(CACHE, r"audiocpp-breeze\models\breeze-tts-2-q8_0.gguf"), "streaming",
     lambda: {"model": "breeze2", "input": TEXT, "response_format": "wav",
              "voice_ref": REF_WAV, "reference_text": REF_TEXT,
              "options": {"instruct": "语速略快，句子短，句尾轻收"}}),
    ("OmniVoice_克隆", 8897, "omnivoice", "omnivoice",
     os.path.join(CACHE, r"audiocpp\models\OmniVoice-GGUF\omnivoice-f16.gguf"), "offline",
     lambda: {"model": "omnivoice", "input": TEXT, "response_format": "wav",
              "voice_ref": REF_WAV, "reference_text": REF_TEXT}),
    ("OmniVoice_克隆无转写", 8897, "omnivoice", "omnivoice",
     os.path.join(CACHE, r"audiocpp\models\OmniVoice-GGUF\omnivoice-f16.gguf"), "offline",
     lambda: {"model": "omnivoice", "input": TEXT, "response_format": "wav",
              "voice_ref": REF_WAV}),
    ("VoxCPM2_克隆", 8899, "voxcpm2", "voxcpm2",
     os.path.join(CACHE, r"audiocpp\models\VoxCPM2-GGUF\voxcpm2-bf16.gguf"), "offline",
     lambda: {"model": "voxcpm2", "input": TEXT, "response_format": "wav",
              "voice_ref": REF_WAV, "reference_text": REF_TEXT}),
    ("VoxCPM2_design", 8899, "voxcpm2", "voxcpm2",
     os.path.join(CACHE, r"audiocpp\models\VoxCPM2-GGUF\voxcpm2-bf16.gguf"), "offline",
     lambda: {"model": "voxcpm2", "input": TEXT, "response_format": "wav",
              "instruct": "young female, soft voice"}),
]


def write_cfg(port, mid, fam, path, mode):
    cfg = {"host": "127.0.0.1", "port": port, "backend": "cuda", "device": 0,
           "threads": 4, "lazy_load": False, "log_request_body": False,
           "models": [{"id": mid, "family": fam, "path": path,
                       "task": "tts", "mode": mode}]}
    p = os.path.join(RUNTIME, f"cfg_cmp_{port}.json")
    json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return p


def start(cfg_path, port):
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    log = open(os.path.join(RUNTIME, f"cmp_{port}.log"), "wb")
    p = subprocess.Popen(
        [os.path.join(RUNTIME, "audiocpp_server.exe"), "--config", cfg_path, "--log"],
        cwd=RUNTIME, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(2)
        try:
            OPENER.open(f"http://127.0.0.1:{port}/health", timeout=3)
            return p
        except Exception:
            if p.poll() is not None:
                print(f"  服务退出 code={p.returncode}")
                return None
    return None


def analyze(path):
    with wave.open(path) as w:
        fs, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
        dur = w.getnframes() / fs
    a = array.array("h")
    a.frombytes(raw)
    if ch > 1:
        a = a[::ch]
    rms = math.sqrt(sum(v * v for v in a) / max(len(a), 1)) / 32768
    peak = max(max(a), -min(a)) / 32768 if a else 0
    return {"sr": fs, "dur": round(dur, 2), "rate": round(CHARS / dur, 2),
            "rms": round(20 * math.log10(max(rms, 1e-9)), 1),
            "peak": round(20 * math.log10(max(peak, 1e-9)), 1)}


def speak(port, payload, out):
    rq = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(rq, timeout=600) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:150]}"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:100]}"
    open(out, "wb").write(data)
    return None


def main():
    print(f"文本：{TEXT}\n")
    rows = []
    # 按端口分组，一个服务跑完它名下的所有用例
    by_port = {}
    for e in ENGINES:
        by_port.setdefault(e[1], []).append(e)

    for port, group in by_port.items():
        label0, _, mid, fam, path, mode, _ = group[0]
        print(f"--- 启动 {fam} @{port} ---")
        cfg = write_cfg(port, mid, fam, path, mode)
        proc = start(cfg, port)
        if not proc:
            for e in group:
                print(f"  {e[0]:<20} 服务启动失败")
            continue
        try:
            for label, _, _, _, _, _, mk in group:
                out = os.path.join(ROOT, f"对比_{label}.wav")
                err = speak(port, mk(), out)
                if err:
                    print(f"  {label:<20} {err}")
                    continue
                st = analyze(out)
                rows.append({"label": label, **st})
                print(f"  {label:<20} {st['sr']}Hz {st['dur']}s "
                      f"{st['rate']}字/s RMS{st['rms']}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
            time.sleep(3)

    print(f"\n{'引擎':<20}{'采样率':>8}{'时长':>7}{'字/秒':>7}{'RMS':>7}{'峰值':>7}")
    for r in rows:
        print(f"{r['label']:<20}{r['sr']:>8}{r['dur']:>7}{r['rate']:>7}"
              f"{r['rms']:>7}{r['peak']:>7}")
    json.dump(rows, open(os.path.join(ROOT, ".tmp_engine_compare.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
