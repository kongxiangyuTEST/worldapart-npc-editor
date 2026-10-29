"""说话表现层 · a1ttsv2 台本兜底补写。

对应 core_proxy.py 里原来内联在回复出口的「模型漏出 a1ttsv2 台本就从
正文引号对白补一段」逻辑。抽成独立模块，纯函数、零依赖。

背景：A1IndexTTSMod 0.8.x 的提示增强会要求模型输出 a1ttsv2（breeze2 引擎
的朗读台本），但小参数模型常漏输出 —— 表现为 NPC 有文本、不朗读。MOD 按
设计「未从正文补写」，那由我们补：从 content 抽出说话内容生成台本，
至少让 NPC 出声。

⚠ 台词提取规则见 build_talkback 的 docstring。
**本模块是唯一构造 segments 的地方**（core_proxy 只读不改），
所以引号/括号的处理规则只在这里维护一份，不存在多处不同步的问题。
"""

from __future__ import annotations

import re
from typing import List, Optional

# 括号动作/旁白：这些不该朗读
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")

_FALLBACK_INSTRUCT = "保持参考声线，吐字清楚，语速适中，平静自然地说完"

# 各类引号字符本身（内容保留，只去掉符号）
_QUOTE_CHARS_RE = re.compile(r"[“”「」『』\"']")
# 句末标点：切开后每句一段
_SENT_SPLIT_RE = re.compile(r"(?<=[。！？!?…])")
_MAX_SEGMENTS = 64        # 官方 schema 的 segments 上限
_MAX_SEGMENT_CHARS = 4096  # 官方 schema 单段字数上限
_PACK_TARGET = 180         # 超长文本按句累积到约这么多字一块

# 句末标点：拼接两个台词块时，若前块没有收尾标点就补一个，让 Breeze 有停顿
_SENT_END_CHARS = "。！？…!?.;；"


def _join_blocks(segs: List[str]) -> str:
    """把多个台词块拼成一整段。

    块与块之间原本隔着旁白（已被剥掉），直接相连会读成一串，
    所以前块末尾没有收尾标点时补一个句号。
    """
    out = ""
    for s in segs:
        s = (s or "").strip()
        if not s:
            continue
        if out and out[-1] not in _SENT_END_CHARS:
            out += "。"
        out += s
    return out


def _pack(text: str) -> List[str]:
    """超长文本按句累积成 ~180 字一块（只在整段超过 4096 字时用）。"""
    out, buf = [], ""
    for piece in _split_segments(text):
        if buf and len(buf) + len(piece) > _PACK_TARGET:
            out.append(buf)
            buf = piece
        else:
            buf += piece
    if buf:
        out.append(buf)
    return out


def _finalize(segs: List[str], merge_to_one: bool = True) -> Optional[List[str]]:
    """收尾：过滤空段；默认把多段合并成**一段**。

    ## 为什么默认合并（2026-10-28 实测）

    用户连续三次反馈「每段话的音色保持得并不统一」。

    原因：Breeze 是逐段合成的，**每个 segment 都是独立一次 HTTP 请求**，
    各自独立采样；段与段之间参考音特征、随机性都会有一点差异，
    听感上就是「同一个人说着说着换了嗓子」。切得越碎越明显。

    所以现在默认把一轮的台词全部拼成一段（≤4096 字照官方上限），
    交给 Breeze 服务端自己的 text_chunk_size 在**同一次请求内部**分块 ——
    同一次调用内部的切块共享参考特征，一致性远好于跨请求拼接。

    需要逐段控制（比如以后给不同段落不同的 tag）时，把 merge_to_one 关掉。
    """
    segs = [x for x in (segs or []) if x and x.strip()]
    if not segs:
        return None

    if merge_to_one:
        joined = _join_blocks(segs) if len(segs) > 1 else segs[0]
        if len(joined) <= _MAX_SEGMENT_CHARS:
            return [joined]
        return _pack(joined)[:_MAX_SEGMENTS]

    # 不合并时也要守住单段上限
    out: List[str] = []
    for x in segs:
        if len(x) > _MAX_SEGMENT_CHARS:
            out.extend(_pack(x))
        else:
            out.append(x)
    return out[:_MAX_SEGMENTS]

