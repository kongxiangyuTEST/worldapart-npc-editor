# -*- coding: utf-8 -*-
"""
core_proxy.py  -  AI 聊天增强中转代理
                 （Python 重写版 + 语义去重 + 残留清洗 + 角色交替修正 + 用户名片注入）
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import json
import random
import re
import difflib
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests
from flask import Flask, request, Response
GAME_DIR = ""
CURRENT_NPC_NAME = None

# ============================================================
# 动态路径（打包/开发环境通用）
# ============================================================
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    PROJECT_DIR = Path(sys.executable).parent.parent   # 打包后：exe 在 proxy\ 里
else:
    PROJECT_DIR = Path(__file__).parent.parent          # 开发环境：proxy\ 的上一级

sys.path.insert(0, str(PROJECT_DIR))
import memory_manager
import os

# ==================== 智能动态路径 ====================
if getattr(sys, 'frozen', False):
    # 打包环境：exe 所在的目录的上一级（因为 exe 在 proxy 文件夹里）
    base_dir = os.path.dirname(sys.executable)
    base_dir = os.path.dirname(base_dir)
else:
    # 源码环境：proxy 文件夹的上一级
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 动态设置 memory 目录
memory_manager.MEMORY_DIR = os.path.join(base_dir, "memory")
if not os.path.exists(memory_manager.MEMORY_DIR):
    os.makedirs(memory_manager.MEMORY_DIR)

# 动态设置 cards 目录
cards_dir = os.path.join(base_dir, "editor_cards")

# ============================================================
# 一、读配置
# ============================================================
import sys as _sys

if getattr(_sys, 'frozen', False):
    # 打包后：exe 所在目录
    ROOT = Path(_sys.executable).parent
else:
    # 开发环境：脚本所在目录
    ROOT = Path(__file__).parent.resolve()

CONF_PATH = ROOT / "core_proxy_config.json"

if not CONF_PATH.exists():
    print(f"[错误] 找不到配置文件: {CONF_PATH}")
    sys.exit(1)

with open(CONF_PATH, "r", encoding="utf-8") as f:
    conf = json.load(f)

LISTEN_HOST = conf.get("listen_host", "127.0.0.1")
LISTEN_PORT = int(conf.get("listen_port", 8789))
UPSTREAM_BASE = conf.get("upstream_base_url", "http://127.0.0.1:8080/").rstrip("/")
FALLBACK_KEY = conf.get("api_key")

# ============================================================
# 二、AI 配置（ai_config.json，按 mtime 热加载）
# ============================================================
AI_CONFIG_PATH = PROJECT_DIR / "ai_config.json"

# inject / rules / 开关 / 记忆预算都放这里。
# 文件内容一变，下一次请求自动重读 —— 改完存盘即生效，不用重启 core_proxy。
_AI = {
    "mtime": None,
    "inject": "",
    "rules": [],             # [(old, new), ...]
    "inject_user_card": True,
    "memory_budget": 2000,   # 每轮记忆注入预算（字）
    "memory_hint": True,     # 低重要度事实是否只在玩家发言命中时注入
    "memory_slot": "past_events",  # past_events=写进游戏自带的「## 过往要事」；append=贴 system 末尾
    "save_guard": True,      # 换存档保护：游戏侧记忆完全对不上时暂停注入
    "memory_merge_url": "",  # 记忆总结单独走哪个地址（空=用上游；填本地可避开 API 审核）
    "memory_merge_every": 12,     # 攒够多少轮触发一次总结
    "memory_merge_max_tokens": 4000,  # 总结输出上限（太小会被截断成非法 JSON）
    "past_events_max_chars": 800,     # 游戏「过往要事」最多注入多少字（0=不压缩）
}


def load_ai_config(force=False):
    """读 ai_config.json；文件没变就复用缓存。返回配置字典。"""
    try:
        mtime = AI_CONFIG_PATH.stat().st_mtime
    except OSError:
        if _AI["mtime"] is None:
            print(f"[警告] 未找到 {AI_CONFIG_PATH}，将原样转发 prompt。")
        return _AI

    if not force and _AI["mtime"] == mtime:
        return _AI

    try:
        with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            conf = json.load(f)
    except Exception as e:
        print(f"[警告] ai_config.json 解析失败，沿用上次配置: {e}")
        return _AI

    new_rules = []
    for r in conf.get("rules") or []:
        old = (r.get("old") or "").strip()
        new = (r.get("new") or "").strip()
        if old:
            new_rules.append((old, new))

    _AI["inject"] = (conf.get("inject") or "").strip()
    _AI["rules"] = new_rules
    _AI["inject_user_card"] = bool(conf.get("inject_user_card", True))
    try:
        _AI["memory_budget"] = int(conf.get("memory_budget", 2000))
    except (TypeError, ValueError):
        print(f"[配置] memory_budget 不是数字，回落 2000")
        _AI["memory_budget"] = 2000
    _AI["memory_hint"] = bool(conf.get("memory_hint", True))

    slot = str(conf.get("memory_slot", "past_events")).strip().lower()
    _AI["memory_slot"] = slot if slot in ("past_events", "append") else "past_events"
    _AI["save_guard"] = bool(conf.get("save_guard", True))
    _AI["memory_merge_url"] = str(conf.get("memory_merge_url") or "").strip()
    try:
        _AI["memory_merge_every"] = max(4, int(conf.get("memory_merge_every", 12)))
    except (TypeError, ValueError):
        _AI["memory_merge_every"] = 12
    try:
        _AI["memory_merge_max_tokens"] = max(500, int(conf.get("memory_merge_max_tokens", 4000)))
    except (TypeError, ValueError):
        _AI["memory_merge_max_tokens"] = 4000
    try:
        _AI["past_events_max_chars"] = max(0, int(conf.get("past_events_max_chars", 800)))
    except (TypeError, ValueError):
        _AI["past_events_max_chars"] = 800
    # 历史裁剪：游戏不限制对话轮数，历史会一直涨，最终撑爆上下文窗口
    # （2026-10-24 实测确认）。0 表示不裁。
    try:
        _AI["history_max_chars"] = max(0, int(conf.get("history_max_chars", 24000)))
    except (TypeError, ValueError):
        print("[配置] history_max_chars 不是数字，回落 24000")
        _AI["history_max_chars"] = 24000
    # 无论如何都保留的最近轮数（一 user + 一 assistant 算一轮）
    try:
        _AI["history_keep_turns"] = max(2, int(conf.get("history_keep_turns", 6)))
    except (TypeError, ValueError):
        _AI["history_keep_turns"] = 6

    _AI["mtime"] = mtime
    print(f"[配置] ai_config.json 已加载：注入 {len(_AI['inject'])} 字，"
          f"替换 {len(new_rules)} 条，名片注入 {_AI['inject_user_card']}，"
          f"记忆预算 {_AI['memory_budget']} 字，记忆落位 {_AI['memory_slot']}，"
          f"换档保护 {'开' if _AI['save_guard'] else '关'}", flush=True)
    return _AI

# ============================================================
# 三、用户名片配置
# ============================================================
# 卡片目录
# _editor_cards = PROJECT_DIR / "editor_cards"
# _mod_cards = Path(GAME_DIR) / "Mods" / "WorldApartNpcCards" if GAME_DIR else None

# if _editor_cards.is_dir() and any(_editor_cards.glob("*.md")):
#     CARDS_DIR = _editor_cards
# elif _mod_cards and _mod_cards.is_dir():
#     CARDS_DIR = _mod_cards
# else:
#     CARDS_DIR = _editor_cards

# 卡片目录，直接写死指向当前编辑器目录
CARDS_DIR = PROJECT_DIR / "editor_cards"

# ============================================================
# 三·补充、Prompt 样本保存
# ============================================================
PROMPT_SAMPLES_DIR = PROJECT_DIR / "prompt_samples"
PROMPT_SAMPLES_DIR.mkdir(exist_ok=True)


def save_prompt_sample(req_json):
    """每次请求，保存一份完整的 messages 到文件"""
    import datetime as _dt
    messages = req_json.get("messages") or []

    # 提取 NPC 名
    npc_name = "unknown"
    for m in messages:
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        if "角色设定" in content:
            match = re.search(r"你是\s*([^\s（(]+)", content)
            if match:
                npc_name = re.sub(r"[（(].*?[）)]", "", match.group(1)).strip()
                break

    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", npc_name)
    filename = f"{ts}-{safe_name}.json"
    filepath = PROMPT_SAMPLES_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(req_json, f, ensure_ascii=False, indent=2)

    print(f"[样本] 已保存: {filename}", flush=True)

# 卡片文件名正则：{数字ID}-{名字}.md
CARD_FILE_RE = re.compile(r"^(\d+)-(.+)\.md$")

# 游戏 prompt 里"角色设定"名字的提取正则
# 匹配 "## 角色设定\n你是 XXX（YYY）" 或 "你是 XXX（YYY）"
NPC_NAME_RE = re.compile(r"你是\s*([^\s（(]+)")

# ============================================================
# 四、状态
# ============================================================
recent_replies = []
RECENT_MAX = 3

FALLBACK_LINES = [
    "（她顿了顿，似乎在斟酌措辞。）",
    "（她垂下眼睫，指尖轻轻敲着桌面。）",
    "（她侧过头，目光落在别处，没有立刻接话。）",
    "（她轻轻吸了口气，好像在等你说下去。）",
]

load_ai_config(force=True)

print("=" * 60)
print("  core_proxy (Python 重写版 + 语义去重 + 残留清洗 + 角色交替修正 + 用户名片注入)  已就绪")
print(f"  监听:  http://{LISTEN_HOST}:{LISTEN_PORT}")
print(f"  上游:  {UPSTREAM_BASE}/v1/chat/completions")
print(f"  改写:  注入 {len(_AI['inject'])} 字符 + {len(_AI['rules'])} 条替换规则（改完存盘即生效）")
print(f"  名片:  {CARDS_DIR}")
print(f"  Key:   优先用游戏 Authorization，配置里有 FALLBACK_KEY: {bool(FALLBACK_KEY)}")
print("=" * 60)

app = Flask(__name__)

@app.errorhandler(Exception)
def _handle_exception(e):
    import traceback
    tb = traceback.format_exc()
    print("=" * 60, flush=True)
    print("[FATAL] 未捕获异常:", flush=True)
    print(tb, flush=True)
    print("=" * 60, flush=True)
    return Response(
        json.dumps({"error": "internal", "traceback": tb}, ensure_ascii=False),
        status=500,
        content_type="application/json; charset=utf-8"
    )
# ============================================================
# 五、用户名片：提取 NPC 名 + 读卡片 + 注入
# ============================================================

def extract_npc_name(messages):
    """
    从 messages 里找出当前对话的 NPC 名字。
    游戏的 system message 里有 "## 角色设定\n你是 苏倾盏（客栈老板娘）"。
    返回：NPC 名字字符串，或 None。
    """
    for m in messages:
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        # 只在含"角色设定"的消息里找
        idx = content.find("角色设定")
        if idx < 0:
            continue

        # 只截取"角色设定"这一节，避免把注入语里的
        # "你是活人，不是 AI" 当成 NPC 名字
        seg = content[idx:]
        nxt = seg.find("## ", len("角色设定"))
        if nxt > 0:
            seg = seg[:nxt]

        # 提取 "你是 XXX"
        match = NPC_NAME_RE.search(seg)
        if match:
            name = match.group(1).strip()
            # 去掉"（客栈老板娘）"之类
            name = re.sub(r"[（(].*?[）)]", "", name).strip()
            if name:
                return name
    return None


def find_card_files(npc_name):
    """
    在卡片目录里找所有 "ID-{npc_name}.md" 文件，按 ID 从小到大排序。
    返回：Path 列表
    """
    if not CARDS_DIR.is_dir():
        return []
    result = []
    for f in CARDS_DIR.iterdir():
        if not f.is_file() or not f.name.endswith(".md"):
            continue
        m = CARD_FILE_RE.match(f.name)
        if not m:
            continue
        card_name = m.group(2)
        if card_name == npc_name:
            result.append((int(m.group(1)), f))
    result.sort(key=lambda x: x[0])
    return [p for _, p in result]


def read_user_card(filepath):
    """
    读 .md 文件，提取 "## 用户名片" 区块的内容（不含 HTML 注释）。
    返回：字符串（空字符串表示没有内容）。
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception as e:
        print(f"[名片] 读取失败 {filepath}: {e}")
        return ""

    lines = raw.split("\n")
    section = None
    skip_comment = False
    user_lines = []

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("## "):
            h2 = stripped[3:].strip()
            if h2.startswith("用户名片"):
                section = "user_card"
            else:
                section = "other"
            skip_comment = False
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            section = "title"
            continue

        if section == "user_card":
            if "<!--" in line:
                skip_comment = True
            if skip_comment:
                if "-->" in line:
                    skip_comment = False
                continue
            user_lines.append(line)

    return "\n".join(user_lines).strip()


