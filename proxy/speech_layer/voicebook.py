"""说话表现层 · 轻量口吻账户（voicebook）。

只收录*通用说话方式*（profile/performance -> delivery 描述），不收录任何特定
角色的台词、身份或专属表演。因此零版权风险、可随包分发。

## 数据来源与两层结构

数据来自 A1IndexTTSMod 内置 `preset-voice-styles.zh-CN.json`（4311 条台账、
1278 角色档案）的实证分析，详见 `docs/reverse/A1IndexTTSMod-语音契约-权威逆向.md`。

官方 delivery 是**两层用中文分号「；」拼接**：

    角色基线（来自 actor_profile，58 种）
    ；
    逐句表演（来自 performance_profile，48 种）

    例：中等语速，成熟从容，停顿有分寸；轻描淡写地自述，语气诚恳，不卑躬屈膝

58 × 48 = 646 种实际组合，4311 条台账全部落在其中。

- 第一层 `PROFILE_DELIVERY`（58 条）：**profile 名与文案均逐字对齐官方原文**，
  由脚本从台账反查得出（每个 profile 恰好对应 1 条 delivery，无一对多）。
- 第二层 `PERFORMANCE_DELIVERY`：官方 48 条里 `humble`(1946) + `intro`(979)
  占 68%，因为台账主体是「基础招呼 + 自我介绍」这类预设脚本，真实对话里
  几乎不出现。所以本层**保留官方措辞风格，但按真实对话场景重配**，
  并补上官方缺失的对话态（拒绝/警告/安抚/争执/自白…）。

## 设计约束

- 所有文案只描述「语速/音量/停顿/气息/句尾/重音」，不写台词、不写故事、
  不写音色身份 → 与官方约束一致，也不构成原创台词表达。
- 每条尽量带一句「不……」的负向约束（官方风格：不卑躬屈膝 / 不强加笑声 /
  不连续逼问），这是防止 TTS 演过头的主要手段。
- 拼接后总长须 ≤ `contract.DELIVERY_MAX`（80 字），超出由
  `normalize_delivery()` 硬截。官方均值约 51 字（16 + 1 + 34），留有余量。
"""

from __future__ import annotations

from typing import Dict, Optional

# ---- 第一层：角色基线（58 条，逐字对齐官方 characters[].delivery）---------
#
# profile 名是游戏/台账里的真实分类名（含 niaoqiao / qianer / taosu / yunyan
# 这类特定角色名），不要自行改名或造别名。

