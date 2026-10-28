"""说话表现层 · 风格帧/台本要求的系统提示词注入。

对应 core_proxy.py 里原来内联在 prepare_messages() 的 TTS 注入逻辑
（A1_TTS_STYLE_V1 marker 协议）。抽成独立函数后：
- 幂等：system 里已有本协议 marker 就不再重复注入（防止与下游 MOD、
  Python 代理自己多次调用叠加）
- 可关：cfg.get("tts_style_inject", True) 为 False 时直接跳过
- 纯文本、零依赖：不绑任何 TTS 引擎，只负责把"要求输出风格帧"
  这段提示词拼进 system 提示词。

示例如下（内容见 tts_style_prompt_v1.txt）：协议要求模型在有可朗读台词时，
于 content 台词末尾追加且仅追加一个 `<a1tts_v1>{JSON}</a1tts_v1>` 帧。
"""

from __future__ import annotations

import os
from typing import List, Optional

from .contract import OPEN_TAG  # 只用于钉出 marker 名，便于幂等判断

# 协议唯一标记：任何一端注入过这段提示，都会带上它，防止重复叠加同段文字
MARKER = "A1_TTS_STYLE_V1"
# V2 台本注入的标记（独立幂等，与 V1 互不影响）
MARKER_V2 = "A1_TTS_SCRIPT_V2"
# 提示词模板文件相对本包目录
_TEMPLATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tts_style_prompt_v1.txt")
_TEMPLATE_V2_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "tts_script_prompt_v2.txt")


def has_marker(messages: List[dict], marker: str = MARKER) -> bool:
    """system 里是否已有指定标记。默认查 V1。"""
    for m in messages or []:
        if m.get("role") == "system" and marker in (m.get("content") or ""):
            return True
    return False


def load_template(path: Optional[str] = None) -> Optional[str]:
    """读取注入提示词模板。文件缺失返回 None（调用方自行忽略）。"""
    try:
        with open(path or _TEMPLATE_FILE, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return text or None
    except OSError:
        return None


def inject_tts_style(messages: List[dict], cfg: dict) -> int:
    """把语音风格帧要求注入到第一条 system 提示词末尾。

    参数：
        messages: 转发给上游的 message 列表（原地修改）
        cfg:      ai_config 风格的配置 dict，读取 tts_style_inject

    返回注入的字符数（未注入/跳过/失败返回 0）。
    """
    if not cfg.get("tts_style_inject", True):
        return 0
    if has_marker(messages):
        return 0
    tpl = load_template()
    if not tpl:
        return 0
    if not messages or messages[0].get("role") != "system":
        return 0
    content = messages[0].get("content") or ""
    messages[0]["content"] = content + "\n\n" + tpl
    return len(tpl)


def inject_tts_script(messages: List[dict], cfg: dict) -> int:
    """把 V2 朗读台本要求注入 system 末尾（让模型输出 a1ttsv2）。

    为什么需要单独注入：
        游戏本体没有 NPC 语音，`a1ttsv2` 台本只能由模型产出。
        而 MOD 的提示增强钩的是游戏进程内的 C# 方法，够不到 HTTP 代理，
        所以它那份「要求输出台本」的提示永远送不到我们这里
        —— 实测 59 个真实样本里 0 个含 a1ttsv2。
        要 NPC 出声，必须由我们在代理侧自己要求。

    参数：
        messages: 转发给上游的 message 列表（原地修改）
        cfg:      读 tts_script_v2（默认 False，实验功能）

    返回注入的字符数。
    """
    if not cfg.get("tts_script_v2", False):
        return 0
    if has_marker(messages, MARKER_V2):
        return 0
    tpl = load_template(_TEMPLATE_V2_FILE)
    if not tpl:
        return 0
    # 尽量追加到最后一条 system（没有就追加第一条 system）
    idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "system":
            idx = i
            break
    if idx is None:
        return 0
    content = messages[idx].get("content") or ""
    messages[idx]["content"] = content + "\n\n" + tpl
    return len(tpl)