def load_user_card_for_npc(npc_name):
    """
    给定 NPC 名字，返回其"用户名片"内容（优先取 ID 最小的、且用户名片非空的卡片）。
    返回：字符串 或 None
    """
    files = find_card_files(npc_name)
    if not files:
        return None
    for fp in files:
        content = read_user_card(fp)
        if content:
            return content
    # 所有卡片的用户名片都空
    return None


def inject_user_card(req_json, npc_name=None):
    """
    读该 NPC 的用户名片，注入到 prompt。
    npc_name 由 prepare_messages() 统一解析后传入，避免重复解析、解析口径不一致。
    返回：注入的字符数（0 表示没注入）
    """
    messages = req_json.get("messages") or []
    if not messages:
        return 0

    if npc_name is None:
        npc_name = extract_npc_name(messages)

    if not npc_name:
        print("[名片] 未提取到 NPC 名字，跳过注入", flush=True)
        return 0

    global CURRENT_NPC_NAME
    CURRENT_NPC_NAME = npc_name

    user_card = load_user_card_for_npc(npc_name)
    if not user_card:
        print(f"[名片] {npc_name} 没有用户名片内容，跳过注入")
        return 0

    # 找到含"角色设定"的那条 system message，在它后面追加"## 用户补充"
    for m in messages:
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        if "角色设定" not in content:
            continue

        # 构造注入文本。
        # 标题写成"要求"而不是"补充"：模型对"补充"会当成可有可无的附注，
        # 对"要求"才会当约束执行。
        # 位置放在 system 末尾：游戏的「任务 / 输出格式 / 本轮状态」都在后面，
        # 插在「角色设定」旁边会被后面这些指令盖住；放末尾既离生成位置最近，
        # 也不影响前面的前缀缓存（前面一字不改）。
        inject_text = (
            "\n\n## 角色演绎要求（优先于以上所有属性与场景描述）\n"
            + user_card
            + "\n\n以上每条都是硬性要求，回复时逐条遵守，不要用套话敷衍。\n"
        )
        new_content = content + inject_text

        m["content"] = new_content
        print(f"[名片] 已注入 {npc_name} 的用户名片（{len(user_card)} 字符）", flush=True)
        return len(user_card)

    return 0

# ============================================================
# 六、工具函数（原有）
# ============================================================

