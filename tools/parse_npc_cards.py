# -*- coding: utf-8 -*-
"""
从 AssetStudioModCLI 导出的 Luban 表解析 NPC 卡片。
用法：
    python parse_npc_cards.py <luban_export_dir> <output_dir>
"""


import io
import json
import re
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from pathlib import Path


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def L10n(d):
    """从多语言字典里取中文"""
    if not d:
        return ""
    if isinstance(d, str):
        return d
    for key in ("zh-Hans", "zh-Hant"):
        v = d.get(key)
        if v:
            return v
    for v in d.values():
        if v:
            return v
    return ""


def gender_text(g):
    if g == 1:
        return "男"
    if g == 2:
        return "女"
    return "未知"


def safe_name(s):
    if not s:
        return "unknown"
    return re.sub(r'[\\/:*?"<>|]', "_", s)


def build_card(npc, tables):
    """按 Mod 的逻辑拼 .md 内容"""
    npc_id = str(npc.get("id", ""))
    npc_name = L10n(npc.get("npcName", {}))
    gender = gender_text(npc.get("gender", 0))
    age = str(npc.get("age", ""))

    # 境界
    realm = "未知或凡人"
    layer_id = npc.get("propLayerId")
    if layer_id is not None:
        layer = tables["layer"].get(str(layer_id)) or tables["layer"].get(layer_id)
        if layer:
            realm = str(layer.get("id", ""))

    # 个性
    personality = npc.get("personalityDesc", "") or ""
    pers_id = npc.get("propPersonality")
    if not personality and pers_id is not None:
        pers = tables["personality"].get(str(pers_id)) or tables["personality"].get(pers_id)
        if pers:
            personality = L10n(pers.get("personalityName", {}))

    # 兴趣
    interests = []
    for iid in (npc.get("propInterestItemTypeList") or []):
        item = tables["item"].get(str(iid)) or tables["item"].get(iid)
        if item:
            n = L10n(item.get("typeName", {}))
            interests.append(n if n else str(iid))
    interest_str = "、".join(interests)

    # 背景
    plot = L10n(npc.get("propPlot", {}))

    lines = []
    lines.append(f"# {npc_name}")
    lines.append("")
    lines.append(f"NPC ID: {npc_id}")
    lines.append("")
    lines.append("## 用户名片（优先读取，可自行增添）")
    lines.append("")
    lines.append("<!-- 在此填写说话习惯、身世、目标、与玩家的约定等；有冲突时优先采用你明确写下的角色设定。 -->")
    lines.append("")
    lines.append("## 预设资料（可修改；导出时快照）")
    lines.append("")
    lines.append(f"- 姓名：{npc_name}")
    lines.append(f"- 性别：{gender}")
    lines.append(f"- 年龄：{age}")
    lines.append(f"- 境界：{realm}")
    lines.append(f"- 个性：{personality}")
    lines.append(f"- 兴趣：{interest_str}")
    lines.append("")
    lines.append("## 原游戏背景")
    lines.append("")
    lines.append(plot)
    lines.append("")
    return "\n".join(lines)


def index_by_id(rows):
    """把 [{id: 1, ...}, ...] 变成 {1: {...}}"""
    out = {}
    for r in rows:
        if isinstance(r, dict) and "id" in r:
            out[str(r["id"])] = r
    return out


def main():
    if len(sys.argv) < 3:
        print("用法: python parse_npc_cards.py <luban_export_dir> <output_dir>")
        sys.exit(1)

    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    data_dir = src / "assets" / "luban" / "data"
    if not data_dir.is_dir():
        print(f"[错误] 找不到 {data_dir}")
        sys.exit(1)

    print("加载表...")
    npc_rows = load_json(data_dir / "tbnpcbasecfg.json")
    layer_rows = load_json(data_dir / "tbcultivatelayer.json")
    pers_rows = load_json(data_dir / "tbnpcpersonality.json")
    item_rows = load_json(data_dir / "tbitemtype.json")

    tables = {
        "layer": index_by_id(layer_rows),
        "personality": index_by_id(pers_rows),
        "item": index_by_id(item_rows),
    }

    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"共 {len(npc_rows)} 条 NPC，开始生成...")
    ok = 0
    err = 0
    for npc in npc_rows:
        try:
            content = build_card(npc, tables)
            npc_id = str(npc.get("id", ""))
            npc_name = L10n(npc.get("npcName", {}))
            fname = f"{npc_id}-{safe_name(npc_name)}.md"
            (out_dir / fname).write_text(content, encoding="utf-8")
            ok += 1
        except Exception as e:
            err += 1
            if err < 5:
                print(f"[错误] {e}")

    print(f"完成：成功 {ok} 张，失败 {err} 张")
    print(f"输出目录：{out_dir}")


if __name__ == "__main__":
    main()