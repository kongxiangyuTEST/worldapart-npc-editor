"""说话表现层 · 逐句表演类型判定（performance perception）。

## 为什么需要它

官方 delivery 是两层拼接：`角色基线；逐句表演`。第二层（performance）如果
只靠模型自己写，小模型经常不遵守两段式格式（与「漏吐风格帧」同源）。
所以需要一个**纯确定性、零模型依赖**的兜底判定：即使模型完全不配合，
我们也能给出合理的第二层，让 delivery 至少有场景层次。

## 判定思路

按「可解释的优先级规则」从窄到宽依次匹配，命中即返回：

    台词侧的强信号（台词自己就在拒绝/警告/道谢…）
      > 玩家侧的情绪信号（玩家刚骂人/刚示弱）
        > 台词结构信号（含问号/很短/很长）
          > 默认 neutral

设计原则：
- **宁可不判，不要误判**。判错比不判更糟（会把平静台词演成发火）。
  所以强信号用「必须同时满足两个条件」的方式收紧。
- 只用字面词表与长度/标点，不引入 NLP 依赖（项目坚持零新依赖）。
- 每条规则都要能在日志里解释清楚（返回 reason），便于排查。

## 与 voicebook 的关系

本模块只决定**用哪个 performance 键**，具体文案由
`voicebook.PERFORMANCE_DELIVERY` 提供。判定结果喂给 `voicebook.compose()`。
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# ---- 词表 ---------------------------------------------------------------
# 全部为短词/短语，只用于「命中即认为可能属于该类」，配合其他条件才生效。

_REFUSE = (
    "不行", "不成", "不可", "不能", "不必", "不用", "不必了", "恕难",
    "别想", "休想", "免谈", "不卖", "不给", "不借", "不去", "不做",
    "不是我不", "碍难", "无此先例", "没这规矩", "不允许",
)

_WARN = (
    "小心", "当心", "留神", "切记", "务必", "千万别", "莫要", "不可近",
    "危险", "有毒", "陷阱", "有诈", "不妥", "凶险", "别碰", "别再",
)

_THREAT = (
    "识相", "否则", "后果", "别怪我", "不客气", "走着瞧", "掂量",
    "活腻", "找死", "代价",
)

_COMFORT = (
    "别怕", "不怕", "无妨", "没事", "放心", "不急", "别急", "别慌",
    "我在", "有我", "会好的", "别担心", "慢慢来", "歇一歇",
)

_REQUEST = (
    "可否", "能否", "劳烦", "麻烦你", "求", "拜托", "还请", "望你",
    "能不能", "可以吗", "可否请", "请帮",
)

_THANKS = (
    "多谢", "谢过", "承蒙", "有劳", "感激", "谢谢你", "谢了", "恩情",
)

_APOLOGY = (
    "抱歉", "对不住", "恕罪", "惭愧", "失礼", "是我不好", "冒犯",
)

_CONFESS = (
    "我心悦", "我欢喜", "我愿意", "我想你", "我喜欢", "舍不得",
    "放不下", "唯独你", "只对你", "不瞒你", "实话与你说",
)

_AFFECTION = (
    "傻", "呆子", "小笨蛋", "乖", "摸摸", "乖些", "哼",
)

_JOKE = (
    "哈", "嘿", "开玩笑", "逗你", "说笑", "有趣", "好笑", "妙极",
)

_SAD = (
    "罢了", "终究", "可惜", "遗憾", "回不去", "来不及", "算了",
    "无力", "空", "散了吧",
)

_TIRED = (
    "累了", "乏了", "歇会", "倦", "困了", "乏",
)

_REGRET = (
    "悔", "不该", "早知", "若当初", "怪我", "错在",
)

_RELIEF = (
    "总算", "好在", "幸好", "还好", "所幸", "松了一口气",
)

_MEMORY = (
    "那年", "当年", "从前", "过去", "记得", "小时候", "旧时",
    "那时候", "后来", "曾经",
)

_EXPLAIN = (
    "因为", "所以", "首先", "其次", "第一", "第二", "步骤",
    "道理是", "缘故", "缘由", "换言之", "也就是说",
)

# 注意：这里只列「结构上确实是提问」的标志。不要把「何/什么/怎么」这类
# 单字放进来 —— 「奈何」「无可如何」都是陈述，会被误判成提问。
_ASK = (
    "？", "?", "谁知", "如何", "怎样", "是否", "多少", "哪里", "何处",
)

_ANGER = (
    "滚", "滚开", "混账", "该死", "放肆", "荒唐", "岂有此理", "可恶",
    "闭嘴", "住口", "无耻",
)

_DOUBT = (
    "当真", "果真", "莫非", "难道", "怎么可能", "不信", "可疑", "蹊跷",
)

_PRAISE = (
    "了不起", "佩服", "了得", "高明", "果然", "名不虚传", "钦佩",
)

_PLAYER_NEG = (
    "滚", "混蛋", "混账", "去死", "该死", "废物", "闭嘴", "住口",
    "无耻", "可恶", "蠢", "垃圾", "烦人",
)

_PLAYER_PLEAD = (
    "求你", "求求", "帮我", "救", "我怕", "害怕", "怎么办", "不会",
    "难过", "想哭", "撑不住",
)

_PLAYER_WARM = (
    "谢谢", "喜欢你", "想你", "在乎", "陪", "一起", "牵挂", "抱",
)


# ---- 辅助 ---------------------------------------------------------------

_SENT_END = re.compile(r"[。！？!?…~～]+$")


def _hit(text: str, words) -> Optional[str]:
    """返回命中的第一个词（用于日志解释），未命中返回 None。"""
    for w in words:
        if w in text:
            return w
    return None


# 否定 / 归属前缀：命中词前面若紧跟这些字，说明语境被否定或转述，
# 不能按字面意思判定。实测误判例：
#   「我不怕」      被当安抚 → 其实是表态
#   「别怕我」      被当安抚 → 其实是让人别怕自己
#   「他求了一辈子」 被当请求 → 其实是叙述别人
#   「他小心眼」    被当警告 → 其实是评价性格
_NEG_PREFIX = (
    "不", "没", "别", "莫", "无", "未", "非", "休",
    "他", "她", "它", "他们", "她们", "我", "你",
)


def _hit_neg(text: str, words) -> Optional[str]:
    """带否定/转述排除的命中：命中词紧邻前一字是否定或人称代词则跳过。

    只检查**前一个字符**，这正好覆盖「我不怕」「他小心眼」「他求了」这类
    误判的共同特征；不做过宽窗口，避免把「别怕，有我在」这种正常安抚排掉
    （它的「别怕」在句首，前面没有字）。

    另外排除「别怕我 / 别怕他」这类——虽然以「别」开头，但对象是说话人自己，
    不是安抚对方。
    """
    for w in words:
        start = 0
        while True:
            i = text.find(w, start)
            if i < 0:
                break
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + len(w):i + len(w) + 1]
            # 前一字是否定/人称 → 否定式或转述，跳过
            if prev and prev in _NEG_PREFIX:
                start = i + len(w)
                continue
            # 「别怕我/别怕他/别慌我」：对象是说话人，不是安慰对方
            if nxt in ("我", "他", "她", "它") and w.startswith("别"):
                start = i + len(w)
                continue
            return w
    return None


def _has_question(text: str) -> bool:
    return ("？" in text) or ("?" in text)


def _strip_quotes(text: str) -> str:
    """只保留台词本体（去引号/括号动作），避免旁白误触发规则。"""
    t = text or ""
    t = re.sub(r"[（(][^）)]*[）)]", "", t)   # 去括号动作
    return t.strip()


# ---- 主判定 -------------------------------------------------------------

def perceive(npc_text: str, player_text: Optional[str] = None) -> Tuple[str, str]:
    """判定这一句 NPC 台词该用哪种 performance。

    参数：
        npc_text:    NPC 本轮台词（会去掉括号动作，只看说出口的话）
        player_text: 玩家上一句（可空）。用于「玩家刚骂人 → NPC 戒备/发火」这类判断

    返回 `(performance_key, reason)`：
        performance_key  可直接喂给 `voicebook.compose()`
        reason           命中原因（英文短码，用于日志排查；未命中为 "default"）
    """
    npc = _strip_quotes(npc_text)
    plr = (player_text or "").strip()

    if not npc:
        return "neutral", "empty_text"

    # ===== 第一优先级：台词自身的强语义信号 =====
    # 用 _hit_neg 而非 _hit：排除「我不怕」「他小心眼」「他求了」这类
    # 否定式/转述句的误判（这些在实测压力测试中确实会误判）。

    for words, perf, code in (
        (_THREAT, "threat", "npc_threat"),
        (_APOLOGY, "regret", "npc_apology"),
        (_THANKS, "thanks", "npc_thanks"),
        (_COMFORT, "comfort", "npc_comfort"),
        (_CONFESS, "confess", "npc_confess"),
        (_RELIEF, "relief", "npc_relief"),
        (_REGRET, "regret", "npc_regret"),
        (_REQUEST, "request", "npc_request"),
        (_WARN, "warn", "npc_warn"),
        (_PRAISE, "praise", "npc_praise"),
    ):
        hit = _hit_neg(npc, words)
        if hit:
            return perf, f"{code}:{hit}"

    # ===== 第二优先级：玩家侧情绪影响（需台词无明显强信号） =====

    if _hit(plr, _PLAYER_NEG):
        # 玩家刚骂人：NPC 若在拒绝/警告（上面已拦），否则戒备或对抗
        if _hit(npc, _REFUSE):
            return "refuse", "player_neg+npc_refuse"
        if _hit(npc, _ANGER):
            return "angry", f"player_neg+npc_anger:{_hit(npc, _ANGER)}"
        return "wary", f"player_neg:{_hit(plr, _PLAYER_NEG)}"

    if _hit(plr, _PLAYER_PLEAD):
        # 玩家示弱求助但台词无安慰词 → 关切
        return "care", f"player_plead:{_hit(plr, _PLAYER_PLEAD)}"

    # ===== 第三优先级：台词语义（较宽，同样做否定/转述排除） =====

    for words, perf, code in (
        (_REFUSE, "refuse", "npc_refuse"),
        (_ANGER, "angry", "npc_anger"),
        (_SAD, "sad", "npc_sad"),
        (_TIRED, "tired", "npc_tired"),
        (_DOUBT, "doubt", "npc_doubt"),
        (_JOKE, "joke", "npc_joke"),
        (_MEMORY, "memory", "npc_memory"),
        (_EXPLAIN, "explain", "npc_explain"),
    ):
        hit = _hit_neg(npc, words)
        if hit:
            return perf, f"{code}:{hit}"

    # ===== 第四优先级：结构信号 =====

    n = len(npc)
    if _has_question(npc):
        # 短问句更可能是直接提问，长问句可能是自问/反问
        if n <= 20:
            return "ask", "question_short"
        return "ask", "question_long"

    # 玩家给了温暖的话 → NPC 回应可稍柔（但不至于到 affection）
    if _hit(plr, _PLAYER_WARM) and n <= 30:
        return "affection", f"player_warm:{_hit(plr, _PLAYER_WARM)}"

    if n >= 60:
        return "explain", "long_text"

    if n <= 8:
        return "neutral", "very_short"

    return "neutral", "default"


def perceive_reason_zh(reason: str) -> str:
    """把 reason 码翻成中文，供日志展示。"""
    if not reason:
        return ""
    head = reason.split(":", 1)[0]
    table = {
        "npc_threat": "台词含威胁", "npc_apology": "台词含致歉",
        "npc_thanks": "台词含致谢", "npc_comfort": "台词含安抚",
        "npc_confess": "台词含表白", "npc_relief": "台词含释然",
        "npc_regret": "台词含懊悔", "npc_request": "台词含请求",
        "npc_warn": "台词含警告", "npc_praise": "台词含赞赏",
        "npc_refuse": "台词含拒绝", "npc_anger": "台词含怒意",
        "npc_sad": "台词含低落", "npc_tired": "台词含疲态",
        "npc_doubt": "台词含疑问", "npc_joke": "台词含玩笑",
        "npc_memory": "台词忆旧", "npc_explain": "台词在解释",
        "player_neg+npc_refuse": "玩家发难+台词拒绝",
        "player_neg+npc_anger": "玩家发难+台词动怒",
        "player_neg": "玩家发难→戒备",
        "player_plead": "玩家示弱→关切",
        "player_warm": "玩家示好→回应偏柔",
        "question_short": "短问句", "question_long": "长问句",
        "long_text": "长台词", "very_short": "极短句",
        "empty_text": "无台词", "default": "默认中性",
    }
    return table.get(head, head)
