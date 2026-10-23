"""
用游戏配置表补全角色卡的「境界」字段。

以前这块是「暂未支持」，理由是"游戏运行时数据，配置表拿不到"。
2026-10-24 逆向配置表后发现这个判断不对——境界在配置表里是有的：

  tbnpcbasecfg.propLayerId   → 层号（0=凡人，1-13 炼气，21-29 筑基，
                                31-39 结丹，41-49 元婴，51 化神）
  tbcultivatephase.layers    → 层号 → "炼气初期 / 筑基后期" 这类显示名
  tbnpcbasecfg.layerZeroShowText → 层号 0 的显示名（通常是"凡人"）

覆盖率：1592 个 NPC 里 1509 个能推出境界（94.8%），
其余 83 个是特殊 NPC（propLayerId 为空），保持原样不动。

⚠ 这是**初始值**：游戏里 NPC 修炼突破后实际境界会变，
所以写进卡片只作为设定参考，不代表玩家档里的当前境界。

用法
----
python tools/apply_realm.py --dry-run 5      # 预览 5 张
python tools/apply_realm.py                  # 全量写入（自动备份）
python tools/apply_realm.py --limit 20       # 只写 20 张
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARDS_DIR = os.path.join(PROJECT_DIR, "editor_cards")
BACKUP_DIR = os.path.join(PROJECT_DIR, "editor_cards_backup")
DEFAULT_LUBAN = r"E:\WorldApart_test\luban_export\assets\luban\data"


def zh(v):
    if isinstance(v, dict):
        return v.get("zh-Hans") or ""
    return str(v) if v else ""


def build_realm_map(luban_dir):
    """npc_id(str) → 境界显示名（推不出来就不放进 map）"""
    phase_path = os.path.join(luban_dir, "tbcultivatephase.json")
    cfg_path = os.path.join(luban_dir, "tbnpcbasecfg.json")
    if not (os.path.isfile(phase_path) and os.path.isfile(cfg_path)):
        raise FileNotFoundError(f"缺少配置表：{luban_dir}")

    # 层号 → 期名
    layer_to_phase = {}
    for x in json.load(open(phase_path, encoding="utf-8")):
        layers = x.get("layers")
        if isinstance(layers, str):
            try:
                layers = json.loads(layers)
            except Exception:
                layers = []
        for L in layers or []:
            layer_to_phase[L] = zh(x.get("phase_name"))

    realms = {}
    for x in json.load(open(cfg_path, encoding="utf-8")):
        nid = str(x.get("id"))
        layer = x.get("propLayerId")
        if layer == 0:
            realms[nid] = zh(x.get("layerZeroShowText")) or "凡人"
        elif layer in layer_to_phase:
            realms[nid] = layer_to_phase[layer]
    return realms


def read_npc_id(text):
    m = re.search(r"NPC[_ ]?ID[：: ]*\s*(-?\d+)", text)
    return m.group(1) if m else None


def apply_realm(text, realm):
    """把境界写进「预设资料」区。已有该行就替换，没有就插在年龄后面。"""
    new_line = f"- 境界：{realm}"
    if re.search(r"^- 境界：.*$", text, re.M):
        return re.sub(r"^- 境界：.*$", new_line, text, count=1, flags=re.M), True
    m = re.search(r"^- 年龄：.*$", text, re.M)
    if m:
        return text[:m.end()] + "\n" + new_line + text[m.end():], True
    return text, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luban", default=DEFAULT_LUBAN, help="luban 导出目录（含 tbnpcbasecfg.json）")
    ap.add_argument("--dry-run", type=int, nargs="?", const=5, default=0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    try:
        realms = build_realm_map(args.luban)
    except FileNotFoundError as e:
        print(e)
        return 1

    cards = sorted(glob.glob(os.path.join(CARDS_DIR, "*.md")))
    plan, skipped = [], {"no_id": 0, "no_realm": 0, "already": 0}

    for path in cards:
        text = open(path, encoding="utf-8").read()
        nid = read_npc_id(text)
        if not nid:
            skipped["no_id"] += 1
            continue
        # NPC ID <= 0 是占位/模板卡（如 -1-unknown.md），不写
        try:
            if int(nid) <= 0:
                skipped["no_id"] += 1
                continue
        except ValueError:
            skipped["no_id"] += 1
            continue
        realm = realms.get(nid, "")
        if not realm:
            skipped["no_realm"] += 1
            continue
        m = re.search(r"^- 境界：(.*)$", text, re.M)
        if m and m.group(1).strip() == realm:
            skipped["already"] += 1
            continue
        new_text, ok = apply_realm(text, realm)
        if ok:
            plan.append((path, new_text, realm))
        else:
            skipped["no_realm"] += 1

    print(f"卡片 {len(cards)} 张 → 计划写入 {len(plan)} 张")
    print(f"  跳过：无 ID {skipped['no_id']} / 推不出境界 {skipped['no_realm']} / 已是最新 {skipped['already']}")
    print()

    if args.dry_run:
        for path, _, realm in plan[:args.dry_run]:
            print(f"  {os.path.basename(path)}  →  境界：{realm}")
        print(f"\n[预览模式] 未写盘。共 {len(plan)} 张可写入。")
        return 0

    if args.limit:
        plan = plan[:args.limit]
    if not plan:
        print("没有可写入的卡片")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, ts)
    os.makedirs(dst, exist_ok=True)
    for f in glob.glob(os.path.join(CARDS_DIR, "*.md")):
        shutil.copy2(f, dst)
    print(f"已备份全部卡片 → {dst}")

    for path, new_text, _ in plan:
        open(path, "w", encoding="utf-8").write(new_text)
    print(f"已写入 {len(plan)} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
