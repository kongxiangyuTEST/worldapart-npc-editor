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

# 「最近一次对话的角色」落盘给编辑器用：代理重启后 /_status 里的 npc 就没了，
# 编辑器要显示"当前角色是谁"就得有个跨进程持久的地方。只在变化时写，不产生频繁 IO。
_LAST_NPC_FILE = None
_LAST_NPC_WRITTEN = None


def _remember_npc(npc_name):
    global _LAST_NPC_FILE, _LAST_NPC_WRITTEN
    if not npc_name or npc_name == "unknown":
        return
    try:
        if _LAST_NPC_FILE is None:
            _LAST_NPC_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "last_npc.json")
        if _LAST_NPC_WRITTEN == npc_name:
            return
        tmp = _LAST_NPC_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"npc": npc_name, "ts": time.time()}, f, ensure_ascii=False)
        os.replace(tmp, _LAST_NPC_FILE)
        _LAST_NPC_WRITTEN = npc_name
    except Exception as e:
        print(f"[音色] 记录最近角色失败（不影响对话）: {type(e).__name__}: {e}",
              flush=True)

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

# 本地推理分流（2026-10-24 新增）。
# local_upstream_base_url 填了本地节点（如 Mac 上的 Ollama http://192.168.0.116:11434）后：
#   游戏自带的记忆压缩请求（passthrough，system 里没有「## 角色设定」的那些）
#   会转投本地小模型；主对话仍走云端。本地失败自动回落云端，不会卡住游戏。
#   好处：这类请求量大、规则死，小模型够用；且不过上游审核、不产生 API 费用。
# 留空 = 全部走云端（原有行为，一字不改）。
LOCAL_UPSTREAM = (conf.get("local_upstream_base_url") or "").strip().rstrip("/")
LOCAL_MODEL_NAME = (conf.get("local_model_name") or "").strip()
LOCAL_ROUTE_PASSTHROUGH = bool(conf.get("local_route_passthrough", True))

# 本机 llama.cpp 快速节点（2026-10-27 新增）。
# 与上面的 local（Mac Ollama）分工不同：
#   local(Mac 192.168.0.116)  —— 游戏自带记忆压缩 summary 分流，24h 常驻、不占本机显存
#   fast(本机 127.0.0.1:8080) —— 记忆总结等后台任务，速度远快于 Mac（同一块显卡直连）
# 启动方式：H:/llama/llamagui 里点「运行」，参数含 --port 8080 --alias local-model。
# 留空 = 不用本机节点，行为与之前一致。
FAST_UPSTREAM = (conf.get("fast_upstream_base_url") or "").strip().rstrip("/")
FAST_MODEL_NAME = (conf.get("fast_model_name") or "").strip()

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
    # 本地模型名可以放在 ai_config.json 里覆盖 core_proxy_config.json ——
    # ai_config 是按 mtime 热加载的，所以改这一行存盘就换模型，不用重启。
    "local_model_name": "",
    # ---- 语音风格层（实验性，默认关）----
    # tts_style_inject: 注入 V1 风格帧要求（老功能，默认开）。
    "tts_style_inject": True,
    # tts_style_v2: 双层 delivery（角色基线；逐句表演）+ decode_reply 兼容顶层
    #   a1tts_v1 字段。**默认关** —— 功能未充分验证，且编辑器有「检查更新」
    #   会检测上传内容，稳妥起见先让用户自己开。
    "tts_style_v2": False,
    # tts_style_auto_perf: 模型没给表演段时，用规则(perceive)自动补第二层。
    "tts_style_auto_perf": True,
    # tts_script_v2: 要求模型输出 a1ttsv2 朗读台本（默认关）。
    #   **这是让 NPC 出声的前提**：游戏本体没有 NPC 语音，台本只能由模型产出；
    #   而 MOD 的提示增强钩的是游戏进程内 C# 方法，够不到我们的 HTTP 代理
    #   （实测 59 个真实样本 0 个含 a1ttsv2）。所以要出声只能由我们在代理侧要求。
    "tts_script_v2": False,
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
            # mode：text（默认，纯文本，兼容旧配置）/ regex（正则，可用 \1 捕获组）
            mode = str(r.get("mode") or "text").strip().lower()
            if mode not in ("text", "regex"):
                mode = "text"
            # flags：i 忽略大小写 / m 多行 / s 点匹配换行 / x 忽略空白
            flags = str(r.get("flags") or "").strip()
            new_rules.append((old, new, mode, flags))

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
    # TTS 代理相关：必须显式加载，否则改了 ai_config 也不会生效
    try:
        _AI["tts_speed"] = float(conf.get("tts_speed", 1.0))
    except Exception:
        _AI["tts_speed"] = 1.0
    for _k, _d in (("tts_pitch", 0.0), ("tts_eq_gain", 0.0),
                   ("tts_eq_q", 1.0), ("tts_volume", 0.0)):
        try:
            _AI[_k] = float(conf.get(_k, _d))
        except Exception:
            _AI[_k] = _d
    try:
        _AI["tts_eq_freq"] = float(conf.get("tts_eq_freq", 2000.0))
    except Exception:
        _AI["tts_eq_freq"] = 2000.0
    _AI["tts_post_enabled"] = bool(conf.get("tts_post_enabled", True))
    # 台词关键词 → options.instruct（instruct_map.py）。默认关，见该文件头的实测表
    _AI["tts_instruct_map_enabled"] = bool(conf.get("tts_instruct_map_enabled", False))
    _mode = str(conf.get("tts_instruct_map_mode") or "fill").strip().lower()
    _AI["tts_instruct_map_mode"] = _mode if _mode in (
        "off", "fill", "override", "append") else "fill"
    _AI["tts_instruct_map_rules"] = conf.get("tts_instruct_map_rules") or []
    _AI["tts_upstream"] = str(
        conf.get("tts_upstream") or "http://127.0.0.1:8893").strip()
    _AI["tts_a1_dir"] = str(conf.get("tts_a1_dir") or "").strip()
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
    # 留空则回落到 core_proxy_config.json 里的 local_model_name
    _AI["local_model_name"] = str(conf.get("local_model_name") or "").strip()
    # 语音风格层开关（实验性，v2 默认关 —— 见文件头 _AI 里的说明）
    _AI["tts_style_inject"] = bool(conf.get("tts_style_inject", True))
    _AI["tts_style_v2"] = bool(conf.get("tts_style_v2", False))
    _AI["tts_style_auto_perf"] = bool(conf.get("tts_style_auto_perf", True))
    _AI["tts_script_v2"] = bool(conf.get("tts_script_v2", False))
    _AI["tts_single_segment"] = bool(conf.get("tts_single_segment", True))
    # 关掉后不再从正文补写台本（和「原版 MOD」做对照时用）
    _AI["tts_autofill"] = bool(conf.get("tts_autofill", True))
    # 完全透传（对照实验）：一个字节都不改，等价「没有中转」
    _AI["proxy_passthrough"] = bool(conf.get("proxy_passthrough", False))
    # 三档：profile（只用角色基线，音色锁死）/ profile_model（基线+模型自写）/ full（再加我们的表演层）
    _AI["tts_instruct_mode"] = str(conf.get("tts_instruct_mode") or "profile")
    # 调试用：非空时直接顶掉 a1ttsv2.instruct，用于游戏内 A/B 验证
    # 「MOD 到底有没有把 instruct 传给 Breeze」。
    _AI["tts_instruct_override"] = str(conf.get("tts_instruct_override") or "").strip()
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
    # 单价（每百万 token，单位与用户充值货币一致）。填 0 表示不折算花费。
    try:
        _AI["price_input_per_million"] = max(0.0, float(conf.get("price_input_per_million", 0) or 0))
    except (TypeError, ValueError):
        _AI["price_input_per_million"] = 0.0
    try:
        _AI["price_output_per_million"] = max(0.0, float(conf.get("price_output_per_million", 0) or 0))
    except (TypeError, ValueError):
        _AI["price_output_per_million"] = 0.0
    # 世界信息（Lorebook）预算：命中条目合起来最多注入多少字
    try:
        _AI["lorebook_budget"] = max(0, int(conf.get("lorebook_budget", 800)))
    except (TypeError, ValueError):
        _AI["lorebook_budget"] = 800
    # 用户人设（玩家自己的设定，每轮都注入）
    _AI["persona"] = (conf.get("persona") or "").strip()
    _AI["inject_persona"] = bool(conf.get("inject_persona", True))

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
    _remember_npc(npc_name)

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
    """归一化到「只留字」，用于替换规则的模糊匹配。

    引号/括号都要去掉：游戏正文里既有 “” 也有 「」，只去一种会让
    同一句话因引号风格不同而匹配不上（与 _norm_for_match 保持一致）。
    """
    if not s:
        return ""
    s = re.sub(r"\s+", "", s)
    for ch in "“”「」『』\"‘’'（）()":
        s = s.replace(ch, "")
    return s