def strip_think(text):
    if not text:
        return text
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"Thinking Process:.*?(?=[\u4e00-\u9fff])", "", text, flags=re.S)
    text = text.replace("<think>", "").replace("</think>", "")
    text = re.sub(r"\n?\d+\.\s*\*\*[^*]+\*\*:.*?(?=[\u4e00-\u9fff]{5,})", "", text, flags=re.S)
    return text


def normalize_for_compare(s):
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    for ch in "“”\"‘’'（）()":
        s = s.replace(ch, "")
    return s


def clean_tail(s):
    if not s:
        return s
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)

    cut = re.search(
        r'["”]\s*[,，]\s*"\s*(emotion|control|observe|intent|content)\s*"',
        s)
    if cut:
        s = s[:cut.start()]

    s = re.sub(
        r'[,，]?\s*"\s*(emotion|control|observe|intent|content)\s*"\s*[:：].*$',
        "", s, flags=re.S)

    s = re.sub(
        r'[.。…]?\s*"\s*(gift|item|gift_item|emotion|control|observe|intent|content)\s*"\s*[:：].*$',
        "", s, flags=re.S)

    s = re.sub(r'[\s"”]+$', '', s)
    s = re.sub(r'[,，]\s*$', '', s)
        # 清理 JSON 拼接残留："...某句话..."+"..."
    s = re.sub(r'"\s*\+\s*"', '', s)       # "xxx"+"yyy" → xxxyyy
    s = re.sub(r'\\"\s*\+\s*\\"', '', s)   # 同上的转义版
    s = re.sub(r'^\s*"\s*\+\s*"', '', s)   # 行首的 "+ "
    s = re.sub(r'"\s*\+\s*"\s*$', '', s)   # 行尾的 "+ "
    s = re.sub(r'^\s*"\+"?', '', s)        # 行首的 "+
    s = re.sub(r'"\s*\+\s*"', '', s)
    return s


def extract_content_value(text, field="content"):
    key = f'"{field}"'
    idx = text.find(key)
    if idx < 0:
        return None

    i = idx + len(key)
    while i < len(text) and text[i] not in '":：':
        i += 1
    while i < len(text) and text[i] in '":：':
        i += 1
    if i >= len(text):
        return None

    out = []
    j = i
    while j < len(text):
        ch = text[j]
        if ch == '\\' and j + 1 < len(text):
            out.append(text[j:j+2])
            j += 2
            continue
        if ch == '"':
            k = j + 1
            while k < len(text) and text[k] in " \t\r\n":
                k += 1
            if k >= len(text) or text[k] in ",}，":
                break
            else:
                out.append(ch)
                j += 1
                continue
        if ch in ",}" and (j == i or text[j-1] != '\\'):
            break
        out.append(ch)
        j += 1

    raw = "".join(out)
    raw = (raw
           .replace('\\"', '"')
           .replace("\\n", "\n")
           .replace("\\r", "")
           .replace("\\\\", "\\"))
    return raw


def normalize_response(raw_body):
    clean_text = ""
    extra_fields = None

    temp = strip_think(raw_body).strip()

    outer = None
    try:
        outer = json.loads(temp)
    except Exception:
        outer = None

    if isinstance(outer, dict):
        if "choices" in outer and isinstance(outer["choices"], list) and outer["choices"]:
            msg = outer["choices"][0].get("message", {})
            inner_text = msg.get("content", "")
            if not isinstance(inner_text, str):
                inner_text = ""
            inner_text = strip_think(inner_text).strip()

            # 上游返回空 content（内容过滤 / 异常，响应体只有几百字节）时，
            # 绝不能再往下走到函数末尾的 return clean_tail(temp)——
            # 那会把整个响应体当台词返回，游戏就直接把原始 JSON 显示出来了
            # （2026-10-24 实测定 bug：578B 空响应 → 游戏显示 chat.completion JSON，
            #  而且这段 JSON 会进对话历史和游戏记忆，污染持续）。
            # 返回空字符串，让上层 too_short 重试 + FALLBACK_LINES 过渡句兜底。
            if not inner_text:
                print("[清洗] 上游 content 为空/非文本 → 返回空，触发重试与兜底")
                return "", None

            if True:
                try:
                    inner = json.loads(inner_text)
                    if isinstance(inner, dict) and isinstance(inner.get("content"), str):
                        return clean_tail(inner["content"]), inner
                except Exception:
                    pass

                if '"content"' in inner_text:
                    value = extract_content_value(inner_text, "content")
                    if value and value.strip():
                        extra = {}
                        for f in ("emotion", "control"):
                            v = extract_content_value(inner_text, f)
                            if v:
                                extra[f] = v
                        if extra:
                            extra_fields = extra
                        print("[清洗] 内层半截 JSON → 括号配对扫描")
                        return clean_tail(value), extra_fields

                if inner_text:
                    return clean_tail(inner_text), extra_fields

        if isinstance(outer.get("content"), str):
            return clean_tail(outer["content"]), outer

    if isinstance(temp, str) and '"content"' in temp:
        value = extract_content_value(temp, "content")
        if value and value.strip():
            extra = {}
            for f in ("emotion", "control"):
                v = extract_content_value(temp, f)
                if v:
                    extra[f] = v
            if extra:
                extra_fields = extra
            print("[清洗] 半截 JSON → 括号配对扫描")
            return clean_tail(value), extra_fields

    return clean_tail(temp), extra_fields


# 逐行模糊匹配：游戏改版时常常只改几个字、或把句子拆成列表项，
# 精确匹配就静默失效（历史 bug：12 条规则全部不生效）。
# 这里在"精确 / 去空白 / CRLF"三级兜底之后再加一级：
# 找相似度最高的一行，够像就整行替换（保留行首的 - 、1. 之类符号）。
FUZZY_LINE_RATIO = 0.70
FUZZY_LINE_MIN_LEN = 12


def _norm_for_match(s):
    """归一化：去列表符号、去空白、去常见中英文标点，只留字。"""
    s = re.sub(r'^\s*(?:[-*•]|\d+[.)])\s*', '', str(s).strip())
    return re.sub(r'[\s，。、；：！？,.;:!?（）()\[\]「」“”"\'"]', '', s)


def _fuzzy_replace_line(text, old, new):
    """整行近似替换。返回 (新文本, 是否替换)。"""
    old_n = _norm_for_match(old)
    if len(old_n) < FUZZY_LINE_MIN_LEN:
        return text, False

    lines = text.split("\n")
    best_i, best_r = -1, 0.0
    for i, ln in enumerate(lines):
        core = ln.strip()
        if not core:
            continue
        r = difflib.SequenceMatcher(None, old_n, _norm_for_match(core)).ratio()
        if r > best_r:
            best_r, best_i = r, i

    if best_i < 0 or best_r < FUZZY_LINE_RATIO:
        return text, False

    line = lines[best_i]
    m = re.match(r'^(\s*(?:[-*•]|\d+[.)])\s*)', line)
    prefix = m.group(1) if m else re.match(r'^(\s*)', line).group(1)
    lines[best_i] = prefix + new
    print(f"[规则] 模糊命中（相似度 {best_r:.2f}）: {line.strip()[:24]}…", flush=True)
    return "\n".join(lines), True


