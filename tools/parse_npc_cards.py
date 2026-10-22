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


# 「用户名片」是唯一由人写、又要注入给游戏的内容。
# 导出角色卡时游戏字段全部重建，但名片必须原样搬过来，
# 否则每次导出都会把用户写的东西清空（历史问题，踩过一次就找不回来）。
CARD_USER_SECTION = re.compile(
    r'(^##[ \t]*用户名片[^\n]*\n)(.*?)(?=^##[ \t]|\Z)', re.S | re.M
)


def _user_card_body(text):
    """取一张卡片里「用户名片」的正文（去掉 HTML 注释），空则返回空串。"""
    m = CARD_USER_SECTION.search(text)
    if not m:
        return ""
    return re.sub(r'<!--.*?-->', '', m.group(2), flags=re.S).strip()


def preserve_user_card(target, new_content):
    """把 target 里已有的用户名片搬进新生成的内容；没有就原样返回。"""
    try:
        if not target.is_file():
            return new_content
        old_text = target.read_text(encoding="utf-8")
    except Exception:
        return new_content

    mo = CARD_USER_SECTION.search(old_text)
    if not mo:
        return new_content
    raw_body = mo.group(2)
    # 只有注释、没有实际内容 → 认为没写过，保持新模板
    if not re.sub(r'<!--.*?-->', '', raw_body, flags=re.S).strip():
        return new_content

    mn = CARD_USER_SECTION.search(new_content)
    if not mn:
        return new_content
    # 连注释一起搬，保留"在此填写…"的提示
    body = raw_body.strip()
    return new_content[:mn.start(2)] + "\n" + body + "\n\n" + new_content[mn.end(2):]


def parse_cards(src, out_dir, log=None):
    """供外部调用。src: Luban 导出目录，out_dir: 输出目录"""
    def _log(msg):
        if log: log(msg)
        else: print(msg)

    src = Path(src)
    out_dir = Path(out_dir)
    data_dir = src / "assets" / "luban" / "data"
    if not data_dir.is_dir():
        _log(f"[错误] 找不到 {data_dir}")
        return {"ok": False, "msg": f"找不到 {data_dir}"}

    _log("加载表...")
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

    _log(f"共 {len(npc_rows)} 条 NPC，开始生成...")
    ok = 0
    err = 0
    for npc in npc_rows:
        try:
            content = build_card(npc, tables)
            npc_id = str(npc.get("id", ""))
            npc_name = L10n(npc.get("npcName", {}))
            fname = f"{npc_id}-{safe_name(npc_name)}.md"
            target = out_dir / fname
            content = preserve_user_card(target, content)
            target.write_text(content, encoding="utf-8")
            ok += 1
        except Exception as e:
            err += 1
            if err < 5:
                _log(f"[错误] {e}")

    _log(f"完成：成功 {ok} 张，失败 {err} 张")
    _log(f"输出目录：{out_dir}")
    return {"ok": True, "done": ok, "failed": err}


def main():
    if len(sys.argv) < 3:
        print("用法: python parse_npc_cards.py <luban_export_dir> <output_dir>")
        sys.exit(1)
    r = parse_cards(sys.argv[1], sys.argv[2])
    sys.exit(0 if r.get("ok") else 1)

if __name__ == "__main__":
    main()