# 引号成对字符。
# ⚠ 半角双引号 " 必须算 —— 实测这个游戏的回复会三种混着用：
#     「是呢……」            （直角引号）
#     “客官这一声唤得轻”      （弯引号）
#     "檐下不留火，案上留话"  （半角直引号！）
# 漏掉半角直引号会让整段退化成「没有引号 → 整段全念」，
# 表现为旁白被念出来、该说的台词反而丢。2026-10-28 实测确认。
_OPENERS = "“「『\""
_CLOSERS = "”」』\""
_ASCII_QUOTE = '\"'"'"


def _extract_spans(text: str) -> List[str]:
    """按顺序取出所有引号内的片段，**未闭合的最后一个也算**。

    为什么必须容忍未闭合：实测这个游戏的回复经常这样结尾 ——

        才正经道：“那便请说吧。是嫌我这阵子熬得晚…我好了个准备。

    开引号 3 个、闭引号 2 个，最后那段最长最关键的台词没有闭合。
    若用「必须成对」的正则，它会被漏掉，表现为 NPC 该说的话没念出来。
    （2026-10-28 用户实测：纪小蝶第三段、苏倾盏第二三段都没声。）

    反过来，只有 1 个开引号、0 个闭引号时若也当整段台词，
    就会把「她说：“台词…」的整句都算进去 —— 但这种情况前面
    「引号前旁白」那一步已经把旁白剥掉了，所以是安全的。
    """
    spans, buf = [], None
    for ch in text:
        if buf is None:
            if ch in _OPENERS:
                buf = []
        else:
            # 半角引号开闭同形：在 span 内遇到它就是闭合
            if ch in _CLOSERS:
                spans.append("".join(buf).strip())
                buf = None
            elif ch in _OPENERS and ch != '"':
                # 嵌套的全角开引号当普通字符，避免把外层切断
                buf.append(ch)
            else:
                buf.append(ch)
    if buf is not None:
        tail = "".join(buf).strip()
        if tail:
            spans.append(tail)
    return [x for x in spans if x]

# 报错/JSON 内容不该被朗读。
# 2026-10-28 从 33 条真实捕获里发现 5 条是上游报错被游戏当成台词显示：
#   {"error":{"message":"Authentication Fails, Your api key: ****8Fwr is invalid"
# 若不拦，NPC 会把 API 报错一字一句念出来。
_NOT_SPEECH_RE = re.compile(
    r'^\s*[\{\[]'                      # 以 { 或 [ 开头，多半是 JSON
    r'|"error"\s*:'                      # JSON 错误对象
    r'|Authentication\s+Fails'
    r'|invalid_request_error'
    r'|api\s*key.*invalid',
    re.I)

# performance -> segment tag（2–6 个汉字，符合 schema 的 `^(|[\u3400-\u9fff]{2,6})$`）
#
# 为什么需要这个：schema 的 tag 是「空 或 2–6 个汉字」的**开放描述**，
# 不是枚举白名单。空 tag 等于放弃逐句表演信号，Breeze 只能按 instruct 念。
# 这里按 perceive() 判出的表演类型给一个保守的局部提示；判不出就给空。
#
# 取值原则：只写「可听见的局部特征」，不写情绪名词堆砌（那是 instruct 的活）。
PERFORMANCE_TAG = {
    "ask": "语尾轻扬",
    "answer": "句间稍停",
    "doubt": "语尾迟疑",
    "think": "边想边说",
    "realize": "语气转实",
    "care": "语气放柔",
    "comfort": "轻声安抚",
    "reassure": "语速放缓",
    "serious": "字字落实",
    "warn": "提醒放慢",
    "threat": "句尾压低",
    "refuse": "语尾干脆",
    "order": "短促有力",
    "angry": "压着怒气",
    "cold_stare": "声调转冷",
    "quarrel": "语速偏快",
    "sad": "语尾轻叹",
    "tired": "气息略沉",
    "regret": "语带迟疑",
    "relief": "松一口气",
    "confess": "放慢说清",
    "affection": "尾音收软",
    "joke": "带轻微笑意",
    "tease": "尾音轻挑",
    "memory": "语速放缓",
    "explain": "条理分明",
    "share": "语气明亮",
    "request": "略带恳请",
    "respect": "称呼有礼",
    "thanks": "语气诚恳",
    "casual": "语尾放松",
    "praise": "语带赞许",
    "proud": "尾音上扬",
    "challenge": "语气带劲",
    "wary": "声音稍收",
    # 中性不给 tag：空字符串是 schema 明示的合法值
    "neutral": "",
}