# NPC 语音 MOD（A1IndexTTSMod）的风格帧：附在 content 正文末尾，
# 形如 <a1tts_v1>{...}</a1tts_v1> 或 <a1ttsv2>...</a1ttsv2>。
# 这段是给语音 MOD 的传输元数据，不是 JSON 残留 ——
# 一旦被下面的清洗规则截断，NPC 就只会出文本、不会朗读。
# 作者在协议文档里也写了「代理改写正文仍可能造成风格缺失」，
# 所以这里必须把整段帧保护起来，洗完再拼回去。
TTS_FRAME_RE = re.compile(r"<a1tts[^<>]*>.*?</a1tts[^<>]*>", re.S)


def clean_tail(s):
    if not s:
        return s

    # 先把风格帧整段摘出来保护，避免被当 JSON 残留清洗掉
    frame = ""
    m = TTS_FRAME_RE.search(s)
    if m:
        frame = m.group(0)
        s = s[:m.start()] + s[m.end():]

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

    # 把保护起来的风格帧拼回正文末尾（语音 MOD 会自己解包并移除）
    if frame:
        s = s + frame
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


def _strip_code_fence(text):
    """剥掉模型爱加的 ```json ... ``` 围栏和前后说明文字，只留 JSON 本体。

    不做这步的后果很具体：模型一加代码围栏，json.loads 就失败，
    于是走到下面的「半截」分支 —— 那个分支只挑 emotion / control 两个字段，
    TTS Mod 需要的扩展字段（a1ttsv2 之类）会被丢掉。
    表现是 NPC 文本照常出、但不会朗读。
    """
    s = (text or "").strip()
    if not s:
        return s

    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        return m.group(1).strip()

    # 没有围栏：取第一个 { 到最后一个 }，去掉前后的客套话
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        return s[i:j + 1]
    return s


def _try_repair_json(text):
    """JSON 被 max_tokens 截断时，补上缺失的右括号再试一次。

    能救回多少算多少：至少 content 完整，且保住它之前的字段。
    """
    s = (text or "").strip()
    if not s:
        return None
    for cand in (s, s + "}" * max(0, s.count("{") - s.count("}"))):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


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
                # 先剥掉 ```json 围栏再整体解析。成功的话返回的是完整的
                # 内层 dict，TTS Mod 需要的扩展字段（a1ttsv2 等）才能保住。
                candidate = _strip_code_fence(inner_text)
                inner = None
                for txt in (inner_text, candidate):
                    try:
                        obj = json.loads(txt)
                        if isinstance(obj, dict) and isinstance(obj.get("content"), str):
                            inner = obj
                            break
                    except Exception:
                        pass

                if inner is not None:
                    if candidate != inner_text:
                        print("[清洗] 剥掉代码围栏后解析成功 → 保留全部字段")
                    return clean_tail(inner["content"]), inner

                # 被 max_tokens 截断（末尾少括号）：补齐再试一次
                repaired = _try_repair_json(candidate)
                if isinstance(repaired, dict) and isinstance(repaired.get("content"), str):
                    print("[清洗] JSON 被截断，补括号后解析成功")
                    return clean_tail(repaired["content"]), repaired

                # 实在解析不出来才退化 —— 这里只能保住 emotion / control，
                # 扩展字段会丢，TTS 那几轮就不会朗读
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
                        print("[清洗] 内层半截 JSON → 括号配对扫描（只保住 emotion/control）")
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

# 正则模式的 flags 字符串 → re 标志位。i 忽略大小写 / m 多行 /
# s 让 . 匹配换行 / x 忽略模式里的空白和注释。
_REGEX_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}
# 正则过长一律跳过：既是防灾难性回溯的粗筛，也几乎不影响正常使用
# （正常替换正则远小于这个长度）。
REGEX_PATTERN_MAX = 500


