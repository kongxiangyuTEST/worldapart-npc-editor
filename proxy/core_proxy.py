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
import time
from difflib import SequenceMatcher
from pathlib import Path

import requests
from flask import Flask, request, Response

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


def _load_game_dir():
    """从 user_config.json 读游戏目录"""
    cfg = PROJECT_DIR / "user_config.json"
    if not cfg.is_file():
        print(f"[警告] 找不到 {cfg}，卡片目录将不可用")
        return ""
    try:
        import json as _json
        with open(cfg, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data.get("game_dir", "")
    except Exception as e:
        print(f"[警告] 读 user_config.json 失败: {e}")
        return ""


GAME_DIR = _load_game_dir()

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
# 二、读 AI 配置（从编辑器项目的 ai_config.json）
# ============================================================
AI_CONFIG_PATH = PROJECT_DIR / "ai_config.json"

inject = ""
rules = []
inject_user_card_enabled = True   # 默认开启

if AI_CONFIG_PATH.exists():
    try:
        with open(AI_CONFIG_PATH, "r", encoding="utf-8") as f:
            ai_conf = json.load(f)
        inject = (ai_conf.get("inject") or "").strip()
        inject_user_card_enabled = ai_conf.get("inject_user_card", True)
        for r in ai_conf.get("rules", []):
            old = (r.get("old") or "").strip()
            new = (r.get("new") or "").strip()
            if old:
                rules.append((old, new))
        print(f"[配置] 已从 {AI_CONFIG_PATH} 读取配置（用户名片注入: {inject_user_card_enabled}）")
    except Exception as e:
        print(f"[警告] 读取 ai_config.json 失败: {e}")
else:
    print(f"[警告] 未找到 {AI_CONFIG_PATH}，将原样转发 prompt。")

# ============================================================
# 三、用户名片配置
# ============================================================
# 卡片目录
_editor_cards = PROJECT_DIR / "editor_cards"
_mod_cards = Path(GAME_DIR) / "Mods" / "WorldApartNpcCards" if GAME_DIR else None

if _editor_cards.is_dir() and any(_editor_cards.glob("*.md")):
    CARDS_DIR = _editor_cards
elif _mod_cards and _mod_cards.is_dir():
    CARDS_DIR = _mod_cards
else:
    CARDS_DIR = _editor_cards
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

print("=" * 60)
print("  core_proxy (Python 重写版 + 语义去重 + 残留清洗 + 角色交替修正 + 用户名片注入)  已就绪")
print(f"  监听:  http://{LISTEN_HOST}:{LISTEN_PORT}")
print(f"  上游:  {UPSTREAM_BASE}/v1/chat/completions")
print(f"  改写:  注入 {len(inject)} 字符 + {len(rules)} 条替换规则")
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
        if "角色设定" not in content:
            continue
        # 提取 "你是 XXX"
        match = NPC_NAME_RE.search(content)
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


def inject_user_card(req_json):
    """
    在 req_json["messages"] 里找到 NPC 名字，读用户名片，注入到 prompt。
    返回：注入的字符数（0 表示没注入）
    """
    messages = req_json.get("messages") or []
    if not messages:
        return 0

    # 1. 先尝试用原版逻辑提取名字
    npc_name = extract_npc_name(messages)
    
    # # 2. 如果原版提取不到（或者提取错了），用暴力搜索兜底
    # if not npc_name or npc_name == "活人 不是":
    #     all_text = "".join([str(m.get("content", "")) for m in messages])
    #     try:
    #         import config
    #         import os
    #         for f in os.listdir(config.CARDS_DIR):
    #             if f.endswith(".md"):
    #                 parts = f.replace(".md", "").split("-")
    #                 if len(parts) >= 2:
    #                     c_name = parts[1]
    #                     if c_name in all_text:
    #                         npc_name = c_name
    #                         break
    #     except Exception:
    #         pass

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

        # 构造注入文本
        inject_text = f"\n\n## 用户补充\n{user_card}\n"

        # 找到"## 角色背景"或"## 说话习惯"的结尾，在"## 当前场景"之前插入
        # 简单做法：把"## 用户补充"插到 "## 说话习惯" 那段的末尾
        # 找"## 说话习惯"的下一段"## " 或消息结尾
        idx = content.find("## 说话习惯")
        if idx >= 0:
            # 找"## 说话习惯"之后的下一个"## "（下一个段落开始）
            next_idx = content.find("## ", idx + 5)
            if next_idx >= 0:
                new_content = content[:next_idx] + inject_text + content[next_idx:]
            else:
                new_content = content + inject_text
        else:
            # 没有"说话习惯"，就追加到末尾
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
            if isinstance(inner_text, str):
                inner_text = strip_think(inner_text).strip()

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


def apply_rules(text):
    if not text:
        return text, 0
    count = 0
    for old, new in rules:
        if old and old in text:
            text = text.replace(old, new)
            count += 1
    return text, count


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
    rules_applied = 0
    messages = req_json.get("messages") or []
    if not messages:
        return rules_applied

    # 1. 替换规则只对最后一条 user 生效
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break
    if last_user_idx >= 0 and isinstance(messages[last_user_idx].get("content"), str):
        txt, rules_applied = apply_rules(messages[last_user_idx]["content"])
        messages[last_user_idx]["content"] = txt

    # 2. 拆分 system / others
    system_parts = []
    others = []
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            system_parts.append(m["content"])
        else:
            others.append(m)

    # 3. 强制角色交替
    others = fix_alternation(others)

    # 4. 合并所有 system
    parts = []
    if inject:
        parts.append(inject)
    parts.extend(system_parts)
    parts.append("必须严格按 system 消息在最前、然后 user 和 assistant 交替的顺序回复。")
    final_system = "\r\n\r\n".join(parts)

    # 5. 重排
    new_messages = [{"role": "system", "content": final_system}]
    new_messages.extend(others)
    req_json["messages"] = new_messages

    roles = [m.get("role") for m in new_messages]
    print(f"[REWRITE] 角色顺序: {roles[:8]}")

    return rules_applied

# ==================== 长期记忆核心注入 ====================
def inject_memory_to_messages(req_json):
    """将长期记忆注入到 system 提示词中"""
    messages = req_json.get("messages") or []
    if not messages: return None, None
    
    global CURRENT_NPC_NAME
    npc_name = None
    npc_id = None
    
    # 优先使用名片模块抓到的精准名字
    if CURRENT_NPC_NAME:
        npc_name = CURRENT_NPC_NAME
        try:
            import config
            import os
            for f in os.listdir(config.CARDS_DIR):
                if f.endswith(f"-{npc_name}.md"):
                    npc_id = int(f.split("-")[0])
                    break
        except Exception:
            pass
            
    # 如果没抓到，再用暴力扫描兜底
    if not npc_id:
        all_text = "".join([str(m.get("content", "")) for m in messages])
        try:
            import config
            import os
            for f in os.listdir(config.CARDS_DIR):
                if f.endswith(".md"):
                    parts = f.replace(".md", "").split("-")
                    if len(parts) >= 2 and parts[0].isdigit():
                        c_id = int(parts[0])
                        c_name = parts[1]
                        if c_name in all_text:
                            npc_name = c_name
                            npc_id = c_id
                            break
        except Exception as e:
            print(f"[记忆] 扫描出错: {e}", flush=True)

    if not npc_id:
        return None, None
        
    print(f"[记忆] 成功锁定角色: {npc_name}({npc_id})", flush=True)
    
    # 加载记忆并拼装
    data = memory_manager.load_memory(npc_id, npc_name)
    memory_text = memory_manager.build_injection_text(data, budget_chars=2000)
    
    if not memory_text.strip():
        return npc_id, npc_name
        
    mem_block = f"\n\n【长期记忆（请自然带入）】\n{memory_text}\n"
    
    # 注入到 system 提示词
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
    return npc_id, npc_name


def update_memory_after_chat(npc_id, npc_name, user_text, ai_text):
    """对话后更新记忆（老化 + 12轮自动合并）"""
    if not npc_id or not npc_name: return
    data = memory_manager.load_memory(npc_id, npc_name)
    
    # 1. 防降级：如果本轮用户提到了某事实，清零老化
    memory_manager.reset_fact_age(data, user_text)
    
    # 2. 老化处理（24轮降级/48轮删除）
    data, changed = memory_manager.process_aging(npc_id, npc_name, data)
    
    # 3. 轮次记录与12轮合并触发
    if "recent_logs" not in data: data["recent_logs"] = []
    data["recent_logs"].append(f"玩家: {user_text}\nAI: {ai_text}")
    
    if len(data["recent_logs"]) >= 12:
        logs_str = "\n\n".join(data["recent_logs"])
        # 开个线程去请求AI，不卡游戏运行
        import threading
        # 从配置文件读取真实的上游地址和 Key
        import json, os
        upstream_url = UPSTREAM_BASE + "/v1/chat/completions"
        f_key = FALLBACK_KEY
        try:
            if os.path.isfile("ai_config.json"):
                with open("ai_config.json", "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    upstream_url = cfg.get("upstream", upstream_url)
                    f_key = cfg.get("fallback_key", f_key)
        except:
            pass

        threading.Thread(
            target=memory_manager.do_auto_merge, 
            args=(npc_id, npc_name, logs_str, upstream_url, f_key)
        ).start()
        data["recent_logs"] = [] # 清空队列
        print(f"[记忆] {npc_name} 已满12轮，正在后台自动合并剧情...", flush=True)
        
    # 4. 保存回文件
    memory_manager.save_memory(npc_id, npc_name, data)
# =========================================================
    """对话后更新记忆（老化+提到了清零）"""
    if not npc_id or not npc_name: return
    data = memory_manager.load_memory(npc_id, npc_name)
    
    # 1. 防降级：如果本轮用户提到了某事实，清零老化
    memory_manager.reset_fact_age(data, user_text)
    
    # 2. 老化处理（24轮降级/48轮删除）
    data, changed = memory_manager.process_aging(npc_id, npc_name, data)
    
    # 3. 保存回文件
    memory_manager.save_memory(npc_id, npc_name, data)


# ============================================================
# 八、转发
# ============================================================

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
# 九、路由
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

    card_len = 0
    if inject_user_card_enabled:
        try:
            card_len = inject_user_card(req_json)
        except Exception as e:
            import traceback
            print(f"[名片] 注入失败: {e}", flush=True)
            print(traceback.format_exc(), flush=True)
            card_len = 0
    else:
        print("[名片] 用户名片注入已关闭，跳过", flush=True)

    try:
        save_prompt_sample(req_json)
    except Exception as e:
        import traceback
        print(f"[样本] 保存失败: {e}", flush=True)
        print(traceback.format_exc(), flush=True)

    rules_applied = rewrite_messages(req_json)

    # 注入长期记忆
    active_npc_id, active_npc_name = inject_memory_to_messages(req_json)
    if active_npc_id:
        print(f"[记忆] 已注入角色 {active_npc_name}({active_npc_id}) 的长期记忆")


    req_json["chat_template_kwargs"] = {"enable_thinking": False}

    messages = req_json.get("messages") or []
    last_user_len = 0
    if messages:
        for m in reversed(messages):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                last_user_len = len(m["content"])
                break

    print(f"[REQ] messages={len(messages)}, last_user_len={last_user_len}, rules={rules_applied}, card={card_len}")

    game_auth = request.headers.get("Authorization")

    raw_body, status, err = forward(req_json, game_auth)
    if err:
        print(f"[错误] 上游请求失败: {err}")
        return Response(json.dumps({"error": err}),
                        status=502, content_type="application/json; charset=utf-8")

    clean_text, extra_fields = normalize_response(raw_body)

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
    })


if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, threaded=True)
