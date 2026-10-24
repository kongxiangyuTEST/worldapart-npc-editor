import webview
import os
import re
import sys
import json
import shutil
import time
import memory_manager

# ==================== 智能动态路径 ====================
if getattr(sys, 'frozen', False):
    # 打包环境：exe 所在的目录
    base_dir = os.path.dirname(sys.executable)
else:
    # 源码环境：当前 py 文件所在的目录
    base_dir = os.path.dirname(os.path.abspath(__file__))

memory_manager.MEMORY_DIR = os.path.join(base_dir, "memory")
if not os.path.exists(memory_manager.MEMORY_DIR):
    os.makedirs(memory_manager.MEMORY_DIR)

import threading
from datetime import datetime
from config import CARDS_DIR, WEB_DIR, IGNORED_FILES, CARD_NAME_PREFIX_RE
import services
from services import CORE_PROXY, CADDY

# ==================== 路径 ====================
if getattr(sys, 'frozen', False):
    PROJECT_DIR = os.path.dirname(sys.executable)
else:
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ORIGINALS_DIR = os.path.join(PROJECT_DIR, "originals")
LAST_SAVED_DIR = os.path.join(PROJECT_DIR, "last_saved")
AI_CONFIG_PATH = os.path.join(PROJECT_DIR, "ai_config.json")

# ==================== 注入方案（预设） ====================
# 把"注入语 + 替换规则 + 名片开关 + 记忆/预算参数"打包成一套方案，
# 存多套、一键切换、方便 A/B 对比。
#
# 方案只覆盖下面这些键，其余配置（上游、单价、合并地址等）不受影响。
PRESET_KEYS = ("inject", "rules", "inject_user_card", "memory_slot", "save_guard",
               "memory_budget", "past_events_max_chars",
               "history_max_chars", "history_keep_turns")

# 内置两套：只含预算类参数，**不含 inject/rules**——
# 用户自己写的注入语和规则不该被内置方案覆盖。
# 数值参考 other/ 那份 MOD 的「记忆档位」两档设计。
BUILTIN_PRESETS = {
    "精简（省 token）": {
        "memory_budget": 1200,
        "past_events_max_chars": 500,
        "history_max_chars": 16000,
        "history_keep_turns": 4,
    },
    "完整（留更多细节）": {
        "memory_budget": 2000,
        "past_events_max_chars": 800,
        "history_max_chars": 24000,
        "history_keep_turns": 6,
    },
}


def _read_ai_conf():
    if os.path.isfile(AI_CONFIG_PATH):
        with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {}


def _write_ai_conf(data):
    with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# 文件对话框的"上次打开位置"存这里：不放进 ai_config（那是给 core_proxy 读的）
UI_STATE_PATH = os.path.join(PROJECT_DIR, "ui_state.json")


