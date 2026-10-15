import webview
import os
import re
import json
import shutil
import time
import threading
from datetime import datetime
from config import CARDS_DIR, WEB_DIR, IGNORED_FILES, CARD_NAME_PREFIX_RE
import services
from services import CORE_PROXY, CADDY


# ==================== 路径 ====================

import sys
if getattr(sys, 'frozen', False):
    PROJECT_DIR = os.path.dirname(sys.executable)
else:
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ORIGINALS_DIR = os.path.join(PROJECT_DIR, "originals")
LAST_SAVED_DIR = os.path.join(PROJECT_DIR, "last_saved")
AI_CONFIG_PATH = os.path.join(PROJECT_DIR, "ai_config.json")
PROMPT_FRAGMENTS_PATH = os.path.join(PROJECT_DIR, "prompt_fragments.json")
PORTRAITS_DIR = os.path.join(WEB_DIR, "portraits")
PORTRAITS_INDEX_PATH = os.path.join(PROJECT_DIR, "portraits_index.json")
PROMPT_SAMPLES_DIR = os.path.join(PROJECT_DIR, "prompt_samples")

os.makedirs(ORIGINALS_DIR, exist_ok=True)
os.makedirs(LAST_SAVED_DIR, exist_ok=True)


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
    """确保 originals\ 里有这张卡片的原始备份"""
    src = os.path.join(CARDS_DIR, filename)
    dst = os.path.join(ORIGINALS_DIR, filename)
    if not os.path.exists(dst) and os.path.isfile(src):
        try:
            shutil.copy2(src, dst)
            print(f"[备份] 生成原版备份: {filename}")
        except Exception as e:
            print(f"[备份] 失败 {filename}: {e}")


def ensure_last_saved_backup(filename):
    """确保 last_saved\ 里有这张卡片的备份（首次直接复制当前文件）"""
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
    """
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

    # 取最新
    matched.sort(reverse=True)
    mtime, fname, path = matched[0]

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"filename": fname, "error": str(e)}

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
    """读取 originals\ 里的原始卡片"""
    path = os.path.join(ORIGINALS_DIR, filename)
    if not os.path.isfile(path):
        return None
    return parse_card_file(path)


def get_last_saved_card(filename):
    """读取 last_saved\ 里的卡片"""
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
    """
    根据 NPC ID，返回 zip 内文件名（如 "0.png"），没有则返回 None。
    游戏内路径格式：assets/res/gui/atlas/headportrait/{id}.png
    """
    key = f"assets/res/gui/atlas/headportrait/{npc_id}.png"
    return PORTRAITS_INDEX.get(key)



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
        return f"/portraits/{filename}"
    
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
        """弹文件选择框，让用户选 AssetStudioModCLI.exe"""
        try:
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("所有文件 (*.*)",),
            )
            if result and len(result) > 0:
                return {"ok": True, "path": result[0]}
            return {"ok": False, "msg": "未选择"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def pick_game_dir(self):
        """弹文件夹选择框，让用户选游戏根目录"""
        try:
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result) > 0:
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
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)
            if not result or len(result) == 0:
                return {"ok": False, "msg": "未选择"}
            gd = result[0]
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
    print("[退出] 正在停止服务...")
    service_mgr.stop_all()
    print("[退出] 服务已停止")


if __name__ == "__main__":
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
