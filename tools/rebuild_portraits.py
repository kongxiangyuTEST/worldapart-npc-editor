# -*- coding: utf-8 -*-
"""
把 AssetStudio 导出的 {ID}.png 按 portraits_index.json 的顺序
重命名为 {序号}.png，放入 web/portraits/。
"""
import json
import shutil
from pathlib import Path

# ==== 路径（都相对项目根目录，打包后也成立）====
PROJECT_DIR = Path(__file__).resolve().parent.parent

EXPORT_DIR = PROJECT_DIR / "_tmp_head_export" / "assets" / "res" / "gui" / "atlas" / "headportrait"
INDEX_JSON = PROJECT_DIR / "portraits_index.json"
OUTPUT_DIR = PROJECT_DIR / "web" / "portraits"

# 想用别的导出目录，可以命令行指定：python rebuild_portraits.py <导出目录>
import sys
if len(sys.argv) > 1:
    EXPORT_DIR = Path(sys.argv[1])
# =====================

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

with INDEX_JSON.open("r", encoding="utf-8") as f:
    mapping = json.load(f)

missing = []
done = 0

for raw_path, new_name in mapping.items():
    # raw_path 形如 assets/res/gui/atlas/headportrait/100000.png
    src_id = Path(raw_path).stem          # 100000
    src_file = EXPORT_DIR / f"{src_id}.png"

    if not src_file.exists():
        missing.append(src_id)
        continue

    dst_file = OUTPUT_DIR / new_name
    shutil.copy2(src_file, dst_file)
    done += 1

print(f"完成 {done} 张")
if missing:
    print(f"缺失 {len(missing)} 张：")
    for m in missing:
        print("  -", m)