def apply_rules(text, rules=None):
    """应用替换规则。

    匹配顺序（越来越宽松，避免游戏更新后静默失效）：
      1. 原文精确匹配
      2. 两边去掉首尾空白后再匹配
      3. 原文的 \\n 换成 \\r\\n 再匹配（游戏 prompt 行尾可能不同）

    返回 (新文本, 命中条数, 命中明细)
    """
    if not text:
        return text, 0, []

    pairs = _AI["rules"] if rules is None else rules
    count = 0
    detail = []

    for old, new in pairs:
        if not old:
            continue
        hit = None
        if old in text:
            hit = old
        else:
            o2 = old.strip()
            if o2 and o2 != old and o2 in text:
                hit = o2
            elif "\n" in old and old.replace("\n", "\r\n") in text:
                hit = old.replace("\n", "\r\n")

        if hit is None:
            # 第四级：整行近似（游戏改了几个字也能替换）
            text2, done = _fuzzy_replace_line(text, old, new)
            if done:
                text = text2
                count += 1
                detail.append(old[:30].replace("\n", " "))
            continue

        if hit == old:
            text = text.replace(old, new)
        elif hit == old.strip():
            text = text.replace(hit, new.strip())
        else:
            text = text.replace(hit, new.replace("\n", "\r\n"))

        count += 1
        detail.append(old[:30].replace("\n", " "))

    return text, count, detail


def apply_rules_to_messages(messages):
    """替换规则只作用于【游戏原始的 system 消息】。

    历史问题：早期实现只替换最后一条 user 消息，而游戏把"默认一两句短句…"这类
    要求全写在 system 里 → 12 条规则从来没生效过（日志永远是 rules=0）。
    """
    total = 0
    detail = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "system":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        new_content, n, det = apply_rules(content)
        if n:
            m["content"] = new_content
            total += n
            detail.extend(det)
    return total, detail


def similarity_ratio(a, b):
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def longest_common_substring_len(a, b):
    if not a or not b:
        return 0
    matcher = SequenceMatcher(None, a, b)
    match = matcher.find_longest_match(0, len(a), 0, len(b))
    return match.size


def is_repetition(new_text, history):
    if not new_text or not history:
        return False
    a = normalize_for_compare(new_text)
    if len(a) < 10:
        return False
    for old in history:
        b = normalize_for_compare(old)
        if len(b) < 10:
            continue
        head_len = min(25, len(a), len(b))
        if head_len >= 15 and a[:head_len] == b[:head_len]:
            return True
        lcs = longest_common_substring_len(a, b)
        if lcs >= max(15, int(len(a) * 0.6)):
            return True
        ratio = similarity_ratio(a, b)
        len_ratio = min(len(a), len(b)) / max(len(a), len(b))
        if ratio >= 0.75 and len_ratio >= 0.6:
            return True
    return False


def strip_repetition_prefix(new_text, history):
    if not new_text or not history:
        return new_text
    a = normalize_for_compare(new_text)
    best = 0
    for old in history:
        b = normalize_for_compare(old)
        n = min(len(a), len(b))
        i = 0
        while i < n and a[i] == b[i]:
            i += 1
        best = max(best, i)
    if best < 12:
        return new_text
    cut_idx = -1
    for j in range(1, len(new_text)):
        if new_text[j] in "（“\"": 
            cut_idx = j
            break
    if cut_idx > 0:
        return new_text[cut_idx:]
    cut_at = max(1, int(len(new_text) * best / max(1, len(a))))
    return new_text[cut_at:]


# ============================================================
# 七、改写 messages
# ============================================================

def fix_alternation(messages):
    if not messages:
        return messages

    fixed = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")

        if role == "system":
            fixed.append(m)
            continue

        if fixed:
            last = fixed[-1]
            last_role = last.get("role")
            if last_role == role and role in ("user", "assistant"):
                last_content = last.get("content", "")
                if isinstance(last_content, str) and isinstance(content, str):
                    last["content"] = last_content + "\n" + content
                else:
                    last["content"] = str(last_content) + "\n" + str(content)
                continue
        fixed.append(m)

    return fixed


def rewrite_messages(req_json):
    """合并 system + 前置注入语 + 强制角色交替。

    替换规则不在这里做 —— 由 prepare_messages() 在注入名片/记忆之前、
    只对游戏原始 system 消息应用一次，避免误伤后面自己加的块。
    """
    messages = req_json.get("messages") or []
    if not messages:
        return 0

    # 1. 拆分 system / others
    system_parts = []
    others = []
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            system_parts.append(m["content"])
        else:
            others.append(m)

    # 2. 强制角色交替
    others = fix_alternation(others)

    # 3. 合并所有 system，注入语放最前面
    parts = []
    if _AI["inject"]:
        parts.append(_AI["inject"])
    parts.extend(system_parts)
    parts.append("必须严格按 system 消息在最前、然后 user 和 assistant 交替的顺序回复。")
    final_system = "\r\n\r\n".join(parts)

    # 4. 重排
    new_messages = [{"role": "system", "content": final_system}]
    new_messages.extend(others)
    req_json["messages"] = new_messages

    roles = [m.get("role") for m in new_messages]
    print(f"[REWRITE] 角色顺序: {roles[:8]}")

    return 0

# ==================== 长期记忆核心注入 ====================
# 游戏自己也记「## 过往要事」，形如：
#   ## 过往要事
#   鱼你 在你这儿做过 3 笔买卖，算是老主顾了。
#   鱼你 前后送过你 1 件东西。
# 每个角色几条，只增不减。我们把长期记忆写进这一节，
# 这样 system 里只留下一处记忆，位置也从末尾挪到中段。
PAST_EVENTS_RE = re.compile(
    r"^##[ \t]*过往要事[^\r\n]*\r?\n(.*?)(?=\r?\n##[ \t]|\Z)", re.M | re.S
)


def parse_game_events(messages):
    """取出游戏侧记忆（「## 过往要事」里的条目）。没有这一节就返回 []。"""
    lines = []
    for m in messages or []:
        if m.get("role") != "system":
            continue
        c = m.get("content")
        if not isinstance(c, str) or "过往要事" not in c:
            continue
        mm = PAST_EVENTS_RE.search(c)
        if not mm:
            continue
        for ln in re.split(r"\r?\n", mm.group(1)):
            ln = ln.strip()
            if ln:
                lines.append(ln)
    return lines


def _compress_past_events(body, max_chars):
    """游戏「过往要事」只增不减，久了能到 1000+ 字（占 system 20%）。

    这里按字数上限保留最近若干条，更早的折成一行提示。
    换档判断读的是原文（在 check_save_guard 里），所以压缩不影响它。
    """
    if max_chars <= 0 or len(body) <= max_chars:
        return body

    lines = [ln for ln in body.split("\n") if ln.strip()]
    kept, total = [], 0
    for ln in reversed(lines):          # 从最近的一条往前留
        if kept and total + len(ln) > max_chars:
            break
        kept.append(ln)
        total += len(ln) + 1
    kept.reverse()

    omitted = len(lines) - len(kept)
    if omitted > 0:
        kept.insert(0, f"（更早的 {omitted} 条已省略）")
    return "\n".join(kept)


def _shrink_past_events(messages):
    """把「## 过往要事」按上限原地压缩。

    不管记忆落在哪（past_events / append），这一段都值得压：
    游戏侧只增不减，久了 1000+ 字，占 system 20%。
    """
    cap = int(_AI.get("past_events_max_chars", 800))
    if cap <= 0:
        return False

    for m in messages:
        if m.get("role") != "system":
            continue
        c = m.get("content")
        if not isinstance(c, str) or "过往要事" not in c:
            continue
        mm = PAST_EVENTS_RE.search(c)
        if not mm:
            continue
        body = mm.group(1).strip()
        shrunk = _compress_past_events(body, cap)
        if shrunk == body:
            return False
        a, b = mm.span(1)
        m["content"] = c[:a] + shrunk + c[b:]
        print(f"[记忆] 「过往要事」{len(body)} 字 → {len(shrunk)} 字", flush=True)
        return True
    return False


