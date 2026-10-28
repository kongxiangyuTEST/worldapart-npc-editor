"""角色口吻原型 → 语音基线（actor_profile）映射。

## 数据从哪来

**只读项目自己的角色卡**（`editor_cards/<npcId>-<name>.md` 的「个性」字段），
不引用、不复制 A1IndexTTSMod 的任何角色台账。

1592 张卡片的「个性」字段全部覆盖 14 种原型（与知识库 04/17 一致）：
    天生领袖 407 · 稳重圆融 115 · 务实坚韧 104 · 精明内敛 96
    直言好胜 95 · 刚毅果决 93 · 智谋机变 92 · 机敏进取 89
    逍遥乐天 88 · 忠诚仁爱 87 · 调和权变 86 · 冷静独行 83
    锐意勇进 79 · 热情磊落 78

这与 `tools/apply_voice_cards.py` 的 ARCHETYPES 是同一套定义。

## 为什么用原型映射，而不是按 npc_id 查表

知识库 19「外部参考 · A1-TTS-MOD」明确写了取舍：

> 「口径台账 + 指纹校验」是否引入……**前提是把「逐字精确」改成
> 「先查 14 原型 → 再按 npc_id 精确」，避免重蹈 speakStyle 错配**。

知识库 20「speech_layer」也定了红线：

> 轻量台账：用口吻档×通用描述，**不含任何特定角色台词/身份**，
> 规避 4311 全量条目的**版权与张冠李戴风险**，可安全打进发布包。

所以这里**只做 14 原型 → 基线描述的映射**：输入是用户自己卡片上的个性标签，
输出是「语速/停顿/语气」级别的风格描述，不含任何角色身份信息。

## 映射依据

目标 `PROFILE_DELIVERY` 的 58 条里，能语义对上的挑最贴合的一条；
对不上的不硬凑，回落到 `natural`（自然清晰，中等语速，不夸张表演）。
右侧注释写的是「为什么选这条」。
"""

from __future__ import annotations

from typing import Dict, Optional

# 14 原型 -> 语音基线 profile
# 左侧是卡片「个性」字段的原值，右侧是 voicebook.PROFILE_DELIVERY 的键。
ARCHETYPE_TO_PROFILE: Dict[str, str] = {
    # 短、硬、命令式，很少用问号 → 节奏沉稳、重点笃定、保持掌控感
    "天生领袖": "leader",

    # 温、缓、有耐心（也是回落默认值）→ 语速适中，措辞质朴，语尾踏实
    "稳重圆融": "diplomat",

    # 平铺直叙、不带笑意、活干完就闭嘴 → 话少干脆，落字有力，不绕弯
    "务实坚韧": "hard",

    # 客气但句句留分寸，笑不达眼底 → 语气温和克制，重点轻落，留有余地
    "精明内敛": "subtle",

    # 冲、快、认死理，输了当场认 → 节奏向前，落字果断，热度适中
    "直言好胜": "bold",

    # 沉、少、字字落地 → 节奏沉稳，声音有分量，不摆夸张官腔
    "刚毅果决": "solemn",

    # 慢、稳、话留半句，喜欢反问 → 转折灵活，试探处稍停，判断说清楚
    "智谋机变": "clever",

    # 快、俏、嘴硬，理亏时声音先大起来 → 灵动明快，嘴硬处点到为止
    "机敏进取": "qianer",

    # 松、飘、爱开玩笑 → 轻快松弛，语尾自然，不强加笑声
    "逍遥乐天": "free",

    # 软、暖、先想别人 → 仁厚平实，关切具体，语尾安稳
    "忠诚仁爱": "kind",

    # 软、圆、会打圆场，从不把话说死 → 节奏稳当圆融，语尾舒展，给人留余地
    "调和权变": "diplomat",

    # 淡、少、像在旁观自己说话 → 声音平稳偏冷，短句克制，不额外示好
    "冷静独行": "cold",

    # 亮、直、带冲劲，认定了就往前冲 → 声音明亮，语速稍快，落字清楚利落
    "锐意勇进": "bright",

    # 响、亮、不藏事 → 明朗直接，声音开阔，热心但不喧哗
    "热情磊落": "sun",
}

# 卡片没写个性、或写了不认识的值时的回落
FALLBACK_PROFILE = "natural"

# 兼容写法：卡片里可能带括号补充，如「精明内敛（商人）」→ 取前缀匹配
_ARCHETYPE_ORDER = sorted(ARCHETYPE_TO_PROFILE.keys(), key=len, reverse=True)


def normalize_archetype(raw: Optional[str]) -> Optional[str]:
    """把卡片「个性」字段归一成 14 原型之一。认不出返回 None。

    处理：
    - 去空白、去全角空格
    - 去括号补充（「精明内敛（商人）」→「精明内敛」）
    - 去尾部标点
    """
    s = (raw or "").strip().replace("\u3000", " ")
    if not s:
        return None
    # 去括号及其内容（中英文括号）
    for lb, rb in (("（", "）"), ("(", ")")):
        if lb in s:
            s = s.split(lb, 1)[0]
    s = s.strip().strip("、，,。.；;：: ")
    if s in ARCHETYPE_TO_PROFILE:
        return s
    # 前缀匹配：卡片里写了「天生领袖型」这类
    for arch in _ARCHETYPE_ORDER:
        if s.startswith(arch):
            return arch
    return None


def profile_for_archetype(raw: Optional[str]) -> str:
    """个性字段 → 语音基线 profile 键。认不出回落 natural。"""
    arch = normalize_archetype(raw)
    if not arch:
        return FALLBACK_PROFILE
    return ARCHETYPE_TO_PROFILE.get(arch, FALLBACK_PROFILE)


def all_mappings() -> Dict[str, str]:
    """返回完整原型->profile 映射（编辑器展示用）。"""
    return dict(ARCHETYPE_TO_PROFILE)
