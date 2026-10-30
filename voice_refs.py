"""角色参考音（Breeze 音色）管理。

MOD 侧的目录结构（都在 <游戏目录>/A1IndexTTSMod/references 下）：

    npcs/<npc_id>.wav     每个角色的默认参考音（MOD 按角色 id 取这个文件）
    imported/             导入的音色
    npcs_backup/          MOD 自己留的一批原版
    根目录                default_female.wav / default_male.wav / demo.wav

换音色的做法：把选中的 wav 覆盖到 npcs/<npc_id>.wav。
覆盖前先备份到 _voice_backup/<npc_id>.wav，恢复时还原。
为什么不去写 config/npc-voice-overrides.json：那个文件的字段结构（StoredVoice /
RefText）没有公开文档，写错格式 MOD 会静默忽略；而 npcs/<id>.wav 是确定被读取的。
"""

import base64
import os
import re
import shutil
import time
import wave

MOD_NAME = "A1IndexTTSMod"
MAX_LIST = 400
MAX_PREVIEW_BYTES = 12 * 1024 * 1024


# ==================== 目录 ====================
def mod_dir(game_dir):
    return os.path.join(game_dir or "", MOD_NAME)


def refs_dir(game_dir):
    return os.path.join(mod_dir(game_dir), "references")


def npcs_dir(game_dir):
    return os.path.join(refs_dir(game_dir), "npcs")


def imported_dir(game_dir):
    return os.path.join(refs_dir(game_dir), "imported")


def backup_dir(game_dir):
    """我们自己的备份目录（不动 MOD 的 npcs_backup）。"""
    return os.path.join(refs_dir(game_dir), "_voice_backup")


def ready(game_dir):
    """参考音目录是否可用。"""
    return bool(game_dir) and os.path.isdir(npcs_dir(game_dir))


# ==================== wav 信息 ====================
def wav_info(path):
    """读 wav 头。返回 ok/时长/采样率/声道/位深，失败给 msg。"""
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return {"ok": False, "msg": f"读不到文件: {e}"}
    try:
        with wave.open(path, "rb") as w:
            fs = w.getframerate()
            ch = w.getnchannels()
            sw = w.getsampwidth()
            n = w.getnframes()
        return {"ok": True, "size": size, "rate": fs, "ch": ch,
                "bits": sw * 8, "dur": round(n / float(fs or 1), 2)}
    except Exception as e:
        return {"ok": False, "size": size, "msg": f"不是标准 PCM WAV（{e}）"}


def _mb(n):
    return f"{n / 1048576:.1f}MB" if n >= 1048576 else f"{n / 1024:.0f}KB"


def describe(path):
    """一行人类可读的描述，给前端直接用。"""
    info = wav_info(path)
    if not info.get("ok"):
        return info.get("msg") or "读不到"
    return (f"{info['dur']:.2f}s · {info['rate']}Hz · "
            f"{info['bits']}bit · {_mb(info['size'])}")


# ==================== 当前角色 ====================
_STATE_NAME = "last_npc.json"


def _state_path(project_dir):
    return os.path.join(project_dir, "proxy", _STATE_NAME)