PROFILE_DELIVERY: Dict[str, str] = {
    "adaptive": "节奏有收放，重点清楚，态度有分寸",
    "ambitious": "语速稍快，目标明确，重音灵活",
    "ancient": "古雅简练，字句有分量，收尾干脆",
    "aunt": "声音爽利，关切有力度，日常交谈不喊叫",
    "blunt": "短句干脆，不铺陈寒暄，句尾明确",
    "bold": "语速略快，音量稍大，重音清楚，句尾轻收，不含糊",
    "bright": "语速略快，音量稍大，句尾轻收，不拖尾音",
    "cantonese": "按原文粤语读音自然说，节奏爽利不模仿腔调",
    "child_bright": "清脆明亮，短句轻快，保持孩子式直率",
    "child_puzzled": "清亮朴实，疑问自然，不装成熟",
    "clever": "语速中等，句中短停，句尾轻扬，不一口气说完",
    "cold": "语速略慢，音量稍轻，停顿较多，句尾平直不升温",
    "diplomat": "语速适中，音量正常，停顿匀称，句尾平收，不抢话头",
    "earth": "熟人闲谈的短句，朴实直给，重音稳当",
    "elder": "语速稍慢，朴实亲切，停顿后再接下句",
    "encourage": "气息充足，语速利落，鼓励有劲不吼叫",
    "free": "语速轻快，音量适中，句尾自然散开，语气松，不刻意笑",
    "frightened": "声音稍轻，短句略急，保持清楚",
    "guide": "平实地说明，语速适中，询问不催促",
    "hard": "语速略快，句子短，句尾轻收，停顿较少，不拖泥带水",
    "hawker": "招呼声响亮，节奏有起伏，夸张适度",
    "hero": "爽快开阔，带前辈式亲切，输赢都说得干脆",
    "hostess": "中等语速，成熟从容，停顿有分寸",
    "innocent": "轻声柔和，疑问坦诚，表达不装老成",
    "kind": "语速稍慢，音量正常，句尾柔软上托，气息温和，不喊不劝",
    "leader": "语速稍缓，音量稍大，句尾平稳下压，不乱加语气",
    "leisure": "语速稍慢，松弛随性，反问不尖刻",
    "lively_elder": "亲切而有长辈分寸，笑意自然，语气有起伏",
    "natural": "自然清晰，中等语速，不夸张表演",
    "niaoqiao": "清亮俏皮，短句利落，笑意自然，认真处收住玩笑",
    "official": "短句稳当，清楚直接，停顿后再落重点",
    "patient": "声音柔和，语速舒缓，说明循序渐进",
    "pet": "保持原有短促兽鸣，不添加人声或台词",
    "plain_master": "像日常闲谈，音量平常，重点不容含混",
    "proud_cold": "语气清冷，收尾笃定，锋芒克制",
    "qianer": "语速略快，音量正常，句尾轻扬，不拖长音",
    "quirky": "节奏灵动，转折自然，带轻微笑意",
    "reserved": "惜字如金，先短暂停顿，再答重点",
    "rough": "字句粗粝干脆，有力度但不嘶吼",
    "sharp": "字句清楚锋利，反问利落，不尖叫",
    "shy": "轻声稍慢，表达清楚，停顿自然不故意结巴",
    "silky": "声音柔和，尾音轻扬，笑意含蓄不拖腔",
    "sly": "语气机巧，转折略停，笑意不外露",
    "soft": "语声柔和通透，节奏自然，关心有分寸",
    "solemn": "语速稍慢，音量稍低，停顿较多，句尾平稳落下，不轻飘",
    "student": "音量稍轻，想清楚再说，认真而不僵硬",
    "subtle": "语速略慢，音量稍轻，停顿稍多，句尾平放，不抬高声音",
    "sun": "语速稍快，音量稍大，句尾舒展，不尖不吵",
    "suspicious": "语速稍缓，疑问重音清楚，留有距离",
    "sword": "简洁直切，重音明确，声音稳而不硬喊",
    "taosu": "朴实少言，认真直接，谈熟悉事物时自然放松",
    "tea": "温润舒缓，留出自然停顿，重音轻而稳",
    "veteran": "像阅历深的长者，短句留白，分量内敛",
    "waiter": "语速稍快，先热情招呼，转折灵活",
    "wanderer": "闲散自然，句子简短，关键提醒稍放慢",
    "wary": "音量适中，停顿审慎，不轻易露底",
    "worker": "语速适中，措辞质朴，语尾踏实",
    "yunyan": "清冷而不生硬，措辞清楚，关心克制而真切",
}

NORMAL_PROFILE = "natural"
NORMAL = PROFILE_DELIVERY[NORMAL_PROFILE]

# 常见别名归一（游戏/编辑器里可能出现的变体写法）
_ALIASES = {
    "warm": "kind", "cool": "cold", "wise": "natural",
    "playful": "free", "stern": "leader", "lenient": "patient",
    "old": "elder", "kid": "child_bright", "child": "child_bright",
    "merchant": "hawker", "vendor": "hawker", "boss": "leader",
    "scholar": "ancient", "noble": "official", "soldier": "sword",
    "beast": "pet", "animal": "pet", "maid": "hostess",
}


# ---- 第二层：逐句表演（对话场景，28 条）----------------------------------
#
# 命名沿用官方 performance_profile 的英文风格（ask / care / think / serious …），
# 但剔除官方那批「预设招呼专用」（humble / intro / welcome / trade …），
# 补上真实对话需要的对话态（refuse / warn / comfort / quarrel / confess …）。