def tag_for(performance: str) -> str:
    """performance -> 合法的 segment tag（空或 2–6 汉字）。"""
    return PERFORMANCE_TAG.get((performance or "").strip().lower(), "")


def _split_segments(text: str) -> List[str]:
    """按句末标点切段，最多 64 段。

    切完要把过短的碎片并回相邻段：句末省略号（……）会被标点切成
    「这话说得…」「…」「倒像…」这种，单剩一个省略号送去合成没有意义，
    还会让 Breeze 停顿得很怪。实测 2026-10-28 纪小蝶那句就出现这个情况。
    """
    chunks = [c.strip() for c in _SENT_SPLIT_RE.split(text)]
    chunks = [c for c in chunks if c]

    # 合并过短碎片（<3 字）到前一段；开头没有前一段就并到后一段
    merged = []
    for c in chunks:
        bare = c.strip("…．.·—－-~～ ")
        if merged and len(bare) < 3:
            merged[-1] = merged[-1] + c
        elif not merged and len(bare) < 3 and chunks.index(c) < len(chunks) - 1:
            merged.append(c)  # 先留着，下一轮会并进它
        else:
            merged.append(c)

    # 第二轮：清掉仍是纯标点的段（并给前一段）
    final = []
    for c in merged:
        bare = c.strip("…．.·—－-~～ ")
        if final and not bare:
            final[-1] = final[-1] + c
        elif bare:
            final.append(c)
    if not final:
        final = [text.strip()] if text.strip() else []
    return final[:_MAX_SEGMENTS]


