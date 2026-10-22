# -*- coding: utf-8 -*-
r"""
extract_fragments.py
从 prompt_samples\ 里提取"固定段落"，保存到 prompt_fragments.json
"""
import json
import os
import re
from pathlib import Path
from collections import defaultdict


# ==================== 配置 ====================
PROJECT_DIR = Path(__file__).parent.resolve()
SAMPLES_DIR = PROJECT_DIR / "prompt_samples"
OUTPUT_FILE = PROJECT_DIR / "prompt_fragments.json"


# ==================== 工具 ====================

def split_sections(text):
    """
    把一条 system message 拆成若干段落。
    按 "## " 开头切分。
    返回：[(标题, 内容), ...]
    """
    if not text:
        return []
    # 找所有 "## " 开头的位置
    matches = list(re.finditer(r"^##\s+(.+)$", text, flags=re.M))
    if not matches:
        return [("(无标题)", text)]

    sections = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i+1].start() if i+1 < len(matches) else len(text)
        content = text[start:end].strip()
        sections.append((title, content))
    return sections


def load_samples():
    """读所有样本，返回 list of (filename, messages)"""
    if not SAMPLES_DIR.is_dir():
        print(f"[错误] 目录不存在: {SAMPLES_DIR}")
        return []
    samples = []
    for f in sorted(SAMPLES_DIR.iterdir()):
        if not f.is_file() or not f.name.endswith(".json"):
            continue
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            samples.append((f.name, data.get("messages", [])))
        except Exception as e:
            print(f"[警告] 读取 {f.name} 失败: {e}")
    return samples


def collect_sections(samples):
    """
    把每个样本里所有 system message 切分成段落。
    返回：list of (npc_name, [(title, content), ...])
    """
    results = []
    for fname, messages in samples:
        npc_name = fname
        all_sections = []
        for m in messages:
            if m.get("role") != "system":
                continue
            content = m.get("content", "")
            if not isinstance(content, str):
                continue
            sections = split_sections(content)
            all_sections.extend(sections)
        results.append((npc_name, all_sections))
    return results


def compare_sections(results):
    """
    对比每个段落，找出"固定段落"（所有样本里完全相同）。
    返回：{
      "fixed": {title: content, ...},      # 固定段落（内容）
      "stats": {title: (count_total, count_same), ...},  # 统计
      "varying": {title: [content1, content2, ...], ...},  # 不同的内容样本
    }
    """
    # 收集每个标题在所有样本里的内容
    title_contents = defaultdict(list)  # title -> [content, ...]
    title_appear_count = defaultdict(int)  # title -> 出现次数
    total_samples = len(results)

    for npc_name, sections in results:
        seen_titles = set()
        for title, content in sections:
            title_contents[title].append(content)
            if title not in seen_titles:
                title_appear_count[title] += 1
                seen_titles.add(title)

    fixed = {}
    stats = {}
    varying = {}

    for title, contents in title_contents.items():
        appear = title_appear_count[title]
        # 只对"所有样本都出现"的段落做"完全相同"判断
        if appear < total_samples:
            # 出现频率不固定（比如"任务：闲聊"）
            stats[title] = (appear, total_samples, "partial")
            varying[title] = list(set(contents))[:3]  # 只留 3 个样本
            continue

        # 所有样本都出现了，检查内容是否一致
        unique = list(set(contents))
        if len(unique) == 1:
            fixed[title] = unique[0]
            stats[title] = (appear, total_samples, "fixed")
        else:
            stats[title] = (appear, total_samples, "varying")
            varying[title] = unique[:3]

    return {
        "fixed": fixed,
        "stats": stats,
        "varying": varying,
        "total_samples": total_samples,
    }


def main():
    print("=" * 70)
    print("Prompt 段落提取工具")
    print("=" * 70)

    samples = load_samples()
    if not samples:
        print("[错误] 没有样本可处理")
        return

    print(f"读取样本数: {len(samples)}")
    for fname, _ in samples:
        print(f"  - {fname}")
    print()

    results = collect_sections(samples)
    report = compare_sections(results)

    # ============ 输出报告 ============
    print("=" * 70)
    print("段落对比报告")
    print("=" * 70)

    stats = report["stats"]
    total = report["total_samples"]

    # 分类打印
    fixed_titles = []
    varying_titles = []
    partial_titles = []

    for title, (appear, total_s, status) in stats.items():
        if status == "fixed":
            fixed_titles.append(title)
        elif status == "partial":
            partial_titles.append(title)
        else:
            varying_titles.append(title)

    print(f"\n【固定段落】（{total}/{total} 相同，可全局替换）:")
    for t in fixed_titles:
        print(f"  ✅ {t}")

    print(f"\n【不固定段落】（各样本不同，不能全局替换）:")
    for t in varying_titles:
        print(f"  ⚠️  {t}")

    print(f"\n【部分出现】（只在某些场景出现，不能全局替换）:")
    for t in partial_titles:
        appear, _, _ = stats[t]
        print(f"  🔶 {t}  ({appear}/{total})")

    # ============ 保存结果 ============
    output = {
        "total_samples": total,
        "fixed_sections": report["fixed"],
        "stats": {k: {"appear": v[0], "total": v[1], "status": v[2]}
                  for k, v in stats.items()},
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 70)
    print(f"已保存: {OUTPUT_FILE}")
    print(f"固定段落数: {len(fixed_titles)}")
    print("=" * 70)

    # 打印固定段落的内容长度
    print("\n固定段落长度:")
    for t in fixed_titles:
        length = len(report["fixed"][t])
        print(f"  {t}: {length} 字符")


if __name__ == "__main__":
    main()