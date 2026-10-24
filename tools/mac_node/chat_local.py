# -*- coding: utf-8 -*-
"""
在 Windows 上直接和 Mac 里的本地模型聊天，用来手动试效果。

用法：
    python tools\\mac_node\\chat_local.py              # 列出模型，选一个开聊
    python tools\\mac_node\\chat_local.py wa-unred      # 直接指定模型

聊天中：
    /clear   清空上下文
    /model   换模型
    /stat    看本机显存与模型占用
    /quit    退出
"""
import json
import sys
import time
import urllib.request

BASE = "http://192.168.0.116:11434"

# 这里放一个和游戏里相近的角色卡，试聊时更接近真实效果
SYSTEM = """## 角色设定
你是 苏倾盏（客栈老板娘），二十八岁，独自撑着城南一家小客栈。
你性子爽利，说话直，不爱绕弯子，但对着熟客会露出软的一面。
你有个弟弟苏怀，上月去了南边至今未归，这是你心里的结。

## 说话习惯
说话简短，多用短句，偶尔带点江南口音的口语（"晓得""莫要"）。
不解释太多，情绪到了就停。会写动作和神态，但不写旁白式总结。
"""


def api(path, body=None, timeout=300):
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_models():
    try:
        d = api("/v1/models", timeout=10)
        return [m["id"] for m in (d.get("data") or [])]
    except Exception as e:
        print(f"连不上 Mac：{e}")
        print("检查：Mac 是否开机、ollama serve 是否在跑、是否同一局域网。")
        return []


def chat(model, messages, stream=True):
    body = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "temperature": 0.8,
        "top_p": 0.9,
        # 关掉 Qwen3 的思考模式，否则会先吐一大段内心独白
        "reasoning_effort": "none",
    }
    req = urllib.request.Request(
        BASE + "/v1/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.time()
    text = ""
    first = None
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            if not stream:
                d = json.loads(r.read().decode("utf-8"))
                text = d["choices"][0]["message"].get("content") or ""
                return text, time.time() - t0, time.time() - t0
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                p = line[5:].strip()
                if p == "[DONE]":
                    break
                try:
                    d = json.loads(p)
                except Exception:
                    continue
                delta = d.get("choices", [{}])[0].get("delta", {})
                chunk = delta.get("content") or ""
                if chunk:
                    if first is None:
                        first = time.time() - t0
                    text += chunk
                    print(chunk, end="", flush=True)
    except Exception as e:
        print(f"\n[出错] {e}")
    return text, first, time.time() - t0


def main():
    models = list_models()
    if not models:
        return 1

    print("Mac 上可用的模型：")
    for i, m in enumerate(models, 1):
        print(f"  {i}. {m}")
    print("")

    if len(sys.argv) > 1:
        model = sys.argv[1]
    else:
        try:
            pick = input("选哪个（序号或名字）：").strip()
        except EOFError:
            return 0
        model = models[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= len(models) else pick

    print(f"\n当前模型：{model}")
    print("输入 /clear 清空、/model 换模型、/quit 退出")
    print("─" * 60)

    messages = [{"role": "system", "content": SYSTEM}]
    while True:
        try:
            user = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user:
            continue
        if user in ("/quit", "/q", "exit"):
            break
        if user == "/clear":
            messages = [{"role": "system", "content": SYSTEM}]
            print("（上下文已清空）")
            continue
        if user == "/model":
            for i, m in enumerate(models, 1):
                print(f"  {i}. {m}")
            p = input("换哪个：").strip()
            if p.isdigit() and 1 <= int(p) <= len(models):
                model = models[int(p) - 1]
            elif p:
                model = p
            messages = [{"role": "system", "content": SYSTEM}]
            print(f"（已切换到 {model}，上下文已清空）")
            continue
        if user == "/stat":
            try:
                d = api("/api/ps", timeout=10)
                for m in d.get("models") or []:
                    gb = (m.get("size") or 0) / 1073741824
                    print(f"  {m.get('name')}  占用 {gb:.2f} GB  到期 {m.get('expires_at')}")
            except Exception as e:
                print(f"  查询失败：{e}")
            continue

        messages.append({"role": "user", "content": user})
        print(f"\n{model}> ", end="", flush=True)
        reply, first, total = chat(model, messages)
        if reply:
            messages.append({"role": "assistant", "content": reply})
            f = f"{first:.2f}s" if first else "?"
            print(f"\n  ── 首字 {f} / 共 {total:.1f}s / {len(reply)} 字")
    print("\n再见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
