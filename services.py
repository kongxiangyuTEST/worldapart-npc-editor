import os
import subprocess
import sys
import threading
import time
from pathlib import Path


# ==================== 动态路径 ====================
if getattr(sys, 'frozen', False):
    # 打包后：exe 所在目录
    PROJECT_DIR = Path(sys.executable).parent
else:
    # 开发环境：脚本所在目录
    PROJECT_DIR = Path(__file__).parent.resolve()


# ==================== 配置 ====================
GAME_DIR = str(PROJECT_DIR / "proxy")

IS_FROZEN = getattr(sys, 'frozen', False)

if IS_FROZEN:
    # 打包后：用 proxy\core_proxy.exe
    PYTHON_EXE = str(PROJECT_DIR / "proxy" / "core_proxy.exe")
    CORE_PROXY = {
        "name": "core_proxy",
        "cwd": GAME_DIR,
        "cmd": [PYTHON_EXE],
    }
else:
    # 开发环境：用 .venv 里的 python 跑 core_proxy.py
    PYTHON_EXE = r"H:\llama\models\.venv\Scripts\python.exe"
    CORE_PROXY = {
        "name": "core_proxy",
        "cwd": GAME_DIR,
        "cmd": [PYTHON_EXE, "core_proxy.py"],
    }

CADDY = {
    "name": "caddy",
    "cwd": GAME_DIR,
    "cmd": [os.path.join(GAME_DIR, "caddy_windows_amd64.exe"), "run"],
}

# 当前正在跑的提取进程
_extract_proc = None
_extract_proc_active = [True]

