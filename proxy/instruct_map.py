"""关键词 → Breeze instruct 映射（台词里出现"低声/吼"，就让声音跟着变）。

## 先看实测结论（2026-10-30，3 个参考音 × 13 条指令，seed 固定）

只看"方向稳不稳"（三个参考音同向才算稳）：

| 指令 | F0 变化 | 高频变化 | 稳不稳 |
|---|---|---|---|
| 低声 / deep voice | −5% ~ −21% | 基本不动 | **稳，三组都降** |
| 气声 / whisper | −5% ~ −26% | −0.4 ~ +1.6pt | **稳，三组都降** |
| 喊 / shout | −0.7 ~ +15% | **+1.6 ~ +3.7pt** | **稳，三组都变亮** |
| 高音 / high voice | −8% ~ +15% | 不动 | 不稳，只对女声有效 |
| 温柔 / gentle | −10% ~ −11% | 不动 | 不稳（时长有 +18% 也有 −6%） |
| 哭腔 / tearful | −3% ~ −8% | +1.1 ~ +3.2pt | 不稳 |
| 笑 / laughing | −8% ~ +4% | 不动 | 不稳，时长 +16% 只出现一次 |
| 慢 / 快 | 时长 −12% ~ +6% | — | **无效**，时长由文本决定 |
| 中文指令 | −9% ~ −19% | 不动 | 全部当"变沉"处理，语义没被解析 |

补测六条（同样 3 个参考音，只列三组同向的）：

| 指令 | F0 三组 | 高频 | 时长 | 结论 |
|---|---|---|---|---|
| 威胁 / threatening menace | −6.9 / −8.5 / −4.8 % | −2.4 ~ +1.4pt | −2.8 ~ +14% | 同向，但就是"变沉"的弱版 |
| 疲惫 / exhausted | −4.3 / −21.1 / −11.2 % | 基本不动 | −1.5 ~ +2.8% | 同向，可当"有气无力" |
| 冷笑 / sarcastic | +9.2 / −5.5 / −6.5 % | −3.9 ~ −0.2pt | 0 ~ +7.9% | **不同向，别用** |
| 急促 / panicked | −1.5 / −4.7 / +2.6 % | 不动 | −4.2 ~ 0% | 不同向 |
| 威严 / commanding | −1.5 / −9.0 / +2.6 % | 不动 | −2.8 ~ +11% | 不同向 |
| 慵懒 / drawling | +5.8 / −5.5 / −8.6 % | +0.5 ~ +1.1pt | −3.2 ~ +5.6% | 不同向 |

冷嘲/嘲讽单独试了六种说法（cold dismissive sneer / biting sarcasm / restrained contempt /
mocking half-laugh / low mocking tone / 中文"冷冷地笑了一声"）：只有 "coldly, with restrained
contempt" 三组同向，但两个参考音只差 −1.5% / −2.5%（等于噪声）→ **嘲讽本身没有稳定声学特征**。
处理办法：把「冷笑/讥/嘲讽」挂到 low 轴（冷笑通常就是压低声音），至少确定性变沉。

结论：**能可靠驱动的只有两个轴 —— F0（变沉/气声/疲惫/威胁，全是往下）和高频（喊叫变亮）**。
同一轴上换个说法只是幅度不同，别指望模型区分"阴冷"和"疲惫"的质感。
语速一律走后处理（WSOLA），温柔/哭/笑这类"情绪色彩"别指望 instruct，
要效果就叠 EQ + 音量——那是确定性的。

## 另一条硬规矩

instruct **必须放在 options 里**。放顶层（`{"instruct": "..."}`）会被静默忽略——
实测顶层指令与完全不传的 md5 一模一样，日志里没有任何提示。
"""

