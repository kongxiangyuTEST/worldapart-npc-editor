# -*- coding: utf-8 -*-
"""
切换「游戏」那边摘要用的本地模型。

为什么需要这个：
  网页（AI试聊.html）里切模型只影响你自己聊天，
  游戏走的是 core_proxy，它读的是 ai_config.json 里的 local_model_name。
  两边是独立的 —— 这个脚本负责把游戏那边切过去。

改完不用重启 core_proxy：ai_config.json 是按 mtime 热加载的。
"""
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AI_CFG = ROOT / "ai_config.json"
PROXY_CFG = ROOT / "proxy" / "core_proxy_config.json"


def load_json(p):
    with open(p, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(p, d):
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def local_base():
    try:
        conf = load_json(PROXY_CFG)
    except Exception as e:
        print(f"[错误] 读不到 {PROXY_CFG.name}：{e}")
        return None
    base = (conf.get("local_upstream_base_url") or "").strip().rstrip("/")
    if not base:
        print("[提示] core_proxy_config.json 里 local_upstream_base_url 是空的，")
        print("       说明本地分流没启用，游戏根本不会走 Mac。")
        return None
    return base


def fetch_models(base):
    """拿模型列表 + 体积（/api/tags 比 /v1/models 信息全）。"""
    try:
        req = urllib.request.Request(base + "/api/tags",
                                     headers={"User-Agent": "switch"})
        with urllib.request.urlopen(req, timeout=12) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[错误] 连不上 Mac（{base}）：{e}")
        print("       检查：Mac 是否开机并已登录、是否同一 WiFi。")
        return None
    out = []
    for m in d.get("models") or []:
        out.append({
            "name": m.get("name") or m.get("model"),
            "size": m.get("size") or 0,
        })
    out.sort(key=lambda x: x["name"])
    return out


def fmt_size(b):
    if not b:
        return "—"
    return f"{b / 1073741824:.2f} GB"


def current_model():
    """返回 (当前生效模型, 来源说明)"""
    try:
        ai = load_json(AI_CFG)
    except Exception:
        ai = {}
    name = str(ai.get("local_model_name") or "").strip()
    if name:
        return name, "ai_config.json（热加载，改了立即生效）"
    try:
        conf = load_json(PROXY_CFG)
        name = str(conf.get("local_model_name") or "").strip()
    except Exception:
        name = ""
    return name, "core_proxy_config.json（需重启才生效）"


def main():
    print("=" * 58)
    print("  切换游戏摘要使用的本地模型")
    print("=" * 58)
    print("")

    base = local_base()
    if not base:
        print("")
        input("按回车退出...")
        return 1
    print(f"  本地节点：{base}")

    cur, src = current_model()
    print(f"  当前生效：{cur or '（未设置）'}")
    print(f"  来源：    {src}")
    print("")

    models = fetch_models(base)
    if models is None:
        print("")
        input("按回车退出...")
        return 1
    if not models:
        print("  Mac 上还没有任何模型。")
        input("按回车退出...")
        return 1

    print("  Mac 上可用的模型：")
    for i, m in enumerate(models, 1):
        mark = "  ← 当前" if m["name"] == cur or m["name"].split(":")[0] == cur else ""
        print(f"    {i}. {m['name']:<28} {fmt_size(m['size']):>8}{mark}")
    print("")

    try:
        pick = input("选择序号（直接回车=不改）：").strip()
    except EOFError:
        return 0
    if not pick:
        print("未做修改。")
        return 0
    if not pick.isdigit() or not (1 <= int(pick) <= len(models)):
        print("序号无效，未做修改。")
        input("按回车退出...")
        return 1

    chosen = models[int(pick) - 1]["name"]
    # Ollama 的 :latest 后缀可有可无，存成不带后缀的形式更好读
    if chosen.endswith(":latest"):
        chosen = chosen[:-7]

    # 备份再写
    try:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(AI_CFG, str(AI_CFG) + f".bak-{stamp}")
    except Exception as e:
        print(f"[警告] 备份失败（继续）：{e}")

    ai = load_json(AI_CFG)
    ai["local_model_name"] = chosen
    save_json(AI_CFG, ai)

    print("")
    print(f"  已切换为：{chosen}")
    print("  写入：ai_config.json → local_model_name")
    print("")
    print("  生效说明：")
    print("    · core_proxy 是按文件修改时间热加载的，下一次游戏请求就用新模型，")
    print("      **不需要重启服务**。")
    print("    · 新模型第一次被调用要把权重读进内存，那一轮会慢几秒，之后正常。")
    print("    · 16GB 机器同时只加载一个模型（MAX_LOADED_MODELS=1），")
    print("      切换后旧模型会被卸载，这是正常的。")
    print("")
    input("按回车退出...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