def build_talkback(content: str, merge_to_one: bool = True) -> Optional[List[str]]:
    """从正文抽可朗读对白段。无对白返回 None。

    merge_to_one=True（默认）时把多段拼成一整段，避免段间音色漂移；
    详见 _finalize 的说明。

    ## 这个游戏的正文结构（2026-10-28 用 33 条真实回复统计得出）

    一条回复通常是「旁白 + 台词」混排，旁白有两种写法：

    - 括号式：`（她怔了一下）台词` —— 最多，直接剥掉
    - 无括号式：`她低下头，把线头掐断，才慢慢道：台词`

    引号也有两种相反用法：

    - 强调：`你倒一句「没有」就打发过去了` —— 引号只圈一个短语，整句都是台词
    - 分隔：`只把声音放平：“你这话说得轻巧。”` —— 引号里才是台词

    实测 19 条含引号回复里，「引号内字数占比」全在 1%–55%，
    **没有一条超过 60%** —— 所以按占比判断（旧思路）根本无效。

    ## 现在用的判据

    1. 剥 `（…）` 括号旁白
    2. **看第一个引号前面那截**：若它以 `。` 或 `：` 结尾，
       说明那是一整段旁白，整段丢掉，从引号处开始念
       （实测 19 条里 12 条命中，且全部正确）
    3. 若剩下几乎全是引号内容（占比 ≥90%，如 `“你来了。”`），只取引号内
    4. 去掉引号符号，按句末标点切段，最多 64 段
    5. 不足 2 字 → None（没有说出口的话，NPC 不吭声是对的）

    ## 已知局限（刻意接受）

    - 引号**后面**的短旁白（`“…”顿了顿，声音低下去些`）仍会被念出来。
      要精确切需要做说话人归属，成本远大于收益。
    - 逗号连接的旁白（`我听见脚步再应一声，问一句“…”`）识别不出，会整句念。
    """
    if not content or not content.strip():
        return None
    # 上游报错 / JSON 残留：绝不能念出来
    if _NOT_SPEECH_RE.search(content):
        return None

    # 1. 剥括号动作/旁白
    spoken = _PAREN_RE.sub("", content).strip()
    if not spoken:
        return None

    # 2. 引号前若是一整段旁白（以 。或 ：结尾且不短），丢掉
    # 只要「引号前那截以 。或 ：结尾」就算旁白，不设长度门槛 ——
    # 「她说：“…”」这种只有 3 字的旁白也算，长度门槛会把它们漏掉。
    # 前端必须排除引号开头：否则 `“第一句。”他说。“第二句。”`
    # 会把第一句台词连同「他说。」一起当成旁白前缀切掉。
    m = re.match(r"^(?![“「『])(.{2,}?[。：:])\s*(?=[“「『])", spoken, re.S)
    if m:
        spoken = spoken[m.end():].strip()
        if not spoken:
            return None

    # 3. 引号是对白分隔符吗？两条判据，命中任一就只取引号内：
    #    a) 出现 2 段以上引号 —— 一段台词一段旁白交替，典型的分隔式
    #       实测：「“你也还没歇呀。”顿了顿，又说：“方才我问你的话…”」
    #    b) 引号内容占全文 ≥90%（如整条就是 “你来了。”）
    #    只有单段引号且占比小 → 判为强调，整句都念（`你倒一句「没有」就…`）
    quoted = _extract_spans(spoken)
    quoted_len = sum(len(x) for x in quoted)
    plain_len = len(_QUOTE_CHARS_RE.sub("", spoken))
    is_delimited = bool(quoted) and (
        len(quoted) >= 2 or (plain_len and quoted_len / plain_len >= 0.9))
    if is_delimited:
        # 4a. 分隔式：**一个引号块 = 一个片段**。
        # 不再按句号二次切分 —— 每次切分都是一次独立的 TTS 调用，
        # 切得越碎，各片段之间的音色漂移越明显（用户实测：「每段音色不统一」）。
        segs = []
        for q in quoted:
            q = _QUOTE_CHARS_RE.sub("", q)
            q = re.sub(r"[ \t]+", " ", q).strip(" 　：:，,、")
            if not q:
                continue
            if len(q) > 4096:            # 官方 schema 单段上限
                segs.extend(_split_segments(q))
            else:
                segs.append(q)
        # 过短的块（如「推荐我？」4 字）单独合成会显得断续，并进下一块
        merged: List[str] = []
        carry = ""
        for i, q in enumerate(segs):
            q = carry + q
            carry = ""
            bare = q.strip("…．.·—－-~～ ")
            is_last = (i == len(segs) - 1)
            if len(bare) < 8 and not is_last:
                carry = q
                continue
            merged.append(q)
        if carry:
            if merged:
                merged[-1] += carry
            else:
                merged.append(carry)
        return _finalize(merged, merge_to_one)

    # 4b. 强调式（引号只是强调，或整段没引号）：整段作为一段。
    #
    # 不再按句切 —— 每切一段都是一次独立 TTS 调用，切得越碎，
    # 片段之间的音色漂移越明显（用户实测「每段音色不统一」）。
    # 只有超长时才按句拆，且每块尽量拼到接近上限。
    plain = _QUOTE_CHARS_RE.sub("", spoken)
    plain = re.sub(r"[ \t]+", " ", plain).strip()
    plain = re.sub(r"^[：:，,、\s]+", "", plain)
    if len(plain) < 2:
        return None
    return _finalize([plain], merge_to_one)


