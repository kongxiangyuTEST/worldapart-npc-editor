# -*- coding: utf-8 -*-
"""从 ERP 榜单里筛出「适合 16GB Mac 做聊天/角色扮演」的模型。

筛选逻辑（每条都有理由）：
  1. Apple Size Tier 必须含 16GB —— 硬约束，别的档装不下
  2. 加权分 >= 阈值 —— 低于这个分数不如用现在手上的
  3. 只要 Dense —— MoE 的「激活参数少」只省算力不省内存，权重必须全部驻留，
     16GB 上跑 MoE 是自找麻烦
  4. 文件大小 <= 7GB —— 再大就要和系统抢内存，开始 swap
  5. 排除已实测有缺陷的（8B Hivemind 那批会中英混杂）

排序按加权分，并标注中文适配性（Qwen 系优先，因为这是中文游戏）。
"""
import html
import json
import re
import sys
from pathlib import Path

SRC = Path(r"H:\llama\models\wa-npc-cards\_tmp\erp_bench.html")
OUT = Path(r"H:\llama\models\wa-npc-cards\_tmp\chat_candidates.json")

MIN_SCORE = 65.0
MAX_SIZE_GB = 7.0

# 已实测有问题的：会中英混杂（"柴大官itto镖局"）
BLACKLIST = ["hivemind"]


def clean(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def num(s):
    try:
        return float(str(s).replace("%", "").replace(" ", "").strip())
    except Exception:
        return None


def size_gb(s):
    m = re.search(r"([\d.]+)\s*GB", str(s))
    return float(m.group(1)) if m else None


def parse():
    t = SRC.read_text(encoding="utf-8", errors="ignore")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S)
    out = []
    for r in rows:
        cells = [clean(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(cells) >= 20:
            out.append(cells)
    return out


def main():
    rows = parse()
    if not rows:
        print("没解析到数据")
        return 1
    header = rows[0]
    idx = {h: i for i, h in enumerate(header)}

    def g(r, key):
        i = idx.get(key)
        return r[i] if i is not None and i < len(r) else ""

    cands, rejected = [], {"内存档不符": 0, "分数不够": 0, "MoE": 0, "太大": 0, "黑名单": 0}

    for r in rows[1:]:
        model = g(r, "Model")
        if not model:
            continue
        score = num(g(r, "Weighted Average Score"))
        apple = g(r, "Apple Size Tier")
        arch = g(r, "Architecture Type")
        size = size_gb(g(r, "File Size"))
        speed = g(r, "Inference speed")
        tier = g(r, "Rank-Tier")

        if "16GB" not in apple:
            rejected["内存档不符"] += 1
            continue
        if score is None or score < MIN_SCORE:
            rejected["分数不够"] += 1
            continue
        if "moe" in arch.lower():
            rejected["MoE"] += 1
            continue
        if size is not None and size > MAX_SIZE_GB:
            rejected["太大"] += 1
            continue
        if any(b in model.lower() for b in BLACKLIST):
            rejected["黑名单"] += 1
            continue

        cands.append({
            "model": model, "score": score, "tier": tier,
            "size": g(r, "File Size"), "arch": arch, "speed": speed,
            "base": g(r, "Base"), "params": g(r, "Billion Params"),
            "format": g(r, "Format"),
        })

    cands.sort(key=lambda x: -x["score"])

    print(f"筛出 {len(cands)} 个候选（{MIN_SCORE:.0f}分以上 / 16GB档 / Dense / ≤{MAX_SIZE_GB:.0f}GB）")
    print(f"被排除：{rejected}")
    print("")
    print(f"{'分数':>5} {'档':<4} {'参数':<6} {'大小':<8} {'速度':<10} {'系列':<16} 模型")
    print("─" * 112)
    for c in cands:
        print(f"{c['score']:>4.0f}% {c['tier']:<4} {c['params']:<6} {c['size']:<8} "
              f"{c['speed']:<10} {c['base'][:16]:<16} {c['model'][:44]}")

    OUT.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
    print("")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