def _place_into_past_events(messages, memory_text):
    """把记忆块写进「## 过往要事」那一节，游戏原有条目保留。
    找不到这一节返回 False（调用方退回 system 末尾追加）。"""
    for m in messages:
        if m.get("role") != "system":
            continue
        c = m.get("content")
        if not isinstance(c, str) or "过往要事" not in c:
            continue
        mm = PAST_EVENTS_RE.search(c)
        if not mm:
            continue
        sep = "\r\n" if "\r\n" in c else "\n"
        body = mm.group(1).strip()
        block = f"【长期记忆（请自然带入）】{sep}{memory_text}"
        new_body = f"{body}{sep}{sep}{block}" if body else block
        a, b = mm.span(1)
        m["content"] = c[:a] + new_body + sep + c[b:]
        return True
    return False


def has_past_events_section(messages):
    """游戏这次到底有没有给「## 过往要事」这一节。
    没有（游戏改版 / 该角色还没有过往）≠ 换了存档，不能当成换档处理。"""
    for m in messages or []:
        if m.get("role") != "system":
            continue
        c = m.get("content")
        if isinstance(c, str) and "过往要事" in c and PAST_EVENTS_RE.search(c):
            return True
    return False


def check_save_guard(npc_id, npc_name, messages, persist=True):
    """换档保护：比对本轮游戏侧记忆与上次记下的基线。
    只有旧条目几乎全对不上时才判"另一份存档"，避免正常推进误伤。"""
    if not _AI.get("save_guard", True):
        return {"status": "off", "blocked": False, "kept": 0, "total": 0, "ratio": 1.0}
    if not has_past_events_section(messages):
        # 连这一节都没有，谈不上换档；交给落位逻辑回落到末尾追加
        return {"status": "no_section", "blocked": False, "kept": 0, "total": 0, "ratio": 1.0}
    try:
        lines = parse_game_events(messages)
        return memory_manager.check_game_events(npc_id, npc_name, lines, persist=persist)
    except Exception as e:
        print(f"[存档] 检查失败，本轮照常注入: {e}", flush=True)
        return {"status": "error", "blocked": False, "kept": 0, "total": 0, "ratio": 1.0}


def inject_memory_to_messages(req_json, npc_name=None, persist=True):
    """把长期记忆注入到 system 提示词。

    npc_name 由 prepare_messages() 在改写前解析好后传进来；不传则就地解析（兜底）。
    persist=False 表示只预览、不写盘（/_preview 用）。
    返回 dict：npc_id / npc_name / chars / slot / guard
    """
    empty = {"npc_id": None, "npc_name": None, "chars": 0, "slot": "", "guard": "skip"}
    messages = req_json.get("messages") or []
    if not messages:
        return empty

    global CURRENT_NPC_NAME

    if npc_name is None:
        npc_name = extract_npc_name(messages)
    CURRENT_NPC_NAME = npc_name

    if not npc_name:
        print("[记忆] 本轮未提取到 NPC 名字，跳过注入", flush=True)
        return empty

    card_files = find_card_files(npc_name)
    if not card_files:
        print(f"[记忆] 卡片目录里找不到 {npc_name}，跳过注入", flush=True)
        return empty

    try:
        npc_id = int(card_files[0].name.split("-", 1)[0])
    except (ValueError, IndexError):
        print(f"[记忆] 无法解析 {npc_name} 的 ID，跳过注入", flush=True)
        return empty

    # 先过换档保护：这一轮的游戏侧记忆还是不是上次那一档
    guard = check_save_guard(npc_id, npc_name, messages, persist)
    if guard.get("blocked"):
        print(f"[存档] 游戏侧记忆只剩 {guard['kept']}/{guard['total']} 条对得上，"
              f"疑似切到了另一份存档 → 本轮不注入长期记忆。"
              f"到「长期记忆」页确认后即可恢复。", flush=True)
        return {"npc_id": npc_id, "npc_name": npc_name, "chars": 0,
                "slot": "", "guard": "blocked"}

    # 加载记忆并拼装（预算从 ai_config.json 读，默认 2000 字）
    data = memory_manager.load_memory(npc_id, npc_name)
    user_say = ""
    for m in reversed(messages):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            user_say = m["content"]
            break

    memory_text = memory_manager.build_injection_text(
        data,
        budget_chars=_AI["memory_budget"],
        hint=user_say if _AI["memory_hint"] else "",
    )

    if not memory_text.strip():
        return {"npc_id": npc_id, "npc_name": npc_name, "chars": 0,
                "slot": "", "guard": guard["status"]}

    # 先把游戏侧记忆压到上限（append 模式也做，省 token）
    try:
        _shrink_past_events(messages)
    except Exception as e:
        print(f"[记忆] 压缩过往要事失败，跳过: {e}", flush=True)

    slot = "append"
    placed = False
    if _AI.get("memory_slot") == "past_events":
        try:
            placed = _place_into_past_events(messages, memory_text)
        except Exception as e:
            print(f"[记忆] 写入「过往要事」失败，改用末尾追加: {e}", flush=True)
        if placed:
            slot = "past_events"

    if not placed:
        mem_block = f"\n\n【长期记忆（请自然带入）】\n{memory_text}\n"
        sys_content = ""
        for m in messages:
            if m.get("role") == "system":
                sys_content = m.get("content", "")
                break
        if sys_content:
            messages[0]["content"] = sys_content + mem_block
        else:
            messages.insert(0, {"role": "system", "content": mem_block})
        req_json["messages"] = messages

    return {"npc_id": npc_id, "npc_name": npc_name, "chars": len(memory_text),
            "slot": slot, "guard": guard["status"]}
def update_memory_after_chat(npc_id, npc_name, user_text, ai_text):
    # 后台合并线程可能同时在读写同一个记忆文件，整段加锁
    with memory_manager.LOCK:
        return _update_memory_locked(npc_id, npc_name, user_text, ai_text)


# 合并失败后先歇一会儿再试，否则 API 一直拒答就会每轮都白烧一次请求
_merge_backoff = {}          # npc_id -> 可以再次尝试的时间戳
MERGE_BACKOFF_SEC = 300


def _run_merge(npc_id, npc_name, logs_str, url, key):
    """后台线程里跑一次总结；失败就退避 5 分钟。"""
    ok = memory_manager.do_auto_merge(
        npc_id, npc_name, logs_str, url, key,
        max_tokens=_AI.get("memory_merge_max_tokens", 4000),
    )
    if not ok:
        _merge_backoff[npc_id] = time.time() + MERGE_BACKOFF_SEC
        print(f"[记忆] {npc_name} 本次总结失败，{MERGE_BACKOFF_SEC // 60} 分钟内不再重试", flush=True)
    else:
        _merge_backoff.pop(npc_id, None)


def _update_memory_locked(npc_id, npc_name, user_text, ai_text):
    """对话后更新记忆（老化 + 12 轮自动合并）"""
    if not npc_id or not npc_name:
        return
    data = memory_manager.load_memory(npc_id, npc_name)
    
    # 1. 防降级：如果本轮用户提到了某事实，清零老化
    memory_manager.reset_fact_age(data, user_text)
    
    # 2. 老化处理（24轮降级/48轮删除）
    data, changed = memory_manager.process_aging(npc_id, npc_name, data)
    
    # 3. 轮次记录与12轮合并触发
    if "recent_logs" not in data: data["recent_logs"] = []
    data["recent_logs"].append(f"玩家: {user_text}\nAI: {ai_text}")
    
    merge_every = int(_AI.get("memory_merge_every", 12))
    cooling = _merge_backoff.get(npc_id, 0) > time.time()

    if len(data["recent_logs"]) >= merge_every and not cooling:
        # 上次总结失败退回的对话也要一起合并，否则永远补不上
        backlog = str(data.get("merge_backlog") or "").strip()
        logs_str = ("\n\n".join(data["recent_logs"]) + ("\n\n" + backlog if backlog else ""))

        import threading
        # 总结默认走上游；配置了 memory_merge_url 就单独走那个地址
        # （接第三方 API 时可以把总结固定到本地模型，避免审核影响记忆质量）
        upstream_url = _AI.get("memory_merge_url") or (UPSTREAM_BASE + "/v1/chat/completions")
        f_key = FALLBACK_KEY

        threading.Thread(
            target=_run_merge,
            args=(npc_id, npc_name, logs_str, upstream_url, f_key),
            daemon=True,
        ).start()
        data["recent_logs"] = []   # 清空队列；失败时 do_auto_merge 会退回 merge_backlog
        data.pop("merge_backlog", None)
        print(f"[记忆] {npc_name} 攒够 {merge_every} 轮，后台合并剧情（{len(logs_str)} 字）...", flush=True)
        
    # 4. 保存回文件
    memory_manager.save_memory(npc_id, npc_name, data)


