"""说话表现层 · 数据契约。

从 A1IndexTTSMod 抽离的*纯数据契约*：
- VoiceStyle: 一帧说话风格（emotion_tags / delivery / intensity）
- Envelope:  `<a1tts_v1>{JSON}</a1tts_v1>` 风格帧的编解码与容错

设计原则（见项目知识库「外部参考 · A1-TTS-MOD」）：
只偷"数据契约"，不偷"生态"——本模块零外部依赖、不绑任何 TTS 引擎 /
BepInEx / 第三方 API，因此游戏更新或组件失效都不影响它工作。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

# ---- 常量 -----------------------------------------------------------

OPEN_TAG = "<a1tts_v1>"
CLOSE_TAG = "</a1tts_v1>"
MAX_FRAME_LEN = 2048            # 帧体总长上限（字符）
EMOTION_MAX = 16                # 单个 emotion_tag 长度上限
DELIVERY_MAX = 80               # delivery 长度上限
TAG_MIN, TAG_MAX = 1, 3         # emotion_tags 数量范围

# 容忍各种被模型漏写/多写标签的写法：`<a1tts_v1>` 大小写、标签内空格、分隔符
# 用下划线 `_` 或斜杠 `/`（`<a1tts/v1>` 变体）都认。
_TAG_RE = re.compile(r"<\s*(?P<closing>/\s*)?a1tts[/_]v1\s*>", re.I)

# JSON 帧校验失败时的失败原因码
REASON = (
    "expected_object", "unexpected_or_missing_fields",
    "emotion_tags_must_be_array", "delivery_must_be_string",
    "intensity_must_be_number", "emotion_tag_must_be_string",
    "emotion_tag_empty_or_too_long", "emotion_tag_count_out_of_range",
    "delivery_empty_or_too_long", "intensity_out_of_range",
    "invalid_json", "frame_too_long", "multiple_frames", "incomplete_frame",
)


# ---- 数据契约 -------------------------------------------------------

@dataclass(frozen=True)
class VoiceStyle:
    """一帧说话风格：情绪标签 + 说话方式 + 强度。与原版 Contrat 一一对应。"""
    emotion_tags: List[str]
    delivery: str
    intensity: float

    @staticmethod
    def parse(data) -> "VoiceStyle":
        """从已解析的对象/dict 构造并校验。失败抛 ValueError。"""
        if not isinstance(data, dict):
            raise ValueError("expected_object")
        keys = set(data.keys())
        if keys != {"emotion_tags", "delivery", "intensity"}:
            raise ValueError("unexpected_or_missing_fields")

        et = data["emotion_tags"]
        if not isinstance(et, (list, tuple)):
            raise ValueError("emotion_tags_must_be_array")
        tags = []
        for t in et:
            if not isinstance(t, str):
                raise ValueError("emotion_tag_must_be_string")
            s = t.strip()
            if not s or len(s) > EMOTION_MAX:
                raise ValueError("emotion_tag_empty_or_too_long")
            tags.append(s)
        if not (TAG_MIN <= len(tags) <= TAG_MAX):
            raise ValueError("emotion_tag_count_out_of_range")

        dl = data["delivery"]
        if not isinstance(dl, str):
            raise ValueError("delivery_must_be_string")
        dl = dl.strip()
        if not dl or len(dl) > DELIVERY_MAX:
            raise ValueError("delivery_empty_or_too_long")

        it = data["intensity"]
        if isinstance(it, bool) or not isinstance(it, (int, float)):
            raise ValueError("intensity_must_be_number")
        it = float(it)
        if not (0.0 <= it <= 1.0):
            raise ValueError("intensity_out_of_range")

        return VoiceStyle(tags, dl, it)

    @staticmethod
    def from_json(s: str) -> "VoiceStyle":
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            raise ValueError("invalid_json") from None
        return VoiceStyle.parse(data)


@dataclass
class EnvelopeResult:
    """解码结果。status: valid / invalid；style 仅 valid 时非 None。"""
    content: str                       # 去掉帧后的纯净正文
    style: Optional[VoiceStyle] = None
    status: str = "valid"              # valid | invalid
    failure: Optional[str] = None      # invalid 时原因
    frame_count: int = 0


class Envelope:
    """`<a1tts_v1>{JSON}</a1tts_v1>` 帧编解码（纯文本，无 IO）。"""

    @staticmethod
    def encode(content: str, style: VoiceStyle) -> str:
        """把风格帧编码追加到正文末尾（字段序 emotion_tags/delivery/intensity）。"""
        obj = {
            "emotion_tags": style.emotion_tags,
            "delivery": style.delivery,
            "intensity": style.intensity,
        }
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return content + OPEN_TAG + payload + CLOSE_TAG

    @staticmethod
    def decode(content: str) -> EnvelopeResult:
        """解析正文中的风格帧。

        容错（对齐原版 StyleEnvelopeCodec）：
        - 容忍 `<\na1tts_v1 >`、`</ a1tts_v1>` 等标签变体
        - 帧体可被 ```json``` 代码围栏包裹、可被外层 JSON 转义（\\"）
        - 只取*第一个*帧；切掉帧后 body 里残留的标签一并清掉
        - 找不到/非法帧 → status=invalid 但不抛异常，交由上层决定
        """
        if not content:
            return EnvelopeResult(content="", status="invalid",
                                  failure="absent", frame_count=0)

        # 1) 正则先给标签"咒语"归一（把大写/空格还原成标准开关标签）
        text = _TAG_RE.sub(
            lambda m: OPEN_TAG if not m.group("closing") else CLOSE_TAG, content)

        # 2) 找第一个完整开关对
        start = text.find(OPEN_TAG)
        if start < 0:
            return EnvelopeResult(content=text, status="invalid",
                                  failure="absent", frame_count=0)
        inner = text.find(OPEN_TAG, start + len(OPEN_TAG))  # 不嵌套
        body_start = start + len(OPEN_TAG)
        end = text.find(CLOSE_TAG, body_start)
        if end < 0:
            return EnvelopeResult(content=text.replace(OPEN_TAG, "").replace(CLOSE_TAG, ""),
                                  status="invalid", failure="incomplete_frame",
                                  frame_count=1)

        payload = text[body_start:end]
        frame_len = end + len(CLOSE_TAG) - start
        if frame_len > MAX_FRAME_LEN:
            return EnvelopeResult(content=text, status="invalid",
                                  failure="frame_too_long", frame_count=1)

        # 只保留第一个帧，body 其余部分作为纯净正文
        body = text[:start] + text[end + len(CLOSE_TAG):]
        body = body.replace(OPEN_TAG, "").replace(CLOSE_TAG, "").strip()

        # 3) 解析帧体：先去可能的 ```json``` 围栏，再解外层转义
        raw = payload.strip()
        if raw.startswith("```") and raw.endswith("```") and len(raw) >= 6:
            raw = raw[3:-3].strip()
            if raw.lower().startswith("json") and (
                    len(raw) == 4 or raw[4].isspace()):
                raw = raw[4:].strip()
        if raw.startswith('{\\"'):
            # 外层被 JSON 字符串转义过：先整体解一层
            try:
                raw = json.loads('"' + raw + '"')
            except json.JSONDecodeError:
                pass

        try:
            style = VoiceStyle.from_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            reason = e.args[0] if e.args else "invalid_json"
            return EnvelopeResult(content=body, status="invalid",
                                  failure=reason, frame_count=1)

        # 4) 确认正文里只剩一个帧（净化后再解一次，防重复）
        return EnvelopeResult(content=body, style=style, status="valid",
                              frame_count=1)


# ---- 整条回复的解码（兼容模型的几种跑偏写法）-----------------------------
#
# 实测（wa-unred / qwen35）：模型经常不写内嵌 `<a1tts_v1>...</a1tts_v1>` 帧，
# 而是把风格数据写成**回复 JSON 的顶层字段**，例如：
#
#     {"content": "台词……", "emotion": "平静", "mind": "…",
#      "a1tts_v1": {"emotion_tags": [...], "delivery": "...", "intensity": 0.2}}
#
# 甚至沿用旧字段名 `voice_style`。这些内容本身完全合法（能被 VoiceStyle.parse
# 通过），但 Envelope.decode 只认内嵌标签，会判 absent 而把整帧丢掉。
#
# 下面的 decode_reply() 按「内嵌帧优先，顶层字段兜底」的顺序处理，并把
# content 从回复 JSON 里正确剥离出来。

# 模型可能用作顶层风格容器的字段名（含旧名）
STYLE_FIELD_NAMES = ("a1tts_v1", "a1ttsv1", "voice_style", "voiceStyle")


def _strip_inner_frame(text: str) -> str:
    """把 content 字符串里内嵌的风格帧剥掉，只留可见正文。"""
    if not text:
        return ""
    res = Envelope.decode(text)
    return res.content if res.frame_count else text


def decode_reply(reply_text: str) -> EnvelopeResult:
    """解码一条完整回复，兼容「内嵌帧」与「顶层字段」两种写法。

    处理顺序：
    1. 回复是合法 JSON 对象 → 先看 content 里有没有内嵌帧；没有再找顶层
       风格字段；两者都没有则 invalid/absent。
    2. 回复不是 JSON（纯文本台词）→ 直接走 Envelope.decode。

    返回的 EnvelopeResult.content 始终是**可直接朗读的可见正文**（已剥离
    内嵌帧、已从 JSON 里取出 content）。
    """
    raw = (reply_text or "").strip()
    if not raw:
        return EnvelopeResult(content="", status="invalid",
                              failure="absent", frame_count=0)

    obj = None
    if raw.startswith("{") and raw.endswith("}"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None

    if not isinstance(obj, dict):
        # 纯文本台词，交给内嵌帧解码
        return Envelope.decode(raw)

    # --- 情况 A：content 里内嵌了帧（最理想）---
    content = obj.get("content")
    if isinstance(content, str) and (OPEN_TAG in content or "<a1tts" in content.lower()):
        res = Envelope.decode(content)
        if res.status == "valid":
            return res
        # 内嵌帧解析失败，但仍要返回剥离后的正文供朗读
        inner_failure = res.failure
        body = res.content
    else:
        body = content if isinstance(content, str) else ""
        inner_failure = None

    # --- 情况 B：顶层字段兜底（实测模型的常见写法）---
    for key in STYLE_FIELD_NAMES:
        cand = obj.get(key)
        if not isinstance(cand, dict):
            continue
        try:
            style = VoiceStyle.parse(cand)
        except ValueError as e:
            return EnvelopeResult(
                content=body, status="invalid",
                failure=(e.args[0] if e.args else "invalid_json"),
                frame_count=0)
        return EnvelopeResult(content=body, style=style, status="valid",
                              frame_count=0)

    # --- 情况 C：JSON 里根本没有风格数据 ---
    return EnvelopeResult(content=body, status="invalid",
                          failure=inner_failure or "absent", frame_count=0)