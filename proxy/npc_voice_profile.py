"""从角色卡读取「个性」字段（轻量版，不依赖 editor.py）。

## 为什么单独写一份

`editor.parse_card_file()` 是给编辑器用的完整解析（含用户名片、背景、
标题等）。core_proxy 在转发链路上每轮都要查一次角色口吻，只需要
「个性」这一个字段，不应该为此拖进 editor.py（它会 import pywebview
等 GUI 依赖，在无头环境里会失败）。

这里只做最小解析：找到 `## 预设资料` 段里的 `- 个性：xxx` 一行。
按 mtime 缓存，卡片改动后自动失效。

## 卡片路径

`editor_cards/<npcId>-<name>.md`。文件名里既有 id 又有名字，所以
按 id 前缀匹配即可，不必知道名字。
"""

from __future__ import annotations

import os
import re
import threading
from typing import Dict, Optional, Tuple

# editor_cards 目录（与 config.CARDS_DIR 同一处，这里不 import config 以免循环依赖）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(_BASE_DIR, "editor_cards")

# 文件名形如 "100000-苏倾盏.md"
_CARD_RE = re.compile(r"^(\d+)-(.+)\.md$")
# 「- 个性：精明内敛」/「- 个性: 精明内敛」
_PERSONALITY_RE = re.compile(r"^-\s*个性\s*[：:]\s*(.+?)\s*$", re.M)

_lock = threading.Lock()
# 缓存：{npc_id_str: (mtime, personality)}
_cache: Dict[str, Tuple[float, str]] = {}
# id -> 文件路径（目录扫描结果，按目录 mtime 失效）
_index: Dict[str, str] = {}
_index_dir_mtime: Optional[float] = None


def _refresh_index() -> Dict[str, str]:
    """扫描 editor_cards 建 id->path 索引。目录没变就复用。"""
    global _index, _index_dir_mtime
    try:
        dir_mtime = os.stat(CARDS_DIR).st_mtime
    except OSError:
        return _index
    if _index_dir_mtime == dir_mtime and _index:
        return _index
    idx: Dict[str, str] = {}
    try:
        for fn in os.listdir(CARDS_DIR):
            m = _CARD_RE.match(fn)
            if m:
                idx[m.group(1)] = os.path.join(CARDS_DIR, fn)
    except OSError:
        pass
    _index = idx
    _index_dir_mtime = dir_mtime
    return idx


def _norm_id(npc_id) -> str:
    """id 归一：100000 / "100000" / "100000.0" 统一成 "100000"。"""
    if npc_id is None:
        return ""
    s = str(npc_id).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def personality_for(npc_id) -> Optional[str]:
    """返回该 NPC 卡片上的「个性」原值（如「精明内敛」）。查不到 None。"""
    nid = _norm_id(npc_id)
    if not nid:
        return None

    with _lock:
        path = _refresh_index().get(nid)
        if not path:
            return None
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            return None

        cached = _cache.get(nid)
        if cached and cached[0] == mtime:
            return cached[1] or None

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            return None

        # 只在「## 预设资料」段里找，避免背景里出现「个性」二字误命中
        seg = raw
        start = raw.find("## 预设资料")
        if start >= 0:
            nxt = raw.find("\n## ", start + 1)
            seg = raw[start: nxt if nxt > 0 else len(raw)]
        m = _PERSONALITY_RE.search(seg)
        val = (m.group(1).strip() if m else "")
        _cache[nid] = (mtime, val)
        return val or None


def clear_cache():
    """清缓存（编辑器改了卡片后可手动调；平时靠 mtime 自动失效）。"""
    global _index, _index_dir_mtime
    with _lock:
        _cache.clear()
        _index = {}
        _index_dir_mtime = None


def stats() -> dict:
    """返回状态，供编辑器展示。"""
    idx = _refresh_index()
    return {
        "cards_dir": CARDS_DIR,
        "cards": len(idx),
        "exists": os.path.isdir(CARDS_DIR),
    }