def fill_a1ttsv2(inner: dict, profile=None, performance=None,
                 instruct_max: int = 120, merge_to_one: bool = True,
                 with_performance: bool = False,
                 instruct_mode: str = "profile") -> bool:
    """若 inner 里缺 a1ttsv2 字段，则从 content 补写一段基础台本。

    就地修改 inner；模型明确给了字段（哪怕 null，表示"无可朗读台词"）
    就尊重、不覆盖。

    参数：
        profile:     actor_profile，用于生成 instruct 的角色基线部分
        performance: performance_profile，用于生成 instruct 的逐句部分
        instruct_max: instruct 长度上限（schema 规定 120）

    为什么不用写死的一句话（旧行为）：
        旧版 instruct 恒为「保持参考声线，吐字清楚，语速适中，平静自然地说完」，
        等于所有角色所有台词都说同一句话，Breeze 拿不到任何区分度。
        现在用 voicebook.compose() 拼「角色基线；逐句表演」，与 V1 的 delivery
        同一套词表，只是搬到 instruct 上（instruct 限 120 字，compose 最长 49，安全）。
    """
    if not isinstance(inner, dict):
        return False
    if "a1ttsv2" in inner:
        return False
    content = str(inner.get("content") or "")
    quotes = build_talkback(content, merge_to_one=merge_to_one)
    if not quotes:
        return False

    # instruct：默认只用**角色基线**（每轮恒定），这样才能保证音色不漂。
    #
    # ## 为什么默认不带表演层（2026-10-28 实测）
    #
    # 同一句话、同一 NPC、同一参考音，只改 instruct 的后半句，基频实测：
    #
    #     纯基线           222.2 Hz   —
    #     +句间留半拍      217.2 Hz   -2.27%
    #     +句尾短促收住    214.8 Hz   -3.31%
    #     +重点处放慢      209.3 Hz   -5.80%
    #     +威胁（原版）    238.0 Hz   +7.11%
    #     +闲聊（诱导型）  227.4 Hz   +2.33%
    #
    # 也就是说 **instruct 只要一变，Breeze 的音高就跟着动 2–7%**；
    # 而同一 instruct 下不同文本的自然语调起伏是 5.5%。
    # 表演层带来的偏移比文本本身还大，听感就是「同一个人说着说着换了嗓子」。
    #
    # 所以默认只注入角色基线（同一 NPC 每轮完全一样 → 音色锁死），
    # 情绪表现交给标点、句长和 tag。想让表演层也进 instruct 时，
    # 把 ai_config 的 tts_instruct_performance 设 true（有情绪但音色会漂）。
    instruct = _FALLBACK_INSTRUCT
    if instruct_mode == "off":
        # 2026-10-30 实测：Breeze 对 instruct 的语速/音量响应是噪声，不是信号
        # （「语速很慢」在 100000 上反而更快；「音量很小」在 100000 上最响；
        #  「语速适中」在三个参考音里两次最快）。唯一稳定的是不给指令。
        # 所以 off 档发空串，把节奏交还参考音自身。
        instruct = ""
    else:
        try:
            from .voicebook import compose, delivery_for
            if with_performance:
                composed = compose(profile, performance)
            else:
                composed = delivery_for(profile) if profile else ""
            if composed:
                instruct = composed[:instruct_max]
        except Exception:
            pass

    inner["a1ttsv2"] = {
        "instruct": instruct,
        "segments": [{"text": q, "tag": tag_for(performance)} for q in quotes],
    }
    return True


def talkback_stats(inner: dict) -> str:
    """返回调试用一句话（用于 core_proxy 的 [TTS] 诊断日志）。"""
    if not isinstance(inner, dict):
        return ""
    tb = inner.get("a1ttsv2")
    if isinstance(tb, dict):
        segs = tb.get("segments") or []
        total = sum(len(s.get("text", "")) for s in segs if isinstance(s, dict))
        return f"字段=a1ttsv2 段数={len(segs)} 字数={total}"
    a1_fields = [k for k in inner.keys() if k.lower().startswith("a1tts")]
    if a1_fields:
        return f"字段={a1_fields}"
    return ""