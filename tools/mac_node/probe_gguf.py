# -*- coding: utf-8 -*-
"""读 GGUF 文件的元数据，判断能否在指定内存的目标机上跑。

只看文件头几百 KB，不会把 10GB 文件读进内存。
"""
import os
import struct
import sys


MAX_STRING = 512 * 1024


def read_str(f):
    n = struct.unpack("<Q", f.read(8))[0]
    # 一旦解析错位，长度字段会变成一个天文数字，f.read() 会试图吞掉整个磁盘
    if n > MAX_STRING:
        raise ValueError(f"字符串长度异常 ({n})，多半是解析错位")
    return f.read(n).decode("utf-8", "ignore")


def read_value(f, vtype):
    if vtype == 0:    return struct.unpack("<B", f.read(1))[0]
    if vtype == 1:    return struct.unpack("<b", f.read(1))[0]
    if vtype == 2:    return struct.unpack("<H", f.read(2))[0]
    if vtype == 3:    return struct.unpack("<h", f.read(2))[0]
    if vtype == 4:    return struct.unpack("<I", f.read(4))[0]
    if vtype == 5:    return struct.unpack("<i", f.read(4))[0]
    if vtype == 6:    return struct.unpack("<f", f.read(4))[0]
    if vtype == 7:    return struct.unpack("<?", f.read(1))[0]
    if vtype == 8:    return read_str(f)
    if vtype == 10:   return struct.unpack("<Q", f.read(8))[0]
    if vtype == 11:   return struct.unpack("<q", f.read(8))[0]
    if vtype == 12:   return struct.unpack("<d", f.read(8))[0]
    if vtype == 9:    # array
        etype = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        return [read_value(f, etype) for _ in range(min(n, 64))]
    raise ValueError(f"unknown type {vtype}")


def probe(path):
    info = {}
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            return {"error": "不是 GGUF 文件"}
        version = struct.unpack("<I", f.read(4))[0]
        info["version"] = version
        tensor_count = struct.unpack("<Q", f.read(8))[0]
        kv_count = struct.unpack("<Q", f.read(8))[0]
        info["tensors"] = tensor_count

        for _ in range(min(kv_count, 400)):
            # 元数据区通常只有几十 KB，超过 2MB 一定出问题了
            if f.tell() > 2 * 1024 * 1024:
                break
            try:
                key = read_str(f)
                vtype = struct.unpack("<I", f.read(4))[0]
                val = read_value(f, vtype)
            except Exception:
                break
            info[key] = val

    return info


def fmt_size(b):
    return f"{b / 1073741824:.2f} GB"


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else r"H:\llama\models"
    target_gb = float(sys.argv[2]) if len(sys.argv) > 2 else 16.0

    files = [x for x in os.listdir(d)
             if x.lower().endswith(".gguf") and not x.lower().startswith("mmproj")]

    print(f"目标机内存：{target_gb:.0f} GB（macOS 系统本身要占 3~4GB）")
    print("")
    print(f"{'模型':<52} {'文件':>9} {'架构':<12} {'上下文':>10} {'模板':>6}  {'判定'}")
    print("─" * 118)

    usable_gb = target_gb - 4.0  # 系统开销
    rows = []
    for name in sorted(files):
        p = os.path.join(d, name)
        size = os.path.getsize(p)
        try:
            info = probe(p)
        except Exception as e:
            print(f"{name[:50]:<52} {fmt_size(size):>9}  读取失败: {e}")
            continue

        arch = str(info.get("general.architecture", "?"))
        ctx = info.get(f"{arch}.context_length")
        if ctx is None:
            for k, v in info.items():
                if k.endswith("context_length"):
                    ctx = v
                    break
        has_tpl = "有" if "tokenizer.chat_template" in info else "**无**"

        # 权重 + KV cache + buffer。
        # KV cache 按实际要用的上下文算，不是按模型标称的最大长度 ——
        # 这些 Qwen 标称 256K，但摘要任务用 8K 就够，可以在 Modelfile 里限住。
        need = size / 1073741824 + 1.5
        if need <= usable_gb - 4:
            verdict = "✓ 轻松"
        elif need <= usable_gb - 1:
            verdict = "~ 勉强"
        else:
            verdict = "✗ 会 swap"

        rows.append((need, name, size, arch, ctx, has_tpl, verdict))
        print(f"{name[:50]:<52} {fmt_size(size):>9} {arch:<12} "
              f"{str(ctx):>10} {has_tpl:>6}  {verdict}")

    print("─" * 118)
    print("")
    print(f"目标可用内存约 {usable_gb:.0f} GB（扣掉系统）。估算含 16K 上下文的 KV cache。")
    print("标『无模板』的导入 Ollama 时要额外指定 chat template，否则对话格式会错。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