# ==================== 服务管理器 ====================
class ServiceManager:
    def __init__(self, log_callback=None):
        self.processes = {}
        self.reader_threads = {}
        self.log_callback = log_callback
        self.start_times = {}

    def _reader_loop(self, name, proc):
        try:
            for raw in iter(proc.stdout.readline, ""):
                if not raw:
                    break
                line = raw.rstrip("\r\n")
                if self.log_callback:
                    self.log_callback(name, line)
        except Exception as e:
            if self.log_callback:
                self.log_callback(name, f"[reader 错误] {e}")

    def start(self, service_def):
        name = service_def["name"]
        if name in self.processes and self.processes[name].poll() is None:
            return {"ok": False, "msg": f"{name} 已在运行"}

        try:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW

            proc = subprocess.Popen(
                service_def["cmd"],
                cwd=service_def["cwd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self.processes[name] = proc
            self.start_times[name] = time.time()

            t = threading.Thread(target=self._reader_loop, args=(name, proc), daemon=True)
            t.start()
            self.reader_threads[name] = t

            if self.log_callback:
                self.log_callback(name, f"[管理器] {name} 已启动 (PID {proc.pid})")

            return {"ok": True, "pid": proc.pid}

        except Exception as e:
            if self.log_callback:
                self.log_callback(name, f"[管理器] 启动失败: {e}")
            return {"ok": False, "msg": str(e)}

    def stop(self, name, timeout=5):
        proc = self.processes.get(name)
        if proc is None or proc.poll() is not None:
            return {"ok": False, "msg": f"{name} 未运行"}

        try:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if self.log_callback:
                    self.log_callback(name, f"[管理器] {name} 超时未退出，强杀")
                proc.kill()
                proc.wait(timeout=3)

            if self.log_callback:
                self.log_callback(name, f"[管理器] {name} 已停止")
            return {"ok": True}

        except Exception as e:
            if self.log_callback:
                self.log_callback(name, f"[管理器] 停止失败: {e}")
            return {"ok": False, "msg": str(e)}

    def stop_all(self):
        for name in list(self.processes.keys()):
            self.stop(name)

    def status(self, name):
        proc = self.processes.get(name)
        if proc is None:
            return "stopped"
        code = proc.poll()
        if code is None:
            return "running"
        else:
            return f"exited({code})"

    def get_all_status(self):
        return {
            "core_proxy": self.status("core_proxy"),
            "caddy": self.status("caddy"),
        }

    def is_running(self, name):
        return self.status(name) == "running"


manager = None

def init_manager(log_callback):
    global manager
    manager = ServiceManager(log_callback=log_callback)
    return manager

# ==================== 头像提取 ====================

def extract_portraits(assetstudio_cli, game_dir, project_dir, keep_tmp=False, log=None):
    """
    从游戏提取头像，重命名后填入 web/portraits。
    assetstudio_cli: AssetStudioModCLI.exe 完整路径
    game_dir: 游戏根目录（含 WorldApart_Data）
    project_dir: 项目根目录（含 portraits_index.json）
    keep_tmp: 是否保留中间文件
    log: 日志回调函数 log(msg)
    """
    global _extract_proc
    _extract_proc_active[0] = True 
    import json
    import shutil
    from pathlib import Path

    def _log(msg):
        if log:
            log(msg)

    project_dir = Path(project_dir)
    game_dir = Path(game_dir)
    data_dir = game_dir / "WorldApart_Data"
    tmp_dir = project_dir / "_tmp_head_export"
    portraits_dir = project_dir / "web" / "portraits"
    backup_dir = project_dir / "web" / "portraits_backup"
    index_path = project_dir / "portraits_index.json"

    # 检查输入
    if not Path(assetstudio_cli).is_file():
        return {"ok": False, "msg": f"找不到 AssetStudioModCLI.exe：{assetstudio_cli}"}
    if not data_dir.is_dir():
        return {"ok": False, "msg": f"找不到游戏资源目录：{data_dir}"}
    if not index_path.is_file():
        return {"ok": False, "msg": f"找不到 portraits_index.json：{index_path}"}

    # 1. 备份老的 portraits
    if portraits_dir.is_dir():
        if backup_dir.is_dir():
            shutil.rmtree(backup_dir)
        shutil.copytree(portraits_dir, backup_dir)
        _log(f"[备份] 老头像已备份到 {backup_dir}")

    # 2. 清掉旧临时目录
    if tmp_dir.is_dir():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 3. 跑 CLI
    _log("[CLI] 开始导出所有 Texture2D...")
    cmd = [
        str(assetstudio_cli),
        str(data_dir),
        "-t", "tex2d",
        "-o", str(tmp_dir),
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        _extract_proc = proc 
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip("\r\n")
            if not line:
                continue
            if "Exported [" in line:
                continue
            if "Finish" in line or "Error" in line or "Loading" in line:
                _log(f"[CLI] {line}")
        proc.wait()
    except Exception as e:
        _extract_proc = None  
        return {"ok": False, "msg": f"CLI 执行失败: {e}"}

    _extract_proc = None 

    if proc.returncode != 0:
        if not _extract_proc_active[0]:
            return {"ok": False, "msg": "已中止", "aborted": True}
        return {"ok": False, "msg": f"CLI 退出码 {proc.returncode}"}
    
    # 4. 找 headportrait 目录
    head_src = tmp_dir / "assets" / "res" / "gui" / "atlas" / "headportrait"
    if not head_src.is_dir():
        return {"ok": False, "msg": f"CLI 没导出 headportrait 目录：{head_src}"}

    # 5. 按映射表重命名
    with index_path.open("r", encoding="utf-8") as f:
        mapping = json.load(f)

    portraits_dir.mkdir(parents=True, exist_ok=True)
    # 清掉老的
    for f in portraits_dir.glob("*.png"):
        f.unlink()

    done = 0
    missing = []
    for raw_path, new_name in mapping.items():
        src_id = Path(raw_path).stem
        src_file = head_src / f"{src_id}.png"
        if not src_file.exists():
            missing.append(src_id)
            continue
        shutil.copy2(src_file, portraits_dir / new_name)
        done += 1

    _log(f"[重命名] 完成 {done} 张，缺失 {len(missing)} 张")

    # 6. 清临时目录
    if not keep_tmp:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _log("[清理] 已删除临时目录")
    else:
        _log(f"[保留] 临时目录在 {tmp_dir}")

    if missing:
        return {"ok": False, "msg": f"缺失 {len(missing)} 张", "done": done, "missing": missing}
        # 如果进程被中止过，直接返回
    if proc.returncode and proc.returncode < 0:
        return {"ok": False, "msg": "已中止", "aborted": True}
    return {"ok": True, "done": done}

def abort_extract():
    """中止正在跑的提取进程"""
    global _extract_proc
    if _extract_proc is None or _extract_proc.poll() is not None:
        return {"ok": False, "msg": "当前没有在跑的提取"}
    _extract_proc_active[0] = False   # ← 加这一行
    try:
        _extract_proc.terminate()
        try:
            _extract_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _extract_proc.kill()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

# ==================== 角色卡导出 ====================

def extract_npc_cards(assetstudio_cli, game_dir, project_dir, keep_tmp=False, log=None):
    """
    从游戏导出 NPC 角色卡（不依赖 Mod）
    流程：AssetStudioModCLI 导出 TextAsset → 解析 Luban 表 → 生成 .md
    """
    global _extract_proc
    _extract_proc_active[0] = True    # ← 加这行
    import json
    import shutil
    import subprocess
    from pathlib import Path

    def _log(msg):
        if log:
            log(msg)

    project_dir = Path(project_dir)
    game_dir = Path(game_dir)
    data_dir = game_dir / "WorldApart_Data"
    tmp_dir = project_dir / "_tmp_luban_export"
    cards_out = project_dir / "editor_cards"


    # 检查
    if not Path(assetstudio_cli).is_file():
        return {"ok": False, "msg": f"找不到 AssetStudioModCLI.exe：{assetstudio_cli}"}
    if not data_dir.is_dir():
        return {"ok": False, "msg": f"找不到游戏资源目录：{data_dir}"}


        # 导出前先备份旧的 editor_cards
    if cards_out.is_dir() and any(cards_out.glob("*.md")):
        backup_editor_cards(project_dir, log=_log)

    # 清临时目录
    if tmp_dir.is_dir():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # 1. CLI 导出 TextAsset
    _log("[CLI] 开始导出 TextAsset...")
    cmd = [str(assetstudio_cli), str(data_dir), "-t", "textAsset", "-o", str(tmp_dir)]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=creationflags,
        )
        _extract_proc = proc
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip("\r\n")
            if line and ("Finish" in line or "Error" in line):
                _log(f"[CLI] {line}")
            elif line and "Exported" in line:
                # 只显示进度百分比
                if "/1467]" in line:
                    n = line.split("[")[-1].rstrip("]")
                    _log(f"[CLI] {n}")
        proc.wait()
    except Exception as e:
        _extract_proc = None
        return {"ok": False, "msg": f"CLI 执行失败: {e}"}
    _extract_proc = None

    if proc.returncode != 0:
        if not _extract_proc_active[0]:
            return {"ok": False, "msg": "已中止", "aborted": True}
        return {"ok": False, "msg": f"CLI 退出码 {proc.returncode}"}

    # 2. 检查关键 JSON 在不在
    json_dir = tmp_dir / "assets" / "luban" / "data"
    for name in ("tbnpcbasecfg.json", "tbcultivatelayer.json",
                 "tbnpcpersonality.json", "tbitemtype.json"):
        if not (json_dir / name).is_file():
            return {"ok": False, "msg": f"CLI 没导出 {name}"}

    # 3. 直接调解析（不依赖外部 Python）
    _log("[解析] 开始生成卡片...")
    try:
        import sys as _sys
        import importlib
        tools_dir = str(project_dir / "tools")
        if tools_dir not in _sys.path:
            _sys.path.insert(0, tools_dir)
        import parse_npc_cards
        importlib.reload(parse_npc_cards)
        r = parse_npc_cards.parse_cards(tmp_dir, cards_out, log=lambda m: _log(f"[解析] {m}"))
        if not r.get("ok"):
            return {"ok": False, "msg": r.get("msg", "解析失败")}
    except Exception as e:
        return {"ok": False, "msg": f"解析失败: {e}"}

    
    # 4. 数一下生成了多少张
    count = len(list(cards_out.glob("*.md")))
    _log(f"[完成] 共生成 {count} 张卡片")

    # 5. 清临时目录
    if not keep_tmp:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        _log("[清理] 已删除临时目录")
    else:
        _log(f"[保留] 临时目录在 {tmp_dir}")

    return {"ok": True, "done": count, "out_dir": str(cards_out)}

# ==================== 卡片对比 ====================

def _parse_card_for_diff(path):
    """读一张 .md 卡片，拆成 {user_card, preset, background, raw}"""
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8") as f:
        raw = f.read()
    lines = raw.split("\n")

    section = None
    user_lines, bg_lines = [], []
    preset = {}
    skip_comment = False

    for line in lines:
        s = line.rstrip()
        if s.startswith("# ") and not s.startswith("## "):
            section = "title"
            continue
        if s.startswith("## "):
            h = s[3:].strip()
            if h.startswith("用户名片"):
                section = "user"
            elif h.startswith("预设资料"):
                section = "preset"
            elif h.startswith("原游戏背景"):
                section = "bg"
            else:
                section = "other"
            skip_comment = False
            continue
        if section == "user":
            if "<!--" in line:
                skip_comment = True
            if skip_comment:
                if "-->" in line:
                    skip_comment = False
                continue
            user_lines.append(line)
        elif section == "preset":
            ss = s.strip()
            if ss.startswith("- ") and "：" in ss:
                k, v = ss[2:].split("：", 1)
                preset[k.strip()] = v.strip()
        elif section == "bg":
            bg_lines.append(line)

    return {
        "user_card": "\n".join(user_lines).strip(),
        "preset": preset,
        "background": "\n".join(bg_lines).strip(),
    }


def diff_editor_cards(old_dir, new_dir, project_dir=None, log=None):
    """
    对比新旧卡片目录。
    返回：新增 / 改动 / 删除 / 没变
    """
    from pathlib import Path
    from datetime import datetime
    import json as _json

    def _log(msg):
        if log: log(msg)

    old_dir = Path(old_dir)
    new_dir = Path(new_dir)

    if not old_dir.is_dir():
        return {"ok": False, "msg": f"旧目录不存在：{old_dir}"}
    if not new_dir.is_dir():
        return {"ok": False, "msg": f"新目录不存在：{new_dir}"}

    old_files = {f.name: f for f in old_dir.glob("*.md")}
    new_files = {f.name: f for f in new_dir.glob("*.md")}

    old_names = set(old_files)
    new_names = set(new_files)

    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    common = sorted(old_names & new_names)

    changed = []
    unchanged = []

    for name in common:
        old_data = _parse_card_for_diff(old_files[name])
        new_data = _parse_card_for_diff(new_files[name])
        if not old_data or not new_data:
            continue

        # 对比"预设资料"和"原游戏背景"
        diffs = []
        if old_data["background"] != new_data["background"]:
            diffs.append("原游戏背景")
        for k in ("姓名", "性别", "年龄", "境界", "个性", "兴趣"):
            if old_data["preset"].get(k) != new_data["preset"].get(k):
                diffs.append(f"预设·{k}")

        if diffs:
            changed.append({
                "filename": name,
                "fields": diffs,
                "old_bg": old_data["background"][:200],
                "new_bg": new_data["background"][:200],
                "user_card_kept": bool(old_data["user_card"]),
            })
        else:
            unchanged.append(name)

    _log(f"[对比] 新增 {len(added)}，改动 {len(changed)}，删除 {len(removed)}，没变 {len(unchanged)}")

    # 写一份报告 JSON 到项目里
    if project_dir:
        report_dir = Path(project_dir) / "diff_reports"
        report_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report = {
            "time": ts,
            "old_dir": str(old_dir),
            "new_dir": str(new_dir),
            "added": added,
            "changed": changed,
            "removed": removed,
            "unchanged_count": len(unchanged),
        }
        with (report_dir / f"diff-{ts}.json").open("w", encoding="utf-8") as f:
            _json.dump(report, f, ensure_ascii=False, indent=2)
        _log(f"[对比] 报告已保存到 {report_dir / f'diff-{ts}.json'}")

    return {
        "ok": True,
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged_count": len(unchanged),
    }


def backup_editor_cards(project_dir, log=None):
    """把 editor_cards\ 备份到 editor_cards_backup\{时间戳}\"""
    from pathlib import Path
    from datetime import datetime
    import shutil

    def _log(msg):
        if log: log(msg)

    src = Path(project_dir) / "editor_cards"
    if not src.is_dir() or not any(src.glob("*.md")):
        return {"ok": False, "msg": "editor_cards 为空，无需备份"}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = Path(project_dir) / "editor_cards_backup"
    backup_root.mkdir(exist_ok=True)
    dst = backup_root / ts
    shutil.copytree(src, dst)
    _log(f"[备份] 已备份到 {dst}")

    # 只保留最近 5 份
    all_backups = sorted([d for d in backup_root.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    for old in all_backups[5:]:
        shutil.rmtree(old, ignore_errors=True)
        _log(f"[清理] 删掉旧备份 {old.name}")

    return {"ok": True, "path": str(dst)}


def list_card_backups(project_dir):
    """"列出所有备份"""
    from pathlib import Path
    root = Path(project_dir) / "editor_cards_backup"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda x: x.name, reverse=True):
        if d.is_dir():
            cnt = len(list(d.glob("*.md")))
            out.append({"name": d.name, "count": cnt, "path": str(d)})
    return out


# ==================== 卡片对比 ====================

def backup_editor_cards(project_dir, log=None):
    """把 editor_cards\\ 备份到 editor_cards_backup\\{时间戳}\\"""
    from pathlib import Path
    from datetime import datetime
    import shutil

    def _log(msg):
        if log: log(msg)

    src = Path(project_dir) / "editor_cards"
    if not src.is_dir() or not any(src.glob("*.md")):
        return {"ok": False, "msg": "editor_cards 为空，无需备份"}

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = Path(project_dir) / "editor_cards_backup"
    backup_root.mkdir(exist_ok=True)
    dst = backup_root / ts
    shutil.copytree(src, dst)
    _log(f"[备份] 已备份到 {dst}")

    all_backups = sorted([d for d in backup_root.iterdir() if d.is_dir()],
                         key=lambda d: d.name, reverse=True)
    for old in all_backups[5:]:
        shutil.rmtree(old, ignore_errors=True)
        _log(f"[清理] 删掉旧备份 {old.name}")

    return {"ok": True, "path": str(dst)}


def list_card_backups(project_dir):
    """列出所有备份"""
    from pathlib import Path
    root = Path(project_dir) / "editor_cards_backup"
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda x: x.name, reverse=True):
        if d.is_dir():
            cnt = len(list(d.glob("*.md")))
            out.append({"name": d.name, "count": cnt, "path": str(d)})
    return out