# ============================================================
# 八、提示词组装（转发和预览共用同一条流水线）
# ============================================================

def prepare_messages(req_json, with_memory=True, with_card=True, dry_run=False):
    """把游戏原始 messages 加工成最终发给模型的 messages（原地修改）。

    顺序很关键：
      1. 先从【原始 system】解析 NPC 名（此时还没塞注入语，不会认错人）
      2. 替换规则只作用于原始 system
      3. 注入用户名片（要在合并 system 之前，靠 "## 说话习惯" 定位插入点）
      4. 合并 system + 前置注入语
      5. 注入长期记忆（追加到合并后的 system 末尾）

    返回 stats 字典，供日志和 /_preview 使用。
    """
    load_ai_config()          # mtime 变了就重读，改完存盘即生效

    messages = req_json.get("messages") or []

    # 游戏内部还有一套自己的记忆系统：它会把"记忆压缩请求"也发到这个接口，
    # 那种请求的 system 是「你是对话记忆压缩器…」，没有「## 角色设定」。
    # 对它注入「你是活人，不是 AI」或套替换规则，会直接破坏压缩任务
    # （实测：注入语把"不超过 150 字"顶成了"通常回复 150 字以上"）。
    # 所以非对话请求一律原样放行，一个字节都不改。
    is_dialogue = any(
        m.get("role") == "system"
        and isinstance(m.get("content"), str)
        and "角色设定" in m["content"]
        for m in messages
    )
    if not is_dialogue:
        print("[跳过] 非对话请求（system 里没有「## 角色设定」），原样转发", flush=True)
        sys_len = 0
        if messages and messages[0].get("role") == "system":
            c0 = messages[0].get("content")
            sys_len = len(c0) if isinstance(c0, str) else 0
        return {
            "npc_name": None, "npc_id": None,
            "rules_hit": 0, "rules_detail": [],
            "inject_chars": 0, "card_chars": 0, "memory_chars": 0,
            "system_chars": sys_len, "messages": len(messages),
            "passthrough": True,
        }

    npc_name = extract_npc_name(messages)

    rules_hit, rule_detail = apply_rules_to_messages(messages)

    # 先注入长期记忆，再注入角色卡：两块都追加在 system 末尾，
    # 名片放最后，离生成位置最近，权重最高（游戏的任务/输出格式盖不住它）
    rewrite_messages(req_json)

    npc_id, npc_label, mem_len = (None, None, 0)
    mem_slot, guard_status = "", ""
    if with_memory:
        r = inject_memory_to_messages(req_json, npc_name, persist=not dry_run)
        npc_id, npc_label, mem_len = r["npc_id"], r["npc_name"] or npc_name, r["chars"]
        mem_slot, guard_status = r["slot"], r["guard"]
        if npc_id and mem_len:
            print(f"[记忆] 已注入 {npc_label}({npc_id}) 的长期记忆（{mem_len} 字）→ {mem_slot}", flush=True)
    else:
        npc_label = npc_name

    card_len = 0
    if with_card and _AI["inject_user_card"]:
        try:
            card_len = inject_user_card(req_json, npc_name)
        except Exception as e:
            import traceback
            print(f"[名片] 注入失败: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
    elif not _AI["inject_user_card"]:
        print("[名片] 用户名片注入已关闭，跳过", flush=True)

    final_messages = req_json.get("messages") or []
    sys_len = len(final_messages[0]["content"]) if final_messages and final_messages[0].get("role") == "system" else 0

    return {
        "npc_name": npc_name or npc_label,
        "npc_id": npc_id,
        "rules_hit": rules_hit,
        "rules_detail": rule_detail,
        "inject_chars": len(_AI["inject"]),
        "card_chars": card_len,
        "memory_chars": mem_len,
        "memory_slot": mem_slot,
        "save_guard": guard_status,
        "system_chars": sys_len,
        "messages": len(final_messages),
    }


# ============================================================
# 九、转发
# ============================================================

# ============================================================
# 历史裁剪
# ============================================================
# 游戏不限制对话轮数，历史会一直累积（实测：一次会话从 6 条涨到 9 条仍在涨），
# 最终撑爆上下文窗口——token 越烧越多，模型也会开始"忘事"。
#
# 策略：
#   · system 全留（那是 prompt 主体：世界观/任务/角色/动态状态）
#   · 对话从最新往旧装，装进预算为止
#   · 无论如何保底保留最近 keep_turns 轮（一 user + 一 assistant 算一轮）
#   · 只裁不加：不在历史里插"前文已省略"之类的提示，避免游戏解析异常。
#     早期的内容游戏自己会通过记忆（过往要事）兜底。
#
# 阈值走 ai_config.json：history_max_chars（默认 24000，0=不裁）、
# history_keep_turns（默认 6），改完存盘即生效。
def trim_history(messages, max_chars=None, keep_turns=None):
    cfg = load_ai_config()
    if max_chars is None:
        max_chars = cfg.get("history_max_chars", 24000)
    if keep_turns is None:
        keep_turns = cfg.get("history_keep_turns", 6)
    if not max_chars or not messages or len(messages) < 3:
        return messages, 0

    systems = [m for m in messages if m.get("role") == "system"]
    history = [m for m in messages if m.get("role") != "system"]
    if len(history) <= keep_turns * 2:
        return messages, 0

    sys_chars = sum(len(str(m.get("content") or "")) for m in systems)
    budget = max(2000, max_chars - sys_chars)

    total = sum(len(str(m.get("content") or "")) for m in history)
    if total <= budget:
        return messages, 0

    # 从最新往旧累加；保底 keep_turns*2 条必须留住（哪怕超预算）
    kept, acc = [], 0
    for m in reversed(history):
        n = len(str(m.get("content") or ""))
        if acc + n > budget and len(kept) >= keep_turns * 2:
            break
        acc += n
        kept.append(m)
    kept.reverse()

    dropped = len(history) - len(kept)
    if dropped <= 0:
        return messages, 0
    return systems + kept, dropped


# ============================================================
# 污染检测与清洗
# ============================================================
# 上游返回空 content 时，游戏（beta）会把整个响应体当 NPC 台词显示并存档。
# 这段 JSON 随后进入对话历史，还会被游戏塞进自己的记忆 content 字段，
# 之后每一轮都带着它——污染会一直滚下去。
#
# 判据取最严格的响应体特征（去空格后含 "object":"chat.completion"），
# 正常台词几乎不可能误撞。命中的 assistant 消息替换为占位文本：
# 那段内容本来就不是 NPC 说过的话，替换不损失任何真实信息。
POLLUTION_MARKER = '"object":"chat.completion"'
POLLUTION_PLACEHOLDER = "（前次回复异常，已略过）"


def clean_polluted_history(messages):
    """把混进 assistant 历史的原始响应 JSON 替换成占位文本。
    返回 (messages, fixed_count)。messages 原地修改。"""
    fixed = 0
    for m in messages:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if not isinstance(c, str):
            continue
        s = c.strip()
        if not s.startswith("{"):
            continue
        # 归一化要去反斜杠：游戏记忆 JSON 里响应体是转义字符串
        # （\"object\":\"chat.completion\"），不去反斜杠就匹配不上。
        norm = s.replace(" ", "").replace("\\", "")
        # 二次判据：真实响应体必然还带 choices / created / finish_reason。
        # 只凭 object 标记会误伤"台词里恰好引用该字样"的情况（实测会撞到），
        # 加上这条后正常台词不会被替换。
        if (POLLUTION_MARKER in norm
                and ('"choices":[' in norm or '"created":' in norm
                     or '"finish_reason"' in norm)):
            m["content"] = POLLUTION_PLACEHOLDER
            fixed += 1
    return messages, fixed


# ============================================================
# 游戏结构自检
# ============================================================
# 2026-10-24 实测结论：段落集合**因 NPC 而异**（章籽梦 7 段 / 赤遥 6 段 / 索雅 5 段），
# 且关系状态、游戏状态这类动态段每轮都变。
# 所以自检基线必须是「结构画像」（有哪些段落、几条 system、什么形状），
# 绝不能按原文文本比对——那样游戏改一个字就误报，动态段更是每轮都误报。
#
# 检查四类，按严重度排序：
#   1. NPC 名解析失败  → 名片与长期记忆全部失效，最严重
#   2. prompt 结构变化 → 游戏改了组装方式（新增/消失段落）
#   3. 规则命中骤降    → 游戏改了文案，替换规则静默失效
#   4. 异常响应计数    → 空 content、污染清洗、历史裁剪的发生次数
_HEALTH = {
    "requests": 0,
    "npc_name_fail": 0,
    "structure_changes": 0,
    "empty_response": 0,
    "polluted_cleaned": 0,
    "trimmed_total": 0,
    "rules_hit_recent": [],
    "last_profile": None,
    "last_fingerprint": "",
    "alerts": [],
}
HEALTH_ALERT_MAX = 20


def extract_structure_profile(messages):
    """提取结构画像：只取形状，不取内容。
    动态段（关系/库存/今日行为）每轮都变，所以只看段落标题集合和 system 形状。"""
    systems = [m for m in messages if m.get("role") == "system"]
    headings = []
    for m in systems:
        for h in re.findall(r'^##[^\n\r]*', str(m.get("content") or ""), re.M):
            headings.append(h.strip())
    return {
        "system_count": len(systems),
        "system_lens": [len(str(m.get("content") or "")) for m in systems],
        "headings": headings,
        "msg_count": len(messages),
    }


def _profile_fingerprint(profile):
    """指纹：段落标题去重集合 + system 条数。不含长度（动态段长度每轮变）。"""
    return "|".join(sorted(set(profile.get("headings") or []))) + f"#{profile.get('system_count')}"


def _add_alert(level, msg):
    _HEALTH["alerts"].append({"level": level, "msg": msg,
                              "ts": datetime.now().strftime("%H:%M:%S")})
    if len(_HEALTH["alerts"]) > HEALTH_ALERT_MAX:
        del _HEALTH["alerts"][:-HEALTH_ALERT_MAX]
    tag = {"error": "[自检✗]", "warn": "[自检⚠]", "info": "[自检·]"}.get(level, "[自检]")
    print(f"{tag} {msg}", flush=True)


def run_self_check(stats, messages):
    """每轮转发前跑一次。只记录和告警，不改变请求内容。"""
    try:
        _HEALTH["requests"] += 1

        # 1) NPC 名解析失败：名片和记忆都不会注入，等于核心功能全废
        if not stats.get("npc_name"):
            _HEALTH["npc_name_fail"] += 1
            _add_alert("error",
                       "NPC 名解析失败，本轮名片与长期记忆都不会注入。"
                       "多半是游戏改了 system 里「## 角色设定」的写法。")

        # 2) prompt 结构变化：和上一次画像比对段落集合
        prof = extract_structure_profile(messages)
        fp = _profile_fingerprint(prof)
        if _HEALTH["last_fingerprint"] and fp != _HEALTH["last_fingerprint"]:
            old = set(_HEALTH["last_profile"].get("headings") or [])
            new = set(prof.get("headings") or [])
            added = sorted(new - old)
            removed = sorted(old - new)
            if added or removed:
                _HEALTH["structure_changes"] += 1
                parts = []
                if added:
                    parts.append(f"新增段落 {added}")
                if removed:
                    parts.append(f"消失段落 {removed}")
                _add_alert("warn", "prompt 结构变化：" + "，".join(parts) +
                           "。若这是游戏更新导致的，请到「预览效果」核对规则命中。")
        _HEALTH["last_profile"] = prof
        _HEALTH["last_fingerprint"] = fp

        # 3) 规则命中骤降：最近 8 轮全为 0，而之前有过命中 → 文案大概变了
        hit = int(stats.get("rules_hit") or 0)
        recent = _HEALTH["rules_hit_recent"]
        recent.append(hit)
        if len(recent) > 8:
            del recent[:-8]
        if len(recent) == 8 and sum(recent) == 0:
            _add_alert("warn",
                       f"替换规则已连续 8 轮零命中。游戏可能改了文案，"
                       f"{len(_AI['rules'])} 条规则正在静默失效。")

        return prof
    except Exception as e:
        print(f"[自检] 执行失败（不影响转发）: {e}", flush=True)
        return None


def forward(req_json, game_auth):
    upstream_url = UPSTREAM_BASE + "/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    if game_auth:
        headers["Authorization"] = game_auth
    elif FALLBACK_KEY:
        headers["Authorization"] = "Bearer " + FALLBACK_KEY

    body_bytes = json.dumps(req_json, ensure_ascii=False).encode("utf-8")

    try:
        up_resp = requests.post(upstream_url, data=body_bytes, headers=headers, timeout=600)
    except Exception as e:
        return None, 502, str(e)

    return up_resp.text, up_resp.status_code, None


# ============================================================
# 十、路由
# ============================================================

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():

    try:
        req_json = request.get_json(force=True)
    except Exception as e:
            print(f"[core_proxy] JSON 解析失败: {e}", flush=True)
            return Response(json.dumps({"error": f"invalid json: {e}"}),
                        status=400, content_type="application/json; charset=utf-8")

    global recent_replies
    t0 = time.time()

    # 样本存的是【游戏原始】prompt（注入前），便于对照"我改了什么"
    try:
        save_prompt_sample(req_json)
    except Exception as e:
        import traceback
        print(f"[样本] 保存失败: {e}", flush=True)
        print(traceback.format_exc(), flush=True)

    try:
        stats = prepare_messages(req_json)
    except Exception as e:
        import traceback
        print(f"[组装] 失败: {e}", flush=True)
        print(traceback.format_exc(), flush=True)
        stats = {"rules_hit": 0, "card_chars": 0, "memory_chars": 0}

    active_npc_id = stats.get("npc_id")
    active_npc_name = stats.get("npc_name")

    req_json["chat_template_kwargs"] = {"enable_thinking": False}

    messages = req_json.get("messages") or []
    last_user_len = 0
    if messages:
        for m in reversed(messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user_len = len(m["content"])
                break

    print(f"[REQ] messages={len(messages)}, last_user_len={last_user_len}, "
          f"rules={stats.get('rules_hit', 0)}, card={stats.get('card_chars', 0)}, "
          f"memory={stats.get('memory_chars', 0)}, system={stats.get('system_chars', 0)}")

    # 历史裁剪：在注入之后、转发之前。失败不阻断转发。
    try:
        trimmed, dropped = trim_history(messages)
        if dropped > 0:
            messages = trimmed
            req_json["messages"] = trimmed
            _HEALTH["trimmed_total"] += dropped
            print(f"[裁剪] 对话历史超预算，裁掉最早 {dropped} 条，"
                  f"保留 {len(trimmed)} 条（含 system）")
    except Exception as e:
        print(f"[裁剪] 失败，按原样转发: {e}")

    # 污染清洗：assistant 历史里混进原始响应 JSON 的，换成占位文本，
    # 否则它会跟着每轮请求继续传，越滚越大。
    polluted_fixed = 0
    try:
        _, polluted_fixed = clean_polluted_history(messages)
        if polluted_fixed > 0:
            _HEALTH["polluted_cleaned"] += polluted_fixed
            print(f"[清洗] 发现 {polluted_fixed} 条 assistant 历史是原始响应 JSON，已替换为占位。"
                  f"游戏侧记忆里若也有残留，需在游戏内重置该 NPC 对话。")
    except Exception as e:
        print(f"[清洗] 失败，按原样转发: {e}")

    # 结构自检：只记录和告警，不改请求
    try:
        run_self_check(stats, messages)
    except Exception as e:
        print(f"[自检] 失败（不影响转发）: {e}")

    game_auth = request.headers.get("Authorization")

    raw_body, status, err = forward(req_json, game_auth)
    if err:
        print(f"[错误] 上游请求失败: {err}")
        return Response(json.dumps({"error": err}),
                        status=502, content_type="application/json; charset=utf-8")

    clean_text, extra_fields = normalize_response(raw_body)

    if not clean_text.strip():
        _HEALTH["empty_response"] += 1

    preview = raw_body[:100].replace("\n", " ")
    print(f"[UP ] status={status}, 原始前100字: {preview}")

    too_short = len(clean_text.strip()) < 10
    repeated = is_repetition(clean_text, recent_replies)

    if too_short:
        print(f"[RETRY] 原因: 回复太短 ({len(clean_text)} 字)")
    if repeated:
        print(f"[RETRY] 原因: 与历史重复")

    if too_short or repeated:
        msgs = req_json.get("messages") or []
        hint = ("请给出完整、连贯、全新的回复。不要复述上文任何句子，"
                "不要用相同的开头，换一个角度继续对话。")
        if msgs and msgs[0].get("role") == "system" and isinstance(msgs[0].get("content"), str):
            msgs[0]["content"] = hint + "\r\n\r\n" + msgs[0]["content"]
        else:
            msgs.insert(0, {"role": "system", "content": hint})

        raw_body2, status2, err2 = forward(req_json, game_auth)
        if not err2:
            clean_text2, extra_fields2 = normalize_response(raw_body2)
            if (len(clean_text2.strip()) >= 10
                    and not is_repetition(clean_text2, recent_replies)):
                clean_text = clean_text2
                extra_fields = extra_fields2 or extra_fields
                print(f"[RETRY] 重试成功，新回复 {len(clean_text)} 字")
            else:
                print(f"[RETRY] 重试未改善，沿用原回复并去重")

    if is_repetition(clean_text, recent_replies):
        stripped = strip_repetition_prefix(clean_text, recent_replies)
        if stripped and len(stripped.strip()) >= 10:
            print(f"[去重] 裁掉重复前缀，保留 {len(stripped)} 字")
            clean_text = stripped
        else:
            clean_text = "（她顿了顿，似乎在等你继续说下去。）"
            print(f"[去重] 完全重复，替换为占位提示")

    if len(clean_text.strip()) <= 3:
        fallback = random.choice(FALLBACK_LINES)
        print(f"[兜底] 模型只回了 {len(clean_text)} 字，替换为过渡句: {fallback}")
        clean_text = fallback

    recent_replies.append(clean_text)
    if len(recent_replies) > RECENT_MAX:
        recent_replies = recent_replies[-RECENT_MAX:]

    inner = {}
    if isinstance(extra_fields, dict):
        for k, v in extra_fields.items():
            if k == "content":
                continue
            inner[k] = v
    inner.setdefault("emotion", "neutral")
    inner.setdefault("control", "none")
    inner.setdefault("observe", {
        "topic_goal_met": False,
        "off_topic": False,
        "player_abandoned": False,
    })
    inner.setdefault("intent", {"give_gift": False, "gift_item": "none"})
    inner["content"] = clean_text

    outer = {
        "id": "chatcmpl-proxy",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "proxy",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": inner,
                },
            }
        ],
    }

    elapsed_ms = int((time.time() - t0) * 1000)

    # 提取本轮用户说的话
    user_say = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_say = m.get("content", "")
            break
            
    # 更新记忆（老化等逻辑）
    if active_npc_id:
        try:
            update_memory_after_chat(active_npc_id, active_npc_name, user_say, clean_text)
            print(f"[记忆] 已更新角色 {active_npc_name} 的记忆")
        except Exception as e:
            print(f"[记忆] 更新失败: {e}")

    print(f"[OK ] 耗时={elapsed_ms}ms, 回复={len(clean_text)}字")

    return Response(json.dumps(outer, ensure_ascii=False),
                    status=200, content_type="application/json; charset=utf-8")



