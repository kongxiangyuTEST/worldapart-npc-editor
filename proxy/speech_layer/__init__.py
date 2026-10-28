"""说话表现层（speech_layer）—— 独立可插拔模块。

从 A1IndexTTSMod 抽离的*纯数据契约与纯逻辑*，与任何 TTS 引擎 / BepInEx /
MOD 生态解耦。可单独作为一个小包使用，也可接入 core_proxy。

对外统一入口：
    process(req_json, cfg)  —— 注入风格帧要求 + 补写台本兜底（可各关）。
    has_frame(text)         —— 判断字符串里是否含风格帧。
    Envelope                —— 帧编解码（见 contract）。
    VoiceStyle              —— 一帧说话风格（见 contract）。
    delivery_for            —— 第一层：角色基线台账（见 voicebook）。
    compose                 —— 两层合成 `基线；表演`（见 voicebook）。
    normalize_delivery      —— 把任意 delivery 规整成合规两段式。
    perceive                —— 逐句表演类型判定（见 perceive）。

安装/使用：
    sys.path.insert(0, "proxy")
    from speech_layer import process
    # 注入侧
    req_json, injected = process(req_json, {"tts_style_inject": True})
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .contract import (
    OPEN_TAG, CLOSE_TAG, VoiceStyle, Envelope, EnvelopeResult,
    MAX_FRAME_LEN, EMOTION_MAX, DELIVERY_MAX, TAG_MIN, TAG_MAX,
    decode_reply, STYLE_FIELD_NAMES,
)
from .inject import (
    inject_tts_style, inject_tts_script, has_marker, load_template,
    MARKER, MARKER_V2,
)
from .talkback import build_talkback, fill_a1ttsv2, talkback_stats, tag_for
from .voicebook import (
    PROFILE_DELIVERY, NORMAL, NORMAL_PROFILE, delivery_for, all_profiles,
    PERFORMANCE_DELIVERY, PERFORMANCE_ALIASES, NORMAL_PERFORMANCE,
    performance_for, compose, normalize_delivery, all_performances,
    EMOTION_TAGS_CORE, layer_instruct, is_generic_instruct,
)
from .perceive import perceive, perceive_reason_zh

# 子模块供深层调用
from . import inject as inject_mod
from . import talkback as talkback_mod
from . import voicebook as voicebook_mod
from . import perceive as perceive_mod


def process(req: dict, cfg: Optional[dict] = None) -> Tuple[dict, int]:
    """在转发链路中统一注入风格帧要求。

    参数：
        req: 转发给上游的请求 dict（含 messages），就地修改并返回。
        cfg: 配置 dict，读取 tts_style_inject（默认开）。

    返回 (req, injected_chars)。注入失败/关闭返回 injected_chars=0。
    本函数只处理"注入"，不碰消息净化/裁剪 —— 那属于 core_proxy 职责。
    """
    cfg = cfg or {}
    msg = (req.get("messages") or []) if isinstance(req.get("messages"), list) else []
    added = 0
    if msg:
        added += inject_tts_style(msg, cfg)
        # V2 朗读台本（默认关）：让模型输出 a1ttsv2，NPC 才有东西可念
        added += inject_tts_script(msg, cfg)
        req["messages"] = msg
    return req, added


def has_frame(text: str) -> bool:
    """是否已在文本里识别到风格帧。"""
    return OPEN_TAG in (text or "")


__all__ = [
    "process", "has_frame",
    "VoiceStyle", "Envelope", "EnvelopeResult",
    "decode_reply", "STYLE_FIELD_NAMES",
    "OPEN_TAG", "CLOSE_TAG", "MAX_FRAME_LEN", "EMOTION_MAX",
    "DELIVERY_MAX", "TAG_MIN", "TAG_MAX",
    "inject_tts_style", "inject_tts_script", "has_marker", "load_template",
    "MARKER", "MARKER_V2",
    "build_talkback", "fill_a1ttsv2", "talkback_stats", "tag_for",
    # 第一层：角色基线
    "PROFILE_DELIVERY", "NORMAL", "NORMAL_PROFILE", "delivery_for", "all_profiles",
    # 第二层：逐句表演
    "PERFORMANCE_DELIVERY", "PERFORMANCE_ALIASES", "NORMAL_PERFORMANCE",
    "performance_for", "compose", "all_performances",
    # 组装与规整
    "normalize_delivery", "EMOTION_TAGS_CORE",
    # 表演类型判定
    "perceive", "perceive_reason_zh",
]