def _parse_card_for_diff(path):
    """读一张 .md 卡片，拆成 {user_card, preset, background}"""
    from pathlib import Path
    p = Path(path)
    if not p.is_file():
        return None
    with p.open("r", encoding="utf-8") as f:
        raw = f.read()
    lines = raw.split("\n")

    section = None
    user_lines, bg_lines = [], []
    preset = {}
    skip_comment = False

    for line in lines:
        s = line.rstrip()
        if s.startswith("# ") and not s.startswith("## "):
            section = "title"
            continue
        if s.startswith("## "):
            h = s[3:].strip()
            if h.startswith("用户名片"):
                section = "user"
            elif h.startswith("预设资料"):
                section = "preset"
            elif h.startswith("原游戏背景"):
                section = "bg"
            else:
                section = "other"
            skip_comment = False
            continue
        if section == "user":
            if "<!--" in line:
                skip_comment = True
            if skip_comment:
                if "-->" in line:
                    skip_comment = False
                continue
            user_lines.append(line)
        elif section == "preset":
            ss = s.strip()
            if ss.startswith("- ") and "：" in ss:
                k, v = ss[2:].split("：", 1)
                preset[k.strip()] = v.strip()
        elif section == "bg":
            bg_lines.append(line)

    return {
        "user_card": "\n".join(user_lines).strip(),
        "preset": preset,
        "background": "\n".join(bg_lines).strip(),
    }


