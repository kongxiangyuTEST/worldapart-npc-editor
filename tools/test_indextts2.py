"""IndexTTS2 验证：克隆质量 + duration_scale（语速）+ emotion_vector（情感）。

## 为什么值得单独测

spec 里 capabilities 写着 clone 支持 emotion_control，request options 有
emotion_alpha / emotion_vector（8 维数值）/ duration_scale —— 这是**真正的数值参数**，
不是 Breeze 那种文字引导。如果成立，用户要的「滑块式调音」就能实现。

## 测什么

1. 克隆：同一参考音，输出 F0 与参考音差多少
2. 语速：duration_scale 0.8 / 1.0 / 1.25 是否线性改变时长
3. 情感：emotion_vector 单维拉高，是否改变输出（用 F0/时长/能量粗判）
"""
import array
import json
import math
import os
import statistics
import subprocess
import time
import urllib.error
import urllib.request
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT = r"H:\llama\audiocpp-0.9.3"
CACHE = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod\.cache"
NPCS = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod\references\npcs"
REF_WAV = os.path.join(NPCS, "100121.wav")
REF_TEXT = ("刚煮好的怎么样？好喝吧，仙长，这是我刚从树上摘的，可甜了。"
            "你看嘛，我想给你做件新衣服，你喜欢什么颜色的？")
TEXT = "你来了。今日山间有风，我正想同你说说话。"
PORT = 8896
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def start():
    cfg = {"host": "127.0.0.1", "port": PORT, "backend": "cuda", "device": 0,
           "threads": 4, "lazy_load": False, "log_request_body": False,
           "models": [{"id": "indextts2", "family": "index_tts2",
                       "path": os.path.join(CACHE, r"audiocpp\models\IndexTTS2-GGUF\index-tts2-q8_0.gguf"),
                       "task": "tts", "mode": "offline"}]}
    cp = os.path.join(RT, "cfg_index.json")
    json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    log = open(os.path.join(RT, "index.log"), "wb")
    p = subprocess.Popen([os.path.join(RT, "audiocpp_server.exe"), "--config", cp, "--log"],
                         cwd=RT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(90):
        time.sleep(2)
        try:
            OPENER.open(f"http://127.0.0.1:{PORT}/health", timeout=3)
            return p
        except Exception:
            if p.poll() is not None:
                print(f"  启动失败 code={p.returncode}")
                return None
    return None


def stats(path):
    with wave.open(path) as w:
        fs, ch = w.getframerate(), w.getnchannels()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        dur = w.getnframes() / fs
    if ch > 1:
        a = a[::ch]
    rms = math.sqrt(sum(v * v for v in a) / max(len(a), 1)) / 32768
    # F0
    st = max(1, fs // 12000)
    b = a[::st] if st > 1 else a
    f = fs // st if st > 1 else fs
    fl = int(f * 0.03)
    f0s = []
    for i in range(0, len(b) - fl, fl):
        fr = b[i:i + fl]
        n = len(fr)
        m = sum(fr) / n
        x = [v - m for v in fr]
        e = sum(v * v for v in x)
        if e <= 0 or math.sqrt(e / n) < 80:
            continue
        lm, lM = int(f / 400), min(int(f / 70), n - 2)
        best, bl = 0.0, 0
        for lag in range(lm, lM + 1):
            s = sum(x[j] * x[j + lag] for j in range(0, n - lag, 2))
            c = s / e
            if c > best:
                best, bl = c, lag
        if best >= 0.3 and bl:
            f0s.append(f / bl)
    return {"sr": fs, "dur": round(dur, 2),
            "f0": round(statistics.median(f0s), 1) if f0s else 0,
            "rms": round(20 * math.log10(max(rms, 1e-9)), 1)}


def speak(extra, out):
    payload = {"model": "indextts2", "input": TEXT, "response_format": "wav",
               "voice_ref": REF_WAV, "reference_text": REF_TEXT}
    payload.update(extra)
    rq = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(rq, timeout=600) as r:
            open(out, "wb").write(r.read())
        return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:180]}"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:100]}"


def main():
    ref = stats(REF_WAV)
    print(f"参考音 100121: F0={ref['f0']} {ref['dur']}s {ref['sr']}Hz\n")
    proc = start()
    if not proc:
        return
    try:
        print("--- 1. 基线克隆 ---")
        cases = [("基线", {})]
        for ds in (0.8, 1.25):
            cases.append((f"语速x{ds}", {"duration_scale": ds}))
        for label, extra in cases:
            out = os.path.join(ROOT, f"Index_{label}.wav")
            err = speak(extra, out)
            if err:
                print(f"  {label:<10} {err}")
                continue
            st = stats(out)
            print(f"  {label:<10} F0={st['f0']:>6.1f} {st['dur']}s "
                  f"RMS{st['rms']} (参考 {ref['dur']}s)")

        print("\n--- 2. 情感向量（8 维，单维拉高）---")
        for idx, name in enumerate(["维1", "维2", "维3", "维4", "维5", "维6", "维7", "维8"]):
            vec = ",".join("1.0" if i == idx else "0.0" for i in range(8))
            out = os.path.join(ROOT, f"Index_emo_{idx+1}.wav")
            err = speak({"emotion_vector": vec, "emotion_alpha": 1.0}, out)
            if err:
                print(f"  {name}: {err[:70]}")
                continue
            st = stats(out)
            print(f"  {name}拉满  F0={st['f0']:>6.1f} {st['dur']}s RMS{st['rms']}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
