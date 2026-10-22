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
     # 境界：暂时不写入（游戏运行时数据）
    # lines.append(f"- 境界：{preset.get('境界', '未知或凡人')}")
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
                          use_memory=True):
        """用 AI 给某张卡的「用户名片」生成内容（只生成，不写盘）。

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
                for sec in ("当前场景", "本轮会话状态", "角色设定", "任务"):
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
                    if data.get("early_gist"):
                        parts.append("早期梗概：" + str(data["early_gist"])[:300])
                    if data.get("long_summary"):
                        parts.append("最近剧情：" + str(data["long_summary"])[:500])
                    for cat, arr in (data.get("facts") or {}).items():
                        for f in arr[:6]:
                            txt = (f or {}).get("text") if isinstance(f, dict) else f
                            if txt:
                                parts.append(f"事实[{cat}]：{txt}")
                    if parts:
                        mem_ctx = "\n".join(parts)[:1400]
                        sources.append("长期记忆（剧情摘要与事实）")
            except Exception as e:
                print(f"[AI补全] 读记忆失败: {e}", flush=True)

        # --- 上游：优先本地（memory_merge_url） ---
        upstream, fallback_key = "", ""
        try:
            with open(AI_CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                acfg = json.load(f)
            upstream = (acfg.get("memory_merge_url") or "").strip()
        except Exception:
            upstream = ""
        if not upstream:
            conf_path = os.path.join(PROJECT_DIR, "proxy", "core_proxy_config.json")
            upstream = "http://127.0.0.1:8080/v1/chat/completions"
            try:
                with open(conf_path, "r", encoding="utf-8") as f:
                    ccfg = json.load(f)
                    base = (ccfg.get("upstream_base_url") or "http://127.0.0.1:8080/").rstrip("/")
                    upstream = base + "/v1/chat/completions"
                    fallback_key = ccfg.get("api_key") or ""
            except Exception:
                pass

        hint = (user_hint or "").strip()
        prompt = (
            "你在给一个游戏 NPC 写「角色演绎要求」。下面给出这个角色的资料，"
            "请据此写出她怎么说话、怎么反应。\n\n"
            "【硬性规则】\n"
            "1. 只依据给定资料推断，不要编造资料里没有的身世、门派、剧情。\n"
            "2. 不要写露骨内容，只写语气、习惯、反应方式。\n"
            "3. 示例台词只示范语气，不要复述资料里的剧情原文。\n"
            "4. 输出严格 JSON，不要 markdown 代码块：\n"
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
            "temperature": 0.5,
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
        try:
            d = json.loads(raw[a:b + 1])
        except Exception as e:
            return {"ok": False, "msg": f"JSON 解析失败: {e}"}

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
                "chars": len(markdown), "name": name}

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
