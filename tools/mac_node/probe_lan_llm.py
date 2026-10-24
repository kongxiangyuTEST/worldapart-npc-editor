# -*- coding: utf-8 -*-
"""
扫描局域网里的本地 LLM 服务（Ollama / LM Studio / llama.cpp），
确认 Mac 上线、列出可用模型、并做一次真实生成测速。

用法：
    python tools\\mac_node\\probe_lan_llm.py            # 扫描 + 列出模型
    python tools\\mac_node\\probe_lan_llm.py --bench    # 额外做一次生成测速
"""
import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# 常见本地推理服务的端口
PORTS = {
    11434: "Ollama",
    1234: "LM Studio",
    8080: "llama.cpp server",
    8000: "vLLM / llama-cpp-python",
    5000: "mlx-lm / 自建服务",
}


def local_subnets():
    """拿到本机所在的所有网段（支持多网卡）。"""
    subnets = set()
    if sys.platform == "win32":
        out = subprocess.run(["ipconfig"], capture_output=True, text=True,
                             encoding="gbk", errors="ignore").stdout
        blocks = out.split("适配器")
        cur = None
        for line in out.splitlines():
            if "IPv4" in line and ":" in line:
                ip = line.split(":")[-1].strip()
                # 跳过 169.254 的 APIPA 地址
                if ip.count(".") == 3 and not ip.startswith("169.254."):
                    cur = ip
            if "子网掩码" in line and ":" in line and cur:
                mask = line.split(":")[-1].strip()
                try:
                    o = [int(x) for x in cur.split(".")]
                    m = [int(x) for x in mask.split(".")]
                    if m == [255, 255, 255, 0]:
                        # 只保留网段前缀三段，后面拼主机位
                        subnets.add(".".join(str(o[i] & m[i]) for i in range(3)))
                except ValueError:
                    pass
                cur = None
    else:
        try:
            out = subprocess.run(["ip", "route"], capture_output=True, text=True).stdout
            for line in out.splitlines():
                if line.startswith("default") or "/" in line:
                    for tok in line.split():
                        if tok.count(".") == 3 and tok.endswith("/24"):
                            subnets.add(".".join(tok.split(".")[:3]))
        except Exception:
            subnets.add("192.168.1")
    return sorted(subnets)


def probe(host, port, timeout=1.2):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def http_json(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "probe"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"__err__": str(e)}


def model_list(ip, port):
    """Ollama 原生接口优先，回落到 OpenAI 兼容接口。"""
    data = http_json(f"http://{ip}:{port}/api/tags", timeout=4)
    names = [m.get("name") for m in data.get("models", [])] if isinstance(data, dict) else None
    if names:
        return names, "Ollama 原生"
    data = http_json(f"http://{ip}:{port}/v1/models", timeout=4)
    if isinstance(data, dict) and "data" in data:
        return [m.get("id") for m in data["data"]], "OpenAI 兼容"
    return [], (str(data)[:80] if data else "无响应")


def bench(ip, port, model, prompt="请把下面这段对话压缩成 100 字以内的第三人称摘要。\n示例内容：甲向乙打听镇上有没有药铺，乙说东街有一家。",
          max_tokens=120):
    """真实生成测速：返回首字延迟与生成速度。"""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://{ip}:{port}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=300) as r:
            first = None
            text = ""
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                if line.endswith("[DONE]"):
                    break
                try:
                    d = json.loads(line[5:].strip())
                except Exception:
                    continue
                delta = d.get("choices", [{}])[0].get("delta", {})
                chunk = delta.get("content") or delta.get("reasoning") or ""
                if chunk:
                    if first is None:
                        first = time.time() - t0
                    text += chunk
        total = time.time() - t0
        return first, total, len(text)
    except Exception as e:
        return None, None, f"失败：{e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", action="store_true", help="额外做一次真实生成测速")
    args = ap.parse_args()

    subnets = local_subnets()
    if not subnets:
        print("没能识别出本机网段，请用 --help 查看手动用法。")
        return 1

    hosts = []
    for net in subnets:
        hosts += [f"{net}.{i}" for i in range(1, 255)]

    print(f"扫描网段：{', '.join(subnets)}.0/24  （共 {len(hosts)} 个地址，几秒钟）")
    print("")

    open_ports = []
    with ThreadPoolExecutor(max_workers=120) as ex:
        futures = {(h, p): ex.submit(probe, h, p) for h in hosts for p in PORTS}
        for (h, p), f in futures.items():
            if f.result():
                open_ports.append((h, p, PORTS[p]))

    # 端口通不等于有 LLM：AirPlay 占 5000、路由器管理页占 8000，都会误报。
    # 必须真的拿到模型列表才算命中。
    hits, rejected = [], []
    for ip, port, kind in open_ports:
        models, src = model_list(ip, port)
        (hits if models else rejected).append((ip, port, kind, models, src))

    if rejected:
        print("端口开着但不是 LLM 服务（忽略即可）：")
        for ip, port, kind, _, src in rejected:
            print(f"  http://{ip}:{port}   [{kind}]   {src[:60]}")
        print("")

    if not hits:
        print("没有发现任何本地推理服务。")
        print("")
        print("确认清单：")
        print("  1. Mac 已经开机，并且跑过了 setup_mac.sh")
        print("  2. Mac 接入了同一个路由器（WiFi 或有线都行）")
        print("  3. macOS 防火墙弹窗点了「允许」")
        print("  4. Mac 没睡过去（先用 caffeinate 压着）")
        return 1

    print("发现可用的推理服务：")
    print("")
    for ip, port, kind, models, src in hits:
        print(f"  http://{ip}:{port}   [{kind}]")
        print(f"      模型列表来源：{src}")
        for m in models:
            print(f"        - {m}")
        print("")

    if args.bench and hits:
        ip, port, _, models, _ = hits[0]
        model = models[0]
        print(f"对 {model} 做一次摘要测速（120 token 上限）...")
        first, total, n = bench(ip, port, model)
        if first is None:
            print(f"  {n}")
        else:
            print(f"  首字延迟：{first:.2f} 秒")
            print(f"  总耗时：{total:.2f} 秒，生成 {n} 字")
            if total > 0:
                print(f"  生成速度：约 {n / total:.1f} 字/秒")
            print("")
            print("  判断参考：")
            if first < 3 and n / total > 15:
                print("    → 摘要任务会很舒服，可以直接接管记忆压缩。")
            elif first < 8:
                print("    → 摘要够用；跑主对话会觉得慢。")
            else:
                print("    → 偏慢，建议只做后台压缩，或换更小的模型。")

    print("")
    print("下一步：把上面那个地址填进 proxy/core_proxy_config.json，我来加分流逻辑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
