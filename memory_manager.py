import os
import json
import re
import datetime

# ================= 配置 =================
# 记忆文件存放的文件夹（与 editor_cards 同级）
MEMORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory")
# 遗忘日志
FORGET_LOG = os.path.join(MEMORY_DIR, "_遗忘纪录.log")

# 老化参数
AGE_DOWNGRADE_LIMIT = 24
AGE_DELETE_LIMIT = 48
FACT_CATEGORIES = ["赠物", "承诺", "关系里程碑", "重要事件"]

# 确保文件夹存在
if not os.path.exists(MEMORY_DIR):
    os.makedirs(MEMORY_DIR)

# ================= 核心逻辑 =================

def get_memory_path(npc_id, npc_name):
    """生成文件名：100000-苏倾盏.json"""
    safe_name = re.sub(r'[\\/*?:"<>|]', "", str(npc_name))
    return os.path.join(MEMORY_DIR, f"{npc_id}-{safe_name}.json")

def load_memory(npc_id, npc_name):
    """读取角色记忆，如果没有就生成一份空白的"""
    path = get_memory_path(npc_id, npc_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
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

def save_memory(npc_id, npc_name, data):
    """保存记忆到文件"""
    path = get_memory_path(npc_id, npc_name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存记忆出错: {e}")
        return False

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

def build_injection_text(data, budget_chars=2000, priority=None):
    """
    把记忆拼成发给 AI 的文本（控制台字数和顺序）
    """
    if not priority:
        priority = ["关系里程碑", "5", "early_gist", "4", "long_summary", "3"]
        
    parts = []
    parts.append(f"【当前时间地点】{data.get('time_location', '未知')}")
    
    # 临时存放事实，按优先级组装
    facts_pool = {i: [] for i in range(1, 6)}
    for cat in FACT_CATEGORIES:
        for fact in data["facts"].get(cat, []):
            imp = fact.get("importance", 3)
            facts_pool[imp].append(f"- [{cat}] {fact['text']}")
            
    for p in priority:
        if p == "early_gist" and data.get("early_gist"):
            parts.append(f"【故事早期梗概】\n{data['early_gist']}")
        elif p == "long_summary" and data.get("long_summary"):
            parts.append(f"【最近剧情】\n{data['long_summary']}")
        elif p.isdigit():
            imp = int(p)
            if imp >= 3: # 只有 3 级及以上默认注入
                parts.extend(facts_pool[imp])
                
    text = "\n".join(parts)
    # 如果超预算，从后往前砍（简单截断）
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n（记忆过长，已截断）"
    return text

def do_auto_merge(npc_id, npc_name, recent_logs, upstream_url, fallback_key):
    """每12轮触发：让 AI 总结剧情并提取事实"""
    if not recent_logs: return
    
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
  "early_gist": "更新后的早期梗概（≤150字）",
  "long_summary": "更新后的最近剧情（≤650字）",
  "new_facts": [
    {{"category": "赠物/承诺/关系里程碑/重要事件", "text": "事实内容", "importance": 1-5的数字}}
  ]
}}
"""
    import requests
    import json
    
    headers = {"Content-Type": "application/json"}
    if fallback_key: headers["Authorization"] = "Bearer " + fallback_key
    
    body = {
        "model": "local-model",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 1000
    }
    
    print(f"\n[记忆合并] 开始请求本地模型 {upstream_url}...", flush=True)
    
    try:
        # 这里加了 longer timeout
        res = requests.post(upstream_url, json=body, headers=headers, timeout=180)
        print(f"[记忆合并] 模型返回状态码: {res.status_code}", flush=True)
        
        if res.status_code == 200:
            text = res.json()["choices"][0]["message"]["content"]
            print(f"[记忆合并] 模型的原始回复(前50字): {text[:50]}...", flush=True)
            
            # 清理多余标记
            text = text.strip("`").replace("json\n", "").replace("json", "")
            
            try:
                res_data = json.loads(text)
            except json.JSONDecodeError as e:
                print(f"[记忆合并] JSON解析失败！模型返回的不是合法的JSON格式。错误: {e}", flush=True)
                return
            
            # 1. 更新梗概
            if res_data.get("early_gist"):
                data["early_gist"] = res_data["early_gist"]
            if res_data.get("long_summary"):
                data["long_summary"] = res_data["long_summary"]
                
            # 2. 插入新事实
            for fact in res_data.get("new_facts", []):
                cat = fact.get("category", "重要事件")
                if cat not in FACT_CATEGORIES: cat = "重要事件"
                data["facts"][cat].append({
                    "text": fact.get("text", ""),
                    "importance": int(fact.get("importance", 3)),
                    "age_count": 0
                })
                
            save_memory(npc_id, npc_name, data)
            print(f"[记忆合并] ✅ {npc_name} 的剧情与事实已成功更新保存！\n", flush=True)
        else:
            print(f"[记忆合并] ❌ 请求失败，报错信息: {res.text}\n", flush=True)
    except Exception as e:
        print(f"[记忆合并] ❌ 发生致命异常: {e}\n", flush=True)

def clear_memory(npc_id, npc_name):
    """清空某个角色的所有长期记忆"""
    path = get_memory_path(npc_id, npc_name)
    if os.path.exists(path):
        try:
            os.remove(path)
            return True
        except Exception as e:
            print(f"删除失败: {e}")
    return False