@app.route("/_status", methods=["GET"])
def _status():
    from flask import jsonify
    return jsonify({
        "upstream": UPSTREAM_BASE,
        "listen_port": LISTEN_PORT,
        "npc": CURRENT_NPC_NAME,
        "inject_chars": len(_AI["inject"]),
        "rules": len(_AI["rules"]),
        "memory_budget": _AI["memory_budget"],
        "memory_slot": _AI.get("memory_slot"),
        "save_guard": _AI.get("save_guard"),
        "memory_merge_url": _AI.get("memory_merge_url") or "(跟随上游)",
        "memory_merge_every": _AI.get("memory_merge_every"),
        "memory_merge_max_tokens": _AI.get("memory_merge_max_tokens"),
        "past_events_max_chars": _AI.get("past_events_max_chars"),
        "config_mtime": _AI["mtime"],
        "health": {
            "requests": _HEALTH["requests"],
            "npc_name_fail": _HEALTH["npc_name_fail"],
            "structure_changes": _HEALTH["structure_changes"],
            "empty_response": _HEALTH["empty_response"],
            "polluted_cleaned": _HEALTH["polluted_cleaned"],
            "trimmed_total": _HEALTH["trimmed_total"],
            "rules_hit_recent": _HEALTH["rules_hit_recent"],
            "last_profile": _HEALTH["last_profile"],
            "alerts": _HEALTH["alerts"],
        },
    })


