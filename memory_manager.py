import os
import json
import re
import shutil
import threading
import datetime

# ================= 配置 =================
# 记忆文件存放的文件夹（与 editor_cards 同级）
import sys
if getattr(sys, 'frozen', False):
    # 打包后的真实 exe 路径
    base_dir = os.path.dirname(sys.executable)
else:
    # 源码运行时的 py 文件路径
    base_dir = os.path.dirname(os.path.abspath(__file__))
MEMORY_DIR = os.path.join(base_dir, "memory")

# 遗忘日志
FORGET_LOG = os.path.join(MEMORY_DIR, "_遗忘纪录.log")

# 老化参数
AGE_DOWNGRADE_LIMIT = 24
AGE_DELETE_LIMIT = 48
FACT_CATEGORIES = ["赠物", "承诺", "关系里程碑", "重要事件"]

# 注入预算相关的默认值（core_proxy 会从 ai_config.json 的 memory_budget 覆盖总预算）
GIST_MAX = 150                 # 早期梗概上限
RECENT_MAX = 650               # 最近剧情上限
FACT_CAP_PER_CATEGORY = 20     # 每个分类最多注入多少条事实
PROTECTED_CATEGORIES = ("关系里程碑",)   # 这几类永不被预算砍掉

# 写盘备份
BACKUP_DIR = os.path.join(MEMORY_DIR, "_backup")
BACKUP_KEEP = 3                # 每个角色保留最近几份备份

# 读写锁：合并跑在后台线程，和请求线程会同时读写同一个记忆文件
LOCK = threading.RLock()

# 确保文件夹存在
for _d in (MEMORY_DIR, BACKUP_DIR):
    if not os.path.exists(_d):
        os.makedirs(_d, exist_ok=True)

# ================= 核心逻辑 =================

def get_memory_path(npc_id, npc_name):
    """生成文件名：100000-苏倾盏.json"""
    safe_name = re.sub(r'[\\/*?:"<>|]', "", str(npc_name))
    return os.path.join(MEMORY_DIR, f"{npc_id}-{safe_name}.json")

def load_memory(npc_id, npc_name):
    """读取角色记忆，如果没有就生成一份空白的"""
    path = get_memory_path(npc_id, npc_name)
    with LOCK:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception as e:
                print(f"读取记忆出错: {e}")

    # 默认空白记忆
    return {
        "npc_id": npc_id,
        "npc_name": npc_name,
        "time_location": "",
        "early_gist": "",
        "long_summary": "",
        "facts": {cat: [] for cat in FACT_CATEGORIES}
    }


def _backup_memory_file(path, npc_id, npc_name):
    """覆盖前把旧文件备份一份，只保留最近 BACKUP_KEEP 份。"""
    try:
        if not os.path.isfile(path):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dst = os.path.join(BACKUP_DIR, f"{npc_id}-{npc_name}.{ts}.json")
        shutil.copy2(path, dst)

        prefix = f"{npc_id}-{npc_name}."
        olds = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith(prefix)], reverse=True)
        for f in olds[BACKUP_KEEP:]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except OSError:
                pass
    except Exception as e:
        print(f"备份记忆失败: {e}")


def save_memory(npc_id, npc_name, data):
    """保存记忆到文件（原子写：先写临时文件再替换，避免写一半崩掉留下坏 JSON）"""
    path = get_memory_path(npc_id, npc_name)
    with LOCK:
        try:
            _backup_memory_file(path, npc_id, npc_name)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception as e:
            print(f"保存记忆出错: {e}")
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except OSError:
                pass
            return False

# ============================================================
# 存档守卫：游戏本身也记「## 过往要事」，换个存档喂过来的条目会完全变样。
# 我们不猜"现在读的是哪个档"，只判断"这一轮的游戏记忆还是不是同一档"，
# 对不上就暂停注入，等用户在记忆页确认，避免把 A 档的剧情塞进 B 档。
# ============================================================
ARCHIVE_DIR = os.path.join(MEMORY_DIR, "_saves")