def diff_editor_cards(old_dir, new_dir, project_dir=None, log=None):
    """对比新旧卡片目录"""
    from pathlib import Path
    from datetime import datetime
    import json as _json

    def _log(msg):
        if log: log(msg)

    old_dir = Path(old_dir)
    new_dir = Path(new_dir)

    if not old_dir.is_dir():
        return {"ok": False, "msg": f"旧目录不存在：{old_dir}"}
    if not new_dir.is_dir():
        return {"ok": False, "msg": f"新目录不存在：{new_dir}"}

    old_files = {f.name: f for f in old_dir.glob("*.md")}
    new_files = {f.name: f for f in new_dir.glob("*.md")}

    old_names = set(old_files)
    new_names = set(new_files)

    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)
    common = sorted(old_names & new_names)

    changed = []
    unchanged = []

    for name in common:
        old_data = _parse_card_for_diff(old_files[name])
        new_data = _parse_card_for_diff(new_files[name])
        if not old_data or not new_data:
            continue

        diffs = []
        if old_data["background"] != new_data["background"]:
            diffs.append("原游戏背景")
        for k in ("姓名", "性别", "年龄", "境界", "个性", "兴趣"):
            if old_data["preset"].get(k) != new_data["preset"].get(k):
                diffs.append(f"预设·{k}")

        if diffs:
            changed.append({
                "filename": name,
                "fields": diffs,
                "old_bg": old_data["background"][:200],
                "new_bg": new_data["background"][:200],
                "user_card_kept": bool(old_data["user_card"]),
            })
        else:
            unchanged.append(name)

    _log(f"[对比] 新增 {len(added)}，改动 {len(changed)}，删除 {len(removed)}，没变 {len(unchanged)}")

    if project_dir:
        report_dir = Path(project_dir) / "diff_reports"
        report_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        report = {
            "time": ts,
            "old_dir": str(old_dir),
            "new_dir": str(new_dir),
            "added": added,
            "changed": changed,
            "removed": removed,
            "unchanged_count": len(unchanged),
        }
        with (report_dir / f"diff-{ts}.json").open("w", encoding="utf-8") as f:
            _json.dump(report, f, ensure_ascii=False, indent=2)
        _log(f"[对比] 报告已保存到 {report_dir / f'diff-{ts}.json'}")

    return {
        "ok": True,
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged_count": len(unchanged),
    }