@app.route("/_preview", methods=["POST"])
def _preview():
    """试演：拿一段 messages 走完整注入流程，返回最终结果。

    不调用上游、不保存样本、不写记忆 —— 可以反复试。
    """
    from flask import jsonify
    import copy

    try:
        body = request.get_json(force=True) or {}
    except Exception as e:
        return Response(json.dumps({"ok": False, "msg": f"invalid json: {e}"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return Response(json.dumps({"ok": False, "msg": "messages 不能为空"}, ensure_ascii=False),
                        status=400, content_type="application/json; charset=utf-8")

    req = {"messages": copy.deepcopy(messages)}
    with_memory = bool(body.get("with_memory", True))
    with_card = bool(body.get("with_card", True))

    try:
        stats = prepare_messages(req, with_memory=with_memory, with_card=with_card, dry_run=True)
    except Exception as e:
        import traceback
        return Response(json.dumps({"ok": False, "msg": f"组装失败: {e}",
                                    "traceback": traceback.format_exc()}, ensure_ascii=False),
                        status=500, content_type="application/json; charset=utf-8")

    final = req.get("messages") or []
    system_text = ""
    if final and final[0].get("role") == "system" and isinstance(final[0].get("content"), str):
        system_text = final[0]["content"]

    return jsonify({
        "ok": True,
        "stats": stats,
        "system": system_text,
        "roles": [m.get("role") for m in final],
        "messages": final,
    })


if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, threaded=True)