PERFORMANCE_DELIVERY: Dict[str, str] = {
    # —— 中性基线（最高频，对应官方 humble/intro 的位置）——
    "neutral": "以自然语速平实说，重点清楚，不额外加戏",

    # —— 问答 ——
    "ask": "重点落在所问之处，句尾轻微上扬，不连续逼问",
    "answer": "先把事情说清，句间稍停，重音稳定，不拖泥带水",
    "doubt": "疑点稍作强调，询问克制，语尾留有余地，不逼对方立刻答",

    # —— 思考 ——
    "think": "边想边说，疑问前自然停顿，不故作高深",
    "realize": "说到关键处稍顿，语气由平转实，不惊呼",

    # —— 关切 / 安抚 ——
    "care": "关心落在具体话上，问句放慢，问完留半拍，不演成训斥",
    "comfort": "语速放慢，句句说完留半拍，坦诚不撒娇",
    "reassure": "语速略缓，句尾平收，接住心情不过度煽情",

    # —— 严肃 / 警告 ——
    "serious": "说得有分量，转折处短停，不摆腔",
    "warn": "提醒处稍放慢，危险重点说清，不渲染恐怖",
    "threat": "威胁处放慢，重音压实，句尾短促收住，不吼叫",
    "refuse": "界限说得明确，语尾干脆，不额外挑衅",
    "order": "要求说得直接，句尾收住，不解释过多",

    # —— 冲突 ——
    "angry": "压着怒气仍保持礼貌，落字加重，不吼叫",
    "cold_stare": "重点前停一下，句尾平直收住，不故作阴森",
    "quarrel": "短句带急切，语速偏快，句句顶住不尖叫",

    # —— 情绪低沉 ——
    "sad": "放慢语速，难处留短停顿，克制不哭喊",
    "tired": "语速略慢，句间停顿稍长，话仍说清楚，不刻意叹气",
    "regret": "承认不足时稍停，语速放慢，不自怨自艾",
    "relief": "先松一口气般放缓，后半句安稳自然，不夸张感慨",

    # —— 亲近 / 情感 ——
    "confess": "关键心意放慢说清，停顿自然，不夸张煽情",
    "affection": "语速放慢，句尾轻收，不撒娇不拖腔",
    "joke": "略带笑意，转折轻快，包袱处稍停，不故意大笑",
    "tease": "笑意浅，试探处稍停，尾句轻扬而不刻薄",

    # —— 回忆 / 叙事 ——
    "memory": "谈旧事稍放缓，转折留白，情绪收着说不外露",
    "explain": "解释清楚，步骤分明，提醒不训人",
    "share": "像把好事讲给熟人，语速轻快，细节处稍提兴致，不炫耀",

    # —— 请求 / 客套 ——
    "request": "请求说得直接有礼，末尾给对方回应空间，不催促",
    "respect": "称呼清楚有礼，语气稳当，不夸张奉承",
    "thanks": "谢意说实，重点稍放慢，情绪克制不客套",
    "casual": "像熟人随口闲聊，语尾放松，起伏自然不做作",

    # —— 戒备 / 其他 ——
    "wary": "语速略慢，重点前短停，保持戒心不急促",
    "praise": "赞赏真切而克制，追问带兴趣，不一味奉承",
    "proud": "成果处语速稍快，收尾短促笃定，不炫耀叫嚷",
    "challenge": "挑战说得利落，末尾有劲，点到为止不叫阵",
}

PERFORMANCE_ALIASES: Dict[str, str] = {
    # 官方 performance_profile -> 本层
    "humble": "neutral", "intro": "neutral", "welcome": "casual",
    "teach": "explain", "trade": "casual", "ask": "ask",
    "firm": "refuse", "amused": "joke", "play": "tease",
    "curious": "ask", "philosophy": "think", "frustrated": "angry",
    "shy": "affection", "deep": "affection", "excited": "joke",
    "conspiracy": "wary", "pet": "neutral", "whisper": "affection",
    # 口语/变体
    "question": "ask", "reply": "answer", "worry": "care",
    "soothe": "comfort", "deny": "refuse", "rage": "angry",
    "grief": "sad", "love": "confess", "sweet": "affection",
    "fun": "joke", "mock": "tease", "reminisce": "memory",
    "inform": "explain", "beg": "request", "polite": "respect",
    "chat": "casual", "guard": "wary", "greet": "casual",
    "retort": "quarrel", "argue": "quarrel", "scold": "angry",
    "sigh": "tired", "calm": "neutral", "plain": "neutral",
}

