"""验证 Breeze 输出的稳定性：跨会话是否随机。

## 背景

同一参考音、同一文本、同一引擎，两次不同会话测出 F0 285.7→292.7 与 285.7→260.9，
差 12%。如果是 seed 每次启动随机，那所有单次测量的调参结论都不可靠。

## 设计

三轮「起服务 → 同一 payload 连测 3 次 → 关服务」：
- 轮内 3 次相同 → 会话内确定性成立
- 轮间不同     → 跨会话随机，seed 未固定
"""
import array
import hashlib
import json
import math
import os
import statistics
import subprocess
import time
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
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def f0_of(data_bytes):
    p = os.path.join(ROOT, ".tmp_stab.wav")
    open(p, "wb").write(data_bytes)
    with wave.open(p) as w:
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


def start():
    cfg = {"host": "127.0.0.1", "port": 8893, "backend": "cuda", "device": 0,
           "threads": 4, "lazy_load": False, "log_request_body": False,
           "models": [{"id": "breeze2", "family": "breeze_tts",
                       "path": os.path.join(CACHE, r"audiocpp-breeze\models\breeze-tts-2-q8_0.gguf"),
                       "task": "tts", "mode": "streaming"}]}
    cp = os.path.join(RT, "cfg_stab.json")
    json.dump(cfg, open(cp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
    log = open(os.path.join(RT, "stab.log"), "wb")
    p = subprocess.Popen([os.path.join(RT, "audiocpp_server.exe"), "--config", cp, "--log"],
                         cwd=RT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(2)
        try:
            OPENER.open("http://127.0.0.1:8893/health", timeout=3)
            return p
        except Exception:
            if p.poll() is not None:
                return None
    return None


def speak():
    payload = {"model": "breeze2", "input": TEXT, "response_format": "wav",
               "voice_ref": REF_WAV, "reference_text": REF_TEXT}
    rq = urllib.request.Request(
        "http://127.0.0.1:8893/v1/audio/speech",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with OPENER.open(rq, timeout=300) as r:
        return r.read()


def main():
    ref_f0, _ = f0_of(open(REF_WAV, "rb").read())
    print(f"参考音 F0 = {ref_f0}\n")
    rounds = []
    for r in range(3):
        proc = start()
        if not proc:
            print(f"第 {r+1} 轮启动失败")
            continue
        vals = []
        try:
            for i in range(3):
                data = speak()
                md5 = hashlib.md5(data).hexdigest()[:8]
                f0, dur = f0_of(data)
                vals.append((f0, dur, md5))
                print(f"  轮{r+1} 第{i+1}次  F0={f0:>6.1f}  {dur}s  md5={md5}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
            time.sleep(3)
        rounds.append(vals)

    print()
    all_f0 = [v[0] for rd in rounds for v in rd]
    if all_f0:
        print(f"全部 F0：{sorted(all_f0)}")
        print(f"波动范围：{min(all_f0)} ~ {max(all_f0)}  "
              f"（{(max(all_f0)/min(all_f0)-1)*100:+.1f}%）")
        print(f"标准差：{statistics.pstdev(all_f0):.1f} Hz")
    # 轮内 vs 轮间
    for i, rd in enumerate(rounds):
        if rd:
            f0s = [v[0] for v in rd]
            print(f"  轮{i+1}: 轮内极差 {max(f0s)-min(f0s):.1f} Hz，"
                  f"md5 全同={len({v[2] for v in rd})==1}")


if __name__ == "__main__":
    main()
