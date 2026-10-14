# -*- coding: utf-8 -*-
"""
把 AssetStudio 导出的 {ID}.png 按 portraits_index.json 的顺序
重命名为 {序号}.png，放入 web/portraits/。
"""
import json
import shutil
from pathlib import Path

# ==== 改这几个路径 ====
EXPORT_DIR = Path(r"H:\head_export\assets\res\gui\atlas\headportrait")   # AssetStudio 导出的图在这里
INDEX_JSON = Path(r"H:\llama\models\wa-npc-cards\portraits_index.json")
OUTPUT_DIR = Path(r"H:\llama\models\wa-npc-cards\web\portraits")
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