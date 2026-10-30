"""角色卡与酒馆（SillyTavern）格式互转：md ↔ Character Card V2（JSON / 嵌进 PNG）。

## 为什么这么做

酒馆的角色卡就是一张 PNG，`tEXt` 块里塞了 base64 的 JSON（关键字 `chara`），
JSON 遵循公开的 Character Card V2 规范。所以「导出/导入」不需要任何第三方库：
PNG 的块结构 + zlib.crc32 + base64 就够，纯标准库。

## 字段对应（我们的 md 段落 → 酒馆字段）

| editor_cards md            | V2 字段                        |
|----------------------------|--------------------------------|
| `# 名字` / `- 姓名`        | `data.name`                    |
| `## 预设资料` 的条目       | `data.personality`（拼成一行行）|
| `## 原游戏背景`            | `data.description`             |
| `## 用户名片`              | `data.system_prompt`（作为人设补充）+ `creator_notes` 里留备份 |
| 个性                       | `data.tags`                    |
| NPC ID                     | `data.extensions.wa_npc_id`    |
| 无对应                     | `scenario` / `first_mes` / `mes_example` 留空（游戏里台词是动态生成的）|

导出时**不**凭空编台词：酒馆的 `first_mes` / `mes_example` 留给使用者，
我们只保证人设、背景、口吻这些「已有信息」搬过去不走样。

## 用法

    python tools/tavern_card.py export 100000          # 导出单张（JSON+PNG，头像来自 web/portraits）
    python tools/tavern_card.py export --all -o out/   # 批量
    python tools/tavern_card.py import 卡.png          # 反向：酒馆卡 → md（打印或落 editor_cards）
"""

import argparse
import base64
import json
import os
import re
import struct
import sys
import zlib

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(PROJECT, "editor_cards")
PORTRAITS_DIR = os.path.join(PROJECT, "web", "portraits")

CARD_RE = re.compile(r"^(\d+)-(.+)\.md$")
SPEC = "chara_card_v2"
SPEC_VERSION = "2.0"


# ==================== md 解析 ====================
def parse_card(md_text):
    """把 md 拆成 name / attrs / user_card / background。"""
    name = ""
    attrs = {}
    user_card = []
    background = []
    section = ""
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if line.startswith("# ") and not name:
            name = line[2:].strip()
            continue
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            section = m.group(1)
            continue
        if line.startswith("- ") and "预设资料" in section:
            kv = line[2:]
            for sep in ("：", ":"):
                if sep in kv:
                    k, v = kv.split(sep, 1)
                    attrs[k.strip()] = v.strip()
                    break
            continue
        if "用户名片" in section:
            if line.startswith("<!--") or line.endswith("-->"):
                continue
            user_card.append(line)
        elif "原游戏背景" in section:
            background.append(line)
    return {
        "name": name or attrs.get("姓名", ""),
        "attrs": attrs,
        "user_card": "\n".join(user_card).strip(),
        "background": "\n".join(background).strip(),
    }


def card_to_v2(card, npc_id):
    """md → Character Card V2 的 data 部分。"""
    attrs = card["attrs"]
    personality = "；".join(
        f"{k}：{v}" for k, v in attrs.items() if k not in ("姓名",) and v
    )
    tags = [t for t in (attrs.get("个性", ""), attrs.get("性别", "")) if t]
    return {
        "name": card["name"],
        "description": card["background"],
        "personality": personality,
        "scenario": "",
        "first_mes": "",
        "mes_example": "",
        "creator_notes": "由 WorldApart 人物卡编辑器导出；NPC ID " + str(npc_id)
                         + ("；用户名片原文见 system_prompt" if card["user_card"] else ""),
        "system_prompt": card["user_card"],
        "post_history_instructions": "",
        "alternate_greetings": [],
        "tags": tags,
        "creator": "WorldApart NPC Card Editor",
        "character_version": "1.0",
        "extensions": {"wa_npc_id": npc_id, "wa_source": "editor_cards",
                       # 原始属性表：酒馆那边没用，但回导时靠它还原
                       "wa_attrs": dict(attrs)},
    }