def read_last_npc(project_dir):
    """代理落盘的"最近一次对话的角色"，代理没跑也能知道。"""
    import json
    try:
        with open(_state_path(project_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        name = (d.get("npc") or "").strip()
        if not name or name == "unknown":
            return None
        return {"name": name, "ts": d.get("ts") or 0}
    except Exception:
        return None


def card_name_map(cards_dir):
    """id -> 角色名。只读文件名（ID-名字.md），不解析卡片内容，快。"""
    out = {}
    if not cards_dir or not os.path.isdir(cards_dir):
        return out
    for fn in os.listdir(cards_dir):
        if not fn.endswith(".md"):
            continue
        m = re.match(r"^(\d+)-(.+)\.md$", fn)
        if m:
            out[int(m.group(1))] = m.group(2)
    return out


def resolve_npc(cards_dir, name):
    """角色名 -> {id, name}。找不到 id 就返回 id=None。"""
    if not name:
        return None
    for npc_id, cn in card_name_map(cards_dir).items():
        if cn == name:
            return {"id": npc_id, "name": cn}
    return {"id": None, "name": name}


# ==================== 音色列表 ====================
def _iter_wavs(root, prefix):
    if not os.path.isdir(root):
        return
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return
    for fn in names:
        if fn.lower().endswith(".wav"):
            yield prefix + fn


def list_voices(game_dir, query="", cards_dir=None, limit=MAX_LIST):
    """列出可选参考音。query 同时匹配文件名和角色名。

    返回 {ok, total, items:[{rel, label, name, npc_id, kind, applied_hint}]}
    """
    if not ready(game_dir):
        return {"ok": False, "msg": "没找到 MOD 的 references/npcs 目录（游戏目录没配？）"}
    name_map = card_name_map(cards_dir)
    tmap = transcript_map(game_dir)
    q = (query or "").strip().lower()

    items = []
    for rel in _iter_wavs(npcs_dir(game_dir), "npcs/"):
        npc_id = None
        m = re.match(r"^npcs/(\d+)\.wav$", rel)
        if m:
            npc_id = int(m.group(1))
        name = name_map.get(npc_id) or ""
        label = f"{name} · {rel}" if name else rel
        if q and q not in rel.lower() and q not in name.lower():
            continue
        items.append({"rel": rel, "label": label, "name": name,
                      "npc_id": npc_id, "kind": "npcs",
                      "ref_text": tmap.get(npc_id, "") if npc_id else ""})
    for rel in _iter_wavs(imported_dir(game_dir), "imported/"):
        if q and q not in rel.lower():
            continue
        items.append({"rel": rel, "label": rel, "name": "",
                      "npc_id": None, "kind": "imported"})
    for rel in _iter_wavs(refs_dir(game_dir), ""):
        if q and q not in rel.lower():
            continue
        items.append({"rel": rel, "label": rel, "name": "",
                      "npc_id": None, "kind": "root"})

    total = len(items)
    return {"ok": True, "total": total, "items": items[:limit]}


# ==================== 应用 / 恢复 / 导入 ====================
def _safe_rel(rel):
    """只允许 references 目录内的相对路径，挡住 ../ 和绝对路径。"""
    rel = (rel or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    if not rel.lower().endswith(".wav"):
        return None
    return rel


def applied_info(game_dir, npc_id):
    """当前角色实际生效的参考音。"""
    dst = os.path.join(npcs_dir(game_dir), f"{npc_id}.wav")
    bak = os.path.join(backup_dir(game_dir), f"{npc_id}.wav")
    out = {"ok": os.path.isfile(dst), "rel": f"npcs/{npc_id}.wav",
           "exists": os.path.isfile(dst), "backup": os.path.isfile(bak),
           "desc": describe(dst) if os.path.isfile(dst) else ""}
    if os.path.isfile(bak):
        out["backup_desc"] = describe(bak)
        src = _read_sidecar(game_dir, npc_id)
        if src:
            out["source"] = src
    return out


def _sidecar_path(game_dir, npc_id):
    return os.path.join(backup_dir(game_dir), f"{npc_id}.json")


def _read_sidecar(game_dir, npc_id):
    """记一下"这个角色的音色是从哪个文件换过来的"——覆盖后文件名还是
    npcs/<id>.wav，光看文件是看不出来源的。"""
    import json
    try:
        with open(_sidecar_path(game_dir, npc_id), "r", encoding="utf-8") as f:
            return (json.load(f).get("source") or "").strip() or None
    except Exception:
        return None


def _write_sidecar(game_dir, npc_id, source):
    import json
    try:
        os.makedirs(backup_dir(game_dir), exist_ok=True)
        with open(_sidecar_path(game_dir, npc_id), "w", encoding="utf-8") as f:
            json.dump({"source": source, "ts": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass


def apply_voice(game_dir, npc_id, rel):
    """把 rel（references 下相对路径）覆盖成该角色的参考音。"""
    if not ready(game_dir):
        return {"ok": False, "msg": "参考音目录不可用"}
    rel = _safe_rel(rel)
    if not rel:
        return {"ok": False, "msg": "来源路径不合法"}
    src = os.path.join(refs_dir(game_dir), rel.replace("/", os.sep))
    if not os.path.isfile(src):
        return {"ok": False, "msg": f"源文件不存在: {rel}"}
    info = wav_info(src)
    if not info.get("ok"):
        return {"ok": False, "msg": info.get("msg") or "源文件不是 WAV"}
    if info.get("bits") not in (16,):
        return {"ok": False, "msg": f"只支持 16bit PCM，这个文件是 {info.get('bits')}bit"}

    dst = os.path.join(npcs_dir(game_dir), f"{npc_id}.wav")
    if os.path.abspath(src) == os.path.abspath(dst):
        return {"ok": True, "rel": rel, "unchanged": True,
                "msg": "选的就是当前角色的默认参考音，没改动"}

    os.makedirs(backup_dir(game_dir), exist_ok=True)
    bak = os.path.join(backup_dir(game_dir), f"{npc_id}.wav")
    made_backup = False
    if os.path.isfile(dst) and not os.path.isfile(bak):
        shutil.copy2(dst, bak)
        made_backup = True
    shutil.copy2(src, dst)
    _write_sidecar(game_dir, npc_id, rel)
    print(f"[音色] {npc_id} ← {rel}（备份{'已建' if made_backup else '已有'}）", flush=True)
    return {"ok": True, "rel": rel, "to": f"npcs/{npc_id}.wav",
            "backup_created": made_backup, "desc": describe(dst),
            "msg": "已应用，重启游戏后生效"}


def restore_voice(game_dir, npc_id):
    """恢复这个角色的默认参考音。"""
    if not ready(game_dir):
        return {"ok": False, "msg": "参考音目录不可用"}
    dst = os.path.join(npcs_dir(game_dir), f"{npc_id}.wav")
    bak = os.path.join(backup_dir(game_dir), f"{npc_id}.wav")
    if os.path.isfile(bak):
        shutil.copy2(bak, dst)
        os.remove(bak)
        try:
            os.remove(_sidecar_path(game_dir, npc_id))
        except OSError:
            pass
        print(f"[音色] {npc_id} 已恢复默认", flush=True)
        return {"ok": True, "desc": describe(dst),
                "msg": "已恢复默认参考音，重启游戏后生效"}
    # 没有我们的备份时，退回 MOD 自己留的 npcs_backup
    mod_bak = os.path.join(refs_dir(game_dir), "npcs_backup", f"{npc_id}.wav")
    if os.path.isfile(mod_bak):
        shutil.copy2(mod_bak, dst)
        return {"ok": True, "desc": describe(dst),
                "msg": "已用 MOD 自带备份恢复，重启游戏后生效"}
    return {"ok": False, "msg": "没找到备份，这个角色没被改过"}


def import_wav(game_dir, npc_id, src_path):
    """把本地 wav 复制进 imported/，返回可用的相对路径（不自动应用）。"""
    if not ready(game_dir):
        return {"ok": False, "msg": "参考音目录不可用"}
    if not src_path or not os.path.isfile(src_path):
        return {"ok": False, "msg": "没选到文件"}
    if not src_path.lower().endswith(".wav"):
        return {"ok": False, "msg": "只支持 .wav（Breeze 要 PCM WAV）"}
    info = wav_info(src_path)
    if not info.get("ok"):
        return {"ok": False, "msg": info.get("msg") or "不是标准 WAV"}
    if info.get("bits") != 16:
        return {"ok": False, "msg": f"只支持 16bit PCM，这个文件是 {info.get('bits')}bit"}
    if info.get("dur", 0) < 1.0:
        return {"ok": False, "msg": f"太短了（{info.get('dur')}s），建议 3~10 秒"}

    os.makedirs(imported_dir(game_dir), exist_ok=True)
    stem = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fa5-]+", "_",
                  os.path.splitext(os.path.basename(src_path))[0])[:40] or "voice"
    name = f"{npc_id}_{stem}.wav"
    dst = os.path.join(imported_dir(game_dir), name)
    if os.path.isfile(dst):
        name = f"{npc_id}_{stem}_{time.strftime('%H%M%S')}.wav"
        dst = os.path.join(imported_dir(game_dir), name)
    shutil.copy2(src_path, dst)
    rel = f"imported/{name}"
    print(f"[音色] 导入 {rel}（{describe(dst)}）", flush=True)
    return {"ok": True, "rel": rel, "desc": describe(dst),
            "msg": "已导入，点「应用音色」才生效"}


# ==================== 参考音转写 ====================
# Breeze 克隆必须带 reference_text，缺了直接 500（实测："BreezeTTS clone
# requires reference_text"）。默认参考音的转写在 MOD 自己维护的
# references/npcs/npc_id_name.csv 里（npcId,npcName,audio,gender,ref_text），
# 导入的音色没有转写，得让用户在界面上填它念的那句话。
_TRANS_CACHE = {"mtime": None, "map": {}}


def transcript_csv(game_dir):
    return os.path.join(npcs_dir(game_dir), "npc_id_name.csv")


def transcript_map(game_dir):
    path = transcript_csv(game_dir)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if _TRANS_CACHE["mtime"] == mtime:
        return _TRANS_CACHE["map"]
    out = {}
    try:
        import csv
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    nid = int(str(row.get("npcId") or "").strip())
                except ValueError:
                    continue
                txt = (row.get("ref_text") or "").strip()
                if txt:
                    out[nid] = txt
    except Exception as e:
        print(f"[音色] 读转写表失败: {type(e).__name__}: {e}", flush=True)
        out = {}
    _TRANS_CACHE["mtime"] = mtime
    _TRANS_CACHE["map"] = out
    return out


def ref_text_for(game_dir, rel):
    """npcs/<id>.wav 的转写；其他来源返回空串。"""
    m = re.match(r"^npcs/(\d+)\.wav$", rel or "")
    if not m:
        return ""
    return transcript_map(game_dir).get(int(m.group(1)), "")


# ==================== 播放 ====================
def preview_data_url(game_dir, rel):
    """把 references 下的 wav 读成 data URL，给前端 <audio> 播。"""
    rel = _safe_rel(rel)
    if not rel:
        return {"ok": False, "msg": "路径不合法"}
    path = os.path.join(refs_dir(game_dir), rel.replace("/", os.sep))
    if not os.path.isfile(path):
        return {"ok": False, "msg": f"文件不存在: {rel}"}
    if os.path.getsize(path) > MAX_PREVIEW_BYTES:
        return {"ok": False, "msg": "文件太大，没法在页面里播"}
    with open(path, "rb") as f:
        data = f.read()
    return {"ok": True, "url": "data:audio/wav;base64,"
            + base64.b64encode(data).decode("ascii")}
