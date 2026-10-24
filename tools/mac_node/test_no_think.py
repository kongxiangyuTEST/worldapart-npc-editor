# -*- coding: utf-8 -*-
"""
测试几种关闭 Qwen3 思考模式的方式，找出对 Ollama 真正有效的那个。

背景：Qwen3 默认先输出一大段「好的，用户要求我…」的推理，再给答案。
做摘要这一步如果关不掉，游戏拿到的「摘要」就会是这堆废话，还会撑爆 max_tokens。

对 OpenAI 兼容端点（/v1/chat/completions）来说，候选方案：
  A. /no_think 放 user 消息末尾（官方 soft switch，位置可能讲究）
  B. /no_think 放 system 末尾（目前 core_proxy 用的，实测无效）
  C. body 加 "think": false（Ollama 原生参数，兼容层不一定透传）
  D. body 加 "reasoning_effort": "none"（OpenAI 新规范）
  E. body 加 "chat_template_kwargs": {"enable_thinking": false}（vLLM 风格）

判定标准：输出里有没有「好的」「让我」「用户要求」这类思考痕迹。
"""
import json
import re
import sys
import urllib.request

BASE = "http://192.168.0.116:11434"
MODEL = "qwen3:8b"

TASK = ("把下面这段对话压成一句第三人称摘要，不超过 40 字，只输出摘要本身。\n\n"
        "甲问乙镇上有没有药铺，乙说东街有一家，掌柜姓陈。")

SYSTEM_PLAIN = "你是一个精确的摘要器。只输出结果，不要解释你的思路。"
SYSTEM_NOTHINK = SYSTEM_PLAIN + "\n\n/no_think"

THINK_MARKERS = ["好的", "让我", "用户要求", "用户希望", "我需要", "首先", "根据要求", "思考"]


def call(system, user, extra=None, max_tokens=200, timeout=180):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "temperature": 0.1,
    }
    if extra:
        body.update(extra)
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return None, None, f"请求失败：{e}"

    msg = d.get("choices", [{}])[0].get("message", {})
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    return content, reasoning, None


def judge(content, reasoning):
    """返回 (是否有思考痕迹, 说明)"""
    if reasoning:
        return True, f"有独立 reasoning 字段（{len(reasoning)} 字）"
    hits = [m for m in THINK_MARKERS if m in content]
    if hits:
        return True, f"正文里出现思考词：{'/'.join(hits[:3])}"
    return False, "看起来是直接作答"


def main():
    cases = [
        ("A. /no_think 放 user 末尾", SYSTEM_PLAIN, TASK + "\n\n/no_think", None),
        ("B. /no_think 放 system 末尾", SYSTEM_NOTHINK, TASK, None),
        ("C. think=false", SYSTEM_PLAIN, TASK, {"think": False}),
        ("D. reasoning_effort=none", SYSTEM_PLAIN, TASK, {"reasoning_effort": "none"}),
        ("E. chat_template_kwargs.enable_thinking=false", SYSTEM_PLAIN, TASK,
         {"chat_template_kwargs": {"enable_thinking": False}}),
        ("C+D 组合", SYSTEM_PLAIN, TASK,
         {"think": False, "reasoning_effort": "none"}),
    ]

    results = []
    for name, system, user, extra in cases:
        print(f"── {name} ──")
        content, reasoning, err = call(system, user, extra)
        if err:
            print(f"   {err}")
            print("")
            results.append((name, None, err))
            continue
        thinking, why = judge(content, reasoning or "")
        text = (content or "").strip().replace("\n", " ")
        print(f"   判定：{'仍有思考 ✗' if thinking else '已关闭 ✓'}（{why}）")
        print(f"   输出：{text[:120]}{'…' if len(text) > 120 else ''}")
        print("")
        results.append((name, not thinking, why))

    print("════ 汇总 ════")
    ok = [r for r in results if r[1] is True]
    for name, good, why in results:
        mark = "✓" if good is True else ("✗" if good is False else "?")
        print(f"  {mark}  {name}")
    print("")
    if ok:
        print(f"可用方案：{ok[0][0]}")
    else:
        print("以上都不行 —— 需要用 Modelfile 改写模板（下一步）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