def v2_to_card_data(v2):
    """酒馆卡 → 我们的字段（用于导入）。"""
    d = v2.get("data", v2)
    return {
        "name": d.get("name", ""),
        "description": d.get("description", ""),
        "personality": d.get("personality", ""),
        "system_prompt": d.get("system_prompt", ""),
        "creator_notes": d.get("creator_notes", ""),
        "tags": d.get("tags", []),
    }


# ==================== PNG tEXt 读写 ====================
def _chunks(buf):
    """逐块遍历 PNG，产出 (type, start, end)。"""
    pos = 8
    while pos + 8 <= len(buf):
        length = struct.unpack(">I", buf[pos:pos + 4])[0]
        ctype = buf[pos + 4:pos + 8]
        yield ctype, pos, pos + 12 + length
        pos += 12 + length


def png_extract_card(path):
    """从 PNG 里取角色卡 JSON（关键字 chara 或 ccv3）。"""
    with open(path, "rb") as f:
        buf = f.read()
    if buf[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG")
    for ctype, start, end in _chunks(buf):
        if ctype != b"tEXt":
            continue
        body = buf[start + 8:end - 4]
        key, _, val = body.partition(b"\x00")
        if key in (b"chara", b"ccv3"):
            return json.loads(base64.b64decode(val).decode("utf-8"))
    raise ValueError("PNG 里没有 chara 块")


def png_embed_card(src_png, dst_png, card_json_text):
    """把角色卡写进 PNG 的 tEXt(chara)，插在 IHDR 之后。"""
    with open(src_png, "rb") as f:
        buf = f.read()
    if buf[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("底图不是 PNG")
    payload = b"chara\x00" + base64.b64encode(card_json_text.encode("utf-8"))
    chunk = (struct.pack(">I", len(payload)) + b"tEXt" + payload
             + struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF))
    # 插在 IHDR（第一个块）之后
    first = next(_chunks(buf))
    cut = first[2]
    with open(dst_png, "wb") as f:
        f.write(buf[:cut] + chunk + buf[cut:])


# ==================== 导出 / 导入 ====================
def _portrait(npc_id):
    for ext in (".png", ".webp", ".jpg"):
        p = os.path.join(PORTRAITS_DIR, f"{npc_id}{ext}")
        if os.path.isfile(p):
            return p
    return None


# 中文字体：PIL 自带的位图字体没有汉字，不指定就全是方块
_FONT_CANDIDATES = [
    r"C:/Windows/Fonts/msyh.ttc",      # 微软雅黑
    r"C:/Windows/Fonts/msyhl.ttc",
    r"C:/Windows/Fonts/simhei.ttf",    # 黑体
    r"C:/Windows/Fonts/simsun.ttc",    # 宋体
]


def _font(size):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _placeholder_png(dst, name, npc_id):
    """没有头像时生成一张占位底图：暗底 + 名字。没有 PIL 就跳过。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGB", (400, 600), (38, 34, 30))
    d = ImageDraw.Draw(img)
    d.rectangle([16, 16, 384, 584], outline=(200, 160, 106), width=2)
    d.text((200, 292), name or "(未命名)", fill=(232, 222, 206),
           font=_font(40), anchor="mm")
    d.text((200, 340), f"NPC {npc_id}", fill=(150, 140, 125),
           font=_font(20), anchor="mm")
    img.save(dst)
    return dst


def export_one(md_path, out_dir):
    m = CARD_RE.match(os.path.basename(md_path))
    if not m:
        return None
    npc_id, fname_name = int(m.group(1)), m.group(2)
    with open(md_path, "r", encoding="utf-8") as f:
        card = parse_card(f.read())
    data = card_to_v2(card, npc_id)
    v2 = {"spec": SPEC, "spec_version": SPEC_VERSION, "data": data}
    text = json.dumps(v2, ensure_ascii=False, indent=2)

    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{fname_name}.json")
    with open(base, "w", encoding="utf-8") as f:
        f.write(text)

    png_out = None
    portrait = _portrait(npc_id)
    if portrait and portrait.lower().endswith(".png"):
        png_out = os.path.join(out_dir, f"{fname_name}.png")
        png_embed_card(portrait, png_out, text)
    elif portrait is None:
        tmp = os.path.join(out_dir, f"_{npc_id}_base.png")
        if _placeholder_png(tmp, data["name"], npc_id):
            png_out = os.path.join(out_dir, f"{fname_name}.png")
            png_embed_card(tmp, png_out, text)
            os.remove(tmp)
    return {"npc_id": npc_id, "json": base, "png": png_out,
            "name": data["name"], "desc_len": len(data["description"])}


def import_one(path, write_dir=None):
    """酒馆卡（PNG 或 JSON）→ 我们的 md 结构（打印 / 或写回 editor_cards）。"""
    if path.lower().endswith(".png"):
        v2 = png_extract_card(path)
    else:
        with open(path, "r", encoding="utf-8") as f:
            v2 = json.load(f)
    d = v2_to_card_data(v2)
    npc_id = (v2.get("data", v2).get("extensions") or {}).get("wa_npc_id")
    if npc_id is None:
        return {"ok": False, "msg": "卡里没有 wa_npc_id，无法确定写回哪个角色"}
    attrs = (v2.get("data", v2).get("extensions") or {}).get("wa_attrs") or {}
    if attrs:
        attr_lines = [f"- {k}：{v}" for k, v in attrs.items()]
    else:
        attr_lines = [f"- 姓名：{d['name']}", f"- 个性：{d['personality']}"]
    lines = [f"# {d['name']}", "", f"NPC ID：{npc_id}", "",
             "## 用户名片（优先读取，可自行增添）", "", d["system_prompt"] or "", "",
             "## 预设资料（可修改；导出时快照）", "", *attr_lines,
             "", "## 原游戏背景", "", d["description"] or ""]
    text = "\n".join(lines).rstrip() + "\n"
    if not write_dir:
        print(text)
        return {"ok": True, "npc_id": npc_id, "wrote": None}
    os.makedirs(write_dir, exist_ok=True)
    out = os.path.join(write_dir, f"{npc_id}-{d['name']}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    return {"ok": True, "npc_id": npc_id, "wrote": out}


def main():
    ap = argparse.ArgumentParser(description="角色卡 ↔ 酒馆 Character Card V2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="md → JSON/PNG")
    e.add_argument("npc", nargs="?", help="NPC ID，或 --all")
    e.add_argument("--all", action="store_true")
    e.add_argument("-o", "--out", default=os.path.join(PROJECT, "dist", "酒馆角色卡"))
    i = sub.add_parser("import", help="酒馆卡 → md")
    i.add_argument("file")
    i.add_argument("-o", "--out", help="写回目录，默认只打印")
    args = ap.parse_args()

    if args.cmd == "export":
        if args.all:
            n = 0
            for fn in sorted(os.listdir(CARDS_DIR)):
                if not fn.endswith(".md"):
                    continue
                r = export_one(os.path.join(CARDS_DIR, fn), args.out)
                if r:
                    n += 1
            print(f"已导出 {n} 张 → {args.out}")
            return 0
        if not args.npc:
            print("要给 NPC ID，或用 --all")
            return 1
        target = [fn for fn in os.listdir(CARDS_DIR)
                  if fn.startswith(str(args.npc) + "-") and fn.endswith(".md")]
        if not target:
            print(f"找不到 {args.npc} 的卡片")
            return 1
        r = export_one(os.path.join(CARDS_DIR, target[0]), args.out)
        print(f"已导出：{r['json']}" + (f"\n         {r['png']}" if r["png"] else "（无头像，只出 JSON）"))
        return 0

    r = import_one(args.file, args.out)
    if not r.get("ok"):
        print(r.get("msg"))
        return 1
    if r.get("wrote"):
        print(f"已写回 {r['wrote']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
