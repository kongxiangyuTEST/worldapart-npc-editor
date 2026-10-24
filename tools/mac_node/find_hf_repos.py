# -*- coding: utf-8 -*-
"""给筛选出的候选模型在 HuggingFace 上找仓库地址。

榜单里的模型名是人工简写（有的带 📦 前缀、有的用 _ 分隔作者），
不能直接拼 URL，得用 HF 搜索接口反查。
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SRC = Path(r"H:\llama\models\wa-npc-cards\_tmp\chat_candidates.json")
PROXY = "http://127.0.0.1:7897"


def hf_search(query, limit=8):
    url = ("https://huggingface.co/api/models?search="
           + urllib.parse.quote(query) + f"&limit={limit}&full=false")
    req = urllib.request.Request(url, headers={"User-Agent": "probe"})
    # 走本机代理
    op = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    try:
        with op.open(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return [{"__err__": str(e)}]


def normalize(name):
    """把榜单里的展示名压成可搜索的关键词。"""
    s = name
    s = re.sub(r"^📦\s*", "", s)          # 去掉包图标
    s = re.sub(r"\s*—.*$", "", s)          # 去掉 — 后面的备注
    s = re.sub(r"[⌥💭❌💥⭐]", "", s)      # 去掉标记符号
    return s.strip()


def main():
    cands = json.loads(SRC.read_text(encoding="utf-8"))
    out = []

    for c in cands:
        raw = c["model"]
        q = normalize(raw)
        print(f"── {q}")
        hits = hf_search(q, 8)
        if hits and "__err__" in hits[0]:
            print(f"   搜索失败：{hits[0]['__err__']}")
            out.append({**c, "query": q, "repo": None})
            continue

        # 挑最像的：id 里包含查询词的主要片段
        key = re.sub(r"[^a-z0-9]", "", q.lower())
        best = None
        for h in hits:
            rid = h.get("id", "")
            if re.sub(r"[^a-z0-9]", "", rid.lower()).find(key[:14]) >= 0:
                best = h
                break
        if not best and hits:
            best = hits[0]

        if best:
            rid = best.get("id")
            print(f"   → {rid}   (下载 {best.get('downloads', '?')})")
            out.append({**c, "query": q, "repo": rid,
                        "url": f"https://huggingface.co/{rid}",
                        "downloads": best.get("downloads")})
        else:
            print("   → 未找到")
            out.append({**c, "query": q, "repo": None})
        print("")

    dst = Path(r"H:\llama\models\wa-npc-cards\_tmp\chat_candidates_linked.json")
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已存 {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