NORMAL_PERFORMANCE = "neutral"


# ---- 高频克制情绪词表 ----------------------------------------------------
#
# 官方 8622 个 emotion_tags 实例去重后仅 74 词，且「平静」(2187) + 「谦和」(1946)
# 占 48%。把高频词单列，供注入提示词引导模型「主标签优先从这里选」，
# 次标签才放开自由描述 —— 既保住表达力，又贴近原版分布。

EMOTION_TAGS_CORE = (
    "平静", "谦和", "温和", "认真", "审慎", "好奇", "坚定", "自信", "沉着",
    "关切", "从容", "沉思", "耐心", "克制", "期待", "轻松", "自然", "愉悦",
    "坦率", "随性", "怀念", "感慨", "直率", "可靠", "稳妥", "冷静", "询问",
)


# ---- 组装 ---------------------------------------------------------------

def performance_for(name: Optional[str]) -> str:
    """把 performance 名归一成本层存在的键；未知回落到 neutral。"""
    p = (name or "").strip().lower()
    if not p:
        return NORMAL_PERFORMANCE
    if p in PERFORMANCE_DELIVERY:
        return p
    alias = PERFORMANCE_ALIASES.get(p)
    if alias and alias in PERFORMANCE_DELIVERY:
        return alias
    return NORMAL_PERFORMANCE


def delivery_for(profile: Optional[str], npc_name: Optional[str] = None) -> str:
    """根据 actor_profile（可空别名归一）返回**第一层**角色基线描述。

    参数：
        profile: actor_profile 英文分类，可为 None（回落到 natural）
        npc_name: 只用于日志/调试，不参与匹配（不按角色定制）
    """
    p = (profile or "").strip().lower()
    if not p:
        return NORMAL
    if p in PROFILE_DELIVERY:
        return PROFILE_DELIVERY[p]
    alias = _ALIASES.get(p)
    if alias and alias in PROFILE_DELIVERY:
        return PROFILE_DELIVERY[alias]
    return NORMAL


def compose(profile: Optional[str], performance: Optional[str]) -> str:
    """合成官方格式的 delivery：`角色基线；逐句表演`。

    参数：
        profile:     actor_profile（第一层）
        performance: performance_profile（第二层，可空 -> neutral）

    返回用中文分号「；」拼接的两段式，与官方格式完全一致。
    """
    base = delivery_for(profile)
    perf = PERFORMANCE_DELIVERY[performance_for(performance)]
    return f"{base}；{perf}"


def normalize_delivery(d: Optional[str], profile: Optional[str] = None,
                       max_len: int = 80) -> str:
    """把任意 delivery 规整为合规的两段式，并保证不超长。

    处理情形：
    1. 已是两段式（含「；」）→ 前段过短/缺表演段时用我们的补齐
    2. 多段（模型常多打分号，如 `甲；乙；`）→ 只保留前两段、丢弃多余分隔
    3. 只有一段 → 判断它是基线还是表演，补上缺的另一段
    4. 空 → 直接返回角色基线
    5. 超长 → 硬截断到 max_len

    参数：
        d:       模型给出的 delivery 原文（可为 None/空）
        profile: actor_profile，用于补基线
        max_len: 长度上限（默认对齐 contract.DELIVERY_MAX=80）
    """
    base = delivery_for(profile)
    text = (d or "").strip()

    if not text:
        return base[:max_len]

    # 统一半角分号/冒号等误写为中文分号（只在还没有「；」时替换第一次）
    if "；" not in text:
        for sep in (";", "：", ":", "\n"):
            if sep in text:
                text = text.replace(sep, "；", 1)
                break

    if "；" in text:
        # 只取前两段：模型常写成「基线；表演；」或多段，多余段丢弃
        parts = [p.strip() for p in text.split("；") if p.strip()]
        if not parts:
            return f"{base}；{PERFORMANCE_DELIVERY[NORMAL_PERFORMANCE]}"[:max_len]
        head = parts[0]
        tail = parts[1] if len(parts) >= 2 else ""
        # 前段太短说明模型没写角色基线，用我们的补
        if len(head) < 6:
            head = base
        if not tail:
            tail = PERFORMANCE_DELIVERY[NORMAL_PERFORMANCE]
        # 第 3 段及以后通常是有价值的补充约束（实测「不显得过于急切」），
        # 在不超长的前提下并入表演段，而不是直接丢弃。
        if len(parts) > 2:
            for extra in parts[2:]:
                merged = f"{tail}，{extra}"
                if len(f"{head}；{merged}") <= max_len:
                    tail = merged
                else:
                    break
        out = f"{head}；{tail}"
    else:
        # 只有一段：若它正好是某条基线，缺表演段；否则当表演段补基线
        if text in PROFILE_DELIVERY.values():
            out = f"{text}；{PERFORMANCE_DELIVERY[NORMAL_PERFORMANCE]}"
        else:
            out = f"{base}；{text}"

    return out[:max_len]


