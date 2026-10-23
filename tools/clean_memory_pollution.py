"""
清理长期记忆里被污染的对话记录。

背景
----
上游返回空 content 时，游戏（beta）会把整个响应体当 NPC 台词显示并存档。
修复前那段 JSON 会被写进记忆的 recent_logs，之后每次 AI 总结都把它
当"新增对话"送给压缩器——摘要质量被拖累，内容也会越滚越脏。

core_proxy 已修（2026-10-24），但**已经落盘的脏记录不会自愈**，需要清一次。

判据：recent_logs 条目里含 "chat.completion"（归一化空格后）。
处理：整条删除（那条记录本来就不是有效对话，删掉不损失剧情）。

用法
----
python tools/clean_memory_pollution.py            # 预览，不写盘
python tools/clean_memory_pollution.py --apply    # 实际清理（自动先备份）
"""
import argparse
import glob
import json
import os
import shutil
import sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.path.join(PROJECT_DIR, "memory")
BACKUP_DIR = os.path.join(MEMORY_DIR, "_backup")
MARKER = "chat.completion"


def is_polluted(entry):
    s = entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
    return MARKER in s.replace(" ", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际清理（默认只预览）")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(MEMORY_DIR, "*.json")))
    if not files:
        print("没有记忆文件")
        return 0

    plan = []
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"  跳过 {os.path.basename(f)}: {e}")
            continue
        logs = d.get("recent_logs") or []
        bad = [i for i, e in enumerate(logs) if is_polluted(e)]
        if bad:
            plan.append((f, d, logs, bad))

    if not plan:
        print("所有记忆文件都干净，无需清理。")
        return 0

    total = sum(len(b) for _, _, _, b in plan)
    print(f"发现 {total} 条污染记录，分布在 {len(plan)} 个角色：\n")
    for f, d, logs, bad in plan:
        name = d.get("npc_name") or os.path.basename(f)
        print(f"  {name}（{os.path.basename(f)}）: recent_logs 第 {[i+1 for i in bad]} 条")
    print()

    if not args.apply:
        print("[预览模式] 未改动任何文件。确认清理请加 --apply。")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, ts)
    os.makedirs(dst, exist_ok=True)
    for f, _, _, _ in plan:
        shutil.copy2(f, dst)
    print(f"已备份待清理文件 → {dst}")

    for f, d, logs, bad in plan:
        for i in sorted(bad, reverse=True):
            logs.pop(i)
        d["recent_logs"] = logs
        with open(f, "w", encoding="utf-8") as fp:
            json.dump(d, fp, ensure_ascii=False, indent=2)
        name = d.get("npc_name") or os.path.basename(f)
        print(f"  已清理 {name}: 删除 {len(bad)} 条，剩余 {len(logs)} 条")
    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