def _now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _norm_line(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def check_game_events(npc_id, npc_name, lines, persist=True):
    """把本轮游戏侧记忆条目和上次记下的对比。

    返回 {"status": init|same|grew|mismatch, "kept": 命中旧条目数,
          "total": 旧条目数, "ratio": 命中率, "blocked": bool}
    """
    cur = [_norm_line(x) for x in (lines or []) if _norm_line(x)]

    with LOCK:
        data = load_memory(npc_id, npc_name)
        stored = [_norm_line(x) for x in (data.get("game_events") or []) if _norm_line(x)]

        # 第一次见到这角色：直接把当前条目记为基线
        if not stored:
            if cur and persist:
                data["game_events"] = cur
                data["game_events_at"] = _now_str()
                save_memory(npc_id, npc_name, data)
            return {"status": "init", "kept": 0, "total": 0, "ratio": 1.0, "blocked": False}

        kept = sum(1 for s in stored if s in cur)
        ratio = kept / len(stored)

        # 正常推进：游戏侧的条目只增不减，旧的基本都还在
        normal = (ratio >= 0.4) or len(stored) < 2
        if normal:
            if cur != stored and persist:
                data["game_events"] = cur
                data["game_events_at"] = _now_str()
                data.pop("game_events_pending", None)
                save_memory(npc_id, npc_name, data)
            return {
                "status": "grew" if cur != stored else "same",
                "kept": kept, "total": len(stored), "ratio": ratio, "blocked": False,
            }

        # 疑似另一份存档：记下待确认的条目，但不动原基线
        if data.get("game_events_pending") != cur and persist:
            data["game_events_pending"] = cur
            data["game_events_pending_at"] = _now_str()
            save_memory(npc_id, npc_name, data)
        return {
            "status": "mismatch", "kept": kept,
            "total": len(stored), "ratio": ratio, "blocked": True,
        }


def get_game_conflict(npc_id, npc_name):
    """读当前挂起的存档冲突（没有就返回 None）"""
    with LOCK:
        data = load_memory(npc_id, npc_name)
        pending = data.get("game_events_pending")
        if not pending:
            return None
        return {
            "pending": pending,
            "pending_at": data.get("game_events_pending_at", ""),
            "known": data.get("game_events") or [],
        }


def resolve_game_conflict(npc_id, npc_name, action):
    """处理存档冲突。

    accept_new：确实是另一份存档 → 把当前记忆整份归档到 memory/_saves/，
                再以新档的条目为基线、长期记忆从空白重新开始
    keep_old  ：其实是同一档（或就想沿用旧记忆）→ 清掉冲突，继续注入
    """
    with LOCK:
        data = load_memory(npc_id, npc_name)
        pending = data.get("game_events_pending")
        if not pending:
            return {"ok": False, "msg": "没有待处理的冲突"}

        if action == "keep_old":
            data.pop("game_events_pending", None)
            data.pop("game_events_pending_at", None)
            save_memory(npc_id, npc_name, data)
            return {"ok": True, "action": "keep_old"}

        if action != "accept_new":
            return {"ok": False, "msg": f"未知操作: {action}"}

        # 先归档旧记忆，再清空
        archived = ""
        path = get_memory_path(npc_id, npc_name)
        try:
            if os.path.isfile(path):
                os.makedirs(ARCHIVE_DIR, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                archived = os.path.join(ARCHIVE_DIR, f"{npc_id}-{npc_name}.{ts}.json")
                shutil.copy2(path, archived)
        except Exception as e:
            print(f"归档记忆失败: {e}")
            return {"ok": False, "msg": f"归档失败: {e}"}

        fresh = {
            "npc_id": npc_id,
            "npc_name": npc_name,
            "time_location": "",
            "early_gist": "",
            "long_summary": "",
            "facts": {cat: [] for cat in FACT_CATEGORIES},
            "game_events": list(pending),
            "game_events_at": _now_str(),
        }
        save_memory(npc_id, npc_name, fresh)
        return {"ok": True, "action": "accept_new", "archived": archived}


def log_forget(npc_id, npc_name, fact):
    """记录被遗忘的事实到日志"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{now_str}] 角色 {npc_id}-{npc_name} | 遗忘事实(重要度 {fact['importance']}): {fact['text']}\n"
    try:
        with open(FORGET_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def process_aging(npc_id, npc_name, data):
    """
    执行老化逻辑（每次对话完调用）
    规则：24 轮没提到降级，48 轮没提到删除。4、5级永不老化。
    """
    changed = False
    for cat in FACT_CATEGORIES:
        if cat not in data["facts"]:
            data["facts"][cat] = []
            continue
            
        new_facts = []
        for fact in data["facts"][cat]:
            imp = fact.get("importance", 3)
            
            # 4、5级不老化，直接保留
            if imp >= 4:
                new_facts.append(fact)
                continue
                
            # 老化计数 +1
            fact["age_count"] = fact.get("age_count", 0) + 1
            changed = True
            
            # 降级检查
            if fact["age_count"] >= AGE_DOWNGRADE_LIMIT:
                fact["importance"] = max(1, imp - 1)
                fact["age_count"] = 0
                
            # 删除检查（降到1级后）
            if fact["importance"] == 1 and fact["age_count"] >= AGE_DELETE_LIMIT:
                log_forget(npc_id, npc_name, fact)
                continue # 丢弃（删除）
                
            new_facts.append(fact)
            
        data["facts"][cat] = new_facts
        
    return data, changed

def reset_fact_age(data, mentioned_text):
    """如果本轮提到了，把相关事实的老化计数清零"""
    if not mentioned_text:
        return data
    # 简单粗暴的关键词匹配，防误判
    for cat in FACT_CATEGORIES:
        for fact in data["facts"].get(cat, []):
            # 如果事实里的核心词在对话里出现了
            core_words = re.findall(r'[\u4e00-\u9fa5]{2,4}', fact["text"])
            if any(w in mentioned_text for w in core_words):
                fact["age_count"] = 0
    return data

def _clip(text, limit):
    """超长时限长：保留开头 60% 和结尾 40%，中间用省略号。"""
    if not text or len(text) <= limit:
        return text
    if limit <= 12:
        return text[:limit]
    head = int(limit * 0.6)
    tail = limit - head - 2
    return text[:head].rstrip() + "……" + (text[-tail:].lstrip() if tail > 0 else "")


def _fact_hits(text, hint):
    """事实里的中文词是否在本轮玩家发言里出现过（纯字符串匹配，不花 token）。"""
    if not hint or not text:
        return False
    words = re.findall(r"[\u4e00-\u9fa5]{2,4}", str(text))
    return any(w in hint for w in words)


def build_injection_text(data, budget_chars=2000, hint="", priority=None):
    """把记忆拼成注入文本。

    与旧版的区别（旧版是拼完 text[:2000] 硬截断，会把摘要和里程碑直接切掉）：
      1. 摘要按 GIST_MAX / RECENT_MAX 各自限长，超了保留头尾
      2. 事实按分类顺序输出，'关系里程碑' 标记为受保护，预算裁剪时永不被砍
      3. 预算不足时从后往前删事实行；事实删光还超，才去压摘要
      4. 重要度 ≤2 的琐事只有在本轮玩家发言命中时才注入
      5. 每个分类最多 FACT_CAP_PER_CATEGORY 条（取最近的）
    """
    facts = data.get("facts") or {}

    items = []   # [(kind, text, protected)]

    time_loc = str(data.get("time_location") or "").strip() or "未记录"
    if time_loc:
        items.append(("time", f"【当前时间地点】{time_loc}", True))

    gist = _clip(str(data.get("early_gist") or "").strip(), GIST_MAX)
    if gist:
        items.append(("gist", f"【故事早期梗概】\n{gist}", False))

    recent = _clip(str(data.get("long_summary") or "").strip(), RECENT_MAX)
    if recent:
        items.append(("recent", f"【最近剧情】\n{recent}", False))

    # 分类顺序：里程碑（保护）→ 承诺 → 赠物 → 重要事件
    order = ["关系里程碑", "承诺", "赠物", "重要事件"]
    order += [c for c in FACT_CATEGORIES if c not in order]

    for cat in order:
        arr = facts.get(cat) or []
        if not isinstance(arr, list) or not arr:
            continue

        picked = []
        for it in arr[-FACT_CAP_PER_CATEGORY:]:
            if isinstance(it, dict):
                imp = it.get("importance", 3)
                text = str(it.get("text") or "").strip()
            else:
                imp, text = 3, str(it).strip()
            if not text:
                continue
            try:
                imp = int(imp)
            except (TypeError, ValueError):
                imp = 3
            # 琐事按需注入：本轮没提到就不塞进去
            if imp <= 2 and not _fact_hits(text, hint):
                continue
            picked.append((imp, text))

        if not picked:
            continue

        protected = cat in PROTECTED_CATEGORIES
        lines = [f"【{cat}】"] + [f"- {t}" for _, t in picked]
        items.append(("fact", "\n".join(lines), protected))

    def total():
        return sum(len(t) + 1 for _, t, _ in items)

    # 预算裁剪：先删事实（从后往前、跳过受保护的），再压摘要
    guard = 0
    while total() > budget_chars and guard < 200:
        guard += 1
        removed = False
        for i in range(len(items) - 1, -1, -1):
            kind, text, protected = items[i]
            if kind == "fact" and not protected:
                items.pop(i)
                removed = True
                break
        if removed:
            continue

        # 没有可删的事实了：压摘要（保留头尾）
        shrunk = False
        for i in range(len(items) - 1, -1, -1):
            kind, text, protected = items[i]
            if kind in ("recent", "gist") and len(text) > 120:
                over = total() - budget_chars
                new_limit = max(120, len(text) - over - 10)
                items[i] = (kind, _clip(text, new_limit), protected)
                shrunk = True
                break
        if not shrunk:
            break

    text = "\n".join(t for _, t, _ in items)
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n（记忆过长，已截断）"
    return text

# 第三方 API 会因为内容审核拒答、只给一段道歉、或干脆返回非 JSON。
# 这种情况下绝不能把"抱歉我无法协助"写进记忆，更不能把原始对话丢掉。
REFUSAL_HINTS = ("抱歉", "无法协助", "无法提供", "不能协助", "不能提供",
                 "i can't", "i cannot", "i'm sorry", "as an ai")


def _looks_refusal(text):
    """粗判模型是不是在拒答（返回里没有任何 JSON 结构时才判）。"""
    if "{" in text:
        return False
    low = text.lower()
    return any(h in low for h in REFUSAL_HINTS)


def _restore_merge_backlog(npc_id, npc_name, logs, reason):
    """总结失败：把这批对话原样塞回待合并队列，下一轮继续试，不丢数据。"""
    with LOCK:
        try:
            data = load_memory(npc_id, npc_name)
            old = str(data.get("merge_backlog") or "").strip()
            data["merge_backlog"] = (logs.strip() + ("\n\n" + old if old else ""))
            data["merge_fail_count"] = int(data.get("merge_fail_count") or 0) + 1
            data["merge_last_error"] = reason
            save_memory(npc_id, npc_name, data)
            print(f"[记忆合并] 已把 {len(logs)} 字对话退回待合并队列（累计失败 "
                  f"{data['merge_fail_count']} 次），下轮重试", flush=True)
        except Exception as e:
            print(f"[记忆合并] 退回失败，这批对话可能丢失: {e}", flush=True)


def do_auto_merge(npc_id, npc_name, recent_logs, upstream_url, fallback_key,
                  max_tokens=4000):
    """让 AI 总结剧情并提取事实。成功返回 True，失败返回 False（失败会把对话退回队列）。"""
    if not recent_logs:
        return False
    
    data = load_memory(npc_id, npc_name)
    
    prompt = f"""
你是一个擅长总结的助手。请阅读以下对话，总结出“故事早期梗概”（只增不减）、“最近剧情”（滚动更新），并提取出“事实条目”。
【当前数据】：
早期梗概：{data.get('early_gist', '')}
最近剧情：{data.get('long_summary', '')}

【最近对话】：
{recent_logs}

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
3. new_facts 只写【真正值得记的事实】：赠物/承诺/关系里程碑/重要事件。
   一次调情、一句闲话不要记；没有就返回空数组。
4. 已有事实里已经写过的内容，不要重复输出。
"""
    import requests
    import json
    
    headers = {"Content-Type": "application/json"}
    if fallback_key: headers["Authorization"] = "Bearer " + fallback_key
    
    body = {
        "model": "local-model",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": int(max_tokens),
        # 关掉思考：否则思考内容会把输出预算吃掉，JSON 被截断
        "thinking": {"type": "disabled"},
    }
    
    print(f"\n[记忆合并] 开始请求本地模型 {upstream_url}...", flush=True)
    
    try:
        # 这里加了 longer timeout
        res = requests.post(upstream_url, json=body, headers=headers, timeout=180)
        print(f"[记忆合并] 模型返回状态码: {res.status_code}", flush=True)
        
        if res.status_code == 200:
            text = res.json()["choices"][0]["message"]["content"]
            print(f"[记忆合并] 模型回复 {len(text)} 字，前 20 字: {text[:20]!r}", flush=True)
            
            # 清理多余标记
            text = text.strip("`").replace("json\n", "").replace("json", "")
            
            # 模型常在 JSON 前后加一句"好的，以下是总结："，
            # 直接 loads 会失败；先切出花括号区间再解析
            _s, _e = text.find("{"), text.rfind("}")
            candidate = text[_s:_e + 1] if (_s >= 0 and _e > _s) else text
            try:
                res_data = json.loads(candidate)
            except json.JSONDecodeError as e:
                why = "拒答/非JSON" if _looks_refusal(text) else "JSON解析失败"
                print(f"[记忆合并] {why}：{e}；模型回复前 40 字: {text[:40]!r}", flush=True)
                _restore_merge_backlog(npc_id, npc_name, recent_logs, f"{why}: {e}")
                return False
            
            # 1. 更新梗概
            if res_data.get("early_gist"):
                data["early_gist"] = res_data["early_gist"]
            if res_data.get("long_summary"):
                data["long_summary"] = res_data["long_summary"]
                
            # 1.5 时间地点（以前没人写这个字段，界面里永远是空的）
            tl = str(res_data.get("time_location") or "").strip()
            if tl:
                data["time_location"] = tl[:60]

            # 2. 插入新事实（按文本去重：同一事实只留一条，重要度取高的、老化清零。
            #    不去重的话，模型每轮重复输出同一条就会越积越多，
            #    注入时同一件事出现 N 遍 → 角色开始复读）
            seen = {}
            for cat in FACT_CATEGORIES:
                for f in data["facts"].get(cat, []):
                    key = re.sub(r"\s+", "", str(f.get("text", "")))
                    if key:
                        seen[key] = f

            added = 0
            for fact in res_data.get("new_facts", []):
                cat = fact.get("category", "重要事件")
                if cat not in FACT_CATEGORIES: cat = "重要事件"
                text = str(fact.get("text") or "").strip()
                if not text:
                    continue
                try:
                    imp = int(fact.get("importance", 3))
                except (TypeError, ValueError):
                    imp = 3
                imp = min(5, max(1, imp))

                key = re.sub(r"\s+", "", text)
                if key in seen:
                    old = seen[key]
                    old["importance"] = min(5, max(int(old.get("importance", 3) or 3), imp))
                    old["age_count"] = 0
                    continue

                data["facts"][cat].append({"text": text, "importance": imp, "age_count": 0})
                seen[key] = data["facts"][cat][-1]
                added += 1
                
            data.pop("merge_backlog", None)
            data.pop("merge_fail_count", None)
            data.pop("merge_last_error", None)
            save_memory(npc_id, npc_name, data)
            print(f"[记忆合并] ✅ {npc_name} 的剧情与事实已成功更新保存！\n", flush=True)
            return True
        else:
            print(f"[记忆合并] ❌ 请求失败（HTTP {res.status_code}），报错信息: {res.text[:200]}\n", flush=True)
            _restore_merge_backlog(npc_id, npc_name, recent_logs, f"HTTP {res.status_code}")
            return False
    except Exception as e:
        print(f"[记忆合并] ❌ 发生异常: {e}\n", flush=True)
        _restore_merge_backlog(npc_id, npc_name, recent_logs, str(e))
        return False

def tidy_memory(data, gist_max=GIST_MAX, recent_max=RECENT_MAX):
    """本地整理一份记忆，不调 AI、不联网。

    做四件事：
      1. 补缺失字段（facts 四个分类、recent_logs）
      2. 摘要超长按上限压缩（保留头 60% + 尾 40%，跟注入时同一套逻辑）
      3. 事实体检：text 空 / importance 越界 / age_count 非法 / 同分类重复
      4. 顺手清掉合并失败留下的临时字段
    返回 (整理后的 data, 报告 dict)
    """
    rep = {"补字段": 0, "压摘要": 0, "删空事实": 0, "修重要度": 0,
           "修老化计数": 0, "去重复": 0, "清临时字段": 0}

    facts = data.get("facts")
    if not isinstance(facts, dict):
        facts = {}
        data["facts"] = facts
        rep["补字段"] += 1
    for cat in FACT_CATEGORIES:
        if not isinstance(facts.get(cat), list):
            facts[cat] = []
            rep["补字段"] += 1

    if not isinstance(data.get("recent_logs"), list):
        data["recent_logs"] = []
        rep["补字段"] += 1

    for key, limit in (("early_gist", gist_max), ("long_summary", recent_max)):
        v = str(data.get(key) or "").strip()
        if len(v) > limit:
            data[key] = _clip(v, limit)
            rep["压摘要"] += 1
        else:
            data[key] = v

    for cat in FACT_CATEGORIES:
        kept, seen = [], set()
        for f in facts.get(cat, []):
            if not isinstance(f, dict):
                rep["删空事实"] += 1
                continue
            text = str(f.get("text") or "").strip()
            if not text:
                rep["删空事实"] += 1
                continue

            try:
                imp = int(f.get("importance", 3))
            except (TypeError, ValueError):
                imp = 3
            if imp < 1 or imp > 5:
                rep["修重要度"] += 1
            imp = min(5, max(1, imp))

            try:
                age = int(f.get("age_count", 0))
            except (TypeError, ValueError):
                age = 0
            if age < 0:
                age = 0
                rep["修老化计数"] += 1

            key = re.sub(r"\s+", "", text)
            if key in seen:
                rep["去重复"] += 1
                continue
            seen.add(key)

            kept.append({"text": text, "importance": imp, "age_count": age})
        facts[cat] = kept

    for tmp in ("merge_fail_count", "merge_last_error"):
        if tmp in data:
            data.pop(tmp, None)
            rep["清临时字段"] += 1

    return data, rep


def clear_memory(npc_id, npc_name):
    """清空某个角色的所有长期记忆"""
    path = get_memory_path(npc_id, npc_name)
    with LOCK:
        if os.path.exists(path):
            try:
                os.remove(path)
                return True
            except Exception as e:
                print(f"删除失败: {e}")
    return False