def merge_cards(old_dir, new_dir, out_dir=None, project_dir=None, log=None):
    """
    把新数据合并进旧卡片：
    - 新增：直接加
    - 改动：游戏字段用新，用户名片保留旧
    - 删除：保留旧卡片
    - 没变：不动
    """
    from pathlib import Path
    from datetime import datetime
    import shutil

    def _log(msg):
        if log: log(msg)

    old_dir = Path(old_dir)
    new_dir = Path(new_dir)
    if not old_dir.is_dir() or not new_dir.is_dir():
        return {"ok": False, "msg": "旧/新目录不存在"}

    # 合并目标：默认写回 new_dir（覆盖新版），保留用户名片
    if out_dir is None:
        out_dir = new_dir
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先备份 out_dir（如果里面有东西）
    if project_dir and out_dir == new_dir and any(new_dir.glob("*.md")):
        backup_editor_cards(project_dir, log=_log)

    old_files = {f.name: f for f in old_dir.glob("*.md")}
    new_files = {f.name: f for f in new_dir.glob("*.md")}

    added, changed, removed, unchanged = 0, 0, 0, 0

    # 1. 处理新版里的每一张
    for name, new_path in new_files.items():
        new_data = _parse_card_for_diff(new_path)
        if not new_data:
            continue

        if name not in old_files:
            # 新增：新版原样保留（但重建格式，保证统一）
            _write_merged_card(out_dir / name, new_data["preset"], new_data["background"], "")
            added += 1
            continue

        old_data = _parse_card_for_diff(old_files[name])
        if not old_data:
            continue

        # 判断游戏字段有没有变
        bg_changed = old_data["background"] != new_data["background"]
        preset_changed = any(
            old_data["preset"].get(k) != new_data["preset"].get(k)
            for k in ("姓名", "性别", "年龄", "境界", "个性", "兴趣")
        )

        if bg_changed or preset_changed:
            # 用新版游戏字段 + 旧版用户名片
            _write_merged_card(
                out_dir / name,
                new_data["preset"],
                new_data["background"],
                old_data["user_card"],
            )
            changed += 1
        else:
            # 没变：保留旧版（用户名片原样）
            shutil.copy2(old_files[name], out_dir / name)
            unchanged += 1

    # 2. 处理旧版里有、新版里没有的（删除）：保留旧卡
    for name, old_path in old_files.items():
        if name not in new_files:
            shutil.copy2(old_path, out_dir / name)
            removed += 1

    _log(f"[合并] 新增 {added}，改动 {changed}，删除保留 {removed}，没变 {unchanged}")
    return {
        "ok": True,
        "added": added,
        "changed": changed,
        "removed": removed,
        "unchanged": unchanged,
        "out_dir": str(out_dir),
    }


def _write_merged_card(path, preset, background, user_card):
    """按现有 .md 格式写一张合并后的卡片"""
    name = preset.get("姓名", "") or ""
    npc_id = ""
    # 从 path 文件名里抽 id
    fname = path.name
    if "-" in fname:
        npc_id = fname.split("-", 1)[0]

    lines = []
    lines.append(f"# {name}")
    lines.append("")
    lines.append(f"NPC ID: {npc_id}")
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
    lines.append(f"- 姓名：{preset.get('姓名', '')}")
    lines.append(f"- 性别：{preset.get('性别', '')}")
    lines.append(f"- 年龄：{preset.get('年龄', '')}")
    lines.append(f"- 境界：{preset.get('境界', '')}")
    lines.append(f"- 个性：{preset.get('个性', '')}")
    lines.append(f"- 兴趣：{preset.get('兴趣', '')}")
    lines.append("")
    lines.append("## 原游戏背景")
    lines.append("")
    lines.append(background)
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))