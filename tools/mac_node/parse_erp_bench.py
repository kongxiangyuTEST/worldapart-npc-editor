# -*- coding: utf-8 -*-
"""解析 Unhinged-ERP-Benchmark 的排行榜，按目标机内存筛选可用模型。

评分维度（来自表头）：
  1️⃣ Realism        真实感
  2️⃣ Willingness   意愿度（🦋 / 🐺 两个子维度）
  3️⃣ Acceptance    接受度
  4️⃣ SOLO           无引导单测表现
  加权平均分 + Rank-Tier 档位
技术字段里有 Apple Size Tier，直接对应 Apple 统一内存能装下哪档。
"""
import html
import json
import re
import sys
from pathlib import Path

SRC = Path(r"H:\llama\models\wa-npc-cards\_tmp\erp_bench.html")


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse():
    t = SRC.read_text(encoding="utf-8", errors="ignore")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
    out = []
    for r in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
        vals = [clean(c) for c in cells]
        if len(vals) >= 8:
            out.append(vals)
    return out


def num(s):
    """分数是百分比字符串（如 "58%"），直接 float() 会全变 None。"""
    try:
        return float(str(s).replace("%", "").replace(" ", "").strip())
    except Exception:
        return None


def main():
    rows = parse()
    if not rows:
        print("没解析到表格行")
        return 1

    header = rows[0]
    print(f"共 {len(rows)} 行，列数 {len(rows[0])}")
    print("列：", " | ".join(header[:12]))
    print("")

    # 定位关键列
    def col(name, default=None):
        for i, h in enumerate(header):
            if name.lower() in h.lower():
                return i
        return default

    i_model = col("Model", 1)
    i_base = col("Base", 0)
    i_score = col("Weighted Average")
    i_tier = col("Rank-Tier")
    i_apple = col("Apple Size Tier")
    i_size = col("File Size")
    i_arch = col("Architecture Type")
    i_speed = col("Inference speed")

    data = []
    for r in rows[1:]:
        if len(r) <= i_model:
            continue
        model = r[i_model]
        if not model or model.lower() == "model":
            continue
        data.append({
            "base": r[i_base] if i_base is not None else "",
            "model": model,
            "score": num(r[i_score]) if i_score is not None and len(r) > i_score else None,
            "tier": r[i_tier] if i_tier is not None and len(r) > i_tier else "",
            "apple": r[i_apple] if i_apple is not None and len(r) > i_apple else "",
            "size": r[i_size] if i_size is not None and len(r) > i_size else "",
            "arch": r[i_arch] if i_arch is not None and len(r) > i_arch else "",
            "speed": r[i_speed] if i_speed is not None and len(r) > i_speed else "",
        })

    # 保存
    outp = Path(r"H:\llama\models\wa-npc-cards\_tmp\erp_bench.json")
    outp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已存 {len(data)} 条到 {outp}")
    print("")

    # 1) 用户已有的模型（按名字片段匹配）
    owned = [
        "Qwen3.5-9B-Unredacted-MAX",
        "Qwen3-8B-Hivemind",
        "Qwen3.5-9B-The-Defiant",
        "Qwen2.5-14B",
        "Qwen3.6-35B",
        "Tiger-Gemma-12B",
    ]
    print("══════ 你手上有的模型，在榜上的表现 ══════")
    for key in owned:
        hit = [d for d in data if key.replace(".", "").lower().replace("-", "")
               in d["model"].lower().replace(".", "").replace("-", "")]
        if not hit:
            print(f"  {key:<30} 榜上未收录")
            continue
        for d in hit[:3]:
            print(f"  {key:<30} 分数 {str(d['score']):>6}  档位 {d['tier']:<10} "
                  f"Apple档 {d['apple']:<12} 大小 {d['size']}")
    print("")

    # 2) Apple 16GB 档里分数最高的 —— 这就是这台 Mac 能跑的全部候选
    print("══════ 16GB 档 · 按加权分排序（前 20）══════")
    cands = [d for d in data if "16GB" in (d["apple"] or "") and d["score"] is not None]
    cands.sort(key=lambda x: -x["score"])
    print(f"  共 {len(cands)} 个有分模型")
    print("")
    print(f"  {'分数':>5}  {'档位':<8} {'大小':<8} {'架构':<7} {'速度':<9} 模型")
    print("  " + "─" * 96)
    for d in cands[:20]:
        print(f"  {d['score']:>4.0f}%  {d['tier']:<8} {d['size']:<8} "
              f"{d['arch']:<7} {d['speed']:<9} {d['model'][:52]}")
    print("")

    # 3) 用户已有模型的完整分项
    print("══════ 你手上有的模型 · 分项分数 ══════")
    for key in owned:
        hit = [d for d in data if key.replace(".", "").lower().replace("-", "")
               in d["model"].lower().replace(".", "").replace("-", "")]
        for d in hit[:2]:
            print(f"  {d['model'][:58]}")
            print(f"      加权 {d['score']}%  档位 {d['tier']}  Apple {d['apple']}  "
                  f"大小 {d['size']}  架构 {d['arch']}  速度 {d['speed']}")
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
