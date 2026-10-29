"""参考音气质 vs 卡片个性：14 原型之间，参考音到底有没有区别？

如果原型间 F0/语速没有统计差异，说明参考音是一批通用音色随机分配的，
那我们"按原型定制口吻"就是在给同一批声音强行套不同人设 —— 这才是音色不像的根因。

用法：
    python tools/analyze_voice_archetype.py [每组抽样数，默认 12]
"""
import array
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NPCS = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod\references\npcs"
CARDS = os.path.join(ROOT, "editor_cards")
CSV = os.path.join(NPCS, "npc_id_name.csv")

FS_TARGET = 12000
FRAME, HOP = 0.03, 0.03
F0_MIN, F0_MAX = 70.0, 400.0
MAX_SEC = 3.0


def f0_scan(path):
    with wave.open(path) as w:
        fs, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        n = min(w.getnframes(), int(fs * MAX_SEC))
        raw = w.readframes(n)
        total = w.getnframes()
    if sw != 2:
        return None
    a = array.array("h")
    a.frombytes(raw)
    if ch > 1:
        a = array.array("h", [sum(a[i:i + ch]) // ch for i in range(0, len(a) - ch + 1, ch)])
    step = max(1, fs // FS_TARGET)
    if step > 1:
        a = array.array("h", a[::step])
        fs //= step
    flen, hop = int(fs * FRAME), int(fs * HOP)
    f0s = []
    for i in range(0, len(a) - flen, hop):
        fr = a[i:i + flen]
        n2 = len(fr)
        mean = sum(fr) / n2
        x = [v - mean for v in fr]
        e = sum(v * v for v in x)
        if e <= 0 or math.sqrt(e / n2) < 80:
            continue
        lm, lM = int(fs / F0_MAX), min(int(fs / F0_MIN), n2 - 2)
        best, bl = 0.0, 0
        for lag in range(lm, lM + 1):
            s = 0
            for j in range(0, n2 - lag, 2):
                s += x[j] * x[j + lag]
            c = s / e
            if c > best:
                best, bl = c, lag
        if best >= 0.3 and bl:
            f0s.append(fs / bl)
    if len(f0s) < 15:
        return None
    return round(statistics.median(f0s), 1), round(total / (fs * step), 2)


def load_cards():
    """npcId -> 个性"""
    out = {}
    for p in glob.glob(os.path.join(CARDS, "*.md")):
        m = re.match(r"(\d+)-", os.path.basename(p))
        if not m:
            continue
        try:
            t = open(p, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        g = re.search(r"^- 个性：(.+)$", t, re.M)
        if g:
            out[m.group(1)] = g.group(1).strip()
    return out


def load_ref_text():
    """npcId -> (ref_text, gender)"""
    out = {}
    try:
        for row in csv.DictReader(open(CSV, encoding="utf-8-sig")):
            nid = (row.get("npcId") or "").strip()
            if nid:
                out[nid] = ((row.get("ref_text") or "").strip(),
                            (row.get("gender") or "").strip())
    except Exception as e:
        print(f"csv 读不到：{e}")
    return out


def main():
    per = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    cards = load_cards()
    refs = load_ref_text()
    print(f"角色卡 {len(cards)} 张，有转写 {len(refs)} 条")

    by_arch = {}
    for nid, arch in cards.items():
        if os.path.isfile(os.path.join(NPCS, f"{nid}.wav")):
            by_arch.setdefault(arch, []).append(nid)

    total_avail = sum(len(v) for v in by_arch.values())
    print(f"其中 {total_avail} 个有参考音，覆盖 {len(by_arch)} 个原型\n")

    # 先采一遍原始数据（原型 + 性别 + F0 + 语速），后面按性别分层复用
    raw = []
    for arch in sorted(by_arch, key=lambda a: -len(by_arch[a])):
        ids = sorted(by_arch[arch])
        stride = max(1, len(ids) // per)
        for nid in ids[::stride][:per]:
            r = f0_scan(os.path.join(NPCS, f"{nid}.wav"))
            if not r:
                continue
            f0, dur = r
            txt, gender = refs.get(nid, ("", ""))
            chars = len(re.sub(r"[，。？！、：；\s]", "", txt))
            raw.append({
                "npc": nid, "原型": arch, "gender": gender,
                "f0": f0,
                "rate": round(chars / dur, 2) if chars and dur else 0,
            })
    print(f"有效样本 {len(raw)} 条\n")

    def table(subset, title):
        by = {}
        for r in subset:
            by.setdefault(r["原型"], []).append(r)
        rows = []
        for arch, rs in by.items():
            if len(rs) < 2:
                continue
            f0s = [r["f0"] for r in rs]
            rates = [r["rate"] for r in rs if r["rate"]]
            rows.append({
                "原型": arch, "n": len(rs),
                "F0中位": round(statistics.median(f0s), 1),
                "F0范围": f"{min(f0s):.0f}~{max(f0s):.0f}",
                "语速中位": round(statistics.median(rates), 2) if rates else 0,
            })
        rows.sort(key=lambda r: -r["n"])
        print(f"—— {title}（{len(subset)} 条）——")
        if not rows:
            print("  样本不足\n")
            return []
        w = max(len(r["原型"]) for r in rows)
        print(f"{'原型':<{w}} {'n':>3} {'F0中位':>8} {'F0范围':>12} {'语速':>6}")
        for r in rows:
            print(f"{r['原型']:<{w}} {r['n']:>3} {r['F0中位']:>8.1f} "
                  f"{r['F0范围']:>12} {r['语速中位']:>6.2f}")
        med = [r["F0中位"] for r in rows]
        if len(med) > 1:
            print(f"  组间 F0 跨度 {min(med):.0f}~{max(med):.0f} Hz "
                  f"（{(max(med)/min(med)-1)*100:+.1f}%）\n")
        return rows

    female = [r for r in raw if r["gender"] == "2"]
    male = [r for r in raw if r["gender"] == "1"]
    f_rows = table(female, "女")
    m_rows = table(male, "男")
    if not female or not male:
        print("注意：性别字段缺失，分层失败，看混合表")
        table(raw, "混合")

    out = os.path.join(ROOT, ".tmp_archetype_f0.json")
    json.dump({"raw": raw, "female": f_rows, "male": m_rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"明细：{out}")


if __name__ == "__main__":
    main()
