"""把本项目的编排层导出成酒馆（SillyTavern）能吃的两种文件：预设 + 世界书。

## 只做导出，不做导入

需求就是"把我的东西拿到别的平台复现"，所以这里单向输出，不解析酒馆回传。
角色卡导出在 `tools/tavern_card.py`（PNG 里嵌 V2 JSON）。

## 两个产品

**preset-worldapart.json** —— Chat Completion 预设：把我们的 7 层注入翻译成
`prompts[]` + `prompt_order[]`。酒馆按这个顺序渲染，于是"名片在最后、注入语在最前"
这条权重关系能保留下来。

**worldbook-worldapart.json** —— 世界书：把注入语做成一条 `constant: true` 的常驻
条目（等价于我们每轮都注入的行为）。默认**不带正文**——注入语含成人向内容，
要带得显式加 `--with-inject`，避免误把敏感文本打进分享物。

## 用法

    python tools/tavern_export.py -o dist/酒馆导出            # 预设 + 空世界书骨架
    python tools/tavern_export.py -o dist/酒馆导出 --with-inject   # 把注入语正文也写进去

## 为什么采样参数是默认值

`ai_config.json` 里没有 temperature/top_p 这些（我们的请求走游戏给的参数），
所以预设里填的是酒馆默认值，不是"我们调过的值"——这一点必须说明，别让人误会。
"""

import argparse
import json
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_CONFIG = os.path.join(PROJECT, "ai_config.json")
LOREBOOK = os.path.join(PROJECT, "lorebook.json")

# 我们的 7 层 → 酒馆的 identifier。
# 酒馆的 marker 类（worldInfoBefore 等）由它自己喂内容，我们只占位排序。
LAYERS = [
    # (identifier, 名称, 角色, 是否 marker, 对应我们的层)
    ("main", "主提示（我们不用，留空）", "system", False, "—"),
    ("inject_prefix", "WorldApart 注入语", "system", False, "ai_config.inject"),
    ("worldInfoBefore", "世界书（触发式）", "system", True, "lorebook.json"),
    ("charDescription", "角色描述", "system", True, "角色卡 description"),
    ("charPersonality", "角色性格", "system", True, "角色卡 personality"),
    ("personaDescription", "玩家设定", "system", True, "ai_config.persona"),
    ("chatHistory", "对话历史", "system", True, "—"),
    ("user_card", "WorldApart 角色演绎要求（名片）", "system", False, "editor_cards 用户名片"),
    ("jailbreak", "越狱/收尾指令", "system", True, "—"),
]

PRESET_DEFAULTS = {
    "temperature": 0.8,
    "frequency_penalty": 0,
    "presence_penalty": 0,
    "top_p": 0.95,
    "top_k": 0,
    "openai_max_context": 32000,
    "openai_max_tokens": 2000,
    "wrap_in_quotes": False,
    "names_behavior": 0,
    "send_if_ai": False,
    "stream_openai": True,
}


def _load_inject():
    try:
        with open(AI_CONFIG, "r", encoding="utf-8-sig") as f:
            return (json.load(f).get("inject") or "").strip()
    except Exception:
        return ""


def build_preset(with_inject=False):
    inject = _load_inject() if with_inject else ""
    prompts = []
    order = []
    for ident, name, role, marker, src in LAYERS:
        p = {"identifier": ident, "name": name, "role": role,
             "content": "", "system_prompt": False, "marker": marker,
             "enabled": True, "injection_position": 0, "injection_depth": 4,
             "injection_order": 100, "forbid_overrides": False}
        if ident == "inject_prefix":
            p["content"] = inject or (
                "（这里放你的注入语。默认没导出正文——注入语含成人向内容，"
                "需要在项目里用 --with-inject 重新导出一次。）")
        prompts.append(p)
        order.append({"identifier": ident, "enabled": True})

    preset = dict(PRESET_DEFAULTS)
    preset["prompts"] = prompts
    preset["prompt_order"] = [{"character_id": 100000, "order": order}]
    preset["extensions"] = {
        "worldapart_export": {
            "note": "由 WorldApart 人物卡编辑器导出；只复现『我们加了什么 + 相对顺序』，"
                    "不包含游戏原生的 10 段 system。采样参数是酒馆默认值，不是我们调过的值。",
            "layer_order": [{"identifier": i, "source": s} for i, _, _, _, s in LAYERS],
        }
    }
    return preset


def build_worldbook(with_inject=False):
    """注入语 → 一条常驻条目；lorebook.json 有内容就一并搬。"""
    entries = []
    inject = _load_inject() if with_inject else ""
    entries.append({
        "uid": 0,
        "key": [], "keysecondary": [],
        "comment": "WorldApart 注入语（常驻）",
        "content": inject or "（未导出正文：用 --with-inject 重新导出）",
        "constant": True, "selective": True, "selectiveLogic": 0,
        "addMemo": True, "order": 100, "position": 0, "disable": False,
        "excludeRecursion": False, "preventRecursion": False, "delayUntilRecursion": False,
        "probability": 100, "useProbability": True, "depth": 4,
        "group": "", "groupOverride": False, "groupWeight": 100,
        "scanDepth": None, "caseSensitive": None, "matchWholeWords": None,
        "useGroupScoring": None, "automationId": "", "role": 0, "sticky": 0,
        "cooldown": 0, "delay": 0, "extensions": {"worldapart_source": "inject"},
    })

    # 我们自己的 lorebook.json（关键词触发式），有就一并转过去
    try:
        with open(LOREBOOK, "r", encoding="utf-8-sig") as f:
            ours = (json.load(f).get("entries") or [])
    except Exception:
        ours = []
    for e in ours:
        entries.append({
            "uid": len(entries),
            "key": [k for k in (e.get("keywords") or []) if k],
            "keysecondary": [],
            "comment": e.get("title") or "未命名",
            "content": e.get("content") or "",
            "constant": False, "selective": True, "selectiveLogic": 0,
            "addMemo": True, "order": int(e.get("priority") or 100),
            "position": 0, "disable": not bool(e.get("enabled", True)),
            "excludeRecursion": False, "preventRecursion": False, "delayUntilRecursion": False,
            "probability": 100, "useProbability": True, "depth": 4,
            "group": "", "groupOverride": False, "groupWeight": 100,
            "scanDepth": None, "caseSensitive": None, "matchWholeWords": None,
            "useGroupScoring": None, "automationId": "", "role": 0, "sticky": 0,
            "cooldown": 0, "delay": 0, "extensions": {"worldapart_source": "lorebook"},
        })
    return {"entries": entries}


def main():
    ap = argparse.ArgumentParser(description="导出酒馆预设与世界书")
    ap.add_argument("-o", "--out", default=os.path.join(PROJECT, "dist", "酒馆导出"))
    ap.add_argument("--with-inject", action="store_true",
                    help="把注入语正文写进去（含成人向内容，分享前慎用）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    preset = build_preset(args.with_inject)
    p1 = os.path.join(args.out, "preset-worldapart.json")
    with open(p1, "w", encoding="utf-8") as f:
        json.dump(preset, f, ensure_ascii=False, indent=2)

    wb = build_worldbook(args.with_inject)
    p2 = os.path.join(args.out, "worldbook-worldapart.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(wb, f, ensure_ascii=False, indent=2)

    print(f"预设：{p1}（{len(preset['prompts'])} 个块）")
    print(f"世界书：{p2}（{len(wb['entries'])} 条）"
          + ("" if args.with_inject else "　— 正文未导出，加 --with-inject"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
