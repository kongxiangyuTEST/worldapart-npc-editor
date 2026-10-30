"""长文本对比：IndexTTS2 vs Breeze，同一参考音、同一段 110 字台词。

短句（21 字）听不出音质差异，用一段有情绪起伏的完整台词。
同时记录生成耗时（RTF），这对游戏体验是硬指标。
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
TEXT = ("你来了。今日山间有风，我正想同你说说话。"
        "前些日子你托我打听的那件事，我托人去问了。"
        "城东的货栈确实有一批新到的布，只是价钱比往年高了不少。"
        "你要是急着用，我可以先替你垫上，等你宽裕了再还。"
        "只是有一件事我得先说清楚，那批布的颜色不太正，你得亲自去看一眼才作数。")
CHARS = len([c for c in TEXT if c not in "，。？！、：； "])
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

MODELS = {
    "indextts2": (8896, "index_tts2",
                  os.path.join(CACHE, r"audiocpp\models\IndexTTS2-GGUF\index-tts2-q8_0.gguf"), "offline"),
    "breeze2": (8893, "breeze_tts",
                os.path.join(CACHE, r"audiocpp-breeze\models\breeze-tts-2-q8_0.gguf"), "streaming"),
}


def start(mid):
    port, fam, path, mode = MODELS[mid]
    cfg = {"host": "127.0.0.1", "port": port, "backend": "cuda", "device": 0,
           "threads": 4, "lazy_load": False, "log_request_body": False,
           "models": [{"id": mid, "family": fam, "path": path,
                       "task": "tts", "mode": mode}]}
    cp = os.path.join(RT, f"cfg_long_{port}.json")
    json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    log = open(os.path.join(RT, f"long_{port}.log"), "wb")
    p = subprocess.Popen([os.path.join(RT, "audiocpp_server.exe"), "--config", cp, "--log"],
                         cwd=RT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(120):
        time.sleep(2)
        try:
            OPENER.open(f"http://127.0.0.1:{port}/health", timeout=3)
            return p, port
        except Exception:
            if p.poll() is not None:
                return None, port
    return None, port


def dur_of(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate(), w.getframerate()


def f0_of(path):
    with wave.open(path) as w:
        fs, ch = w.getframerate(), w.getnchannels()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    if ch > 1:
        a = a[::ch]
    st = max(1, fs // 12000)
    if st > 1:
        a = a[::st]
        fs //= st
    fl = int(fs * 0.03)
    out = []
    for i in range(0, len(a) - fl, fl * 2):
        fr = a[i:i + fl]
        n = len(fr)
        m = sum(fr) / n
        x = [v - m for v in fr]
        e = sum(v * v for v in x)
        if e <= 0 or math.sqrt(e / n) < 80:
            continue
        lm, lM = int(fs / 400), min(int(fs / 70), n - 2)
        best, bl = 0.0, 0
        for lag in range(lm, lM + 1):
            s = sum(x[j] * x[j + lag] for j in range(0, n - lag, 2))
            c = s / e
            if c > best:
                best, bl = c, lag
        if best >= 0.3 and bl:
            out.append(fs / bl)
    return round(statistics.median(out), 1) if out else 0


def speak(port, payload, out):
    rq = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with OPENER.open(rq, timeout=900) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:150]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:90]}"
    el = time.time() - t0
    open(out, "wb").write(data)
    return el, None


def main():
    print(f"文本 {CHARS} 字\n")
    results = []
    for mid, cases in [
        ("indextts2", [("基线", {}), ("语速x1.3", {"duration_scale": 1.3}),
                       ("维5拉满", {"emotion_vector": "0,0,0,0,1,0,0,0"})]),
        ("breeze2", [("基线", {})]),
    ]:
        proc, port = start(mid)
        if not proc:
            print(f"{mid} 启动失败")
            continue
        print(f"--- {mid} ---")
        try:
            for label, extra in cases:
                out = os.path.join(ROOT, f"长_{mid}_{label}.wav")
                payload = {"model": mid, "input": TEXT, "response_format": "wav",
                           "voice_ref": REF_WAV, "reference_text": REF_TEXT}
                payload.update(extra)
                el, err = speak(port, payload, out)
                if err:
                    print(f"  {label:<10} {err}")
                    continue
                d, sr = dur_of(out)
                rtf = el / d if d else 0
                results.append({"engine": mid, "label": label, "dur": round(d, 2),
                                "gen_s": round(el, 2), "rtf": round(rtf, 2),
                                "f0": f0_of(out), "sr": sr})
                print(f"  {label:<10} 音频 {d:>5.2f}s  生成 {el:>5.2f}s  "
                      f"RTF {rtf:.2f}  F0 {results[-1]['f0']}  {sr}Hz")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            time.sleep(3)
    json.dump(results, open(os.path.join(ROOT, ".tmp_long.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