def all_profiles() -> Dict[str, str]:
    """返回完整 profile->delivery 映射（供 UI / 配置核对）。"""
    return dict(PROFILE_DELIVERY)


def all_performances() -> Dict[str, str]:
    """返回完整 performance->delivery 映射（供 UI / 配置核对）。"""
    return dict(PERFORMANCE_DELIVERY)


# ---- V2 台本的 instruct 分层 -------------------------------------------
#
# 背景（2026-10-28 实测发现）：
#   模型按 MOD 的契约输出 a1ttsv2 时，instruct 常直接照抄契约里的示例：
#       「保持参考声线，吐字清楚，语速适中，平静自然地说完」
#   这句话对**所有角色都一样**，于是每个 NPC 听起来都没区别。
#
#   而我们的双层词表（角色基线 + 逐句表演）原本只作用在 V1 的 delivery 上，
#   根本没碰 a1ttsv2.instruct —— 等于白做。
#
# 这里的做法：**把角色基线补到模型写的那句前面**，保留模型对场景的理解，
# 同时给每个角色一个稳定的说话方式。已经带基线的不重复加。

# 判定「模型是不是在照抄示例」的特征词
_GENERIC_MARKERS = (
    "保持参考声线，吐字清楚",
    "吐字清楚，语速适中",
    "平静自然地说完",
    "Speak clearly and naturally",
)


def is_generic_instruct(text: str) -> bool:
    """模型写的 instruct 是不是那种「谁都能用」的通用句。"""
    t = (text or "").strip()
    if not t:
        return True
    hits = sum(1 for m in _GENERIC_MARKERS if m in t)
    # 只有通用特征、且没有具体场景描述（短于 30 字）才算照抄
    return hits > 0 and len(t) < 30


def layer_instruct(instruct: str, profile=None, performance=None,
                   max_len: int = 120) -> str:
    """给模型写的 instruct 补上角色基线，形成「基线；原句」。

    参数：
        instruct:    模型写的 instruct（可能为空、可能是通用句、可能已带基线）
        profile:     角色口吻基线键（来自角色卡个性）
        performance: 表演类型（一般让模型自己写，这里只在对空 instruct 兜底时用）
        max_len:     长度上限（官方 schema 是 120）

    返回最终 instruct。始终不超过 max_len。
    """
    base = delivery_for(profile)
    text = (instruct or "").strip()

    # 模型没写 → 直接给两层合成
    if not text:
        return compose(profile, performance)[:max_len]

    # 已经带了我们的基线 → 尊重，不再叠加
    if text.startswith(base) or base in text:
        return text[:max_len]

    # 只有基线和原句，保持「基线；原句」两段式
    out = f"{base}；{text}"
    if len(out) <= max_len:
        return out

    # 超长：优先保住基线，截断模型那段（尽量断在标点）
    room = max_len - len(base) - 1  # 减掉分号
    if room <= 6:
        return out[:max_len]
    tail = text[:room]
    for sep in ("。", "；", "，", "、", " "):
        idx = tail.rfind(sep)
        if idx >= room * 0.6:
            tail = tail[: idx + 1]
            break
    tail = tail.rstrip("；;，,、 ")
    return f"{base}；{tail}"[:max_len]