# 默认只放"方向稳定"的三类。keywords 里同时给中英文，游戏台词是中文，英文留着给手填场景。
RULES = [
    {
        "key": "whisper",
        "keywords": ["耳语", "低语", "小声", "压低声音", "压低嗓子", "呢喃",
                     "嘟囔", "气声", "whisper", "murmur"],
        "instruct": "Speak in a soft, breathy whisper.",
    },
    {
        "key": "low",
        "keywords": ["低沉", "沉声", "沉下声", "闷声", "低低地", "哑着嗓子",
                     "低声", "放低声音", "压着嗓子", "压低声线",
                     "deep voice", "low voice"],
        "instruct": "Speak in a low, deep voice.",
    },
    {
        # 冷嘲没有独立特征（见文件头第三轮实测），借 low 轴：冷笑通常就是压低声音
        "key": "sneer",
        "keywords": ["冷笑", "讥", "嘲讽", "嘲讽地", "嗤", "阴阳怪气", "阴阳",
                     "sneer", "sarcast", "mock"],
        "instruct": "Speak in a low, deep voice.",
    },
    {
        "key": "tired",
        "keywords": ["疲惫", "有气无力", "虚弱", "叹了口气", "叹气", "倦",
                     "exhausted", "tired"],
        "instruct": "Speak in a tired, exhausted voice, as if out of breath.",
    },
    {
        "key": "threat",
        "keywords": ["威胁", "阴冷", "冷冷地", "冷哼", "森然", "压得人",
                     "menacing", "threaten"],
        "instruct": "Speak with a cold, threatening menace.",
    },
    {
        "key": "shout",
        "keywords": ["吼", "咆哮", "怒吼", "喊叫", "大叫", "厉声", "呵斥",
                     "喝道", "怒喝", "shout", "yell"],
        "instruct": "Shout angrily, with force.",
    },
]

# 实测方向不稳，默认不启用；留着当"试过但别指望"的记录，也方便以后复测对照。
EXPERIMENTAL = [
    {"key": "sarcastic", "keywords": ["冷笑", "讥", "嘲讽", "阴阳怪气"],
     "instruct": "Speak with a cold, mocking, sarcastic edge.",
     "why": "F0 三组 +9.2 / −5.5 / −6.5，方向相反，别启用"},
    {"key": "high", "keywords": ["尖", "高音", "清脆"],
     "instruct": "Speak in a high, bright voice.",
     "why": "只对女声有效，男声反而降 6%"},
    {"key": "gentle", "keywords": ["温柔", "轻柔", "轻声细语", "安抚", "哄"],
     "instruct": "Speak in a gentle, tender tone.",
     "why": "F0 都降但幅度与时长不一致，不如用 EQ"},
    {"key": "tearful", "keywords": ["哭腔", "哽咽", "颤抖", "带着哭"],
     "instruct": "Speak with a trembling, tearful voice.",
     "why": "高频变化 +1.1~+3.2pt，方向对但幅度不稳"},
    {"key": "laugh", "keywords": ["笑着", "轻笑", "笑意"],
     "instruct": "Speak with a smile in the voice.",
     "why": "只有一处时长 +16%，其余无差别"},
]

MODES = ("off", "fill", "override", "append")


def _rules(rules):
    """把配置里的自定义规则规整成同一种形状；空则用默认表。"""
    if not rules:
        return RULES
    out = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        kws = [str(k).strip() for k in (r.get("keywords") or []) if str(k).strip()]
        ins = str(r.get("instruct") or "").strip()
        if kws and ins:
            out.append({"key": str(r.get("key") or "custom"), "keywords": kws,
                        "instruct": ins})
    return out or RULES


def match(text, rules=None):
    """在台词里找命中的关键词。多个命中时取"词最长"的那个。

    为什么取最长：避免「低」把「低语」抢走（虽然当前表里没有单字），
    也方便以后加更细的词。返回 (key, instruct) 或 None。
    """
    t = (text or "")
    if not t:
        return None
    best = None
    for r in _rules(rules):
        for kw in r["keywords"]:
            if kw and kw in t:
                if best is None or len(kw) > best[2]:
                    best = (r["key"], r["instruct"], len(kw))
    return (best[0], best[1]) if best else None


def decide(text, base_instruction="", mode="fill", rules=None):
    """算最终 instruct。

    mode:
        off       —— 原样返回 base（等于关掉）
        fill      —— MOD 没给指令时才补（最保守，不抢 MOD/表现层的活）
        override  —— 命中就覆盖 MOD 给的指令
        append    —— 命中就拼在 MOD 指令后面（未实测组合效果，慎用）

    返回 (instruct, matched_key|None)。
    """
    base = (base_instruction or "").strip()
    if mode not in MODES or mode == "off":
        return base, None
    hit = match(text, rules)
    if not hit:
        return base, None
    key, phrase = hit
    if mode == "fill":
        return (base or phrase), (None if base else key)
    if mode == "override":
        return phrase, key
    # append
    return ((base + " " + phrase).strip() if base else phrase), key
