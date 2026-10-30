"""用 OmniVoice 的音色设计造参考音，再用 Breeze 克隆回来验证。

## 要回答的问题

Breeze 克隆最准（±0.0%）但音质有「停顿感」，OmniVoice 音质好但克隆偏 13.5%。
能不能「OmniVoice 造音 + Breeze 发音」各取所长？

关键看两点：
1. Breeze 克隆 OmniVoice 造的参考音，音质跟现在一样还是有改善
2. 不同 instruct 造出来的参考音，音色是否真的不同（决定能否解决撞音）

## 流程（服务在同一次运行内起停，避免被会话清理）

OmniVoice 8897 造 4 条参考音 → 关闭 → Breeze 8893 克隆这 4 条 → 对比
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
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

REF_TEXT = ("你好，请先听我说明。今日山间有风，溪水经过石桥，远处传来几声鸟鸣。"
            "我们不必着急，先把事情讲清楚，再决定接下来要往哪里去。")
TEXT = "你来了。今日山间有风，我正想同你说说话。"
CHARS = 21

# 要造的参考音：instruct 只是 7 个合法标签的组合
DESIGNS = [
    ("低声女", "female, low pitch"),
    ("高音女", "female, high pitch"),
    ("低音男", "male, low pitch"),
    ("年长女", "female, elderly, low pitch"),
]

MODELS = {
    8897: ("omnivoice", "omnivoice",
           os.path.join(CACHE, r"audiocpp\models\OmniVoice-GGUF\omnivoice-f16.gguf"), "offline"),
    8893: ("breeze2", "breeze_tts",
           os.path.join(CACHE, r"audiocpp-breeze\models\breeze-tts-2-q8_0.gguf"), "streaming"),
}


def start(port):
    mid, fam, path, mode = MODELS[port]
    cfg = {"host": "127.0.0.1", "port": port, "backend": "cuda", "device": 0,
           "threads": 4, "lazy_load": False, "log_request_body": False,
           "models": [{"id": mid, "family": fam, "path": path,
                       "task": "tts", "mode": mode}]}
    cp = os.path.join(RT, f"cfg_design_{port}.json")
    json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    log = open(os.path.join(RT, f"design_{port}.log"), "wb")
    p = subprocess.Popen([os.path.join(RT, "audiocpp_server.exe"), "--config", cp, "--log"],
                         cwd=RT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(90):
        time.sleep(2)
        try:
            OPENER.open(f"http://127.0.0.1:{port}/health", timeout=3)
            print(f"  {fam} @{port} 就绪")
            return p
        except Exception:
            if p.poll() is not None:
                print(f"  {fam} 启动失败 code={p.returncode}")
                return None
    return None


def stop(p):
    if not p:
        return
    p.terminate()
    try:
        p.wait(timeout=20)
    except Exception:
        p.kill()
    time.sleep(3)


def post(port, payload, out, timeout=600):
    rq = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(rq, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:140]}"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:100]}"
    open(out, "wb").write(data)
    return None


def normalize(path, target_db=-3.0):
    """峰值归一化，避免参考音太响导致克隆输出削波。"""
    with wave.open(path) as w:
        fs, ch = w.getframerate(), w.getnchannels()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
    peak = max(max(a), -min(a)) or 1
    g = 10 ** (target_db / 20) * 32768 / peak
    a = array.array("h", [max(-32768, min(32767, int(v * g))) for v in a])
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(a.tobytes())


def f0_of(path):
    with wave.open(path) as w:
        fs, ch = w.getframerate(), w.getnchannels()
        a = array.array("h")
        a.frombytes(w.readframes(w.getnframes()))
        dur = w.getnframes() / fs
    if ch > 1:
        a = a[::ch]
    st = max(1, fs // 12000)
    if st > 1:
        a = a[::st]
        fs //= st
    fl = int(fs * 0.03)
    out = []
    for i in range(0, len(a) - fl, fl):
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
    return (round(statistics.median(out), 1) if out else 0), round(dur, 2)


def main():
    print("=== 阶段 1：OmniVoice 设计音色 ===")
    proc = start(8897)
    if not proc:
        return
    refs = {}
    try:
        for name, ins in DESIGNS:
            p = os.path.join(ROOT, f"新参考音_{name}.wav")
            err = post(8897, {"model": "omnivoice", "input": REF_TEXT,
                              "response_format": "wav", "instruct": ins}, p)
            if err:
                print(f"  {name:<8} {err}")
                continue
            normalize(p)
            f0, dur = f0_of(p)
            refs[name] = p
            print(f"  {name:<8} F0={f0:>6.1f}  {dur}s  {ins}")
    finally:
        stop(proc)

    # 对照：现有参考音
    old = os.path.join(NPCS, "100121.wav")
    old_f0, old_dur = f0_of(old)
    print(f"\n  对照 现有100121  F0={old_f0:>6.1f}  {old_dur}s")

    print("\n=== 阶段 2：Breeze 克隆这些参考音 ===")
    proc = start(8893)
    if not proc:
        return
    rows = []
    try:
        targets = [("现有100121", old)] + list(refs.items())
        for name, refpath in targets:
            out = os.path.join(ROOT, f"克隆_{name}.wav")
            err = post(8893, {"model": "breeze2", "input": TEXT, "response_format": "wav",
                              "voice_ref": refpath, "reference_text": REF_TEXT}, out)
            if err:
                print(f"  {name:<10} {err}")
                continue
            rf0, rdur = f0_of(refpath)
            of0, odur = f0_of(out)
            dev = (of0 / rf0 - 1) * 100 if rf0 else 0
            rows.append({"ref": name, "ref_f0": rf0, "out_f0": of0,
                         "dev": round(dev, 1), "dur": odur})
            print(f"  {name:<10} 参考F0={rf0:>6.1f} → 克隆F0={of0:>6.1f} ({dev:+.1f}%)  {odur}s")
    finally:
        stop(proc)

    if rows:
        devs = [abs(r["dev"]) for r in rows]
        print(f"\n克隆偏离：中位 {statistics.median(devs):.1f}%，最大 {max(devs):.1f}%")
    json.dump(rows, open(os.path.join(ROOT, ".tmp_design_clone.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n生成的文件：新参考音_*.wav（OmniVoice 造的参考音）、克隆_*.wav（Breeze 克隆结果）")


if __name__ == "__main__":
    main()
