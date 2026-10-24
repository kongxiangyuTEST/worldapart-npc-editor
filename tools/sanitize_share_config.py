# -*- coding: utf-8 -*-
"""分享包出厂前，抹掉只属于本机环境的信息。

目前要清的是 memory_merge_url（指向自己局域网的 Mac）。
别人拿到那个地址会去打不通的网络请求，且等于把你的内网 IP 发出去。

注入语、替换规则这些**保留** —— 那是产品的默认体验，不是隐私。
"""
import json
import sys

FIELDS_TO_CLEAR = ("memory_merge_url",)


def main(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[清理] 没有 {path}，跳过")
        return 0
    except Exception as e:
        print(f"[清理] 读取失败：{e}")
        return 1

    changed = []
    for k in FIELDS_TO_CLEAR:
        if data.get(k):
            data[k] = ""
            changed.append(k)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if changed:
        print(f"[清理] 已清空：{', '.join(changed)}")
    else:
        print("[清理] 无需清理")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "ai_config.json"))
