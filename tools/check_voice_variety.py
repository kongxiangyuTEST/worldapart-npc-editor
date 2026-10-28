"""多角色口吻验收 —— 看不同 NPC 的 instruct 到底有没有区别。

## 为什么需要

双层 delivery 的设计是「角色基线（来自角色卡个性）+ 逐句表演」。
写完代码只能证明逻辑对，**证不了不同角色真的被区别对待**。

这个脚本把 tts_decisions.log 和 stage3-audio 的音频对起来，
按 NPC 汇总，输出一张对比表。一眼能看出：

- 不同 NPC 的 instruct 前半段（角色基线）是否不同
- 同一 NPC 的 instruct 后半段（逐句表演）是否随台词变化
- 每轮的语速（字/秒），可以横向比

## 用法

    .venv\\Scripts\\python.exe tools\\check_voice_variety.py
    .venv\\Scripts\\python.exe tools\\check_voice_variety.py --since 10-28

`--since` 传 "10-28" 这样的日期前缀，只看之后的记录。
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import wave
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOG_PATH = os.path.join(ROOT, "proxy", "tts_decisions.log")

MOD_DIR = r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod"
AUDIO_DIR = os.path.join(MOD_DIR, ".state", "stage3-audio")
CSV_PATH = os.path.join(MOD_DIR, "references", "npcs", "npc_id_name.csv")

# 一行日志的格式（见 core_proxy._tts_log）：
#   10-28 01:47:10  npc=100121 profile=hard | 改动摘要 | chars=183 | instruct='...'
LINE_RE = re.compile(
    r"^(?P<ts>\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"npc=(?P<npc>[^\s|]+)\s+profile=(?P<prof>[^\s|]+)\s*\|\s*"
    r"(?P<action>[^|]*)\|\s*chars=(?P<chars>\d+)\s*\|\s*"
    r"instruct='(?P<instruct>.*)'\s*$"
)


def load_names() -> dict:
    """npc_id -> 名字。读不到就返回空表。"""
    out = {}
    try:
        import csv
        with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out[str(r.get("npcId"))] = r.get("npcName") or ""
    except Exception:
        pass
    return out


def parse_log(since: str = "") -> list:
    """解析日志，返回记录列表。"""
    if not os.path.isfile(LOG_PATH):
        return []
    rows = []
    with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if since and not line.startswith(since):
                continue
            m = LINE_RE.match(line)
            if not m:
                continue
            d = m.groupdict()
            try:
                ts = datetime.strptime(
                    f"{datetime.now().year}-{d['ts']}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            rows.append({
                "ts": ts,
                "ts_text": d["ts"],
                "npc": d["npc"],
                "profile": d["prof"],
                "action": (d["action"] or "").strip(),
                "chars": int(d["chars"]),
                "instruct": d["instruct"],
            })
    return rows


def load_audio() -> list:
    """返回 [(mtime, path, duration)]，按时序排好。"""
    out = []
    for p in glob.glob(os.path.join(AUDIO_DIR, "*.wav")):
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(p))
            with wave.open(p) as w:
                dur = w.getnframes() / w.getframerate()
            out.append((mt, p, dur))
        except Exception:
            continue
    out.sort()
    return out


def attach_audio(rows: list, audio: list, window: int = 180):
    """给每条日志配上它那一轮的音频。

    日志在合成**之前**写，所以音频的 mtime 略晚于日志时间。
    取「不早于日志时间、且在窗口内、最接近」的那个。
    """
    used = set()
    for r in rows:
        best = None
        for i, (mt, p, dur) in enumerate(audio):
            if i in used:
                continue
            delta = (mt - r["ts"]).total_seconds()
            if -5 <= delta <= window:
                if best is None or abs(delta) < abs(best[0]):
                    best = (delta, i, p, dur)
        if best:
            used.add(best[1])
            r["audio"] = os.path.basename(best[2])
            r["dur"] = best[3]
            r["cps"] = r["chars"] / best[3] if best[3] > 0 else 0
        else:
            r["audio"] = ""
            r["dur"] = 0.0
            r["cps"] = 0.0


def split_instruct(text: str):
    """把 instruct 拆成（角色基线, 逐句表演）。没有分号就整段当基线。"""
    t = (text or "").strip()
    if "；" in t:
        a, b = t.split("；", 1)
        return a.strip(), b.strip()
    return t, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="", help='只看某个日期前缀，如 "10-28"')
    args = ap.parse_args()

    rows = parse_log(args.since)
    if not rows:
        print("日志里没有可解析的记录。先跑一局游戏。")
        print(f"日志路径：{LOG_PATH}")
        return

    audio = load_audio()
    attach_audio(rows, audio)
    names = load_names()

    print("=" * 78)
    print(f"共 {len(rows)} 轮记录，{len(audio)} 个音频文件")
    print("=" * 78)
    print()

    # 逐轮明细
    print(f"{'时间':>11}  {'NPC':<12}{'基线':<12}{'字数':>4}{'时长':>8}{'字/秒':>6}  表演段")
    print("-" * 78)
    for r in rows:
        nm = names.get(r["npc"]) or r["npc"]
        base, perf = split_instruct(r["instruct"])
        dur = f"{r['dur']:.1f}s" if r["dur"] else "—"
        cps = f"{r['cps']:.2f}" if r["cps"] else "—"
        print(f"{r['ts_text']:>11}  {nm[:12]:<12}{base[:12]:<12}"
              f"{r['chars']:>4}{dur:>8}{cps:>6}  {perf[:30]}")
    print()

    # 按 NPC 汇总
    by_npc = {}
    for r in rows:
        by_npc.setdefault(r["npc"], []).append(r)

    print("=" * 78)
    print("按角色汇总")
    print("=" * 78)
    for npc, items in sorted(by_npc.items(), key=lambda kv: -len(kv[1])):
        nm = names.get(npc) or npc
        bases = {split_instruct(x["instruct"])[0] for x in items}
        perfs = {split_instruct(x["instruct"])[1] for x in items if split_instruct(x["instruct"])[1]}
        with_audio = [x for x in items if x["dur"]]
        avg_cps = (sum(x["cps"] for x in with_audio) / len(with_audio)) if with_audio else 0
        print(f"\n{nm}（{npc}） profile={items[0]['profile']}  {len(items)} 轮")
        print(f"  角色基线：{list(bases)[0] if len(bases) == 1 else bases}")
        print(f"  表演段种类：{len(perfs)} 种")
        for p in list(perfs)[:5]:
            print(f"      · {p[:60]}")
        if avg_cps:
            print(f"  平均语速：{avg_cps:.2f} 字/秒（{len(with_audio)} 段有音频）")

    # 验收判定
    print()
    print("=" * 78)
    print("判定")
    print("=" * 78)
    all_bases = {}
    for npc, items in by_npc.items():
        all_bases[npc] = split_instruct(items[0]["instruct"])[0]

    if len(all_bases) < 2:
        print(f"只测了 {len(all_bases)} 个角色 —— 至少要 3 个不同角色才能判断"
              "「角色之间有没有区别」。")
        print("建议找个性差异大的：")
        print("  100000 苏倾盏（精明内敛 → 语气温和克制）")
        print("  100001 柳秀娘（逍遥乐天 → 轻快松弛）")
        print("  110062 商挽（冷静独行 → 声音平稳偏冷）")
    else:
        uniq = len(set(all_bases.values()))
        print(f"测了 {len(all_bases)} 个角色，产出 {uniq} 种不同的角色基线。")
        if uniq == len(all_bases):
            print("✓ 每个角色都有自己的基线 —— 双层 delivery 在角色维度生效。")
        else:
            print("✗ 有角色共用同一条基线 —— 检查卡片「个性」字段或原型映射：")
            seen = {}
            for npc, b in all_bases.items():
                seen.setdefault(b, []).append(names.get(npc) or npc)
            for b, who in seen.items():
                if len(who) > 1:
                    print(f"    共用「{b}」：{who}")


if __name__ == "__main__":
    main()