def _parse_regex_flags(s):
    f = 0
    for ch in str(s or ""):
        f |= _REGEX_FLAG_MAP.get(ch.strip().lower(), 0)
    return f


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

    两种模式：
      {"old": "原文", "new": "替换为"}                      纯文本（默认）
      {"old": "正则", "new": "…\\1…", "mode": "regex", "flags": "im"}   正则

    文本模式保留原来的四级匹配（精确 → 去空白 → CRLF → 整行近似），
    让游戏改几个字也还能命中；正则模式走 re.subn 直路，不参与模糊匹配。

    正则模式下 new 里可以写 \\1 \\2 引用捕获组（Python 原生语法）。

    安全：单条规则异常（语法错、执行错）只跳过该条并记日志，
    绝不影响其他规则、更不中断转发。

    返回 (新文本, 命中条数, 命中明细)
    """
    if not text:
        return text, 0, []

    pairs = _AI["rules"] if rules is None else rules
    count = 0
    detail = []
    # 赠物机制依赖 {GIFT} 占位符：NPC 申请赠物时台词里只写 {GIFT}，
    # 由游戏在运行时替换成实际物品名（2026-10-24 实测确认）。
    # 规则要是把它改没了，赠物功能就静默失效——这里留一道提醒。
    had_gift_token = "{GIFT}" in text

    for rule in pairs:
        # 兼容两种元组：旧 (old, new) / 新 (old, new, mode, flags)
        if len(rule) >= 4:
            old, new, mode, flags = rule[0], rule[1], rule[2], rule[3]
        else:
            old, new, mode, flags = rule[0], rule[1], "text", ""
        if not old:
            continue

        if mode == "regex":
            try:
                if len(old) > REGEX_PATTERN_MAX:
                    print(f"[规则] 正则过长（>{REGEX_PATTERN_MAX} 字符），已跳过: {old[:40]}")
                    continue
                new_text, n = re.subn(old, new, text, flags=_parse_regex_flags(flags))
                if n:
                    print(f"[规则] 正则命中 {n} 处: {old[:40]}", flush=True)
                    text = new_text
                    count += 1
                    detail.append(old[:30].replace("\n", " "))
            except re.error as e:
                print(f"[规则] 正则语法错误，已跳过该条: {old[:40]} → {e}", flush=True)
            except Exception as e:
                print(f"[规则] 正则执行异常，已跳过该条: {e}", flush=True)
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

    if had_gift_token and "{GIFT}" not in text:
        print("[警告] 替换规则把 {GIFT} 占位符改没了——"
              "赠物机制靠它在运行时替换物品名，这会让赠物失效", flush=True)

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
    _remember_npc(npc_name)

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


def _run_merge(npc_id, npc_name, logs_str, url, key, model=None):
    """后台线程里跑一次总结；失败就退避 5 分钟。"""
    ok = memory_manager.do_auto_merge(
        npc_id, npc_name, logs_str, url, key,
        max_tokens=_AI.get("memory_merge_max_tokens", 4000),
        model=model,
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

        # 目标是本地节点时必须换成它真实拥有的模型名，否则 Ollama 会报 model not found
        merge_model = None
        if LOCAL_UPSTREAM and upstream_url.startswith(LOCAL_UPSTREAM):
            merge_model = _AI.get("local_model_name") or LOCAL_MODEL_NAME
            print(f"[记忆] 总结转本地节点，使用模型 {merge_model}", flush=True)

        threading.Thread(
            target=_run_merge,
            args=(npc_id, npc_name, logs_str, upstream_url, f_key, merge_model),
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
    # 完全透传（对照实验用）：不改 system、不注入名片/记忆/注入语、不套替换规则。
    # 用途：验证「原版 MOD + 原版 prompt」下某些现象是否还存在 ——
    # 之前只关 tts_autofill/tts_style_v2 时，名片和记忆注入仍在改 prompt，
    # 严格说不算原版。开这个开关才是真正的「中转什么都不做」。
    if _AI.get("proxy_passthrough"):
        print("[透传] proxy_passthrough 已开，本次请求一个字节都不改", flush=True)
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

    # 世界信息：按关键词命中的才注入（跨 NPC 共用的设定，游戏没有这层）
    lore_len = 0
    try:
        lore_len = inject_lorebook(req_json, npc_id)
    except Exception as e:
        print(f"[世界信息] 注入失败（不影响转发）: {e}", flush=True)

    # 用户人设：每轮都注入（玩家自己的设定）
    persona_len = 0
    try:
        persona_len = inject_persona(req_json)
    except Exception as e:
        print(f"[人设] 注入失败（不影响转发）: {e}", flush=True)

    # 名片放最后：离生成位置最近，权重最高，能盖住上面两块
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

    # TTS 语音 MOD 增强：MOD 的钩子只覆盖游戏的 C# 托管代理，覆盖不到
    # 独立 Python 代理（我们），所以「要求模型输出台本」的提示词不会
    # 被注入 —— 模型根本不知道要输出 a1ttsv2 / 风格帧，NPC 就不出声。
    # 这里代替 MOD 注入 V1 风格帧要求（带幂等标记 A1_TTS_STYLE_V1，
    # MOD 哪天自己支持了 Python 代理、注入过，这里就不会重复加）。
    # 不想要的话在 ai_config.json 加 "tts_style_inject": false。
    try:
        _msgs = req_json.get("messages") or []
        # 走 speech_layer 统一注入（V1 风格帧 + V2 朗读台本各带独立幂等标记）。
        # 以前这里是手写的一段内联逻辑，只注 V1；V2 加进来后合并到一处，
        # 避免两份实现各自 drift。
        sl = _sl()
        if sl and _msgs:
            n1 = sl.inject_tts_style(_msgs, _AI)      # 读 tts_style_inject
            n2 = sl.inject_tts_script(_msgs, _AI)     # 读 tts_script_v2（默认关）
            if n1:
                print(f"[TTS] 已注入语音风格帧要求（{n1} 字）")
            if n2:
                print(f"[TTS] 已注入朗读台本要求（{n2} 字，让模型输出 a1ttsv2）")
    except Exception as e:
        print(f"[TTS] 注入失败（不影响转发）: {e}", flush=True)

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
        "lorebook_chars": lore_len,
        "persona_chars": persona_len,
        "memory_slot": mem_slot,
        "save_guard": guard_status,
        "system_chars": sys_len,
        "messages": len(final_messages),
    }


# ============================================================
# 语音风格层 v2（实验性）—— 出口侧规整
# ============================================================
# 开关 _AI["tts_style_v2"] 关掉时完全不执行，数据一字不改。
#
# 为什么需要它（两条来自实测的坑，不是推演）：
#
# 1) **模型不写内嵌帧，改写成回复 JSON 的顶层字段**
#    实测 wa-unred(qwen35) 输出形如：
#      {"content":"台词…","emotion":"平静",
#       "a1tts_v1":{"emotion_tags":[…],"delivery":"…","intensity":0.25}}
#    有时还用旧字段名 voice_style。这些内容本身合法，但老的「找 <a1tts_v1> 标签」
#    做法会判 absent，**整帧静默丢失**，表现为 NPC 有字不出声。
#    → 用 speech_layer.decode_reply() 兼容「内嵌帧优先、顶层字段兜底」。
#
# 2) **delivery 必须是两段式**（官方格式 `角色基线；逐句表演`）
#    官方台账 4311 条全部是这个结构：58 种角色基线 × 48 种逐句表演。
#    模型自己写的常是一段散文，或带多余分号 `基线；表演；`。
#    → 用 speech_layer.normalize_delivery() 规整；模型没给表演段时，
#      再用 perceive() 按台词/玩家上句的规则补第二层。

_SPEECH_LAYER = None
# 语音决策日志：编辑器日志页只留最近 500 行且重启即清，验证时老是丢证据，
# 所以把每轮的台本处理结果另存一份到文件，方便事后比对。
_TTS_LOG_PATH = PROJECT_DIR / "proxy" / "tts_decisions.log"
_TTS_LOG_MAX_LINES = 2000


def _tts_log(msg):
    """把语音处理结果追加到 tts_decisions.log（超过上限自动截断到后半）。"""
    try:
        line = f"{datetime.now().strftime('%m-%d %H:%M:%S')}  {msg}\n"
        with open(_TTS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        # 简单限长：文件过大时只保留后半
        try:
            if _TTS_LOG_PATH.stat().st_size > 512 * 1024:
                txt = _TTS_LOG_PATH.read_text(encoding="utf-8").splitlines()
                _TTS_LOG_PATH.write_text(
                    "\n".join(txt[-_TTS_LOG_MAX_LINES // 2:]) + "\n",
                    encoding="utf-8")
        except Exception:
            pass
    except Exception:
        pass  # 日志失败绝不影响转发


def _sl():
    """惰性导入 speech_layer（避免影响 core_proxy 启动速度/缺文件时崩溃）。"""
    global _SPEECH_LAYER
    if _SPEECH_LAYER is None:
        try:
            import speech_layer
            _SPEECH_LAYER = speech_layer
        except Exception as e:
            print(f"[TTS] speech_layer 不可用: {e}", flush=True)
            _SPEECH_LAYER = False
    return _SPEECH_LAYER or None


def _last_user_text(messages):
    """取最后一条 user 消息的文本内容（没有则空串）。

    独立成函数是因为响应侧多处要用，而 user_say 那个变量在函数后面才赋值，
    直接在台本兜底处引用会拿到空值。
    """
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else ""
    return ""


def _sl_apply_style_v2(inner, clean_text, user_say, npc_id=None, npc_name=None):
    """就地把风格帧规整成官方两段式；模型没给就按规则补。

    只在 _AI["tts_style_v2"] 开启时被调用。任何异常都不影响返回。
    """
    # 双层防护：调用处已判断过开关，这里再判一次 —— 函数是公开的，
    # 以后被别处直接调用时不应该绕过开关把线上数据改掉。
    if not _AI.get("tts_style_v2"):
        return
    sl = _sl()
    if not sl:
        return

    # 当前 NPC 的 actor_profile：用于拼两层 delivery 的角色基线
    profile = _sl_current_profile(inner, npc_id, npc_name)

    edited = []

    # --- A2. V2 台本 a1ttsv2.instruct（实测模型最常走这条）---
    #
    # 2026-10-28 实机发现：模型按 MOD 契约输出的 instruct 常直接照抄示例
    # 「保持参考声线，吐字清楚，语速适中，平静自然地说完」——
    # 所有角色都是这一句，听起来完全没区别。
    # 这里把角色基线补进去，形成「角色基线；模型原句」，让每个 NPC 有自己的说话方式。
    v2 = inner.get("a1ttsv2")
    if isinstance(v2, dict) and isinstance(v2.get("instruct"), str):
        old_ins = v2["instruct"]
        # 调试用：tts_instruct_override 非空时，直接顶掉 instruct。
        # 用途是 A/B 验证「MOD 到底有没有把 instruct 传给 Breeze」：
        # 同一角色连续两次对话，一次正常、一次给极端值，
        # 若游戏里产出的音频明显不同 → 传过去了；完全一样 → 没传。
        _ov = str(_AI.get("tts_instruct_override") or "").strip()
        mode = str(_AI.get("tts_instruct_mode") or "profile")
        if _ov:
            v2["instruct"] = _ov[:120]
            edited.append(f"a1ttsv2.instruct 被 override 覆盖（{len(_ov)}字）")
        elif mode == "off":
            # 2026-10-30 实测：Breeze 对语速/音量指令的响应是噪声不是信号
            # （「语速很慢」在 100000 上反而更快；「音量很小」在 100000 上最响）。
            # off 档把节奏完全交还参考音，唯一可复现的行为。
            if old_ins:
                v2["instruct"] = ""
                edited.append(f"a1ttsv2.instruct 清空（off 档，{len(old_ins)}→0字）")
        else:
            if mode == "profile" and profile:
                # 音色锁定模式：instruct 恒等于角色基线，每轮完全一样。
                # 模型自写的那句（常带「温和笑意」「放轻」）会改音高 3–7%，
                # 同一角色跨轮听起来就像换了人，所以这里直接覆盖掉。
                base = sl.delivery_for(profile) if hasattr(sl, "delivery_for") else ""
                if base and old_ins != base:
                    v2["instruct"] = base[:120]
                    edited.append(f"a1ttsv2.instruct 锁为角色基线（{len(base)}字，音色优先）")
            else:
                new_ins = sl.layer_instruct(old_ins, profile, max_len=120)
                if new_ins != old_ins:
                    v2["instruct"] = new_ins
                    generic = sl.is_generic_instruct(old_ins)
                    edited.append(
                        f"a1ttsv2.instruct {len(old_ins)}→{len(new_ins)}字"
                        + ("（原句是通用模板，已补角色基线）" if generic else "（已补角色基线）"))

    # --- A. 顶层 a1tts_v1 / voice_style 字段（V1 老路径）---
    for key in ("a1tts_v1", "a1ttsv1", "voice_style", "voiceStyle"):
        cand = inner.get(key)
        if not isinstance(cand, dict):
            continue
        try:
            style = sl.VoiceStyle.parse(cand)
        except ValueError as e:
            print(f"[TTS] v2: 顶层 {key} 字段不合法（{e.args[0] if e.args else '?'}），放弃规整")
            continue
        _ov = _AI.get("tts_instruct_override") or ""
        if _ov:
            # 全局覆盖：所有角色统一用这一句，用于隔离「指令内容 vs 引擎」变量
            new_delivery = _ov
            edited.append(f"{key}.delivery 被 override 覆盖（{len(_ov)}字）")
        else:
            new_delivery = sl.normalize_delivery(
                style.delivery, profile, max_len=sl.DELIVERY_MAX)
            # 模型没给表演段时，用规则补第二层（开关可关）
            if (_AI.get("tts_style_auto_perf", True)
                    and new_delivery.count("；") < 1):
                perf, why = sl.perceive(clean_text, user_say)
                new_delivery = sl.compose(profile, perf)
                edited.append(f"补表演={perf}({why})")
        if new_delivery != style.delivery:
            edited.append(f"{key}.delivery {len(style.delivery)}→{len(new_delivery)}字")
        cand["delivery"] = new_delivery
        # 顶层字段名统一成 a1ttsv1 之外的规范名：保持原键名不动，
        # 因为 MOD 侧读的是自己那份契约，我们只改内容不改结构。

    # --- B. 正文里内嵌的 <a1tts_v1> 帧 ---
    if isinstance(clean_text, str) and sl.OPEN_TAG in clean_text:
        res = sl.decode_reply(clean_text)
        if res.status == "valid" and res.style:
            _ov = _AI.get("tts_instruct_override") or ""
            new_delivery = _ov or sl.normalize_delivery(
                res.style.delivery, profile, max_len=sl.DELIVERY_MAX)
            if new_delivery != res.style.delivery:
                from speech_layer.contract import Envelope as _Env
                fixed = _Env.encode(res.content, sl.VoiceStyle(
                    res.style.emotion_tags, new_delivery, res.style.intensity))
                if isinstance(inner.get("content"), str):
                    inner["content"] = fixed
                edited.append(f"正文帧 delivery 规整 {len(res.style.delivery)}→{len(new_delivery)}字")
        elif res.failure and res.failure != "absent":
            print(f"[TTS] v2: 正文帧解码失败（{res.failure}），保持原样")

    # 台词字数（用于算「每秒念多少字」，不靠听也能判断语速有没有变）
    def _spoken_chars(obj):
        try:
            segs = (obj or {}).get("segments") or []
            return sum(len(str(s.get("text") or "")) for s in segs if isinstance(s, dict))
        except Exception:
            return 0

    if edited:
        print(f"[TTS] v2 已规整: {'; '.join(edited)}")
        # 落盘一份，方便事后核对（编辑器日志重启就清）
        try:
            v2 = inner.get("a1ttsv2") if isinstance(inner, dict) else None
            ins = (v2 or {}).get("instruct") if isinstance(v2, dict) else ""
            _tts_log(f"npc={npc_id or '?'} profile={profile or 'natural'} "
                     f"| {'; '.join(edited)} | chars={_spoken_chars(v2)} | instruct={ins!r}")
        except Exception:
            pass
    elif _AI.get("tts_style_v2"):
        # v2 开着但没改动，也记一笔，便于确认「确实跑到了」
        try:
            v2 = inner.get("a1ttsv2") if isinstance(inner, dict) else None
            if isinstance(v2, dict):
                _tts_log(f"npc={npc_id or '?'} profile={profile or 'natural'} "
                         f"| 未改动（已带基线或无 instruct） | chars={_spoken_chars(v2)}"
                         f" | instruct={v2.get('instruct')!r}")
        except Exception:
            pass


def _sl_current_profile(inner, npc_id=None, npc_name=None):
    """取当前 NPC 的口吻基线（voicebook.PROFILE_DELIVERY 的键）。

    **数据源只用项目自己的角色卡**（`editor_cards/*.md` 的「个性」字段），
    经 `archetype_voice` 映射到 14 个个性原型对应的语音基线。
    不引用 MOD 台账 —— 知识库 20 明确规定「轻量台账不含任何特定角色
    台词/身份，规避版权与张冠李戴风险」。

    优先级：
    1. inner 里显式带的 actor_profile（编辑器/名片若配了就尊重）
    2. 角色卡「个性」→ archetype_voice 映射
    3. None（voicebook 回落 natural）

    认不出个性就不猜，直接回落中性基线。
    """
    # 1) 显式字段（已是 voicebook 的键，直接透传）
    for k in ("actor_profile", "actorProfile"):
        v = inner.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    nid = npc_id or inner.get("npc_id") or inner.get("npcId") or ""
    if not str(nid).strip():
        return None

    try:
        import npc_voice_profile as _nvp
        import archetype_voice as _av

        raw = _nvp.personality_for(nid)
        if not raw:
            return None
        profile = _av.profile_for_archetype(raw)
        if profile == _av.FALLBACK_PROFILE:
            # 认不出就明说，方便排查是卡片没写还是写了奇怪的值
            print(f"[TTS] 角色 {nid} 个性「{raw}」未匹配原型，回落 {profile}", flush=True)
        return profile
    except Exception as e:
        print(f"[TTS] 口吻基线查询失败: {e}", flush=True)
    return None


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


# ============================================================
# 用量统计
# ============================================================
# 上游每次返回都带 usage（prompt_tokens / completion_tokens），
# 以前直接丢了——界面上只有一个"注入字数"，看不到真实花销。
# 现在每轮记一行到 proxy/usage_log.jsonl，内存里存累计值。
USAGE_LOG_PATH = Path(__file__).parent / "usage_log.jsonl"
_USAGE = {
    "rounds": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "last": None,
    "by_model": {},
}


def _extract_usage(raw_body):
    """从上游原始响应里取 usage。取不到就返回 None（不影响转发）。"""
    try:
        d = json.loads(raw_body)
    except Exception:
        return None
    u = d.get("usage") if isinstance(d, dict) else None
    if not isinstance(u, dict):
        return None
    try:
        return {"prompt_tokens": int(u.get("prompt_tokens") or 0),
                "completion_tokens": int(u.get("completion_tokens") or 0)}
    except (TypeError, ValueError):
        return None


def record_usage(model, usage, npc_name=""):
    """记一轮用量：内存累计 + 追加 jsonl。写盘失败不影响转发。"""
    if not usage:
        return
    _USAGE["rounds"] += 1
    _USAGE["prompt_tokens"] += usage["prompt_tokens"]
    _USAGE["completion_tokens"] += usage["completion_tokens"]

    entry = {"ts": int(time.time()), "model": model or "", "npc": npc_name or "",
             "prompt_tokens": usage["prompt_tokens"],
             "completion_tokens": usage["completion_tokens"]}
    _USAGE["last"] = entry

    key = model or "unknown"
    bm = _USAGE["by_model"].setdefault(
        key, {"rounds": 0, "prompt_tokens": 0, "completion_tokens": 0})
    bm["rounds"] += 1
    bm["prompt_tokens"] += usage["prompt_tokens"]
    bm["completion_tokens"] += usage["completion_tokens"]

    try:
        with open(USAGE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[用量] 写日志失败: {e}", flush=True)


def _compute_cost():
    """按 ai_config.json 里的单价折算花费。
    单价没配（0）就返回 None——不瞎猜价格，宁可显示"未配置"。"""
    cfg = load_ai_config()
    p_in = float(cfg.get("price_input_per_million") or 0)
    p_out = float(cfg.get("price_output_per_million") or 0)
    if p_in <= 0 and p_out <= 0:
        return None
    return round(_USAGE["prompt_tokens"] / 1e6 * p_in
                 + _USAGE["completion_tokens"] / 1e6 * p_out, 4)


# ============================================================
# 世界信息（Lorebook）+ 用户人设（Persona）
# ============================================================
# 游戏只有「按 NPC 匹配名片」这一种触发方式，没有跨 NPC 共用的设定
# （门派规矩、某段共同剧情、地点背景这类），所以补一层：
#
#   世界信息：存 lorebook.json，按关键词命中当前对话才注入
#   用户人设：存在 ai_config.json 的 persona，每轮都注入
#
# 两者都追加在 system 末尾、且排在用户名片之前——名片仍然最后注入，
# 离生成位置最近、权重最高，可以盖住它们。
LOREBOOK_PATH = Path(__file__).parent.parent / "lorebook.json"


def load_lorebook():
    """读 lorebook.json。文件不存在或格式坏了都返回空列表，不影响转发。"""
    try:
        with open(LOREBOOK_PATH, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception:
        return []
    entries = d.get("entries") if isinstance(d, dict) else None
    return entries if isinstance(entries, list) else []


def _append_system_text(messages, text):
    """把 text 追加到最后一条 system 末尾；没有 system 就插一条在最前。"""
    for m in reversed(messages):
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            m["content"] = m["content"].rstrip() + "\n\n" + text
            return
    messages.insert(0, {"role": "system", "content": text})


def match_lorebook(entries, haystack, npc_id=None):
    """按关键词命中条目。
    · enabled=false 跳过
    · entries[].npcs 非空时，只对这些 NPC 生效
    · keywords 任一出现即命中（纯字符串包含，零成本）
    · 命中后按 priority 降序
    """
    hits = []
    scope_id = str(npc_id) if npc_id is not None else ""
    for e in entries:
        if not isinstance(e, dict) or not e.get("enabled", True):
            continue
        scope = e.get("npcs") or []
        if scope and scope_id and scope_id not in [str(x) for x in scope]:
            continue
        kws = [str(k).strip() for k in (e.get("keywords") or []) if str(k).strip()]
        # 关键词为空 = 常驻条目，每轮都注入（用户想让某条设定一直在时用这个）
        if not kws:
            hits.append(e)
            continue
        if any(k in haystack for k in kws):
            hits.append(e)
    hits.sort(key=lambda x: -int(x.get("priority") or 0))
    return hits


def inject_lorebook(req_json, npc_id=None):
    """把命中的世界信息注入 system。返回注入字数（0 表示没命中）。"""
    entries = load_lorebook()
    if not entries:
        return 0

    messages = req_json.get("messages") or []
    # 搜索范围：最近 4 条对话（约本轮玩家发言 + 前两轮），不扫全量
    parts, cnt = [], 0
    for m in reversed(messages):
        if m.get("role") in ("user", "assistant"):
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                parts.append(c)
                cnt += 1
        if cnt >= 4:
            break
    haystack = "\n".join(parts)[-800:]
    if not haystack:
        return 0

    hits = match_lorebook(entries, haystack, npc_id)
    if not hits:
        return 0

    budget = int(_AI.get("lorebook_budget") or 800)
    chunks, used, titles = [], 0, []
    for e in hits:
        body = str(e.get("content") or "").strip()
        if not body:
            continue
        title = str(e.get("title") or "未命名").strip()
        block = f"### {title}\n{body}"
        if chunks and used + len(block) > budget:
            break
        chunks.append(block)
        used += len(block)
        titles.append(title)

    if not chunks:
        return 0

    text = "## 世界信息（补充设定）\n\n" + "\n\n".join(chunks)
    _append_system_text(messages, text)
    print(f"[世界信息] 命中 {len(chunks)} 条（{used} 字）: "
          f"{'、'.join(titles)}", flush=True)
    return len(text)


def inject_persona(req_json):
    """把用户人设注入 system。每轮都注入，没配就跳过。"""
    persona = (_AI.get("persona") or "").strip()
    if not persona or not _AI.get("inject_persona", True):
        return 0
    messages = req_json.get("messages") or []
    # 太长的截断，避免把上下文挤爆（人设不是剧情，2000 字足够）
    if len(persona) > 2000:
        persona = persona[:2000] + "\n（人设过长已截断）"
    text = "## 玩家设定（关于和你说话的这个人的信息）\n\n" + persona
    _append_system_text(messages, text)
    print(f"[人设] 已注入玩家设定（{len(persona)} 字）", flush=True)
    return len(text)


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


def forward_local(req_json, game_auth):
    """转发到局域网内的本地推理节点（Mac 上的 Ollama），供记忆压缩这类非对话请求使用。

    和 forward() 有三处差别：
    1. 模型名换成本地模型（Ollama 只认自己拉过的名字，云端的 deepseek-chat 它没有）
    2. 加 reasoning_effort="none" —— 关掉 Qwen3 的思考模式。这一条是实测出来的：
       2026-10-24 在 Ollama 0.40.10 / qwen3:8b 上试了六种写法，只有它有效。
         ✗ /no_think 放 system 末尾（官方 soft switch，这里不认）
         ✗ /no_think 放 user 末尾
         ✗ think=false（Ollama 原生参数，OpenAI 兼容层不透传）
         ✗ chat_template_kwargs.enable_thinking=false
         ✓ reasoning_effort="none"
       不关掉的话，思考会占满 max_tokens，正文直接是空的 —— 游戏拿到的
       记忆摘要就成了空白，而且它不会报错，只是"记忆不更新"，极难排查。
    3. 不发 Authorization —— 本地不校验，没必要把云端的 key 送到局域网另一台机器上

    用 json 往返深拷贝而非浅拷贝：messages 是嵌套 list，浅拷贝后改 system
    会连带改掉调用方的 req_json。
    """
    url = LOCAL_UPSTREAM + "/v1/chat/completions"
    body = json.loads(json.dumps(req_json, ensure_ascii=False))
    # 模型名支持热切换：ai_config.json 里填了就用它（改完存盘即生效），
    # 没填则回落到 core_proxy_config.json 的 local_model_name（那个要重启才生效）。
    body["model"] = _AI.get("local_model_name") or LOCAL_MODEL_NAME
    body["reasoning_effort"] = "none"

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    # 内网/本地地址禁用系统代理 —— 加速器的代理只该管外网，
    # 管到内网会把请求直接拒掉（实测：editor 报 404，curl 报 000）。
    proxies = None
    _host = re.match(r"https?://([^/:]+)", url or "")
    if _host and re.match(
            r"^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)",
            _host.group(1)):
        proxies = {"http": None, "https": None}
    try:
        r = requests.post(url, data=data,
                          headers={"Content-Type": "application/json"},
                          timeout=600, proxies=proxies)
    except Exception as e:
        return None, 502, str(e)
    return r.text, r.status_code, None


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

    # 分流：非对话请求（游戏自带的记忆压缩）转投本地小模型。
    # stats["passthrough"] 由 prepare_messages 判定，判据是 system 里有没有「## 角色设定」。
    use_local = bool(
        LOCAL_UPSTREAM and LOCAL_MODEL_NAME
        and LOCAL_ROUTE_PASSTHROUGH
        and stats.get("passthrough")
    )
    if use_local:
        _HEALTH["local_routed"] = _HEALTH.get("local_routed", 0) + 1
        print(f"[分流] 非对话请求 → 本地节点 {LOCAL_UPSTREAM}（模型 {LOCAL_MODEL_NAME}）")
        raw_body, status, err = forward_local(req_json, game_auth)
        if err or status != 200:
            # 本地节点睡了 / Ollama 没启动 / 模型没拉 —— 一律回落云端。
            # 宁可多花一次 API 费用，也不能让游戏的记忆压缩失败。
            _HEALTH["local_fallback"] = _HEALTH.get("local_fallback", 0) + 1
            print(f"[分流] 本地不可用（err={err}, status={status}），回落云端")
            raw_body, status, err = forward(req_json, game_auth)
    else:
        raw_body, status, err = forward(req_json, game_auth)

    if err:
        print(f"[错误] 上游请求失败: {err}")
        return Response(json.dumps({"error": err}),
                        status=502, content_type="application/json; charset=utf-8")

    # 完全透传（对照实验）：上游响应一个字节不改直接还给游戏。
    # 连 content 都不包装成 {content, emotion, a1ttsv2...} 结构，
    # 也就是「这台中转不存在」时游戏会拿到的样子。
    # 只在排查「某个现象是不是代理造成的」时开，平时必须关：
    # 开着的话 NPC 会丢失结构化字段（emotion/intent/observe），游戏可能判异常。
    if _AI.get("proxy_passthrough"):
        print("[透传] 上游原始响应直接返回（未包装、未补台本、未改 instruct）", flush=True)
        return Response(raw_body, status=status or 200,
                        content_type="application/json; charset=utf-8")

    # 台本补全请求（0.8.2 的 VoiceCompletion）：响应必须原样返回，不能包装。
    #
    # 识别方式：system 里含 a1ttsv2 契约、但不含「## 角色设定」。
    #   - 对话请求：有「## 角色设定」（MOD 可能同时注入 a1ttsv2 契约）
    #   - 记忆压缩请求：两者都没有
    #   - 台本补全请求：有 a1ttsv2、没有「## 角色设定」  ← 只有这种
    #
    # 为什么必须透传：补全要的是裸 JSON（{"instruct":..., "segments":[...]}）。
    # 我们出口会把它包成 {content:{...}, emotion, a1ttsv2...}，MOD 那边就解析失败了。
    try:
        _msgs = req_json.get("messages") or []
        _sys = ""
        for _m in _msgs:
            if _m.get("role") == "system" and isinstance(_m.get("content"), str):
                _sys = _m["content"]
                break
        _is_script_completion = ("a1ttsv2" in _sys and "角色设定" not in _sys)
    except Exception:
        _is_script_completion = False

    if _is_script_completion:
        print("[透传] 台本补全请求，响应原样返回（不包装）", flush=True)
        return Response(raw_body, status=status or 200,
                        content_type="application/json; charset=utf-8")

    clean_text, extra_fields = normalize_response(raw_body)

    if not clean_text.strip():
        _HEALTH["empty_response"] += 1

    # 用量统计：上游响应里的 usage，以前直接丢了
    try:
        record_usage(req_json.get("model"), _extract_usage(raw_body), active_npc_name or "")
    except Exception as e:
        print(f"[用量] 记录失败: {e}", flush=True)

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

    # TTS 台本兜底：模型漏输出 a1ttsv2 时，从正文引号对白补一段。
    #
    # 为什么必须补：游戏本体没有 NPC 语音，台本只能由模型产出；MOD 按设计
    # 「未从正文补写」，实测 last-error.json 里就记着
    # 「缺少有效 a1ttsv2 台本，未从正文补写」—— NPC 因此完全不出声。
    #
    # 具体提取规则（只读引号内对白、不读旁白；无引号时整段当台词）见
    # speech_layer.talkback.build_talkback 的注释，那是原 core_proxy 的决策，
    # 抽包时原样保留。
    #
    # instruct / tag 不再是写死的一句话：用当前 NPC 的口吻基线 + 规则判定的
    # 表演类型合成，这样兜底台本也带区分度，而不是所有角色念同一句。
    # tts_autofill=False 时不补台本。
    # 用途：和「原版 MOD」对照 —— 没有我们补的台本时，送礼轮是否一样静音。
    if (isinstance(inner, dict) and "a1ttsv2" not in inner
            and _AI.get("tts_autofill", True)):
        sl = _sl()
        if sl:
            try:
                _prof = _sl_current_profile(inner, active_npc_id, active_npc_name)
                _perf, _why = sl.perceive(clean_text, _last_user_text(messages))
                # merge_to_one：一轮只出 1 段，避免逐段独立合成导致的音色漂移
                # （2026-10-28 用户三次反馈「段与段不太统一」）。
                # 需要逐段控制时把 ai_config 的 tts_single_segment 设 false。
                if sl.fill_a1ttsv2(inner, profile=_prof, performance=_perf,
                                   merge_to_one=_AI.get("tts_single_segment", True),
                                   with_performance=(str(_AI.get("tts_instruct_mode") or "profile") == "full"),
                                   instruct_mode=str(_AI.get("tts_instruct_mode") or "profile")):
                    segs = inner["a1ttsv2"]["segments"]
                    total = sum(len(s["text"]) for s in segs)
                    print(f"[TTS] 模型没输出台本 → 已补写 {len(segs)} 段"
                          f"（共 {total} 字，口吻={_prof or 'natural'}"
                          f"，表演={_perf}:{_why}）")
            except Exception as e:
                print(f"[TTS] 台本兜底失败（不影响返回）: {e}", flush=True)

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

    # 语音 MOD（A1IndexTTSMod）诊断：一眼看出台本字段有没有穿过中转。
    # 没有的话 NPC 只出文本不朗读 —— 可能是模型没输出，也可能被清洗丢了。
    tts_keys = [k for k in inner.keys() if k.lower().startswith("a1tts")]
    frame_in_text = "<a1tts" in clean_text
    if tts_keys or frame_in_text:
        print(f"[TTS] 台本已保留: 字段={tts_keys or '无'} 正文帧={'有' if frame_in_text else '无'}")
    else:
        print("[TTS] 本轮响应没有台本字段（MOD 侧显示 absent 属正常，是模型没输出）")

    # ---- 语音风格层 v2（实验性，默认关）--------------------------------
    # 做三件默认行为不做的事：
    #   1) 用 decode_reply 兼容「模型把风格数据写成顶层 a1tts_v1 字段」的写法
    #      （实测 wa-unred 就是这样，老的内嵌帧提取会整帧丢掉）；
    #   2) 把 delivery 规整成官方两段式「角色基线；逐句表演」，超长截断；
    #   3) 模型没给表演段时，用 perceive 规则补第二层。
    # 开关关掉时这一段完全跳过，数据一字不改。
    if _AI.get("tts_style_v2"):
        try:
            _sl_apply_style_v2(inner, clean_text, user_say,
                               npc_id=active_npc_id, npc_name=active_npc_name)
        except Exception as e:
            print(f"[TTS] v2 处理失败（不影响返回）: {e}", flush=True)

    return Response(json.dumps(outer, ensure_ascii=False),
                    status=200, content_type="application/json; charset=utf-8")



# --- TTS 上游自动拉起 -------------------------------------------------------
# 一旦 BreezeTtsUrl 指向本代理，MOD 就不再自己启动 audio.cpp 了 —— 它会每 7 秒
# 轮询 /health，探测不到就只是干等，永远不出声（2026-10-30 实测）。
# 所以拉起 8893 的责任转移到代理这边。
_TTS_PROC = None
_TTS_LAUNCH_LOCK = __import__("threading").Lock()
_TTS_LAST_TRY = 0.0


def _tts_paths():
    """从 ai_config 取 runtime 与配置路径，缺省回落到 MOD 的默认位置。"""
    a1 = str(_AI.get("tts_a1_dir")
             or r"H:\SteamLibrary\steamapps\common\A1\A1IndexTTSMod")
    rt = os.path.join(a1, ".cache", "audiocpp", "runtime", "audiocpp_server.exe")
    cfg = os.path.join(a1, ".state", "audiocpp", "server.breeze2.json")
    return rt, cfg


def _derive_tts_config(src_cfg: str, port: int) -> str:
    """基于 MOD 的配置派生一份，只把 port 改成本代理要连的端口。

    ★ 必须派生，不能直接用 MOD 的：MOD 会把自己 BreezeTtsUrl 的端口写进
    server.breeze2.json（实测已变成 8789 = 代理自己的端口）。若直接拿它启动，
    audio.cpp 会和代理抢同一个端口，起不来（ConnectionError 死循环）。
    """
    d = json.load(open(src_cfg, encoding="utf-8-sig"))
    d["port"] = int(port)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", ".tts_upstream.json")
    out = os.path.abspath(out)
    json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return out


def _ensure_tts_upstream(upstream: str) -> bool:
    """确保上游在跑；没跑就拉起来。返回是否可用。"""
    global _TTS_PROC, _TTS_LAST_TRY
    try:
        requests.get(f"{upstream}/health", timeout=2)
        return True
    except Exception:
        pass

    with _TTS_LAUNCH_LOCK:
        # 冷却：避免 MOD 每次轮询都触发一次重启
        if time.time() - _TTS_LAST_TRY < 30:
            return False
        _TTS_LAST_TRY = time.time()

        rt, cfg = _tts_paths()
        if not (os.path.isfile(rt) and os.path.isfile(cfg)):
            print(f"[TTS-PROXY] 无法拉起上游：缺少 {rt} 或 {cfg}")
            return False

        # 端口取自 upstream 地址（默认 8893），并派生一份配置避免与代理抢端口
        try:
            port = int(upstream.rsplit(":", 1)[1].split("/")[0])
        except Exception:
            port = 8893
        try:
            cfg = _derive_tts_config(cfg, port)
        except Exception as e:
            print(f"[TTS-PROXY] 派生配置失败: {type(e).__name__}: {e}")
            return False

        import subprocess
        env = {k: v for k, v in os.environ.items()
               if k.lower() not in ("http_proxy", "https_proxy", "all_proxy")}
        # audio.cpp 的输出留档：拉不起来时唯一能看的原因就在这
        ulog = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", ".tts_upstream.log"))
        fh = open(ulog, "wb")
        try:
            # 不用 DETACHED_PROCESS：实测加了它 audio.cpp 会卡在 CUDA 初始化后
            # 不再往下走（同一份配置手动跑却能正常 listening）。代价是代理退出时
            # 子进程可能成为孤儿，可接受。
            _TTS_PROC = subprocess.Popen(
                [rt, "--config", cfg],
                cwd=os.path.dirname(rt), env=env,
                stdout=fh, stderr=subprocess.STDOUT)
            print(f"[TTS-PROXY] 已拉起 audio.cpp PID={_TTS_PROC.pid} port={port} 日志={ulog}")
        except Exception as e:
            print(f"[TTS-PROXY] 拉起失败: {type(e).__name__}: {e}")
            return False

    # 等它就绪（模型加载约 20~40 秒）
    port_hint = ""
    try:
        port_hint = upstream.rsplit(":", 1)[1].split("/")[0]
    except Exception:
        pass
    for _ in range(40):
        time.sleep(1.5)
        try:
            requests.get(f"{upstream}/health", timeout=2)
            print(f"[TTS-PROXY] 上游就绪 ({port_hint})")
            return True
        except Exception:
            continue
    print(f"[TTS-PROXY] 上游拉起后仍未就绪 ({port_hint})")
    return False


def _wsola_hop(rate: float) -> int:
    """挑合成步长，使 int(sh × rate) 的取整误差最小，平局取最接近 240 的。

    ★ 抄自 VoiceLab 的实测结论：audiotsm 内部 analysis_hop = int(sh × speed)，
    取整误差直接变成速率误差 —— sh=256 时 0.8x 得 204（偏 0.39%），
    MCD 从 1.0 飙到 6.2（音质崩）。这里按倍率动态选，实测倍率误差 < 0.001。
    """
    return min((round(abs(sh * rate - round(sh * rate)), 9),
                abs(sh - 240), sh) for sh in range(180, 321))[2]


def _apply_tts_post(wav_bytes: bytes, speed: float = 1.0,
                    semitones: float = 0.0, eq_freq: float = 0.0,
                    eq_gain: float = 0.0, eq_q: float = 1.0,
                    volume: float = 0.0) -> bytes:
    """对 TTS 输出的 wav 做后处理：变调 → 变速 → EQ → 音量。失败时抛异常。

    顺序为什么是「先变调再变速」：变调内部要用 WSOLA 拉伸再重采样，
    如果先变速，WSOLA 的拼接点会被重采样再打一次，痕迹更重。

    变调原理（时长不变、音高变）：
        WSOLA 拉到 1/ratio 速度（音频变长 ratio 倍）→ 重采样压缩回原长度
        → 净效果是音高乘 ratio，时长不变。

    Q 值的用法：Q 大 = 只影响很窄一段（适合修某个共振峰）；
    Q 小（0.3~0.5）= 影响很宽（把 150Hz 附近的 Q 调小就是"加低频厚度"）。
    """
    import io
    import wave as _wave
    import numpy as np
    from audiotsm import wsola
    from audiotsm.io.array import ArrayReader, ArrayWriter

    with _wave.open(io.BytesIO(wav_bytes)) as w:
        fs, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise ValueError(f"只支持 16bit PCM，收到 {sw*8}bit")

    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)      # 降单声道（Breeze 本来就单声道）

    def _wsola(x, sp):
        h = _wsola_hop(sp)
        wr = ArrayWriter(channels=1)
        wsola(channels=1, speed=sp, frame_length=h * 2, synthesis_hop=h).run(
            ArrayReader(np.asarray(x, dtype=np.float32).reshape(1, -1).copy()), wr)
        return np.asarray(wr.data)[0].astype(np.float32)

    # 1) 变调
    if abs(semitones) >= 0.1:
        ratio = 2.0 ** (semitones / 12.0)
        stretched = _wsola(a, 1.0 / ratio)          # 变长 ratio 倍
        from scipy.signal import resample
        a = resample(stretched, int(len(a))).astype(np.float32)

    # 2) 变速
    if abs(speed - 1.0) >= 0.02:
        a = _wsola(a, speed)

    # 3) EQ（峰值滤波：只抬/压某一段，不动整体音色）
    if abs(eq_gain) >= 0.5 and eq_freq > 20:
        from scipy.signal import lfilter
        A = 10 ** (eq_gain / 40)
        w0 = 2 * np.pi * eq_freq / fs
        al = np.sin(w0) / (2 * max(eq_q, 0.1))
        b = [1 + al * A, -2 * np.cos(w0), 1 - al * A]
        d = [1 + al / A, -2 * np.cos(w0), 1 - al / A]
        a = lfilter(b, d, a).astype(np.float32)

    # 4) 整体音量（放在 EQ 之后，避免 EQ 抬起来的部分把整体推爆）
    if abs(volume) >= 0.5:
        a = a * (10 ** (volume / 20.0))

    # 削波保护：超过 -3dBFS 就整体压下来，别让 MOD 那边再削一次
    pk = float(np.max(np.abs(a))) if len(a) else 0.0
    if pk > 0.708:
        a = a * (0.708 / pk)

    pcm = np.clip(a * 32767, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


@app.route("/health", methods=["GET"])
def tts_health():
    """语音服务的健康探测。

    ★ MOD 在发 TTS 请求之前会先 GET /health 探活；这里 404 会让它判定
    「服务不可用」而直接跳过合成 —— 表现为游戏里完全没声音（2026-10-30 实测）。

    ★ 8893 没起时这里会主动拉起它（MOD 若发现 BreezeTtsUrl 指向外部就不再负责启动）。
    直接转发上游的 health，保证返回体格式与 audio.cpp 一致
    （{"status":"ok","backend":"cuda","models":1,...}）。
    """
    upstream = str(_AI.get("tts_upstream") or "http://127.0.0.1:8893").rstrip("/")
    try:
        r = requests.get(f"{upstream}/health", timeout=5)
        return Response(r.content, status=r.status_code,
                        mimetype=r.headers.get("Content-Type", "application/json"))
    except Exception:
        # 上游没跑：尝试拉起，成功后重试一次
        if _ensure_tts_upstream(upstream):
            try:
                r = requests.get(f"{upstream}/health", timeout=5)
                return Response(r.content, status=r.status_code,
                                mimetype=r.headers.get("Content-Type", "application/json"))
            except Exception as e:
                print(f"[TTS-PROXY] /health 重试仍失败 {type(e).__name__}")
        print(f"[TTS-PROXY] /health 上游不可用 {upstream}")
        return Response(json.dumps({"status": "down", "upstream": upstream}),
                        status=503, mimetype="application/json")


def _apply_instruct_map(raw_body):
    """按台词关键词改 options.instruct，返回 (body, 日志片段)。

    ## 为什么在代理做
    MOD 那边把台词发过来、我们自己再拼一次指令，是唯一不用改 MOD 的插入点；
    而且关掉开关就能整体回退，风险可控。

    ## 三条边界
    - 解析失败/配置关闭：原样返回，绝不因为这里抛异常让语音断掉
    - instruct 必须写进 options（顶层会被 audio.cpp 静默忽略）
    - 只补不改为默认：mode=fill 时 MOD 已经有指令就不动它
    """
    if not _AI.get("tts_instruct_map_enabled"):
        return raw_body, ""
    mode = str(_AI.get("tts_instruct_map_mode") or "fill")
    try:
        from instruct_map import decide
    except Exception as e:                      # 打包漏了模块也只会退化，不影响转发
        print(f"[TTS-PROXY] instruct 映射不可用: {type(e).__name__}: {e}", flush=True)
        return raw_body, ""
    try:
        payload = json.loads(raw_body.decode("utf-8"))
        if not isinstance(payload, dict):
            return raw_body, ""
        text = str(payload.get("input") or "")
        opts = payload.get("options")
        opts = dict(opts) if isinstance(opts, dict) else {}
        base = str(opts.get("instruct") or payload.get("instruct") or "")
        final, key = decide(text, base, mode=mode,
                            rules=_AI.get("tts_instruct_map_rules"))
        if not key:
            return raw_body, ""
        opts["instruct"] = final
        payload["options"] = opts
        return (json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                f" 指令={key}({mode})")
    except Exception as e:
        print(f"[TTS-PROXY] instruct 映射失败，原样转发: {type(e).__name__}: {e}",
              flush=True)
        return raw_body, ""


@app.route("/v1/audio/speech", methods=["POST"])
def tts_proxy():
    """TTS 转发：把 MOD 的语音请求中转到 audio.cpp，为后处理留出插入点。

    ## 为什么可以走这里

    实测 audio.cpp 的 /v1/audio/speech **永远返回完整 wav**（5 种流式尝试全部返回
    相同的 Content-Length，没有 chunked 也没有 SSE）。MOD 的「流式播放」是它自己
    收到完整 wav 后按 BreezeStreamingBufferMs 分块播的。所以中间插一层代理：
    - 不损失流式（服务端本来就没有）
    - 延迟只多一次本地 HTTP 跳转（几十毫秒）

    ## 当前行为

    纯转发，一个字节都不改。目的先验证 MOD 能否接受代理返回的响应
    （Content-Type / X-AudioCPP-* 头 / 二进制体）。通了之后再在这里叠加
    WSOLA 变速和 EQ。

    改 MOD 的 BreezeTtsUrl 指向本服务：http://127.0.0.1:8789/v1/audio/speech
    """
    upstream = str(_AI.get("tts_upstream") or "http://127.0.0.1:8893").rstrip("/")
    # 让 tts_speed / tts_upstream 这类改动存盘即生效（否则要重启代理）
    try:
        load_ai_config()
    except Exception:
        pass
    upstream = str(_AI.get("tts_upstream") or "http://127.0.0.1:8893").rstrip("/")
    body = request.get_data()
    # ⚠ 语音输出增强已停用（2026-10-30）：脱离 MOD 的独立语音方案暂不可行，
    #   先把「台词关键词 → options.instruct」和「音频后处理」两处关掉，代码全部保留。
    #   恢复办法：去掉下两行的注释即可（其余代码不用动）。
    # body, instruct_note = _apply_instruct_map(body)
    instruct_note = ""
    hdrs = {"Content-Type": request.headers.get("Content-Type", "application/json")}
    t0 = time.time()
    try:
        up = requests.post(f"{upstream}/v1/audio/speech", data=body,
                           headers=hdrs, timeout=600)
    except Exception as e:
        # 上游可能刚挂 / 还没起：拉起后重试一次
        print(f"[TTS-PROXY] 转发失败 {upstream}: {type(e).__name__}")
        if not _ensure_tts_upstream(upstream):
            return Response(f"tts upstream unavailable: {e}", status=502,
                            mimetype="text/plain")
        try:
            up = requests.post(f"{upstream}/v1/audio/speech", data=body,
                               headers=hdrs, timeout=600)
        except Exception as e2:
            print(f"[TTS-PROXY] 重试失败: {type(e2).__name__}")
            return Response(f"tts upstream failed: {e2}", status=502,
                            mimetype="text/plain")

    elapsed = time.time() - t0
    resp_bytes = up.content
    post_note = "无"

    # 后处理：变调 / 变速 / EQ。任何一个参数偏离默认值才动手
    def _num(key, default):
        try:
            return float(_AI.get(key) if _AI.get(key) is not None else default)
        except Exception:
            return default

    speed = _num("tts_speed", 1.0)
    semis = _num("tts_pitch", 0.0)
    eq_freq = _num("tts_eq_freq", 2000.0)
    eq_gain = _num("tts_eq_gain", 0.0)
    eq_q = _num("tts_eq_q", 1.0)
    volume = _num("tts_volume", 0.0)
    # ⚠ 已停用（见上）：恢复时把这行 need_post 的注释换回来即可。
    need_post = False
    # need_post = (abs(speed - 1.0) >= 0.02 or abs(semis) >= 0.1
    #              or abs(eq_gain) >= 0.5 or abs(volume) >= 0.5)

    enabled = bool(_AI.get("tts_post_enabled", True))
    if enabled and need_post and up.status_code == 200 \
            and str(up.headers.get("Content-Type", "")).startswith("audio/"):
        t1 = time.time()
        try:
            resp_bytes = _apply_tts_post(resp_bytes, speed, semis,
                                         eq_freq, eq_gain, eq_q, volume)
            bits = []
            if abs(semis) >= 0.1:
                bits.append(f"变调{semis:+.1f}半音")
            if abs(speed - 1.0) >= 0.02:
                bits.append(f"变速x{speed:.2f}")
            if abs(eq_gain) >= 0.5:
                bits.append(f"EQ{eq_freq:.0f}Hz{eq_gain:+.1f}dB/Q{eq_q:.1f}")
            if abs(volume) >= 0.5:
                bits.append(f"音量{volume:+.1f}dB")
            post_note = " ".join(bits) + f"({(time.time()-t1)*1000:.0f}ms)"
        except Exception as e:
            # 后处理失败绝不能导致没声音，原样返回
            post_note = f"后处理失败({type(e).__name__})已跳过"
            print(f"[TTS-PROXY] 后处理失败，原样返回: {type(e).__name__}: {e}")

    # 原样带回 audio.cpp 的诊断头，MOD 若读这些值不会因代理而丢失
    resp = Response(resp_bytes, status=up.status_code)
    for k in ("Content-Type", "X-AudioCPP-Wall-Ms",
              "X-AudioCPP-Audio-Duration-Ms", "X-AudioCPP-RTF"):
        if k in up.headers:
            resp.headers[k] = up.headers[k]
    n = len(resp_bytes)
    print(f"[TTS-PROXY] {up.status_code} {n} 字节 {elapsed:.2f}s "
          f"upstream={upstream}{instruct_note} 后处理={post_note}")
    return resp


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
        "usage": {
            "rounds": _USAGE["rounds"],
            "prompt_tokens": _USAGE["prompt_tokens"],
            "completion_tokens": _USAGE["completion_tokens"],
            "total_tokens": _USAGE["prompt_tokens"] + _USAGE["completion_tokens"],
            "last": _USAGE["last"],
            "by_model": _USAGE["by_model"],
            "cost": _compute_cost(),
            "price_input_per_million": _AI.get("price_input_per_million", 0),
            "price_output_per_million": _AI.get("price_output_per_million", 0),
        },
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

    # 测试器用：逐条规则的命中情况（在【注入前】的原文上统计，
    # 这样能看到"这条规则到底能不能命中"，而不是被别的规则改过之后的结果）
    rule_report = []
    original_sys = ""
    for m in messages:
        if m.get("role") == "system" and isinstance(m.get("content"), str):
            original_sys = m["content"]
            break
    for rule in _AI["rules"]:
        if len(rule) >= 4:
            old, new, mode, flags = rule[0], rule[1], rule[2], rule[3]
        else:
            old, new, mode, flags = rule[0], rule[1], "text", ""
        if not old:
            continue
        if mode == "regex":
            try:
                hits = len(re.findall(old, original_sys, flags=_parse_regex_flags(flags)))
            except re.error:
                hits = -1          # 语法错误，前端标红
            except Exception:
                hits = -1
        else:
            hits = original_sys.count(old)
        rule_report.append({"old": old, "new": new, "mode": mode,
                            "flags": flags, "hits": hits})

    return jsonify({
        "ok": True,
        "stats": stats,
        "system": system_text,
        "roles": [m.get("role") for m in final],
        "rule_report": rule_report,
        # 记忆注入明细：每条事实的分数与是否注入，用来解释"为什么这条进了/没进"
        "memory_detail": memory_manager.get_last_injection_detail() if with_memory else [],
        "messages": final,
    })


if __name__ == "__main__":
    app.run(host=LISTEN_HOST, port=LISTEN_PORT, threaded=True)