def _ui_state_load():
    try:
        with open(UI_STATE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _ui_state_save(d):
    try:
        with open(UI_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _pick_start_dir(key, fallback=""):
    """取某个对话框上次用的目录；不存在就退回 fallback，再不行返回空。"""
    p = _ui_state_load().get(key) or fallback or ""
    return p if p and os.path.isdir(p) else ""


def _remember_pick(key, path):
    if not path:
        return
    d = _ui_state_load()
    d[key] = path
    _ui_state_save(d)
PROMPT_FRAGMENTS_PATH = os.path.join(PROJECT_DIR, "prompt_fragments.json")
PORTRAITS_DIR = os.path.join(WEB_DIR, "portraits")
PORTRAITS_INDEX_PATH = os.path.join(PROJECT_DIR, "portraits_index.json")
PROMPT_SAMPLES_DIR = os.path.join(PROJECT_DIR, "prompt_samples")

os.makedirs(ORIGINALS_DIR, exist_ok=True)
os.makedirs(LAST_SAVED_DIR, exist_ok=True)


def ensure_user_configs():
    """首次启动时，从 example 生成实际配置文件"""
    ai_path = os.path.join(PROJECT_DIR, "ai_config.json")
    ai_example = os.path.join(PROJECT_DIR, "ai_config.example.json")
    if not os.path.isfile(ai_path) and os.path.isfile(ai_example):
        try:
            shutil.copy2(ai_example, ai_path)
            print(f"[启动] 已生成 ai_config.json")
        except Exception as e:
            print(f"[启动] 生成 ai_config.json 失败: {e}")

    u_path = os.path.join(PROJECT_DIR, "user_config.json")
    u_example = os.path.join(PROJECT_DIR, "user_config.example.json")
    if not os.path.isfile(u_path) and os.path.isfile(u_example):
        try:
            shutil.copy2(u_example, u_path)
            print(f"[启动] 已生成 user_config.json")
        except Exception as e:
            print(f"[启动] 生成 user_config.json 失败: {e}")

# ==================== Markdown 卡片解析 ====================
CARD_RE = re.compile(CARD_NAME_PREFIX_RE)
# ==================== 头像映射 ====================
def load_portraits_index():
    """加载"游戏路径 → zip 文件名"的映射"""
    if not os.path.isfile(PORTRAITS_INDEX_PATH):
        print(f"[头像] 找不到 {PORTRAITS_INDEX_PATH}")
        return {}
    try:
        with open(PORTRAITS_INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[头像] 加载失败: {e}")
        return {}


PORTRAITS_INDEX = load_portraits_index()
print(f"[头像] 已加载 {len(PORTRAITS_INDEX)} 条映射")


def get_portrait_filename(npc_id):
    """根据 NPC ID，返回 zip 内文件名（如 "0.png"），没有则返回 None"""
    key = f"assets/res/gui/atlas/headportrait/{npc_id}.png"
    return PORTRAITS_INDEX.get(key)

def parse_card_file(path):
    fname = os.path.basename(path)
    m = CARD_RE.match(fname)
    if not m:
        return None
    npc_id = int(m.group(1))
    name_from_file = m.group(2)

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    lines = raw.split("\n")
    result = {
        "filename": fname,
        "id": npc_id,
        "name": name_from_file,
        "title_in_file": "",
        "user_card": "",
        "preset": {"姓名": "", "性别": "", "年龄": "", "境界": "", "个性": "", "兴趣": ""},
        "background": "",
        "raw": raw,
    }

    section = None
    user_card_lines = []
    background_lines = []
    skip_comment = False

    for line in lines:
        stripped = line.rstrip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            result["title_in_file"] = stripped[2:].strip()
            section = "title"
            continue
        if stripped.startswith("## "):
            h2 = stripped[3:].strip()
            if h2.startswith("用户名片"):
                section = "user_card"
            elif h2.startswith("预设资料"):
                section = "preset"
            elif h2.startswith("原游戏背景"):
                section = "background"
            else:
                section = "other"
            skip_comment = False
            continue

        if section == "user_card":
            if "<!--" in line:
                skip_comment = True
            if skip_comment:
                if "-->" in line:
                    skip_comment = False
                continue
            user_card_lines.append(line)
        elif section == "preset":
            s = stripped.strip()
            if s.startswith("- "):
                kv = s[2:].split("：", 1)
                if len(kv) == 2:
                    key = kv[0].strip()
                    val = kv[1].strip()
                    if key in result["preset"]:
                        result["preset"][key] = val
        elif section == "background":
            background_lines.append(line)

    result["user_card"] = "\n".join(user_card_lines).strip()
    result["background"] = "\n".join(background_lines).strip()
    return result


def build_card_file(card):
    name = card["name"]
    npc_id = card["id"]
    preset = card["preset"]
    user_card = card.get("user_card", "")
    background = card.get("background", "")

    lines = []
    lines.append(f"# {name}")
    lines.append("")
    lines.append(f"NPC ID：{npc_id}")
    lines.append("")
    lines.append("## 用户名片（优先读取，可自行增添）")
    lines.append("")
    lines.append("<!-- 在此填写说话习惯、身世、目标、与玩家的约定等；有冲突时优先采用你明确写下的角色设定。 -->")
    lines.append("")
    if user_card.strip():
        lines.append(user_card.strip())
        lines.append("")
    lines.append("## 预设资料（可修改；导出时快照）")
    lines.append("")
    lines.append(f"- 姓名：{preset.get('姓名', name)}")
    lines.append(f"- 性别：{preset.get('性别', '未知')}")
    lines.append(f"- 年龄：{preset.get('年龄', '0')}")
    # 境界：配置表能推出来（tbnpcbasecfg.propLayerId + tbcultivatephase.layers），
    # 由 tools/apply_realm.py 批量填充；导出时保留卡片里已有的值。
    realm = str(preset.get('境界', '') or '').strip()
    if realm:
        lines.append(f"- 境界：{realm}")
    lines.append(f"- 个性：{preset.get('个性', '')}")
    lines.append(f"- 兴趣：{preset.get('兴趣', '')}")
    lines.append("")
    lines.append("## 原游戏背景")
    lines.append("")
    if background.strip():
        lines.append(background.strip())
        lines.append("")

    return "\n".join(lines)


# ==================== 备份 ====================

def ensure_original_backup(filename):
    r"""确保 originals\ 里有这张卡片的原始备份"""
    src = os.path.join(CARDS_DIR, filename)
    dst = os.path.join(ORIGINALS_DIR, filename)
    if not os.path.exists(dst) and os.path.isfile(src):
        try:
            shutil.copy2(src, dst)
            print(f"[备份] 生成原版备份: {filename}")
        except Exception as e:
            print(f"[备份] 失败 {filename}: {e}")


def ensure_last_saved_backup(filename):
    r"""确保 last_saved\ 里有这张卡片的备份（首次直接复制当前文件）"""
    src = os.path.join(CARDS_DIR, filename)
    dst = os.path.join(LAST_SAVED_DIR, filename)
    if not os.path.exists(dst) and os.path.isfile(src):
        try:
            shutil.copy2(src, dst)
            print(f"[备份] 生成上次保存备份: {filename}")
        except Exception as e:
            print(f"[备份] 失败 {filename}: {e}")

# ==================== 样本查询 ====================

def find_latest_sample(npc_name):
    r"""
    在 prompt_samples\ 里找"含该 NPC 名字"的最新样本。
    返回：{"filename": ..., "mtime": ..., "content": ...} 或 None
    """
    if not os.path.isdir(PROMPT_SAMPLES_DIR):
        return None

    matched = []
    for fname in os.listdir(PROMPT_SAMPLES_DIR):
        if not fname.endswith(".json"):
            continue
        # 文件名格式：20261012-234318-苏倾盏.json
        # 判断是否含 NPC 名字
        if npc_name and npc_name not in fname:
            continue
        path = os.path.join(PROMPT_SAMPLES_DIR, fname)
        try:
            mtime = os.path.getmtime(path)
            matched.append((mtime, fname, path))
        except Exception:
            continue

    if not matched:
        return None

    # 取最新，但要跳过"非对话"样本。
    # 游戏自己也有一套记忆系统，它会把"记忆压缩请求"发到同一个接口，
    # 这种样本的 system 是「你是对话记忆压缩器…」，没有「## 角色设定」。
    # 只按时间取最新会挑到它，导致"预览效果"跑错对象、命中 0 条规则。
    matched.sort(reverse=True)
    picked = None
    for mtime, fname, path in matched:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        msgs = data.get("messages") or []
        is_dialogue = any(
            m.get("role") == "system"
            and isinstance(m.get("content"), str)
            and "角色设定" in m["content"]
            for m in msgs
        )
        if is_dialogue:
            picked = (mtime, fname, data)
            break

    if picked is None:
        return None

    mtime, fname, data = picked

    return {
        "filename": fname,
        "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "content": data,
    }


def open_dir(path):
    """用系统默认程序打开文件夹"""
    if os.path.isdir(path):
        os.startfile(path)
        return True
    return False


# ==================== 卡片管理 ====================
def list_cards():
    if not os.path.isdir(CARDS_DIR):
        return []
    result = []
    for fname in os.listdir(CARDS_DIR):
        if not fname.endswith(".md"):
            continue
        if fname in IGNORED_FILES:
            continue
        m = CARD_RE.match(fname)
        if not m:
            continue
        # 首次扫描时，确保备份存在
        ensure_original_backup(fname)
        ensure_last_saved_backup(fname)

        path = os.path.join(CARDS_DIR, fname)
        user_card = ""
        try:
            card = parse_card_file(path)
            if card:
                user_card = card.get("user_card", "")
        except Exception as e:
            print(f"[warn] 解析失败: {fname} - {e}")
        npc_id = int(m.group(1))
        result.append({
            "id": npc_id,
            "name": m.group(2),
            "filename": fname,
            "user_card": user_card,
            "portrait_file": get_portrait_filename(npc_id),
        })
    result.sort(key=lambda x: x["id"])
    return result


def get_card(filename):
    path = os.path.join(CARDS_DIR, filename)
    if not os.path.isfile(path):
        return None
    return parse_card_file(path)


def save_card(filename, card):
    path = os.path.join(CARDS_DIR, filename)
    new_name = card.get("name", "").strip()
    if new_name:
        expected_fname = f"{card['id']}-{new_name}.md"
    else:
        expected_fname = filename
    content = build_card_file(card)

    if expected_fname != filename:
        new_path = os.path.join(CARDS_DIR, expected_fname)
        if os.path.isfile(path):
            os.remove(path)
        with open(new_path, "w", encoding="utf-8") as f:
            f.write(content)
        # 同步更新 last_saved
        with open(os.path.join(LAST_SAVED_DIR, expected_fname), "w", encoding="utf-8") as f:
            f.write(content)
        return expected_fname
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        with open(os.path.join(LAST_SAVED_DIR, filename), "w", encoding="utf-8") as f:
            f.write(content)
        return filename


def create_card(npc_id, name):
    filename = f"{npc_id}-{name}.md"
    path = os.path.join(CARDS_DIR, filename)
    if os.path.exists(path):
        return None
    card = {
        "id": npc_id,
        "name": name,
        "user_card": "",
        "preset": {"姓名": name, "性别": "未知", "年龄": "0",
                   "境界": "未知或凡人", "个性": "", "兴趣": ""},
        "background": "",
    }
    content = build_card_file(card)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    # 新卡：原版和上次保存都复制一份
    shutil.copy2(path, os.path.join(ORIGINALS_DIR, filename))
    shutil.copy2(path, os.path.join(LAST_SAVED_DIR, filename))
    return filename


def delete_card(filename):
    path = os.path.join(CARDS_DIR, filename)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def get_original_card(filename):
    r"""读取 originals\ 里的原始卡片"""
    path = os.path.join(ORIGINALS_DIR, filename)
    if not os.path.isfile(path):
        return None
    return parse_card_file(path)


def get_last_saved_card(filename):
    r"""读取 last_saved\ 里的卡片"""
    path = os.path.join(LAST_SAVED_DIR, filename)
    if not os.path.isfile(path):
        return None
    return parse_card_file(path)


# ==================== 日志缓冲 ====================
LOG_BUFFER = []
LOG_LOCK = threading.Lock()
LOG_MAX = 500


def push_log(name, line):
    ts = datetime.now().strftime("%H:%M:%S")
    with LOG_LOCK:
        LOG_BUFFER.append({"time": ts, "name": name, "line": line})
        if len(LOG_BUFFER) > LOG_MAX:
            del LOG_BUFFER[: len(LOG_BUFFER) - LOG_MAX]

# ==================== 服务管理器 ====================
service_mgr = services.init_manager(log_callback=push_log)


# ==================== API ====================


def _loose_extract_fields(raw):
    """整体 JSON 坏掉时（常见原因：值里带了 ASCII 双引号），按字段逐个捞。

    只要每个值还被引号包着就能捞出来；值本身含引号时会被截断，
    但比整块失败强。返回 dict，可能为空。
    """
    out = {}
    for key in ("tone", "rhythm", "self_ref", "address"):
        m = re.search(r'"' + key + r'"\s*:\s*"([^"\n]{1,200})"', raw)
        if m:
            out[key] = m.group(1).strip()
    for key in ("catch", "habits", "rules", "lines"):
        m = re.search(r'"' + key + r'"\s*:\s*\[(.*?)\]', raw, re.S)
        if not m:
            continue
        items = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
        out[key] = [x.replace('\\"', '"').strip() for x in items if x.strip()]
    return out


class Api:
        # --- 样本查看 ---
    def get_latest_sample(self, npc_name):
        """获取某 NPC 的最新样本"""
        sample = find_latest_sample(npc_name)
        if sample is None:
            return {"ok": False, "msg": f"没有 {npc_name} 的样本，请进游戏对话一次后再看"}
        return {"ok": True, "data": sample}

    def open_samples_dir(self):
        """打开样本目录"""
        ok = open_dir(PROMPT_SAMPLES_DIR)
        return {"ok": ok}
    
        # --- 头像 ---
    def get_portrait_url(self, npc_id):
        """
        返回头像的可访问 URL（通过 pywebview 的静态文件服务）。
        没头像则返回 None。
        """
        filename = get_portrait_filename(int(npc_id))
        if not filename:
            return None
        # 检查文件是否存在
        path = os.path.join(PORTRAITS_DIR, filename)
        if not os.path.isfile(path):
            return None
        return f"portraits/{filename}"
    
        # --- 全局提示词片段 ---
    def get_prompt_fragments(self):
        """读取 prompt_fragments.json"""
        if not os.path.isfile(PROMPT_FRAGMENTS_PATH):
            return {"ok": False, "msg": "prompt_fragments.json 不存在"}
        try:
            with open(PROMPT_FRAGMENTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"ok": True, "data": data}
        except Exception as e:
            return {"ok": False, "msg": f"读取失败: {e}"}
        
        # --- 长期记忆 ---
    def get_memory_list(self):
        """列出 memory/ 下的记忆文件，返回结构化条目给前端（含错误信息）。"""
        import traceback

        mem_dir = memory_manager.MEMORY_DIR
        try:
            items = []
            if os.path.isdir(mem_dir):
                for fname in os.listdir(mem_dir):
                    if not fname.endswith(".json"):
                        continue
                    stem = fname[:-5].strip()
                    npc_id, sep, npc_name = stem.partition("-")
                    if not sep or not npc_id.isdigit():
                        continue
                    path = os.path.join(mem_dir, fname)
                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        mtime = 0
                    items.append({
                        "id": int(npc_id),
                        "name": npc_name,
                        "filename": fname,
                        "mtime": mtime,
                        "portrait": get_portrait_filename(int(npc_id)),
                    })
            items.sort(key=lambda x: (-x["mtime"], x["id"]))
            return {"ok": True, "items": items, "debug_path": mem_dir}
        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "debug_path": mem_dir,
            }

    def get_memory(self, npc_id, npc_name):
        try:
            data = memory_manager.load_memory(int(npc_id), npc_name)
            conflict = memory_manager.get_game_conflict(int(npc_id), npc_name)
            return {"ok": True, "data": data, "conflict": conflict}
        except Exception as e:
            return {"ok": False, "msg": f"读取记忆失败: {e}"}

    def resolve_save_conflict(self, npc_id, npc_name, action):
        """处理"疑似换到另一份存档"的冲突。

        accept_new：确实换了档 → 旧记忆整份归档到 memory/_saves/，从空白重来
        keep_old  ：还是同一档 → 清掉冲突，继续用现在的记忆
        """
        try:
            return memory_manager.resolve_game_conflict(int(npc_id), npc_name, action)
        except Exception as e:
            return {"ok": False, "msg": f"处理失败: {e}"}

    def save_memory(self, npc_id, npc_name, data):
        try:
            success = memory_manager.save_memory(int(npc_id), npc_name, data)
            return {"ok": success, "msg": "" if success else "写入文件失败"}
        except Exception as e:
            return {"ok": False, "msg": f"保存记忆失败: {e}"}

    def trigger_memory_merge(self, npc_id, npc_name):
        import memory_manager
        import json
        import os
        import requests
        
        # 1. 上游：ai_config 的 memory_merge_url 优先（GUI 里"记忆总结 → 用本地 8080"就是改它），
        #    没填才退回 core_proxy_config.json 的 upstream_base_url
        upstream = ""
        fallback_key = ""
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                acfg = json.load(f)
            upstream = (acfg.get("memory_merge_url") or "").strip()
        except Exception:
            upstream = ""

        if not upstream:
            conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
            upstream_base = "http://127.0.0.1:8080/"
            try:
                with open(conf_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                upstream_base = (cfg.get("upstream_base_url") or upstream_base).rstrip("/")
                fallback_key = cfg.get("api_key") or ""
            except FileNotFoundError:
                print(f"[记忆总结] 没找到 {conf_path}，用默认 8080")
            except Exception as e:
                print(f"[记忆总结] 读取 core_proxy_config.json 失败: {e}")
            upstream = upstream_base + "/v1/chat/completions"
            
        data = memory_manager.load_memory(int(npc_id), npc_name)
        # 上次总结失败退回的对话也要一起合并，否则那批内容永远补不回来
        backlog = str(data.get("merge_backlog") or "").strip()
        logs = "\n\n".join(data.get("recent_logs", []))
        if backlog:
            logs = (logs + "\n\n" + backlog).strip()

        # 给点测试数据，免得你还没聊过，没东西总结
        if not logs:
            logs = "玩家：你好。\nAI：你好。"

        prompt = f"""
你是一个擅长总结的助手。请阅读以下对话，总结出“故事早期梗概”（只增不减）、“最近剧情”（滚动更新），并提取出“事实条目”。
【当前数据】：
早期梗概：{data.get('early_gist', '')}
最近剧情：{data.get('long_summary', '')}

【最近对话】：
{logs}

【输出格式】：
请严格输出 JSON 格式，不要包含任何 markdown 标记：
{{
  "time_location": "这段对话发生的地点与时段，例如：焚天宗大殿·夜；判断不出就填空字符串",
  "early_gist": "更新后的早期梗概（≤150字）",
  "long_summary": "更新后的最近剧情（≤650字）",
  "new_facts": [
    {{"category": "赠物/承诺/关系里程碑/重要事件", "text": "事实内容", "importance": 1-5的数字}}
  ]
}}

★ 压缩纪律（不遵守会让角色开始复读）：
1. 同一件事只写一遍，不要在不同字段里重复描述。
2. 不要把对话原文整句照抄进摘要或事实，用你自己的话概括。
3. new_facts 只写真正值得记的事实（赠物/承诺/关系里程碑/重要事件），一次调情一句闲话不要记。
4. 已有事实里写过的内容不要重复输出。
"""
        headers = {"Content-Type": "application/json"}
        if fallback_key: headers["Authorization"] = "Bearer " + fallback_key
        
        body = {
            "model": "local-model",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 4000,
            "thinking": {"type": "disabled"},
        }
        
        print(f"[记忆总结] 正在请求 {upstream} ...", flush=True)
        
        # 2. 发请求（这次我们不用线程，直接同步等待，如果出错了直接把错误扔给网页）
        try:
            res = requests.post(upstream, json=body, headers=headers, timeout=60)
            if res.status_code != 200:
                # 带上地址：401 到底是本地模型要 key，还是打到了第三方，一眼能看出
                hint = ""
                if res.status_code in (401, 403):
                    hint = "（地址或密钥不对：本地模型一般不需要 key，第三方 API 必须填对 key）"
                return {"ok": False,
                        "msg": f"模型报错: HTTP {res.status_code}（{upstream}）{hint}"}
                
            text = res.json()["choices"][0]["message"]["content"]
            # 暴力清理各种可能的大模型废话；再退一步：只取第一个 { 到最后一个 }
            text = text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            text = text.strip()
            _a, _b = text.find("{"), text.rfind("}")
            if _a >= 0 and _b > _a:
                text = text[_a:_b + 1]

            res_data = json.loads(text)

            if res_data.get("early_gist"):
                data["early_gist"] = res_data["early_gist"]
            if res_data.get("long_summary"):
                data["long_summary"] = res_data["long_summary"]
            tl = str(res_data.get("time_location") or "").strip()
            if tl:
                data["time_location"] = tl[:60]

            # 事实按文本去重：同一事实只留一条，重要度取高的
            import re as _re
            seen = {}
            for cat in memory_manager.FACT_CATEGORIES:
                for f in data["facts"].get(cat, []):
                    key = _re.sub(r"\s+", "", str(f.get("text", "")))
                    if key:
                        seen[key] = f

            for fact in res_data.get("new_facts", []):
                cat = fact.get("category", "重要事件")
                if cat not in memory_manager.FACT_CATEGORIES: cat = "重要事件"
                ftext = str(fact.get("text") or "").strip()
                if not ftext:
                    continue
                try:
                    imp = min(5, max(1, int(fact.get("importance", 3))))
                except (TypeError, ValueError):
                    imp = 3
                key = _re.sub(r"\s+", "", ftext)
                if key in seen:
                    old = seen[key]
                    old["importance"] = min(5, max(int(old.get("importance", 3) or 3), imp))
                    old["age_count"] = 0
                    continue
                data["facts"][cat].append({"text": ftext, "importance": imp, "age_count": 0})
                seen[key] = data["facts"][cat][-1]

            data["recent_logs"] = []      # 成功合并后清空
            data.pop("merge_backlog", None)
            data.pop("merge_fail_count", None)
            data.pop("merge_last_error", None)
            memory_manager.save_memory(int(npc_id), npc_name, data)
            return {"ok": True, "msg": "AI总结成功！"}
            
        except requests.exceptions.ConnectionError:
            return {"ok": False,
                    "msg": f"连接失败（{upstream}）！请确认本地模型(8080)是否开启。"}
        except json.JSONDecodeError:
            return {"ok": False, "msg": "模型返回的不是纯 JSON 格式，解析失败！"}
        except Exception as e:
            return {"ok": False, "msg": f"发生异常: {e}"}

        
    def clear_memory(self, npc_id, npc_name):
        try:
            success = memory_manager.clear_memory(int(npc_id), npc_name)
            return {"ok": success, "msg": "" if success else "记忆文件不存在"}
        except Exception as e:
            return {"ok": False, "msg": f"清空失败: {e}"}

    def open_memory_dir(self):
        """打开记忆目录（打包后就是 exe 同级的 memory\\）"""
        try:
            mem_dir = memory_manager.MEMORY_DIR
            os.makedirs(mem_dir, exist_ok=True)
            return {"ok": open_dir(mem_dir), "path": mem_dir}
        except Exception as e:
            return {"ok": False, "msg": str(e), "path": memory_manager.MEMORY_DIR}

    # --- 卡片 ---
    def get_cards_list(self):
        return list_cards()

    def get_card(self, filename):
        return get_card(filename)

    def save_card(self, filename, card):
        new_name = save_card(filename, card)
        return {"ok": True, "filename": new_name}

    def create_card(self, npc_id, name):
        fn = create_card(npc_id, name)
        if fn is None:
            return {"ok": False, "msg": "同名 ID 的卡片已存在"}
        return {"ok": True, "filename": fn}

    def delete_card(self, filename):
        ok = delete_card(filename)
        return {"ok": ok}

    def get_cards_dir(self):
        return CARDS_DIR

    def get_original_card(self, filename):
        """读取原版备份"""
        card = get_original_card(filename)
        return {"ok": True, "data": card} if card else {"ok": False, "msg": "无原版备份"}

    def get_last_saved_card(self, filename):
        """读取上次保存备份"""
        card = get_last_saved_card(filename)
        return {"ok": True, "data": card} if card else {"ok": False, "msg": "无上次保存备份"}

    # ==================== 试聊：用真实卡片 + 记忆跟本地模型对话 ====================
    # 目的：不启动游戏也能验证「改完名片 / 记忆之后，这个 NPC 说话像不像样」。
    # 只连本地节点（Mac 上的 Ollama）—— 试聊本来就是为了挑本地模型；
    # 云端需要游戏里填的 API Key，编辑器这边没有。

    def _load_card_sections(self, filename):
        """把卡片 md 按 '## 段名' 拆成字典"""
        path = os.path.join(CARDS_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        sections, cur, buf = {}, None, []
        for line in text.splitlines():
            m = re.match(r"^##\s+(.+)$", line)
            if m:
                if cur:
                    sections[cur] = "\n".join(buf).strip()
                cur, buf = m.group(1).strip(), []
            else:
                buf.append(line)
        if cur:
            sections[cur] = "\n".join(buf).strip()
        return sections

    def chat_trial_context(self, filename):
        """返回试聊要用的 system 片段，让前端能看清注入了什么"""
        try:
            secs = self._load_card_sections(filename)
        except Exception as e:
            return {"ok": False, "msg": "读卡片失败：" + str(e)}

        def pick(prefix):
            for k, v in secs.items():
                if k.startswith(prefix):
                    return v
            return ""

        name_m = re.match(r"^\d+-(.+)\.md$", filename)
        npc_name = name_m.group(1) if name_m else filename

        preset = pick("预设资料")
        origin = pick("原游戏背景")
        card = pick("用户名片")

        base = "## 角色设定\n你是 " + npc_name + "。\n"
        if preset:
            base += "\n" + preset + "\n"
        if origin:
            base += "\n" + origin + "\n"

        # 把 ai_config.json 里的注入语也带上。游戏里每轮都会注入它，
        # 试聊不带的话效果会差一截 —— 回复变短、不写旁白、口吻松散，
        # 容易让人误判成「这个模型不行」。
        inject = ""
        try:
            ai_path = os.path.join(PROJECT_DIR, "ai_config.json")
            with open(ai_path, "r", encoding="utf-8-sig") as f:
                inject = (json.load(f).get("inject") or "").strip()
        except Exception:
            pass
        if inject:
            base += "\n## 通用要求\n" + inject + "\n"

        mem_text = ""
        try:
            id_m = re.match(r"^(\d+)-", filename)
            if id_m:
                data = memory_manager.load_memory(int(id_m.group(1)), npc_name)
                gist = (data.get("early_gist") or "").strip()
                summ = (data.get("long_summary") or "").strip()
                mem_text = "\n".join(x for x in (gist, summ) if x)
        except Exception:
            pass

        return {
            "ok": True,
            "npc_name": npc_name,
            "base": base,
            "user_card": card,
            "memory": mem_text,
            "base_chars": len(base),
            "card_chars": len(card),
            "memory_chars": len(mem_text),
            "has_card": bool(card.strip()),
            "has_memory": bool(mem_text.strip()),
        }

    # ==================== 模型源管理 ====================
    # 三类来源：本机 / 局域网节点 / 云端 API。
    # 本质上都是 OpenAI 兼容端点，差别只在地址和是否需要密钥。
    # 密钥存 user_config.json（已 gitignore、打包不带用户数据），
    # 且**回传前端时只给后四位** —— 后端不回传明文，
    # 即使截图或前端代码泄露，也拿不到完整 key。

    def _user_cfg_path(self):
        return os.path.join(PROJECT_DIR, "user_config.json")

    def _load_user_config(self):
        path = self._user_cfg_path()
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_user_config(self, data):
        path = self._user_cfg_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @staticmethod
    def _mask_key(k):
        k = (k or "").strip()
        if not k:
            return ""
        if len(k) <= 10:
            return "•" * len(k)
        return k[:5] + "•" * 6 + k[-4:]

    def model_sources_get(self):
        """读模型源（密钥脱敏后返回）"""
        ms = (self._load_user_config().get("model_sources") or {})
        lan = []
        for n in (ms.get("lan_nodes") or []):
            lan.append({"id": n.get("id") or "",
                        "name": n.get("name") or "未命名",
                        "base": n.get("base") or ""})

        # 没配过节点时，用 core_proxy_config.json 的 local_upstream_base_url 兜底。
        # 那个地址在接本地模型时早就填过了（用户填的是 Mac 的 11434），
        # 没道理让他在这里再填一遍 —— 之前漏了这段，所以下拉是空的。
        if not lan:
            try:
                conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
                with open(conf_path, "r", encoding="utf-8") as f:
                    conf = json.load(f)
                lb = (conf.get("local_upstream_base_url") or "").strip().rstrip("/")
                if lb:
                    host = lb.replace("http://", "").replace("https://", "").rstrip("/")
                    lan.append({"id": "default", "name": host, "base": lb})
            except Exception:
                pass

        api = []
        for a in (ms.get("api_services") or []):
            k = a.get("key") or ""
            api.append({"id": a.get("id") or "",
                        "name": a.get("name") or "未命名",
                        "base": a.get("base") or "",
                        "model": a.get("model") or "",
                        "key_masked": self._mask_key(k),
                        "has_key": bool(k)})
        return {
            "ok": True,
            "local_base": ms.get("local_base") or "http://127.0.0.1:11434",
            "lan_nodes": lan,
            "api_services": api,
        }

    def model_sources_save(self, payload):
        """保存模型源。

        密钥处理：前端拿不到明文，所以提交上来 key 为空时**沿用旧值**；
        要清空就显式传 "__clear__"。这样用户改个地址不会被顺手清掉密钥。
        """
        try:
            cfg = self._load_user_config()
            old = cfg.get("model_sources") or {}
            old_keys = {a.get("id"): (a.get("key") or "")
                        for a in (old.get("api_services") or [])}

            ms = payload.get("model_sources") or {}
            new_api = []
            for a in (ms.get("api_services") or []):
                aid = a.get("id")
                k = a.get("key")
                if k == "__clear__":
                    k = ""
                elif not k:
                    k = old_keys.get(aid, "")
                new_api.append({"id": aid or "",
                                "name": a.get("name") or "",
                                "base": a.get("base") or "",
                                "model": a.get("model") or "",
                                "key": k or ""})

            cfg["model_sources"] = {
                "local_base": ms.get("local_base") or "http://127.0.0.1:11434",
                "lan_nodes": [{"id": n.get("id") or "",
                               "name": n.get("name") or "",
                               "base": n.get("base") or ""}
                              for n in (ms.get("lan_nodes") or [])],
                "api_services": new_api,
            }
            self._save_user_config(cfg)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def trial_source_get(self):
        """试聊用的连接配置：一个地址 + 可选密钥。

        比「三段切换」简单也更通用 —— 云端、局域网、本机都是填地址，
        用户想连哪台机器就填哪台。没配过时用 core_proxy 的本地地址预填。
        """
        cfg = self._load_user_config()
        ts = cfg.get("trial_source") or {}
        base = (ts.get("base") or "").strip()
        default_base = ""
        try:
            conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
            with open(conf_path, "r", encoding="utf-8") as f:
                default_base = (json.load(f).get("local_upstream_base_url") or "").strip().rstrip("/")
        except Exception:
            pass
        if not base:
            base = default_base
        key = ts.get("key") or ""
        return {"ok": True,
                "base": base,
                "default_base": default_base,
                "model": ts.get("model") or "",
                "has_key": bool(key),
                "key_masked": self._mask_key(key)}

    def trial_source_save(self, payload):
        """保存试聊连接。key 传空 = 沿用旧值，传 __clear__ 才清空。"""
        try:
            cfg = self._load_user_config()
            old = cfg.get("trial_source") or {}
            k = payload.get("key")
            if k == "__clear__":
                k = ""
            elif not k:
                k = old.get("key") or ""
            cfg["trial_source"] = {"base": (payload.get("base") or "").strip(),
                                   "model": payload.get("model") or "",
                                   "key": k}
            self._save_user_config(cfg)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def models_scan(self):
        """扫描编辑器目录下的 models/，列出 GGUF 并标记是否已导入本机 Ollama"""
        d = os.path.join(PROJECT_DIR, "models")
        if not os.path.isdir(d):
            try:
                os.makedirs(d)
            except Exception:
                pass

        files = []
        try:
            for fn in sorted(os.listdir(d)):
                if not fn.lower().endswith(".gguf"):
                    continue
                p = os.path.join(d, fn)
                try:
                    sz = os.path.getsize(p)
                except Exception:
                    sz = 0
                files.append({
                    "file": fn,
                    "size": sz,
                    "model_name": re.sub(r"\.gguf$", "", fn, flags=re.I),
                })
        except Exception as e:
            return {"ok": False, "msg": str(e)}

        # 拿本机 Ollama 的模型名做模糊比对（同一个模型文件名和模型名往往不同）
        imported, has_local = set(), False
        try:
            import urllib.request
            base = (self.model_sources_get().get("local_base") or "").rstrip("/")
            with urllib.request.urlopen(base + "/api/tags", timeout=4) as r:
                d2 = json.loads(r.read().decode())
            has_local = True
            for m in (d2.get("models") or []):
                imported.add((m.get("name") or "").split(":")[0].lower())
        except Exception:
            pass

        for f in files:
            k = f["model_name"].lower()
            f["imported"] = bool(imported) and any(k in x or x in k for x in imported)

        return {"ok": True, "dir": d, "local_available": has_local, "files": files}

    def models_import(self, filename, model_name=None):
        """把 models/ 里的一个 GGUF 导入本机 Ollama（走 ollama create）"""
        src = os.path.join(PROJECT_DIR, "models", os.path.basename(filename))
        if not os.path.isfile(src):
            return {"ok": False, "msg": "文件不存在"}
        name = (model_name or re.sub(r"\.gguf$", "", os.path.basename(src), flags=re.I)).strip()
        if not re.match(r"^[A-Za-z0-9._-]+$", name):
            return {"ok": False, "msg": "模型名只能用字母、数字和 . _ -"}

        import subprocess
        import shutil as _sh
        exe = _sh.which("ollama")
        if not exe:
            return {"ok": False,
                    "msg": "本机没找到 ollama 命令。模型在别的机器上跑的话，"
                           "把文件拷到那台机器再导入，或者直接用那边的模型。"}

        mf = os.path.join(PROJECT_DIR, "models", "_import.Modelfile")
        try:
            with open(mf, "w", encoding="utf-8") as f:
                f.write("FROM " + src.replace(os.sep, "/") + "\n")
                f.write("PARAMETER num_ctx 16384\n")
                f.write("PARAMETER temperature 0.8\n")
            r = subprocess.run([exe, "create", name, "-f", mf],
                               capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                return {"ok": False, "msg": (r.stderr or r.stdout or "导入失败")[:400]}
            return {"ok": True, "model": name}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
        finally:
            try:
                os.remove(mf)
            except Exception:
                pass

    def chat_trial_fragments(self):
        """列出游戏 prompt 的可选段落（试聊时用来模拟真实组合）"""
        try:
            path = os.path.join(PROJECT_DIR, "prompt_fragments.json")
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            stats = d.get("stats") or {}
            samples = d.get("section_samples") or {}
            total = d.get("total_samples") or 0
            out = []
            for title, s in stats.items():
                text = samples.get(title) or ""
                out.append({
                    "title": title,
                    "pct": round((s.get("appear") or 0) * 100 / total) if total else 0,
                    "chars": len(text),
                    "text": text,
                })
            out.sort(key=lambda x: -x["pct"])
            return {"ok": True, "fragments": out, "total_samples": total}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def chat_trial_models(self, base=None, key=None):
        """列出某个端点上的模型。

        不传 base 时沿用 core_proxy_config 里的本地地址（兼容旧调用）。
        先试 Ollama 原生的 /api/tags（能拿到体积），失败再退回通用的 /v1/models，
        这样云端 API 也能列模型。
        """
        try:
            if not base:
                conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
                with open(conf_path, "r", encoding="utf-8") as f:
                    conf = json.load(f)
                base = (conf.get("local_upstream_base_url") or "").strip()
            base = (base or "").strip().rstrip("/")
            if not base:
                return {"ok": False, "msg": "没有可用的地址"}

            import urllib.request
            headers = {"User-Agent": "wa-editor"}
            if key:
                headers["Authorization"] = "Bearer " + key

            try:
                req = urllib.request.Request(base + "/api/tags", headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    d = json.loads(r.read().decode())
                models = [{"name": m.get("name") or "", "size": m.get("size") or 0}
                          for m in (d.get("models") or [])]
                if models:
                    models.sort(key=lambda x: x["name"])
                    return {"ok": True, "base": base, "models": models}
            except Exception:
                pass

            req = urllib.request.Request(base + "/v1/models", headers=headers)
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.loads(r.read().decode())
            models = [{"name": m.get("id") or "", "size": 0}
                      for m in (d.get("data") or [])]
            models = [m for m in models if m["name"]]
            models.sort(key=lambda x: x["name"])
            if not models:
                return {"ok": False, "msg": "这个地址没返回模型列表"}
            return {"ok": True, "base": base, "models": models}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def chat_trial_send(self, payload):
        """发一轮对话到本地节点"""
        try:
            import urllib.request
            base = (payload.get("base") or "").strip().rstrip("/")
            model = payload.get("model")
            messages = payload.get("messages") or []
            if not base or not model:
                return {"ok": False, "msg": "缺少 base 或 model"}
            body = {
                "model": model,
                "messages": messages,
                "temperature": float(payload.get("temperature", 0.85)),
                "max_tokens": int(payload.get("max_tokens", 900)),
                # 不关思考的话，Qwen3.5 会先吐一大段内心独白才开口
                "reasoning_effort": "none",
            }
            headers = {"Content-Type": "application/json"}
            # 密钥不出后端：前端不传时依次从 trial_source、api_services 里找，
            # 免得明文在 JS 里流转（控制台、截图都能看到）
            api_key = str(payload.get("key") or "")
            cfg = self._load_user_config()
            if not api_key:
                api_key = (cfg.get("trial_source") or {}).get("key") or ""
            api_id = payload.get("api_id")
            if not api_key and api_id:
                for a in ((cfg.get("model_sources") or {}).get("api_services") or []):
                    if a.get("id") == api_id:
                        api_key = a.get("key") or ""
                        break
            if api_key:
                headers["Authorization"] = "Bearer " + api_key
            req = urllib.request.Request(
                base + "/v1/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
            )
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode())
            reply = d["choices"][0]["message"].get("content") or ""
            return {"ok": True, "reply": reply, "elapsed": round(time.time() - t0, 1)}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    # --- AI 注入配置 ---
    def get_ai_config(self):
        if not os.path.isfile(AI_CONFIG_PATH):
            return {"ok": False, "msg": "ai_config.json 不存在"}
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"ok": True, "data": data}
        except Exception as e:
            return {"ok": False, "msg": f"读取失败: {e}"}

    def save_ai_config(self, data):
        try:
            if os.path.isfile(AI_CONFIG_PATH):
                shutil.copy2(AI_CONFIG_PATH, AI_CONFIG_PATH + ".bak")
            with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": f"保存失败: {e}"}

    def ai_fill_user_card(self, filename, user_hint="", use_samples=True,
                          use_memory=True, use_local=True):
        r"""用 AI 给某张卡的「用户名片」生成内容（只生成，不写盘）。

        资料来源（都可关）：
          1. 卡片自带：预设资料 + 原游戏背景
          2. prompt_samples\ 里该 NPC 的最新游戏 prompt（场景 / 关系 / 当前状态）
          3. memory\ 里该 NPC 的长期记忆（早期梗概 / 最近剧情 / 事实）

        返回 {ok, markdown, sources:[...], chars} 或 {ok:False, msg}
        """
        import json
        import os
        import re

        card = self.get_card(filename)
        if not card:
            return {"ok": False, "msg": "读不到卡片：" + str(filename)}

        name = card.get("name") or ""
        preset = card.get("preset") or {}
        background = (card.get("background") or "").strip()
        existing = (card.get("user_card") or "").strip()
        sources = ["卡片预设资料 + 原游戏背景"]

        # --- 资料 2：最新的游戏 prompt ---
        game_ctx = ""
        if use_samples:
            try:
                smp = find_latest_sample(name)
            except Exception:
                smp = None
            if smp and isinstance(smp.get("content"), str):
                sys_txt = smp["content"]
                keep = []
                # 「说话习惯」= 游戏官方的口吻设定（配置表 speakStyle）。
                # 拿它当依据，生成的口吻和示例台词才不会跟官方设定打架。
                for sec in ("当前场景", "本轮会话状态", "角色设定", "任务", "说话习惯"):
                    m = re.search(r'^##[ \t]*' + sec + r'[^\n]*\n(.*?)(?=^##[ \t]|\Z)',
                                  sys_txt, re.S | re.M)
                    if m:
                        body = m.group(1).strip()
                        if body:
                            keep.append(f"【{sec}】\n{body[:600]}")
                if keep:
                    game_ctx = "\n\n".join(keep)[:1800]
                    sources.append(f"游戏 prompt 样本（{smp.get('filename', '')}）")

        # --- 资料 3：长期记忆 ---
        # 注意：剧情摘要和事实里混着【玩家说过的话】，玩家会自称各种身份、
        # 临时编剧情，拿它当设定依据会把 NPC 的官方人设带偏。
        # 所以默认只取中性的「时间地点」；剧情向内容要用户显式勾选。
        mem_ctx = ""
        if use_memory:
            try:
                npc_id = None
                m = re.search(r'(\d+)', os.path.splitext(str(filename))[0])
                if m:
                    npc_id = int(m.group(1))
                if npc_id:
                    data = memory_manager.load_memory(npc_id, name)
                    parts = []
                    if data.get("time_location"):
                        parts.append("时间地点：" + str(data["time_location"]))
                    if use_memory == "plot":
                        if data.get("early_gist"):
                            parts.append("早期梗概：" + str(data["early_gist"])[:300])
                        if data.get("long_summary"):
                            parts.append("最近剧情：" + str(data["long_summary"])[:400])
                        for cat, arr in (data.get("facts") or {}).items():
                            for f in arr[:4]:
                                txt = (f or {}).get("text") if isinstance(f, dict) else f
                                if txt:
                                    parts.append(f"事实[{cat}]：{txt}")
                    if parts:
                        mem_ctx = "\n".join(parts)[:900]
                        sources.append("长期记忆（仅时间地点）" if use_memory != "plot"
                                       else "长期记忆（含剧情摘要，受玩家发言影响）")
            except Exception as e:
                print(f"[AI补全] 读记忆失败: {e}", flush=True)

        # --- 用哪个模型生成 ---
        # use_local=True  → 本地（ai_config 的 memory_merge_url，一般填 127.0.0.1:8080）
        # use_local=False → 跟随 core_proxy 的上游（第三方 API）
        # 不是每台电脑都部署了本地模型，所以两种都要能选。
        upstream, fallback_key, which = "", "", ""
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                acfg = json.load(f)
        except Exception:
            acfg = {}

        if use_local:
            upstream = (acfg.get("memory_merge_url") or "").strip()
            which = "本地模型"
            if not upstream:
                upstream = "http://127.0.0.1:8080/v1/chat/completions"
        else:
            conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
            base = "http://127.0.0.1:8080/"
            try:
                with open(conf_path, "r", encoding="utf-8") as f:
                    ccfg = json.load(f)
                base = (ccfg.get("upstream_base_url") or base).rstrip("/")
                fallback_key = ccfg.get("api_key") or ""
            except Exception:
                pass
            upstream = base + "/v1/chat/completions"
            which = "跟随上游（API）"

        hint = (user_hint or "").strip()
        prompt = (
            "你在给一个游戏 NPC 写「角色演绎要求」。下面给出这个角色的资料，"
            "请据此写出她怎么说话、怎么反应。\n\n"
            "【硬性规则】\n"
            "1. 资料优先级：卡片预设与原游戏背景 > 游戏里的角色设定与当前场景 > 长期记忆。\n"
            "   冲突时以上面优先级为准，不要让低优先级资料改写官方设定。\n"
            "2. 资料里凡是【玩家说过的话、玩家自称的身份、玩家临时提起的剧情】，"
            "一律不当成设定依据——玩家可能只是角色扮演或试探。\n"
            "3. 【游戏里的当前状态】里如果有「说话习惯」，那是游戏官方写的口吻设定，"
            "口吻要跟它一致（基调、句长、自称、称谓都不能反着写）；"
            "但不要把这段描述原样抄进名片——游戏每轮已经自己注入了，抄了是重复。\n"
            "4. 只依据给定资料推断，不要编造资料里没有的身世、门派、剧情。\n"
            "5. 不要写露骨内容，只写语气、习惯、反应方式。\n"
            "6. 示例台词只示范语气，不要复述资料里的剧情原文。\n"
            "7. 输出严格 JSON，不要 markdown 代码块：\n"
            '{"tone":"整体语气基调","rhythm":"句长节奏","self_ref":"她怎么自称",'
            '"address":"她怎么称呼玩家","catch":["口头禅1","口头禅2","口头禅3"],'
            '"habits":["小动作1","小动作2","小动作3"],'
            '"rules":["当…→则…","当…→则…","当…→则…","当…→则…","当…→则…"],'
            '"lines":["示例台词1","示例台词2","示例台词3"]}\n\n'
            "【参考范例：冷静强硬型的宗主怎么写】\n"
            '{"tone":"短、硬、命令式，很少用问号","rhythm":"极短句，一句一断，很少超过 20 字",'
            '"self_ref":"对外「本座」，独处或私下「我」",'
            '"address":"一律「你」，极少叫名字；正式场合加「阁下」",'
            '"catch":["不必","就这样","说完了"],'
            '"habits":["说话时不看人，视线落在别处","动怒前先把袖子拢好","抬手止住对方话头"],'
            '"rules":["当被质疑 → 不解释，直接给结论或反问",'
            '"当对方示弱 → 不安慰，改用行动（递东西、挡在前面）",'
            '"当有外人在场 → 话更少，只下指令",'
            '"当被说中心事 → 沉默一息，然后换话题",'
            '"当受伤 → 只说「无碍」，不让人靠近"],'
            '"lines":["「不必。你且站着。」","「要走，现在就走。」","「……知道了。」（把茶推过去，别过头）"]}\n\n'
            "【要描写的角色】\n"
            f"姓名：{name}\n性别：{preset.get('性别', '')}\n年龄：{preset.get('年龄', '')}\n"
            f"个性：{preset.get('个性', '')}\n兴趣：{preset.get('兴趣', '')}\n"
            f"\n【原游戏背景】\n{background or '（无）'}\n"
        )
        if game_ctx:
            prompt += f"\n【游戏里的当前状态】\n{game_ctx}\n"
        if mem_ctx:
            prompt += f"\n【她与玩家已经经历过的事】\n{mem_ctx}\n"
        if hint:
            prompt += f"\n【玩家补充的人设原稿（最高优先级，按它来写）】\n{hint}\n"
        if existing:
            prompt += f"\n【现有的名片内容（可参考，不满意就重写）】\n{existing[:600]}\n"

        headers = {"Content-Type": "application/json"}
        if fallback_key:
            headers["Authorization"] = "Bearer " + fallback_key
        body = {
            "model": "local-model",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 4000,
            "thinking": {"type": "disabled"},
        }

        try:
            import requests
            r = requests.post(upstream, json=body, headers=headers, timeout=180)
        except Exception as e:
            return {"ok": False, "msg": f"请求失败（{upstream}）: {e}"}
        if r.status_code != 200:
            return {"ok": False,
                    "msg": f"模型报错: HTTP {r.status_code}（{upstream}）"}

        try:
            raw = r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return {"ok": False, "msg": f"响应格式异常: {e}"}

        a, b = raw.find("{"), raw.rfind("}")
        if a < 0 or b <= a:
            return {"ok": False, "msg": "模型没有返回 JSON（可能被拒答），前 60 字：" + raw[:60]}

        d = None
        try:
            d = json.loads(raw[a:b + 1])
        except Exception:
            # 值里混了 ASCII 双引号，整体解析不了 → 按字段兜底
            d = _loose_extract_fields(raw[a:b + 1]) or None
        if not d:
            return {"ok": False,
                    "msg": "JSON 解析失败（模型可能在值里用了英文引号），前 80 字：" + raw[:80]}

        lines = ["### 口吻",
                 f"- 基调：{d.get('tone', '')}",
                 f"- 句长：{d.get('rhythm', '')}",
                 f"- 自称：{d.get('self_ref', '')}",
                 f"- 称谓：{d.get('address', '')}",
                 f"- 口头禅：{' / '.join(d.get('catch') or [])}",
                 f"- 小动作：{'；'.join(d.get('habits') or [])}",
                 "", "### 行为逻辑"]
        lines += [f'- {x}' for x in (d.get('rules') or [])]
        lines += ["", "### 绝不说",
                 '- 不用「作为一个…」这类自我指涉',
                 '- 不复述玩家刚说过的话',
                 '- 不总结对方的情绪（如「我理解你的感受」）',
                 '- 不排比抒情，不用感叹号堆情绪',
                 "", "### 示例台词"]
        lines += [f'{i}. {x}' for i, x in enumerate(d.get('lines') or [], 1)]
        markdown = "\n".join(lines)

        return {"ok": True, "markdown": markdown, "sources": sources,
                "chars": len(markdown), "name": name,
                "model": which, "url": upstream}

    def optimize_fragment(self, title, text):
        """让模型精简一段固定的 system 段落，只给建议、不自动改配置。

        这些段落每轮都会原样发给模型（通用要求 1200+ 字、赠物通则 500+ 字），
        压缩掉冗余表述能直接省 token。但里面有 JSON 结构、枚举、硬约束，
        所以提示词里明确禁止动它们，返回结果由用户确认后才变成替换规则。
        """
        import json
        import os
        import requests

        text = (text or "").strip()
        if len(text) < 80:
            return {"ok": False, "msg": "这段太短，没有压缩价值"}

        # 上游：优先用记忆总结地址（可以把压缩也放本地），否则用 core_proxy 的上游
        upstream = ""
        fallback_key = ""
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                acfg = json.load(f)
            upstream = (acfg.get("memory_merge_url") or "").strip()
        except Exception:
            pass
        if not upstream:
            conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
            try:
                with open(conf_path, "r", encoding="utf-8") as f:
                    ccfg = json.load(f)
                base = (ccfg.get("upstream_base_url") or "http://127.0.0.1:8080/").rstrip("/")
                upstream = base + "/v1/chat/completions"
                fallback_key = ccfg.get("api_key") or ""
            except Exception:
                upstream = "http://127.0.0.1:8080/v1/chat/completions"

        prompt = (
            "你在做【提示词压缩】。下面这段是游戏每次对话都会原样发给模型的固定 system 段落，\n"
            "内容长期不变，但占了不少 token。请在**不改变任何约束含义**的前提下把它压缩。\n\n"
            "硬性要求（违反任何一条都算失败）：\n"
            "1. 段落里如果有 JSON 结构、字段名、枚举值、示例，必须原样保留，一个字都不能改。\n"
            "2. 不能弱化任何「必须 / 不要 / 禁止 / 只能」这类约束，只能合并同义句、"
            "删掉重复表述和解释性赘述。\n"
            "3. 不能新增任何规则，也不要加入你自己的风格偏好。\n"
            "4. 保持原有的条目顺序与结构（列表还是列表）。\n\n"
            "输出严格 JSON（不要 markdown 代码块，不要多余文字）：\n"
            '{"compressed": "压缩后的全文", "dropped": ["删掉/合并了哪些内容，逐条简述"], '
            '"risk": "low|mid|high", "note": "一句话说明压缩思路"}\n\n'
            "【段落标题】" + str(title or "") + "\n"
            "【原文】\n" + text
        )

        headers = {"Content-Type": "application/json"}
        if fallback_key:
            headers["Authorization"] = "Bearer " + fallback_key
        body = {
            "model": "local-model",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 4000,
            "thinking": {"type": "disabled"},
        }

        try:
            r = requests.post(upstream, json=body, headers=headers, timeout=180)
        except Exception as e:
            return {"ok": False, "msg": f"请求失败（{upstream}）: {e}"}

        if r.status_code != 200:
            return {"ok": False, "msg": f"上游返回 HTTP {r.status_code}: {r.text[:160]}"}

        try:
            raw = r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return {"ok": False, "msg": f"响应格式异常: {e}"}

        a, b = raw.find("{"), raw.rfind("}")
        if a < 0 or b <= a:
            return {"ok": False, "msg": "模型没有返回 JSON（可能被拒答），原文前 40 字: " + raw[:40]}
        try:
            data = json.loads(raw[a:b + 1])
        except Exception as e:
            return {"ok": False, "msg": f"JSON 解析失败: {e}"}

        compressed = str(data.get("compressed") or "").strip()
        if not compressed:
            return {"ok": False, "msg": "模型返回的压缩结果为空"}

        orig_len, new_len = len(text), len(compressed)
        return {
            "ok": True,
            "title": title,
            "original": text,
            "compressed": compressed,
            "original_chars": orig_len,
            "compressed_chars": new_len,
            "saved_chars": orig_len - new_len,
            "ratio": round(new_len * 100.0 / max(1, orig_len), 1),
            "dropped": data.get("dropped") or [],
            "risk": str(data.get("risk") or "mid"),
            "note": str(data.get("note") or ""),
        }

    def save_memory_settings(self, merge_url, merge_every, merge_max_tokens,
                             memory_slot=None, past_events_max_chars=None):
        """只改记忆相关的几个键，其余配置原样保留。"""
        try:
            data = {}
            if os.path.isfile(AI_CONFIG_PATH):
                with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)

            data["memory_merge_url"] = str(merge_url or "").strip()
            try:
                data["memory_merge_every"] = max(4, int(merge_every or 12))
            except (TypeError, ValueError):
                return {"ok": False, "msg": "合并频率要填数字（≥4）"}
            try:
                data["memory_merge_max_tokens"] = max(500, int(merge_max_tokens or 4000))
            except (TypeError, ValueError):
                return {"ok": False, "msg": "输出上限要填数字（≥500）"}

            if memory_slot is not None:
                slot = str(memory_slot or "").strip()
                if slot not in ("past_events", "append"):
                    return {"ok": False, "msg": f"未知的记忆落位: {slot}"}
                data["memory_slot"] = slot

            if past_events_max_chars is not None:
                try:
                    data["past_events_max_chars"] = max(0, int(past_events_max_chars))
                except (TypeError, ValueError):
                    return {"ok": False, "msg": "过往要事上限要填数字（0 = 不压缩）"}

            if os.path.isfile(AI_CONFIG_PATH):
                shutil.copy2(AI_CONFIG_PATH, AI_CONFIG_PATH + ".bak")
            with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"ok": True, "data": {
                "memory_merge_url": data["memory_merge_url"],
                "memory_merge_every": data["memory_merge_every"],
                "memory_merge_max_tokens": data["memory_merge_max_tokens"],
                "memory_slot": data.get("memory_slot", "past_events"),
                "past_events_max_chars": data.get("past_events_max_chars", 800),
            }}
        except Exception as e:
            return {"ok": False, "msg": f"保存失败: {e}"}

    def tidy_memories(self):
        """本地整理全部记忆：补字段、压摘要、修重要度、去重复。

        不调 AI、不联网，写盘前照常备份到 memory/_backup。
        """
        try:
            mem_dir = memory_manager.MEMORY_DIR
            if not os.path.isdir(mem_dir):
                return {"ok": True, "files": 0, "changed": 0, "detail": {}}

            files = sorted([f for f in os.listdir(mem_dir) if f.endswith(".json")])
            total = {}
            changed = 0
            for fname in files:
                path = os.path.join(mem_dir, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue

                npc_id = data.get("npc_id")
                npc_name = data.get("npc_name")
                try:
                    npc_id = int(npc_id)
                except (TypeError, ValueError):
                    continue
                if not npc_name:
                    continue

                new_data, rep = memory_manager.tidy_memory(data)
                if any(rep.values()):
                    memory_manager.save_memory(npc_id, npc_name, new_data)
                    changed += 1
                    for k, v in rep.items():
                        if v:
                            total[k] = total.get(k, 0) + v

            return {"ok": True, "files": len(files), "changed": changed, "detail": total}
        except Exception as e:
            return {"ok": False, "msg": f"整理失败: {e}"}

    def apply_ai_config(self):
        """保存后确认 core_proxy 真的读到了新配置。

        ai_config.json 是按 mtime 热加载的，正常情况下存盘即生效；
        这里主动问一次 /_status，把"到底生效没有"变成可验证的结果，
        而不是让用户去猜要不要重启。
        """
        import json
        import os
        import urllib.request

        try:
            mtime = os.path.getmtime(AI_CONFIG_PATH)
            with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                conf = json.load(f)
            want_inject = len((conf.get("inject") or "").strip())
            want_rules = len([r for r in (conf.get("rules") or []) if (r.get("old") or "").strip()])
        except Exception as e:
            return {"ok": False, "msg": f"读取配置失败: {e}"}

        # core_proxy 端口从 core_proxy_config.json 取，读不到就用默认 8789
        port = 8789
        try:
            with open(os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json"), "r",
                      encoding="utf-8") as f:
                port = int(json.load(f).get("listen_port") or 8789)
        except Exception:
            pass

        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_status", timeout=2) as r:
                st = json.loads(r.read().decode("utf-8"))
        except Exception:
            return {"ok": True, "applied": False, "running": False,
                    "msg": "core_proxy 没在运行，配置已存盘；启动服务后自动生效"}

        got_inject = st.get("inject_chars")
        got_rules = st.get("rules")
        if got_inject == want_inject and got_rules == want_rules:
            return {"ok": True, "applied": True, "running": True,
                    "rules": got_rules, "inject_chars": got_inject,
                    "msg": f"已生效（{got_rules} 条规则 / 注入 {got_inject} 字）"}

        return {"ok": True, "applied": False, "running": True, "needs_restart": True,
                "want": {"rules": want_rules, "inject_chars": want_inject},
                "got": {"rules": got_rules, "inject_chars": got_inject},
                "msg": f"服务里还是旧配置（{got_rules} 条规则），需要重启"}

    def _proxy_port(self):
        """core_proxy 的监听端口，以 proxy/core_proxy_config.json 为准。"""
        port = 8789
        try:
            with open(os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json"), "r",
                      encoding="utf-8") as f:
                port = int(json.load(f).get("listen_port") or 8789)
        except Exception:
            pass
        return port

    def get_proxy_health(self):
        """读 core_proxy 的结构自检结果（告警、计数、最近一次结构画像）。

        core_proxy 每轮转发前都会比对"段落集合 / system 条数"这些结构特征，
        发现游戏改了 prompt 组装方式就记告警。这里只负责取回来给页面显示。
        """
        port = self._proxy_port()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_status", timeout=2) as r:
                st = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "running": False,
                    "msg": f"core_proxy 没在运行（{port} 端口无响应）"}
        return {"ok": True, "running": True, "port": port,
                "health": st.get("health") or {},
                "usage": st.get("usage") or {},
                "npc": st.get("npc"),
                "rules": st.get("rules"),
                "inject_chars": st.get("inject_chars")}

    def list_presets(self):
        """列出方案：内置两套 + 用户自存的。不返回 inject/rules 内容（可能很长）。"""
        data = _read_ai_conf()
        presets = data.get("presets") or {}
        brief = {}
        for name, snap in presets.items():
            if isinstance(snap, dict):
                brief[name] = {
                    "inject_chars": len(str(snap.get("inject") or "")),
                    "rules": len(snap.get("rules") or []),
                    "memory_budget": snap.get("memory_budget"),
                }
        return {"ok": True,
                "builtin": list(BUILTIN_PRESETS.keys()),
                "builtin_detail": BUILTIN_PRESETS,
                "presets": brief,
                "active": data.get("active_preset") or ""}

    def save_preset(self, name):
        """把当前配置里方案相关的键存成一份命名快照。"""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "msg": "方案名不能为空"}
        if name in BUILTIN_PRESETS:
            return {"ok": False, "msg": "不能覆盖内置方案，换个名字"}
        try:
            data = _read_ai_conf()
            snap = {k: data[k] for k in PRESET_KEYS if k in data}
            if not snap:
                return {"ok": False, "msg": "当前没有可保存的配置项"}
            data.setdefault("presets", {})[name] = snap
            data["active_preset"] = name
            _write_ai_conf(data)
        except Exception as e:
            return {"ok": False, "msg": f"保存失败: {e}"}
        return {"ok": True, "msg": f"已存为方案「{name}」（{len(snap)} 项）"}

    def apply_preset(self, name):
        """应用方案：把方案里的键写回 ai_config.json。其余键一律不动。"""
        try:
            data = _read_ai_conf()
            if name in BUILTIN_PRESETS:
                snap = BUILTIN_PRESETS[name]
            else:
                snap = (data.get("presets") or {}).get(name)
                if not isinstance(snap, dict):
                    return {"ok": False, "msg": f"没有方案「{name}」"}
            data.update(snap)
            data["active_preset"] = name
            _write_ai_conf(data)
        except Exception as e:
            return {"ok": False, "msg": f"应用失败: {e}"}
        return {"ok": True, "msg": f"已应用「{name}」，core_proxy 会自动重新读取"}

    def delete_preset(self, name):
        try:
            data = _read_ai_conf()
            presets = data.get("presets") or {}
            if name in BUILTIN_PRESETS:
                return {"ok": False, "msg": "内置方案不能删除"}
            if name not in presets:
                return {"ok": False, "msg": f"没有方案「{name}」"}
            presets.pop(name)
            data["presets"] = presets
            if data.get("active_preset") == name:
                data["active_preset"] = ""
            _write_ai_conf(data)
        except Exception as e:
            return {"ok": False, "msg": f"删除失败: {e}"}
        return {"ok": True, "msg": f"已删除「{name}」"}

    # ==================== 世界信息 / 用户人设 ====================

    def get_lorebook(self):
        """读世界信息条目。文件不存在就返回空列表（前端可直接新建）。"""
        path = os.path.join(PROJECT_DIR, "lorebook.json")
        entries = []
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("entries"), list):
                entries = d["entries"]
        except Exception:
            entries = []
        budget = 800
        try:
            budget = int(_read_ai_conf().get("lorebook_budget", 800))
        except Exception:
            pass
        return {"ok": True, "entries": entries, "path": path, "budget": budget}

    def save_lorebook(self, entries):
        """整体保存世界信息条目（前端负责增删改，这里只做校验与落盘）。"""
        if not isinstance(entries, list):
            return {"ok": False, "msg": "entries 必须是数组"}
        clean = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            title = str(e.get("title") or "").strip()
            content = str(e.get("content") or "").strip()
            kws = [str(k).strip() for k in (e.get("keywords") or []) if str(k).strip()]
            # 没正文的条目没有意义（草稿），直接跳过；
            # 但「有正文、没关键词」是合法的——那是常驻条目，每轮都注入
            if not content:
                continue
            clean.append({
                "id": str(e.get("id") or "").strip() or str(int(time.time() * 1000)),
                "title": title or "未命名",
                "keywords": kws,
                "content": content,
                "enabled": bool(e.get("enabled", True)),
                "priority": int(e.get("priority") or 50),
                "npcs": [str(x) for x in (e.get("npcs") or [])],
            })
        path = os.path.join(PROJECT_DIR, "lorebook.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"entries": clean}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"ok": False, "msg": f"保存失败: {e}"}
        return {"ok": True, "msg": f"已保存 {len(clean)} 条", "count": len(clean)}

    def get_persona(self):
        data = _read_ai_conf()
        return {"ok": True,
                "persona": data.get("persona") or "",
                "inject": bool(data.get("inject_persona", True))}

    def save_persona(self, text, inject=True):
        """保存玩家设定。core_proxy 按 mtime 热加载，存盘即生效。"""
        try:
            data = _read_ai_conf()
            data["persona"] = str(text or "").strip()
            data["inject_persona"] = bool(inject)
            _write_ai_conf(data)
        except Exception as e:
            return {"ok": False, "msg": f"保存失败: {e}"}
        return {"ok": True, "msg": "已保存，core_proxy 会自动重新读取"}

    def save_price_settings(self, price_in, price_out):
        """保存 token 单价（每百万 token）。填 0 = 不折算花费。

        core_proxy 按 mtime 热加载 ai_config.json，存盘即生效，不用重启。
        """
        try:
            p_in = max(0.0, float(price_in or 0))
            p_out = max(0.0, float(price_out or 0))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "单价要填数字（0 表示不折算）"}

        try:
            data = {}
            if os.path.isfile(AI_CONFIG_PATH):
                with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            data["price_input_per_million"] = p_in
            data["price_output_per_million"] = p_out
            with open(AI_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"ok": False, "msg": f"保存失败: {e}"}

        return {"ok": True, "msg": "已保存，core_proxy 会自动重新读取"}

    def test_rules(self, text):
        """规则测试器：把一段文本当作 system 走一遍预览，返回逐条命中报告。

        走 core_proxy 的 /_preview（dry_run：不调上游、不写记忆、不存样本），
        并关掉名片与记忆注入——只关心"这些规则到底能不能命中"。
        """
        text = (text or "").strip()
        if not text:
            return {"ok": False, "msg": "请先输入或载入一段文本"}

        port = self._proxy_port()
        payload = json.dumps({
            "messages": [
                {"role": "system", "content": text},
                {"role": "user", "content": "测试"},
            ],
            "with_memory": False,
            "with_card": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/_preview", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False, "msg": f"连不上 core_proxy（{port}）: {e}"}

        if not res.get("ok"):
            return {"ok": False, "msg": res.get("msg") or "预览失败"}

        return {"ok": True,
                "rule_report": res.get("rule_report") or [],
                "system": res.get("system") or "",
                "stats": res.get("stats") or {}}

    def restart_core_proxy(self):
        try:
            service_mgr.stop("core_proxy")
            time.sleep(0.5)
            start_res = service_mgr.start(CORE_PROXY)
            if start_res.get("ok"):
                return {"ok": True}
            else:
                return {"ok": False, "msg": start_res.get("msg", "启动失败")}
        except Exception as e:
            return {"ok": False, "msg": f"重启失败: {e}"}
        
    # --- 头像提取 ---
    def pick_assetstudio_cli(self):
        """弹文件选择框，让用户选 AssetStudioModCLI.exe（记上次打开的目录）"""
        try:
            start = _pick_start_dir("cli_dir")
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                directory=start,
                file_types=("所有文件 (*.*)",),
            )
            if result and len(result) > 0:
                picked = result[0]
                _remember_pick("cli_dir", os.path.dirname(picked))
                return {"ok": True, "path": picked}
            return {"ok": False, "msg": "未选择"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def pick_game_dir(self):
        """弹文件夹选择框，让用户选游戏根目录（记上次选的位置）"""
        try:
            start = _pick_start_dir("game_dir_pick")
            result = webview.windows[0].create_file_dialog(
                webview.FOLDER_DIALOG, directory=start)
            if result and len(result) > 0:
                _remember_pick("game_dir_pick", result[0])
                return {"ok": True, "path": result[0]}
            return {"ok": False, "msg": "未选择"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def extract_portraits(self, assetstudio_cli, game_dir, keep_tmp=False):
        """从游戏提取头像"""
        return services.extract_portraits(
            assetstudio_cli=assetstudio_cli,
            game_dir=game_dir,
            project_dir=PROJECT_DIR,
            keep_tmp=keep_tmp,
            log=lambda msg: push_log("extract", msg),
        )

    def extract_npc_cards(self, assetstudio_cli, game_dir, keep_tmp=False):
        """从游戏导出角色卡（不依赖 Mod）"""
        return services.extract_npc_cards(
            assetstudio_cli=assetstudio_cli,
            game_dir=game_dir,
            project_dir=PROJECT_DIR,
            keep_tmp=keep_tmp,
            log=lambda msg: push_log("npc_cards", msg),
        )
    # --- 卡片对比 ---
    def backup_cards(self):
        return services.backup_editor_cards(PROJECT_DIR, log=lambda m: push_log("backup", m))

    def list_card_backups(self):
        return {"ok": True, "backups": services.list_card_backups(PROJECT_DIR)}

    def diff_cards(self, backup_name):
        """对比指定备份 vs 当前 editor_cards"""
        import os as _os
        old_dir = _os.path.join(PROJECT_DIR, "editor_cards_backup", backup_name)
        new_dir = _os.path.join(PROJECT_DIR, "editor_cards")
        return services.diff_editor_cards(old_dir, new_dir, project_dir=PROJECT_DIR,
                                          log=lambda m: push_log("diff", m))
    
    def merge_cards(self, backup_name):
        """把备份的用户名片合并进当前编辑器卡片（新数据保留）"""
        import os as _os
        old_dir = _os.path.join(PROJECT_DIR, "editor_cards_backup", backup_name)
        new_dir = _os.path.join(PROJECT_DIR, "editor_cards")
        return services.merge_cards(
            old_dir=old_dir,
            new_dir=new_dir,
            out_dir=new_dir,
            project_dir=PROJECT_DIR,
            log=lambda m: push_log("merge", m),
        )     

    def get_editor_cards_count(self):
        """返回 editor_cards 目录里的卡片数"""
        cards_dir = os.path.join(PROJECT_DIR, "editor_cards")
        if not os.path.isdir(cards_dir):
            return {"ok": True, "count": 0, "path": cards_dir}
        count = len([f for f in os.listdir(cards_dir) if f.endswith(".md")])
        return {"ok": True, "count": count, "path": cards_dir}

    def open_editor_cards_dir(self):
        cards_dir = os.path.join(PROJECT_DIR, "editor_cards")
        os.makedirs(cards_dir, exist_ok=True)
        return {"ok": open_dir(cards_dir)}    
    def abort_extract(self):
        return services.abort_extract()
    
        # --- 上游地址 ---
    def get_upstream(self):
        """读 core_proxy_config.json 里的 upstream_base_url"""
        path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
        if not os.path.isfile(path):
            return {"ok": False, "msg": "找不到 core_proxy_config.json"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {"ok": True, "url": data.get("upstream_base_url", "")}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def save_upstream(self, url):
        """把新地址写进 core_proxy_config.json"""
        path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
        if not os.path.isfile(path):
            return {"ok": False, "msg": "找不到 core_proxy_config.json"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["upstream_base_url"] = url
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def save_and_restart_upstream(self, url):
        """保存 + 重启 core_proxy"""
        r = self.save_upstream(url)
        if not r.get("ok"):
            return r
        return self.restart_core_proxy()

    def get_upstream_status(self):
        """去问 core_proxy 现在实际用的上游"""
        import urllib.request
        import json as _json
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8789/_status",
                headers={"User-Agent": "editor"}
            )
            # 绕过系统代理
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            with opener.open(req, timeout=3) as r:
                raw = r.read().decode("utf-8")
                data = _json.loads(raw)
            return {"ok": True, "upstream": data.get("upstream", "")}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
        
        # --- 游戏目录 ---
    def check_game_dir(self):
        """检查 user_config.json 里 game_dir 是否有效"""
        path = os.path.join(PROJECT_DIR, "user_config.json")
        if not os.path.isfile(path):
            return {"ok": False, "msg": "user_config.json 不存在"}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            gd = data.get("game_dir", "")
            if not gd:
                return {"ok": False, "msg": "未填写"}
            if not os.path.isdir(gd):
                return {"ok": False, "msg": f"目录不存在：{gd}"}
            if not os.path.isfile(os.path.join(gd, "WorldApart.exe")):
                return {"ok": False, "msg": f"该目录下找不到 WorldApart.exe"}
            return {"ok": True, "game_dir": gd}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def pick_and_save_game_dir(self):
        """弹文件夹选择框，选完写入 user_config.json 并重启 core_proxy"""
        try:
            start = _pick_start_dir("game_dir_pick")
            result = webview.windows[0].create_file_dialog(
                webview.FOLDER_DIALOG, directory=start)
            if not result or len(result) == 0:
                return {"ok": False, "msg": "未选择"}
            gd = result[0]
            _remember_pick("game_dir_pick", gd)
            if not os.path.isfile(os.path.join(gd, "WorldApart.exe")):
                return {"ok": False, "msg": "该目录下找不到 WorldApart.exe，请重新选择"}

            path = os.path.join(PROJECT_DIR, "user_config.json")
            data = {}
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            data["game_dir"] = gd
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 重启 core_proxy 让它重新读配置
            service_mgr.stop("core_proxy")
            time.sleep(0.5)
            service_mgr.start(CORE_PROXY)

            return {"ok": True, "game_dir": gd}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
        
    # --- 提示词预览（试演）---
    def preview_prompt(self, npc_name=None, with_memory=True):
        """用最近一次游戏对话的真实 prompt 跑一遍完整注入。

        不调用上游、不保存样本、不写记忆 —— 可以反复试。
        """
        import urllib.request
        import json as _json

        sample = find_latest_sample(npc_name or "")
        if sample is None:
            tip = f"没有找到 {npc_name} 的样本" if npc_name else "没有找到任何样本"
            return {"ok": False, "msg": tip + "，先进游戏对话一次再试"}
        if sample.get("error"):
            return {"ok": False, "msg": f"样本读取失败: {sample['error']}"}

        data = sample.get("content") or {}
        messages = data.get("messages") or []
        if not messages:
            return {"ok": False, "msg": "样本里没有 messages"}

        port = 8789
        try:
            with open(os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json"),
                      "r", encoding="utf-8") as f:
                port = int(_json.load(f).get("listen_port", 8789))
        except Exception:
            pass

        body = _json.dumps({"messages": messages, "with_memory": bool(with_memory)},
                           ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/_preview",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as r:
                out = _json.loads(r.read().decode("utf-8"))
        except Exception as e:
            return {"ok": False,
                    "msg": f"core_proxy 没响应（{e}）。先到「服务管理」把它启动起来"}

        if not out.get("ok"):
            return {"ok": False, "msg": out.get("msg", "预览失败")}

        return {
            "ok": True,
            "stats": out.get("stats", {}),
            "system": out.get("system", ""),
            "roles": out.get("roles", []),
            "rule_report": out.get("rule_report", []),
            "memory_detail": out.get("memory_detail", []),
            "sample": sample.get("filename"),
            "mtime": sample.get("mtime"),
        }

    # --- 服务 ---
    def get_services_status(self):
        return service_mgr.get_all_status()

    def start_service(self, name):
        if name == "core_proxy":
            return service_mgr.start(CORE_PROXY)
        elif name == "caddy":
            return service_mgr.start(CADDY)
        return {"ok": False, "msg": "未知服务: " + name}

    def stop_service(self, name):
        return service_mgr.stop(name)

    def start_all_services(self):
        results = {}
        results["core_proxy"] = service_mgr.start(CORE_PROXY)
        results["caddy"] = service_mgr.start(CADDY)
        return results

    def stop_all_services(self):
        service_mgr.stop_all()
        return {"ok": True}

    # --- 日志 ---
    def get_logs(self, since_index=0):
        with LOG_LOCK:
            data = LOG_BUFFER[since_index:]
            return {"logs": data, "next_index": len(LOG_BUFFER)}

    def clear_logs(self):
        with LOG_LOCK:
            LOG_BUFFER.clear()
        return {"ok": True}


# ==================== 启动 ====================

def on_closing():
    try:
        service_mgr.stop_all()
    except Exception:
        pass

if __name__ == "__main__":
    ensure_user_configs()
    print("=" * 60)
    print(f"卡片目录: {CARDS_DIR}")
    print(f"原版备份: {ORIGINALS_DIR}")
    print(f"上次保存: {LAST_SAVED_DIR}")
    cards = list_cards()
    print(f"卡片数量: {len(cards)}")
    print("=" * 60)

    print("[启动] 正在启动 core_proxy 和 caddy...")
    service_mgr.start(CORE_PROXY)
    service_mgr.start(CADDY)
    print("[启动] 服务已启动（状态可在界面查看）")

    api = Api()
    window = webview.create_window(
        "WorldApart · 人物卡编辑器",
        url=os.path.join(WEB_DIR, "index.html"),
        js_api=api,
        width=1280,
        height=1000,
        min_size=(1000, 600),
    )
    window.events.closing += on_closing
    webview.start()
